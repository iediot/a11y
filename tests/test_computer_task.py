"""Fresh observation sequencing and input contracts; all native submission is doubled."""
from copy import deepcopy
import time
import unittest
from unittest.mock import Mock, patch
from companion.computer_task import ComputerTask, needs_review
from companion.desktop_context import DesktopContext
from companion.input_actions import authorize_input
from companion.models import ValidationError
from companion.media import describe_music, describe_play_result
from test_desktop_context import FakeBridge, SAMPLE


def context():
    value = deepcopy(SAMPLE)
    value.update(enabled=True, age_seconds=0, focused_role='AXTextField',
                 focused_bounds={'x': 30, 'y': 50, 'width': 200, 'height': 25})
    return value


class InputTests(unittest.TestCase):
    def test_typing_is_bound_to_field_and_does_not_add_enter(self):
        action = authorize_input({'type': 'type_text', 'text': 'hello 👋'}, context())
        self.assertEqual(action['text'], 'hello 👋')
        self.assertEqual(action['focused_bounds'], context()['focused_bounds'])
        self.assertEqual(action['bundle_id'], 'com.apple.finder')
        self.assertNotIn('key', action)

    def test_missing_secure_stale_and_shell_destinations_are_refused(self):
        for modification in ({'focused_role': 'secure_input'}, {'focused_role': 'AXButton'},
                             {'focused_bounds': None}, {'age_seconds': 11}, {'enabled': False},
                             {'app': {'bundle_id': 'com.apple.Terminal'}}):
            with self.assertRaises(ValueError):
                authorize_input({'type': 'type_text', 'text': 'hello'}, dict(context(), **modification))
        for text in ('hello\n', 'hello\t', 'x'*2001):
            with self.assertRaises(ValueError):
                authorize_input({'type': 'type_text', 'text': text}, context())

    def test_reported_line_breaks_are_typed_only_into_a_document_area(self):
        # Live: "a shopping list with onions, bread and apples, one per line" was refused as one action.
        document = dict(context(), focused_role='AXTextArea')
        action = authorize_input({'type': 'type_text', 'text': 'onions\r\nbread\napples'}, document)
        self.assertEqual(action['text'], 'onions\nbread\napples')
        self.assertIn('onions ⏎ bread', action['label'])
        for text in ('onions\nbread', 'onions\tbread'):   # a one-line field never takes a line break
            with self.assertRaises(ValueError):
                authorize_input({'type': 'type_text', 'text': text}, context())
        with self.assertRaises(ValueError):
            authorize_input({'type': 'type_text', 'text': 'a\tb'}, document)

    def test_keyboard_and_scroll_are_allowlisted(self):
        self.assertEqual(authorize_input({'type': 'press_key', 'key': 'cmd_l'}, context())['key'], 'cmd_l')
        self.assertEqual(authorize_input({'type': 'scroll', 'direction': 'down'}, context())['direction'], 'down')
        for action in ({'type': 'press_key', 'key': 'run_shell'}, {'type': 'press_key', 'key': 'cmd_q'},
                       {'type': 'scroll', 'direction': 'sideways'}, {'type': 'type_text', 'text': 'hi', 'script': 'bad'}):
            with self.assertRaises(ValueError):
                authorize_input(action, context())

    def test_input_revalidates_destination_before_native_submission(self):
        bridge = FakeBridge()
        bridge.value = context()
        bridge.application_action = Mock(return_value={'submitted': True})
        monitor = DesktopContext(bridge, clock=lambda: 100)
        monitor.enabled = True
        monitor.collect_once()
        action = monitor.authorize_action({'type': 'type_text', 'text': 'hello'})
        bridge.value['focused_bounds']['x'] += 10
        with self.assertRaisesRegex(ValueError, 'destination changed'):
            monitor.application_action(action)
        bridge.application_action.assert_not_called()


class TaskTests(unittest.TestCase):
    def setUp(self):
        bridge = FakeBridge()
        bridge.value = context()
        self.desktop = DesktopContext(bridge, clock=lambda: 100)
        self.desktop.enabled = True
        self.vision = Mock(enabled=True, generation=0)
        self.vision.capture.side_effect = lambda *args, **kwargs: {
            'image_data': 'data:image/png;base64,synthetic', 'display_id': 7,
            'screen': {'x': 0, 'y': 0, 'width': 1000, 'height': 700}, 'captured_monotonic': time.monotonic()}
        self.vision.click_guard.return_value = {'region_hash': 'synthetic'}
        self.provider = Mock()
        self.task = ComputerTask('Find a help page', automatic=True)

    def respond(self, action=None, status='action', risk='navigation'):
        self.provider.complete_structured.return_value = {'status': status, 'message': 'Current screen evidence.', 'risk': risk, 'action': action}
        return self.task.propose(self.desktop, self.vision, self.provider, {'x': 0, 'y': 0})

    def finished_screen(self, *lines):
        """Fixture only: the screen text a finished task shows, and a completion check that quotes
        its first line. Step replies still come from respond() (DEFAULT keeps return_value)."""
        from unittest.mock import DEFAULT
        from companion.task_completion import MET_SYSTEM
        capture = self.vision.capture.side_effect
        self.vision.capture.side_effect = lambda *args, **kwargs: dict(capture(*args, **kwargs), text_regions=[
            {'text': line, 'x': .2, 'y': .3+.1*i, 'width': .4, 'height': .05} for i, line in enumerate(lines)])
        self.provider.complete_structured.side_effect = lambda system, *args, **kwargs: (
            {'met': True, 'missing': '', 'evidence': lines[0]} if system == MET_SYSTEM else DEFAULT)

    def test_each_step_uses_a_new_capture_and_preserves_goal_and_receipts(self):
        first = self.respond({'type': 'click_point', 'x': .2, 'y': .4, 'label': 'Help'})
        self.assertFalse(first['review'])
        self.assertIn('capture_guard', first['action'])
        self.task.record({'type': 'click_point', 'label': 'Help', 'status': 'submitted'})
        second = self.respond({'type': 'type_text', 'text': 'keyboard accessibility'})
        self.assertEqual(second['action']['text'], 'keyboard accessibility')
        self.assertEqual(self.vision.capture.call_count, 2)
        self.assertIn('Find a help page', self.provider.complete_structured.call_args.args[1][0]['content'])
        self.assertIn('Help', self.provider.complete_structured.call_args.args[1][1]['content'])
        self.assertEqual(self.provider.complete_structured.call_args.kwargs['image_data'], 'data:image/png;base64,synthetic')
        self.assertEqual(self.provider.complete_structured.call_args.kwargs['max_tokens'], 1200)
        self.assertIs(self.provider.complete_structured.call_args.kwargs['thinking'], False)

    def test_progress_changes_after_capture_and_before_model_inference(self):
        events = []
        # Fixture: a done is now checked against the screen, so the screen shows the tasks and that
        # check (its own call, not a step decision) is answered without being logged as 'model'.
        self.finished_screen('Tasks: 3 open')
        check = self.provider.complete_structured.side_effect
        from companion.task_completion import MET_SYSTEM
        self.provider.complete_structured.side_effect = lambda system, *args, **kwargs: check(system) if system == MET_SYSTEM else (
            events.append('model') or {'status': 'done', 'message': 'Found tasks.', 'risk': 'navigation', 'action': None})
        self.task.propose(self.desktop, self.vision, self.provider, {'x': 0, 'y': 0},
                          progress=lambda message: events.append(message))
        # A completion reported before anything ran is rechecked once.
        self.assertEqual(events, ['Deciding the next step…', 'model', 'Double-checking…', 'model'])

    def enable_ocr(self):
        capture = self.vision.capture.side_effect
        self.vision.capture.side_effect = lambda *args, **kwargs: dict(capture(*args, **kwargs),
            text_regions=[{'text': 'Battery', 'x': .2, 'y': .3, 'width': .2, 'height': .1},
                          {'text': 'Wi-Fi', 'x': .2, 'y': .45, 'width': .2, 'height': .1},
                          {'text': 'Bluetooth', 'x': .2, 'y': .6, 'width': .2, 'height': .1}])


    def test_image_inference_keeps_ocr_and_screenshot_click_guard(self):
        self.enable_ocr()
        result = self.respond({'type': 'click_point', 'x': .25, 'y': .35, 'label': 'Battery'})
        call = self.provider.complete_structured.call_args
        self.assertEqual(call.kwargs['image_data'], 'data:image/png;base64,synthetic')
        self.assertIn('Battery', call.args[1][1]['content'])
        self.assertIn('capture_guard', result['action'])
        self.vision.click_guard.assert_called_once()

    def test_inspection_rejects_post_control_then_replans_to_activity(self):
        self.task.goal_mode = 'inspect'
        self.desktop.bridge.value['controls'] = [
            {'id': 'post', 'label': 'Post in channel button', 'enabled': True, 'bounds': {'x': 100, 'y': 100, 'width': 80, 'height': 30}},
            {'id': 'activity', 'label': 'Activity', 'enabled': True, 'bounds': {'x': 200, 'y': 100, 'width': 80, 'height': 30}}]
        self.provider.complete_structured.side_effect = [
            {'status': 'action', 'message': 'Check activity.', 'risk': 'navigation', 'action': {'type': 'press_control', 'control_id': 'post'}},
            {'status': 'action', 'message': 'Check activity.', 'risk': 'navigation', 'action': {'type': 'press_control', 'control_id': 'activity'}}]
        result = self.task.propose(self.desktop, self.vision, self.provider, {'x': 0, 'y': 0})
        self.assertEqual(result['action']['control_id'], 'activity')
        self.assertEqual(self.task.receipts, [])

    def test_old_receipt_snapshots_do_not_bloat_the_next_prompt(self):
        self.task.record({'type': 'launch_app', 'label': 'Open Safari', 'status': 'submitted',
            'before': {'old_window_title': 'OLD_STATE'*1000}, 'after': {'old_window_title': 'OLD_STATE'*1000},
            'native_result': {'submitted': True, 'unused_debug': 'OLD_STATE'*1000}})
        self.respond({'type': 'scroll', 'direction': 'down'})
        prompt = self.provider.complete_structured.call_args.args[1][1]['content']
        self.assertNotIn('OLD_STATE', prompt)
        self.assertIn('Open Safari', prompt)

    def test_visual_clicks_do_not_require_accessibility_control_names(self):
        self.desktop.bridge.value['controls'] = []
        first = self.respond({'type': 'click_point', 'x': .2, 'y': .3, 'label': 'Blank document area'})
        self.assertEqual(first['action']['type'], 'click_point')
        self.assertFalse(first['review'])
        self.assertIn('capture_guard', first['action'])
        self.task.record({'type': 'click_point', 'status': 'submitted'})
        second = self.respond({'type': 'type_text', 'text': 'A prince travels between planets and learns about friendship.'})
        self.assertEqual(second['action']['type'], 'type_text')
        self.assertFalse(second['review'])
        self.task.record({'type': 'type_text', 'status': 'submitted'})
        third = self.respond({'type': 'click_point', 'x': .7, 'y': .6, 'label': 'Document area'})
        self.assertEqual(third['action']['x'], .7)
        self.task.record({'type': 'click_point', 'status': 'submitted'})
        # Fixture: a done is now checked against the screen, so the screen shows the sentence that was typed.
        self.finished_screen('A prince travels between planets and learns about friendship.')
        self.assertEqual(self.respond(status='done')['status'], 'done')
        self.assertEqual(self.vision.capture.call_count, 4)

    def test_task_can_press_a_current_accessible_control_without_coordinate_guessing(self):
        # Fixture: a button press that would run unconfirmed is asked about once (ComputerTask.commits);
        # this scripted answer says the button only navigates. A reply that is no answer waits for the user.
        from unittest.mock import DEFAULT
        from companion.task_prompts import COMMIT_SYSTEM
        self.provider.complete_structured.side_effect = lambda system, *args, **kwargs: (
            {'effect': 'navigate'} if system == COMMIT_SYSTEM else DEFAULT)
        first = self.respond({'type': 'press_control', 'control_id': 'control-0'})
        self.assertEqual(first['action']['type'], 'press_control')
        self.assertEqual(first['action']['label'], 'New Folder')
        self.assertFalse(first['review'])
        self.assertIn('observation_id', first['action'])
        with self.assertRaises(ValidationError):
            self.respond({'type': 'press_control', 'control_id': 'unobserved-control'})

    def test_missing_accessibility_labels_can_be_reconsidered_as_a_visual_click(self):
        self.desktop.bridge.value['controls'] = []
        self.provider.complete_structured.side_effect = [
            {'status': 'needs_user', 'message': 'No accessibility control for the editor.', 'risk': 'navigation', 'action': None},
            {'status': 'action', 'message': 'Click the visible editor.', 'risk': 'navigation',
             'action': {'type': 'click_point', 'x': .4, 'y': .5, 'label': 'Editor'}}]
        result = self.task.propose(self.desktop, self.vision, self.provider, {'x': 0, 'y': 0})
        self.assertEqual(result['action']['type'], 'click_point')
        self.assertEqual(self.provider.complete_structured.call_count, 2)
        self.assertEqual(self.vision.capture.call_count, 1)

    def test_stop_during_capture_does_not_start_model_inference(self):
        capture = self.vision.capture.side_effect
        def stop(*args, **kwargs):
            self.task.stop()
            return capture(*args, **kwargs)
        self.vision.capture.side_effect = stop
        with self.assertRaisesRegex(ValueError, 'stopped'):
            self.task.propose(self.desktop, self.vision, self.provider, {'x': 0, 'y': 0})
        self.provider.complete_structured.assert_not_called()

    def test_mutations_enter_and_review_mode_pause_for_confirmation(self):
        for action, risk in [({'type': 'press_key', 'key': 'enter'}, 'navigation'),
                             ({'type': 'click_point', 'label': 'Send'}, 'navigation'),
                             ({'type': 'type_text'}, 'change')]:
            self.assertTrue(needs_review(action, risk))
        self.task.automatic = False
        self.assertTrue(self.respond({'type': 'scroll', 'direction': 'down'})['review'])

    def test_done_requires_an_observation_and_cannot_smuggle_an_action(self):
        # Fixture: a done is now checked against the screen, so the screen shows the help page that was asked for.
        self.finished_screen('Help: getting started')
        self.assertEqual(self.respond(status='done')['status'], 'done')
        self.vision.capture.assert_called_once()
        with self.assertRaises(ValidationError):
            self.respond({'type': 'press_key', 'key': 'enter'}, status='done')

    def test_contradictory_status_is_repaired_before_a_visual_click_is_proposed(self):
        for status in ('done', 'needs_user'):
            self.task = ComputerTask('Open Battery information', automatic=True)
            click = {'type': 'click_point', 'x': .2, 'y': .3, 'label': 'Battery sidebar'}
            self.provider.complete_structured.side_effect = [
                {'status': status, 'message': 'Select Battery.', 'risk': 'navigation', 'action': click},
                {'status': 'action', 'message': 'Select Battery.', 'risk': 'navigation', 'action': click}]
            step = self.task.propose(self.desktop, self.vision, self.provider, {'x': 0, 'y': 0})
            self.assertEqual(step['status'], 'action')
            self.assertIn('capture_guard', step['action'])
            self.assertFalse(step['review'])
            self.assertEqual(self.task.receipts, [])
            correction = self.provider.complete_structured.call_args.args[1][-1]['content']
            self.assertIn('No action has run', correction)

    def test_failed_repair_stops_after_two_calls_without_a_pending_action(self):
        bad = {'status': 'done', 'message': 'Click Battery.', 'risk': 'navigation',
               'action': {'type': 'click_point', 'x': .2, 'y': .3, 'label': 'Battery'}}
        self.provider.complete_structured.side_effect = [bad, bad]
        with self.assertRaises(ValidationError):
            self.task.propose(self.desktop, self.vision, self.provider, {'x': 0, 'y': 0})
        self.assertEqual(self.provider.complete_structured.call_count, 2)
        self.assertIsNone(self.task.pending)
        self.vision.click_guard.assert_not_called()

    def test_cancellation_before_repair_prevents_the_second_model_call(self):
        self.provider.complete_structured.return_value = {'status': 'done', 'message': 'Click.', 'risk': 'navigation',
                                                         'action': {'type': 'click_point', 'x': .2, 'y': .3, 'label': 'Battery'}}
        def progress(message):
            if message.startswith('Double-checking'):
                self.task.stop()
        with self.assertRaisesRegex(ValueError, 'stopped'):
            self.task.propose(self.desktop, self.vision, self.provider, {'x': 0, 'y': 0}, progress=progress)
        self.provider.complete_structured.assert_called_once()
        self.assertIsNone(self.task.pending)

    def test_repaired_sensitive_action_still_requires_review(self):
        click = {'type': 'click_point', 'x': .2, 'y': .3, 'label': 'Delete document'}
        self.provider.complete_structured.side_effect = [
            {'status': 'done', 'message': 'Delete.', 'risk': 'change', 'action': click},
            {'status': 'action', 'message': 'Delete.', 'risk': 'navigation', 'action': click}]
        step = self.task.propose(self.desktop, self.vision, self.provider, {'x': 0, 'y': 0})
        self.assertTrue(step['review'])

    def test_unsupported_shell_and_invalid_coordinates_never_reach_executor(self):
        for action in ({'type': 'shell', 'command': 'anything'},
                       {'type': 'click_point', 'x': 500, 'y': .3, 'label': 'bad'},
                       {'type': 'click_point', 'x': 1500, 'y': 300, 'label': 'bad'}):
            with self.assertRaises(ValidationError):
                self.respond(action)

    def test_stop_and_step_limit_prevent_further_model_calls(self):
        self.task.stop()
        with self.assertRaises(ValueError):
            self.respond({'type': 'scroll', 'direction': 'down'})
        self.provider.complete_structured.assert_not_called()
        self.task = ComputerTask('Find help', max_steps=1)
        self.task.record({'type': 'scroll', 'label': 'Scroll', 'status': 'submitted'})
        self.assertEqual(self.respond()['status'], 'needs_user')
        self.provider.complete_structured.assert_not_called()

    def test_identical_action_on_unchanged_screen_does_not_repeat(self):
        first = self.respond({'type': 'scroll', 'direction': 'down'})
        self.task.record({'type': 'scroll', 'label': first['action']['label'], 'status': 'submitted'})
        second = self.respond({'type': 'scroll', 'direction': 'down'})
        self.assertEqual(second['status'], 'needs_user')
        self.assertIsNone(second['action'])

    def test_closed_browser_is_reconsidered_as_an_authorized_navigation_step(self):
        self.desktop.bridge.catalog = Mock(return_value=[{'name': 'Safari', 'bundle_id': 'com.apple.Safari'}])
        self.task = ComputerTask('Open Teams in a new Safari window and check my tasks', automatic=True)
        self.provider.complete_structured.side_effect = [
            {'status': 'needs_user', 'message': 'Safari is not visible. Confirm whether to open it.', 'risk': 'navigation', 'action': None},
            {'status': 'action', 'message': 'Open Teams in Safari.', 'risk': 'navigation',
             'action': {'type': 'browse_url', 'bundle_id': 'com.apple.Safari', 'url': 'https://teams.microsoft.com/', 'browser_mode': 'window', 'label': 'Open Teams'}}]
        step = self.task.propose(self.desktop, self.vision, self.provider, {'x': 0, 'y': 0})
        self.assertEqual(step['action']['type'], 'browse_url')
        self.assertEqual(step['action']['browser_mode'], 'window')
        self.assertFalse(step['review'])
        self.assertEqual(self.provider.complete_structured.call_count, 0)  # a named site opens in code now

    def test_actual_login_block_is_not_overridden_by_pause_reconsideration(self):
        step = self.respond(status='needs_user')
        self.assertEqual(step['status'], 'needs_user')
        self.assertIsNone(step['action'])
        self.assertIsNone(self.task.pending)
        self.assertEqual(self.provider.complete_structured.call_count, 2)

    def test_cancelled_pause_does_not_trigger_a_second_model_call(self):
        def pause(*args, **kwargs):
            self.task.stop()
            return {'status': 'needs_user', 'message': 'Confirm opening Safari', 'risk': 'navigation', 'action': None}
        self.provider.complete_structured.side_effect = pause
        with self.assertRaises(ValueError):
            self.task.propose(self.desktop, self.vision, self.provider, {'x': 0, 'y': 0})
        self.provider.complete_structured.assert_called_once()

    def test_pausing_or_cancelling_during_inference_discards_the_next_action(self):
        def response(*args, **kwargs):
            self.vision.generation += 1
            return {'status': 'action', 'message': 'Click', 'risk': 'navigation',
                    'action': {'type': 'click_point', 'x': .3, 'y': .4, 'label': 'Help'}}
        self.provider.complete_structured.side_effect = response
        with self.assertRaisesRegex(ValueError, 'paused'):
            self.task.propose(self.desktop, self.vision, self.provider, {'x': 0, 'y': 0})
        self.assertIsNone(self.task.pending)


class PlaybackTests(unittest.TestCase):
    def test_window_or_running_process_is_not_evidence_of_playback(self):
        self.assertIn('could not read', describe_music({'running': True, 'window': 'Music'}))
        self.assertIn('paused', describe_music({'player_state': 'paused', 'track': {'name': 'Last Christmas'}}))
        self.assertNotIn('playing', describe_music({'player_state': 'paused'}))

    def test_playback_success_requires_matching_track_id_and_playing_state(self):
        result = {'play_submitted': True, 'requested_id': 'ABC', 'player_state': 'playing',
                  'track': {'id': 'ABC', 'name': 'Last Christmas', 'artist': 'Wham!'}}
        self.assertIn('playing Last Christmas by Wham!', describe_play_result(result))
        result['track']['id'] = 'OTHER'
        self.assertIn('not confirmed', describe_play_result(result))
        self.assertIn('nothing was played', describe_play_result({'outcome': 'not_found'}).casefold())
        self.assertIn('Nothing was played', describe_play_result({'outcome': 'ambiguous'}))
