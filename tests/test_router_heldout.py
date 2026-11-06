"""The held-out router set loads, stays out of training and prompts, and scores without a model."""
import ast
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
import router_heldout
from companion.request_plan import INTENT_SYSTEM, OPERATIONS


def normalized(text):
    return ' '.join(text.casefold().replace('’', "'").split()).strip(' .!?,"\'')


def literals(path):
    """Every string constant in a Python file, plus each quoted example inside one."""
    found = set()
    for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            found.add(normalized(node.value))
            found.update(normalized(part) for part in re.findall(r'"([^"]+)"', node.value))
    return found


def perfect(row):
    expect = row['expect']
    plan = {key: value for key, value in expect.items()}
    if isinstance(plan['operation'], list):
        plan['operation'] = plan['operation'][0]
    return plan


class HeldOutDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = router_heldout.load()

    def test_size_ids_and_schema(self):
        self.assertGreaterEqual(len(self.rows), 140)
        self.assertEqual(len({row['id'] for row in self.rows}), len(self.rows))
        for row in self.rows:
            operations = row['expect']['operation']
            for op in operations if isinstance(operations, list) else [operations]:
                self.assertIn(op, OPERATIONS, row['id'])

    def test_requests_are_unique_unless_history_differs(self):
        seen = {}
        for row in self.rows:
            key = (normalized(row['request']), tuple(row['recent_user_requests']), row['context'].get('front_app'))
            self.assertNotIn(key, seen, row['id'])
            seen[key] = row['id']

    def test_covers_the_known_confusions(self):
        categories = {row['category'] for row in self.rows}
        for name in ('dictionary_vs_move', 'sidebar_vs_highlight', 'another_one_followup',
                     'inspect_vs_change', 'named_app_vs_front_app'):
            self.assertIn(name, categories)
            self.assertGreaterEqual(sum(row['category'] == name for row in self.rows), 10, name)
        # The same words after a reminder and after an event route differently.
        followups = [row for row in self.rows if row['category'] == 'another_one_followup']
        by_request = {}
        for row in followups:
            by_request.setdefault(normalized(row['request']), set()).add(row['expect']['operation'])
        self.assertTrue(any(ops == {'add_reminder', 'add_event'} for ops in by_request.values()))
        modes = {row['expect'].get('goal_mode') for row in self.rows if row['category'] == 'inspect_vs_change'}
        self.assertLessEqual({'inspect', 'change', 'draft'}, modes)

    def test_never_in_a_prompt_test_or_training_source(self):
        sources = [*(ROOT / 'companion').glob('*.py'), *(ROOT / 'scripts').glob('router_*.py'),
                   *(ROOT / 'scripts').glob('live_*.py'), *(ROOT / 'scripts').glob('*eval*.py'),
                   *(path for path in (ROOT / 'tests').glob('*.py') if path.name != Path(__file__).name)]
        known = set().union(*(literals(path) for path in sources))
        known.update(normalized(part) for part in re.findall(r'"([^"]+)"', INTENT_SYSTEM))
        for row in self.rows:
            self.assertFalse(normalized(row['request']) in known, row['id'] + ': ' + row['request'])

    def test_not_in_generated_router_corpus(self):
        from router_data import build
        corpus = {normalized(row['request']) for row in build()}
        for row in self.rows:
            self.assertFalse(normalized(row['request']) in corpus, row['id'] + ': ' + row['request'])

    def test_every_request_reaches_the_router(self):
        """Code answers some requests before the model; those would not test the router."""
        from companion import service, mail_reader
        from companion.arithmetic import answer, clock_answer
        from companion.quick_open import bare_name, plan
        from companion.request_plan import simple_app_command
        for row in self.rows:
            request = row['request']
            moving = service.MOVE_SELF.search(request)
            caught = {
                'arithmetic': answer(request), 'clock': clock_answer(request),
                'never mind': service.NEVER_MIND.match(request),
                'move self': moving and service.geometric_destination(moving.group(0)),
                'bare verb': service.BARE_VERB.match(request), 'click': service.CLICK_REQUEST.match(request),
                'close tab': service.CLOSE_TAB.search(request), 'mail': mail_reader.position(request),
                'unread': mail_reader.UNREAD.search(request), 'conversation': service.ABOUT_CONVERSATION.search(request),
                'simple app': (simple_app_command(request) or {}).get('operation') not in (None, 'launch_app'), 'quick open': plan(request, []),
                'bare name': bare_name(request, []),
            }
            self.assertEqual([name for name, hit in caught.items() if hit], [], row['id'] + ': ' + request)


class HeldOutScoringTests(unittest.TestCase):
    def setUp(self):
        self.rows = router_heldout.load()

    def test_perfect_predictions_score_everything(self):
        report = router_heldout.score(self.rows, {row['id']: perfect(row) for row in self.rows})
        self.assertEqual(report['operation_correct'], len(self.rows))
        self.assertEqual(report['all_fields_correct'], len(self.rows))
        self.assertEqual(report['misses'], [])

    def test_wrong_operation_fails_its_fields_and_errors_count(self):
        rows = [row for row in self.rows if row['expect']['operation'] == 'add_reminder' and 'when' in row['expect']][:2]
        good, bad = rows
        predictions = {good['id']: dict(perfect(good), operation='add_event'), bad['id']: None}
        report = router_heldout.score(rows, predictions)
        self.assertEqual((report['operation_correct'], report['all_fields_correct'], report['errors']), (0, 0, 1))
        self.assertEqual(report['fields']['when']['correct'], 0)
        self.assertEqual(report['confusion']['add_reminder'], {'add_event': 1, '<error>': 1})
        self.assertEqual(report['misses'][0]['failed'], ['operation', 'when'])

    def test_opening_folded_into_a_task_counts_as_the_opening(self):
        row = {'id': 'x', 'category': 'c', 'request': 'r', 'recent_user_requests': [], 'context': {},
               'expect': {'operation': 'launch_app', 'app_name': 'Calculator'}}
        plan = {'operation': 'computer_task', 'app_name': '', 'target': 'r',
                'opening': {'operation': 'launch_app', 'app_name': 'Calculator', 'target': '', 'browser_mode': 'default'}}
        self.assertEqual(router_heldout.score_row(row, plan), {'operation': True, 'app_name': True})

    def test_lenient_only_on_case_vendor_and_url_scheme(self):
        ok = router_heldout.field_ok
        self.assertTrue(ok('app_name', 'Microsoft Teams', 'teams', 'computer_task'))
        self.assertTrue(ok('target', 'https://bbc.co.uk', 'https://www.bbc.co.uk/', 'browser_open'))
        self.assertTrue(ok('when', 'Saturday', 'saturday', 'weather'))
        self.assertTrue(ok('when', '2026-11-01T14:00', '2026-11-01T14:00:00', 'add_reminder'))
        self.assertFalse(ok('when', '2026-11-01T14:00', '2026-11-02T14:00:00', 'add_reminder'))
        self.assertFalse(ok('when', '2026-11-20T12:00', '12:00', 'add_reminder'))
        self.assertTrue(ok('referent', 'other', None, 'answer'))
        self.assertFalse(ok('target', 'Morning Routine', 'Morning', 'run_shortcut'))
        self.assertFalse(ok('acts_on_computer', False, None, 'answer'))
        self.assertTrue(ok('operation', ['click_point', 'computer_task'], 'computer_task', None))

    def test_run_uses_the_router_with_history_and_context_and_survives_errors(self):
        rows = self.rows[:3]
        calls = []

        class Provider:
            def interpret_request(self, request, recent, context):
                calls.append((request, recent, context))
                if len(calls) == 2:
                    raise RuntimeError('invalid plan')
                return perfect(next(row for row in rows if row['request'] == request))

        predictions, seconds = router_heldout.run(rows, Provider())
        self.assertEqual([call[0] for call in calls], [row['request'] for row in rows])
        self.assertEqual(calls[0][2], rows[0]['context'])
        self.assertIsNone(predictions[rows[1]['id']])
        self.assertEqual(len(seconds), 3)

    def test_cli_scores_saved_predictions_without_a_model(self):
        with tempfile.TemporaryDirectory() as folder:
            saved = Path(folder) / 'plans.jsonl'
            saved.write_text(''.join(json.dumps({'id': row['id'], 'plan': perfect(row)}) + '\n' for row in self.rows))
            output = Path(folder) / 'report.json'
            with mock.patch('sys.stdout'):
                report = router_heldout.main(['--predictions', str(saved), '--output', str(output),
                                              '--category', 'another_one_followup'])
            self.assertEqual(report['rows'], report['all_fields_correct'])
            self.assertTrue(report['rows'] >= 10)
            self.assertIsNone(report['model'])
            self.assertEqual(json.loads(output.read_text())['rows'], report['rows'])

    def test_bad_rows_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'bad.jsonl'
            path.write_text(json.dumps({'id': 'a', 'category': 'c', 'request': 'hi', 'recent_user_requests': [],
                                        'context': {}, 'expect': {'operation': 'dance'}}) + '\n')
            with self.assertRaises(ValueError):
                router_heldout.load(path)


if __name__ == '__main__':
    unittest.main()
