"""Tests the staged planner boundary; model semantic quality has separate live evaluators."""
import os
os.environ.setdefault('A11Y_WEB_ANSWERS', '0')  # tests never look things up online unless a test turns it on
os.environ.setdefault('A11Y_ROUTES', '0')  # nor read routes a live run remembered
import json
import unittest
from unittest.mock import Mock, patch
from companion.request_plan import response_schema, validate_plan
from companion.computer_use import ComputerUse
from companion.desktop_context import DesktopContext
from companion.models import ValidationError
from companion.ollama import OllamaProvider
from companion.provider import AIConfig
from test_core import CompanionFixture
from test_desktop_context import FakeBridge


class StagedProvider:
    def __init__(self, operation, app_name='Terminal', target=''):
        self.interpret_request = Mock(return_value={'operation': operation, 'app_name': app_name, 'target': target})
        self.complete_structured = Mock()
        self.complete = Mock(side_effect=AssertionError('Actions must not use unrestricted chat output.'))


class RequestPlanningTests(CompanionFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.bridge = FakeBridge()
        self.bridge.catalog = Mock(return_value=[{'name': 'Terminal', 'bundle_id': 'com.apple.Terminal'},
                                                {'name': 'Stremio', 'bundle_id': 'com.fixture.stremio'}])
        self.bridge.application_action = Mock(return_value={'submitted': True})
        self.bridge.app_state = Mock(return_value={'bundle_id': 'com.fixture.stremio', 'running': True})
        self.monitor = self.app.desktop_context = DesktopContext(self.bridge, clock=lambda: 100)

    def test_open_stremio_with_awareness_off_does_not_focus_or_launch_terminal(self):
        provider = self.app.provider = StagedProvider('launch_app', 'Stremio')
        self.app.desktop_session.append({'role': 'assistant', 'content': 'Move toward Terminal to open another app.'})
        self.bridge.sample = Mock(side_effect=AssertionError('Launch must not read the desktop.'))
        result = self.app.ask('open up stremio please')
        self.assertEqual(result['desktop_action']['type'], 'launch_app')
        self.assertEqual(result['desktop_action']['bundle_id'], 'com.fixture.stremio')
        self.assertIn('Open Stremio', result['message'])
        provider.complete.assert_not_called()
        provider.complete_structured.assert_not_called()
        provider.interpret_request.assert_not_called()  # "open X" is resolved in code
        receipt = ComputerUse(self.monitor).execute_confirmed(result['desktop_action'])
        self.assertTrue(receipt['application_state']['running'])
        self.assertFalse(self.monitor.enabled)
        self.bridge.sample.assert_not_called()

    def test_unknown_app_never_falls_back_to_terminal(self):
        provider = self.app.provider = StagedProvider('launch_app', 'MissingApp')
        self.bridge.catalog.return_value.append({'name': 'Safari', 'bundle_id': 'com.apple.Safari'})
        result = self.app.ask('open MissingApp')
        # Not installed: searched on the web (then its website opened), never a substitute local app.
        self.assertEqual(result['desktop_action']['type'], 'start_task')
        self.assertEqual(result['desktop_action']['opening']['url'], 'https://duckduckgo.com/?q=MissingApp')
        provider.complete_structured.assert_not_called()
        self.bridge.application_action.assert_not_called()

    def test_close_without_access_requests_app_metadata_not_movement_or_pixels(self):
        provider = self.app.provider = StagedProvider('close_window')
        result = self.app.ask('close the lower right terminal please')
        self.assertEqual(result['desktop_action']['type'], 'request_app_access')
        provider.complete_structured.assert_not_called()

    def test_object_without_pixels_never_uses_a_corner_search(self):
        provider = self.app.provider = StagedProvider('move_object', '', 'ally name on Terminal title')
        result = self.app.ask('move to the ally name on the left terminal title')
        self.assertEqual(result['desktop_action']['type'], 'request_screen')
        provider.complete_structured.assert_not_called()

    def test_named_app_actions_resolve_without_model_ids(self):
        window = {'window_id': 'com.apple.Terminal:1', 'name': 'Terminal', 'bundle_id': 'com.apple.Terminal',
                  'title': 'zsh', 'can_close': True, 'bounds': {'x': 650, 'y': 410, 'width': 500, 'height': 300}}
        self.bridge.value['app_windows'] = [window]
        self.bridge.value['available_apps'] = [{'name': 'Terminal', 'bundle_id': 'com.apple.Terminal'}]
        self.monitor.enabled = True
        self.monitor.collect_once()
        for operation, expected in [('close_window', {'type': 'close_window', 'window_id': 'com.apple.Terminal:1'}),
                                    ('quit_app', {'type': 'quit_app', 'bundle_id': 'com.apple.Terminal'}),
                                    ('focus_app', {'type': 'focus_app', 'bundle_id': 'com.apple.Terminal'})]:
            provider = self.app.provider = StagedProvider(operation, 'terminal')
            result = self.app.ask(operation.replace('_', ' ')+' terminal')
            provider.complete_structured.assert_not_called()
            self.assertEqual({k: result['desktop_action'][k] for k in expected}, expected)
            if operation != 'focus_app':
                self.assertNotIn('Confirm below', result['message'])   # the user asked for no confirmation step
        self.app.provider = StagedProvider('quit_app', 'Discord')
        result = self.app.ask('close discord')
        self.assertIsNone(result['desktop_action'])
        self.assertIn('Discord', result['message'])

    def test_wrong_operation_is_rejected_independently_of_model_self_classification(self):
        provider = self.app.provider = StagedProvider('close_window')
        # Two Terminal windows: choosing "the lower right" one is left to the model.
        self.bridge.value['app_windows'] = [{'window_id': 'com.apple.Terminal:1', 'name': 'Terminal',
            'bundle_id': 'com.apple.Terminal', 'title': 'zsh', 'can_close': True,
            'bounds': {'x': 650, 'y': 410, 'width': 500, 'height': 300}},
            {'window_id': 'com.apple.Terminal:0', 'name': 'Terminal', 'bundle_id': 'com.apple.Terminal',
             'title': 'logs', 'can_close': True, 'bounds': {'x': 0, 'y': 0, 'width': 500, 'height': 300}}]
        self.monitor.enabled = True
        self.monitor.collect_once()
        base = {'message': 'Proposed operation.', 'target': None}
        provider.complete_structured.side_effect = [
            dict(base, request_kind='move_geometry', desktop_action={'type': 'move_companion', 'destination': 'bottom_right'}),
            dict(base, request_kind='close_window', desktop_action={'type': 'close_window', 'window_id': 'com.apple.Terminal:1'})]
        result = self.app.ask('close the lower right terminal please')
        self.assertEqual(provider.complete_structured.call_count, 2)
        self.assertEqual(result['desktop_action']['type'], 'close_window')
        self.assertNotIn('Confirm below', result['message'])   # the user asked for no confirmation step
        first = provider.complete_structured.call_args_list[0]
        context = json.loads(first.args[1][-2]['content'])
        self.assertNotIn('history', context)
        self.assertEqual(context['request_plan']['operation'], 'close_window')
        variants = first.kwargs['schema']['properties']['desktop_action']['anyOf']
        types = [value for entry in variants if 'properties' in entry for value in entry['properties']['type'].get('enum', [])]
        self.assertNotIn('move_companion', types)
        self.assertNotIn('launch_app', types)

    def test_verification_schema_has_no_action_field_or_original_instruction(self):
        provider = self.app.provider = StagedProvider('launch_app', 'Stremio')
        self.app.last_action_result = {'type': 'press_control', 'application_state': {'running': True}, 'task_completed': False}
        provider.complete_structured.return_value = {'message': 'Stremio is running. Its window contents are not available.'}
        result = self.app.ask('open Stremio', mode='verify')
        provider.interpret_request.assert_not_called()
        call = provider.complete_structured.call_args
        self.assertEqual(list(call.kwargs['schema']['properties']), ['message'])
        payload = json.loads(call.args[1][0]['content'])
        self.assertNotIn('request', payload)
        self.assertNotIn('history', payload)
        self.assertIsNone(result['desktop_action'])
        self.assertIn('Stremio is running', result['message'])

    def test_malformed_verification_cannot_issue_actions(self):
        provider = self.app.provider = StagedProvider('answer')
        provider.complete_structured.return_value = {'message': 'Opening Terminal', 'desktop_action': {'type': 'launch_app', 'bundle_id': 'com.apple.Terminal'}}
        with self.assertRaises(ValidationError):
            self.app.ask('open Stremio', mode='verify')
        self.bridge.application_action.assert_not_called()


class IntentTransportTests(unittest.TestCase):
    def test_intent_call_uses_small_schema_and_no_screen_or_assistant_history(self):
        provider = OllamaProvider(AIConfig(base_url='http://127.0.0.1:11434', model='test'))
        provider.metadata['test'] = {'capabilities': ['completion', 'thinking']}
        with patch.object(provider, 'request', side_effect=[
                {'done': True, 'message': {'content': json.dumps({'operation': 'launch_app', 'app_name': 'Stremio', 'target': ''})}},
                {'done': True, 'message': {'content': '{"requires_followup_actions":false}'}}]) as request:
            plan = provider.interpret_request('open up stremio please', ['close Terminal'])
        body = request.call_args_list[0].args[1]
        self.assertFalse(body['think'])
        self.assertEqual(body['options']['num_predict'], 700)  # room for a direct reply
        # The wire schema is one shape per group of operations (request_plan.INTENT_WIRE_SCHEMA); the
        # launch_app shape holds the plan's fields except "when" and the artist, and no free reply.
        shape = next(item for item in body['format']['anyOf'] if 'launch_app' in item['properties']['operation']['enum'])
        self.assertEqual(sorted(shape['required']), ['acts_on_computer', 'app_name', 'browser_mode', 'goal_mode', 'operation',
                                                      'referent', 'remaining_request', 'reply', 'target'])
        self.assertEqual(shape['properties']['reply'], {'const': ''})
        self.assertNotIn('images', body['messages'][-1])
        self.assertEqual(plan['app_name'], 'Stremio')

    def test_object_schema_cannot_emit_geometric_movement(self):
        schema = response_schema({'operation': 'move_object'})
        variants = schema['properties']['desktop_action']['anyOf']
        for entry in variants:
            if 'properties' in entry:
                types = entry['properties']['type']['enum']
                self.assertNotIn('move_companion', types)
                if 'move_to_point' in types:
                    self.assertEqual(entry['properties']['basis'], {'const': 'image'})

    def test_bad_interpretation_is_rejected_before_grounding(self):
        for plan in [None, {'operation': 'run_shell', 'app_name': 'Terminal', 'target': 'anything'},
                     {'operation': 'launch_app', 'app_name': 123, 'target': ''}]:
            with self.assertRaises(ValidationError):
                validate_plan(plan)


class FastPathTests(unittest.TestCase):
    def test_single_app_commands_skip_the_planner(self):
        from companion.request_plan import simple_app_command
        for request, operation, name in [('open discord', 'launch_app', 'discord'), ('Please open the Settings app', 'launch_app', 'Settings'),
                                         ('Hey Ally, launch Microsoft Teams.', 'launch_app', 'Microsoft Teams'),
                                         ('close spotify', 'close_window', 'spotify'), ('quit Notepad please', 'quit_app', 'Notepad'),
                                         ('switch to outlook', 'focus_app', 'outlook')]:
            plan = simple_app_command(request)
            self.assertEqual((plan['operation'], plan['app_name']), (operation, name), request)
            self.assertEqual(plan['remaining_request'], '')

    def test_anything_more_than_one_app_goes_to_the_planner(self):
        from companion.request_plan import simple_app_command
        for request in ('open teams and check my tasks', 'open notepad then write a summary', 'open the report in excel',
                        'open youtube.com', 'close the tab', 'how do I open settings?', 'open a new window for the quarterly budget review',
                        'start the timer for five minutes', 'what is open right now'):
            self.assertIsNone(simple_app_command(request), request)

    def test_echoed_remaining_request_is_not_follow_up_work(self):
        from companion.request_plan import preserve_desktop_goal
        plan = {'operation': 'launch_app', 'app_name': 'Discord', 'target': '', 'browser_mode': 'default', 'remaining_request': 'Open Discord.'}
        self.assertEqual(preserve_desktop_goal(plan, 'open discord')['operation'], 'launch_app')
        plan['remaining_request'] = 'and check my messages'
        self.assertEqual(preserve_desktop_goal(plan, 'open discord and check my messages')['operation'], 'computer_task')


class FastPathServiceTests(CompanionFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.bridge = FakeBridge()
        self.bridge.catalog = Mock(return_value=[{'name': 'Discord', 'bundle_id': 'discord.exe'},
                                                {'name': 'Outlook (classic)', 'bundle_id': 'o1'}, {'name': 'Outlook (new)', 'bundle_id': 'o2'}])
        self.app.desktop_context = DesktopContext(self.bridge, clock=lambda: 100)

    def test_known_app_opens_without_any_model_call(self):
        provider = self.app.provider = StagedProvider('answer')
        result = self.app.ask('open discord')
        provider.interpret_request.assert_not_called()
        provider.complete_structured.assert_not_called()
        self.assertEqual(result['desktop_action']['bundle_id'], 'discord.exe')
        self.assertEqual(result['message'], 'Open Discord…')

    def test_ambiguous_name_still_uses_the_planner(self):
        provider = self.app.provider = StagedProvider('launch_app', 'Outlook')
        self.app.ask('open outlook')
        provider.interpret_request.assert_called_once()

    def test_tasks_fail_fast_where_input_is_not_supported(self):
        self.bridge.supports_input = False
        self.app.provider = StagedProvider('computer_task', target='open notepad and write a summary')
        result = self.app.ask('open notepad and write a summary')
        self.assertIsNone(result['desktop_action'])
        self.assertIn('not available on this system yet', result['message'])

    def test_close_and_quit_results_are_reported_without_a_model_call(self):
        provider = self.app.provider = StagedProvider('answer')
        self.app.last_action_result = {'type': 'quit_app', 'label': 'Quit Discord', 'application_state': {'running': False}}
        self.assertEqual(self.app.ask('quit discord', mode='verify')['message'], 'Closed Discord.')
        self.app.last_action_result = {'type': 'close_window', 'label': 'Close Discord — Friends', 'before': {'window_count': 1},
                                       'after': {'window_count': 1}}
        self.assertIn('still open', self.app.ask('close discord', mode='verify')['message'])
        provider.complete_structured.assert_not_called()
