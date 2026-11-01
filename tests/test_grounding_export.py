import unittest
from companion.eval_control import grounding_targets


class GroundingExportTests(unittest.TestCase):
    def test_crop_coordinates_are_normalized_and_no_field_values_or_ids_export(self):
        window=dict(x=100,y=200,width=400,height=300);screen=dict(x=0,y=0,width=1200,height=900)
        target=dict(label='Toolbar control',role='AXButton',bounds=dict(x=200,y=260,width=40,height=30),value='private',id='not-replayable')
        result=grounding_targets(dict(targets=[target,target]),screen,window)
        self.assertEqual(len(result),1);self.assertEqual(set(result[0]),{'label','role','box'})
        self.assertAlmostEqual(result[0]['box'][0],.25);self.assertAlmostEqual(result[0]['box'][1],.2)
    def test_clipped_and_other_window_targets_do_not_get_fabricated_boxes(self):
        screen=dict(x=0,y=0,width=100,height=100);window=dict(x=10,y=10,width=50,height=50)
        target=dict(label='Outside',bounds=dict(x=80,y=80,width=10,height=10))
        self.assertEqual(grounding_targets(dict(targets=[target]),screen,window),[])
        self.assertEqual(grounding_targets({},screen,dict(x=90,y=90,width=50,height=50)),[])
    def test_invalid_and_disabled_targets_are_excluded(self):
        window=dict(x=0,y=0,width=100,height=100)
        bad=[dict(label='Hidden',enabled=False,bounds=dict(x=1,y=1,width=2,height=2)),
             dict(label='NaN',bounds=dict(x=float('nan'),y=1,width=2,height=2))]
        self.assertEqual(grounding_targets(dict(targets=bad),window,window),[])
