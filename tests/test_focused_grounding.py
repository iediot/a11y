import json
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from companion.computer_task import ComputerTask
from companion.ollama import OllamaProvider, RequestCancelled
from companion.provider import AIConfig, ProviderError
from companion.gui_models import localize_target


class Policy:
    def __init__(self):
        self.prepare = Mock(return_value='Compass')
        self.ground = Mock(return_value={'x':.5,'y':.5})
    def prepare_single_click(self,*a,**kw):return self.prepare(*a,**kw)
    def localize_target(self,*a,**kw):return self.ground(*a,**kw)


class FocusedTests(unittest.TestCase):
    def setUp(self):
        self.snapshot={'platform':'macOS','app':{'name':'Maps','bundle_id':'com.apple.Maps'},
                       'window':{'title':'Map','bounds':dict(x=10,y=20,width=900,height=600)},'controls':[],'targets':[]}
        self.visual={'screen':dict(x=0,y=0,width=1000,height=800)}
        self.desktop=SimpleNamespace(enabled=True,generation=0,bridge=SimpleNamespace(hit=Mock(return_value={'title':'Compass','role':'AXButton'})))
        self.vision=SimpleNamespace(enabled=True,generation=0,click_guard=Mock(return_value={'region_hash':'same'}))
        self.provider=Policy()
        self.task=ComputerTask('Click Compass.',automatic=True,goal_mode='inspect')
    def run_step(self):
        return self.task.focused_click_step(self.desktop,self.snapshot,[],self.visual,'pixels',self.vision,self.provider)
    def test_one_supplied_target_with_existing_guard_and_navigation(self):
        result=self.run_step()
        self.assertEqual(result['action']['label'],'Compass')
        self.assertFalse(result['review']);self.assertEqual(result['action']['capture_guard']['region_hash'],'same')
        self.assertEqual(self.provider.prepare.call_count,1);self.assertEqual(self.provider.ground.call_count,1)
        self.assertEqual(self.task.pending,result['action'])
    def test_native_or_ocr_matches_and_ambiguities_never_invoke_localizer(self):
        for count in (1,2):
            self.snapshot['targets']=[dict(label='Compass',enabled=True,bounds=dict(x=30+i*100,y=40,width=40,height=40)) for i in range(count)]
            self.assertIsNone(self.run_step())
        self.provider.prepare.assert_not_called();self.provider.ground.assert_not_called()
    def test_compound_or_unsupported_scope_falls_back_without_coordinates(self):
        self.provider.prepare.return_value=None
        self.task.goal='Click Compass then zoom out.'
        self.assertIsNone(self.run_step());self.provider.ground.assert_not_called()
    def test_native_named_mismatch_and_other_window_never_submit(self):
        self.desktop.bridge.hit.return_value={'title':'Send','role':'AXButton'}
        self.assertIsNone(self.run_step());self.assertIsNone(self.task.pending)
        self.task.focused_attempted=False;self.provider.ground.return_value={'x':.99,'y':.99}
        self.assertIsNone(self.run_step());self.assertIsNone(self.task.pending)
    def test_gestures_preserved(self):
        for verb,button,count in [('Double-click','left',2),('Right-click','right',1)]:
            self.task=ComputerTask(verb+' Compass.',automatic=True,goal_mode='inspect')
            result=self.run_step();self.assertEqual(result['action']['button'],button);self.assertEqual(result['action']['click_count'],count)
    def test_sensitive_click_keeps_confirmation_or_full_policy(self):
        self.task=ComputerTask('Click Save.',automatic=True,goal_mode='change')
        self.provider.prepare.return_value='Save';self.desktop.bridge.hit.return_value={'title':'Save','role':'AXButton'}
        result=self.run_step();self.assertTrue(result is None or result['review'])
    def test_pause_or_stop_during_either_inference_prevents_proposal(self):
        for stage in ('prepare','ground'):
            self.setUp()
            def pause(*a,**kw):
                self.desktop.generation+=1
                return 'Compass' if stage=='prepare' else {'x':.5,'y':.5}
            getattr(self.provider,stage).side_effect=pause
            with self.assertRaises(ValueError):self.run_step()
            self.assertIsNone(self.task.pending)
    def test_cancel_is_not_swallowed_and_failures_do_not_loop_localization(self):
        self.provider.ground.side_effect=RequestCancelled()
        with self.assertRaises(RequestCancelled):self.run_step()
        self.setUp();self.provider.ground.side_effect=ProviderError('failed')
        self.assertIsNone(self.run_step());self.assertIsNone(self.run_step())
        self.assertEqual(self.provider.ground.call_count,1)
    def test_policy_requires_literal_target_and_verified_default_architecture(self):
        provider=OllamaProvider(AIConfig(model='text',vision_model='vision'))
        provider.model_info=Mock(return_value={'model_info':{'general.architecture':'qwen3vl'}})
        provider.complete_structured=Mock(return_value={'scope':'named_target','target':'Compass'})
        with patch('companion.gui_models.selection',return_value=('qwen3-vl','')):
            self.assertEqual(provider.prepare_single_click('Click Compass.'),'Compass')
            for reply in ({'scope':'named_target','target':'Send'},{'scope':'conditional','target':'Compass'}, {'scope':True,'target':'Compass'}):
                provider.complete_structured.return_value=reply
                self.assertIsNone(provider.prepare_single_click('Click Compass.'))
        with patch.dict(os.environ,{'A11Y_FOCUSED_GROUNDING':'0'}),patch('companion.gui_models.selection',return_value=('qwen3-vl','')):
            provider.complete_structured.reset_mock();self.assertIsNone(provider.prepare_single_click('Click Compass.'));provider.complete_structured.assert_not_called()
    def test_grid_parser_strict_and_context_matches_policy(self):
        with patch('companion.gui_models.infer',return_value=('{"x":500,"y":250}',100,100)):
            self.assertEqual(localize_target(Mock(),'vision','Compass','pixels'),{'x':.5,'y':.25})
        for payload in ('{"x":0.5,"y":250}','{"x":true,"y":2}','{"x":1000,"y":2}','{"x":2,"y":3,"shell":"bad"}'):
            with self.subTest(payload=payload),patch('companion.gui_models.infer',return_value=(payload,100,100)),self.assertRaises(ProviderError):localize_target(Mock(),'vision','Compass','pixels')
        p=OllamaProvider(AIConfig(model='text',vision_model='vision'))
        with patch.dict(os.environ,{'A11Y_OLLAMA_CONTEXT':'12288'}),patch('companion.gui_models.localize_target',return_value={}) as local:
            p.localize_target('Compass','pixels');self.assertEqual(local.call_args.kwargs['context'],12288)

    def test_ocr_match_bypasses_focus_and_container_names_are_not_target_names(self):
        regions=[dict(text='Compass',x=.4,y=.4,width=.1,height=.1)]
        self.assertIsNone(self.task.focused_click_step(self.desktop,self.snapshot,regions,self.visual,'pixels',self.vision,self.provider))
        self.provider.prepare.assert_not_called()
        self.desktop.bridge.hit.return_value={'title':'Fixture page','role':'AXWebArea'}
        self.assertIsNotNone(self.run_step())

    def test_scope_cannot_discard_followup_conditions_or_spatial_qualifiers(self):
        for goal,target in [('Click Compass then zoom out.','Compass'),('Click Compass if it is safe.','Compass'),('Click the left Compass.','Compass')]:
            self.setUp();self.task.goal=goal;self.provider.prepare.return_value=target
            self.assertIsNone(self.run_step());self.provider.ground.assert_not_called()

    def test_explicit_holo2_selection_uses_its_grounder_without_qwen_image_policy(self):
        p=OllamaProvider(AIConfig(model='text',vision_model='qwen-vision'))
        p.model_info=Mock(return_value={'model_info':{'general.architecture':'qwen3vl'}})
        p.complete_structured=Mock(return_value={'scope':'named_target','target':'Compass'})
        with patch('companion.gui_models.selection',return_value=('holo2','owned-holo2')),patch('companion.gui_models.localize_target',return_value={'x':.5,'y':.5}) as local:
            self.assertEqual(p.prepare_single_click('Click Compass.'),'Compass')
            p.localize_target('Compass','pixels')
            p.model_info.assert_called_once_with('owned-holo2')
            self.assertEqual(local.call_args.args[1],'owned-holo2')
            self.assertNotIn('image_data',p.complete_structured.call_args.kwargs)

    def test_other_profiles_do_not_silently_use_qwen_focused_coordinates(self):
        p=OllamaProvider(AIConfig(model='text',vision_model='vision'))
        p.model_info=Mock()
        with patch('companion.gui_models.selection',return_value=('ui-tars','owned')):
            self.assertIsNone(p.prepare_single_click('Click Compass.'))
            with self.assertRaises(ProviderError):p.localize_target('Compass','pixels')
        p.model_info.assert_not_called()
