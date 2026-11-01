import unittest
from companion.request_plan import preserve_desktop_goal


class TaskPlanStabilityTests(unittest.TestCase):
    def test_screen_actions_promoted_to_tasks_stay_tasks_on_revalidation(self):
        for operation in ('click_point', 'press_control', 'type_text', 'press_key', 'scroll'):
            for referent in ('companion', 'destination', 'other'):
                # Click/control always promote; other inputs promote with follow-up work.
                base = dict(operation=operation, app_name='Clock', target='Start', referent=referent,
                            remaining_request='then inspect the result')
                request = 'In Clock, begin a countdown lasting 600 seconds.'
                first = preserve_desktop_goal(base, request)
                self.assertEqual(first['operation'], 'computer_task')
                self.assertEqual(first['referent'], 'destination')
                for _ in range(3):
                    self.assertEqual(preserve_desktop_goal(first, request), first)

    def test_explicit_companion_movement_remains_movement(self):
        base = dict(operation='move_object', app_name='', target='beside the window', referent='companion')
        self.assertEqual(preserve_desktop_goal(base, 'Move beside the window.'), base)

    def test_pointing_referent_still_prevents_activation(self):
        base = dict(operation='click_point', app_name='', target='button', referent='visible_target')
        self.assertEqual(preserve_desktop_goal(base, 'Point out this button.')['operation'], 'highlight')
