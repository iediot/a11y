"""computer_task.py is split by responsibility; every name stays importable and patchable there."""
import unittest
from unittest.mock import Mock, patch

from companion import computer_task, task_answers, task_completion, task_fallbacks, task_prompts


class SplitTests(unittest.TestCase):
    def test_moved_names_are_the_same_objects_in_computer_task(self):
        for module in (task_prompts, task_completion, task_answers, task_fallbacks):
            for name, value in vars(module).items():
                if name.startswith('__') or getattr(value, '__module__', module.__name__) != module.__name__:
                    continue
                if isinstance(value, type(computer_task)) or name in ('choices', 'permitted', 'variant'):
                    continue
                with self.subTest(module=module.__name__, name=name):
                    self.assertIs(getattr(computer_task, name), value)

    def test_the_task_keeps_every_moved_method(self):
        for name in ('completion_observation', 'still_missing', 'goal_met', 'screen_answer', 'report_only',
                     'menu_pick_step', 'settings_pane_step'):
            self.assertTrue(callable(getattr(computer_task.ComputerTask, name)), name)

    def test_leaf_modules_import_without_computer_task_first(self):
        import subprocess, sys
        for module in ('task_prompts', 'task_completion', 'task_answers', 'task_fallbacks'):
            result = subprocess.run([sys.executable, '-c', 'import companion.'+module], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_patching_validate_step_in_computer_task_reaches_the_menu_fallback(self):
        task = computer_task.ComputerTask('Show the sites I visited.', automatic=True, goal_mode='inspect')
        task.receipts = [dict(type='click_point', label='Back', status='submitted', effect='nothing changed on screen')]
        desktop = Mock()
        desktop.bridge.menu_items.return_value = ['History > Show All History']
        provider = Mock(complete_structured=Mock(return_value={'command': 'History > Show All History'}))
        snapshot = {'app': {'name': 'Safari', 'bundle_id': 'com.apple.Safari'}, 'window': {'title': 'Start'}}
        action = {'type': 'choose_menu', 'path': 'History > Show All History', 'label': 'History > Show All History'}
        with patch('companion.computer_task.validate_step', return_value={'action': action}) as validate:
            step = task.menu_pick_step(desktop, snapshot, provider, {})
        validate.assert_called_once()
        self.assertEqual(step['action'], action)


if __name__ == '__main__':
    unittest.main()
