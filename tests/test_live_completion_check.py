import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from live_features import honest


class LiveCompletionCheckTests(unittest.TestCase):
    def test_pending_action_and_running_task_cannot_pass_with_empty_reply(self):
        self.assertFalse(honest(dict(task=dict(status='running'),pending_action=dict(type='click_point',label='Light')),[]))
        self.assertFalse(honest(dict(task=dict(status='running')),[]))
        self.assertFalse(honest(dict(task=dict(status='needs_user')),[]))

    def test_completed_tasks_and_code_actions_remain_eligible(self):
        self.assertTrue(honest(dict(task=dict(status='done')),[]))
        self.assertTrue(honest({},[]))
