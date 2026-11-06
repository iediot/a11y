"""Legacy evaluation must reject fake success and private setup without native I/O."""
from contextlib import redirect_stderr
from decimal import Decimal
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
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


if __name__ == '__main__':
    unittest.main()
