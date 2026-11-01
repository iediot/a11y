import base64
import json
import os
import unittest
from unittest.mock import Mock, patch
from companion.gui_models import (computer_step, fraction, infer, literal_call, parsed_step,
                                  prepare_image, selection)
from companion.provider import ProviderError


def image():
    from PyQt6.QtCore import QByteArray, QBuffer, QIODevice
    from PyQt6.QtGui import QImage
    im=QImage(140,84,QImage.Format.Format_RGB32)
    im.fill(0xffffff)
    data=QByteArray();b=QBuffer(data);b.open(QIODevice.OpenModeFlag.WriteOnly);im.save(b,'PNG')
    return 'data:image/png;base64,'+bytes(data.toBase64()).decode()


class GUITests(unittest.TestCase):
    def test_coordinate_units_are_explicit(self):
        self.assertEqual(fraction(1,1000),.001)
        for value in (-1,1000,float('nan'),float('inf'),True):
            with self.subTest(value=value), self.assertRaises(ProviderError): fraction(value,1000)
    def test_ui_tars_actual_box_tokens_and_gesture(self):
        r=parsed_step('uitars',"Thought: Open folder\nAction: left_double(start_box='<|box_start|>(70,42)<|box_end|>')",140,84)
        self.assertEqual((r['action']['x'],r['action']['y']),(.5,.5))
        self.assertEqual(r['action']['click_count'],2)
        self.assertEqual(r['risk'],'change')
    def test_finished_does_not_prove_completion(self):
        for source in ('finished()',"finished(content='Done')"):
            r=parsed_step('uitars','Action: '+source,140,84)
            self.assertEqual(r['status'],'done');self.assertIsNone(r['action'])
        # ComputerTask still verifies every policy's done through its existing completion checks.
    def test_unsupported_actions_are_not_silently_approximated(self):
        for source in ('wait()',"scroll(start_box='(20,20)', direction='down')", "hotkey(key='cmd delete')"):
            with self.subTest(source=source),self.assertRaises(ProviderError):
                parsed_step('uitars','Action: '+source,140,84)
    def test_type_content_is_data(self):
        payload="__import__('os').system('never')"
        r=parsed_step('uitars','Action: type(content='+repr(payload)+')',140,84)
        self.assertEqual(r['action']['text'],payload)
    def test_python_is_never_executed(self):
        attacks=["pyautogui.click(1,2); __import__('os')", "__import__('os').system('x')", "pyautogui.click(*[1,2])", "pyautogui.click(x=float('nan'),y=1)","pyautogui.__dict__['click'](1,2)","pyautogui.click(1,2, **{})"]
        for source in attacks:
            with self.subTest(source=source),self.assertRaises(ProviderError): literal_call(source,'pyautogui')
    def test_opencua_pixels_and_input(self):
        r=parsed_step('pyautogui','```python\npyautogui.click(x=70,y=42)\n```',140,84)
        self.assertEqual(r['action']['x'],.5)
        r=parsed_step('pyautogui',"```python\npyautogui.hotkey('cmd','f')\n```",140,84)
        self.assertEqual(r['action']['key'],'cmd_f')
    def test_multi_action_outputs_rejected(self):
        for source in ("```python\npyautogui.click(1,2)\npyautogui.write('x')\n```", "```python\nimport os\n```",'```python\npyautogui.click(1,2)\n```\n```python\npyautogui.click(3,4)\n```'):
            with self.subTest(source=source),self.assertRaises(ProviderError): parsed_step('pyautogui',source,140,84)
    def test_image_dimensions_are_transmitted_grid(self):
        _,w,h=prepare_image(image(),True)
        self.assertEqual((w,h),(140,84))
        with self.assertRaises(ProviderError): prepare_image('data:image/png;base64,no!',True)
    def test_default_delegates_unchanged(self):
        provider=Mock();provider.complete_structured.return_value={'original':True}
        with patch('companion.gui_models.selection',return_value=('qwen3-vl','')):
            r=computer_step(provider,'system',[{'role':'user','content':'x'}],schema={},image_data='same')
        self.assertEqual(r,{'original':True})
        self.assertEqual(provider.complete_structured.call_args.kwargs['image_data'],'same')
        provider.request.assert_not_called()
    def test_holo2_only_localizes_pixel_clicks(self):
        provider=Mock();provider.complete_structured.return_value={'status':'action','risk':'navigation','action':{'type':'click_target','target_id':'t1'}}
        with patch('companion.gui_models.selection',return_value=('holo2','owned')),patch('companion.gui_models.infer') as call:
            computer_step(provider,'s',[],schema={},image_data='same');call.assert_not_called()
        provider.complete_structured.return_value={'status':'action','risk':'navigation','action':{'type':'click_point','x':.1,'y':.2,'label':'Target','click_count':2}}
        with patch('companion.gui_models.selection',return_value=('holo2','owned')),patch('companion.gui_models.infer',return_value=('{"x":500,"y":250}',140,84)):
            r=computer_step(provider,'s',[],schema={},image_data='same')
        self.assertEqual(r['action']['x'],.5);self.assertEqual(r['action']['click_count'],2)
        self.assertEqual(r['risk'],'navigation')
    def test_holo2_invalid_output_rejected(self):
        p=Mock();p.complete_structured.return_value={'action':{'type':'click_point','label':'Target'}}
        for output in ('{"x":1000,"y":0}','{"x":true,"y":2}','{"x":2,"y":3,"code":"x"}','not json'):
            with patch('companion.gui_models.selection',return_value=('holo2','owned')),patch('companion.gui_models.infer',return_value=(output,140,84)), self.subTest(output=output),self.assertRaises(ProviderError):
                computer_step(p,'s',[],schema={},image_data='same')
    def test_gui_wire_is_native_and_never_pulls(self):
        p=Mock();p.model_info.return_value={'capabilities':['vision']}
        p.request.return_value={'done':True,'done_reason':'stop','message':{'content':'Action: finished()'}}
        r=infer(p,'ui-tars','owned','prompt',image())
        path,body=p.request.call_args.args
        self.assertEqual(path,'/api/chat');self.assertNotIn('format',body)
        self.assertEqual(body['model'],'owned');self.assertEqual(r[1:],(140,84))
    def test_truncation_no_vision_and_cancellation(self):
        p=Mock();p.model_info.return_value={'capabilities':['vision']}
        p.request.return_value={'done':True,'done_reason':'length','message':{'content':'Action: finished()'}}
        with self.assertRaises(ProviderError): infer(p,'ui-tars','owned','prompt',image())
        p.model_info.return_value={'capabilities':[]}
        with self.assertRaises(ProviderError): infer(p,'ui-tars','owned','prompt',image())
        import threading
        cancel=threading.Event();cancel.set();p.reset_mock()
        with self.assertRaises(ProviderError): infer(p,'ui-tars','owned','prompt',image(),cancel=cancel)
        p.request.assert_not_called()
    def test_unknown_and_unverified_profiles_blocked(self):
        for profile in ('cloud','opencua','holo3.1','holo4'):
            with patch.dict(os.environ,{'A11Y_GUI_PROFILE':profile}),self.subTest(profile=profile),self.assertRaises(ProviderError):selection()
    def test_default_is_not_inferred_from_checkpoint_alias(self):
        with patch.dict(os.environ,{'A11Y_GUI_PROFILE':'qwen3-vl','A11Y_GUI_MODEL':'unrelated'}):
            self.assertEqual(selection(),('qwen3-vl','unrelated'))

    def test_exact_readonly_target_keeps_navigation_review_rules(self):
        messages=[{'role':'user','content':json.dumps({'goal_mode':'inspect'})},{'role':'user','content':'image'}]
        target={'x':.4,'y':.4,'width':.2,'height':.2,'label':'Folder'}
        with patch('companion.gui_models.selection',return_value=('ui-tars','owned')),patch('companion.gui_models.infer',return_value=("Action: click(start_box='(70,42)')",140,84)):
            raw=computer_step(Mock(),'s',messages,schema={},image_data='image',targets=[target])
            self.assertEqual(raw['action']['label'],'Folder');self.assertEqual(raw['risk'],'navigation')
            from companion.computer_task import needs_review
            self.assertFalse(needs_review(raw['action'],raw['risk']))
            raw=computer_step(Mock(),'s',messages,schema={},image_data='image',targets=[dict(target,label='Send')])
            self.assertTrue(needs_review(raw['action'],raw['risk']))
            raw=computer_step(Mock(),'s',messages,schema={},image_data='image',targets=[])
            self.assertEqual(raw['risk'],'change')
