"""Negative controls for screen-free conversation oracles; no model/app calls."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('conversation_eval', Path(__file__).resolve().parents[1]/'scripts'/'conversation_eval.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def reply(message, **values):
    return dict(message=message, desktop_action=None, target=None, **values)


class ConversationOracleTests(unittest.TestCase):
    def test_correct_words_with_an_action_cannot_pass_conversation(self):
        value = reply('ready')
        value['desktop_action'] = {'type': 'start_task'}
        self.assertFalse(module.score(value, 'exact', 'ready'))

    def test_identity_and_capability_reject_denial_or_base_model_persona(self):
        self.assertFalse(module.score(reply('I am Qwen.'), 'identity', ''))
        self.assertFalse(module.score(reply('My name is not Ally.'), 'identity', ''))
        self.assertFalse(module.score(reply('My name is Ally? No, I am Qwen.'), 'identity', ''))
        self.assertFalse(module.score(reply('My name is Ally. My name is Qwen.'), 'identity', ''))
        self.assertFalse(module.score(reply('I cannot open apps; I only chat.'), 'capability', ''))
        self.assertFalse(module.score(reply('Yes, you can open apps.'), 'capability', ''))
        self.assertFalse(module.score(reply("I can open apps. I'll open one now."), 'capability', ''))
        self.assertTrue(module.score(reply('I am Ally.'), 'identity', ''))
        self.assertTrue(module.score(reply('Yes, I can open apps.'), 'capability', ''))

    def test_old_or_wrong_memory_fact_fails(self):
        self.assertFalse(module.score(reply('amber-6741'), 'contains', 'amber-6742'))
        self.assertFalse(module.score(reply('It is not amber-6742.'), 'contains', 'amber-6742'))
        self.assertFalse(module.score(reply('Your code is amber-67420.'), 'contains', 'amber-6742'))
        self.assertFalse(module.score(reply("I don't remember whether amber-6742 is your code."), 'contains', 'amber-6742'))
        self.assertTrue(module.score(reply('Your code is amber-6742.'), 'contains', 'amber-6742'))
        self.assertNotEqual(module.scenarios(1)[-1], module.scenarios(2)[-1])

    def test_empty_or_partly_failed_run_cannot_be_successful(self):
        self.assertFalse(module.summarize([], planned_turns=1, completed=True)['passed'])
        result = module.summarize([dict(status='pass', seconds=1, model_calls=1),
                                   dict(status='fail', seconds=2, model_calls=1)], planned_turns=2, completed=True)
        self.assertFalse(result['passed'])
        self.assertEqual(result['statuses']['fail'], 1)

    def test_interrupted_passing_prefix_cannot_report_a_complete_success(self):
        prefix = [dict(status='pass', seconds=1, model_calls=1)]
        result = module.summarize(prefix, planned_turns=18, completed=False)
        self.assertFalse(result['passed'])
        self.assertEqual(result['not_attempted'], 17)
        self.assertFalse(module.summarize(prefix, planned_turns=18, completed=True)['passed'])


if __name__ == '__main__':
    unittest.main()
