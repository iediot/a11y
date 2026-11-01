"""scripts/ci_tests.py, the CI test runner: no real child process is started."""
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import ci_tests


class FakeChild:
    def __init__(self, lines):
        self.stdout = iter(lines)

    def wait(self):
        return 1


class RunnerTests(unittest.TestCase):
    def test_output_beyond_cp1252_is_printed_not_a_crash(self):
        # Reported on Windows CI: a failure message holding '›' crashed the runner (cp1252 console).
        console = io.TextIOWrapper(io.BytesIO(), encoding='cp1252')
        with patch.object(ci_tests.subprocess, 'Popen', return_value=FakeChild(['START t\n', "AssertionError: '› what…'\n"])) as popen, \
             patch.object(sys, 'stdout', console), self.assertRaises(SystemExit) as done:
            ci_tests.main()
        self.assertEqual(done.exception.code, 1)
        self.assertEqual(popen.call_args.kwargs['env']['PYTHONIOENCODING'], 'utf-8')
        console.flush()
        self.assertIn('› what…', console.buffer.getvalue().decode('utf-8'))


if __name__ == '__main__':
    unittest.main()
