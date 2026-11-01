import unittest
from companion.computer_task import completion_evidence, completion_schema


class CompletionEvidenceBoundaryTests(unittest.TestCase):
    def check(self, evidence, observation):
        return completion_evidence(dict(met=True, missing='', evidence=evidence), observation)

    def test_partial_names_paths_and_statuses_do_not_prove_observed_outcomes(self):
        pairs = [
            ('report.txt', {'facts': {'document_name': 'report.txt.backup'}}),
            ('/tmp/owned/Reports', {'facts': {'finder': {'target': '/tmp/owned/Reports-old'}}}),
            ('Timer running', {'screen_text': ['Timer running is not supported']}),
            ('example.org', {'facts': {'url': 'https://example.org.evil.invalid/'}}),
        ]
        for quote, observation in pairs:
            with self.subTest(quote=quote):
                self.assertFalse(self.check(quote, observation))

    def test_actual_values_remain_valid_with_case_and_whitespace_normalization(self):
        observation = dict(window='Report.txt', screen_text=['Total packs: 43'], facts={'finder': {'target': '/tmp/owned/Reports'}})
        for quote in ('report.TXT', '  Total   packs: 43  ', '/tmp/owned/Reports'):
            self.assertTrue(self.check(quote, observation))

    def test_schema_bounds_are_also_enforced_by_final_validation(self):
        observation = {'screen_text': ['x' * 1025] + ['line %03d' % i for i in range(90)]}
        choices = completion_schema(observation)['properties']['evidence']['enum']
        self.assertTrue(self.check(choices[-1], observation))
        self.assertFalse(self.check('line 089', observation))
        self.assertFalse(self.check('x' * 1025, observation))

    def test_plan_goal_and_action_labels_do_not_supply_evidence(self):
        observation = dict(goal='Open report.txt', last_click='report.txt', planned_outcome='report.txt is open', facts={})
        for quote in ('report.txt', 'report.txt is open', 'Open report.txt'):
            self.assertFalse(self.check(quote, observation))

    def test_non_string_model_evidence_is_rejected(self):
        for evidence in (42, True, None, ['report.txt']):
            self.assertFalse(self.check(evidence, {'facts': {'count': 42, 'state': True}, 'screen_text': ['report.txt']}))

    def test_blank_and_single_character_values_cannot_supply_proof(self):
        observation = {'screen_text': ['  ', 'x', '\n \t']}
        for evidence in ('', '  ', 'x', '\n \t'):
            self.assertFalse(self.check(evidence, observation))
