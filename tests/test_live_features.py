"""Offline contracts only: never start an application, model, or live suite."""
import ast
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

SCRIPTS = Path(__file__).resolve().parents[1]/'scripts'
sys.path.insert(0, str(SCRIPTS))
import live_features as features


class FeaturesSuite(unittest.TestCase):
    def setUp(self):
        self.window = Mock()
        with patch('live_features.osa', side_effect=AssertionError('native call during build')), \
             patch('live_features.subprocess.run', side_effect=AssertionError('process during build')), \
             patch('live_features.le.activate', side_effect=AssertionError('activation during build')), \
             patch('live_features.tempfile.mkdtemp', side_effect=AssertionError('fixture during build')):
            self.cases = features.build(self.window)
        self.assertEqual(self.window.mock_calls, [])

    def test_shape_and_forbidden_apps(self):
        names, requests = [], []
        forbidden = re.compile(r'\b(mail|messages|photos|contacts|teams|whatsapp|facetime|youtube)\b|/Applications/A11y\.app', re.I)
        for case in self.cases:
            self.assertTrue(callable(case['setup']))
            self.assertTrue(case['scoped_documents'])
            self.assertTrue(case['cleanup'], case['name'])
            self.assertTrue(all(callable(f) for f in case['cleanup']))
            for task in case['turns']:
                names.append(task.family)
                requests.append(task.request.casefold())
                self.assertNotRegex(task.request+' '+task.wanted_app, forbidden)
                self.assertTrue(task.checks)
                self.assertTrue(all(callable(f) for _, f in task.checks))
                if task.wanted_app in ('Notes', 'Reminders', 'TextEdit'):
                    self.assertRegex(task.request, r'A11y test \d+')
        self.assertGreaterEqual(len(names), 45)
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(len(requests), len(set(requests)))
        self.assertEqual(len(self.cases), len({c['name'] for c in self.cases}))

    def test_held_out_against_every_other_suite(self):
        literals, templates = set(), []
        for path in SCRIPTS.glob('live_*.py'):
            if path.stem == 'live_features':
                continue
            for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    literals.add(' '.join(node.value.casefold().split()))
                if isinstance(node, ast.JoinedStr):
                    parts = [part.value for part in node.values if isinstance(part, ast.Constant)]
                    if sum(map(len, parts)) < 25:
                        continue
                    expression = ''.join(re.escape(' '.join(part.value.casefold().split())) if isinstance(part, ast.Constant) else '.*' for part in node.values)
                    templates.append(re.compile(expression.replace(r'\ ', r'\s*')))
        for case in self.cases:
            for task in case['turns']:
                request = ' '.join(task.request.casefold().split())
                self.assertNotIn(request, literals)
                for template in templates:
                    self.assertIsNone(template.fullmatch(request), task.request)

    def test_fixture_cleanup_even_when_native_close_fails(self):
        for case in self.cases:
            if not (case['name'].startswith('fixture_') or case['name'] == 'retry_after_failure'):
                continue
            with self.subTest(case=case['name']), tempfile.TemporaryDirectory() as parent:
                root = Path(parent)/'fixture'
                root.mkdir()
                with patch('live_features.tempfile.mkdtemp', return_value=str(root)), \
                     patch('live_features.osa', side_effect=RuntimeError('native unavailable')):
                    with self.assertRaises(RuntimeError):
                        case['setup'](Mock())
                    try:
                        case['cleanup'][0]()
                    except RuntimeError:
                        pass
                self.assertFalse(root.exists())

    def test_every_case_uses_finally_cleanup_runner(self):
        from live_conversation import run
        for case in self.cases:
            callbacks = [Mock(side_effect=RuntimeError('close failed')), Mock()]
            copy = dict(case, setup=Mock(side_effect=RuntimeError('setup failed')), cleanup=callbacks)
            app = Mock()
            with tempfile.TemporaryDirectory() as folder, \
                 patch('live_conversation.le.app_names', return_value=set()), \
                 patch('live_conversation.le.running', return_value=False), \
                 patch('live_conversation.osa', side_effect=AssertionError('native call')), \
                 patch('live_conversation.le.trash_test_documents') as sweep:
                run(app, self.window, Path(folder), [copy])
            for callback in callbacks:
                callback.assert_called_once_with()
            sweep.assert_not_called()

    def test_no_denial_or_unperformed_promise(self):
        for text in ("I can't use the computer", 'I cannot control your computer', "I'm doing that now", "I'll open Finder", 'I could not finish that'):
            with patch('live_features.answer', return_value=text):
                self.assertFalse(features.honest({}, []), text)
        with patch('live_features.answer', return_value='The folder is open.'):
            self.assertTrue(features.honest({}, []))

    def test_drag_never_confirms_and_records_owner_requirement(self):
        case = next(c for c in self.cases if c['name'] == 'fixture_drag')
        runner = Mock()
        runner.call.return_value = {'screen': dict(x=0, y=0, width=1440, height=900)}
        with tempfile.TemporaryDirectory() as folder, \
             patch('live_features.tempfile.mkdtemp', return_value=folder), \
             patch('live_features.osa'), patch('live_features.le.activate'):
            case['setup'](runner)
            state = {'pending_action': {'type': 'move_file', 'label': 'fixture'}}
            self.assertFalse(case['turns'][0].checks[0][1](state, []))
            self.assertTrue(state['_observed']['confirmation_appeared'])
            self.assertIn('allowlist', state['_observed']['needs_owner'])
            self.assertTrue(all(c.args[0]['cmd'] == 'state' for c in runner.call.call_args_list))
            case['cleanup'][0]()

    def test_cursor_requires_multiple_parked_steps_and_reformation(self):
        case = next(c for c in self.cases if c['name'] == 'fixture_cursor')
        check = case['turns'][0].checks[0][1]
        sample = dict(cursor_phase='parked', cursor_visible=True, cursor_size=18, system_pointer_size=18)
        state = {'ui': {'cursor_visible': False, 'mascot_visible': True}, '_ui_samples': [dict(sample, steps=1)],
                 'task': {'receipts': [{'type': 'click_point'} for _ in range(3)]}}
        self.assertFalse(check(state, []))
        state['_ui_samples'].append(dict(sample, steps=2))
        self.assertTrue(check(state, []))
        state['_ui_samples'][0]['cursor_visible'] = False
        self.assertFalse(check(state, []))

    def test_runner_records_skips_and_owner_requirements(self):
        import json
        from live_conversation import run
        for field, status in [('skipped', 'skipped'), ('needs_owner', 'needs_owner')]:
            def check(s,e,field=field):
                features.observed(s, **{field: 'recorded reason'})
                return False
            task = features.Task('status', 'offline fixture', [('real state', check)])
            case = dict(name='status', turns=[task], cleanup=[], scoped_documents=True)
            app = Mock()
            app.run.return_value = ({}, [], False)
            with tempfile.TemporaryDirectory() as folder, \
                 patch('live_conversation.le.app_names', return_value=set()), \
                 patch('live_conversation.le.running', return_value=False), \
                 patch('live_conversation.osa', side_effect=AssertionError('native call')):
                run(app, self.window, Path(folder), [case])
                record = json.loads((Path(folder)/'runs.jsonl').read_text())
            self.assertEqual(record['status'], status)
            self.assertFalse(record['passed'])
            if field == 'skipped':
                self.assertEqual(record['skip_reason'], 'recorded reason')

    def test_eval_ui_snapshot_is_read_only(self):
        from companion.eval_control import EvalControl
        from types import SimpleNamespace
        control = EvalControl.__new__(EvalControl)
        mascot = Mock()
        mascot.character.mapToGlobal.return_value = SimpleNamespace(x=lambda: 123, y=lambda: 456)
        mascot.input.isVisible.return_value = True
        mascot.input.hasFocus.return_value = True
        mascot.isVisible.return_value = True
        mascot.isMinimized.return_value = False
        mascot.reply_shown = False
        cursor = Mock(phase='parked')
        cursor.isVisible.return_value = True
        control.window = SimpleNamespace(mascot=mascot, cursor_flight=cursor)
        with patch.dict(sys.modules, {'companion.cursor_flight': SimpleNamespace(CURSOR=36, system_cursor_scale=lambda: 2)}):
            result = control.ui_state()
        self.assertEqual(result['face'], [123, 456])
        self.assertTrue(result['input_visible'] and result['input_focused'])
        self.assertEqual(result['cursor_phase'], 'parked')
        self.assertEqual(result['cursor_size'], result['system_pointer_size'])
        mascot.input.setFocus.assert_not_called()
        mascot.show.assert_not_called()
        cursor.show.assert_not_called()

    def test_wait_keeps_temporal_ui_samples(self):
        runner = Mock()
        runner.call.side_effect = [
            dict(now=1, events=[], idle=False, quiet_for=0, task={'steps': 1}, ui={'cursor_visible': True}),
            dict(now=2, events=[], idle=True, quiet_for=2, task={'steps': 2}, ui={'cursor_visible': False})]
        with patch('live_eval.time.sleep'):
            state, events, timeout = features.le.App.wait(runner, 10, 0)
        self.assertFalse(timeout)
        self.assertEqual([x['steps'] for x in state['_ui_samples']], [1, 2])
        self.assertEqual([x['cursor_visible'] for x in state['_ui_samples']], [True, False])

    def test_denial_in_an_earlier_utterance_is_not_hidden_by_final_reply(self):
        events = [dict(kind='say', data={'message': "I can't use the computer"}),
                  dict(kind='say', data={'message': 'The folder is open.'})]
        self.assertFalse(features.honest({}, events))

    def test_spaces_single_space_skips_without_keys(self):
        import plistlib
        case = next(c for c in self.cases if c['name'] == 'companion_spaces')
        runner = Mock()
        runner.call.return_value = {'ui': {'pid': 123}}
        case['setup'](runner)
        data = {'SpacesDisplayConfiguration': {'Management Data': {'Monitors': [{'Spaces': [{}]}]}}}
        with patch('live_features.subprocess.run', return_value=Mock(stdout=plistlib.dumps(data))), patch('live_features.osa') as native:
            state = {}
            self.assertFalse(case['turns'][0].checks[0][1](state, []))
        self.assertIn('only one Space', state['_observed']['skipped'])
        native.assert_not_called()


if __name__ == '__main__':
    unittest.main()
