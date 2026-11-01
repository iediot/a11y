"""A validated tool queue must not carry input into an unrelated application."""
from copy import deepcopy
import unittest
from unittest.mock import Mock, patch

from companion.computer_task import ComputerTask


class ToolQueueDestinationTests(unittest.TestCase):
    def setUp(self):
        self.task = ComputerTask('Locate the requested item.', automatic=True, goal_mode='inspect')
        self.snapshot = {'app': {'bundle_id': 'fixture.browser'},
                         'window': {'title': 'Catalog'}}

    def test_every_queued_input_stops_when_another_app_is_in_front(self):
        for action in ({'scroll_to': 'Route guide', 'left': 6},
                       {'type': 'press_key', 'key': 'enter'},
                       {'type': 'type_text', 'text': 'search'}):
            with self.subTest(action=action):
                self.task.queue = [dict(action, bundle='fixture.browser', window='Catalog')]
                snapshot = deepcopy(self.snapshot)
                snapshot['app']['bundle_id'] = 'unrelated.app'
                with patch('companion.computer_task.validate_step') as validate:
                    self.assertIsNone(self.task.queued_step(Mock(), snapshot, [], {}, ''))
                    validate.assert_not_called()
                self.assertEqual(self.task.queue, [])
                self.assertIsNone(self.task.pending)

    def test_scrolling_stops_when_a_different_window_is_in_front(self):
        self.task.queue = [{'scroll_to': 'Route guide', 'left': 6,
                            'bundle': 'fixture.browser', 'window': 'Catalog'}]
        snapshot = deepcopy(self.snapshot)
        snapshot['window']['title'] = 'Different document'
        with patch('companion.computer_task.validate_step') as validate:
            self.assertIsNone(self.task.queued_step(Mock(), snapshot, [], {}, ''))
            validate.assert_not_called()
        self.assertEqual(self.task.queue, [])

    def test_scope_change_clears_the_entire_remaining_tool(self):
        self.task.queue = [dict(type='press_key', key='enter', bundle='old.app', window='Old'),
                           dict(type='type_text', text='more', bundle='old.app', window='Old')]
        self.assertIsNone(self.task.queued_step(Mock(), self.snapshot, [], {}, ''))
        self.assertEqual(self.task.queue, [])

    def test_search_enter_can_follow_a_title_update_in_the_same_app(self):
        self.task.queue = [dict(type='press_key', key='enter', bundle='fixture.browser', window='Old title')]
        action = dict(type='press_key', key='enter')
        with patch('companion.computer_task.validate_step', return_value={'action': action}) as validate:
            result = self.task.queued_step(Mock(), self.snapshot, [], {}, '')
        self.assertEqual(result['action'], action)
        validate.assert_called_once()
        self.assertTrue(result['review'])  # no search-field evidence means normal Enter review

    def test_scroll_tool_stops_when_requested_text_is_observed(self):
        self.task.queue = [{'scroll_to': 'Route guide', 'left': 6,
                            'bundle': 'fixture.browser', 'window': 'Catalog'}]
        with patch('companion.computer_task.validate_step') as validate:
            self.assertIsNone(self.task.queued_step(Mock(), self.snapshot,
                                                   [{'text': 'Route guide'}], {}, ''))
            validate.assert_not_called()
        self.assertEqual(self.task.queue, [])


if __name__ == '__main__':
    unittest.main()
