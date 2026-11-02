"""Correctness bugs found reviewing commit bcc8483; each test failed before its fix."""
import unittest

from companion.provider import ProviderError


class ReturnKeyTests(unittest.TestCase):
    def test_pyautogui_return_is_enter(self):
        # pyautogui names Enter "return"; the alias was spelled "return_" and never matched.
        from companion.gui_models import key_action, parsed_step
        self.assertEqual(key_action(['return']), {'type': 'press_key', 'key': 'enter'})
        self.assertEqual(key_action(['Return']), {'type': 'press_key', 'key': 'enter'})
        step = parsed_step('pyautogui', "Action:\n```python\npyautogui.press('return')\n```", 100, 100)
        self.assertEqual(step['action'], {'type': 'press_key', 'key': 'enter'})
        with self.assertRaises(ProviderError):
            key_action(['return_'])


if __name__ == '__main__':
    unittest.main()
