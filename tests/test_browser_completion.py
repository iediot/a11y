import json
import unittest
from unittest.mock import Mock
from companion.computer_task import ComputerTask, completion_schema


class BrowserCompletionTests(unittest.TestCase):
    def task(self, goal='Create two tabs and read the page heading.'):
        task = ComputerTask(goal, automatic=True, goal_mode='inspect')
        task.initial_browser_counts = dict(window_id=42, tab_count=1)
        task.receipts = [dict(type='press_key', label='Press shortcut', status='submitted', screen='same')]
        task.screen_seen = 'same'
        task.plan = dict(done_when='Two additional tabs exist and the heading has been read.')
        return task

    def snapshot(self, count=2, window=42, bundle='com.apple.Safari'):
        return dict(app=dict(name='Safari', bundle_id=bundle),
                    window=dict(title='Start Page'), browser=dict(window_id=window, tab_count=count))

    def provider(self, task, snapshot, *, met=True, missing=''):
        observed = task.completion_observation(snapshot, [])
        evidence = (observed['facts'].get('browser_transition') or {}).get('evidence', 'Start Page')
        return Mock(complete_structured=Mock(side_effect=[
            dict(met=met, missing=missing, evidence=evidence), dict(message='The requested tabs are open.')]))

    def test_structural_change_checks_full_goal_even_without_changed_pixels(self):
        task = self.task('Create one additional tab.')
        snap = self.snapshot()
        provider = self.provider(task, snap)
        self.assertEqual(task.goal_met(provider, snap, [], {})['status'], 'done')
        call = provider.complete_structured.call_args_list[0]
        observation = json.loads(call.args[1][0]['content'])
        self.assertEqual(observation['goal'], task.goal)
        self.assertNotIn('planned_outcome', observation)
        self.assertIn('change +1', observation['facts']['browser_transition']['evidence'])

    def test_count_change_does_not_override_missing_compound_outcome(self):
        task = self.task()
        snap = self.snapshot()
        provider = self.provider(task, snap, met=False, missing='Another tab and the page heading are missing.')
        self.assertIsNone(task.goal_met(provider, snap, [], {}))
        self.assertEqual(provider.complete_structured.call_count, 1)

    def test_contradictory_met_true_with_missing_is_rejected(self):
        task = self.task(); snap = self.snapshot()
        provider = self.provider(task, snap, missing='Heading not read.')
        self.assertIsNone(task.goal_met(provider, snap, [], {}))

    def test_absent_unchanged_unbound_or_invalid_counts_cannot_enable_check(self):
        for snap in (self.snapshot(1), self.snapshot(window=43), self.snapshot(count=True),
                     self.snapshot(count=-1), self.snapshot(window=0), self.snapshot(bundle='com.google.Chrome'),
                     dict(app=dict(bundle_id='com.apple.Safari'), window={})):
            with self.subTest(snapshot=snap):
                task = self.task(); provider = Mock()
                self.assertIsNone(task.goal_met(provider, snap, [], {}))
                provider.complete_structured.assert_not_called()
        task = self.task(); task.initial_browser_counts = {}
        provider = Mock()
        self.assertIsNone(task.goal_met(provider, self.snapshot(), [], {}))
        provider.complete_structured.assert_not_called()

    def test_rejected_action_or_remaining_queue_cannot_enable_check(self):
        for mode in ('rejected', 'queued', 'change'):
            with self.subTest(mode=mode):
                task = self.task()
                if mode == 'rejected': task.receipts[-1]['status'] = 'rejected'
                elif mode == 'queued': task.queue = [{'type':'press_key'}]
                else: task.goal_mode = 'change'
                provider = Mock()
                self.assertIsNone(task.goal_met(provider, self.snapshot(), [], {}))
                provider.complete_structured.assert_not_called()

    def test_browser_plan_is_not_added_as_evidence_or_a_new_requirement(self):
        task = self.task(); observation = task.completion_observation(self.snapshot(), [])
        choices = completion_schema(observation)['properties']['evidence']['enum']
        self.assertNotIn(task.plan['done_when'], choices)
        self.assertIn(observation['facts']['browser_transition']['evidence'], choices)

    def test_successful_verifier_response_cannot_report_missing_requirements(self):
        observation = self.task().completion_observation(self.snapshot(), [])
        schema = completion_schema(observation)
        alternatives = schema.get('anyOf', [schema])
        success = [part for part in alternatives
                   if True in part['properties']['met'].get('enum', [True, False])]
        self.assertTrue(success)
        for part in success:
            self.assertEqual(part['properties']['missing'].get('enum'), [''])
            self.assertIn(observation['facts']['browser_transition']['evidence'],
                          part['properties']['evidence']['enum'])
        self.assertTrue(any(False in part['properties']['met'].get('enum', [True, False])
                            for part in alternatives))
