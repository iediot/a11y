"""Correctness bugs found reviewing commit bcc8483; each test failed before its fix."""
import unittest

from companion.provider import ProviderError


class ReturnKeyTests(unittest.TestCase):
    def test_pyautogui_return_is_enter(self):
        # pyautogui names Enter "return"; the alias was spelled "return_" and never matched.
        from companion.gui_models import key_action, parsed_step
        self.assertEqual(key_action(['return']), {'type': 'press_key', 'key': 'enter'})
        self.assertEqual(key_action(['Return']), {'type': 'press_key', 'key': 'enter'})
        step = parsed_step('pyautogui', "Action:\n```python\npyautogui.press('return')\n```", 100, 100)
        self.assertEqual(step['action'], {'type': 'press_key', 'key': 'enter'})
        with self.assertRaises(ProviderError):
            key_action(['return_'])


class NamedLinkWordBoundaryTests(unittest.TestCase):
    def task(self, goal, label):
        from companion.computer_task import ComputerTask
        task = ComputerTask(goal, automatic=True, goal_mode='inspect')
        task.page_seen = 'http://127.0.0.1:57906/'
        task.pending = {'type': 'click_point', 'label': label}
        task.record({'type': 'click_point', 'label': label, 'status': 'submitted'})
        return task

    PAGE = {'app': {'name': 'Safari', 'bundle_id': 'com.apple.Safari'},
            'window': {'title': 'Store', 'url': 'http://127.0.0.1:57906/store'}}
    REGIONS = [dict(text='Store', x=.1, y=.1, width=.2, height=.04), dict(text='Gift cards', x=.1, y=.2, width=.2, height=.04)]

    def test_a_link_name_inside_another_word_is_not_the_named_link(self):
        # "Store" is not named by "Restore": the early check must not run, and its
        # clicked-label quote must not be accepted as navigation evidence.
        from unittest.mock import Mock
        task = self.task('Open the Restore guide on this support site.', 'Store')
        provider = Mock(complete_structured=Mock(side_effect=[
            dict(met=True, missing='', evidence='Store'), dict(message='The store is open.')]))
        self.assertIsNone(task.goal_met(provider, self.PAGE, self.REGIONS, {}))
        provider.complete_structured.assert_not_called()

    def test_the_named_link_as_whole_words_still_checks(self):
        from unittest.mock import Mock
        task = self.task('Open the Store page on this site.', 'Store')
        provider = Mock(complete_structured=Mock(side_effect=[
            dict(met=True, missing='', evidence='Store'), dict(message='The store is open.')]))
        self.assertEqual(task.goal_met(provider, self.PAGE, self.REGIONS, {})['status'], 'done')


class BenchmarkMalformedReplyTests(unittest.TestCase):
    def test_a_malformed_reply_is_a_miss_not_a_crash(self):
        # The replay loop catches ProviderError/ValueError/KeyError/TypeError only: an
        # AttributeError from score() ended the whole benchmark run.
        from scripts.gui_benchmark import score
        row = {'box': [.1, .2, .3, .4]}
        for raw in ({'status': 'action', 'message': 'm', 'risk': 'navigation', 'action': 'click'},
                    {'status': 'action', 'message': 'm', 'risk': 'navigation', 'action': ['click_point', .2, .3]},
                    ['not', 'an', 'object'], None, 'Action: click'):
            with self.subTest(raw=raw):
                self.assertEqual(score(raw, row), {'hit': False, 'click_valid': False})


class PolicyChoiceTargetTests(unittest.TestCase):
    def test_missing_or_blank_targets_never_agree(self):
        from scripts.policy_choice_eval import matches_target
        self.assertTrue(matches_target(' Amber Lantern ', {'target': 'amber lantern'}))
        self.assertFalse(matches_target('Amber', {'target': None}))  # crashed: None.casefold()
        self.assertFalse(matches_target('   ', {}))  # a blank search counted as agreement
        self.assertFalse(matches_target('', {'target': ''}))
        self.assertFalse(matches_target('x', {'target': 7}))


class BenchmarkPercentileTests(unittest.TestCase):
    def test_p95_is_the_nearest_rank_not_the_maximum(self):
        from scripts.gui_benchmark import distribution
        self.assertEqual(distribution(range(1, 21))['p95'], 19)  # was 20, the maximum
        self.assertEqual(distribution(range(1, 101))['p95'], 95)  # was 96
        self.assertEqual(distribution([4])['p95'], 4)
        self.assertEqual(distribution([3, 1])['p95'], 3)
        self.assertIsNone(distribution([]))


@unittest.skipUnless(__import__('shutil').which('bash') and __import__('shutil').which('flock')
                     and __import__('sys').platform != 'win32', 'needs bash and flock')
class ConversionInterruptTests(unittest.TestCase):
    def test_an_interrupted_conversion_stays_interrupted(self):
        # The INT/TERM trap wrote "interrupted", then its exit 130 ran the EXIT trap,
        # which overwrote progress.json with "failed".
        import json, os, signal, subprocess, sys, tempfile, time
        from pathlib import Path
        script = Path(__file__).resolve().parent.parent/'scripts'/'router_arch_convert.sh'
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            (root/'qualification-pilot-1').mkdir()
            for env in ('.venv', '.venv-convert'):
                (root/env/'bin').mkdir(parents=True)
                stub = root/env/'bin'/'python'
                stub.write_text('#!/bin/sh\ncat >/dev/null\nsleep 1\n')  # stands in for the version checks
                stub.chmod(0o755)
            out = root/'out'
            env = dict(os.environ, PATH=os.environ.get('PATH', '')+os.pathsep+os.path.dirname(sys.executable))
            process = subprocess.Popen(['bash', str(script), str(root), str(out)], env=env,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            progress = out/'progress.json'
            deadline = time.monotonic()+20
            while time.monotonic() < deadline and not (progress.exists() and progress.read_text().strip()):
                time.sleep(.05)
            self.assertEqual(json.loads(progress.read_text())['status'], 'running')
            process.send_signal(signal.SIGTERM)
            self.assertEqual(process.wait(timeout=30), 130)
            self.assertEqual(json.loads(progress.read_text())['status'], 'interrupted')


if __name__ == '__main__':
    unittest.main()
