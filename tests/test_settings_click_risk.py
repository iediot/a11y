import unittest
from unittest.mock import Mock
from companion.computer_task import ComputerTask


class SettingsClickRiskTests(unittest.TestCase):
    def test_content_click_cannot_self_declare_navigation(self):
        task = ComputerTask('Show appearance without changing it.', automatic=True, goal_mode='inspect')
        desktop = Mock()
        desktop.bridge.hit.return_value = dict(owner='com.apple.systempreferences',role='AXButton',title='Light',ancestor_roles=['AXGroup','AXScrollArea','AXWindow'])
        step = dict(status='action', message='Click.', risk='navigation', action=dict(type='click_point',x=.6,y=.3,label='Light'))
        snapshot = dict(app=dict(bundle_id='com.apple.systempreferences'))
        result = task.app_click(step,desktop,snapshot,dict(screen=dict(x=0,y=0,width=1000,height=800)))
        self.assertEqual(result['risk'],'change')
        self.assertTrue(task.review_needed(result['action'],result['risk'],snapshot))

    def test_native_sidebar_and_toolbar_navigation_stay_navigation(self):
        task = ComputerTask('Show a settings pane.',automatic=True,goal_mode='inspect')
        for ancestry in (['AXRow','AXOutline','AXScrollArea'],['AXToolbar','AXWindow']):
            desktop = Mock()
            desktop.bridge.hit.return_value = dict(owner='com.apple.systempreferences',role='AXButton',title='Pane',ancestor_roles=ancestry)
            step = dict(status='action',message='Click.',risk='navigation',action=dict(type='click_point',x=.2,y=.4,label='Pane'))
            result=task.app_click(step,desktop,dict(app=dict(bundle_id='com.apple.systempreferences')),dict(screen=dict(x=0,y=0,width=1000,height=800)))
            self.assertEqual(result['risk'],'navigation')
            self.assertFalse(task.review_needed(result['action'],result['risk'],dict(app=dict(bundle_id='com.apple.systempreferences')),navigation_verified=result.get('settings_navigation',False)))

    def test_direct_settings_control_without_structural_evidence_needs_confirmation(self):
        task=ComputerTask('Show settings.',automatic=True,goal_mode='inspect')
        self.assertTrue(task.review_needed(dict(type='press_control'),'navigation',dict(app=dict(bundle_id='com.apple.systempreferences'))))

    def test_unverified_pixel_click_in_settings_requires_confirmation(self):
        task=ComputerTask('Click an option.',automatic=True,goal_mode='inspect')
        self.assertTrue(task.review_needed(dict(type='click_point'),'navigation',dict(app=dict(bundle_id='com.apple.systempreferences'))))
