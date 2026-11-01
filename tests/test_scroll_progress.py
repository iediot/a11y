import unittest
from copy import deepcopy
import json
import time
from unittest.mock import Mock

from companion.computer_task import ComputerTask, scroll_view_state


class ScrollProgressTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = {'app': {'bundle_id': 'fixture.app'},
                         'window': {'title': 'A11y test', 'bounds': {'x': 10, 'y': 20, 'width': 500, 'height': 400}}}
        self.regions = [{'text': 'A visible row', 'x': .2, 'y': .3, 'width': .2, 'height': .04}]
        self.task = ComputerTask('Find the requested document', automatic=True)
        self.task.check_last_scroll(self.snapshot, self.regions)

    def scroll(self):
        self.task.record({'type': 'scroll', 'direction': 'down', 'status': 'submitted'})

    def test_stalled_tool_returns_control_after_two_unchanged_observations(self):
        self.task.queue = [{'scroll_to': 'Requested document', 'left': 6}]
        self.scroll()
        self.task.check_last_scroll(self.snapshot, self.regions)
        self.assertTrue(self.task.queue)
        self.assertEqual(self.task.receipts[-1]['scroll_progress'], 'unchanged')
        self.scroll()
        self.task.check_last_scroll(self.snapshot, self.regions)
        self.assertFalse(self.task.queue)
        self.assertIn('choose another way', self.task.receipts[-1]['effect'])
        self.assertEqual(self.task.receipts[-1]['direction'], 'down')
        self.assertEqual(self.task.status, 'running')  # a stall is not task success or failure

    def test_same_words_moving_on_screen_are_progress(self):
        self.task.queue = [{'scroll_to': 'Requested document', 'left': 6}]
        self.scroll()
        moved = [dict(self.regions[0], y=.1)]
        self.task.check_last_scroll(self.snapshot, moved)
        self.assertEqual(self.task.receipts[-1]['scroll_progress'], 'changed')
        self.assertTrue(self.task.queue)
        self.assertIn('completion still needs evidence', self.task.receipts[-1]['effect'])

    def test_native_target_movement_counts_without_ocr(self):
        target = {'label': 'Row', 'role': 'AXRow', 'bounds': {'x': 20, 'y': 90, 'width': 100, 'height': 20}}
        a = dict(self.snapshot, targets=[target])
        b = deepcopy(a)
        b['targets'][0]['bounds']['y'] = 60
        self.assertNotEqual(scroll_view_state(a, []), scroll_view_state(b, []))

    def test_direction_survives_executor_receipt_without_direction(self):
        self.task.pending = {'type': 'scroll', 'direction': 'up'}
        self.task.record({'type': 'scroll', 'status': 'submitted'})
        self.assertEqual(self.task.receipts[-1]['direction'], 'up')

    def test_absent_content_is_unknown_instead_of_unchanged(self):
        self.assertIsNone(scroll_view_state(self.snapshot, []))
        self.task.queue = [{'scroll_to': 'Requested document', 'left': 6}]
        self.task.check_last_scroll(self.snapshot, [])
        self.scroll()
        self.task.check_last_scroll(self.snapshot, [])
        self.assertNotIn('scroll_progress', self.task.receipts[-1])
        self.assertTrue(self.task.queue)

    def test_progress_breaks_stalled_sequence(self):
        self.scroll()
        self.task.check_last_scroll(self.snapshot, self.regions)
        self.scroll()
        moved = [dict(self.regions[0], text='Another row')]
        self.task.check_last_scroll(self.snapshot, moved)
        self.task.queue = [{'scroll_to': 'Requested document', 'left': 4}]
        self.scroll()
        self.task.check_last_scroll(self.snapshot, moved)
        self.assertTrue(self.task.queue)

    def test_other_queued_tools_and_click_effects_are_preserved(self):
        self.task.queue = [{'type': 'type_text', 'text': 'Search'}]
        for _ in range(2):
            self.scroll()
            self.task.check_last_scroll(self.snapshot, self.regions)
        self.assertTrue(self.task.queue)
        self.task.receipts.append({'type': 'click_point', 'effect': 'the text field is focused: type into it now'})
        self.task.check_last_scroll(self.snapshot, self.regions)
        self.assertEqual(self.task.receipts[-1]['effect'], 'the text field is focused: type into it now')

    def test_stall_evidence_reaches_model_and_model_selects_recovery(self):
        from companion.desktop_context import DesktopContext
        from test_desktop_context import FakeBridge
        from test_computer_task import context
        bridge = FakeBridge()
        bridge.value = context()
        desktop = DesktopContext(bridge, clock=lambda: 100)
        desktop.enabled = True
        desktop.collect_once()
        snapshot = desktop.context()
        b = snapshot['window']['bounds']
        regions = [{'text': 'Visible row', 'x': (b['x']+b['width']/2)/1000,
                    'y': (b['y']+b['height']/2)/700, 'width': .01, 'height': .01}]
        self.task.goal_mode = 'inspect'
        self.task.check_last_scroll(snapshot, regions)
        self.task.queue = [{'scroll_to': 'Requested document', 'left': 6}]
        self.scroll()
        self.task.check_last_scroll(snapshot, regions)
        self.scroll()  # second outcome is checked by the actual propose path
        vision = Mock(enabled=True, generation=0)
        vision.capture.side_effect = lambda *a, **kw: {
            'image_data': 'data:image/png;base64,synthetic', 'text_regions': regions,
            'screen': {'x': 0, 'y': 0, 'width': 1000, 'height': 700}, 'captured_monotonic': time.monotonic()}
        provider = Mock()
        provider.complete_structured.return_value = {
            'status': 'action', 'message': 'Search the current app.', 'risk': 'navigation',
            'action': {'type': 'press_key', 'key': 'cmd_f'}}
        result = self.task.propose(desktop, vision, provider, {'x': 0, 'y': 0})
        history = json.loads(provider.complete_structured.call_args.args[1][1]['content'])['actions_taken']
        self.assertIn('choose another way', history[-1]['effect'])
        self.assertEqual(history[-1]['direction'], 'down')
        self.assertEqual(result['action']['key'], 'cmd_f')
        self.assertFalse(self.task.queue)


if __name__ == '__main__':
    unittest.main()
