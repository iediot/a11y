import unittest
from unittest.mock import Mock,patch
from companion.desktop_context import browser_counts,SAFARI_COUNTS_SCRIPT
from companion.computer_task import ComputerTask


class BrowserCountsTests(unittest.TestCase):
    def snapshot(self):
        return dict(app=dict(bundle_id='com.apple.Safari',name='Safari'),window=dict(bounds=dict(x=10,y=20,width=800,height=600)))

    def test_counts_bound_to_observed_window_and_no_activation(self):
        with patch('companion.desktop_context.sys.platform','darwin'),patch('companion.desktop_context.subprocess.run',return_value=Mock(returncode=0,stdout='42\n3\n10\n20\n810\n620\n')) as native:
            self.assertEqual(browser_counts(self.snapshot()),dict(window_id=42,tab_count=3))
        self.assertNotIn('activate',SAFARI_COUNTS_SCRIPT)
        self.assertNotIn('URL',SAFARI_COUNTS_SCRIPT)
        self.assertEqual(native.call_args.kwargs['timeout'],1)

    def test_invalid_or_other_window_counts_are_unknown(self):
        for output in ('42\n3\n11\n20\n811\n621','42\n3\n100\n20\n900\n620','42\n0\n10\n20\n810\n620','42\n3','bad'):
            with patch('companion.desktop_context.sys.platform','darwin'),patch('companion.desktop_context.subprocess.run',return_value=Mock(returncode=0,stdout=output)):
                value=browser_counts(self.snapshot())
                if output.startswith('42\n3\n11'):self.assertEqual(value['tab_count'],3)
                else:self.assertEqual(value,{})

    def test_no_query_for_other_apps_platforms_or_invalid_geometry(self):
        for snap,platform in [(dict(app=dict(bundle_id='com.apple.mail')),'darwin'),(self.snapshot(),'win32'),(dict(self.snapshot(),window=dict(bounds=dict(x=float('nan'),y=0,width=10,height=10))),'darwin')]:
            with patch('companion.desktop_context.sys.platform',platform),patch('companion.desktop_context.subprocess.run') as native:
                self.assertEqual(browser_counts(snap),{})
                native.assert_not_called()

    def test_completion_quotes_real_counts_and_binds_delta_to_same_window(self):
        task=ComputerTask('Open one additional tab.',automatic=True,goal_mode='inspect')
        task.initial_browser_counts=dict(window_id=42,tab_count=1)
        snap=dict(self.snapshot(),browser=dict(window_id=42,tab_count=2))
        observed=task.completion_observation(snap,[])
        self.assertEqual(observed['facts']['browser_transition']['tab_delta'],1)
        snap['browser']['window_id']=43
        self.assertNotIn('browser_transition',task.completion_observation(snap,[])['facts'])
