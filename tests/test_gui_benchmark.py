import unittest
from scripts.gui_benchmark import score, replay_summary


class BenchmarkTests(unittest.TestCase):
    def test_strict_hits_and_unsupported_denominator(self):
        row={'box':[.1,.2,.3,.4]}
        self.assertTrue(score({'status':'action','message':'Click','risk':'navigation','action':{'type':'click_point','x':.2,'y':.3,'label':'Target'}},row)['hit'])
        self.assertFalse(score({'status':'action','message':'Click','risk':'navigation','action':{'type':'click_point','x':.3,'y':.3,'label':'Target'}},row)['hit'])
        self.assertFalse(score({'status':'done','action':None},row)['hit'])
    def test_reliability_not_average_only(self):
        rows=[dict(profile='ui-tars',model='owned',id='a',scene='scene',seconds=1,hit=h,point=p,click_valid=v,resident_bytes=None)
              for h,p,v in [(True,[.2,.3],True),(False,None,False)]]
        r=replay_summary(rows)[0]
        self.assertEqual(r['hit_rate'],.5);self.assertEqual(r['all_repeats_hit_rate'],0)
        self.assertEqual(r['point_consistency_rate'],0)
    def test_malformed_never_counts_as_stable(self):
        rows=[dict(profile='p',model='m',id='a',scene='s',seconds=1,hit=False,error='bad')]*3
        self.assertEqual(replay_summary(rows)[0]['point_consistency_rate'],0)

    def test_live_workflow_is_not_average_turn_success(self):
        import tempfile,json
        from pathlib import Path
        from scripts.gui_benchmark import live_summary
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            (root/'scope.json').write_text(json.dumps(dict(runs=2,scheduled_turns=2,case_turns={'workflow':2})))
            rows=[dict(case='workflow',turn=t,attempt=a,passed=not(a==2 and t==2),seconds=1)
                  for a in (1,2) for t in (1,2)]
            (root/'runs.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
            report=live_summary([root])[0]
            self.assertEqual(report['turn_success_rate'],.75)
            self.assertEqual(report['task_success_rate'],.5)
            self.assertEqual(report['all_repeats_task_pass_rate'],0)
            (root/'runs.jsonl').write_text(json.dumps(rows[0])+'\n')
            report=live_summary([root])[0]
            self.assertFalse(report['complete']);self.assertIsNone(report['task_success_rate'])

    def test_native_grounding_uses_one_localizer_call_without_policy(self):
        from unittest.mock import Mock,patch
        from scripts.gui_benchmark import localization_proposal
        provider=Mock()
        with patch('scripts.gui_benchmark.infer',return_value=('{"x":500,"y":250}',140,84)) as infer:
            result=localization_proposal(provider,'owned','Target','pixels')
            self.assertEqual(result['action']['x'],.5)
            self.assertEqual(result['action']['y'],.25)
            infer.assert_called_once()
            self.assertEqual(infer.call_args.args[2],'owned')
            provider.complete_structured.assert_not_called()
    def test_native_grounding_rejects_invalid_coordinates_and_extra_fields(self):
        from unittest.mock import Mock,patch
        from scripts.gui_benchmark import localization_proposal
        from companion.provider import ProviderError
        for reply in ('{"x":true,"y":2}','{"x":1000,"y":2}','{"x":2,"y":3,"action":"send"}','[]'):
            with self.subTest(reply=reply),patch('scripts.gui_benchmark.infer',return_value=(reply,140,84)),self.assertRaises(ProviderError):
                localization_proposal(Mock(),'owned','Target','pixels')

    def test_memory_samples_separate_candidate_from_other_resident_models(self):
        from unittest.mock import Mock
        from scripts.gui_benchmark import MemorySampler
        provider=Mock()
        provider.request.side_effect=[{'models':[{'name':'candidate:latest','size_vram':100},{'name':'other:latest','size_vram':200}]},{'models':[{'name':'candidate:latest','size_vram':150}]}]
        sampler=MemorySampler(provider)
        sampler.sample();sampler.sample()
        self.assertEqual(sampler.peak,300)
        self.assertEqual(sampler.models,{'candidate:latest':150,'other:latest':200})
