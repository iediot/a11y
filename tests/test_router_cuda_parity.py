import importlib.util
import unittest


@unittest.skipUnless(importlib.util.find_spec('torch') and importlib.util.find_spec('transformers'),
                     'Numerical parity needs the separate torch/transformers tool environment')
class RouterCudaParityTests(unittest.TestCase):
    def test_selected_projection_matches_full_causal_loss_and_gradients(self):
        from scripts.router_cuda_parity import check
        result=check('cpu')
        self.assertTrue(result['passed'])
        self.assertEqual(len(result['cases']),8)
        self.assertTrue(all(case['gradient_tensors_checked']>0 for case in result['cases']))
