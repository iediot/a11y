"""Regressions for failures reported from live use.

Model replies and native submission are doubled here. These tests prove the loop's
control flow and validation for each reported failure; they do not prove that a
live model makes the right choice on a real desktop."""
import os
os.environ.setdefault('A11Y_WEB_ANSWERS', '0')  # tests never look things up online unless a test turns it on
os.environ.setdefault('A11Y_ROUTES', '0')  # nor read routes a live run remembered
from copy import deepcopy
import importlib.util
import json
import time
import unittest
from unittest.mock import Mock, patch
from companion.computer_task import ComputerTask, SYSTEM, IMAGE_PASS
from companion.desktop_context import DesktopContext
from companion.request_plan import preserve_desktop_goal, highlight_only
from test_desktop_context import FakeBridge, SAMPLE


def region(text, x, y, width=.08, height=.03):
    return {'text': text, 'x': x, 'y': y, 'width': width, 'height': height}


CALCULATOR = {'status': 'available', 'platform': 'macOS', 'accessibility': 'granted',
    'app': {'name': 'Calculator', 'bundle_id': 'com.apple.calculator', 'pid': 7},
    'window': {'title': 'Calculator', 'bounds': {'x': 0, 'y': 0, 'width': 1000, 'height': 700}},
    'available_apps': [{'name': 'Calculator', 'bundle_id': 'com.apple.calculator'}],
    'installed_apps': [{'name': 'Calculator', 'bundle_id': 'com.apple.calculator'}],
    'controls': [{'id': 'control-%d' % index, 'label': label, 'role': 'AXButton', 'enabled': True,
                  'bounds': {'x': 100+index*40, 'y': 300, 'width': 36, 'height': 36}}
                 for index, label in enumerate(['2', 'Add', 'Equals', 'Clear'])]}
TEAMS_REGIONS = [region('Activity', .02, .2), region('Chat', .02, .3), region('Teams', .02, .4),
                 region('Post in channel', .5, .9, .12), region('Assignments', .02, .5)]


class Harness(unittest.TestCase):
    snapshot = CALCULATOR
    regions = []

    def setUp(self):
        bridge = FakeBridge()
        bridge.value = deepcopy(self.snapshot)
        self.desktop = DesktopContext(bridge, clock=lambda: 100)
        self.desktop.enabled = True
        self.vision = Mock(enabled=True, generation=0)
        self.vision.capture.side_effect = lambda *args, **kwargs: {
            'image_data': 'data:image/png;base64,synthetic', 'display_id': 7, 'text_regions': deepcopy(self.regions),
            'screen': {'x': 0, 'y': 0, 'width': 1000, 'height': 700}, 'captured_monotonic': time.monotonic()}
        self.vision.click_guard.return_value = {'region_hash': 'synthetic'}
        self.provider = Mock()

    def propose(self, task):
        return task.propose(self.desktop, self.vision, self.provider, {'x': 0, 'y': 0})


class StopsAfterLaunchTests(Harness):
    def test_finder_filename_editor_is_cancelled_without_typing_or_model_call(self):
        self.desktop.bridge.value.update(app={'bundle_id':'com.apple.finder','name':'Finder'},
            focused_role='AXTextField',focused_container_roles=['AXCell','AXRow','AXOutline'])
        task=ComputerTask('Double-click the folder in Finder.',automatic=True,goal_mode='inspect')
        step=self.propose(task)
        self.assertEqual((step['action']['type'],step['action']['key']),('press_key','escape'))
        self.assertFalse(step['review'])
        self.provider.complete_structured.assert_not_called()

    def test_calculator_arithmetic_continues_after_a_premature_done(self):
        task = ComputerTask('Open Calculator and compute (2+2)*3', automatic=True, goal_mode='inspect')
        task.record({'type': 'launch_app', 'label': 'Open Calculator', 'status': 'submitted'})
        self.provider.complete_structured.side_effect = [
            {'status': 'done', 'message': 'Calculator is open.', 'risk': 'navigation', 'action': None},
            {'status': 'action', 'message': 'Press 2.', 'risk': 'navigation',
             'action': {'type': 'press_control', 'control_id': 'control-0'}}]
        step = self.propose(task)
        self.assertEqual(step['status'], 'action')
        self.assertEqual(step['action']['label'], '2')
        self.assertFalse(step['review'])
        recheck = self.provider.complete_structured.call_args_list[1]
        self.assertIn('only opening', recheck.args[1][-1]['content'])
        self.assertIsNotNone(recheck.kwargs['image_data'])

    def test_launch_uses_catalog_names_in_the_prompt_and_resolves_the_id(self):
        self.desktop.bridge.catalog = Mock(return_value=CALCULATOR['installed_apps'])
        task = ComputerTask('Open Calculator and compute (2+2)*3', automatic=True, goal_mode='inspect')
        self.provider.complete_structured.return_value = {'status': 'action', 'message': 'Open Calculator.', 'risk': 'navigation',
            'action': {'type': 'launch_app', 'bundle_id': 'Calculator'}}
        step = self.propose(task)
        self.assertEqual(step['action']['bundle_id'], 'com.apple.calculator')
        self.assertFalse(step['review'])
        prompt = self.provider.complete_structured.call_args.args[1][0]['content']
        self.assertIn('"installed_apps":["Calculator"]', prompt)
        self.provider.complete_structured.return_value = {'status': 'action', 'message': 'Open it.', 'risk': 'navigation',
            'action': {'type': 'launch_app', 'bundle_id': 'Nonexistent Tool'}}
        with self.assertRaises(Exception):
            self.propose(ComputerTask('Open a tool', goal_mode='inspect'))

    def test_done_after_real_work_is_not_second_guessed(self):
        task = ComputerTask('Open Calculator and compute (2+2)*3', automatic=True, goal_mode='inspect')
        for kind in ('launch_app', 'press_control', 'press_control', 'press_control', 'press_control'):
            task.record({'type': kind, 'status': 'submitted'})
        self.provider.complete_structured.return_value = {'status': 'done', 'message': 'The display shows 4.', 'risk': 'navigation', 'action': None}
        self.assertEqual(self.propose(task)['status'], 'done')
        self.assertEqual(self.provider.complete_structured.call_count, 1)

    def test_notepad_drafting_continues_to_the_editor_after_launch(self):
        self.desktop.bridge.value['controls'] = []
        task = ComputerTask('Open Notepad and write a summary of my day', automatic=True, goal_mode='draft')
        task.record({'type': 'launch_app', 'label': 'Open TextEdit', 'status': 'submitted'})
        self.provider.complete_structured.side_effect = [
            {'status': 'done', 'message': 'TextEdit opened.', 'risk': 'navigation', 'action': None},
            {'status': 'action', 'message': 'Choose New Document.', 'risk': 'navigation',
             'action': {'type': 'click_point', 'x': .3, 'y': .8, 'label': 'New Document'}}]
        step = self.propose(task)
        self.assertEqual(step['action']['type'], 'click_point')
        self.assertIn('capture_guard', step['action'])


class TeamsInspectionTests(Harness):
    regions = TEAMS_REGIONS

    def test_post_in_channel_under_a_model_point_is_rejected_for_inspection(self):
        self.desktop.bridge.value['controls'] = []
        task = ComputerTask('Open Teams and check whether I have tasks', automatic=True, goal_mode='inspect')
        self.provider.complete_structured.side_effect = [
            # The label claims Activity, but the point lies on OCR text "Post in channel".
            {'status': 'action', 'message': 'Check activity.', 'risk': 'navigation',
             'action': {'type': 'click_point', 'x': .55, 'y': .91, 'label': 'Activity'}},
            {'status': 'action', 'message': 'Open Activity.', 'risk': 'navigation',
             'action': {'type': 'click_point', 'x': .05, 'y': .21, 'label': 'Activity'}}]
        step = self.propose(task)
        # The click now snaps onto the OCR text "Activity" instead of being rejected and retried.
        self.assertEqual((step['action']['x'], step['action']['y']), (.06, .215))
        self.assertEqual(self.provider.complete_structured.call_count, 1)

    def test_a_point_on_post_in_channel_without_a_matching_label_is_still_rejected(self):
        self.desktop.bridge.value['controls'] = []
        task = ComputerTask('Open Teams and check whether I have tasks', automatic=True, goal_mode='inspect')
        self.provider.complete_structured.side_effect = [
            {'status': 'action', 'message': 'Check the feed.', 'risk': 'navigation',
             'action': {'type': 'click_point', 'x': .55, 'y': .91, 'label': 'Feed'}},
            {'status': 'action', 'message': 'Open Activity.', 'risk': 'navigation',
             'action': {'type': 'click_point', 'x': .05, 'y': .21, 'label': 'Activity'}}]
        self.propose(task)
        self.assertIn('inspection-only', self.provider.complete_structured.call_args.args[1][-1]['content'])


    def test_scope_from_the_intent_call_avoids_a_classifier_call(self):
        class Provider:
            classified = 0
            def classify_task_goal(self, goal):
                Provider.classified += 1
                return 'inspect'
            def complete_structured(self, *args, **kwargs):
                return {'status': 'done', 'message': 'Assignments shows 0 tasks.', 'risk': 'navigation', 'action': None}
        task = ComputerTask('check tasks', goal_mode='inspect')
        task.receipts.append({'type': 'press_control'})
        task.propose(self.desktop, self.vision, Provider(), {'x': 0, 'y': 0})
        self.assertEqual(Provider.classified, 0)
        legacy = ComputerTask('check tasks')
        legacy.receipts.append({'type': 'press_control'})
        legacy.propose(self.desktop, self.vision, Provider(), {'x': 0, 'y': 0})
        self.assertEqual(Provider.classified, 1)


    def test_step_timing_is_recorded_without_screen_content(self):
        task = ComputerTask('check tasks', goal_mode='inspect')
        task.receipts.append({'type': 'press_control'})
        self.regions = TEAMS_REGIONS+[region('No outstanding tasks', .3, .3)]
        self.provider.complete_structured.side_effect = [
            {'status': 'done', 'message': 'Private Teams text', 'risk': 'navigation', 'action': None},
            {'met': True, 'missing': '', 'evidence': 'No outstanding tasks'},
            {'message': 'Private Teams text'}]
        self.propose(task)
        stage = task.timings[-1]
        self.assertEqual(stage['text_regions'], 6)
        self.assertEqual([call['mode'] for call in stage['model']], ['image'])
        self.assertNotIn('Private', repr(stage))


class StopDuringInferenceTests(Harness):
    def test_stop_reaches_the_in_flight_model_request_and_no_action_is_proposed(self):
        import threading
        from companion.ollama import RequestCancelled
        task = ComputerTask('Open Teams and check tasks', automatic=True, goal_mode='inspect')
        class SlowProvider:
            def complete_structured(self, *args, cancel=None, **kwargs):
                # Stands in for the streaming transport, which closes its socket on cancel.
                if cancel.wait(10):
                    raise RequestCancelled()
                return {'status': 'action', 'message': 'late', 'risk': 'navigation',
                        'action': {'type': 'scroll', 'direction': 'down'}}
        outcome = []
        def run():
            try:
                outcome.append(task.propose(self.desktop, self.vision, SlowProvider(), {'x': 0, 'y': 0}))
            except RuntimeError as error:
                outcome.append(error)
        worker = threading.Thread(target=run)
        worker.start()
        time.sleep(0.1)
        started = time.monotonic()
        task.stop()
        worker.join(2)
        self.assertLess(time.monotonic()-started, 1)
        self.assertIsInstance(outcome[0], RequestCancelled)
        self.assertIsNone(task.pending)


class LiveTeamsLogTests(Harness):
    """From a live macOS log: 10 slow steps, each text pass then image pass, six
    identical Activity clicks, and a separate Safari launch before navigating."""
    regions = TEAMS_REGIONS
    activity = {'status': 'action', 'message': 'Activity has a badge.', 'risk': 'navigation',
                'action': {'type': 'click_point', 'x': .05, 'y': .21, 'label': 'Activity section with notification badge'}}

    def setUp(self):
        super().setUp()
        self.desktop.bridge.value['controls'] = []


    def test_third_identical_click_is_corrected_and_then_stopped(self):
        task = ComputerTask('Open Teams and check tasks', automatic=True, goal_mode='inspect')
        for _ in range(2):
            task.record({'type': 'click_point', 'label': 'Activity section with notification badge', 'status': 'submitted'})
        done = {'status': 'done', 'message': 'Activity lists 2 assignments due Friday.', 'risk': 'navigation', 'action': None}
        self.regions = TEAMS_REGIONS+[region('2 assignments due Friday', .3, .3, .3)]
        self.provider.complete_structured.side_effect = [self.activity, done,
            {'met': True, 'missing': '', 'evidence': '2 assignments due Friday'},
            {'message': 'Two assignments are due Friday.'}]
        self.assertEqual(self.propose(task)['status'], 'done')
        self.assertIn('already ran twice', self.provider.complete_structured.call_args_list[1].args[1][-1]['content'])
        self.provider.complete_structured.side_effect = [self.activity, self.activity,
            {'status': 'needs_user', 'message': 'The requested result cannot be verified.', 'risk': 'navigation', 'action': None}]
        step = self.propose(task)
        self.assertEqual(step['status'], 'needs_user')
        self.assertIsNone(task.pending)

    def test_reported_a_question_whose_clicks_change_nothing_is_answered_from_the_screen(self):
        # Live (v2): "…tell me what year they were born" clicked "Born 7 November 1867" until it stopped.
        task = ComputerTask('go to the Wikipedia article on Marie Curie and tell me what year they were born', automatic=True, goal_mode='inspect')
        born = {'status': 'action', 'message': 'Click the birth date.', 'risk': 'navigation',
                'action': {'type': 'click_point', 'x': .05, 'y': .21, 'label': 'Born 7 November 1867'}}
        for _ in range(2):
            task.record({'type': 'click_point', 'label': 'Born 7 November 1867', 'status': 'submitted'})
        self.regions = [region('Marie Curie - Wikipedia', .3, .1, .3), region('Born 7 November 1867', .3, .3, .3)]
        self.desktop.bridge.value.update(app={'name': 'Safari', 'bundle_id': 'com.apple.Safari'},
            window=dict(self.desktop.bridge.value['window'], title='Marie Curie - Wikipedia', document='https://en.wikipedia.org/wiki/Marie_Curie'))
        self.provider.complete_structured.side_effect = [born,
            {'status': 'done', 'message': 'Marie Curie was born in 1867.', 'risk': 'navigation', 'action': None},
            {'met': True, 'missing': '', 'evidence': 'Born 7 November 1867'},
            {'message': 'Marie Curie was born in 1867.'}]
        step = self.propose(task)
        self.assertEqual((step['status'], step['message']), ('done', 'Marie Curie was born in 1867.'))
        self.assertIsNone(task.pending)

    def test_digit_presses_may_repeat(self):
        task = ComputerTask('compute 22+1', goal_mode='inspect')
        for _ in range(2):
            task.record({'type': 'press_control', 'label': '2', 'status': 'submitted'})
        self.assertEqual(task.repeats({'type': 'press_control', 'label': '2'}), 0)

    def test_launching_the_browser_for_a_known_web_app_becomes_the_navigation(self):
        safari = [{'name': 'Safari', 'bundle_id': 'com.apple.Safari'}]
        self.desktop.bridge.value['installed_apps'] = safari
        self.desktop.bridge.catalog = Mock(return_value=safari)
        task = ComputerTask('open teams in a new safari window and check tasks', automatic=True, goal_mode='inspect')
        self.provider.complete_structured.return_value = {'status': 'action', 'message': 'Launch Safari.', 'risk': 'navigation',
                                                         'action': {'type': 'launch_app', 'bundle_id': 'Safari'}}
        step = self.propose(task)
        self.assertEqual((step['action']['type'], step['action']['url'], step['action']['browser_mode']),
                         ('browse_url', 'https://teams.microsoft.com', 'window'))
        self.assertFalse(step['review'])
        self.assertEqual(self.provider.complete_structured.call_count, 0)  # opened in code, no model step

    def test_browser_is_not_launched_as_a_separate_step_for_other_sites(self):
        safari = [{'name': 'Safari', 'bundle_id': 'com.apple.Safari'}]
        self.desktop.bridge.value['installed_apps'] = safari
        self.desktop.bridge.catalog = Mock(return_value=safari)
        task = ComputerTask('open the company portal in safari and check tasks', automatic=True, goal_mode='inspect')
        browse = {'status': 'action', 'message': 'Open portal.', 'risk': 'navigation',
                  'action': {'type': 'browse_url', 'bundle_id': 'com.apple.Safari', 'url': 'https://portal.example.com/',
                             'browser_mode': 'default', 'label': 'Open portal'}}
        self.provider.complete_structured.side_effect = [
            {'status': 'action', 'message': 'Launch Safari.', 'risk': 'navigation', 'action': {'type': 'launch_app', 'bundle_id': 'Safari'}}, browse]
        step = self.propose(task)
        self.assertEqual(step['action']['type'], 'browse_url')
        self.assertIn('browse_url opens the browser itself', self.provider.complete_structured.call_args.args[1][-1]['content'])


class PendingTextTests(unittest.TestCase):
    def test_conversation_is_never_reported_as_pending_work(self):
        plan = preserve_desktop_goal({'operation': 'answer', 'app_name': '', 'target': '', 'remaining_request': 'yo wyd'}, 'yo wyd')
        self.assertEqual(plan['remaining_request'], '')
        plan = preserve_desktop_goal({'operation': 'launch_app', 'app_name': 'Teams', 'target': '', 'remaining_request': 'check my tasks'},
                                     'open teams and check my tasks')
        self.assertEqual(plan['operation'], 'computer_task')


class ThinEvidenceTests(Harness):
    regions = [region('OK', .5, .5)]

    def test_one_ocr_fragment_without_controls_goes_straight_to_the_image(self):
        self.desktop.bridge.value['controls'] = []
        task = ComputerTask('Click the gear icon', automatic=True, goal_mode='inspect')
        self.provider.complete_structured.return_value = {'status': 'action', 'message': 'Click gear.', 'risk': 'navigation',
            'action': {'type': 'click_point', 'x': .9, 'y': .1, 'label': 'Gear'}}
        self.propose(task)
        self.assertEqual(self.provider.complete_structured.call_count, 1)
        self.assertIsNotNone(self.provider.complete_structured.call_args.kwargs['image_data'])


class HighlightOnlyTests(unittest.TestCase):
    def test_mark_or_show_where_never_becomes_an_activating_operation(self):
        for request, operation in [("Mark where the left terminal's close button is", 'close_window'),
                                   ('Show me where the Send button is', 'click_point'),
                                   ('where is the Post in channel button?', 'computer_task'),
                                   ('Highlight the Desktop tools section', 'press_control')]:
            plan = preserve_desktop_goal({'operation': operation, 'app_name': '', 'target': 'x', 'referent': 'visible_target'}, request)
            self.assertEqual(plan['operation'], 'highlight', request)

    def test_explicit_activation_after_locating_is_not_downgraded(self):
        self.assertFalse(highlight_only('Show me where Send is and click it'))
        plan = preserve_desktop_goal({'operation': 'click_point', 'app_name': '', 'target': 'x'}, 'Find the Help link and then click it')
        self.assertEqual(plan['operation'], 'computer_task')
        plan = preserve_desktop_goal({'operation': 'launch_app', 'app_name': 'Calculator', 'target': '',
                                      'remaining_request': 'sum 2+2', 'goal_mode': 'inspect'}, 'Open Calculator and compute (2+2)*3')
        self.assertEqual((plan['operation'], plan['goal_mode']), ('computer_task', 'inspect'))


if __name__ == '__main__':
    unittest.main()


class ClickGroundingTests(unittest.TestCase):
    """Teams 'click Activity' missed twice, then failed on a 0–1000 coordinate."""
    REGIONS = [{'text': 'Activity', 'x': .010, 'y': .200, 'width': .030, 'height': .015},
               {'text': 'Chat', 'x': .015, 'y': .260, 'width': .020, 'height': .015},
               {'text': 'Turn on desktop notifications', 'x': .40, 'y': .05, 'width': .20, 'height': .02},
               {'text': 'Activity', 'x': .30, 'y': .10, 'width': .05, 'height': .02}]

    def test_click_snaps_to_the_named_ocr_text_nearest_the_models_guess(self):
        from companion.computer_task import snap_click
        snapped = snap_click({'type': 'click_point', 'x': .05, 'y': .23, 'label': 'Activity icon'}, self.REGIONS)
        self.assertEqual((snapped['x'], snapped['y']), (.025, .2075))
        header = snap_click({'type': 'click_point', 'x': .33, 'y': .12, 'label': 'Activity'}, self.REGIONS)
        self.assertEqual((header['x'], header['y']), (.325, .11))

    def test_unmatched_label_keeps_the_models_point(self):
        from companion.computer_task import snap_click
        action = {'type': 'click_point', 'x': .5, 'y': .5, 'label': 'Calendar icon'}
        self.assertEqual(snap_click(action, self.REGIONS), action)

    def test_accessible_control_bounds_are_used_too(self):
        from companion.computer_task import snap_click
        snapped = snap_click({'type': 'click_point', 'x': .5, 'y': .5, 'label': 'Calls button'}, [],
                             [{'label': 'Calls', 'bounds': {'x': 100, 'y': 300, 'width': 40, 'height': 40}}],
                             {'x': 0, 'y': 0, 'width': 1000, 'height': 1000})
        self.assertEqual((snapped['x'], snapped['y']), (.12, .32))

    def test_qwen_grid_coordinates_are_accepted(self):
        from companion.computer_task import normalize_click
        self.assertEqual(normalize_click({'x': 25, 'y': 207.5})['x'], .025)
        self.assertEqual(normalize_click({'x': .5, 'y': 1.0}), {'x': .5, 'y': .999})
        self.assertEqual(normalize_click({'x': .5, 'y': .4}), {'x': .5, 'y': .4})
        self.assertEqual(normalize_click({'x': 2000, 'y': 5})['x'], 2000)  # still rejected later
        self.assertEqual(normalize_click({'x': 500, 'y': .3})['x'], 500)

    def test_inspect_goal_can_click_activity_next_to_a_notifications_banner(self):
        from companion.computer_task import validate_step
        desktop = DesktopContext(FakeBridge())
        step = validate_step({'status': 'action', 'message': 'Opening Activity.', 'risk': 'navigation',
                              'action': {'type': 'click_point', 'x': 480, 'y': 62, 'label': 'Activity'}},
                             desktop, {'controls': []}, goal_mode='inspect', text_regions=self.REGIONS)
        self.assertEqual((step['action']['x'], step['action']['y']), (.325, .11))


class NotificationViewTests(unittest.TestCase):
    """Reported: 'any new notifications on Teams?' read the current feed instead of opening Activity."""
    REGIONS = [{'text': 'Post in channel', 'x': .40, 'y': .90, 'width': .1, 'height': .02},
               {'text': 'Activity', 'x': .30, 'y': .10, 'width': .05, 'height': .02},
               {'text': 'Activity', 'x': .010, 'y': .200, 'width': .030, 'height': .015}]

    def test_notification_goal_opens_the_rail_activity_view_first_in_code(self):
        vision = Mock()
        vision.click_guard.return_value = {'guard': True}
        desktop = DesktopContext(FakeBridge())
        task = ComputerTask('are there any new notifications on the teams page', automatic=True, goal_mode='inspect')
        step = task.view_step(desktop, {'controls': []}, self.REGIONS, {'screen': {'x': 0, 'y': 0, 'width': 1000, 'height': 1000}}, 'img', vision)
        self.assertEqual(step['action']['type'], 'click_point')
        self.assertEqual((step['action']['x'], step['action']['y']), (.025, .2075))  # the left rail, not the page header
        # A click refused before it ran (the screen changed) is proposed again (live: the retry
        # went to the model, which invented control IDs); once it ran, never again.
        self.assertEqual(task.view_step(desktop, {'controls': []}, self.REGIONS, {}, 'img', vision)['action']['label'], 'Activity')
        task.record({'type': 'click_point', 'label': 'Activity', 'status': 'submitted'})
        self.assertIsNone(task.view_step(desktop, {'controls': []}, self.REGIONS, {}, 'img', vision))
        other = ComputerTask('read my last email', automatic=True, goal_mode='inspect')
        self.assertIsNone(other.view_step(desktop, {'controls': []}, self.REGIONS, {}, 'img', vision))


@unittest.skipUnless(__import__('sys').platform == 'darwin', 'Qt raises activate the app only on macOS')
class FocusTests(unittest.TestCase):
    """Live evaluation: after "Microsoft Teams." opened Teams in Safari, A11y itself was the
    frontmost app. On macOS Qt's raise_() of the mascot activated the whole process, taking
    keyboard focus from the app being worked in after every action."""

    def test_launcher_keeps_raising_the_mascot_from_activating_a11y(self):
        import os
        import sys
        import tempfile
        import a11y_desktop
        seen = {}

        class Stop(Exception):
            pass

        def application(*args):
            seen['raise_process'] = os.environ.get('QT_MAC_SET_RAISE_PROCESS')
            raise Stop()
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'A11Y_DATA_DIR': directory}), \
                patch.object(sys, 'argv', ['a11y_desktop.py']), patch.object(sys, 'excepthook', sys.excepthook), \
                patch('PyQt6.QtWidgets.QApplication', application), patch('companion.instance.DesktopInstance'):
            os.environ.pop('QT_MAC_SET_RAISE_PROCESS', None)
            with self.assertRaises(Stop):
                a11y_desktop.main()
        self.assertEqual(seen['raise_process'], '0')


class LiveRoutingTests(unittest.TestCase):
    """Live evaluation (scripts/live_eval.py) on qwen3:4b-instruct, wording as typed."""

    def setUp(self):
        import tempfile
        from companion.models import Identity
        from companion.service import Companion
        from companion.storage import Store
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        bridge = FakeBridge()
        bridge.catalog = Mock(return_value=[{'name': 'Safari', 'bundle_id': 'com.apple.Safari'},
                                            {'name': 'Photos', 'bundle_id': 'com.apple.Photos'}])
        bridge.calendar_events = Mock(return_value=[])
        bridge.app_state = Mock(return_value={'running': False})
        self.provider = Mock()
        self.app = Companion(Store(self.temp.name), Identity('company', 'employee'), self.provider)
        self.app.desktop_context = DesktopContext(bridge, clock=lambda: 100)
        for patcher in (patch('companion.mac_apps.available', return_value=True), patch('companion.mail_reader.available', return_value=True),
                        patch('companion.mail_reader.newest', side_effect=AssertionError('Mail must not be read'))):
            patcher.start()
            self.addCleanup(patcher.stop)

    def route(self, operation, target='', **extra):
        self.provider.interpret_request.return_value = dict({'operation': operation, 'app_name': '', 'target': target, 'reply': '',
                                                             'browser_mode': 'default', 'remaining_request': ''}, **extra)

    def test_notifications_on_teams_are_looked_for_in_teams_not_calendar_or_mail(self):
        # Routed to read_calendar three times in a row, and to read_mail in another session.
        for operation, target in (('read_calendar', 'today'), ('read_mail', ''), ('read_mail', '1')):
            self.route(operation, target)
            response = self.app.ask('check if I have new notifications on Teams')
            self.assertEqual(response['desktop_action']['type'], 'start_task')
            self.assertEqual(response['desktop_action']['goal'], 'check if I have new notifications on Teams')
        self.app.desktop_context.bridge.calendar_events.assert_not_called()
        self.route('read_mail', '1')
        self.assertEqual(self.app.ask('any new messages in Slack?')['desktop_action']['type'], 'start_task')

    def test_reported_show_me_my_reminders_opens_reminders(self):
        # Live (answers suite): "show me my reminders" read them out instead of opening the app.
        self.route('read_reminders', 'all')
        plan = self.app.prepare_request('show me my reminders')
        self.assertEqual((plan['operation'], plan['app_name']), ('launch_app', 'Reminders'))
        self.route('read_notes', 'rent')
        self.assertEqual(self.app.prepare_request('show me my note about rent')['operation'], 'computer_task')

    def test_reported_open_safari_and_search_is_one_search(self):
        # Live (answers suite): an app launch plus ten seconds of clicking.
        self.route('answer')
        plan = self.app.prepare_request('open safari and search for lasagna recipe')
        self.assertEqual((plan['operation'], plan['app_name'], plan['target']), ('browser_search', 'Safari', 'lasagna recipe'))
        self.provider.interpret_request.assert_not_called()  # decided in code

    def test_built_in_reads_still_answer_when_they_name_their_own_app(self):
        self.route('read_calendar', 'tomorrow')
        response = self.app.ask("what's on my calendar tomorrow")
        self.assertTrue(response['full_text'])
        self.app.desktop_context.bridge.calendar_events.assert_called_once()
        self.route('read_notes', 'trip on Photos')
        with patch('companion.mac_apps.read_notes', return_value='Your note.') as notes:
            self.assertEqual(self.app.ask('read my note trip on Photos')['message'], 'Your note.')
        notes.assert_called_once()

    def test_earlier_requests_are_sent_only_to_resolve_a_reference(self):
        # After two calendar questions the same model read the calendar for a Teams question.
        for request in ('read my second last email', "what's on my calendar tomorrow"):
            self.app.desktop_session.append({'role': 'user', 'content': request})
        self.route('computer_task', 'check if I have new notifications on Teams')
        self.app.prepare_request('check if I have new notifications on Teams')
        self.assertEqual(self.provider.interpret_request.call_args.args[1], [])
        self.app.prepare_request('do that again')
        self.assertEqual(self.provider.interpret_request.call_args.args[1], ['read my second last email', "what's on my calendar tomorrow"])

    def test_a_reminder_keeps_its_title_when_the_time_has_its_own_field(self):
        # Packed as "title | time" the model wrote only "6 PM" and the reminder lost its title.
        self.route('add_reminder', 'call mom', when='2026-10-23T18:00')
        action = self.app.ask('remind me to call mom at 6')['desktop_action']
        self.assertEqual((action['kind'], action['title']), ('add_reminder', 'call mom'))
        self.assertIn('18:00', action['label'])
        self.route('add_event', 'Dentist | 30', when='2026-10-24T15:00')
        action = self.app.ask('dentist tomorrow at 3 for half an hour')['desktop_action']
        self.assertEqual(action['end']-action['start'], 30*60)

    def test_a_reminder_title_is_copied_from_the_request_words(self):
        # Live (harder suite): the router titled it with the whole request, then with the time phrase.
        request = 'Put A11y test 1793364930750777000900 in Reminders for tomorrow at 8 in the morning.'
        self.route('add_reminder', request, when='tomorrow at 8 in the morning')
        self.provider.complete_structured.return_value = {'title': 'A11y test 1793364930750777000900'}
        action = self.app.ask(request)['desktop_action']
        self.assertEqual(action['title'], 'A11y test 1793364930750777000900')
        call = self.provider.complete_structured.call_args
        choices = call.kwargs['schema']['properties']['title']['enum']
        self.assertIn('A11y test 1793364930750777000900', choices)
        self.assertNotIn('A11y test 17933647000900', choices)  # free text once dropped digits
        # A title that is not the request's own words is ignored; the router's stays.
        self.route('add_reminder', 'call mom', when='2026-10-23T18:00')
        self.provider.complete_structured.return_value = {'title': 'phone your mother'}
        self.assertEqual(self.app.ask('remind me to call mom at 6')['desktop_action']['title'], 'call mom')

    def test_youtube_search_is_one_navigation_without_a_model(self):
        # The task opened YouTube, then clicked Safari's New Tab and address bar (live).
        response = self.app.ask('open youtube and search for lo-fi')
        self.assertEqual(response['desktop_action']['type'], 'browse_url')
        self.assertEqual(response['desktop_action']['url'], 'https://www.youtube.com/results?search_query=lo-fi')
        self.provider.interpret_request.assert_not_called()
        plan = self.app.prepare_request('open youtube and search for rock and roll')
        self.assertEqual(plan['operation'], 'computer_task')  # unclear where the query ends: the task decides


class DeterministicSamplingTests(unittest.TestCase):
    """At temperature 0.2 the same request was routed three different ways in one session."""

    def test_every_local_model_call_is_greedy_with_a_fixed_seed(self):
        from companion.ollama import OllamaProvider
        from companion.provider import AIConfig
        provider = OllamaProvider(AIConfig(base_url='http://127.0.0.1:11434', model='m', vision_model='', timeout=5))
        provider.metadata['m'] = {'capabilities': ['completion']}
        sent = []
        def request(path, body=None, **kwargs):
            sent.append(json.loads(json.dumps(body)))
            return {'done': True, 'done_reason': 'stop', 'message': {'content': '{"a": 1}'}}
        provider.request = request
        provider.complete('system', [{'role': 'user', 'content': 'x'}], response_schema={'type': 'object'})
        provider.complete('system', [{'role': 'user', 'content': 'x'}], response_schema={'type': 'object'})
        self.assertEqual([(body['options']['temperature'], body['options']['seed']) for body in sent], [(0, 7), (0, 7)])


class CodeClickTests(Harness):
    """"click on Chat" in Teams took five model calls and ~20 s for one click (live)."""
    snapshot = dict(CALCULATOR, controls=[], app={'name': 'Safari', 'bundle_id': 'com.apple.Safari', 'pid': 7})
    regions = TEAMS_REGIONS

    def test_one_labelled_target_is_clicked_in_code_and_the_task_ends_after_it(self):
        task = ComputerTask('click on Chat', automatic=True)
        step = self.propose(task)
        self.provider.complete_structured.assert_not_called()
        self.provider.classify_task_goal.assert_not_called()
        self.assertEqual((step['action']['type'], step['action']['label']), ('click_point', 'Chat'))
        self.assertEqual((step['action']['x'], step['action']['y']), (.06, .315))
        self.assertFalse(step['review'])
        task.record({'type': 'click_point', 'label': 'Chat', 'status': 'submitted'})
        self.regions = TEAMS_REGIONS+[region('Recent chats', .2, .1)]
        self.assertEqual(self.propose(task), {'status': 'done', 'action': None, 'message': 'Clicked Chat.'})
        self.provider.complete_structured.assert_not_called()

    def test_a_click_refused_before_it_ran_is_proposed_again(self):
        task = ComputerTask('click on Chat', automatic=True)
        self.propose(task)
        task.pending = None  # the guard refused it: no receipt
        step = self.propose(task)
        self.assertEqual(step['action']['label'], 'Chat')
        self.provider.complete_structured.assert_not_called()

    def test_an_unchanged_screen_after_the_click_is_reported(self):
        task = ComputerTask('click on Chat', automatic=True)
        self.propose(task)
        task.record({'type': 'click_point', 'label': 'Chat', 'status': 'submitted'})
        with patch('companion.computer_task.time.sleep'):
            result = self.propose(task)
        self.assertEqual(result['status'], 'needs_user')
        self.assertIn('nothing on the screen changed', result['message'])

    def test_an_accessible_control_is_pressed_and_sensitive_labels_still_need_review(self):
        self.desktop.bridge.value['controls'] = [{'id': 'control-0', 'label': 'Send', 'role': 'AXButton', 'enabled': True,
                                                  'bounds': {'x': 900, 'y': 600, 'width': 60, 'height': 30}}]
        step = self.propose(ComputerTask('click Send', automatic=True))
        self.assertEqual(step['action']['type'], 'press_control')
        self.assertTrue(step['review'])

    def test_several_or_no_matches_are_left_to_the_model(self):
        self.regions = TEAMS_REGIONS+[region('Chat', .5, .1)]
        self.provider.classify_task_goal = None
        self.provider.complete_structured.return_value = {'status': 'needs_user', 'message': 'Which Chat?', 'risk': 'navigation', 'action': None}
        self.propose(ComputerTask('click on Chat', automatic=True, goal_mode='inspect'))
        self.provider.complete_structured.assert_called()
        self.provider.complete_structured.reset_mock()
        self.propose(ComputerTask('click the first email in Mail', automatic=True, goal_mode='inspect'))
        self.provider.complete_structured.assert_called()


class ListRowTests(Harness):
    """"click the first email in Mail": the model clicked the window title "All Inboxes – 17.865
    messages" twice, then said done (live, three runs out of three)."""
    snapshot = dict(CALCULATOR, controls=[], app={'name': 'Mail', 'bundle_id': 'com.apple.mail', 'pid': 7},
                    window={'title': 'All Inboxes', 'bounds': {'x': 0, 'y': 0, 'width': 1000, 'height': 700}},
                    lists=[{'role': 'AXOutline', 'label': 'mailboxes', 'bounds': {'x': 0, 'y': 80, 'width': 200, 'height': 600},
                            'rows': [{'x': 0, 'y': 85 + 24*i, 'width': 200, 'height': 24} for i in range(5)]},
                           {'role': 'AXTable', 'label': 'messages', 'bounds': {'x': 200, 'y': 80, 'width': 300, 'height': 99999},
                            'rows': [{'x': 200, 'y': 90 + 80*i, 'width': 300, 'height': 80} for i in range(12)]}])
    regions = [region('All Inboxes – 17.865 messages', .3, .02, .3)]

    def test_the_first_row_of_the_message_list_is_clicked_in_code(self):
        task = ComputerTask('click the first email in Mail', automatic=True)
        step = self.propose(task)
        self.provider.complete_structured.assert_not_called()
        self.assertEqual(step['action']['type'], 'click_point')
        self.assertEqual((step['action']['x'], step['action']['y']), (.35, round(130/700, 4)))  # the messages table, row 1
        self.assertEqual(step['action']['label'], 'First email')
        task.record({'type': 'click_point', 'label': 'First email', 'status': 'submitted'})
        self.assertEqual(self.propose(task), {'status': 'done', 'action': None, 'message': 'Clicked the first email.'})

    def test_rows_outside_the_window_and_other_apps_are_not_used(self):
        task = ComputerTask('double-click the tenth message', automatic=True, goal_mode='inspect')
        self.desktop.bridge.value['window']['bounds']['height'] = 500  # rows 6+ are below the window
        self.provider.complete_structured.return_value = {'status': 'needs_user', 'message': 'Not visible.', 'risk': 'navigation', 'action': None}
        self.propose(task)
        self.provider.complete_structured.assert_called()
        self.provider.complete_structured.reset_mock()
        self.desktop.bridge.value['app'] = {'name': 'Safari', 'bundle_id': 'com.apple.Safari', 'pid': 7}
        self.propose(ComputerTask('click the first email in Mail', automatic=True, goal_mode='inspect'))
        self.provider.complete_structured.assert_called()  # Mail is not in front: the model opens it first

    def test_list_rows_stay_out_of_model_prompts(self):
        from companion.computer_task import compact_desktop
        self.assertNotIn('lists', compact_desktop(self.snapshot))


class ReviewFindingTests(LiveRoutingTests):
    """Edge cases of the live-evaluation fixes, found in review."""

    def test_a_past_reminder_time_moves_to_the_next_occurrence(self):
        # At 18:59 "remind me to call mom at 6" was proposed for today 18:00 (live).
        from datetime import datetime
        from companion.service import upcoming
        now = datetime(2026, 10, 23, 18, 59)
        self.assertEqual(upcoming('2026-10-23T18:00', 'call mom at 6', now), '2026-10-24T18:00')
        self.assertEqual(upcoming('2026-10-23T06:00', 'call mom at 6', now), '2026-10-24T06:00')
        self.assertEqual(upcoming('2026-10-23T09:00', 'call mom at 9', datetime(2026, 10, 23, 10, 0)), '2026-10-23T21:00')
        self.assertEqual(upcoming('2026-10-23T09:00', 'call mom at 9am', datetime(2026, 10, 23, 10, 0)), '2026-10-24T09:00')
        self.assertEqual(upcoming('2026-10-24T15:00', 'dentist tomorrow at 3', now), '2026-10-24T15:00')
        self.assertEqual(upcoming('2026-10-23T09:00', 'dentist today at 9am', now), '2026-10-23T09:00')  # as said
        self.assertEqual(upcoming('2026-10-22T18:59', 'call mom at 18:59', now), '2026-10-22T18:59')  # no crash at one day

    def test_follow_ups_without_a_pronoun_still_get_the_earlier_request(self):
        self.app.desktop_session.append({'role': 'user', 'content': "what's on my calendar today"})
        self.route('read_calendar', 'tomorrow')
        self.app.prepare_request('what about tomorrow?')
        self.assertEqual(self.provider.interpret_request.call_args.args[1], ["what's on my calendar today"])

    def test_vendor_names_and_the_mail_shortcut_respect_another_named_app(self):
        self.app.desktop_context.bridge.catalog.return_value.append({'name': 'Microsoft To Do', 'bundle_id': 'com.microsoft.to-do-mac'})
        self.route('read_reminders', 'all')
        self.assertEqual(self.app.ask('read my tasks in To Do')['desktop_action']['type'], 'start_task')
        self.route('read_mail', '1')
        self.assertEqual(self.app.ask('In Outlook, read my latest email')['desktop_action']['type'], 'start_task')

    def test_a_site_search_with_more_after_a_comma_is_not_one_query(self):
        plan = self.app.prepare_request('open youtube and search for cats, click the first result')
        self.assertEqual(plan['operation'], 'computer_task')


class CodeClickReviewTests(Harness):
    snapshot = dict(CALCULATOR, controls=[], app={'name': 'Mail', 'bundle_id': 'com.apple.mail', 'pid': 7},
                    lists=ListRowTests.snapshot['lists'])
    regions = [region('OK', .5, .5), region('Cancel', .6, .5)]

    def test_a_confirming_label_waits_for_the_user(self):
        # "OK" can confirm a deletion; the code click must not press it unreviewed.
        self.assertTrue(self.propose(ComputerTask('click OK', automatic=True))['review'])

    def test_a_click_that_changes_only_pixels_counts_as_done(self):
        task = ComputerTask('click Cancel', automatic=True)
        self.propose(task)
        task.record({'type': 'click_point', 'label': 'Cancel', 'status': 'submitted'})
        self.vision.click_guard.return_value = {'region_hash': 'changed'}
        self.assertEqual(self.propose(task)['status'], 'done')

    def test_the_noun_chooses_the_list(self):
        step = self.propose(ComputerTask('click the first mailbox in Mail', automatic=True))
        self.assertEqual(step['action']['x'], .1)  # the mailboxes outline, not the messages table


class ScreenDescriptionTests(LiveRoutingTests):
    """"What's on my screen" evaluated ~9,000 prompt tokens (6-8 s) with the full chat prompt (live)."""

    def test_a_short_prompt_and_compact_observation_describe_the_screen(self):
        from companion.service import SCREEN_SCHEMA, SCREEN_SYSTEM
        self.route('inspect_screen')
        self.provider.complete_structured.return_value = {'message': 'Mail is open with your inbox.'}
        self.app.desktop_context.enabled = True
        self.app.desktop_context.collect_once()
        response = self.app.ask("what's on my screen", mode='assist', image_data='data:image/png;base64,FRAME',
                                visual_context={'windows': [{'app': 'Mail', 'title': 'Inbox'}]})
        self.assertEqual(response['message'], 'Mail is open with your inbox.')
        self.assertIsNone(response['desktop_action'])
        system, messages = self.provider.complete_structured.call_args.args
        self.assertEqual(system, SCREEN_SYSTEM)
        self.assertEqual(self.provider.complete_structured.call_args.kwargs['schema'], SCREEN_SCHEMA)
        self.assertEqual(self.provider.complete_structured.call_args.kwargs['image_data'], 'data:image/png;base64,FRAME')
        observation = json.loads(messages[-2]['content'])
        self.assertEqual(observation['visible_windows'], ['Mail — Inbox'])
        self.assertNotIn('installed_apps', messages[-2]['content'])
        self.assertLess(len(system)+len(messages[-2]['content']), 3000)


class InspectionAnswerTests(Harness):
    """Teams Activity was open; the model twice proposed a Safari toolbar button as a change and
    the task ended "I could not find a safe next step" (live)."""
    regions = TEAMS_REGIONS

    def test_an_inspection_ends_with_what_the_screen_shows_and_no_action(self):
        task = ComputerTask('check whether I have tasks in Teams', automatic=True, goal_mode='inspect')
        task.record({'type': 'click_point', 'label': 'Assignments', 'status': 'submitted'})
        change = {'status': 'action', 'message': 'Press it.', 'risk': 'change', 'action': {'type': 'press_control', 'control_id': 'control-0'}}
        self.provider.complete_structured.side_effect = [change, change,
            {'status': 'done', 'message': 'Ana mentioned you in General.', 'risk': 'navigation', 'action': None},
            {'met': True, 'missing': '', 'evidence': 'Assignments'},
            {'message': 'Ana mentioned you in General.'}]
        step = self.propose(task)
        self.assertEqual(step, {'status': 'done', 'message': 'Ana mentioned you in General.', 'action': None})
        report = self.provider.complete_structured.call_args_list[2]
        self.assertEqual(report.kwargs['schema']['properties']['action'], {'type': 'null'})

    def test_other_goals_still_stop_with_nothing_done(self):
        task = ComputerTask('open Teams and post hello', automatic=True, goal_mode='change')
        task.record({'type': 'click_point', 'label': 'Activity', 'status': 'submitted'})
        bad = {'status': 'action', 'message': 'Press it.', 'risk': 'navigation', 'action': {'type': 'press_control', 'control_id': 'missing'}}
        self.provider.complete_structured.side_effect = [bad, bad]
        with self.assertRaises(Exception):
            self.propose(task)
        self.assertEqual(self.provider.complete_structured.call_count, 2)


class ScreenAnswerTests(Harness):
    """Inspection answers were vague ("Notifications are visible; no further action is needed").
    The finished answer is written from the screen's readable text, without the image."""
    regions = [region('Activity', .02, .2), region('Ana Popescu mentioned you', .3, .3, .2), region('Demo is due at 11', .3, .34, .2)]

    def test_the_answer_is_written_from_the_screen_text(self):
        from companion.computer_task import ANSWER_SYSTEM
        task = ComputerTask('check whether Ana wrote to me in Teams', automatic=True, goal_mode='inspect')
        task.record({'type': 'click_point', 'label': 'Chat', 'status': 'submitted'})
        self.provider.complete_structured.side_effect = [
            {'status': 'done', 'message': 'Messages are visible.', 'risk': 'navigation', 'action': None},
            {'met': True, 'missing': '', 'evidence': 'Ana Popescu mentioned you'},
            {'message': 'Ana Popescu mentioned you: the demo is due at 11.'}]
        self.assertEqual(self.propose(task)['message'], 'Ana Popescu mentioned you: the demo is due at 11.')
        answer = self.provider.complete_structured.call_args
        self.assertEqual(answer.args[0], ANSWER_SYSTEM)
        self.assertIsNone(answer.kwargs.get('image_data'))
        self.assertIn('Ana Popescu mentioned you', answer.args[1][0]['content'])

    def test_a_failed_answer_keeps_the_first_reading(self):
        from companion.provider import ProviderError
        task = ComputerTask('check whether Ana wrote to me', automatic=True, goal_mode='inspect')
        task.record({'type': 'click_point', 'label': 'Chat', 'status': 'submitted'})
        self.provider.complete_structured.side_effect = [
            {'status': 'done', 'message': 'Two mentions from Ana.', 'risk': 'navigation', 'action': None},
            {'met': True, 'missing': '', 'evidence': 'Ana Popescu mentioned you'},
ProviderError('down')]
        self.assertEqual(self.propose(task)['message'], 'Two mentions from Ana.')


class ConversationTests(LiveRoutingTests):
    """Reported transcript: "14" got no reply, "what number did I just say" got "I don't have access
    to your previous spoken words", and "are there any notifications that require my attention" right
    after opening Teams invented "You have 10 unread notifications. Click the bell icon"."""

    def setUp(self):
        super().setUp()
        self.provider.complete_structured.return_value = {'message': 'Got it: 14.'}

    def test_what_was_said_is_remembered_for_the_reply(self):
        self.route('answer', reply='Okay.')
        self.assertEqual(self.app.ask('14')['message'], 'Got it: 14.')
        self.provider.complete_structured.return_value = {'message': "You said 14."}
        self.provider.interpret_request.reset_mock()
        self.assertEqual(self.app.ask('what number did I just say')['message'], 'You said 14.')
        self.provider.interpret_request.assert_not_called()  # about this conversation: answered from memory
        from companion.service import CHAT_SYSTEM
        system, messages = self.provider.complete_structured.call_args.args
        self.assertEqual(system, CHAT_SYSTEM)
        self.assertEqual([m['content'] for m in messages[:2]], ['14', 'Got it: 14.'])

    def test_routing_sees_the_front_app_but_replies_do_not(self):
        self.app.desktop_context.enabled = True
        self.app.desktop_context.bridge.value = dict(self.app.desktop_context.bridge.value, app={'name': 'Safari', 'bundle_id': 'com.apple.Safari'},
                                                     window={'title': '(3) Activity | Microsoft Teams', 'bounds': {'x': 0, 'y': 0, 'width': 10, 'height': 10}})
        self.app.desktop_context.collect_once()
        self.route('answer', reply='Okay.')
        self.app.prepare_request('ok')
        self.assertEqual(self.provider.interpret_request.call_args.kwargs['context']['front_app'], 'Safari — Activity | Microsoft Teams')
        self.assertNotIn('front_app', self.provider.complete_structured.call_args.args[1][-2]['content'])

    def test_a_statement_is_never_typed_and_notifications_are_not_mail(self):
        self.route('type_text', 'Ana')
        plan = self.app.prepare_request('my name is Ana')
        self.assertEqual(plan['operation'], 'answer')
        self.route('type_text', 'hello')
        self.assertEqual(self.app.prepare_request('type hello')['operation'], 'type_text')
        self.route('read_mail', '1')
        self.assertEqual(self.app.prepare_request('are there any notifications that require my attention')['operation'], 'computer_task')

    def test_memory_keeps_no_email_text(self):
        with patch('companion.mail_reader.newest', return_value=('Ana <a@x>', 'Demo', 'Friday', 'The secret body.')):
            self.app.ask('read my last email')
        self.assertNotIn('secret body', json.dumps(list(self.app.conversation)))
        self.assertIn('Demo', self.app.conversation[-1]['content'])
        self.route('answer', reply='Okay.')
        self.app.ask('ok')


class ConversationPrivacyTests(ConversationTests):
    """Review of the conversation memory: what it may keep and to whom it may go."""

    def test_screen_and_app_content_is_forgotten_with_access_and_never_sent_to_a_cloud_model(self):
        self.app.remember('user', 'check my notifications')
        self.app.remember('assistant', 'Ana mentioned you in General.', private=True)
        self.route('answer', reply='Okay.')
        self.provider.cloud = True
        self.app.ask('ok')
        self.assertNotIn('Ana mentioned', json.dumps(self.provider.complete_structured.call_args.args[1]))
        self.app.forget_desktop()
        self.assertNotIn('Ana mentioned', json.dumps(list(self.app.conversation)))
        self.assertIn('check my notifications', json.dumps(list(self.app.conversation)))

    def test_a_request_that_mentions_the_conversation_still_acts(self):
        self.app.remember('user', 'my mom is visiting Friday')
        self.route('add_reminder', 'call mom', when='2026-10-30T09:00')
        plan = self.app.prepare_request('remind me tomorrow about what I told you')
        self.assertTrue(plan.get('app_write'))

    def test_the_next_email_follows_a_mail_read(self):
        with patch('companion.mail_reader.newest', return_value=('Ana <a@x>', 'Demo', 'Friday', 'Body')):
            self.app.ask('read my last email')
            self.route('read_mail', '2')
            plan = self.app.prepare_request('read the next one')
        self.assertTrue(plan.get('full_text'))
        self.route('answer', reply='Okay.')
        self.app.prepare_request('ok')
        self.route('read_mail', '2')
        self.assertEqual(self.app.prepare_request('read the next one')['operation'], 'computer_task')  # no mail just read


class ScreenAnswerAccessTests(Harness):
    regions = ScreenAnswerTests.regions

    def test_no_answer_from_the_screen_after_access_is_paused(self):
        task = ComputerTask('check whether Ana wrote to me in Teams', automatic=True, goal_mode='inspect')
        task.record({'type': 'click_point', 'label': 'Chat', 'status': 'submitted'})
        replies = [{'status': 'done', 'message': 'Messages are visible.', 'risk': 'navigation', 'action': None}]
        def reply(*args, **kwargs):
            if replies:
                return replies.pop(0)
            self.desktop.enabled = False  # paused while the final answer was being written
            return {'message': 'Ana mentioned you.'}
        self.provider.complete_structured.side_effect = reply
        with self.assertRaises(ValueError):
            self.propose(task)


class NotificationLoopTests(Harness):
    """"Are there any notifications that require my attention" alternated "Activity (1)" and "Bell
    icon" clicks until the 24-step limit (164 s, live)."""
    regions = ScreenAnswerTests.regions

    def test_the_opened_activity_view_is_answered_from_its_text(self):
        task = ComputerTask('are there any notifications that require my attention', automatic=True)
        task.record({'type': 'click_point', 'label': 'Activity', 'status': 'submitted'})
        self.provider.complete_structured.return_value = {'message': 'Ana Popescu mentioned you: the demo is due at 11.'}
        step = self.propose(task)
        self.assertEqual(step, {'status': 'done', 'message': 'Ana Popescu mentioned you: the demo is due at 11.', 'action': None})
        self.assertEqual(self.provider.complete_structured.call_count, 1)
        self.assertIsNone(self.provider.complete_structured.call_args.kwargs.get('image_data'))
        self.provider.classify_task_goal.assert_not_called()

    def test_alternating_clicks_on_the_same_targets_are_repeats(self):
        task = ComputerTask('check my notifications', automatic=True, goal_mode='inspect')
        for label in ('Activity (1)', 'Bell icon', 'Activity (2)', 'Bell icon'):
            task.record({'type': 'click_point', 'label': label, 'status': 'submitted'})
        # Twice each, alternating: one more try is allowed (opening two tabs is legitimate)…
        self.assertEqual(task.repeats({'type': 'click_point', 'label': 'Activity'}), 1)
        task.record({'type': 'click_point', 'label': 'Activity (1)', 'status': 'submitted'})
        # …a third time anywhere is a loop.
        self.assertEqual(task.repeats({'type': 'click_point', 'label': 'Activity'}), 2)
        self.assertEqual(task.repeats({'type': 'click_point', 'label': 'Calendar'}), 0)


class LookingAtTests(ConversationTests):
    """"Are there any notifications that require my attention" with Teams in front went to Mail twice
    (live): a goal that names no app is about the app the user is looking at."""

    def front(self):
        self.app.desktop_context.enabled = True
        self.app.desktop_context.bridge.value = dict(self.app.desktop_context.bridge.value, app={'name': 'Safari', 'bundle_id': 'com.apple.Safari'},
                                                     window={'title': 'Activity | Microsoft Teams', 'bounds': {'x': 0, 'y': 0, 'width': 10, 'height': 10}})
        self.app.desktop_context.collect_once()

    def test_the_task_knows_what_the_user_was_looking_at(self):
        self.front()
        self.route('computer_task', 'are there any notifications that require my attention')
        action = self.app.ask('are there any notifications that require my attention')['desktop_action']
        self.assertEqual(action['looking_at'], 'Safari — Activity | Microsoft Teams')
        self.route('computer_task', 'check Slack')
        self.assertNotIn('looking_at', self.app.ask('any new messages in Slack?')['desktop_action'])

    def test_the_task_prompt_carries_it(self):
        task = ComputerTask('any notifications for me?', looking_at='Safari — Activity | Microsoft Teams')
        self.assertEqual(task.looking_at, 'Safari — Activity | Microsoft Teams')
        self.assertIn('user_was_looking_at', SYSTEM)


class ScreenTargetTests(Harness):
    """Navigation evaluation: icons and web links (GitHub's "Explore", Hacker News "19 comments")
    were clicked by guessing pixels. Everything clickable is now numbered from the accessibility
    tree (web pages included) and OCR; the model picks a number and code knows where it is."""
    snapshot = dict(CALCULATOR, controls=CALCULATOR['controls'], app={'name': 'Safari', 'bundle_id': 'com.apple.Safari', 'pid': 7},
                    targets=[{'role': 'AXLink', 'label': '19 comments', 'bounds': {'x': 380, 'y': 140, 'width': 60, 'height': 20}},
                             {'role': 'AXButton', 'label': 'Open menu', 'bounds': {'x': 10, 'y': 10, 'width': 30, 'height': 30}}])
    regions = [region('19 comments', .385, .2, .05, .02), region('OBS Studio 33', .1, .2)]

    def test_targets_and_uncovered_text_are_numbered_once(self):
        from companion.computer_task import screen_marks, compact_marks
        marks = screen_marks(self.snapshot['targets'], self.regions, {'x': 0, 'y': 0, 'width': 1000, 'height': 700})
        self.assertEqual([(m['id'], m['kind'], m['label']) for m in marks],
                         [('t1', 'link', '19 comments'), ('t2', 'button', 'Open menu'), ('t3', 'text', 'OBS Studio 33')])
        self.assertEqual(compact_marks(marks)[0], 't1|link|19 comments|0.410,0.214')

    def test_the_model_picks_a_number_and_the_click_lands_on_its_center(self):
        self.provider.complete_structured.return_value = {'status': 'action', 'message': 'Open the comments.', 'risk': 'navigation',
                                                         'action': {'type': 'click_target', 'target_id': 't1'}}
        step = self.propose(ComputerTask('open the comments of the first story', automatic=True, goal_mode='inspect'))
        self.assertEqual((step['action']['type'], step['action']['label']), ('click_point', '19 comments'))
        self.assertEqual((step['action']['x'], step['action']['y']), (.41, round(150/700, 4)))
        self.assertIn('capture_guard', step['action'])
        call = self.provider.complete_structured.call_args
        prompt = call.args[1][1]['content']  # the observation; the goal is in the cached first message
        self.assertIn('t1|link|19 comments', prompt)
        self.assertNotIn('controls', json.loads(prompt)['desktop'])  # the same things, listed once
        variant = call.kwargs['schema']['anyOf'][0]['properties']['action']['anyOf'][0]
        self.assertEqual(variant['properties']['target_id'], {'enum': ['t1', 't2', 't3']})

    def test_an_unknown_number_is_rejected(self):
        from companion.computer_task import resolve_target
        from companion.models import ValidationError
        with self.assertRaises(ValidationError):
            resolve_target({'status': 'action', 'action': {'type': 'click_target', 'target_id': 't9'}}, [])

    def test_task_steps_ask_the_helper_for_targets(self):
        self.desktop.bridge.sample_targets = Mock(return_value=deepcopy(self.snapshot))
        self.provider.complete_structured.return_value = {'status': 'done', 'message': 'Done.', 'risk': 'navigation', 'action': None}
        self.propose(ComputerTask('look at the page', automatic=True, goal_mode='inspect'))
        self.desktop.bridge.sample_targets.assert_called()


class NavigationRoutingTests(LiveRoutingTests):
    """Navigation evaluation: "show me my Documents folder" moved the companion, "find the Bluetooth
    settings" described the screen; going somewhere is a task, whatever the place is."""

    def test_going_somewhere_is_a_task_not_movement_or_a_description(self):
        for operation, request in (('move_object', 'go to my Pictures folder'), ('inspect_screen', 'find the dark mode setting')):
            self.route(operation, request)
            self.assertEqual(self.app.prepare_request(request)['operation'], 'computer_task', request)
        self.route('move_object', 'the clock')
        self.assertEqual(self.app.prepare_request('hop over to the clock')['operation'], 'move_object')

    def test_a_screen_description_needs_the_request_to_be_about_the_screen(self):
        self.provider.complete_structured.return_value = {'message': 'It is 17:00 in Tokyo.'}
        self.route('inspect_screen')
        self.assertEqual(self.app.prepare_request('what time is it in Tokyo')['operation'], 'answer')
        self.route('inspect_screen')
        self.assertEqual(self.app.prepare_request("what's on my screen")['operation'], 'inspect_screen')

    def test_reading_a_page_needs_a_browser(self):
        self.route('read_page')
        self.assertEqual(self.app.prepare_request('show me the battery health')['operation'], 'computer_task')


class LaunchWithMoreTests(LiveRoutingTests):
    """"In System Settings, open Displays" only brought Settings forward (live, 2 of 2)."""

    def test_words_beyond_the_app_name_are_work_in_the_app(self):
        self.app.desktop_context.bridge.catalog.return_value.append({'name': 'System Settings', 'bundle_id': 'com.apple.systempreferences'})
        self.provider.interpret_request.return_value = {'operation': 'launch_app', 'app_name': 'System Settings', 'target': '', 'reply': '',
                                                        'browser_mode': 'default', 'remaining_request': ''}
        plan = self.app.prepare_request('in System Settings, open Displays')
        self.assertEqual(plan['operation'], 'computer_task')
        self.assertEqual(plan['opening']['operation'], 'launch_app')


class EchoedLaunchTests(LaunchWithMoreTests):
    def test_an_echoed_remainder_is_still_work_in_the_app(self):
        # "open the Calendar in Teams": launch_app Teams, remaining_request = the whole request (live).
        self.provider.interpret_request.return_value = {'operation': 'launch_app', 'app_name': 'Teams', 'target': 'Calendar', 'reply': '',
                                                        'browser_mode': 'default', 'remaining_request': 'open the Calendar in Teams'}
        self.assertEqual(self.app.prepare_request('open the Calendar in Teams')['operation'], 'computer_task')


class PromptCacheTests(Harness):
    """A task step re-read ~4,000 prompt tokens (2.8 s): the goal and app list shared a message with
    the changing observation. They now come first, unchanged from step to step, so Ollama keeps them."""
    regions = TEAMS_REGIONS

    def test_the_first_message_is_the_same_on_every_step(self):
        task = ComputerTask('open the calendar in Teams', automatic=True, goal_mode='inspect')
        self.provider.complete_structured.return_value = {'status': 'action', 'message': 'Scroll.', 'risk': 'navigation',
                                                         'action': {'type': 'scroll', 'direction': 'down'}}
        firsts = []
        for step in range(2):
            self.regions = TEAMS_REGIONS+[region('Step %d' % step, .5, .5)]
            self.propose(task)
            firsts.append(self.provider.complete_structured.call_args.args[1][0]['content'])
            task.record({'type': 'scroll', 'label': 'Scroll down', 'status': 'submitted'})
        self.assertEqual(firsts[0], firsts[1])
        self.assertNotIn('Step', firsts[0])


class NamedSiteSearchTests(LiveRoutingTests):
    """"On Wikipedia, search for Bucharest" became a web search for "Bucharest" (live)."""

    def test_a_search_on_a_named_site_opens_the_site_first(self):
        self.route('browser_search', 'Bucharest')
        plan = self.app.prepare_request('on Wikipedia, search for Bucharest and open the article')
        self.assertEqual(plan['operation'], 'computer_task')
        self.assertEqual(plan['opening']['target'], 'https://www.wikipedia.org')
        self.route('browser_search', 'weather in Bucharest')
        self.assertEqual(self.app.prepare_request('search for weather in Bucharest')['operation'], 'browser_search')


class SearchFieldEnterTests(Harness):
    """"On Wikipedia, search for Bucharest" typed the name and then waited: every Enter needed a confirmation."""
    snapshot = dict(CALCULATOR, controls=[], app={'name': 'Safari', 'bundle_id': 'com.apple.Safari', 'pid': 7}, focused_role='AXTextField',
                    focused_bounds={'x': 300, 'y': 200, 'width': 400, 'height': 30},
                    targets=[{'role': 'AXTextField', 'label': 'Search Wikipedia', 'bounds': {'x': 300, 'y': 200, 'width': 400, 'height': 30}}])

    def enter(self, goal_mode):
        self.provider.complete_structured.return_value = {'status': 'action', 'message': 'Search.', 'risk': 'navigation',
                                                         'action': {'type': 'press_key', 'key': 'enter'}}
        return self.propose(ComputerTask('on Wikipedia, search for Bucharest', automatic=True, goal_mode=goal_mode))

    def test_enter_in_a_search_field_runs_for_a_read_only_goal(self):
        self.assertFalse(self.enter('inspect')['review'])

    def test_enter_elsewhere_or_for_a_change_still_asks(self):
        self.assertTrue(self.enter('change')['review'])
        self.desktop.bridge.value['targets'][0]['label'] = 'Message'
        self.assertTrue(self.enter('inspect')['review'])


class SearchFollowUpTests(LiveRoutingTests):
    def test_more_work_after_the_query_makes_it_a_task(self):
        self.route('browser_search', 'weather in Bucharest')
        plan = self.app.prepare_request('search DuckDuckGo for weather in Bucharest and open the first result')
        self.assertEqual(plan['operation'], 'computer_task')
        self.assertEqual(plan['opening']['operation'], 'browser_search')


class StaleObservationTests(Harness):
    """Navigation evaluation: five valid steps were rejected as "Fresh desktop context is required"
    because the model answered more than 10 s after the observation."""

    def test_a_slow_answer_is_validated_against_a_fresh_reading(self):
        now = [100]
        self.desktop.clock = lambda: now[0]
        self.desktop.bridge.value['available_apps'] = [{'name': 'Calculator', 'bundle_id': 'com.apple.calculator'}]
        def slow(*args, **kwargs):
            now[0] += 12  # the model took 12 s
            return {'status': 'action', 'message': 'Switch.', 'risk': 'navigation', 'action': {'type': 'focus_app', 'bundle_id': 'com.apple.calculator'}}
        self.provider.complete_structured.side_effect = slow
        step = self.propose(ComputerTask('switch to the calculator', automatic=True, goal_mode='inspect'))
        self.assertEqual(step['action']['type'], 'focus_app')
        self.assertEqual(self.provider.complete_structured.call_count, 1)


class AskedForSensitiveTests(Harness):
    """"Sign me in with my passwords" was refused as "inspection-only" three times (live); what the
    goal asks for is proposed for confirmation instead. What it does not ask for is still refused."""
    snapshot = dict(CALCULATOR, controls=[], app={'name': 'Safari', 'bundle_id': 'com.apple.Safari', 'pid': 7})
    regions = [region('Sign in', .5, .5), region('Delete account', .5, .7)]

    def click(self, goal, label, y):
        self.provider.complete_structured.return_value = {'status': 'action', 'message': 'Sign in.', 'risk': 'change',
                                                         'action': {'type': 'click_point', 'x': .54, 'y': y, 'label': label}}
        return self.propose(ComputerTask(goal, automatic=True, goal_mode='inspect'))

    def test_the_sign_in_the_user_asked_for_waits_for_confirmation(self):
        step = self.click('sign me in with my passwords', 'Sign in', .515)
        self.assertEqual(step['action']['label'], 'Sign in')
        self.assertTrue(step['review'])

    def test_a_sensitive_click_the_goal_does_not_mention_is_still_refused(self):
        with self.assertRaises(Exception):
            self.click('sign me in with my passwords', 'Delete account', .715)
        with self.assertRaises(Exception):
            self.click('check my notifications', 'Sign in', .515)


class VoiceImportTests(unittest.TestCase):
    """Voice never started: the voice start and the readiness check imported faster_whisper at the
    same time and Python 3.14 reported an import deadlock (live log)."""

    def test_every_faster_whisper_import_shares_one_lock(self):
        import inspect
        from companion import installer, voice
        self.assertTrue(hasattr(voice.WHISPER_IMPORT, 'acquire'))
        for module in (voice, installer):
            source = inspect.getsource(module)
            for line in [l for l in source.splitlines() if 'faster_whisper' in l and ('import' in l) and 'find_spec' not in l and not l.strip().startswith('#')]:
                index = source.splitlines().index(line)
                self.assertIn('WHISPER_IMPORT', source.splitlines()[index-1], line)


class VariedSuiteRoutingTests(LiveRoutingTests):
    """Live evaluation v2 (random tasks): questions about the Mac, this article, text documents."""

    def test_questions_about_this_mac_are_read_in_code(self):
        # "What version of macOS am I running?" got "I don't have access to your system's version".
        self.route('system_info', 'macos_version')
        with patch('companion.mac_apps.system_info', return_value='You are running macOS 27.0.') as info:
            self.assertEqual(self.app.ask('what version of macOS am I running?')['message'], 'You are running macOS 27.0.')
        info.assert_called_once()

    def test_a_text_document_is_not_a_note(self):
        self.route('create_note', 'The quick brown fox')
        self.assertEqual(self.app.prepare_request("type 'The quick brown fox' into a new text document")['operation'], 'computer_task')
        self.route('create_note', 'Groceries | milk')
        self.assertTrue(self.app.prepare_request('make a note called Groceries saying milk').get('app_write'))


class PlanStartTests(Harness):
    """Live evaluation v2: the first vision steps clicked an app's name, a page title or Finder.
    A task now plans where to start and opens it in code."""

    def test_the_planned_app_is_opened_without_a_vision_step(self):
        textedit = [{'name': 'TextEdit', 'bundle_id': 'com.apple.TextEdit'}]
        self.desktop.bridge.catalog = Mock(return_value=textedit)
        self.desktop.bridge.value['installed_apps'] = textedit
        class Planning(Mock):
            def plan_task(self, observation):
                return {'start': 'app', 'app': 'TextEdit', 'url': '', 'query': '', 'steps': ['New document', 'Type'], 'done_when': 'text shown'}
        self.provider = Planning()
        step = self.propose(ComputerTask('write a shopping list in a new TextEdit document', automatic=True, goal_mode='draft'))
        self.assertEqual((step['action']['type'], step['action']['bundle_id']), ('launch_app', 'com.apple.TextEdit'))
        self.assertFalse(step['review'])
        self.provider.complete_structured.assert_not_called()
        self.vision.capture.assert_not_called()

    def test_a_missing_app_starts_at_its_website(self):
        safari = [{'name': 'Safari', 'bundle_id': 'com.apple.Safari'}]
        self.desktop.bridge.catalog = Mock(return_value=safari)
        self.desktop.bridge.value['installed_apps'] = safari
        class Planning(Mock):
            def plan_task(self, observation):
                return {'start': 'app', 'app': 'Notion', 'url': 'notion.so', 'query': '', 'steps': [], 'done_when': ''}
        self.provider = Planning()
        step = self.propose(ComputerTask('open Notion', automatic=True))
        self.assertEqual((step['action']['type'], step['action']['url']), ('browse_url', 'https://notion.so'))

    def test_clicking_an_apps_name_opens_it(self):
        from companion.computer_task import validate_step
        catalog = [{'name': 'Calendar', 'bundle_id': 'com.apple.iCal'}]
        self.desktop.bridge.catalog = Mock(return_value=catalog)
        self.desktop.bridge.value['installed_apps'] = catalog
        self.desktop.collect_once()
        step = validate_step({'status': 'action', 'message': 'Open Calendar.', 'risk': 'navigation',
                              'action': {'type': 'click_point', 'x': .5, 'y': .97, 'label': 'Calendar icon in Dock'}},
                             self.desktop, deepcopy(CALCULATOR), catalog=catalog, screen={'x': 0, 'y': 0, 'width': 1000, 'height': 700})
        self.assertEqual((step['action']['type'], step['action']['bundle_id']), ('launch_app', 'com.apple.iCal'))


class TaskToolTests(Harness):
    """v2 suite: a field was clicked but never typed into, menus were clicked as text, pop-ups
    blocked tasks. Tools let one decision do several steps; code runs the rest, checked each time."""
    snapshot = dict(CALCULATOR, controls=[], app={'name': 'Safari', 'bundle_id': 'com.apple.Safari', 'pid': 7},
                    focused_role='AXTextField', focused_bounds={'x': 300, 'y': 200, 'width': 400, 'height': 30},
                    targets=[{'role': 'AXTextField', 'label': 'Search Wikipedia', 'page': True,
                              'bounds': {'x': 300, 'y': 200, 'width': 400, 'height': 30}},
                             {'role': 'AXButton', 'label': "Don't Allow", 'bounds': {'x': 600, 'y': 400, 'width': 90, 'height': 30}}])

    def model(self, action):
        self.provider.complete_structured.return_value = {'status': 'action', 'message': 'Go.', 'risk': 'navigation', 'action': action}

    def test_search_in_app_clicks_the_field_then_types_and_presses_enter_without_the_model(self):
        task = ComputerTask('on Wikipedia, search for Bucharest', automatic=True, goal_mode='inspect')
        self.model({'type': 'search_in_app', 'query': 'Bucharest'})
        step = self.propose(task)
        self.assertEqual((step['action']['type'], step['action']['label']), ('click_point', 'Search Wikipedia'))
        task.record({'type': 'click_point', 'label': 'Search Wikipedia', 'status': 'submitted'})
        self.provider.complete_structured.reset_mock()
        typed = self.propose(task)
        self.assertEqual((typed['action']['type'], typed['action']['text']), ('type_text', 'Bucharest'))
        task.record({'type': 'type_text', 'label': 'Type', 'status': 'submitted'})
        enter = self.propose(task)
        self.assertEqual((enter['action']['type'], enter['action']['key']), ('press_key', 'enter'))
        self.assertFalse(enter['review'])  # Enter in a search field, read-only goal
        self.provider.complete_structured.assert_not_called()

    def test_page_targets_are_marked_and_declining_needs_no_confirmation(self):
        from companion.computer_task import screen_marks
        marks = screen_marks(self.snapshot['targets'], [], {'x': 0, 'y': 0, 'width': 1000, 'height': 700})
        self.assertEqual([m['kind'] for m in marks], ['page field', 'button'])
        self.model({'type': 'click_target', 'target_id': 't2'})
        step = self.propose(ComputerTask('check my notifications', automatic=True, goal_mode='inspect'))
        self.assertEqual(step['action']['label'], "Don't Allow")
        self.assertFalse(step['review'])

    def test_menus_are_chosen_by_title_and_risky_items_wait(self):
        self.model({'type': 'choose_menu', 'path': 'View > as List'})
        step = self.propose(ComputerTask('show the files as a list', automatic=True, goal_mode='inspect'))
        self.assertEqual((step['action']['type'], step['action']['path']), ('choose_menu', 'View > as List'))
        self.assertFalse(step['review'])
        self.model({'type': 'choose_menu', 'path': 'Finder > Empty Bin…'})
        self.assertTrue(self.propose(ComputerTask('empty the bin', automatic=True, goal_mode='change'))['review'])


class FileKindTests(unittest.TestCase):
    def test_the_named_kind_of_file_is_opened(self):
        import tempfile
        from pathlib import Path
        from companion import mac_apps
        with tempfile.TemporaryDirectory() as home:
            downloads = Path(home)/'Downloads'
            downloads.mkdir()
            (downloads/'report.pdf').write_text('x')
            time.sleep(0.01)
            (downloads/'A11y.dmg').write_text('x')
            opened = []
            reply = mac_apps.open_file('the last PDF I downloaded', home=Path(home), opener=lambda args, **k: opened.append(args))
            self.assertEqual(reply, 'Opened report.pdf.')
            (Path(home)/'Movies').mkdir()
            self.assertEqual(mac_apps.open_file('my Movies folder', home=Path(home), opener=lambda args, **k: opened.append(args)), 'Opened Movies.')


class SafetyReviewTests(Harness):
    """v2 suite and review: a left-over save sheet's Save was clicked and wrote a test document to the
    Desktop; Notion's "Continue as <name>" signed in; prefix matches made destructive labels harmless."""
    snapshot = dict(CALCULATOR, controls=[], app={'name': 'Safari', 'bundle_id': 'com.apple.Safari', 'pid': 7})
    regions = [region('Save', .5, .5), region('Continue as Ana', .5, .6), region('Close Account', .5, .7), region("Don't Allow", .5, .8)]

    def click(self, label, y, goal_mode='inspect', risk='navigation'):
        self.provider.complete_structured.return_value = {'status': 'action', 'message': 'Click.', 'risk': risk,
                                                         'action': {'type': 'click_point', 'x': .54, 'y': y, 'label': label}}
        return self.propose(ComputerTask('look around', automatic=True, goal_mode=goal_mode))

    def test_saving_and_signing_in_are_never_silent(self):
        from companion.computer_task import needs_review
        for label in ('Save', 'Continue as Ana', 'Continue with Google'):
            self.assertTrue(needs_review({'type': 'click_point', 'label': label}, 'navigation'), label)
        with self.assertRaises(Exception):
            self.click('Save', .515)  # a look-only goal never saves

    def test_only_whole_dismissive_labels_count_as_declining(self):
        from companion.computer_task import DECLINE
        for label in ("Don't Allow", 'Not now', 'Reject all', 'Close', 'Cancel'):
            self.assertTrue(DECLINE.match(label), label)
        for label in ('Close Account', 'Cancel Subscription', 'Never Allow', 'Closed'):
            self.assertFalse(DECLINE.match(label), label)

    def test_a_rejected_first_step_starts_no_tool_queue(self):
        task = ComputerTask('look around', automatic=True, goal_mode='inspect')
        self.provider.complete_structured.return_value = {'status': 'action', 'message': 'Type.', 'risk': 'navigation',
                                                         'action': {'type': 'type_into', 'target_id': 't99', 'text': 'secret'}}
        with self.assertRaises(Exception):
            self.propose(task)
        self.assertEqual(task.queue, [])


class ReviewFixTests(unittest.TestCase):
    def test_battery_status_is_read_exactly(self):
        from companion.mac_apps import system_info
        run = lambda text: (lambda command, **kwargs: Mock(stdout=text))
        self.assertIn('on battery', system_info('battery', run("Now drawing from 'Battery Power'\n -InternalBattery-0 (id=1)\t80%; discharging; 4:10 remaining")))
        self.assertIn('charging', system_info('battery', run("Now drawing from 'AC Power'\n -InternalBattery-0 (id=1)\t50%; charging; 1:00 remaining")))
        self.assertIn('fully charged', system_info('battery', run("Now drawing from 'AC Power'\n -InternalBattery-0 (id=1)\t100%; charged; 0:00 remaining")))

    def test_a_folder_name_is_not_a_file_kind(self):
        import tempfile
        from pathlib import Path
        from companion import mac_apps
        with tempfile.TemporaryDirectory() as home:
            documents = Path(home)/'Documents'
            documents.mkdir()
            (documents/'budget.xlsx').write_text('x')
            reply = mac_apps.open_file('newest file in Documents', home=Path(home), opener=lambda args, **k: None)
            self.assertIn('budget.xlsx', reply)

    def test_eval_confirm_only_takes_test_reminders(self):
        from companion.eval_control import EvalControl
        control = EvalControl.__new__(EvalControl)
        control.events, control.lock = [], __import__('threading').Lock()
        window = Mock()
        control.window = window
        control.event = lambda *a: None
        for pending in ({'type': 'app_write', 'kind': 'run_shortcut', 'label': 'Run shortcut “A11y test 1”', 'name': 'A11y test 1'},
                        {'type': 'app_write', 'kind': 'delete_reminder', 'title': 'Pay rent', 'label': 'Delete the reminder “Pay rent” A11y test'}):
            window.pending_desktop_action = pending
            self.assertIn('error', control.run({'cmd': 'confirm'}))
        window.pending_desktop_action = {'type': 'app_write', 'kind': 'delete_reminder', 'title': 'A11y test 4821 buy stamps', 'label': 'Delete'}
        self.assertEqual(control.run({'cmd': 'confirm'}), {'ok': True})
        # Test notes too (the owner allowed test notes and reminders), never another note.
        window.pending_desktop_action = {'type': 'app_write', 'kind': 'create_note', 'title': 'A11y test 4709', 'body': 'x'}
        self.assertEqual(control.run({'cmd': 'confirm'}), {'ok': True})
        window.pending_desktop_action = {'type': 'app_write', 'kind': 'create_note', 'title': 'Groceries', 'body': 'A11y test'}
        self.assertIn('error', control.run({'cmd': 'confirm'}))


class GuessedAddressTests(LiveRoutingTests):
    def test_an_invented_address_becomes_a_task(self):
        # "Where can I see the sites I visited? open it" opened https://www.safari.com/visited-sites (live).
        self.route('browser_open', 'https://www.safari.com/visited-sites')
        self.assertEqual(self.app.prepare_request('where can I see the sites I visited? open it')['operation'], 'computer_task')
        from companion.service import Companion
        self.assertFalse(Companion.guessed_address('go to github.com/iediot', 'https://github.com/iediot'))  # the user's own address
        self.assertFalse(Companion.guessed_address('open youtube', 'https://www.youtube.com'))  # a known site


class MenuGoalTests(Harness):
    snapshot = dict(CALCULATOR, controls=[], app={'name': 'Finder', 'bundle_id': 'com.apple.finder', 'pid': 7}, menus=['Finder', 'File', 'Edit', 'View', 'Go'])

    def test_a_named_menu_is_opened_in_code(self):
        task = ComputerTask('show me the View menu in Finder', automatic=True)
        step = self.propose(task)
        self.assertEqual((step['action']['type'], step['action']['path']), ('choose_menu', 'View'))
        self.provider.complete_structured.assert_not_called()
        task.record({'type': 'choose_menu', 'label': 'Choose View in Finder', 'status': 'submitted'})
        self.assertEqual(self.propose(task)['status'], 'done')


class NamedAppStartTests(Harness):
    """v2 suite: "in the Calculator app, what is 832 divided by 705?" ended with "Calculator not visible,
    open it from the Dock"; the named app is now opened in code before anything else."""

    def test_the_app_the_goal_names_is_opened_first(self):
        weather = [{'name': 'Weather', 'bundle_id': 'com.apple.weather'}, {'name': 'Safari', 'bundle_id': 'com.apple.Safari'}]
        self.desktop.bridge.catalog = Mock(return_value=weather)
        self.desktop.bridge.value['installed_apps'] = weather
        step = self.propose(ComputerTask("what's the weather in Oslo? use the Weather app", automatic=True))
        self.assertEqual((step['action']['type'], step['action']['bundle_id']), ('launch_app', 'com.apple.weather'))
        self.provider.complete_structured.assert_not_called()

    def test_a_click_that_changed_the_screen_is_progress_not_a_loop(self):
        # "next month" twice to reach March was stopped as "nothing changed" (live).
        task = ComputerTask('go to March in Calendar', automatic=True, goal_mode='inspect')
        for month in ('November', 'December'):
            task.screen_seen = month
            task.record({'type': 'click_point', 'label': 'next month', 'status': 'submitted'})
        task.screen_seen = 'January'
        self.assertEqual(task.repeats({'type': 'click_point', 'label': 'next month'}), 0)
        task.screen_seen = 'December'
        self.assertGreaterEqual(task.repeats({'type': 'click_point', 'label': 'next month'}), 1)


class AddressBarTests(Harness):
    """v2 suite: Safari's address bar was clicked again and again: a click left focus on the page, so
    typing was refused. The browser's own address field is focused with Command-L."""
    snapshot = dict(CALCULATOR, controls=[], app={'name': 'Safari', 'bundle_id': 'com.apple.Safari', 'pid': 7},
                    targets=[{'role': 'AXTextField', 'label': 'smart search field', 'bounds': {'x': 300, 'y': 20, 'width': 400, 'height': 30}}])

    def test_the_address_bar_is_focused_with_command_l_and_typing_follows(self):
        task = ComputerTask('go to the Wikipedia article on Albert Einstein', automatic=True, goal_mode='inspect')
        self.provider.complete_structured.return_value = {'status': 'action', 'message': 'Go.', 'risk': 'navigation',
                                                         'action': {'type': 'type_into', 'target_id': 't1', 'text': 'en.wikipedia.org/wiki/Albert_Einstein'}}
        step = self.propose(task)
        self.assertEqual((step['action']['type'], step['action']['key']), ('press_key', 'cmd_l'))
        self.assertEqual([item.get('type') for item in task.queue], ['type_text', 'press_key'])


class SettingsVersusInfoTests(LiveRoutingTests):
    def test_a_settings_pane_request_is_not_a_reading(self):
        # "Show me Battery in System Settings" answered "Your battery is at 100%".
        self.route('system_info', 'battery')
        self.assertEqual(self.app.prepare_request('show me Battery in System Settings')['operation'], 'computer_task')


@unittest.skipUnless(importlib.util.find_spec('PyQt6') is not None, 'Install requirements.txt to run Qt checks')
class MicrophonePermissionAnswerTests(unittest.TestCase):
    """Reported: on startup, "checkPermission(...): argument 1 has unexpected type
    'QPermission'". Qt answers the microphone request with a plain QPermission."""

    def answer(self, status, *, frozen):
        from types import SimpleNamespace
        from PyQt6.QtCore import Qt
        from companion.voice import VoiceListener
        calls = []
        listener = SimpleNamespace(mode='loading', open_microphone=lambda: calls.append('open'),
                                   microphone_denied=lambda: calls.append('denied'))
        permission = SimpleNamespace(status=lambda: getattr(Qt.PermissionStatus, status))
        with patch('companion.paths.frozen', return_value=frozen):
            VoiceListener.permission_answered(listener, permission)
        return calls

    def test_the_answer_is_read_from_the_permission_itself(self):
        self.assertEqual(self.answer('Granted', frozen=True), ['open'])
        self.assertEqual(self.answer('Denied', frozen=True), ['denied'])
        # From source Qt always says no; macOS asks for the terminal when the microphone opens.
        self.assertEqual(self.answer('Denied', frozen=False), ['open'])


class WeatherDayTests(unittest.TestCase):
    def test_reported_this_friday_is_understood(self):
        # Live (answers suite): "what's the weather in New York this Friday?" → "next week only".
        import datetime
        from companion.weather import day_offset
        saturday = datetime.date(2026, 10, 24)
        for when, days in (('this Friday', 6), ('on fri', 6), ('2026-10-30', 6), ('2026-10-30T09:00', 6),
                           ('tomorrow morning', 1), ('now', 0), ('', 0), ('saturday', 7)):
            self.assertEqual(day_offset(when, saturday), days, when)
        self.assertIsNone(day_offset('someday', saturday))


class StatedTimeTests(unittest.TestCase):
    def test_reported_at_6_is_six_not_the_time_it_is_now(self):
        # Live, at 22:46: "remind me to call mom at 6" came back as today 22:46.
        from datetime import datetime
        from companion.service import stated_time, upcoming
        late = datetime(2026, 10, 24, 22, 46)
        when = upcoming(stated_time('2026-10-24T22:46:00', 'remind me to call mom at 6', late), 'remind me to call mom at 6', late)
        self.assertEqual(when, '2026-10-25T06:00')
        morning = datetime(2026, 10, 24, 9, 10)
        self.assertEqual(stated_time('2026-10-24T18:00:00', 'remind me to call mom at 6', morning), '2026-10-24T18:00:00')  # right already
        self.assertEqual(stated_time('', 'remind me at 7:30pm to stretch', morning), '2026-10-24T19:30')
        self.assertEqual(stated_time('2026-10-26T10:00', 'meeting on monday at 3', morning), '2026-10-26T15:00')
        self.assertEqual(stated_time('2026-10-24T18:00', 'call the 6 people', morning), '2026-10-24T18:00')  # no "at"


class CalendarFallbackTests(unittest.TestCase):
    """Reported: from source, calendar questions said "Allow A11y to use Calendars" with no
    way to allow it (the terminal declares no calendar use, so macOS never asks)."""

    def test_source_runs_ask_the_calendar_app_when_eventkit_refuses(self):
        from types import SimpleNamespace
        from companion import desktop_context
        bridge = desktop_context.MacDesktopBridge('/private/tmp/a11y-bridge-unit')
        refused = RuntimeError('Allow A11y to use Calendars in System Settings > Privacy & Security > Calendars, then ask again.')
        listing = SimpleNamespace(returncode=0, stdout='Standup\t3600\t5400\tfalse\tWork\nLunch\t7200\t9000\tfalse\tHome\n')
        with patch.object(bridge, 'call', side_effect=refused), patch('companion.paths.frozen', return_value=False), \
                patch('companion.desktop_context.subprocess.run', return_value=listing) as run, patch('time.time', return_value=1000):
            events = bridge.calendar_events(1000, 1000+86400)
        self.assertEqual([(e['title'], e['start'], e['calendar']) for e in events], [('Standup', 4600, 'Work'), ('Lunch', 8200, 'Home')])
        argv = run.call_args.args[0]
        self.assertEqual(argv[-2:], ['0', '86400'])          # values as argv, never spliced into the script
        self.assertNotIn('Standup', ' '.join(argv))
        with patch.object(bridge, 'call', side_effect=refused), patch('companion.paths.frozen', return_value=True):
            with self.assertRaises(RuntimeError):            # the packaged app shows the real reason
                bridge.calendar_events(1000, 2000)


class BackLoopTests(unittest.TestCase):
    def test_reported_going_back_over_and_over_is_a_loop(self):
        # Live (v3 suite): "Go back" was clicked 13 times in a row; each page differed, so the
        # repeat guard (same screen) never fired.
        task = ComputerTask('switch to the Wikipedia tab', automatic=True)
        back = {'type': 'click_point', 'label': 'Go back', 'x': 0.62, 'y': 0.06}
        for screen in range(3):
            task.screen_seen = 'page %d' % screen
            task.record(dict(back, status='executed'))
        task.screen_seen = 'page 3'
        self.assertGreaterEqual(task.repeats(back), 2)                       # the fourth in a row stops
        self.assertEqual(ComputerTask('go back two pages', automatic=True).repeats(back), 0)
        self.assertTrue(task.is_history({'type': 'press_key', 'key': 'cmd_['}))
        self.assertFalse(task.is_history({'type': 'click_point', 'label': 'Back to school sale'}))


class QuestionsDoNotOpenTests(LiveRoutingTests):
    def test_reported_a_question_about_a_file_is_answered_not_opened(self):
        # Live (v3): "what's the size of the last thing I downloaded?" opened Untitled_Artwork.png.
        self.route('open_file', 'latest download')
        with patch('companion.mac_apps.recent_files', return_value=[]) as listing:
            response = self.app.ask("what's the size of the last thing I downloaded?")
        listing.assert_called_once_with('downloads')
        self.assertIsNone(response.get('desktop_action'))
        self.route('computer_task', 'how big is the newest file in my Downloads folder?')
        with patch('companion.mac_apps.recent_files', return_value=[]) as listing:
            response = self.app.ask('how big is the newest file in my Downloads folder?')
        listing.assert_called_once_with('downloads')
        self.assertIsNone(response.get('desktop_action'))

    def test_reported_a_named_site_is_where_the_task_starts(self):
        # Live (v3): Preview in front, the planner kept "current", and the task gave up.
        from companion.web_apps import site_address
        self.assertEqual(site_address('on Wikipedia, what does the History section of the Coffee article start with?'),
                         'https://en.wikipedia.org/w/index.php?search=Coffee')
        self.assertEqual(site_address('open the Wikipedia article on Penguin and tell me the first sentence'),
                         'https://en.wikipedia.org/w/index.php?search=Penguin')
        self.assertEqual(site_address('check my GitHub notifications'), 'https://github.com/notifications')
        self.assertIsNone(site_address('what is the weather like'))


class GoBackTests(unittest.TestCase):
    def test_reported_go_back_is_one_press(self):
        # Live (v3): "go back in Safari" pressed Back 13 times.
        from companion.computer_task import HISTORY_GOAL
        for goal, way, count in (('go back in Safari', 'back', None), ('go back', 'back', None), ('go back two pages', 'back', 'two'),
                                 ('go forward a page in chrome', 'forward', 'a'), ('please go back to the previous page', 'back', None)):
            match = HISTORY_GOAL.match(goal)
            self.assertIsNotNone(match, goal)
            self.assertEqual((match.group('way'), match.group('count')), (way, count), goal)
        self.assertIsNone(HISTORY_GOAL.match('go back to the Teams tab and check my chat'))


class RememberedRouteTests(unittest.TestCase):
    def test_a_finished_route_is_offered_for_a_similar_goal_only(self):
        import tempfile
        from companion import routes
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'A11Y_DATA_DIR': folder, 'A11Y_ROUTES': '1'}):
            receipts = [{'type': 'browse_url', 'label': 'Open https://teams.microsoft.com in Safari', 'status': 'executed'},
                        {'type': 'click_point', 'label': 'Activity', 'status': 'executed'},
                        {'type': 'type_text', 'label': 'secret words', 'status': 'executed'},
                        {'type': 'click_point', 'label': 'x'*80, 'status': 'executed'}]
            self.assertTrue(routes.remember('check my Teams notifications', receipts))
            hint = routes.recall('check my notifications in Teams please')
            self.assertEqual(hint['steps'], ['browse_url Open https://teams.microsoft.com in Safari', 'click_point Activity'])
            self.assertNotIn('secret', json.dumps(routes.load()))              # typed text is never kept
            self.assertIsNone(routes.recall('check my Slack messages'))       # not similar enough
            routes.remember('compute 812 times 7 on the calculator', [{'type': 'press_key', 'key': '8', 'status': 'executed'}])
            self.assertIsNotNone(routes.recall('compute 40 times 3 on the calculator'))  # numbers differ, same route
            routes.forget()
            self.assertIsNone(routes.recall('check my Teams notifications'))

    def test_a_task_gets_the_route_in_its_first_message(self):
        import tempfile
        from companion import routes
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'A11Y_DATA_DIR': folder, 'A11Y_ROUTES': '1'}):
            routes.remember('open the sound settings', [{'type': 'launch_app', 'label': 'Open System Settings', 'status': 'executed'},
                                                        {'type': 'click_point', 'label': 'Sound', 'status': 'executed'}])
            task = ComputerTask('open the sound settings for me', automatic=True)
        self.assertEqual(task.worked_before['steps'][-1], 'click_point Sound')


class V3RoundTwoTests(LiveRoutingTests):
    def test_reported_unread_count_comes_from_mail(self):
        # Live (v3): "how many unread emails do I have?" was a guessed number.
        with patch('companion.mail_reader.unread_count', return_value=3):
            plan = self.app.prepare_request('how many unread emails do I have?')
        self.assertEqual((plan['operation'], plan['reply']), ('answer', 'You have 3 unread emails.'))
        self.provider.interpret_request.assert_not_called()

    def test_reported_play_on_youtube_searches_youtube(self):
        # Live (v3): "play Bohemian Rhapsody Queen on YouTube" went to the Music library player.
        self.route('play_media', 'Bohemian Rhapsody', media_artist='Queen')
        plan = self.app.prepare_request('play Bohemian Rhapsody Queen on YouTube')
        self.assertEqual(plan['operation'], 'computer_task')
        self.assertIn('youtube.com/results?search_query=Bohemian+Rhapsody+Queen', plan['opening']['target'])

    def test_reported_take_me_back_one_page_is_one_press(self):
        from companion.computer_task import HISTORY_GOAL
        self.assertEqual(HISTORY_GOAL.match('take me back one page').group('count'), 'one')


class OwnAppTests(unittest.TestCase):
    def test_reported_a_quoted_marker_never_launches_a11y(self):
        # Live (v3): 'remind me "A11y test 4821: take an umbrella"' launched the installed A11y app.
        task = ComputerTask('if it is going to rain in Oslo tomorrow, remind me "A11y test 4821: take an umbrella" tomorrow at 8',
                            automatic=True)
        task._catalog = [{'name': 'A11y', 'bundle_id': 'com.a11y.companion'}, {'name': 'Reminders', 'bundle_id': 'com.apple.reminders'}]
        self.assertIsNone(task.named_app({'app': {'bundle_id': 'com.apple.Safari'}}))  # nothing to launch, least of all A11y
        task = ComputerTask('open A11y settings', automatic=True)
        task._catalog = [{'name': 'A11y', 'bundle_id': 'com.a11y.companion'}]
        self.assertIsNone(task.named_app({'app': {}}))


class V3RoundThreeTests(LiveRoutingTests):
    def test_reported_apple_support_search_uses_its_search_page(self):
        # Live (v3): a DuckDuckGo search for "Apple's support site for battery".
        self.route('browser_search', "Apple's support site for battery")
        plan = self.app.prepare_request("search Apple's support site for battery")
        self.assertEqual((plan['operation'], plan['target']), ('browser_open', 'https://support.apple.com/kb/index?page=search&q=battery'))
        self.route('browser_open', 'https://support.apple.com')  # the router opened the site instead (live, v3)
        plan = self.app.prepare_request("search Apple's support site for battery")
        self.assertEqual(plan['target'], 'https://support.apple.com/kb/index?page=search&q=battery')
        self.route('browser_open', 'https://support.apple.com')
        self.assertNotIn('search', str(self.app.prepare_request("open Apple's support site").get('target')))  # opening is not searching

    def test_reported_named_places_inside_sites(self):
        from companion.web_apps import find_site, same_site_hosts
        self.assertEqual(find_site('check my GitHub notifications')[1], 'https://github.com/notifications')
        self.assertIn('en.wikipedia.org', same_site_hosts('https://www.wikipedia.org'))  # an open article is reused

    def test_reported_finder_sort_is_the_menu(self):
        # Live (v3): 24 steps of clicking around to sort Documents by date added.
        from companion.computer_task import FINDER_SORT
        task = ComputerTask('open my Documents folder and sort it by date added', automatic=True)
        proposals = []
        def fake_validate(raw, *a, **k):
            proposals.append(raw['action'])
            return {'action': dict(raw['action'], label='Choose '+raw['action'].get('path', '').replace(' > ', ' › '))}
        with patch('companion.computer_task.validate_step', side_effect=fake_validate):
            for title in ('', 'Recents', 'Documents'):
                app = 'com.apple.finder' if title else 'com.apple.Safari'
                step = task.finder_sort_step(None, {'app': {'bundle_id': app}, 'window': {'title': title}})
                task.record(dict(step['action'], status='executed'))
            done = task.finder_sort_step(None, {'app': {'bundle_id': 'com.apple.finder'}, 'window': {'title': 'Documents'}})
        self.assertEqual([p.get('path', p['type']) for p in proposals], ['focus_app', 'Go > Documents', 'View > Sort By > Date Added | View > Group By > Date Added'])
        self.assertEqual(done['status'], 'done')
        self.assertTrue(FINDER_SORT.search('show Downloads in Finder, newest added first'))


class GoBackCountTests(unittest.TestCase):
    def test_reported_executed_presses_are_counted(self):
        # Live (v3): "go back in Safari" pressed ⌘[ 24 times; executed receipts carry only the label.
        task = ComputerTask('go back in Safari', automatic=True)
        task.record({'type': 'press_key', 'label': 'Press ⌘+[ in Safari', 'status': 'executed'})
        step = task.history_step(None, {'app': {'bundle_id': 'com.apple.Safari'}})
        self.assertEqual(step['status'], 'done')


class SiteNotAppTests(unittest.TestCase):
    def test_reported_hacker_news_is_not_the_news_app(self):
        # Live (v3): "what's the fifth story on Hacker News?" launched Apple News and failed.
        task = ComputerTask("what's the fifth story on Hacker News right now?", automatic=True)
        task._catalog = [{'name': 'News', 'bundle_id': 'com.apple.news'}, {'name': 'Safari', 'bundle_id': 'com.apple.Safari'}]
        self.assertIsNone(task.named_app({'app': {'bundle_id': 'com.apple.finder'}}))
        task = ComputerTask('open the News app and show me the top story', automatic=True)
        task._catalog = [{'name': 'News', 'bundle_id': 'com.apple.news'}]
        self.assertEqual(task.named_app({'app': {}})['name'], 'News')  # the app itself still opens


class V3RoundFourTests(LiveRoutingTests):
    def test_reported_a_wikipedia_article_is_not_an_app(self):
        # Live (v3): "the History section of the Chess article" on Wikipedia launched the Chess app.
        task = ComputerTask('on Wikipedia, what does the History section of the Chess article start with?', automatic=True)
        task._catalog = [{'name': 'Chess', 'bundle_id': 'com.apple.Chess'}]
        self.assertIsNone(task.named_app({'app': {'bundle_id': 'com.apple.finder'}}))
        task = ComputerTask('open the Chess app and look up Chess on Wikipedia', automatic=True)
        task._catalog = [{'name': 'Chess', 'bundle_id': 'com.apple.Chess'}]
        self.assertEqual(task.named_app({'app': {}})['name'], 'Chess')

    def test_reported_switching_to_a_tab_is_one_step(self):
        # Live (v3): the tab was in front, then the model pressed Back twice.
        task = ComputerTask('go back to the Wikipedia tab I had open', automatic=True)
        task._catalog = [{'name': 'Safari', 'bundle_id': 'com.apple.Safari'}]
        with patch('companion.computer_task.validate_step', side_effect=lambda raw, *a, **k: {'action': raw['action']}):
            first = task.tab_step(None, {'app': {'bundle_id': 'com.apple.Safari'}})
        self.assertEqual((first['action']['type'], first['action']['browser_mode']), ('browse_url', 'default'))
        task.record(dict(first['action'], status='executed'))
        self.assertEqual(task.tab_step(None, {'app': {'bundle_id': 'com.apple.Safari'}})['status'], 'done')

    def test_reported_two_named_sites_open_in_code_one_tab_each(self):
        # Live (v2): "open apple.com and duckduckgo.com in two separate tabs" clicked a Favourites icon ten times.
        task = ComputerTask('open apple.com and duckduckgo.com in two separate tabs', automatic=True)
        task._catalog = [{'name': 'Safari', 'bundle_id': 'com.apple.Safari'}]
        front = {'app': {'bundle_id': 'com.apple.Safari'}}
        with patch('companion.computer_task.validate_step', side_effect=lambda raw, *a, **k: {'action': raw['action']}):
            urls = [task.sites_step(None, front)['action']['url'] for _ in range(2)]
            self.assertEqual(urls, ['https://apple.com', 'https://duckduckgo.com'])
            self.assertEqual(task.sites_step(None, front)['status'], 'done')
            more = ComputerTask('open Wikipedia and Hacker News and tell me which one loads faster', automatic=True)
            more._catalog, more.sites_opened = task._catalog, ['https://www.wikipedia.org', 'https://news.ycombinator.com']
            self.assertIsNone(more.sites_step(None, front))                       # opened; the rest is the model's
            self.assertIsNone(ComputerTask('email test@example.com and open apple.com', automatic=True).sites_step(None, front))

    def test_reported_then_read_it_back_follows_the_note(self):
        # Live (v3): "make a note … then read it back to me" made the note and stopped.
        self.route('create_note', 'A11y test 9473 | the spare key is under the blue pot')
        with patch('companion.mac_apps.prepare', return_value=('confirm', {'type': 'app_write', 'kind': 'create_note', 'title': 'A11y test 9473',
                                                                             'body': 'the spare key is under the blue pot', 'label': 'Create note “A11y test 9473”'})):
            plan = self.app.prepare_request('make a note titled "A11y test 9473" that says the spare key is under the blue pot, then read it back to me')
        self.assertEqual(plan['remaining_request'], 'read it back to me')
        self.assertEqual(self.app.pending_app_write['remaining_request'], 'read it back to me')  # what runs it once confirmed
        with patch('companion.mac_apps.prepare', return_value=('confirm', {'type': 'app_write', 'kind': 'create_note', 'title': 'x',
                                                                             'body': 'and then we left', 'label': 'Create note “x”'})):
            plan = self.app.prepare_request('make a note titled "x" that says "and then we left"')
        self.assertNotIn('remaining_request', plan)  # quoted text is the note's

    def test_reported_nth_story_is_read_from_the_list(self):
        # Live (v3): "what's the fifth story on Hacker News?" clicked a story instead of reading the list.
        task = ComputerTask("what's the fifth story on Hacker News right now?", automatic=True)
        regions = [{'text': '%d.' % n, 'x': 0.02, 'y': 0.1+n*0.03} for n in range(1, 8)] + \
                  [{'text': 'Story number %d (example%d.com)' % (n, n), 'x': 0.05, 'y': 0.1+n*0.03+0.002} for n in range(1, 8)]
        self.assertEqual(task.nth_item_answer(regions)['message'], 'The fifth story is “Story number 5”.')
        inline = [{'text': '%d. Headline %d' % (n, n), 'x': 0.02, 'y': 0.1+n*0.03} for n in range(1, 8)]
        self.assertIn('Headline 3', ComputerTask('tell me the 3rd result', automatic=True).nth_item_answer(inline)['message'])
        self.assertIsNone(ComputerTask('open the fifth story', automatic=True).nth_item_answer(regions))  # opening is not reading


class AppClickTests(unittest.TestCase):
    """Reported: "it still doesn't get apps right". Clicks on an app's Dock icon or its menu-bar name
    (seen: "Safari|0.048,0.016", "Safari icon in Dock", over and over) are decided by what macOS says
    is at the point, for any app."""

    def run_click(self, hit, front='com.apple.finder'):
        from companion.computer_task import ValidationError as TaskValidationError
        task = ComputerTask('find the Bluetooth settings', automatic=True)
        bridge = Mock(hit=Mock(return_value=hit))
        desktop = Mock(bridge=bridge)
        step = {'status': 'action', 'message': 'Click.', 'action': {'type': 'click_point', 'x': 0.05, 'y': 0.95, 'label': 'Safari icon in Dock'}}
        snapshot = {'app': {'bundle_id': front}, 'available_apps': [{'bundle_id': 'com.apple.Safari'}]}
        visual = {'screen': {'x': 0, 'y': 0, 'width': 1000, 'height': 800}}
        with patch('companion.computer_task.validate_step', side_effect=lambda raw, *a, **k: {'action': dict(raw['action'], label='Switch'), 'message': raw['message']}):
            try:
                return task.app_click(step, desktop, snapshot, visual)
            except TaskValidationError as error:
                return str(error)

    def test_dock_icon_of_another_app_switches_to_it(self):
        result = self.run_click({'role': 'AXDockItem', 'app': 'com.apple.Safari', 'title': 'Safari'})
        self.assertEqual((result['action']['type'], result['action']['bundle_id']), ('focus_app', 'com.apple.Safari'))

    def test_the_front_apps_own_name_is_refused_with_the_reason(self):
        result = self.run_click({'role': 'AXMenuBarItem', 'app': 'com.apple.Safari', 'app_name_item': True, 'title': 'Safari'},
                                front='com.apple.Safari')
        self.assertEqual(result['action']['type'], 'wait')       # never a refusal that ends the task
        self.assertIn('already in front', result['action']['label'])

    def test_reported_the_full_screen_button_is_skipped_unless_asked(self):
        # Owner: "it loves to fullscreen shit and that breaks the whole thing".
        result = self.run_click({'role': 'AXButton', 'subrole': 'AXFullScreenButton', 'title': ''})
        self.assertEqual(result['action']['type'], 'wait')
        from companion.computer_task import FULL_SCREEN_MENU, WINDOW_SIZE_WORDS
        self.assertTrue(FULL_SCREEN_MENU.search('View > Enter Full Screen'))
        self.assertTrue(WINDOW_SIZE_WORDS.search('make Safari full screen'))
        self.assertFalse(WINDOW_SIZE_WORDS.search('find the Bluetooth settings'))

    def test_reported_a_second_click_on_the_front_apps_name_is_refused(self):
        # Live: Finder's menu-bar name was clicked 24 times, each one turned into a wait.
        hit = {'role': 'AXMenuBarItem', 'app': 'com.apple.finder', 'app_name_item': True, 'title': 'Finder'}
        from companion.computer_task import ValidationError as TaskValidationError
        task = ComputerTask('sort Documents by date added', automatic=True)
        task.receipts.append({'type': 'wait', 'label': 'Finder is already in front; name in the menu bar skipped', 'status': 'executed'})
        desktop = Mock(bridge=Mock(hit=Mock(return_value=hit)))
        step = {'status': 'action', 'message': 'Click.', 'action': {'type': 'click_point', 'x': 0.05, 'y': 0.01, 'label': 'Finder'}}
        with self.assertRaises(TaskValidationError):
            task.app_click(step, desktop, {'app': {'bundle_id': 'com.apple.finder'}}, {'screen': {'x': 0, 'y': 0, 'width': 1000, 'height': 800}})

    def test_an_ordinary_target_is_left_alone(self):
        result = self.run_click({'role': 'AXButton', 'title': 'Bluetooth', 'owner': 'com.apple.systempreferences'})
        self.assertEqual(result['action']['type'], 'click_point')


class ClickEffectTests(unittest.TestCase):
    def test_a_click_that_changed_nothing_is_reported_and_its_target_dropped(self):
        task = ComputerTask('turn on dark mode', automatic=True)
        task.screen_seen = 'screen A'
        task.record({'type': 'click_point', 'label': 'Appearance', 'status': 'executed'})
        task.screen_seen = 'screen A'          # the next observation reads the same
        task.check_last_click()
        self.assertEqual(task.receipts[-1]['effect'], 'nothing changed on screen')
        marks = [{'id': 't1', 'label': 'Appearance'}, {'id': 't2', 'label': 'Dark'}]
        self.assertEqual([m['label'] for m in task.live_marks(marks)], ['Dark'])
        task.screen_seen = 'screen B'          # a different screen: it is offered again
        self.assertEqual(len(task.live_marks(marks)), 2)
        task.record({'type': 'click_point', 'label': 'Dark', 'status': 'executed'})
        task.screen_seen = 'screen C'
        task.check_last_click()
        self.assertNotIn('effect', task.receipts[-1])  # said only when nothing changed


    def test_reported_a_click_that_focused_a_field_says_to_type(self):
        # Live: the Notes search field was clicked twice ("nothing changed") and the task stopped.
        task = ComputerTask('find my note about keys', automatic=True)
        task.screen_seen = 'screen A'
        task.record({'type': 'click_point', 'label': 'Search', 'status': 'executed'})
        task.check_last_click({'focused_role': 'AXSearchField'})
        self.assertIn('type into it', task.receipts[-1]['effect'])
        self.assertEqual(len(task.live_marks([{'id': 't1', 'label': 'Search'}])), 1)  # not a dead target


class RouteHygieneTests(unittest.TestCase):
    def test_reported_a_looping_route_is_never_remembered(self):
        # Live: "done" after clicking "All iCloud" three times was saved as a route to follow.
        import tempfile
        from companion import routes
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'A11Y_DATA_DIR': folder, 'A11Y_ROUTES': '1'}):
            loop = [{'type': 'click_point', 'label': 'All iCloud', 'status': 'executed'}]*3
            self.assertFalse(routes.remember('which folders do I have in Notes', loop))
            dead = [{'type': 'click_point', 'label': 'Videos', 'status': 'executed', 'effect': 'nothing changed on screen'}]
            self.assertFalse(routes.remember('show my videos in Photos', dead))
            self.assertEqual(routes.load(), [])
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'A11Y_DATA_DIR': folder, 'A11Y_ROUTES': '0'}):
            self.assertFalse(routes.remember('open the sound settings', [{'type': 'click_point', 'label': 'Sound', 'status': 'executed'}]))


class LookUpOnSiteTests(LiveRoutingTests):
    def test_reported_weather_in_the_weather_app_opens_the_app(self):
        # Live (held-out): "Show me the Visibility details in Weather …" was answered with a forecast.
        self.app.desktop_context.bridge.catalog.return_value = [{'name': 'Weather', 'bundle_id': 'com.apple.weather'}]
        self.route('weather', 'Visibility')
        self.assertEqual(self.app.prepare_request('Show me the Visibility details in Weather for the selected place')['operation'], 'computer_task')
        self.route('weather', 'Paris')
        self.assertEqual(self.app.prepare_request('what is the weather in Paris?')['operation'], 'weather')

    def test_reported_look_up_on_wikipedia_opens_it(self):
        # Live A/B: "look up Photosynthesis on Wikipedia" was answered in the background, nothing opened.
        self.route('look_up', 'Photosynthesis')
        plan = self.app.prepare_request('look up Photosynthesis on Wikipedia')
        self.assertEqual((plan['operation'], plan['target']), ('browser_open', 'https://en.wikipedia.org/w/index.php?search=Photosynthesis'))
        self.route('look_up', 'Ada Lovelace birth')
        self.assertEqual(self.app.prepare_request('when was Ada Lovelace born? check Wikipedia')['operation'], 'look_up')  # a question


class WindowlessAppTests(unittest.TestCase):
    def test_reported_a_running_app_without_a_window_gets_one(self):
        # Live: TextEdit was running with no window; switching to it left nothing to type into.
        task = ComputerTask("write 'Call the dentist' in a new TextEdit document", automatic=True)
        task._catalog = [{'name': 'TextEdit', 'bundle_id': 'com.apple.TextEdit'}]
        proposed = []
        with patch('companion.computer_task.validate_step', side_effect=lambda raw, *a, **k: proposed.append(raw['action']) or {'action': raw['action']}):
            task.plan_start(None, {'app': {'bundle_id': 'com.apple.finder', 'name': 'Finder'},
                                   'available_apps': [{'bundle_id': 'com.apple.TextEdit'}], 'app_windows': []}, Mock())
        self.assertEqual(proposed[0]['type'], 'new_window')

    def test_reported_a_new_document_is_new_even_with_one_open(self):
        # Live (v2): TextEdit had "Dentist Appointment" open; the shopping list was written into it.
        open_window = {'app': {'bundle_id': 'com.apple.finder', 'name': 'Finder'}, 'available_apps': [{'bundle_id': 'com.apple.TextEdit'}],
                       'app_windows': [{'bundle_id': 'com.apple.TextEdit', 'title': 'Dentist Appointment'}]}
        for goal, kind in (('in a new TextEdit document write a shopping list', 'new_window'),
                           ('write the date in TextEdit', 'focus_app')):
            for front in ('com.apple.finder', 'com.apple.TextEdit'):  # TextEdit in front too (live, v3)
                if front == 'com.apple.TextEdit' and kind == 'focus_app':
                    continue
                open_window['app'] = {'bundle_id': front, 'name': front.rsplit('.', 1)[1]}
                task = ComputerTask(goal, automatic=True)
                task._catalog = [{'name': 'TextEdit', 'bundle_id': 'com.apple.TextEdit'}]
                proposed = []
                with patch('companion.computer_task.validate_step', side_effect=lambda raw, *a, **k: proposed.append(raw['action']) or {'action': raw['action']}):
                    task.plan_start(None, open_window, Mock())
                self.assertEqual(proposed[0]['type'], kind, (goal, front))

    def test_reported_the_named_app_in_front_without_a_window_gets_one(self):
        # Live (held-out): Finder in front showing only the desktop; the task stopped asking to open a window.
        task = ComputerTask('show me what is in my Movies folder in Finder', automatic=True)
        task._catalog = [{'name': 'Finder', 'bundle_id': 'com.apple.finder'}]
        front = {'app': {'bundle_id': 'com.apple.finder', 'name': 'Finder'}, 'available_apps': [{'bundle_id': 'com.apple.finder'}]}
        with patch('companion.computer_task.validate_step', side_effect=lambda raw, *a, **k: {'action': raw['action']}):
            desktop_only = [{'bundle_id': 'com.apple.finder', 'title': ''}]  # Finder lists the desktop as an untitled window
            step = task.plan_start(None, dict(front, app_windows=desktop_only, window={'title': ''}), Mock())
            self.assertEqual(step['action'], {'type': 'new_window', 'bundle_id': 'com.apple.finder'})
            again = ComputerTask('show me what is in my Movies folder in Finder', automatic=True)
            again._catalog = task._catalog
            self.assertIsNone(again.plan_start(None, dict(front, app_windows=[{'bundle_id': 'com.apple.finder', 'title': 'Downloads'}]), Mock()))

    def test_reported_a_look_only_goal_never_creates_a_document(self):
        # Live (held-out): "Which documents are currently open in TextEdit? … do not make a new document" made one.
        task = ComputerTask('Which documents are currently open in TextEdit? Read their names; do not make a new document.', automatic=True, goal_mode='inspect')
        task._catalog = [{'name': 'TextEdit', 'bundle_id': 'com.apple.TextEdit'}]
        windowless = {'app': {'bundle_id': 'com.apple.finder', 'name': 'Finder'}, 'available_apps': [{'bundle_id': 'com.apple.TextEdit'}], 'app_windows': []}
        with patch('companion.computer_task.validate_step', side_effect=lambda raw, *a, **k: {'action': raw['action']}):
            self.assertEqual(task.plan_start(None, windowless, Mock())['action']['type'], 'focus_app')

    def test_reported_a_launch_for_a_new_document_is_followed_by_one(self):
        # Live (v3): TextEdit reopened "Dentist Appointment" on launch and the number went into it.
        task = ComputerTask('put "54.2 million" in a new TextEdit document', automatic=True)
        task._catalog = [{'name': 'TextEdit', 'bundle_id': 'com.apple.TextEdit'}]
        with patch('companion.computer_task.validate_step', side_effect=lambda raw, *a, **k: {'action': raw['action']}):
            first = task.plan_start(None, {'app': {'bundle_id': 'com.apple.finder', 'name': 'Finder'}, 'available_apps': [], 'app_windows': []}, Mock())
            self.assertEqual(first['action']['type'], 'launch_app')
            self.assertIsNone(task.new_document_step(None, {'app': {'bundle_id': 'com.apple.finder'}}))  # not in front yet
            then = task.new_document_step(None, {'app': {'bundle_id': 'com.apple.TextEdit'}})
            self.assertEqual(then['action'], {'type': 'new_window', 'bundle_id': 'com.apple.TextEdit'})
            self.assertIsNone(task.new_document_step(None, {'app': {'bundle_id': 'com.apple.TextEdit'}}))  # once

    def test_reported_typed_text_is_not_typed_again(self):
        # Live (v2): "onions ⏎ bread ⏎ apples" typed, then typed again five times.
        from companion.computer_task import already_typed
        task = ComputerTask('in a new TextEdit document write a shopping list', automatic=True)
        task.pending = {'type': 'type_text', 'text': 'onions\nbread\napples'}
        task.record({'type': 'type_text', 'label': 'Type', 'status': 'submitted'})
        self.assertTrue(already_typed('Onions\napples', task.typed))
        self.assertTrue(already_typed('bread', task.typed))
        self.assertFalse(already_typed('onions\nmilk', task.typed))
        self.assertFalse(already_typed('', task.typed))

    def test_reported_the_menu_command_is_picked_from_the_apps_own_menus(self):
        # Live (v2): "where can I see the sites I visited? open it" clicked around Safari for 22 steps.
        task = ComputerTask('where can I see the sites I visited? open it', automatic=True)
        task.brought = {'com.apple.Safari'}  # the task switched to Safari first
        task.receipts = [{'type': 'click_point', 'label': 'Tab Group picker', 'effect': 'nothing changed on screen'}]  # and got stuck
        items = ['Safari > Services > Add to Music as a Spoken Track', 'Edit > Find > Find…', 'Edit > Search', 'File > Save…', 'File > Export as PDF…', 'Safari > Clear History…', 'File > Close Window', 'History > Show All History', 'History > Recently Closed > Example Domain',
                 'Bookmarks > Show Bookmarks', 'View > Enter Full Screen', 'History > Home', 'History > Weather - BBC News']
        desktop = Mock(bridge=Mock(menu_items=Mock(return_value=items)))
        provider = Mock()
        provider.complete_structured.return_value = {'command': 'History > Show All History'}
        with patch('companion.computer_task.validate_step', side_effect=lambda raw, *a, **k: {'action': raw['action']}):
            step = task.menu_pick_step(desktop, {'app': {'bundle_id': 'com.apple.Safari', 'name': 'Safari'}}, provider, {})
        self.assertEqual(step['action'], {'type': 'choose_menu', 'path': 'History > Show All History'})
        offered = provider.complete_structured.call_args.kwargs['schema']['properties']['command']['enum']
        self.assertEqual(offered, ['none', 'History > Show All History', 'History > Home'])  # nothing that clears, closes or lists pages
        self.assertIsNone(task.menu_pick_step(desktop, {'app': {'bundle_id': 'com.apple.Safari'}}, provider, {}))  # once per task
        provider.complete_structured.return_value = {'command': 'none'}
        finder = ComputerTask('open my notes in Notes', automatic=True)
        finder._catalog = [{'name': 'Notes', 'bundle_id': 'com.apple.Notes'}]
        finder.receipts = list(task.receipts)
        self.assertIsNone(finder.menu_pick_step(desktop, {'app': {'bundle_id': 'com.apple.finder'}}, provider, {}))  # another app's goal
        other = ComputerTask('find the cheapest flight', automatic=True)
        other.brought = {'com.apple.Safari'}
        other.receipts = list(task.receipts)
        fresh = ComputerTask('where can I see the sites I visited? open it', automatic=True)
        fresh.brought = {'com.apple.Safari'}
        self.assertIsNone(fresh.menu_pick_step(desktop, {'app': {'bundle_id': 'com.apple.Safari'}}, provider, {}))  # not stuck yet
        self.assertIsNone(other.menu_pick_step(desktop, {'app': {'bundle_id': 'com.apple.Safari'}}, provider, {}))
        self.assertIsNone(ComputerTask('open apple.com and find the iPhone page', automatic=True)
                          .menu_pick_step(desktop, {'app': {'bundle_id': 'com.apple.Safari'}}, provider, {}))  # a site's own pages

    def test_reported_an_address_in_the_plan_is_opened_in_code(self):
        # Live (held-out): the planner said "current" (Safari in front) with "navigate to https://open-meteo.com" in its steps.
        class Planner:
            def plan_task(self, observation):
                return {'start': 'current', 'app': 'Safari', 'url': '', 'query': 'Open-Meteo website',
                        'steps': ['In Safari, navigate to https://open-meteo.com.', 'Search for Dublin'], 'done_when': ''}
        task = ComputerTask('Using Open-Meteo, tell me how long daylight lasts today in Dublin.', automatic=True)
        task._catalog = [{'name': 'Safari', 'bundle_id': 'com.apple.Safari'}]
        with patch('companion.computer_task.validate_step', side_effect=lambda raw, *a, **k: {'action': raw['action']}), \
                patch('companion.desktop_context.default_browser_id', return_value='com.apple.Safari'):
            step = task.plan_start(None, {'app': {'bundle_id': 'com.apple.Safari', 'name': 'Safari'}, 'window': {'title': 'Example Domain'}}, Planner())
        self.assertEqual(step['action']['url'], 'https://open-meteo.com')

    def test_reported_an_app_that_is_not_installed_opens_its_own_site(self):
        # Live (v2): "I want to use Notion" (not installed) ended on a DuckDuckGo results page.
        task = ComputerTask('I want to use Notion', automatic=True)
        task._catalog = [{'name': 'Safari', 'bundle_id': 'com.apple.Safari'}]
        class Planner:
            def plan_task(self, observation):
                return {'start': 'app', 'app': 'Notion', 'url': '', 'query': '', 'steps': [], 'done_when': ''}
        provider = Planner()
        with patch('companion.computer_task.validate_step', side_effect=lambda raw, *a, **k: {'action': raw['action']}), \
                patch('companion.desktop_context.default_browser_id', return_value='com.apple.Safari'):
            step = task.plan_start(None, {'app': {'bundle_id': 'com.apple.finder', 'name': 'Finder'}, 'available_apps': [], 'app_windows': []}, provider)
        self.assertEqual(step['action']['url'], 'https://duckduckgo.com/?q=%5CNotion')  # "\Notion": the first result, its site

    def test_reported_a_named_section_is_its_anchor_on_the_page(self):
        # Live (v3): "…the first sentence of its History section" scrolled nine times past it.
        task = ComputerTask('open the Wikipedia article on Coffee and tell me the first sentence of its History section', automatic=True)
        front = {'app': {'bundle_id': 'com.apple.Safari', 'name': 'Safari'}}
        with patch('companion.computer_task.validate_step', side_effect=lambda raw, *a, **k: {'action': raw['action']}):
            with patch('companion.mac_apps.read_page', return_value=('Search', 'https://en.wikipedia.org/w/index.php?search=Coffee', '')):
                self.assertIsNone(task.section_step(None, front))  # the search hasn't landed on the article yet
            with patch('companion.mac_apps.read_page', return_value=('Coffee', 'https://en.wikipedia.org/wiki/Coffee#Etymology', '')):
                step = task.section_step(None, front)
                self.assertEqual((step['action']['url'], step['action']['browser_mode']), ('https://en.wikipedia.org/wiki/Coffee#History', 'current'))
                task.record({'type': 'browse_url', 'label': 'Go to the History section', 'status': 'submitted'})
                task.goal_mode = 'inspect'
                self.assertEqual(task.section_step(None, front)['status'], 'done')  # then read it (it clicked another article, live)
                self.assertIsNone(task.section_step(None, front))  # once
                self.assertIsNone(ComputerTask('show me the comments on the top post', automatic=True).section_step(None, front))
                self.assertIsNone(ComputerTask('take me to the history part', automatic=True).section_step(None, {'app': {'bundle_id': 'com.apple.finder'}}))

    def test_reported_a_long_answer_is_not_mistaken_for_progress(self):
        # Live: a 40-word answer cut with "…" was treated as a progress line, left out of History and the reply.
        from companion.desktop import brief, progress_message
        long = 'There are multiple anecdotal origin stories about coffee '+'and its long history '*12+'in Ethiopia.'
        self.assertFalse(progress_message(brief(long)))
        self.assertTrue(progress_message('Open https://en.wikipedia.org in Safari…'))

    def test_reported_a_click_that_reached_the_goal_ends_the_task(self):
        # Live (held-out): "Show Sidebar" opened Calculator's history; the model clicked on and closed it.
        task = ComputerTask('Open the history panel in Calculator so I can review previous calculations.', automatic=True, goal_mode='inspect')
        task.screen_seen = 'closed'
        task.record({'type': 'click_point', 'label': 'Show Sidebar', 'status': 'submitted'})
        task.screen_seen = 'open'
        regions = [{'text': 'Yesterday', 'x': .3, 'y': .2}, {'text': '12+30', 'x': .3, 'y': .3}, {'text': '42', 'x': .3, 'y': .32}]
        provider = Mock()
        provider.complete_structured.side_effect = [{'met': True, 'evidence': '12+30'}, {'message': 'Your history is open: 12+30 = 42.'}]
        step = task.goal_met(provider, {'app': {'name': 'Calculator'}, 'window': {'title': 'Calculator'}}, regions, {})
        self.assertEqual((step['status'], step['message']), ('done', 'Your history is open: 12+30 = 42.'))
        provider.complete_structured.side_effect = [{'evidence': '12+30', 'missing': 'a ten-minute timer running', 'met': True}]
        task.screen_seen = 'open'
        self.assertIsNone(task.goal_met(provider, {'app': {'name': 'Calculator'}, 'window': {}}, regions, {}))  # something still left (live, Clock)
        provider.complete_structured.side_effect = [{'met': True, 'evidence': 'Show Sidebar'}]  # only the button it clicked
        task.screen_seen, regions = 'open', regions+[{'text': 'Show Sidebar', 'x': .2, 'y': .1}]
        self.assertIsNone(task.goal_met(provider, {'app': {'name': 'Calculator'}, 'window': {}}, regions, {}))
        provider.complete_structured.side_effect = [{'met': True, 'evidence': 'History panel'}]  # not on the screen
        self.assertIsNone(task.goal_met(provider, {'app': {}, 'window': {}}, regions, {}))
        task.receipts[-1]['effect'] = 'nothing changed on screen'
        self.assertIsNone(task.goal_met(provider, {'app': {}, 'window': {}}, regions, {}))  # only after a click that changed it
        del task.receipts[-1]['effect']
        self.assertIsNone(task.goal_met(provider, {'app': {'bundle_id': 'com.apple.Safari'}, 'window': {}}, regions, {}))  # not on web pages

    def test_reported_a_click_cycle_through_other_screens_is_refused(self):
        # Owner: in Calculator "a11y just presses the tab opener over and over" (Show Sidebar, Hide Sidebar, …).
        task = ComputerTask('open the history panel in Calculator', automatic=True)
        for screen, label in (('closed', 'Show Sidebar'), ('open', 'Hide Sidebar')):
            task.screen_seen = screen
            task.record({'type': 'click_point', 'label': label, 'status': 'submitted'})
        task.screen_seen = 'closed'                          # back where it started
        self.assertTrue(task.cycled({'type': 'click_point', 'label': 'Show  sidebar'}))
        self.assertFalse(task.cycled({'type': 'click_point', 'label': 'History'}))
        task.screen_seen = 'something new'
        self.assertFalse(task.cycled({'type': 'click_point', 'label': 'Show Sidebar'}))
    def test_review_a_typed_address_becomes_one_navigation(self):
        # Review 2026-10-25: Open-Meteo and Open Library failed every run fumbling ⌘L, typing and Enter.
        task = ComputerTask('Using Open-Meteo, tell me how long daylight lasts in Dublin', automatic=True)
        safari = {'app': {'bundle_id': 'com.apple.Safari'}}
        typed = {'status': 'action', 'message': 'Type.', 'action': {'type': 'type_text', 'text': 'open-meteo.com'}}
        with patch('companion.computer_task.validate_step', side_effect=lambda raw, *a, **k: {'action': raw['action'], 'message': raw['message']}):
            step = task.address_typed(typed, None, safari)
            self.assertEqual((step['action']['type'], step['action']['url'], step['action']['browser_mode']),
                             ('browse_url', 'https://open-meteo.com', 'current'))
            query = {'status': 'action', 'message': 'Type.', 'action': {'type': 'type_text', 'text': 'daylight Dublin'}}
            self.assertEqual(task.address_typed(query, None, safari), query)                      # a search stays typing
            self.assertEqual(task.address_typed(typed, None, {'app': {'bundle_id': 'com.apple.TextEdit'}}), typed)  # not a browser

    def test_review_an_address_in_the_plan_is_opened_in_code(self):
        # The planner said "current" (Safari in front) with "navigate to https://open-meteo.com" in its steps.
        class Planner:
            def plan_task(self, observation):
                return {'start': 'current', 'app': 'Safari', 'url': '', 'query': 'Open-Meteo website',
                        'steps': ['In Safari, navigate to https://open-meteo.com.', 'Search for Dublin'], 'done_when': ''}
        task = ComputerTask('Using Open-Meteo, tell me how long daylight lasts today in Dublin.', automatic=True)
        task._catalog = [{'name': 'Safari', 'bundle_id': 'com.apple.Safari'}]
        with patch('companion.computer_task.validate_step', side_effect=lambda raw, *a, **k: {'action': raw['action']}), \
                patch('companion.desktop_context.default_browser_id', return_value='com.apple.Safari'):
            step = task.plan_start(None, {'app': {'bundle_id': 'com.apple.Safari', 'name': 'Safari'}, 'window': {'title': 'Example'}}, Planner())
        self.assertEqual(step['action']['url'], 'https://open-meteo.com')

    def test_review_a_navigation_that_did_not_happen_is_tried_once_more(self):
        task = ComputerTask('open Calculator', automatic=True)
        task.pending = {'type': 'launch_app', 'bundle_id': 'com.apple.calculator'}
        task.record({'type': 'launch_app', 'label': 'Open Calculator', 'status': 'submitted'})
        with patch('companion.computer_task.validate_step', side_effect=lambda raw, *a, **k: {'action': raw['action']}):
            again = task.navigation_check(None, {'app': {'bundle_id': 'com.apple.finder'}})
            self.assertEqual(again['action'], {'type': 'launch_app', 'bundle_id': 'com.apple.calculator'})
            self.assertIn('did not happen', task.receipts[-1]['effect'])
            self.assertIsNone(task.navigation_check(None, {'app': {'bundle_id': 'com.apple.finder'}}))   # only once
            fine = ComputerTask('open Calculator', automatic=True)
            fine.pending = {'type': 'launch_app', 'bundle_id': 'com.apple.calculator'}
            fine.record({'type': 'launch_app', 'label': 'Open Calculator', 'status': 'submitted'})
            self.assertIsNone(fine.navigation_check(None, {'app': {'bundle_id': 'com.apple.calculator'}}))
            web = ComputerTask('open apple.com', automatic=True)
            web.pending = {'type': 'browse_url', 'bundle_id': 'com.apple.Safari', 'url': 'https://www.apple.com'}
            web.record({'type': 'browse_url', 'label': 'Open', 'status': 'submitted'})
            with patch('companion.mac_apps.read_page', return_value=('Apple', 'https://www.apple.com/ro/', '')):
                self.assertIsNone(web.navigation_check(None, {'app': {'bundle_id': 'com.apple.Safari'}}))  # same site
            with patch('companion.mac_apps.read_page', return_value=('Example', 'https://example.com/', '')):
                self.assertEqual(web.navigation_check(None, {'app': {'bundle_id': 'com.apple.Safari'}})['action']['url'], 'https://www.apple.com')

    def test_owner_a_drag_between_two_lines_selects_them_and_other_drags_ask(self):
        # Owner: "it should have a double click and a click and drag ability cause i see it try and text select".
        from companion.computer_task import resolve_target, needs_review, schema_for
        marks = [{'id': 't1', 'kind': 'text', 'label': 'onions', 'x': .1, 'y': .2, 'width': .1, 'height': .02},
                 {'id': 't2', 'kind': 'text', 'label': 'apples', 'x': .1, 'y': .26, 'width': .1, 'height': .02},
                 {'id': 't3', 'kind': 'button', 'label': 'Trash', 'x': .5, 'y': .9, 'width': .05, 'height': .05}]
        drag = resolve_target({'action': {'type': 'drag_target', 'from_target': 't1', 'to_target': 't2'}}, marks)['action']
        self.assertEqual((drag['type'], drag['label']), ('drag', 'Select from “onions” to “apples”'))
        self.assertLess(drag['x'], .102)            # from the first line's start
        self.assertGreater(drag['to_x'], .198)      # to the second line's end
        self.assertFalse(needs_review(dict(drag, selects_text=True), 'navigation'))
        self.assertTrue(needs_review(dict(drag, selects_text=False), 'navigation'))   # could move a file to the Trash
        self.assertTrue(needs_review({'type': 'press_key', 'key': 'cmd_s'}, 'navigation'))  # saving: the user's OK
        kinds = [v['properties']['type']['enum'][0] for v in schema_for('Darwin', target_ids=['t1', 't2'])['anyOf'][0]['properties']['action']['anyOf']]
        self.assertIn('drag_target', kinds)

    def test_live_an_animated_target_is_still_the_same_named_element(self):
        # Live (held-out): Weather's animated sky changed the pixels; every click on "Conditions" was refused.
        from companion.vision import ScreenVision
        screen = {'x': 0, 'y': 0, 'width': 1000, 'height': 800}
        proposal = {'type': 'click_point', 'x': .5, 'y': .5, 'label': 'Conditions'}
        for title, same in (('Conditions', True), ('conditions ', True), ('Sunrise', False), ('', False)):
            desktop = Mock(bridge=Mock(hit=Mock(return_value={'role': 'AXButton', 'title': title})))
            self.assertEqual(ScreenVision.same_target(desktop, proposal, screen), same, title)
        self.assertFalse(ScreenVision.same_target(Mock(bridge=object()), proposal, screen))  # no way to tell: refused


class DefaultBrowserTests(unittest.TestCase):
    @unittest.skipUnless(__import__('sys').platform == 'darwin', 'macOS LaunchServices')
    def test_reported_no_chosen_browser_means_safari(self):
        # Live: with no browser ever chosen, one task opened in Google Chrome and the next in Safari.
        import plistlib, tempfile
        from pathlib import Path
        from companion import desktop_context
        with tempfile.TemporaryDirectory() as home:
            folder = Path(home)/'Library/Preferences/com.apple.LaunchServices'
            with patch('pathlib.Path.home', return_value=Path(home)):
                self.assertEqual(desktop_context.default_browser_id(), 'com.apple.Safari')  # no file at all
                folder.mkdir(parents=True)
                (folder/'com.apple.launchservices.secure.plist').write_bytes(plistlib.dumps({'LSHandlers': [
                    {'LSHandlerContentType': 'public.html', 'LSHandlerRoleAll': 'com.google.chrome'}]}))
                self.assertEqual(desktop_context.default_browser_id(), 'com.apple.Safari')  # no https choice
                (folder/'com.apple.launchservices.secure.plist').write_bytes(plistlib.dumps({'LSHandlers': [
                    {'LSHandlerURLScheme': 'https', 'LSHandlerRoleAll': 'com.google.chrome'}]}))
                self.assertEqual(desktop_context.default_browser_id(), 'com.google.Chrome')  # a real choice wins


class FollowUpTests(unittest.TestCase):
    """A follow-up to a task ("the same with X") is that task again, changed."""
    setUp, route = LiveRoutingTests.setUp, LiveRoutingTests.route

    def test_reported_the_same_but_different_is_done_not_just_said(self):
        # Reported: after a task, "now do the same with github.com" got "I'm doing it" and nothing
        # started; asked why, A11y said it could not use the computer.
        self.assertEqual(self.app.ask('open safari and go to apple.com')['desktop_action']['type'], 'start_task')
        self.route('answer', reply="I'm doing it now.")
        self.provider.complete_structured.side_effect = lambda system, messages, **options: (
            {'request': 'Open Safari and go to github.com'} if 'last_task' in messages[-1]['content']
            else {'message': "I'm doing it now."})
        action = self.app.ask('now do the same with github.com')['desktop_action']
        self.assertEqual((action['type'], action['goal']), ('start_task', 'Open Safari and go to github.com'))
        sent = json.loads(self.provider.complete_structured.call_args_list[0].args[1][-1]['content'])
        self.assertEqual(sent, {'last_task': 'open safari and go to apple.com', 'latest': 'now do the same with github.com'})
        # The next follow-up builds on the changed task.
        self.provider.complete_structured.side_effect = lambda system, messages, **options: {'request': 'Open Chrome and go to github.com'}
        self.app.prepare_request('do that again in Chrome')
        self.assertIn('github.com', json.loads(self.provider.complete_structured.call_args.args[1][-1]['content'])['last_task'])

    def test_a_reference_that_is_not_the_task_again_is_routed_as_said(self):
        self.app.ask('open safari and go to apple.com')
        self.provider.complete_structured.side_effect = lambda system, messages, **options: {'request': ''}
        self.route('answer', reply='Apple makes the iPhone.')
        plan = self.app.prepare_request('what does that company make?')
        self.assertNotIn('resolved_request', plan)
        self.assertEqual(self.provider.interpret_request.call_args.args[0], 'what does that company make?')
        self.provider.complete_structured.reset_mock()
        self.app.prepare_request('what is 2+2?')  # answered in code: no extra model call
        self.route('computer_task', 'please open the Downloads folder in Finder for me')
        self.app.prepare_request('please open the Downloads folder in Finder for me')  # long, nothing points back
        self.provider.complete_structured.assert_not_called()

    def test_chat_never_announces_or_denies_computer_use(self):
        from companion.service import CHAT_SYSTEM
        text = ' '.join(CHAT_SYSTEM.split())
        self.assertIn('never say you cannot', text)
        self.assertIn('never say you are doing, starting or about to do something', text)

    def test_reported_short_follow_up_without_a_pronoun(self):
        # Live (conversation suite): "now for Cluj" after "search Wikipedia for Bucharest" answered from Maps.
        self.app.ask('open safari and go to apple.com')
        self.provider.complete_structured.side_effect = lambda system, messages, **options: {'request': 'Open Safari and go to example.org'}
        self.assertEqual(self.app.prepare_request('now example.org')['resolved_request'], 'Open Safari and go to example.org')

    def test_reported_a_note_to_confirm_is_a_task_to_follow_up(self):
        # Live: "and another one saying bye" after a note started a screen task instead of a second note.
        self.route('create_note', 'A11y test 1 | hi')
        plan = self.app.prepare_request('make a note titled A11y test 1 saying hi')
        self.assertTrue(plan.get('app_write'))
        self.assertEqual(self.app.last_task, 'make a note titled A11y test 1 saying hi')


class AppNamedTests(unittest.TestCase):
    """Live (broad suite): naming an app asks for work in it."""
    setUp, route = LiveRoutingTests.setUp, LiveRoutingTests.route

    def catalog(self, *names):
        self.app.desktop_context.bridge.catalog.return_value = [{'name': name, 'bundle_id': 'com.apple.'+name} for name in names]

    def test_reported_conversion_in_calculator_is_done_in_calculator(self):
        self.catalog('Calculator', 'Safari')
        self.route('answer', reply='12 inches is 30.48 centimeters.')
        self.assertEqual(self.app.prepare_request('Convert 12 inches to centimeters in Calculator.')['operation'], 'computer_task')
        self.route('answer', reply='Open View and pick Convert.')
        self.assertEqual(self.app.prepare_request('How do I convert units in Calculator?')['operation'], 'answer')

    def test_reported_switching_an_apps_view_is_work_in_the_app(self):
        # "Switch Calendar to the day view" only brought Calendar forward.
        self.catalog('Calendar', 'Safari')
        self.route('focus_app', app_name='Calendar')
        plan = self.app.prepare_request('Switch Calendar to the day view.')
        self.assertEqual((plan['operation'], plan['opening']['operation'], plan['opening']['app_name']), ('computer_task', 'launch_app', 'Calendar'))
        self.route('focus_app', app_name='Calendar')
        self.assertEqual(self.app.prepare_request('go back to Calendar')['operation'], 'focus_app')

    def test_reported_editing_the_front_document_is_a_task(self):
        # Live (broad): "make all the text bold" was routed to typing, then to a chat refusal.
        self.route('type_text', 'bold', app_name='TextEdit', acts_on_computer=True)
        self.assertEqual(self.app.prepare_request('Make all the text in this document bold.')['operation'], 'computer_task')
        self.route('type_text', 'Ana', acts_on_computer=False)
        self.assertEqual(self.app.prepare_request('My name is Ana')['operation'], 'answer')
        self.route('answer', reply="I don't have access to the document.", acts_on_computer=False)  # the same request, another run
        with patch.object(type(self.app), 'front_app', return_value='TextEdit — Untitled'):
            self.assertEqual(self.app.prepare_request('Make all the text in this document bold.')['operation'], 'computer_task')
            self.route('answer', reply='It is a draft.', acts_on_computer=False)
            self.assertEqual(self.app.prepare_request('What is this document about?')['operation'], 'answer')

    def test_reported_a_reminder_operation_in_another_app_is_a_task(self):
        # Live (broad): "Delete only the note titled …" searched Reminders.
        for app in ('Notes', ''):
            self.route('delete_reminder', 'A11y test 12', app_name=app, acts_on_computer=True)
            self.assertEqual(self.app.prepare_request('Delete only the note titled A11y test 12.')['operation'], 'computer_task')


class CheckedDoneTests(Harness):
    """Live (broad suite): the model said done with the goal not reached ("Sorted by kind" in icon view,
    a timer that never started). Its done is checked against the screen once, then refused."""
    regions = TEAMS_REGIONS

    def test_reported_a_done_with_something_missing_is_retried_then_refused(self):
        task = ComputerTask('Start a ten-minute timer in Clock.', automatic=True, goal_mode='inspect')
        task.record({'type': 'click_point', 'label': 'Timers', 'status': 'submitted'})
        done = {'status': 'done', 'message': 'The timer is set.', 'risk': 'navigation', 'action': None}
        missing = {'evidence': 'Timers', 'missing': 'a ten-minute timer running', 'met': False}
        self.provider.complete_structured.side_effect = [done, missing, done, missing]
        step = self.propose(task)
        self.assertEqual(step['status'], 'needs_user')
        self.assertIn('a ten-minute timer running', step['message'])
        self.assertIn('Not done yet', self.provider.complete_structured.call_args_list[2].args[1][-1]['content'])

    def test_a_checked_done_ends_the_task(self):
        task = ComputerTask('Start a ten-minute timer in Clock.', automatic=True, goal_mode='inspect')
        task.record({'type': 'click_point', 'label': 'Start', 'status': 'submitted'})
        self.provider.complete_structured.side_effect = [
            {'status': 'done', 'message': 'The timer is running.', 'risk': 'navigation', 'action': None},
            {'evidence': 'Activity', 'missing': '', 'met': True}, {'message': 'Your ten-minute timer is running.'}]
        self.assertEqual(self.propose(task)['status'], 'done')


class HarderRoutingRegressions(LiveRoutingTests):
    def test_settings_destination_is_navigation_not_highlight(self):
        request = 'Show me where the light and dark appearance choices live in System Settings, without switching either one.'
        self.route('highlight', 'Appearance', app_name='System Settings', referent='destination', acts_on_computer=True)
        response = self.app.ask(request)
        self.assertEqual(response['desktop_action']['type'], 'start_task')
        self.assertEqual(response['desktop_action']['goal'], request)

    def test_self_move_uses_geometry_without_grounding_call(self):
        request = 'Please tuck yourself into the upper-left corner of my screen.'
        self.route('highlight', 'upper-left', referent='companion', acts_on_computer=True)
        response = self.app.ask(request)
        self.assertEqual(response['desktop_action']['type'], 'move_companion')
        self.assertEqual(response['desktop_action']['destination'], 'top_left')
        self.provider.complete_structured.assert_not_called()

    def test_explicit_reminders_destination_keeps_native_confirmation(self):
        request = 'Put A11y test 90800 in Reminders for tomorrow at 8 in the morning.'
        self.route('add_reminder', 'A11y test 90800', app_name='Reminders', when='2026-10-27T08:00', referent='destination')
        with patch.object(self.app, 'named_installed_app', return_value='Reminders'):
            response = self.app.ask(request)
        self.assertEqual(response['desktop_action']['type'], 'app_write')
        self.assertEqual(response['desktop_action']['kind'], 'add_reminder')
        self.assertEqual(response['desktop_action']['title'], 'A11y test 90800')
        self.assertTrue(response['desktop_action']['due'])

    def test_settings_lookup_never_opens_browser_search(self):
        request = 'Go to About in System Settings and read out the installed memory in GB, without changing anything.'
        self.route('browser_search', 'About in System Settings', app_name='System Settings', referent='destination',
                   remaining_request='read out the installed memory in GB')
        response = self.app.ask(request)
        self.assertEqual(response['desktop_action']['type'], 'start_task')
        self.assertNotIn('opening', response['desktop_action'])

    def test_finder_open_keeps_requested_reading(self):
        import tempfile
        from pathlib import Path
        from companion import mac_apps
        request = 'Open the file called A11y test 314159 packing.txt from this Finder folder and tell me what it says to pack.'
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)/'orchard'
            folder.mkdir()
            path = folder/'A11y test 314159 packing.txt'
            path.write_text('A11y test 314159\nPack the amber lantern.\n')
            self.route('open_file', path.name, remaining_request='tell me what it says to pack', referent='destination')
            with patch.object(mac_apps, 'recent_files', return_value=[]), patch.object(mac_apps, 'run_script', return_value=str(folder)) as script, patch.object(mac_apps.subprocess, 'run') as opener:
                response = self.app.ask(request)
            self.assertIn('amber lantern', response['message'])
            self.assertTrue(response['full_text'])
            self.assertIsNone(response['desktop_action'])
            self.assertIn(str(path), opener.call_args.args[0])
            self.assertNotIn(path.name, script.call_args.args[0])
            self.assertEqual(self.app.conversation[-1]['content'], 'A11y test 314159')


class HarderHarnessRegressions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import sys
        from pathlib import Path
        cls.scripts = str(Path(__file__).resolve().parents[1]/'scripts')
        sys.path.insert(0, cls.scripts)
        import live_harder, live_eval, live_tasks
        cls.harder, cls.le, cls.tasks = live_harder, live_eval, live_tasks

    @classmethod
    def tearDownClass(cls):
        import sys
        sys.path.remove(cls.scripts)

    def test_notes_normalize_layout_and_record_raw_content(self):
        state = {}
        html = '<div>A11y test 0800</div><div>Bring the green blanket\nand two pears.</div>'
        with patch.object(self.harder, 'osa', side_effect=['1', html]):
            self.assertTrue(self.harder.exact_note('A11y test 0800', 'Bring the green blanket and two pears.', state))
        self.assertEqual(state['_observed']['note_html'], html)
        self.assertEqual(state['_observed']['note_count'], '1')
        with patch.object(self.harder, 'osa', side_effect=['1', html.replace('pears', 'apples')]):
            self.assertFalse(self.harder.exact_note('A11y test 0800', 'Bring the green blanket and two pears.'))

    def test_sort_records_all_predicates_and_accepts_column_case(self):
        case = next(c for c in self.harder.build(Mock()) if c['name'] == 'finder_sort')
        # The setup function owns the isolated fixture state; replace native/file setup with a temporary fixture.
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as root:
            with patch.object(self.harder.tempfile, 'mkdtemp', return_value=root), patch.object(self.harder, 'osa', return_value='7'), patch.object(self.le, 'activate'):
                case['setup'](Mock())
            folder = next(Path(root).iterdir())
            state = {}
            with patch.object(self.harder, 'osa', side_effect=[str(folder), 'list view', 'name']):
                self.assertTrue(case['turns'][0].checks[0][1](state, []))
            self.assertTrue(state['_observed']['name_column'])
            self.assertEqual(state['_observed']['finder_target'], str(folder))

    def test_dictionary_fallback_is_scoped_and_preserves_raw_values(self):
        case = next(c for c in self.harder.build(Mock()) if c['name'] == 'lookup_dictionary')
        state = {}
        events = [{'kind': 'say', 'data': {'message': 'Active at night.'}}]
        with patch('live_broad.ax_values', return_value='nocturnal'), patch.object(self.le, 'front_app', return_value='Dictionary'), patch.object(self.le, 'desktop_sample', return_value={'app': {'name': 'Dictionary'}, 'window': {'bounds': {'x': 0}}}), patch.object(self.le, 'screen_lines', return_value=['nocturnal: active at night']) as ocr:
            self.assertTrue(case['turns'][0].checks[0][1](state, events))
            ocr.assert_called_once_with(window_only=True)
        self.assertEqual(state['_observed']['dictionary_ax'], 'nocturnal')
        with patch('live_broad.ax_values', return_value='nocturnal night'), patch.object(self.le, 'front_app', return_value='Safari'):
            self.assertFalse(case['turns'][0].checks[0][1]({}, events))

    def test_failure_replies_do_not_pass(self):
        for message in ('I could not finish: the information window is not on the screen.',
                        'I couldn’t do that safely.', 'I could not find a safe next step, so nothing was done.',
                        'I clicked Recipes, but nothing on the screen changed.',
                        'The same action was proposed on an unchanged screen. I stopped rather than repeat an uncertain action.',
                        'Stopped at the 12-action limit. The task is not marked complete.'):
            events = [{'kind': 'say', 'data': {'message': message}}]
            self.assertTrue(self.le.failed_reply(message), message)
            self.assertFalse(self.tasks.no_failure({}, events), message)
        self.assertFalse(self.le.failed_reply('The folder is open.'))

    def test_build_change_aborts_before_next_request(self):
        from pathlib import Path
        with patch('companion.instance.build_id', return_value='first'):
            app = self.le.App(Path('/private/tmp'), Path('/private/tmp/unused.log'))
        self.addCleanup(app.socket.parent.rmdir)
        with patch('companion.instance.build_id', return_value='second'):
            with self.assertRaises(SystemExit):
                app.call({'cmd': 'ask', 'request': 'test'})


class HarderFileLookupRegressions(unittest.TestCase):
    def test_lookup_uses_common_folder_before_finder_and_never_executes_installer(self):
        import tempfile
        from pathlib import Path
        from companion import mac_apps
        with tempfile.TemporaryDirectory() as root:
            home = Path(root)
            (home/'Downloads').mkdir()
            path = home/'Downloads'/'packing.txt'
            path.write_text('Pack the amber lantern.')
            opener, script = Mock(), Mock(side_effect=AssertionError('No Finder lookup needed'))
            self.assertEqual(mac_apps.open_file(path.name, home, opener, script=script, read_content=True), 'Pack the amber lantern.')
            script.assert_not_called()
            installer = home/'Setup.pkg'
            installer.write_bytes(b'installer')
            script = Mock(return_value=str(home))
            mac_apps.open_file(installer.name, home, opener, script=script, read_content=True)
            self.assertEqual(opener.call_args.args[0], ['open', '-R', str(installer)])

    def test_missing_finder_window_and_ambiguous_names_do_not_open_anything(self):
        import tempfile
        from pathlib import Path
        from companion import mac_apps
        with tempfile.TemporaryDirectory() as root:
            home, opener = Path(root), Mock()
            reply = mac_apps.open_file('packing.txt', home, opener, script=Mock(return_value=''))
            self.assertIn('could not find', reply)
            (home/'one packing.txt').touch()
            (home/'two packing.txt').touch()
            reply = mac_apps.open_file('packing.txt', home, opener, script=Mock(return_value=root))
            self.assertIn('could not find', reply)
            opener.assert_not_called()


class StructuredCompletionTests(Harness):
    def test_finder_nested_selection_is_not_an_open_folder(self):
        from companion.computer_task import compact_desktop
        task = ComputerTask('In this Finder folder, go into Recipes and then Saturday.', goal_mode='inspect')
        snapshot = dict(self.snapshot, finder={'target': '/tmp/orchard', 'view': 'list view', 'sort': 'name'},
                        selected=[{'role': 'AXRow', 'name': 'Saturday'}])
        self.desktop.bridge.value = snapshot
        with patch('companion.desktop_context.finder_facts', return_value=snapshot['finder']):
            self.desktop.collect_once(targets=True)
        self.assertEqual(compact_desktop(self.desktop.context())['facts']['finder']['target'], '/tmp/orchard')
        self.provider.complete_structured.return_value = {'met': False, 'evidence': 'Saturday', 'missing': 'Finder target is still /tmp/orchard'}
        self.assertIn('target', task.still_missing(self.provider, snapshot, [], {}))
        payload = json.loads(self.provider.complete_structured.call_args.args[1][0]['content'])
        self.assertEqual(payload['facts']['selected'][0]['name'], 'Saturday')

    def test_finder_info_done_requires_observed_evidence(self):
        task = ComputerTask('Show the information window for this orchard folder in Finder, so I can see its details.', goal_mode='inspect')
        self.provider.complete_structured.return_value = {'met': True, 'missing': '', 'evidence': 'orchard Info'}
        self.assertTrue(task.still_missing(self.provider, self.snapshot, [], {}))
        snapshot = dict(self.snapshot, window={'title': 'orchard Info'})
        self.assertEqual(task.still_missing(self.provider, snapshot, [], {}), '')
        self.provider.complete_structured.side_effect = RuntimeError('offline')
        self.assertTrue(task.still_missing(self.provider, snapshot, [], {}))

    def test_preview_sidebar_2_exact_document_and_focused_text(self):
        task = ComputerTask('Keep those thumbnails showing and tell me the filename of this PDF.', goal_mode='inspect')
        snapshot = dict(self.snapshot, window={'title': 'marker', 'document': 'file:///tmp/marker%20atlas.pdf'},
                        focused={'role': 'AXStaticText', 'value': 'Page 1'})
        self.provider.complete_structured.return_value = {'message': 'marker atlas.pdf'}
        self.assertEqual(task.screen_answer(self.provider, snapshot, [], 'marker', {}), 'marker atlas.pdf')
        payload = json.loads(self.provider.complete_structured.call_args.args[1][0]['content'])
        self.assertEqual(payload['facts']['document_name'], 'marker atlas.pdf')
        self.assertEqual(payload['facts']['focused']['value'], 'Page 1')

    def test_web_screen_answer_has_url_heading_and_ocr(self):
        task = ComputerTask('Visit example.org in Safari and read me the heading on that page.', goal_mode='inspect')
        snapshot = dict(self.snapshot, window={'title': 'Example Domain', 'url': 'https://example.org/'},
                        focused={'role': 'AXHeading', 'value': 'Example Domain'})
        self.provider.complete_structured.return_value = {'message': 'Example Domain'}
        task.screen_answer(self.provider, snapshot, [region('This domain is for use in documentation examples.', .2, .4)], '', {})
        payload = json.loads(self.provider.complete_structured.call_args.args[1][0]['content'])
        self.assertEqual(payload['facts']['url'], 'https://example.org/')
        self.assertEqual(payload['facts']['focused']['value'], 'Example Domain')
        self.assertIn('documentation', payload['screen_text'][0])

    def test_secure_values_are_never_in_model_facts(self):
        from companion.computer_task import structured_facts
        snapshot = {'focused_role': 'secure_input', 'focused': {'value': 'secret'}, 'selected': [{'value': 'secret'}]}
        self.assertEqual(structured_facts(snapshot), {})
        self.assertEqual(structured_facts({'focused': {'role': 'AXSecureTextField', 'value': 'secret'}}), {})

    def test_report_only_cannot_bypass_completion_check(self):
        task = ComputerTask('In this Finder folder, go into Recipes and then Saturday.', goal_mode='inspect')
        task.receipts = [{'type': 'press_control'}]
        self.provider.complete_structured.side_effect = [
            {'status': 'done', 'action': None, 'message': 'I can see Saturday.'},
            {'met': False, 'evidence': 'Saturday', 'missing': 'Saturday is selected, not open'}]
        result = task.report_only(self.provider, [], None, {}, lambda: None, self.snapshot, [])
        self.assertEqual(result['status'], 'needs_user')

    def test_finder_facts_are_frontmost_only_and_pass_bundle_as_argv(self):
        from companion.desktop_context import finder_facts, FINDER_FACTS_SCRIPT
        with patch('companion.desktop_context.sys.platform', 'darwin'), patch('companion.desktop_context.subprocess.run') as run:
            self.assertEqual(finder_facts({'app': {'bundle_id': 'com.apple.Preview'}}), {})
            run.assert_not_called()
            run.return_value = Mock(returncode=0, stdout='/tmp/orchard/\nlist view\nname\n')
            facts = finder_facts({'app': {'bundle_id': 'com.apple.finder'}})
            self.assertEqual(facts, {'target': '/tmp/orchard/', 'view': 'list view', 'sort': 'name'})
            self.assertEqual(run.call_args.args[0], ['osascript', '-e', FINDER_FACTS_SCRIPT, 'com.apple.finder'])
            self.assertIn('if not frontmost', FINDER_FACTS_SCRIPT)

    def test_missing_facts_add_no_step_observation_tokens(self):
        from companion.computer_task import compact_desktop
        self.assertNotIn('facts', compact_desktop(self.snapshot))
