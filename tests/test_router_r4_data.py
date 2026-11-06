"""The R4 router rows are valid plans, stay away from the held-out and frozen test requests,
and render prompts exactly as Planner.interpret_request sends them."""
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
import router_r4_data as r4
from companion.request_plan import INTENT_SCHEMA, INTENT_SYSTEM, OPERATIONS


class R4RowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = r4.new_rows()

    def test_rows_are_schema_plans(self):
        self.assertGreaterEqual(len(self.rows), 200)
        props = INTENT_SCHEMA['properties']
        for item in self.rows:
            label = r4.finish(item)
            self.assertLessEqual(set(INTENT_SCHEMA['required']), set(label), item['request'])
            self.assertIn(label['operation'], OPERATIONS)
            for key in ('browser_mode', 'goal_mode', 'referent'):
                self.assertIn(label[key], props[key]['enum'], item['request'])
            self.assertIs(type(label['acts_on_computer']), bool)
            if label['operation'] == 'answer':
                self.assertTrue(label['reply'] and not label['acts_on_computer'], item['request'])
            else:
                self.assertEqual(label['reply'], '', item['request'])
            if label['when'] and label['operation'] in ('add_event', 'add_reminder'):
                self.assertRegex(label['when'], r'^\d{4}-\d\d-\d\dT\d\d:\d\d$')

    def test_never_near_held_out_or_frozen_test_requests(self):
        held, frozen = r4.protected_requests()
        self.assertGreaterEqual(len(held), 140)
        for item in self.rows:
            self.assertIsNone(r4.too_close(item['request'], held), item['request'])
            self.assertIsNone(r4.too_close(item['request'], frozen), item['request'])

    def test_too_close_catches_wrappers_and_near_copies(self):
        self.assertTrue(r4.too_close('Could you open the Bluetooth settings, please?', ['open the bluetooth settings']))
        self.assertTrue(r4.too_close('Turn on Night Shift tonight.', ['Turn Night Shift on tonight.']))
        self.assertIsNone(r4.too_close('Translate left into Spanish.', ['Open the Downloads folder.']))

    def test_when_is_worked_out_from_now(self):
        self.assertEqual(r4.when('mon', 'thursday', 16), '2026-11-05T16:00')
        self.assertEqual(r4.when('fri', 'tomorrow', 9), '2026-11-07T09:00')
        self.assertEqual(r4.when('thu', 'monday', 10), '2026-11-16T10:00')
        with self.assertRaises(ValueError):
            r4.when('fri', 'today', 9)

    def test_prompt_matches_the_planner_call(self):
        context = {'user_profile': {}, 'preferences': dict(r4.PREFERENCES), 'now': r4.NOW['mon'], 'front_app': 'Finder'}
        payload = {'recent_user_requests': ['a'], 'current_request': 'b'}
        self.assertEqual(r4.user_message('b', ['a'], context), json.dumps(dict(context, **payload)))
        self.assertEqual(r4.user_message('b', [], {}), json.dumps({'recent_user_requests': [], 'current_request': 'b'}))

    def test_polite_forms_are_grammatical(self):
        self.assertEqual(r4.polite('open TextEdit'), 'Could you open TextEdit, please?')
        self.assertEqual(r4.polite('what is playing'), 'Hey Ally, what is playing?')
        self.assertEqual(r4.polite('thanks for your help', 'answer'), 'Hey Ally, thanks for your help.')
        self.assertEqual(r4.polite('tell me the current track'), 'Could you tell me the current track?')

    @unittest.skipUnless((r4.R3 / 'train.jsonl').is_file(), 'local r3 snapshot not present')
    def test_build_uses_current_system_prompt_and_canonical_answers(self):
        splits, manifest, report = r4.build()
        self.assertEqual(report['new_rows'], len(self.rows))
        for rows in splits.values():
            for row in rows:
                system, user, assistant = row['messages']
                if 'current_request' in json.loads(user['content']):
                    self.assertEqual(system['content'], INTENT_SYSTEM)
                    keys = list(json.loads(assistant['content']))
                    self.assertEqual(keys[:len(INTENT_SCHEMA['required'])], sorted(INTENT_SCHEMA['required']))
        replayed = [m['request'] for m in manifest if m.get('kind') == 'replay']
        self.assertFalse(any(text.lower().startswith(('could you what', 'would you what', 'pls ')) for text in replayed))


if __name__ == '__main__':
    unittest.main()
