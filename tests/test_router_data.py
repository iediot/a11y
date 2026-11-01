import json
from pathlib import Path
import sys
import tempfile
import unittest
from collections import defaultdict
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import router_data as data
from companion.request_plan import INTENT_SCHEMA, OPERATIONS, validate_plan

class RouterDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = data.build()
    def test_labels_validate(self):
        self.assertGreater(len(self.rows), 1400)
        for row in self.rows:
            validate_plan(row['plan'])
            if row['kind'] == 'intent':
                label = json.loads(row['messages'][-1]['content'])
                self.assertTrue(set(INTENT_SCHEMA['required']) <= set(label))
        self.assertEqual({r['plan']['operation'] for r in self.rows}, set(OPERATIONS))
    def test_no_harder_leak(self):
        blocked = data.harder_strings()
        for row in self.rows:
            if row['split'] != 'test':
                self.assertFalse(data.held_out_wording(row['request']))
    def test_disjoint_families_and_prompts(self):
        families, prompts = defaultdict(set), defaultdict(set)
        for r in self.rows:
            families[r['family']].add(r['split'])
            prompts[json.dumps(r['messages'][:-1], sort_keys=True)].add(r['split'])
        self.assertTrue(all(len(v)==1 for v in families.values()))
        self.assertTrue(all(len(v)==1 for v in prompts.values()))
    def test_real_prompt_builders(self):
        p = data.plan('read_mail', '2')
        msgs = data.messages('read my second last email', p, ['read my last email'], {'front_app':'Mail'})
        from companion.request_plan import INTENT_SYSTEM
        self.assertEqual(msgs[0]['content'], INTENT_SYSTEM)
        self.assertEqual(json.loads(msgs[1]['content']), {'front_app':'Mail', 'recent_user_requests':['read my last email'], 'current_request':'read my second last email'})
    def test_reproducible_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data.generate(root)
            first = (root/'train.jsonl').read_bytes()
            data.generate(root)
            self.assertEqual(first, (root/'train.jsonl').read_bytes())
            for split in ('train','valid','test'):
                for line in (root/f'{split}.jsonl').read_text(encoding='utf-8').splitlines():
                    self.assertEqual(set(json.loads(line)), {'messages'})


class EvaluationTests(unittest.TestCase):
    def test_scoring_offline(self):
        from scripts.router_eval import evaluate
        label = data.plan('read_mail', '2')
        row = {'messages': data.messages('read my second last email', label)}
        report = evaluate([row], data.Capture(label))
        self.assertEqual(report['operation_accuracy'], 1)
        self.assertEqual(report['outputs'][0]['gold'], label)
        self.assertEqual(report['outputs'][0]['prediction'], label)
        self.assertEqual(report['field_accuracy']['target']['accuracy'], 1)
        self.assertEqual(report['confusion'], {'read_mail': {'read_mail': 1}})
        report = evaluate([row], data.Capture(data.plan('answer')))
        self.assertEqual(report['operation_accuracy'], 0)
    def test_generation_cannot_infer(self):
        from unittest.mock import patch
        with patch('companion.ollama.OllamaProvider.complete', side_effect=AssertionError('inference forbidden')), patch('subprocess.run', side_effect=AssertionError('apps forbidden')):
            rows = data.build()
        self.assertEqual({r['plan']['operation'] for r in rows if r['split']=='train'}, set(OPERATIONS))



class AuditedLabelsTests(unittest.TestCase):
    def test_referents_and_question_labels(self):
        rows = {r['request']:r for r in data.build() if r['kind']=='intent'}
        for text in ('find the Bluetooth settings', 'show me Battery in System Settings',
                     'where can I see the sites I visited? open it', 'open https://example.org/4'):
            self.assertEqual(rows[text]['plan']['referent'], 'destination')
        self.assertEqual(rows['click the toolbar']['plan']['referent'], 'other')
        self.assertEqual(rows['highlight the Preview close button']['plan']['referent'], 'visible_target')
        self.assertEqual(rows['move yourself beside the Preview window']['plan']['referent'], 'companion')
        self.assertFalse(rows['what do I have to do today']['plan']['acts_on_computer'])
        self.assertFalse(rows['what Mac model is this']['plan']['acts_on_computer'])
        self.assertTrue(rows['list my reminders']['plan']['acts_on_computer'])

    def test_equivalence_preserves_meaning(self):
        from scripts.router_scoring import equivalent
        gold = data.plan('browser_search', 'cats')
        self.assertTrue(equivalent('app_name', gold, dict(gold, app_name='Safari')))
        self.assertFalse(equivalent('app_name', gold, dict(gold, app_name='A11y')))
        gold = data.plan('read_mail', '2')
        self.assertTrue(equivalent('app_name', gold, dict(gold, app_name='Mail')))
        self.assertFalse(equivalent('app_name', gold, dict(gold, app_name='A11y')))
        gold = data.plan('highlight', 'Preview close button', 'Preview')
        self.assertTrue(equivalent('target', gold, dict(gold, target='the close button')))
        self.assertTrue(equivalent('target', gold, dict(gold, target='Preview close button')))
        gold = data.plan('type_text', 'hello')
        self.assertFalse(equivalent('target', gold, dict(gold, target='Hello')))
        gold = data.plan('browser_open', 'https://www.youtube.com/results?search_query=lo-fi')
        self.assertFalse(equivalent('target', gold, dict(gold, target='https://www.youtube.com')))
        gold = data.plan('recent_files', 'downloads')
        self.assertFalse(equivalent('target', gold, dict(gold, target='recent_files')))


if __name__ == '__main__':
    unittest.main()
