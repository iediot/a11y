"""An unchanged view cannot turn a compound navigation/read task into success."""
import hashlib
import json
import time
import unittest
from unittest.mock import Mock

from companion.computer_task import ComputerTask, SYSTEM, MET_SYSTEM, ANSWER_SYSTEM, scroll_view_state
from companion.desktop_context import DesktopContext
from test_computer_task import context
from test_desktop_context import FakeBridge


IMAGE = 'data:image/png;base64,synthetic'
SCROLL = {'type': 'scroll', 'direction': 'down'}
SEARCH = {'type': 'press_key', 'key': 'cmd_f'}


def action(value):
    return {'status': 'action', 'message': 'Continue toward the requested result.', 'risk': 'navigation', 'action': value}


def done():
    return {'status': 'done', 'message': 'The requested information is not visible.', 'risk': 'navigation', 'action': None}


class RepeatRecoveryTests(unittest.TestCase):
    def setUp(self):
        bridge = FakeBridge()
        bridge.value = context()
        self.desktop = DesktopContext(bridge, clock=lambda: 100)
        self.desktop.enabled = True
        self.desktop.collect_once()
        self.regions = [dict(text='Equipment catalog', x=.2, y=.2, width=.2, height=.04),
                        dict(text='Equipment shelf 0', x=.2, y=.3, width=.2, height=.04)]
        self.vision = Mock(enabled=True, generation=0)
        self.vision.capture.side_effect = lambda *a, **kw: {
            'image_data': IMAGE, 'display_id': 7, 'text_regions': self.regions,
            'screen': dict(x=0, y=0, width=1000, height=700), 'captured_monotonic': time.monotonic()}
        self.task = ComputerTask('Scroll through the equipment catalog, open its Route guide, and tell me the collection point.',
                                  automatic=True, goal_mode='inspect')
        self.task.planned = True
        self.task.receipts = [dict(type='scroll', direction='down', label='Scroll down', status='submitted')]
        self.task.last_proposal = self.signature(SCROLL)
        self.step_messages = []
        self.verification = dict(met=False, evidence='Equipment catalog', missing='The guide is not open and the collection point is missing.')

    def signature(self, value, image=IMAGE):
        return (hashlib.sha256(image.encode()).hexdigest(), scroll_view_state(self.desktop.context(), self.regions),
                json.dumps(value, sort_keys=True))

    def run_choices(self, choices):
        choices = iter(choices)
        def respond(system, messages, **kwargs):
            if system == SYSTEM:
                self.step_messages.append(messages)
                return next(choices)
            if system == MET_SYSTEM:
                return self.verification
            if system == ANSWER_SYSTEM:
                return {'message': 'Collection point: Ridge 42.'}
            raise AssertionError('Unexpected model stage')
        self.provider = Mock(complete_structured=Mock(side_effect=respond))
        return self.task.propose(self.desktop, self.vision, self.provider, {'x': 0, 'y': 0})

    def test_identical_scroll_reconsiders_and_accepts_a_different_method(self):
        result = self.run_choices([action(SCROLL), action(SEARCH)])
        self.assertEqual(result['status'], 'action')
        self.assertEqual(result['action']['key'], 'cmd_f')
        self.assertIn('Choose another useful authorized method', self.step_messages[1][-1]['content'])
        self.assertEqual(len(self.step_messages), 2)
        self.assertFalse(self.task.queue)
        self.assertFalse(any(call.args[0] == ANSWER_SYSTEM for call in self.provider.complete_structured.call_args_list))

    def test_repeated_scroll_and_unsupported_terminal_report_cannot_claim_done(self):
        result = self.run_choices([action(SCROLL), action(SCROLL), done()])
        self.assertEqual(result['status'], 'needs_user')
        self.assertIsNone(result['action'])
        self.assertIn('guide is not open', result['message'])

    def test_unusable_terminal_report_after_repetition_pauses_without_an_exception(self):
        result = self.run_choices([action(SCROLL), action(SCROLL), action(SCROLL)])
        self.assertEqual(result['status'], 'needs_user')
        self.assertIsNone(result['action'])
        self.assertIsNone(self.task.pending)

    def test_done_for_compound_fact_goal_must_check_the_navigation_and_answer(self):
        self.task.last_proposal = None
        result = self.run_choices([done(), action(SEARCH)])
        self.assertEqual(result['status'], 'action')
        self.assertEqual(result['action']['key'], 'cmd_f')
        self.assertIn('guide is not open', self.step_messages[1][-1]['content'])

    def test_reading_wording_does_not_waive_missing_information_after_an_action(self):
        self.task.goal = 'What is the collection point in the guide?'
        self.task.last_proposal = None
        result = self.run_choices([done(), done()])
        self.assertEqual(result['status'], 'needs_user')
        self.assertIn('collection point is missing', result['message'])

    def test_verified_reading_still_finishes_and_answers(self):
        self.regions.append(dict(text='Collection point: Ridge 42.', x=.2, y=.4, width=.3, height=.04))
        self.task.goal = 'Tell me the collection point.'
        self.task.last_proposal = None
        self.verification = dict(met=True, evidence='Collection point: Ridge 42.', missing='')
        result = self.run_choices([done()])
        self.assertEqual(result['status'], 'done')
        self.assertEqual(result['message'], 'Collection point: Ridge 42.')

    def test_same_scroll_on_a_new_image_remains_allowed(self):
        self.task.last_proposal = self.signature(SCROLL, image='older image')
        result = self.run_choices([action(SCROLL)])
        self.assertEqual(result['action']['type'], 'scroll')
        self.assertEqual(len(self.step_messages), 1)

    def test_new_observed_rows_with_the_same_image_are_not_a_stalled_action(self):
        self.regions.append(dict(text='A newly observed row', x=.2, y=.4, width=.2, height=.04))
        result = self.run_choices([action(SCROLL)])
        self.assertEqual(result['action']['type'], 'scroll')
        self.assertEqual(len(self.step_messages), 1)

    def test_wait_does_not_trigger_the_unchanged_action_guard(self):
        wait = {'type': 'wait', 'milliseconds': 250}
        self.task.last_proposal = self.signature(wait)
        result = self.run_choices([action(wait)])
        self.assertEqual(result['action']['type'], 'wait')

    def test_repeat_clicks_cannot_skip_the_full_goal_check(self):
        click = {'type': 'press_control', 'control_id': 'control-0'}
        self.task.receipts = [dict(type='press_control', label='New Folder', status='submitted') for _ in range(2)]
        self.task.last_proposal = None
        result = self.run_choices([action(click), done()])
        self.assertEqual(result['status'], 'needs_user')
        self.assertIn('guide is not open', result['message'])
        self.assertIn('already ran twice', self.step_messages[1][-1]['content'])


if __name__ == '__main__':unittest.main()
