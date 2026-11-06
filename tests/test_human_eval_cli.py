"""Runner isolation and failure sequencing; native apps are never invoked here."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1]/'scripts'
spec = importlib.util.spec_from_file_location('human_eval_cli', SCRIPTS/'human_eval.py')
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


class ConsentIsolationTests(unittest.TestCase):
    def test_only_existing_local_consent_and_model_choices_are_copied(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {'A11Y_COMPANY_ID': 'fixture', 'A11Y_USER_ID': 'user'}):
            source, destination = Path(temporary)/'source', Path(temporary)/'destination'
            source.mkdir()
            (source/'ai.json').write_text(json.dumps(dict(model='text-fixture', vision_model='vision-fixture', provider='claude', private_extra='PRIVATE')))
            owner = json.dumps(['fixture', 'user'])
            with contextlib.closing(sqlite3.connect(source/'employee.sqlite3')) as db, db:
                db.execute('CREATE TABLE desktop_access (owner TEXT, data TEXT)')
                db.execute('CREATE TABLE messages (content TEXT)')
                db.execute('INSERT INTO messages VALUES (?)', ('PRIVATE MESSAGE',))
                db.execute('INSERT INTO desktop_access VALUES (?,?)', (owner, json.dumps(dict(remember=True, app_awareness=True, screen_vision=True,
                                                                                             approved=dict(cloud_ai=True, cloud_screen_vision=True)))))
            copied, config = cli.copy_existing_consent(source, destination)
            self.assertTrue(copied)
            self.assertEqual(config['provider'], 'ollama')
            self.assertNotIn('PRIVATE', (destination/'ai.json').read_text())
            with contextlib.closing(sqlite3.connect(destination/'employee.sqlite3')) as db:
                tables = db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                self.assertEqual(tables, [('desktop_access',)])
                saved = json.loads(db.execute('SELECT data FROM desktop_access').fetchone()[0])
                self.assertNotIn('cloud_ai', saved['approved'])
                self.assertNotIn('cloud_screen_vision', saved['approved'])

    def test_missing_consent_is_not_granted(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, destination = Path(temporary)/'source', Path(temporary)/'destination'
            source.mkdir()
            copied, _ = cli.copy_existing_consent(source, destination)
            self.assertFalse(copied)
            self.assertFalse((destination/'employee.sqlite3').exists())


class FakeServer:
    base_url = 'http://127.0.0.1:9999/test-nonce'
    facts = dict(delivery='quill-123', pickup='ridge-456', product='amber kettle 127', price='42')
    def __init__(self, seed): pass
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def event_count(self): return 0
    def events_since(self, offset): return []


class RunnerControlTests(unittest.TestCase):
    def run_main(self, folder, **patches):
        import human_eval_core as core
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(cli, 'prerequisites', return_value=dict(ready=True, reason='')))
        stack.enter_context(patch.object(cli, 'copy_existing_consent', return_value=(True, {})))
        stack.enter_context(patch.object(core, 'FixtureServer', FakeServer))
        stack.enter_context(patch.object(cli.urllib.request, 'urlopen', return_value=io.BytesIO(b'{"models": []}')))
        for name, value in patches.items():
            stack.enter_context(patch.object(cli, name, value))
        with contextlib.redirect_stdout(io.StringIO()):
            result = cli.main(['--runs', '1', '--only', 'browser_read', '--consent-from', folder, '--output', str(Path(folder)/'run')])
        return result, json.loads((Path(folder)/'run'/'summary.json').read_text())

    def setUp(self):
        self.path = patch.object(sys, 'path', [str(SCRIPTS)]+sys.path)
        self.path.start()
        self.addCleanup(self.path.stop)

    def test_list_touches_no_native_or_consent_state(self):
        with patch.object(cli, 'prerequisites', side_effect=AssertionError('must not probe')), \
                patch.object(cli, 'osa', side_effect=AssertionError('must not touch apps')), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(cli.main(['--list']), 0)
        self.assertIn('browser_catalog', output.getvalue())

    def test_blocked_preflight_opens_no_apps_or_server(self):
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(cli, 'prerequisites', return_value=dict(ready=False, reason='Accessibility unavailable')), \
                patch.object(cli.AppClient, 'launch', side_effect=AssertionError('no app')), \
                patch.object(cli.NativeFixture, 'setup', side_effect=AssertionError('no native setup')), \
                contextlib.redirect_stdout(io.StringIO()):
            out = Path(temporary)/'run'
            self.assertEqual(cli.main(['--runs', '1', '--output', str(out)]), 1)
            summary = json.loads((out/'summary.json').read_text())
        self.assertEqual(summary['counts']['blocked'], 14)
        self.assertEqual(summary['attempted'], 0)
        self.assertIsNone(summary['latency']['p50_seconds'])
        self.assertFalse(summary['all_workflows_passed'])

    def test_ownership_loss_prevents_stimulus_and_stops_rest_of_run(self):
        calls = []
        class Client:
            def __init__(self, *args): self.process = types.SimpleNamespace(pid=31415)
            def launch(self): pass
            def ask(self, *args): raise AssertionError('must not submit after ownership loss')
            def close(self): calls.append('app cleanup'); raise RuntimeError('cleanup fixture error')
        class Native:
            def __init__(self, *args): pass
            def setup(self, *args): pass
            def observe(self): return dict(owned=False)
            def close(self): calls.append('native cleanup')
        with tempfile.TemporaryDirectory() as temporary:
            result, summary = self.run_main(temporary, AppClient=Client, NativeFixture=Native)
        self.assertEqual(result, 1)
        self.assertEqual(summary['counts']['cancelled'], 1)
        self.assertFalse(summary['complete'])
        self.assertEqual(calls, ['app cleanup', 'native cleanup'])
        self.assertEqual(len(summary['cleanup_errors']), 1)

    def test_source_app_failure_is_not_mislabelled_as_fixture_setup(self):
        class Client:
            def __init__(self, *args): self.process = types.SimpleNamespace(pid=31415)
            def launch(self): pass
            def ask(self, *args): raise RuntimeError('source process stopped responding')
            def close(self): pass
        class Native:
            def __init__(self, *args): pass
            def setup(self, *args): pass
            def observe(self): return dict(owned=True, url=FakeServer.base_url+'/home')
            def close(self): pass
        with tempfile.TemporaryDirectory() as temporary:
            result, summary = self.run_main(temporary, AppClient=Client, NativeFixture=Native)
        self.assertEqual(result, 1)
        self.assertEqual(summary['counts']['fail'], 1)
        self.assertEqual(summary['counts']['setup_error'], 0)


if __name__ == '__main__':
    unittest.main()
