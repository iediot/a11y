"""Legacy evaluation must reject fake success and private setup without native I/O."""
from contextlib import redirect_stderr, redirect_stdout
from decimal import Decimal
import importlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, mock_open, patch

SCRIPTS = Path(__file__).resolve().parents[1]/'scripts'
sys.path.insert(0, str(SCRIPTS))
import live_broad
import live_eval as le
import live_tasks


def reply(message):
    return [{'kind': 'say', 'data': {'message': message}}]


class LegacyEntrySafetyTests(unittest.TestCase):
    def test_suite_selection_is_required_before_any_native_startup(self):
        with patch.object(sys, 'argv', ['live_eval.py']), patch.object(le.subprocess, 'Popen') as start, redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as stopped:
                le.main()
        self.assertEqual(stopped.exception.code, 2)
        start.assert_not_called()

    def test_private_capable_suites_are_rejected_before_any_native_startup(self):
        for suite in ('requests', 'navigation', 'all', 'v2', 'v3', 'heldout'):
            with self.subTest(suite=suite), patch.object(sys, 'argv', ['live_eval.py', '--suite', suite]), \
                 patch.object(le.subprocess, 'Popen') as start, patch.object(le, 'App') as app, redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as stopped:
                    le.main()
                self.assertEqual(stopped.exception.code, 2)
                start.assert_not_called()
                app.assert_not_called()

    def test_private_literal_and_argv_targets_never_reach_osascript(self):
        for target in ('Mail', 'Photos', 'Contacts', 'Microsoft Teams', 'WhatsApp', 'FaceTime'):
            with self.subTest(target=target), patch.object(le.subprocess, 'run') as run:
                with self.assertRaisesRegex(RuntimeError, 'Private applications'):
                    le.osa('tell application "'+target+'" to activate')
                with self.assertRaisesRegex(RuntimeError, 'Private applications'):
                    le.native_command('osascript', '-e', 'tell application (item 1 of argv) to activate', target)
                run.assert_not_called()
        with self.assertRaisesRegex(RuntimeError, 'Private applications'):
            le.native_command('osascript', '-e', 'tell application id "COM.APPLE.MAIL" to activate')

    def test_private_word_as_fixture_data_is_not_a_private_target(self):
        script = 'tell application (item 1 of argv) to make new note with properties {name:item 2 of argv}'
        command = le.native_command('osascript', '-e', script, 'Notes', 'Mail')
        self.assertEqual(command[-2:], ['Notes', 'Mail'])
        result = subprocess.CompletedProcess([], 0, '42\n', '')
        with patch.object(le.subprocess, 'run', return_value=result):
            self.assertEqual(le.osa('return 42'), '42')

    def test_source_app_uses_supplied_data_without_granting_access(self):
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder)/'isolated'
            app = le.App(data, Path(folder)/'app.log')
            process = Mock()
            process.poll.return_value = None
            ready = {'ready': {'app_awareness': True, 'screen_vision': True}, 'idle': True}
            with patch.dict(os.environ, {'A11Y_DATA_DIR': '/wrong/inherited/directory'}), \
                 patch.object(app, 'check_build'), patch.object(app, 'call', return_value=ready) as call, \
                 patch.object(le.subprocess, 'Popen', return_value=process) as start, patch('builtins.open', mock_open()):
                app.launch()
            self.assertEqual(start.call_args.kwargs['env']['A11Y_DATA_DIR'], str(data.resolve()))
            self.assertEqual(call.call_args.args, ({'cmd': 'state', 'since': 10**9},))
            self.assertFalse(data.exists(), 'The launcher must not create consent/settings on the caller\'s behalf.')
            app.socket.parent.rmdir()


class LegacyOutcomeTests(unittest.TestCase):
    def test_quoted_typing_request_is_not_a_document_edit(self):
        case = next(case for case in live_broad.build(None) if case['name'] == 'textedit_multiline')
        check = case['turns'][0].checks[0][1]
        expected = case['turns'][0].request.split('exactly: ', 1)[1]
        with patch.object(live_broad.le, 'front_app', return_value='TextEdit'), \
             patch.object(live_broad, 'ax_values', return_value=''), patch.object(live_broad, 'osa', return_value=''):
            self.assertFalse(check({}, reply(expected)))
        with patch.object(live_broad.le, 'front_app', return_value='TextEdit'), \
             patch.object(live_broad, 'ax_values', return_value=''), patch.object(live_broad, 'osa', return_value=expected):
            self.assertTrue(check({}, reply('Done.')))
        with patch.object(live_broad.le, 'front_app', return_value='TextEdit'), \
             patch.object(live_broad, 'ax_values', return_value=''), patch.object(live_broad, 'osa', return_value=expected+' unwanted words'):
            self.assertFalse(check({}, reply(expected)))

    def test_calculator_claim_requires_independent_correct_display(self):
        with patch.object(le, 'front_app', return_value='Calculator'), patch.object(le, 'calculator_display', return_value='142'):
            self.assertFalse(le.calculator_matches(42))
        with patch.object(le, 'front_app', return_value='Calculator'), patch.object(le, 'calculator_display', return_value='42'):
            self.assertTrue(le.calculator_matches(42))
        with patch.object(le, 'front_app', return_value='Finder'), patch.object(le, 'calculator_display') as display:
            self.assertFalse(le.calculator_matches(42))
            display.assert_not_called()

    def test_reported_calculator_case_cannot_pass_on_a_lying_reply(self):
        # Build only: callbacks that open apps are never invoked.
        case = next(case for case in le.build_cases(Mock()) if 'add 12 and 30' in case.request)
        with patch.object(le, 'front_app', return_value='Calculator'), patch.object(le, 'calculator_display', return_value='0'):
            self.assertIsNotNone(case.check({}, reply('The answer is 42.')))
        with patch.object(le, 'front_app', return_value='Calculator'), patch.object(le, 'calculator_display', return_value='42'):
            self.assertIsNone(case.check({}, reply('The answer is 42.')))

    def test_exact_signed_decimal_tokens_reject_substrings_and_wrong_signs(self):
        for message in ('142', '4.2', '-42', '42.1', '420', 'answer142'):
            self.assertFalse(le.number_in_text(message, 42), message)
        self.assertTrue(le.number_in_text('The answer is −42.', -42))
        self.assertTrue(le.number_in_text('The answer is 12,345.25.', Decimal('12345.25')))
        self.assertFalse(le.number_equals('42 keypad buttons', 42))
        self.assertFalse(le.number_equals('NaN', 42))
        self.assertEqual(le.arithmetic_result(2, '-', 7), Decimal('-5'))
        self.assertEqual(le.arithmetic_result(1, '/', 8), Decimal('0.125'))
        self.assertEqual(le.arithmetic_result(1, '/', 3), Decimal('0.3333333333'))

    def test_generated_calculator_expectation_is_independent_of_product_arithmetic(self):
        with patch.object(live_tasks, 'installed', return_value=set()), \
             patch('companion.arithmetic.evaluate', side_effect=AssertionError('production arithmetic used')):
            task = live_tasks.build(Mock(), 1, 17, only=['calculator'])[0]
        result = Decimal(task.checks[0][0].removeprefix('answer is '))
        self.assertTrue(task.checks[0][1]({}, reply(str(result))))
        self.assertFalse(task.checks[0][1]({}, reply(str(result+1))))
        with patch.object(le, 'front_app', return_value='Calculator'), patch.object(le, 'calculator_display', return_value=str(result+1)):
            self.assertFalse(task.checks[1][1]({}, reply(str(result))))


LIVE_SCRIPTS = ('live_eval', 'live_tasks', 'live_broad', 'live_hard', 'live_harder', 'live_features',
                'live_heldout', 'live_conversation', 'live_capability', 'live_grounding')
DICT_SUITES = ('conversation', 'broad', 'harder', 'features', 'capability', 'grounding')
# Hand-audited inventory (docs/ai/LEGACY_LIVE_SUITES.md), written independently of the
# screening code: the cases whose request or setup opens or scripts a private application.
PRIVATE_REQUESTS = {
    'requests': {'Microsoft Teams.', 'click on Chat', 'check if I have new notifications on Teams',
                 'read my second last email', 'click the first email in Mail', "what's on my screen",
                 'are there any notifications that require my attention'},
    'navigation': {'open the Calendar in Teams'},
    'answers': set(), 'varied': set()}
PRIVATE_FAMILIES = {'v2': {'safety_send'}, 'v3': {'unread_mail'},
                    'heldout': {'heldout_photos_collection', 'heldout_contacts_count'}}
EXPECTED_PRIVATE = {**PRIVATE_REQUESTS, **PRIVATE_FAMILIES, **{suite: set() for suite in DICT_SUITES}}


def build_time_facts_only(args, *rest, **kwargs):
    command = list(args) if isinstance(args, (list, tuple)) else [args]
    if command and command[0] in ('sw_vers', 'pmset', 'defaults'):
        return subprocess.CompletedProcess(command, 0, '', '')  # live_tasks reads these while building
    raise AssertionError('native command during case collection: %r' % (command,))


def collect_every_case(seeds=(0, 1, 2)):
    """Every legacy suite's cases, built without apps, network, model or A11y; nothing runs."""
    modules = {name: importlib.import_module(name) for name in LIVE_SCRIPTS}
    window = le.EvalWindow()  # real methods, so screening reads EvalWindow.ensure as a run would
    suites = {}
    with tempfile.TemporaryDirectory() as home:
        for folder in ('Downloads', 'Documents', 'Desktop'):
            (Path(home)/folder).mkdir()
        with patch.object(subprocess, 'run', side_effect=build_time_facts_only), \
             patch.object(subprocess, 'Popen', side_effect=AssertionError('process started during collection')), \
             patch('urllib.request.urlopen', side_effect=AssertionError('network during collection')), \
             patch.object(Path, 'home', return_value=Path(home)), \
             patch.object(le, 'running', return_value=False), \
             patch.object(live_tasks, 'installed', return_value=set()), \
             patch.object(modules['live_conversation'], 'installed', return_value=set()), \
             patch.object(modules['live_hard'], 'section_opening', return_value='The history begins here.'):
            suites['answers'] = le.build_answers(window)
            suites['requests'] = le.build_cases(window)
            suites['navigation'] = le.build_navigation(window)
            for seed in seeds:
                suites.setdefault('varied', []).extend(le.build_varied(window, 40, seed))
                suites.setdefault('v2', []).extend(live_tasks.build(window, 120, seed))
                suites.setdefault('v3', []).extend(modules['live_hard'].build(window, 60, seed))
                suites.setdefault('heldout', []).extend(modules['live_heldout'].build(window, 75, seed))
            for suite in DICT_SUITES:
                suites[suite] = modules['live_'+suite].build(window, seed=0)
    return suites


def label(suite, case):
    if suite in PRIVATE_REQUESTS:
        return case.request
    return case.family if suite in PRIVATE_FAMILIES else case['name']


class LegacyPrivateCaseScreeningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.suites = collect_every_case()

    def test_every_suite_is_collected_without_native_work(self):
        self.assertEqual(set(self.suites), set(EXPECTED_PRIVATE))
        for suite, cases in self.suites.items():
            with self.subTest(suite=suite):
                self.assertTrue(cases)

    def test_screening_flags_exactly_the_audited_private_cases(self):
        for suite, cases in self.suites.items():
            with self.subTest(suite=suite):
                present = {label(suite, case) for case in cases}
                self.assertLessEqual(EXPECTED_PRIVATE[suite], present, 'an audited private case is no longer collected; update the inventory')
                flagged = {label(suite, case) for case in cases if le.private_case_reasons(case)[1]}
                self.assertEqual(flagged, EXPECTED_PRIVATE[suite])

    def test_no_private_case_runs_by_default(self):
        for suite, cases in self.suites.items():
            with self.subTest(suite=suite):
                lines = []
                kept = le.refuse_private_cases(cases, report=lines.append)
                self.assertFalse({label(suite, case) for case in kept} & EXPECTED_PRIVATE[suite])
                self.assertFalse([case for case in kept if le.private_case_reasons(case)[1]])
                self.assertEqual(len(kept)+len(lines), len(cases))
                self.assertTrue(all(line.startswith('refused ') and '--allow-private-apps' in line for line in lines))
                self.assertEqual(len(le.refuse_private_cases(cases, allow=True, report=[].append)), len(cases))

    def test_every_legacy_script_is_marked_as_a_diagnostic(self):
        for name in LIVE_SCRIPTS:
            with self.subTest(script=name):
                self.assertIn('Legacy diagnostic, not an acceptance gate; see docs/ai/TESTING.md',
                              importlib.import_module(name).__doc__)


class PrivateMentionTests(unittest.TestCase):
    def test_private_application_words_and_identifiers(self):
        for text in ('open teams in a new safari window', 'read my newest e-mail', 'Show my Photos', 'check my inbox',
                     'call Ana on FaceTime', 'https://teams.microsoft.com/', 'tell application id "com.apple.MobileSMS"'):
            self.assertTrue(le.private_app_mentions(text), text)
        for text in ('look for photo files in Finder', 'https://teams', 'mail_front', 'test@example.com', 'mailto', 'Contact sheet'):
            self.assertEqual(le.private_app_mentions(text), [], text)

    def test_setup_helpers_are_read_not_run(self):
        window = le.EvalWindow()

        def documented(app):
            """Unlike Teams, this only brings Finder forward."""
            le.activate('Finder')
        with patch.object(le.subprocess, 'run', side_effect=AssertionError('a native command ran')):
            self.assertEqual(le.private_app_mentions(lambda app: le.mail_front()), ['Mail'])
            self.assertTrue(any('teams' in name.casefold() for name in le.private_app_mentions(lambda app: le.teams_view(window, 1))))
            self.assertEqual(le.private_app_mentions(lambda app: (window.ensure(le.NEUTRAL), le.activate('Finder'))), [])
            self.assertEqual(le.private_app_mentions([window.close]), [])
            self.assertEqual(le.private_app_mentions(documented), [])


class LegacyRunnerRefusalTests(unittest.TestCase):
    def run_main(self, *extra):
        from live_tasks import Task
        safe = dict(name='safe_case', setup=lambda app: le.activate('Finder'), cleanup=[],
                    turns=[Task('safe', 'open my Downloads folder', [])])
        private = dict(name='private_case', setup=lambda app: le.mail_front(), cleanup=[],
                       turns=[Task('private', 'open my Downloads folder', [])])
        provider = SimpleNamespace(config=SimpleNamespace(vision_model='vision', model='text'))
        with tempfile.TemporaryDirectory() as out, \
             patch.object(sys, 'argv', ['live_eval.py', '--suite', 'conversation', '--out', out, *extra]), \
             patch.object(le.subprocess, 'Popen') as start, patch.object(le, 'App') as app, \
             patch('live_conversation.build', return_value=[safe, private]), patch('live_conversation.run') as run, \
             patch('companion.instance.build_id', return_value='test-build'), \
             patch('companion.gui_models.selection', return_value=('profile', 'model')), \
             patch('companion.ollama.OllamaProvider', return_value=provider), \
             redirect_stdout(io.StringIO()) as printed:
            le.main()
        self.assertEqual([call.args[0][0] for call in start.call_args_list], ['caffeinate'])
        app.return_value.launch.assert_not_called()
        return [case['name'] for case in run.call_args.args[3]], printed.getvalue()

    def test_runner_refuses_a_private_case_and_says_why(self):
        names, printed = self.run_main()
        self.assertEqual(names, ['safe_case'])
        self.assertIn('refused private_case: setup names Mail', printed)
        self.assertIn('--allow-private-apps', printed)

    def test_only_the_explicit_flag_admits_it(self):
        names, printed = self.run_main('--allow-private-apps')
        self.assertEqual(names, ['safe_case', 'private_case'])
        self.assertIn('running private-application case private_case', printed)

    def test_task_runner_drops_private_tasks_before_launch(self):
        from live_tasks import Task
        safe, private = Task('safe', 'open my Downloads folder', []), Task('private', 'post "hello" in my Teams chat', [])
        record = dict(success=True, score=1.0, seconds=1, steps=0, detours=0, checks=[])
        with tempfile.TemporaryDirectory() as out, patch.object(le, 'settings_panes', return_value=[]), \
             patch.object(live_tasks, 'build', return_value=[safe, private]), \
             patch.object(live_tasks, 'run_task', return_value=record) as run_task, patch.object(live_tasks, 'report'), \
             redirect_stdout(io.StringIO()):
            le.run_v2(Mock(), Mock(), Path(out), 2, 0)
        self.assertEqual([call.args[1] for call in run_task.call_args_list], [safe])


if __name__ == '__main__':
    unittest.main()
