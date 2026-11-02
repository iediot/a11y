"""The packaging workflow tests every push to main and every pull request on macOS and Windows."""
import re
import unittest
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[1]/'.github'/'workflows'/'package.yml'


def section(text, name, indent):
    """The lines under `name:` at this indent, up to the next key at the same indent."""
    match = re.search(rf'^{" "*indent}{name}:.*\n((?:(?:{" "*(indent+1)}.*)?\n)*)', text, re.M)
    return match.group(1) if match else None


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text(encoding='utf-8')

    def test_pushes_to_main_and_pull_requests_trigger_it(self):
        triggers = section(self.text, 'on', 0)
        self.assertIsNotNone(triggers)
        self.assertRegex(triggers, r'(?m)^  pull_request:')
        push = section(triggers, 'push', 2)
        self.assertIn('branches: [main]', push)

    def test_both_platforms_run_the_unit_tests(self):
        jobs = section(self.text, 'jobs', 0)
        for job, command in (('macos', 'python -m unittest discover -s tests'), ('windows', 'python scripts/ci_tests.py')):
            body = section(jobs, job, 2)
            self.assertIsNotNone(body, job)
            self.assertIn('QT_QPA_PLATFORM: offscreen', body)
            self.assertIn(command, body)
        self.assertIn('runs-on: macos-', section(jobs, 'macos', 2))
        self.assertIn('runs-on: windows-', section(jobs, 'windows', 2))

    def test_only_pull_request_runs_are_cancelled_by_newer_ones(self):
        self.assertIn("cancel-in-progress: ${{ github.event_name == 'pull_request' }}", self.text)
        self.assertIn('github.event.pull_request.number || github.run_id', self.text)

    def test_release_never_runs_for_a_pull_request(self):
        release = section(section(self.text, 'jobs', 0), 'release', 2)
        self.assertIn("if: startsWith(github.ref, 'refs/tags/v') || startsWith(inputs.release, 'v')", release)


if __name__ == '__main__':
    unittest.main()
