"""Suite construction/validators only; never launch native apps or a model."""
import ast
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

SCRIPTS = Path(__file__).resolve().parents[1]/'scripts'
sys.path.insert(0, str(SCRIPTS))
import live_harder as harder


class HarderSuite(unittest.TestCase):
    def setUp(self):
        self.window = Mock()
        with patch('live_harder.osa', side_effect=AssertionError('native call during build')), \
             patch('live_harder.subprocess.run', side_effect=AssertionError('process during build')), \
             patch('live_harder.le.activate', side_effect=AssertionError('activation during build')), \
             patch('live_harder.tempfile.mkdtemp', side_effect=AssertionError('fixture during build')):
            self.cases = harder.build(self.window)
        self.window.assert_not_called()
        self.assertEqual(self.window.mock_calls, [])

    def test_shape_unique_names_and_safety(self):
        names, requests = [], []
        forbidden = r'\b(?:mail|messages|photos|contacts|teams|whatsapp|facetime|youtube|slack|discord|telegram|signal)\b|/Applications/A11y\.app'
        for case in self.cases:
            self.assertTrue(callable(case['setup']))
            self.assertTrue(case['cleanup'])
            self.assertTrue(all(callable(fn) for fn in case['cleanup']))
            self.assertTrue(case['extends'])
            self.assertTrue(case['scoped_documents'])
            for task in case['turns']:
                names.append(task.family)
                requests.append(task.request.casefold())
                self.assertTrue(task.checks)
                self.assertTrue(all(callable(fn) for _,fn in task.checks))
                self.assertNotRegex(task.request, re.compile(forbidden, re.I))
                self.assertNotRegex(task.wanted_app, re.compile(forbidden, re.I))
                if task.wanted_app in ('Notes', 'Reminders', 'TextEdit'):
                    self.assertRegex(task.request, r'A11y test \d+')
        self.assertEqual(len(names), 30)
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(len(requests), len(set(requests)))
        self.assertEqual(len(self.cases), len({c['name'] for c in self.cases}))

    def test_no_existing_wording(self):
        # Read syntax only: older suite builders perform native setup/discovery.
        existing = set()
        templates = []
        for name in ('live_eval', 'live_tasks', 'live_hard', 'live_heldout', 'live_broad', 'live_conversation'):
            tree = ast.parse((SCRIPTS/(name+'.py')).read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    existing.add(' '.join(node.value.casefold().split()))
                elif isinstance(node, ast.JoinedStr):
                    literals = [part.value for part in node.values if isinstance(part, ast.Constant) and isinstance(part.value, str)]
                    # Ignore generic formatting fragments; compare complete prompts.
                    if sum(map(len, literals)) >= 25:
                        parts = [re.escape(' '.join(part.value.casefold().split())) if isinstance(part, ast.Constant) else '.*' for part in node.values]
                        templates.append(re.compile(''.join(parts).replace(r'\ ', r'\s*')))
        for case in self.cases:
            for task in case['turns']:
                normalized = ' '.join(task.request.casefold().split())
                self.assertNotIn(normalized, existing)
                for template in templates:
                    self.assertIsNone(template.fullmatch(normalized), task.request)

    def test_url_exact_host_and_path(self):
        check = harder.at_url('python.org', '/downloads')
        for url, valid in [('https://www.python.org/downloads/', True),
                           ('https://python.org/downloads', True),
                           ('https://python.org/downloads/other', False),
                           ('https://python.org.evil.test/downloads', False)]:
            with patch('live_harder.le.safari_front_tab', return_value=(url,'Downloads')):
                self.assertEqual(check({}, []), valid)

    def test_note_requires_exact_body_and_single_title(self):
        for native, valid in [(['1','<div>A11y test 123</div><div>Pack pears.</div>'], True),
                              (['1','<div>A11y test 123</div><div>Pack pears. Extra</div>'], False),
                              (['0', ''], False), (['2', '<div>duplicate notes</div>'], False)]:
            with patch('live_harder.osa', side_effect=native):
                self.assertEqual(harder.exact_note('A11y test 123', 'Pack pears.'), valid)

    def test_preview_fixture_is_marked_and_cleaned_after_setup_failure(self):
        case = next(c for c in self.cases if c['name'] == 'preview_sidebar')
        created = []
        def fail_open(args, **kwargs):
            created.append(Path(args[-1]))
            raise RuntimeError('fixture open failed')
        with patch('live_harder.subprocess.run', side_effect=fail_open):
            with self.assertRaisesRegex(RuntimeError, 'fixture open failed'):
                case['setup'](None)
        path = created[0]
        try:
            self.assertRegex(path.read_bytes(), rb'A11y test \d+')
            self.assertIn(b'/Count 2', path.read_bytes())
        finally:
            with patch('live_harder.osa'):
                case['cleanup'][0]()
        self.assertFalse(path.parent.exists())

    def test_reply_alone_never_proves_web_task(self):
        case = next(c for c in self.cases if c['name'] == 'web_screen_answer')
        with patch('live_harder.le.safari_front_tab', return_value=('https://example.net/', 'Example Domain')), \
             patch('live_harder.answer', return_value='Example Domain'):
            self.assertFalse(case['turns'][0].checks[0][1]({}, []))

    def test_runner_always_cleans_and_never_sweeps_scoped_documents(self):
        from live_conversation import run
        app = Mock()
        app.run.return_value = ({}, [], False)
        callbacks = [Mock(side_effect=RuntimeError('first cleanup failed')), Mock()]
        task = harder.Task('fixture', 'A11y test 123', [('real state', lambda s,e: True)], wanted_app='TextEdit')
        case = dict(name='fixture', turns=[task], cleanup=callbacks, scoped_documents=True,
                    setup=Mock(side_effect=RuntimeError('setup failed')))
        with tempfile.TemporaryDirectory() as folder, \
             patch('live_conversation.le.app_names', return_value=set()), \
             patch('live_conversation.le.running', return_value=False), \
             patch('live_conversation.osa', side_effect=AssertionError('native call')), \
             patch('live_conversation.le.trash_test_documents') as trash:
            run(app, self.window, Path(folder), [case])
        callbacks[0].assert_called_once_with()
        callbacks[1].assert_called_once_with()
        trash.assert_not_called()
        app.quit.assert_called()

    @unittest.skipUnless(sys.platform == 'darwin', 'needs macOS: the TextEdit fixture setup asks System Events through osascript')
    def test_text_check_uses_document_content_not_proposal(self):
        case = next(c for c in self.cases if c['name'] == 'textedit_lines')
        with patch('live_harder.tempfile.mkdtemp', return_value='/tmp/a11y-unit-fixture'), \
             patch.object(Path, 'write_text'), patch('live_harder.osa'), patch('live_harder.le.activate'):
            case['setup'](None)
        with patch('live_harder.osa', side_effect=['/tmp/a11y-unit-fixture/other.txt', 'A11y test 271828\nSaturday picnic\nBring pears and a blanket.']):
            self.assertFalse(case['turns'][0].checks[0][1]({}, []))
        with patch('live_harder.osa') as native, patch('live_harder.shutil.rmtree') as remove:
            case['cleanup'][0]()
            self.assertIn('close d saving no', native.call_args.args[0])
            self.assertTrue(native.call_args.args[1].startswith('/tmp/a11y-unit-fixture/A11y test '))
            remove.assert_called_once_with(Path('/tmp/a11y-unit-fixture'))


if __name__ == '__main__':
    unittest.main()
