import unittest
from unittest.mock import patch, Mock
from companion.eval_control import PROHIBITED_TEST_APPS, guard_native_test_call
from companion.desktop_context import MacDesktopBridge


class EvalAppGuardTests(unittest.TestCase):
    def test_private_app_calls_are_blocked_before_native_preparation(self):
        bridge = MacDesktopBridge('/tmp/a11y-unit-unused')
        bridge.prepare = Mock()
        with patch('companion.eval_control.enabled', return_value=True):
            for bundle in PROHIBITED_TEST_APPS:
                for flag in ('--launch', '--activate', '--observe-app', '--menu'):
                    with self.assertRaisesRegex(RuntimeError, 'prohibited'):
                        bridge.call(flag, bundle)
        bridge.prepare.assert_not_called()

    def test_normal_use_and_safe_test_apps_remain_available(self):
        with patch('companion.eval_control.enabled', return_value=False):
            guard_native_test_call(('--launch', 'com.apple.mail'))
        with patch('companion.eval_control.enabled', return_value=True):
            guard_native_test_call(('--launch', 'com.apple.finder'))
            guard_native_test_call(('--targets',))

    def test_prohibited_dock_target_sends_no_click(self):
        import time
        from companion.vision import ScreenVision
        vision = ScreenVision('/tmp/a11y-unit-unused')
        vision.set_enabled(True)
        screen = dict(x=0, y=0, width=1000, height=800)
        guard = dict(screen=screen, display_id=1, windows=[], region_hash='same', captured_monotonic=time.monotonic())
        vision.capture = Mock(return_value={'image_data': 'synthetic'})
        vision.click_guard = Mock(return_value=guard)
        desktop = Mock()
        desktop.bridge.hit.return_value = {'role': 'AXDockItem', 'title': 'Photos', 'app': 'com.apple.Photos'}
        with patch('companion.eval_control.enabled', return_value=True), self.assertRaisesRegex(RuntimeError, 'prohibited'):
            vision.execute_click(None, desktop, dict(type='click_point', label='Photos', x=.2, y=.3, capture_guard=guard))
        desktop.bridge.click.assert_not_called()
