import sys
from pathlib import Path
import unittest
from unittest.mock import Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import live_grounding as ground


class GroundingSuiteTests(unittest.TestCase):
    def test_fresh_scene_seed_changes_positions_and_boxes_do_not_overlap(self):
        self.assertEqual(ground.scene(7,0),ground.scene(7,0));self.assertNotEqual(ground.scene(7,0),ground.scene(8,0))
        for seed in range(10):
            items=ground.scene(seed,0)
            for i,a in enumerate(items):
                for b in items[i+1:]:self.assertGreater(abs(a['x']-b['x']),a['radius']+b['radius'])
    def test_build_is_pure_and_uses_owned_local_fixture(self):
        window=Mock();cases=ground.build(window,seed=7);window.ensure.assert_not_called()
        self.assertEqual(len(cases),4);self.assertEqual(sum(len(c['turns']) for c in cases),12)
        self.assertTrue(all(t.wanted_app=='Safari' for c in cases for t in c['turns']))
        page=ground.html(ground.scene(7,0),'test-token')
        self.assertIn('aria-hidden="true"',page);self.assertNotIn('aria-label="compass"',page)
        self.assertIn("fetch('/hit?",page)
        self.assertIn('++selections',page)
