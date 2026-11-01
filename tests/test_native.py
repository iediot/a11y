"""Actual Qt widget/controller tests, rendered offscreen; no desktop capture or AI network calls."""
import os
os.environ.setdefault('A11Y_WEB_ANSWERS', '0')  # tests never look things up online unless a test turns it on
os.environ.setdefault('A11Y_ROUTES', '0')  # nor read routes a live run remembered
import importlib.util
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
HAS_QT = importlib.util.find_spec('PyQt6') is not None
if HAS_QT:
    from PyQt6.QtCore import Qt, QPoint, QRect
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QApplication, QPushButton
    from companion import faces
    from companion.desktop import CompanionWindow
    from companion.mascot import MARGIN, TITLE_HEIGHT, SLEEP_AFTER_MS, REPLY_WIDTH, WIDTH
    from companion.terminal_theme import saved_appearance
    from companion.models import Identity
    from companion.service import Companion
    from companion.storage import Store
    from test_core import RecordingProvider


@unittest.skipUnless(HAS_QT, 'Install requirements.txt to run native Qt checks')
class NativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])
        cls.qt.setQuitOnLastWindowClosed(False)

    def setUp(self):
        # From source on macOS the installed-app lookup first compiles the Swift desktop helper
        # (up to a minute on CI), which stalled request workers past the tests' waits. Widget
        # tests never need the real catalog.
        mail = patch('companion.mail_reader.available', return_value=False)  # never drive the real Mail app
        mail.start()
        self.addCleanup(mail.stop)
        for name, kwargs in (('catalog', {'return_value': []}),
                             ('prepare', {'side_effect': RuntimeError('The desktop helper is not built in widget tests.')})):
            patcher = patch('companion.desktop_context.MacDesktopBridge.'+name, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.temp = tempfile.TemporaryDirectory()
        # Saved AI choices (ai.json) go to this test's folder, never the developer's .data.
        data = patch.dict(os.environ, {'A11Y_DATA_DIR': self.temp.name})
        data.start()
        self.addCleanup(data.stop)
        self.provider = RecordingProvider()
        self.service = Companion(Store(self.temp.name), Identity('company', 'employee'), self.provider)
        self.window = CompanionWindow(self.service)
        self.window.start_companion()
        self.qt.processEvents()
        # Most widget tests exercise a user-opened conversation, not startup.
        self.window.mascot.character.click()
        self.window.mascot.finish_animations()
        self.qt.processEvents()

    def tearDown(self):
        if self.window.worker:
            self.window.worker.wait(2000)
        self.window.finish_workers(120000)  # e.g. VisionPermission compiling the Swift helper on macOS
        self.window.desktop_monitor.disable()
        self.window.context_timer.stop()
        self.window.mascot.close()
        self.window.overlay.close()
        self.window.pointer.close()
        self.window.cursor_flight.stop()
        self.window.cursor_flight.close()
        self.window.quitting = True
        self.window.close()
        self.window.deleteLater()
        self.qt.processEvents()
        self.temp.cleanup()

    def choose(self, action):
        for i in range(self.window.mascot.choice_layout.count()):
            btn = self.window.mascot.choice_layout.itemAt(i).widget()
            if btn.property('companion_action') == action:
                QTest.mouseClick(btn, Qt.MouseButton.LeftButton)
                self.qt.processEvents()
                return
        self.fail('Missing mascot action: '+action)

    def test_mascot_stays_visible_when_mac_app_deactivates(self):
        self.assertTrue(self.window.mascot.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        self.assertTrue(self.window.mascot.testAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow))

    def test_context_menu_has_transparent_corners_and_keyboard_actions(self):
        from companion.mascot import RoundedMenu
        menu = RoundedMenu(self.window.mascot)
        menu.addAction('Talk to me')
        menu.addAction('Settings…')
        menu.show()
        self.qt.processEvents()
        image = menu.grab().toImage()
        self.assertEqual(image.pixelColor(0, 0).alpha(), 0)
        self.assertEqual(image.pixelColor(image.width()-1, image.height()-1).alpha(), 0)
        self.assertEqual(image.pixelColor(15, 15).alpha(), 255)
        menu.setActiveAction(menu.actions()[0])
        QTest.keyClick(menu, Qt.Key.Key_Down)
        self.assertEqual(menu.activeAction(), menu.actions()[1])
        menu.close()

    def test_opening_actions_execute_without_an_extra_confirmation_button(self):
        for kind in ('launch_app', 'browse_url', 'focus_app', 'new_window'):
            action = {'type': kind, 'bundle_id': 'com.apple.Safari', 'label': 'Open Safari'}
            response = {'message': 'Open Safari…', 'target': None, 'desktop_action': action}
            with patch.object(self.window, 'execute_desktop_action') as execute:
                self.window.received(response)
                self.qt.processEvents()
                execute.assert_called_once_with(action)
            self.assertIsNone(self.window.pending_desktop_action)

    def test_closing_runs_at_once_but_model_selected_presses_wait(self):
        # The user asked for app open/close/quit without a confirmation step.
        action = {'type': 'close_window', 'window_id': 'test:0', 'label': 'Close window'}
        with patch.object(self.window, 'execute_desktop_action') as execute:
            self.window.received({'message': 'Close window…', 'target': None, 'desktop_action': action})
            self.qt.processEvents()
            execute.assert_called_once_with(action)
        press = {'type': 'press_control', 'control_id': 'control-0', 'label': 'Delete'}
        with patch.object(self.window, 'execute_desktop_action') as execute:
            self.window.received({'message': 'Ready to press Delete. Confirm below.', 'target': None, 'desktop_action': press})
            self.qt.processEvents()
            execute.assert_not_called()
        self.assertEqual(self.window.pending_desktop_action, press)

    def test_visual_click_only_outlines_an_actual_target_rectangle(self):
        area = self.window.mascot.screen().geometry()
        frame = {'screen': {'x': area.x(), 'y': area.y(), 'width': area.width(), 'height': area.height()},
                 'captured_monotonic': time.monotonic()}
        response = {'message': 'Click Battery.', 'target': None,
                    'visual_capture': frame, 'task_step': True,
                    'desktop_action': {'type': 'click_point', 'x': .4, 'y': .5, 'label': 'Battery',
                                       'capture_guard': {'synthetic': True}}}
        with patch.object(self.window.overlay, 'guide') as guide:
            self.window.received(response)
            guide.assert_not_called()
            self.assertTrue(self.window.pointer.isVisible())
            target = {'label': 'Battery', 'x': .3, 'y': .4, 'width': .2, 'height': .2}
            self.window.received(dict(response, target=target))
            guide.assert_called_once_with(area, target)

    def test_remember_access_checkbox_is_explicit_and_pause_is_persisted(self):
        self.assertFalse(self.window.remember_access.isChecked())
        self.window.desktop_monitor.enabled = True
        self.window.vision.set_enabled(True)
        self.window.remember_access.setChecked(True)
        self.assertTrue(self.window.access_preferences.get()['screen_vision'])
        self.window.toggle_screen_vision()
        self.assertFalse(self.window.access_preferences.get()['screen_vision'])
        self.window.desktop_action('context:toggle')
        self.assertFalse(self.window.access_preferences.get()['app_awareness'])
        self.window.remember_access.setChecked(False)
        self.assertFalse(self.window.access_preferences.get()['remember'])

    def test_first_app_enable_remembers_and_pause_reenable_does_not_reprompt(self):
        def enable(**kwargs):
            self.window.desktop_monitor.enabled = True
        with patch.object(self.window, 'confirm_access', return_value=True) as consent, patch.object(self.window.desktop_monitor, 'enable', side_effect=enable):
            self.window.desktop_action('context:toggle')
            self.assertEqual(self.window.access_preferences.get(), {'remember': True, 'app_awareness': True, 'screen_vision': False})
            self.assertTrue(self.window.access_preferences.approved('app_awareness'))
            self.window.desktop_action('context:toggle')
            self.assertFalse(self.window.access_preferences.get()['app_awareness'])
            self.window.desktop_action('context:toggle')
            consent.assert_called_once()

    def test_declining_first_enable_does_not_remember_or_enable_anything(self):
        with patch.object(self.window, 'confirm_access', return_value=False), patch.object(self.window.desktop_monitor, 'enable') as enable:
            self.window.desktop_action('context:toggle')
            enable.assert_not_called()
        self.assertFalse(self.window.access_preferences.get()['remember'])
        self.assertFalse(self.window.access_preferences.approved('app_awareness'))

    def test_previous_consent_respects_explicit_session_only_choice(self):
        self.window.access_preferences.approve('app_awareness')
        self.window.access_preferences.save(remember=False)
        with patch.object(self.window, 'confirm_access') as consent, patch.object(self.window.desktop_monitor, 'enable'):
            self.window.desktop_action('context:toggle')
            consent.assert_not_called()
        self.assertFalse(self.window.access_preferences.get()['remember'])

    def test_startup_restoration_preflights_permissions_without_os_prompt(self):
        self.window.access_preferences.save(remember=True, app_awareness=True, screen_vision=True)
        from companion.ollama import OllamaProvider
        self.window.service.provider = OllamaProvider()
        with patch.object(self.window.desktop_monitor, 'enable') as enable, patch('companion.desktop.VisionPermission') as permission:
            self.window.restore_access()
            enable.assert_called_once_with(request_permission=False)
            self.assertFalse(permission.call_args.kwargs['request_permission'])
            permission.return_value.start.assert_called_once()
        self.window.vision_permission = None

    def test_claude_needs_a_key_and_one_time_cloud_consent(self):
        from companion.claude import ClaudeProvider
        with patch.dict(os.environ, {'A11Y_DATA_DIR': self.temp.name}), patch('companion.claude.saved_key', return_value=''):
            os.environ.pop('ANTHROPIC_API_KEY', None)
            with patch.object(self.window, 'confirm_access') as consent:
                self.window.use_claude()
                consent.assert_not_called()
            self.assertIn('API key', self.window.connection_status.text())
            self.window.claude_key.setText('sk-test')
            with patch.object(self.window, 'confirm_access', return_value=False):
                self.window.use_claude()
            self.assertNotIsInstance(self.window.service.provider, ClaudeProvider)
            with patch.object(self.window, 'confirm_access', return_value=True) as consent:
                self.window.use_claude()
                consent.assert_called_once()
                self.assertIn('Anthropic', consent.call_args.args[1])
            self.assertIsInstance(self.window.service.provider, ClaudeProvider)
            self.assertEqual(self.window.service.provider.config.key, 'sk-test')
            self.assertEqual(self.window.claude_key.text(), '')
            from companion.paths import saved_ai_settings
            self.assertEqual(saved_ai_settings()['provider'], 'claude')
            self.assertEqual(self.window.service.state()['ai']['provider'], 'claude')
            self.assertIn('Claude Sonnet', self.window.connection_summary.text())
            with patch.object(self.window, 'confirm_access') as consent:
                self.window.use_claude()  # already agreed: no second prompt
                consent.assert_not_called()
            self.window.forget_claude()
            self.assertNotIsInstance(self.window.service.provider, ClaudeProvider)
            self.assertEqual(saved_ai_settings()['provider'], 'ollama')
            # No background thread may outlive the window: on Windows a model scan started here
            # was still connecting when the window closed, and Qt aborted the whole process.
            self.qt.processEvents()
            from PyQt6.QtCore import QThread
            self.assertEqual([t for t in self.window.findChildren(QThread) if t.isRunning()], [])

    def test_local_screen_consent_never_covers_claude(self):
        from companion.claude import ClaudeProvider
        from companion.provider import AIConfig
        from companion.ollama import OllamaProvider
        self.window.access_preferences.approve('screen_vision')
        self.window.access_preferences.approve('cloud_ai')
        self.window.vision.set_enabled(True)
        self.window.claude_key.setText('sk-test')
        with patch.dict(os.environ, {'A11Y_DATA_DIR': self.temp.name}):
            self.window.use_claude()
        # Switching pauses vision that was allowed for the local model only.
        self.assertFalse(self.window.vision.enabled)
        self.assertFalse(self.window.vision.cloud_approved)
        with self.assertRaisesRegex(RuntimeError, 'only after you allow'):
            self.window.vision.enabled = True
            self.window.vision.capture(self.window.service.provider)
        self.window.vision.enabled = False
        # The setup window's local consent (consented=True) still asks about Claude.
        with patch.object(self.window, 'confirm_access', return_value=False) as consent, \
                patch('companion.desktop.VisionPermission') as permission:
            self.window.toggle_screen_vision(consented=True)
            consent.assert_called_once()
            self.assertIn('Anthropic', consent.call_args.args[1])
            permission.assert_not_called()
        with patch.object(self.window, 'confirm_access', return_value=True), \
                patch('companion.desktop.VisionPermission') as permission:
            self.window.toggle_screen_vision()
            permission.return_value.start.assert_called_once()
        self.assertTrue(self.window.vision.cloud_approved)
        self.assertTrue(self.window.access_preferences.approved('cloud_screen_vision'))
        self.window.vision_permission = None
        self.assertIsInstance(self.window.service.provider, ClaudeProvider)

    def test_startup_is_mascot_only_and_restoration_does_not_open_the_prompt(self):
        self.window.start_companion()
        self.qt.processEvents()
        mascot = self.window.mascot
        self.assertTrue(mascot.user_collapsed)
        self.assertFalse(mascot.card.isVisible())
        self.assertFalse(mascot.reply_panel.isVisible())
        self.window.screen_permission_result({'granted': True}, quiet=True)
        self.assertTrue(self.window.vision.enabled)
        self.assertFalse(mascot.card.isVisible())
        self.assertNotIn('Screen vision is on', mascot.bubble.toPlainText())
        mascot.character.click()
        self.qt.processEvents()
        self.assertTrue(mascot.card.isVisible())

    def test_background_permission_result_does_not_replace_an_open_conversation(self):
        self.window.mascot.present('Current task response', compact=True)
        self.window.screen_permission_result({'granted': True}, quiet=True)
        self.assertEqual(self.window.mascot.bubble.toPlainText(), 'Current task response')
        self.window.screen_permission_failed('Permission needs attention', quiet=True)
        self.assertFalse(self.window.vision.enabled)
        self.assertEqual(self.window.mascot.bubble.toPlainText(), 'Current task response')
        self.assertEqual(self.window.essentials_vision.toolTip(), 'Permission needs attention')

    def test_visual_pointer_is_independent_click_through_and_respects_reduced_motion(self):
        from PyQt6.QtGui import QCursor
        pointer = self.window.pointer
        geometry = self.window.mascot.screen().geometry()
        before = QCursor.pos()
        pointer.point_at(geometry, .2, .7, 'Example target', reduced_motion=True)
        from companion.overlay import TIP
        self.assertEqual(pointer.pos()+QPoint(TIP, TIP), QPoint(geometry.x()+int(.2*geometry.width()), geometry.y()+int(.7*geometry.height())))  # the tip on the point
        self.assertEqual(QCursor.pos(), before)
        self.assertTrue(pointer.windowFlags() & Qt.WindowType.WindowTransparentForInput)
        self.assertTrue(pointer.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus)
        self.assertEqual(pointer.accessibleDescription(), 'Example target')
        self.assertEqual(pointer.animation.state().name, 'Stopped')
        self.window.clear_guidance()
        self.assertFalse(pointer.isVisible())
        self.assertIsNone(pointer.target_point)

    def test_browser_request_runs_actual_workers_once_without_confirmation_or_false_failure(self):
        from test_request_plan import StagedProvider
        self.window.setup_dialog.reject()
        provider = self.service.provider = StagedProvider('browser_search', 'Safari', 'youtube.com')
        provider.interpret_request.return_value['browser_mode'] = 'window'
        receipt = {'type': 'browse_url', 'requested_url': 'https://youtube.com',
                   'native_result': {'submitted': True, 'tab_placement': 'window',
                                     'browser_observation': {'url': 'https://youtube.com/'}}}
        with patch.object(self.window.desktop_monitor, 'installed_apps', return_value=[{'name': 'Safari', 'bundle_id': 'com.apple.Safari'}]), patch('companion.desktop.ComputerUse.execute_confirmed', return_value=receipt) as execute:
            self.window.input.setPlainText('open a new safari window and search for youtube.com')
            self.window.ask(mode='assist', from_mascot=True)
            deadline = time.monotonic()+3
            while time.monotonic() < deadline:
                self.qt.processEvents()
                QTest.qWait(5)
                if 'browser reports' in self.window.mascot.bubble.toPlainText() and not self.window.worker.isRunning():
                    break
            self.qt.processEvents()
        execute.assert_called_once()
        action = execute.call_args.args[0]
        self.assertEqual(action['browser_mode'], 'window')
        self.assertEqual(action['url'], 'https://youtube.com')
        provider.interpret_request.assert_called_once()
        provider.complete_structured.assert_not_called()
        self.assertIsNone(self.window.pending_desktop_action)
        self.assertIn('Opened https://youtube.com', self.window.mascot.bubble.toPlainText())
        self.assertTrue(self.window.mascot.isVisible())

    def test_fast_task_reobserves_after_each_action_and_pauses_before_enter(self):
        import base64
        from copy import deepcopy
        from unittest.mock import Mock
        from PyQt6.QtCore import QByteArray, QBuffer, QIODevice
        from PyQt6.QtGui import QImage
        from companion.desktop_context import DesktopContext
        from test_desktop_context import FakeBridge
        from test_computer_task import context
        self.window.setup_dialog.reject()
        bridge = FakeBridge()
        bridge.value = context()
        monitor = self.window.desktop_monitor = self.service.desktop_context = DesktopContext(bridge, clock=lambda: 100)
        monitor.enabled = True
        monitor.collect_once()
        self.window.vision.set_enabled(True)
        area = self.window.mascot.screen().geometry()
        image = QImage(200, 120, QImage.Format.Format_RGB32)
        image.fill(Qt.GlobalColor.blue)
        png = QByteArray()
        buffer = QBuffer(png)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buffer, 'PNG')
        frame = {'image_data': 'data:image/png;base64,'+base64.b64encode(bytes(png)).decode(),
                 'screen': {'x': area.x(), 'y': area.y(), 'width': area.width(), 'height': area.height()},
                 'display_id': 7, 'captured_monotonic': time.monotonic(), 'windows': [],
                 'text_regions': [{'text': 'Search', 'x': .25, 'y': .35, 'width': .1, 'height': .1}]}
        actions = [{'type': 'click_point', 'x': .3, 'y': .4, 'label': 'Search'},
                   {'type': 'type_text', 'text': 'accessibility'},
                   {'type': 'scroll', 'direction': 'down'},
                   {'type': 'press_key', 'key': 'enter'}]
        outputs = [{'status': 'action', 'message': 'Next step on fresh screen.', 'risk': 'navigation', 'action': action} for action in actions]
        outputs.append({'status': 'done', 'message': 'The requested result is visible.', 'risk': 'navigation', 'action': None})
        outputs.append({'met': True, 'missing': '', 'evidence': 'Accessibility help'})
        class Provider:
            complete_structured = Mock(side_effect=outputs)
        self.service.provider = Provider()
        def submitted(proposal):
            return {'type': proposal['type'], 'label': proposal['label'], 'status': 'submitted'}
        def captured(*args, **kwargs):
            observed = deepcopy(frame)
            if execute.call_count >= 4:
                observed['text_regions'].append({'text': 'Accessibility help', 'x': .25, 'y': .6, 'width': .3, 'height': .1})
            return observed
        with patch.object(self.window.vision, 'capture', side_effect=captured) as capture, patch('companion.desktop.ComputerUse.execute_confirmed', side_effect=submitted) as execute:
            self.window.offer_computer_task('Find accessibility help')
            deadline = time.monotonic()+6
            while time.monotonic() < deadline:
                self.qt.processEvents()
                QTest.qWait(5)
                if (self.window.pending_desktop_action or {}).get('key') == 'enter' and not self.window.worker.isRunning():
                    break
            self.assertEqual(execute.call_count, 3)
            self.assertEqual(capture.call_count, 4)
            self.assertEqual(self.window.pending_desktop_action['key'], 'enter')
            self.window.desktop_action('desktop:apply')
            deadline = time.monotonic()+4
            while time.monotonic() < deadline:
                self.qt.processEvents()
                QTest.qWait(5)
                if self.window.computer_task.status == 'done' and not self.window.worker.isRunning():
                    break
            self.assertEqual(execute.call_count, 4)
            self.assertEqual(capture.call_count, 5)
            self.assertEqual(self.window.computer_task.status, 'done')
            self.assertEqual(len(self.window.computer_task.receipts), 4)
            while self.window.cursor_flight.parked() and time.monotonic() < deadline+2:
                QTest.qWait(20)  # she re-forms from her cursor to say it
            self.assertEqual('The requested result is visible.', self.window.mascot.bubble.toPlainText())
            for call in self.service.provider.complete_structured.call_args_list:
                self.assertIn('Find accessibility help', call.args[1][0]['content'])
                from companion.computer_task import MET_SYSTEM
                if call.args[0] == MET_SYSTEM:
                    self.assertIsNone(call.kwargs.get('image_data'))
                else:
                    self.assertTrue(call.kwargs['image_data'].startswith('data:image/png;base64,'))
        self.assertIsNone(self.window.pending_desktop_action)

    def test_cloud_messages_and_history_record_the_same_task_text(self):
        import base64
        from copy import deepcopy
        from unittest.mock import Mock
        from PyQt6.QtCore import QByteArray, QBuffer, QIODevice
        from PyQt6.QtGui import QImage
        from companion.desktop_context import DesktopContext
        from test_desktop_context import FakeBridge
        from test_computer_task import context
        self.window.setup_dialog.reject()
        bridge = FakeBridge()
        bridge.value = context()
        monitor = self.window.desktop_monitor = self.service.desktop_context = DesktopContext(bridge, clock=lambda: 100)
        monitor.enabled = True
        monitor.collect_once()
        self.window.vision.set_enabled(True)
        area = self.window.mascot.screen().geometry()
        image = QImage(200, 120, QImage.Format.Format_RGB32)
        image.fill(Qt.GlobalColor.blue)
        png = QByteArray()
        buffer = QBuffer(png)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buffer, 'PNG')
        frame = {'image_data': 'data:image/png;base64,'+base64.b64encode(bytes(png)).decode(),
                 'screen': {'x': area.x(), 'y': area.y(), 'width': area.width(), 'height': area.height()},
                 'display_id': 7, 'captured_monotonic': time.monotonic(), 'windows': []}
        outputs = [{'status': 'action', 'message': 'Search is visible; opening it.', 'risk': 'navigation',
                    'action': {'type': 'press_control', 'control_id': 'control-0'}},
                   {'status': 'done', 'message': 'Help results are listed.', 'risk': 'navigation', 'action': None},
                   {'status': 'done', 'message': 'Help results are listed.', 'risk': 'navigation', 'action': None}]
        class Provider:
            complete_structured = Mock(side_effect=outputs)
        self.service.provider = Provider()
        shown = []
        present = self.window.mascot.present
        def record(message, *args, **kwargs):
            shown.append(message)
            return present(message, *args, **kwargs)
        def submitted(proposal):
            return {'type': proposal['type'], 'label': proposal['label'], 'status': 'submitted'}
        start = len(self.window.transcript.toPlainText())
        with patch.object(self.window.vision, 'capture', side_effect=lambda *args, **kwargs: deepcopy(frame)), \
                patch('companion.desktop.ComputerUse.execute_confirmed', side_effect=submitted), \
                patch.object(self.window.mascot, 'present', side_effect=record):
            self.window.offer_computer_task('Find accessibility help')
            deadline = time.monotonic()+6
            while time.monotonic() < deadline:
                self.qt.processEvents()
                QTest.qWait(5)
                if self.window.computer_task.status == 'done' and not self.window.worker.isRunning():
                    break
            while self.window.cursor_flight.parked() and time.monotonic() < deadline+2:
                QTest.qWait(20)  # she re-forms from her cursor to say it
        history = self.window.transcript.toPlainText()[start:]
        recorded = [entry.strip() for entry in history.split('A11y: ')[1:]]
        # History keeps the answers; status lines are only shown while they are current.
        from companion.desktop import progress_message
        self.assertEqual(recorded, [message.strip() for message in shown if not progress_message(message)])
        self.assertTrue(any(message == 'New Folder…' for message in shown))
        self.assertIn('Help results are listed.', shown[-1])

    def test_live_teams_request_opens_safari_window_before_any_model_step(self):
        # Live (build e79f024ef6): the opening was resolved but dropped by the UI, so the
        # model clicked dock icons instead. Drive the user's sentence through the window.
        from copy import deepcopy
        from unittest.mock import Mock
        from companion.desktop_context import DesktopContext
        from test_desktop_context import FakeBridge
        from test_computer_task import context
        self.window.setup_dialog.reject()
        bridge = FakeBridge()
        bridge.value = context()
        bridge.catalog = Mock(return_value=[{'name': 'Safari', 'bundle_id': 'com.apple.Safari'}])
        monitor = self.window.desktop_monitor = self.service.desktop_context = DesktopContext(bridge, clock=lambda: 100)
        monitor.enabled = True
        monitor.collect_once()
        self.window.vision.set_enabled(True)
        area = self.window.mascot.screen().geometry()
        frame = {'image_data': 'data:image/png;base64,iVBORw0KGgo=', 'display_id': 7, 'windows': [],
                 'screen': {'x': area.x(), 'y': area.y(), 'width': area.width(), 'height': area.height()},
                 'captured_monotonic': time.monotonic(), 'text_regions': []}
        class Provider:
            interpret_request = Mock(side_effect=AssertionError('"open X" must not need the interpreter'))
            classify_task_goal = Mock(return_value='inspect')
            complete_structured = Mock(return_value={'status': 'done', 'message': 'Activity shows no new notifications.',
                                                     'risk': 'navigation', 'action': None})
        self.service.provider = Provider()
        executed = []
        def submitted(proposal):
            executed.append((proposal['type'], Provider.complete_structured.call_count))
            return {'type': proposal['type'], 'label': proposal['label'], 'status': 'submitted', 'native_result': {'submitted': True}}
        self.window.input.setPlainText('open up teams in a new safari window and see if ive got any new notifications')
        with patch.object(self.window.vision, 'capture', side_effect=lambda *a, **k: deepcopy(frame)), \
                patch('companion.desktop.ComputerUse.execute_confirmed', side_effect=submitted) as execute, \
                patch('companion.computer_task.time.sleep'):
            self.window.ask(mode='assist', from_mascot=True)
            deadline = time.monotonic()+6
            while time.monotonic() < deadline:
                self.qt.processEvents()
                QTest.qWait(5)
                task = self.window.computer_task
                if task and task.status == 'done' and not (self.window.worker and self.window.worker.isRunning()):
                    break
        opening = execute.call_args_list[0].args[0]
        self.assertEqual((opening['type'], opening['url'], opening['browser_mode']), ('browse_url', 'https://teams.microsoft.com', 'window'))
        self.assertEqual(executed[0], ('browse_url', 0))  # executed before any model step
        self.assertEqual(self.window.computer_task.status, 'done')
        Provider.interpret_request.assert_not_called()

    def test_a_changed_target_during_a_task_looks_again_instead_of_failing(self):
        from companion.computer_task import ComputerTask
        task = self.window.computer_task = ComputerTask('Find help', automatic=True)
        proposal = {'type': 'press_key', 'key': 'tab', 'label': 'Next field'}
        with patch('companion.desktop.ComputerUse.execute_confirmed',
                   side_effect=ValueError('The target or its window changed. No click was sent; ask for a fresh target.')), \
                patch.object(self.window, 'next_computer_step') as next_step:
            for attempt in range(3):
                self.window.execute_desktop_action(proposal)
                deadline = time.monotonic()+2
                while self.window.worker.isRunning() and time.monotonic() < deadline:
                    self.qt.processEvents()
                QTest.qWait(400)
                self.qt.processEvents()
        self.assertEqual(next_step.call_count, 2)  # two fresh looks, then the task stops
        self.assertEqual(task.status, 'blocked')
        self.assertFalse(task.receipts)

    def test_her_cursor_flies_on_a_fresh_curve_and_lands_exactly(self):
        import random
        from PyQt6.QtCore import QPointF
        from companion.cursor_flight import along, flight_path
        start, end = QPointF(100, 700), QPointF(900, 200)
        paths = [flight_path(start, end, random.Random(seed)) for seed in (1, 2, 3)]
        for path in paths:
            for t, point in ((0, start), (1, end)):
                landed = along(path, t)
                self.assertLess(abs(landed.x()-point.x())+abs(landed.y()-point.y()), 0.01)
        self.assertEqual(len({(round(along(p, .5).x()), round(along(p, .5).y())) for p in paths}), 3)  # different every time
        middle = [along(paths[0], k/20) for k in range(21)]  # not a straight line: it wanders off it
        straight = lambda q: abs((end.y()-start.y())*q.x()-(end.x()-start.x())*q.y()+end.x()*start.y()-end.y()*start.x())/((end-start).manhattanLength() or 1)
        self.assertGreater(max(straight(q) for q in middle), 10)

    def test_a_click_flight_lands_then_clicks_once_and_stop_cancels_it(self):
        from PyQt6.QtCore import QPoint, QRect
        from PyQt6.QtGui import QPixmap
        flight = self.window.cursor_flight
        landed = []
        self.window.pointer.show()  # a static pointer from an earlier step
        flight.fly(QRect(0, 0, 1440, 900), QRect(1100, 600, 230, 140), QPixmap(230, 140), QPoint(300, 200),
                   QRect(330, 130, 230, 140), lambda: landed.append(True))
        self.assertTrue(flight.flying())
        deadline = time.monotonic()+4
        while flight.flying() and time.monotonic() < deadline:
            QTest.qWait(30)
        self.assertEqual(landed, [True])
        flight.fly(QRect(0, 0, 1440, 900), QRect(1100, 600, 230, 140), QPixmap(230, 140), QPoint(300, 200),
                   QRect(330, 130, 230, 140), lambda: landed.append('late'))
        flight.stop()  # Stop pressed mid-flight: nothing is clicked
        QTest.qWait(50)
        self.assertEqual(landed, [True])
        self.assertFalse(flight.flying())

    def test_her_cursor_stays_between_steps_and_she_comes_back_to_speak(self):
        from PyQt6.QtCore import QPoint, QRect
        from PyQt6.QtGui import QPixmap
        from companion.cursor_flight import CURSOR, system_cursor_scale
        self.assertLessEqual(CURSOR/system_cursor_scale(), 17*1.1)  # about the real pointer's size (owner)
        flight, mascot = self.window.cursor_flight, self.window.mascot
        wait = lambda done: [QTest.qWait(30) for _ in range(150) if not done()]
        clicks = []
        flight.fly(QRect(0, 0, 1440, 900), QRect(1100, 600, 230, 140), QPixmap(230, 140), QPoint(300, 200),
                   QRect(330, 130, 230, 140), lambda: clicks.append(1))
        wait(lambda: clicks)
        self.assertTrue(flight.parked())
        self.assertTrue(flight.isVisible())       # it sits where it clicked
        flight.fly(QRect(0, 0, 1440, 900), QRect(330, 130, 230, 140), None, QPoint(700, 500),
                   QRect(760, 430, 230, 140), lambda: clicks.append(2))
        self.assertEqual(flight.phase, 'travel')  # from where it sat: no second melt
        self.assertLess(abs(flight.first.x()-300)+abs(flight.first.y()-200), 0.01)
        wait(lambda: len(clicks) == 2)
        mascot.hide()
        self.window.say('Step 3 · Looking at the screen…', [('Stop', 'computer:stop')], compact=True)
        self.assertFalse(mascot.isVisible())      # progress while she is her cursor
        self.window.say('Done.', compact=True)
        wait(lambda: mascot.isVisible())
        self.assertTrue(mascot.isVisible())       # a result: she is back in her window to say it
        self.assertFalse(flight.parked())
        self.assertFalse(flight.isVisible())

    def test_the_reply_opens_away_from_the_clicked_point(self):
        from PyQt6.QtCore import QPoint
        mascot = self.window.mascot
        area = mascot.screen().availableGeometry()
        mascot.move(area.center().x()-mascot.width()//2, area.center().y())
        mascot.avoid_point = QPoint(mascot.x()-40, mascot.y()+20)    # clicked left of her
        mascot.choose_reply_side()
        self.assertEqual(mascot.reply_side, 'right')
        mascot.avoid_point = QPoint(mascot.x()+mascot.width()+40, mascot.y())  # clicked right of her
        mascot.choose_reply_side()
        self.assertEqual(mascot.reply_side, 'left')
        mascot.avoid_point = None

    def test_reduced_motion_moves_her_without_a_flight(self):
        from PyQt6.QtCore import QPoint
        self.window.mascot.reduced_motion = True
        arrived = []
        try:
            self.window.fly_companion_to(QPoint(300, 200), QPoint(400, 150), lambda: arrived.append(True))
            deadline = time.monotonic()+3
            while not arrived and time.monotonic() < deadline:
                QTest.qWait(30)
            self.assertFalse(self.window.cursor_flight.flying())
            self.assertEqual(arrived, [True])
        finally:
            self.window.mascot.reduced_motion = False

    def test_companion_waits_beside_a_target_without_covering_it(self):
        from PyQt6.QtCore import QRect
        from companion.mascot import REPLY_WIDTH
        screen = QRect(0, 0, 1440, 900)
        size = self.window.mascot.size()
        for x in range(0, 1440, 60):
            for y in range(0, 900, 60):
                target = QPoint(x, y)
                spot = self.window.companion_spot(target, screen)
                if spot is not None:
                    covered = QRect(spot, size).adjusted(-24, -24, 24, 24)
                    self.assertFalse(covered.contains(target), (x, y))
                    # Reported: the reply window, open beside her on either side, took the click.
                    for side in (-REPLY_WIDTH, size.width()):
                        reply = QRect(spot.x()+side, spot.y(), REPLY_WIDTH, size.height())
                        self.assertFalse(reply.contains(target), (x, y, side))
                    self.assertTrue(screen.contains(QRect(spot, size)), (x, y))

    def test_task_action_waits_for_the_glide_and_stop_during_glide_sends_nothing(self):
        from companion.computer_task import ComputerTask
        area = self.window.mascot.screen().geometry()
        def step(task):
            action = {'type': 'press_control', 'control_id': 'control-0', 'label': 'Activity',
                      'bounds': {'x': area.x()+700, 'y': area.y()+300, 'width': 80, 'height': 30}}
            task.pending = action
            return {'status': 'action', 'message': 'Open Activity.', 'review': False, 'action': action, 'visual_capture': None}
        for stop_midway in (False, True):
            self.window.mascot.move(area.topLeft())  # start away from the target each round
            task = self.window.computer_task = ComputerTask('Find help', automatic=True)
            with patch.object(self.window, 'execute_desktop_action') as execute:
                self.window.receive_computer_step(task, step(task))
                QTest.qWait(60)
                self.qt.processEvents()
                execute.assert_not_called()  # still hopping
                if stop_midway:
                    self.window.mascot_action('computer:stop')
                deadline = time.monotonic()+3
                while time.monotonic() < deadline and not execute.called:
                    QTest.qWait(20)
                    self.qt.processEvents()
                self.assertEqual(execute.called, not stop_midway)

    def test_reduced_motion_disables_glides_lean_and_hops(self):
        character = self.window.mascot.character
        character.set_reduced_motion(True)
        character.hop()
        self.assertIsNone(character.hop_started)
        self.assertFalse(character.anim_timer.isActive())
        self.assertEqual(character.current_face(), faces.face(self.window.mascot.skin, 'idle', 0))
        with patch.object(self.service.preferences, 'effective', return_value={'reduced_motion': True}):
            done = []
            self.window.animate_companion_to(self.window.mascot.pos()+QPoint(300, 0), finished=lambda: done.append(1))
            self.qt.processEvents()
            QTest.qWait(10)
            self.qt.processEvents()
        self.assertEqual(done, [1])

    def test_setup_window_installs_only_after_set_up_and_then_uses_the_models(self):
        from companion.readiness_ui import ReadinessDialog
        status = {'ollama_installed': False, 'ollama_running': False, 'supported': True, 'text_model': 'qwen3:4b-instruct',
                  'vision_model': 'qwen3-vl:4b-instruct', 'text_ready': False, 'vision_ready': False}
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'A11Y_DATA_DIR': directory}), \
                patch('companion.installer.prepare_everything', return_value=('qwen3:4b-instruct', 'qwen3-vl:4b-instruct')) as prepare:
            dialog = ReadinessDialog(self.window, status)
            dialog.show()
            self.qt.processEvents()
            prepare.assert_not_called()  # nothing is installed until Set up
            dialog.start.click()
            deadline = time.monotonic()+3
            while dialog.isVisible() and time.monotonic() < deadline:
                QTest.qWait(20)
            prepare.assert_called_once()
            self.assertEqual(self.service.provider.config.model, 'qwen3:4b-instruct')
            self.assertEqual(self.service.provider.config.vision_model, 'qwen3-vl:4b-instruct')
            self.assertIn('qwen3-vl:4b-instruct', (Path(directory)/'ai.json').read_text())

    def test_stop_cancels_an_automatic_step_queued_for_execution(self):
        from companion.computer_task import ComputerTask
        task = self.window.computer_task = ComputerTask('Find help', automatic=True)
        area = self.window.mascot.screen().geometry()
        step = {'status': 'action', 'message': 'Focus the address bar.', 'review': False,
                'action': {'type': 'press_key', 'key': 'cmd_l', 'label': 'Address bar'},
                'visual_capture': {'screen': {'x': area.x(), 'y': area.y(), 'width': area.width(), 'height': area.height()}, 'captured_monotonic': time.monotonic()}}
        with patch.object(self.window, 'execute_desktop_action') as execute:
            self.window.receive_computer_step(task, step)
            self.window.mascot_action('computer:stop')
            self.qt.processEvents()
            execute.assert_not_called()
        self.assertTrue(task.cancelled.is_set())
        self.assertIsNone(self.window.pending_desktop_action)

    def test_task_refuses_automatic_click_when_capture_display_is_not_connected(self):
        from companion.computer_task import ComputerTask
        task = self.window.computer_task = ComputerTask('Find help', automatic=True)
        action = {'type': 'click_point', 'x': .3, 'y': .4, 'label': 'Help', 'capture_guard': {'synthetic': True}}
        task.pending = action
        step = {'status': 'action', 'message': 'Click Help.', 'review': False, 'action': action,
                'visual_capture': {'screen': {'x': 99999, 'y': 99999, 'width': 100, 'height': 100}, 'captured_monotonic': time.monotonic()}}
        with patch.object(self.window, 'execute_desktop_action') as execute:
            self.window.receive_computer_step(task, step)
            self.qt.processEvents()
            execute.assert_not_called()
        self.assertEqual(task.status, 'blocked')
        self.assertIsNone(self.window.pending_desktop_action)

    def test_task_request_starts_automatically_without_mode_selection(self):
        with patch.object(self.window, 'start_computer_task') as start:
            self.window.offer_computer_task('Check my tasks in Teams')
            choices = [self.window.mascot.choice_layout.itemAt(i).widget().property('companion_action') for i in range(self.window.mascot.choice_layout.count())]
            self.assertEqual(choices, ['computer:stop'])
            self.qt.processEvents()
            start.assert_called_once_with(automatic=True)

    def test_task_autostart_waits_for_request_worker_to_finish(self):
        from PyQt6.QtCore import QObject, pyqtSignal
        class Gate(QObject):
            finished = pyqtSignal()
            running = True
            def isRunning(self):
                return self.running
        gate = Gate()
        self.window.worker = gate
        try:
            with patch.object(self.window, 'start_computer_task') as start:
                self.window.offer_computer_task('Check Teams')
                self.qt.processEvents()
                start.assert_not_called()
                gate.running = False
                gate.finished.emit()
                self.qt.processEvents()
                start.assert_called_once_with(automatic=True)
        finally:
            self.window.worker = None

    def test_stopping_task_before_queued_start_prevents_it_from_starting(self):
        with patch.object(self.window, 'start_computer_task') as start:
            self.window.offer_computer_task('Check Teams')
            self.window.stop_computer_task()
            self.qt.processEvents()
            start.assert_not_called()

    def test_busy_prompt_and_stop_remain_visible_and_interactive(self):
        from companion.computer_task import ComputerTask
        task = self.window.computer_task = ComputerTask('Find help', automatic=True)
        mascot = self.window.mascot
        mascot.present('Observing…', [('Stop task', 'computer:stop')], compact=True)
        self.window.set_busy(True)
        self.qt.processEvents()
        self.assertTrue(mascot.input.isVisible())
        self.assertTrue(mascot.input.isEnabled())
        stop = mascot.choice_layout.itemAt(0).widget()
        self.assertTrue(stop.isEnabled())
        QTest.mouseClick(stop, Qt.MouseButton.LeftButton)
        self.assertTrue(task.cancelled.is_set())
        self.window.set_busy(False)

    def test_typing_interrupts_task_and_draft_survives_progress_updates(self):
        from companion.computer_task import ComputerTask
        task = self.window.computer_task = ComputerTask('Find help', automatic=True)
        mascot = self.window.mascot
        mascot.present('Working…', [('Stop task', 'computer:stop')], compact=True)
        self.window.set_busy(True)
        QTest.keyClicks(mascot.input, 'my next request')
        self.assertTrue(task.cancelled.is_set())
        mascot.present('A later update', placeholder='Ask anything…', compact=True)
        self.assertEqual(mascot.input.text(), 'my next request')
        self.assertFalse(mascot.send.isEnabled())
        self.window.set_busy(False)
        self.assertTrue(mascot.send.isEnabled())

    def test_clicking_pill_padding_focuses_input_without_starting_a_drag(self):
        mascot = self.window.mascot
        mascot.present('Ready', placeholder='Ask anything…', compact=True)
        self.qt.processEvents()
        QTest.mouseClick(mascot.card, Qt.MouseButton.LeftButton, pos=QPoint(3, 3))
        self.qt.processEvents()
        self.assertTrue(mascot.input.hasFocus())
        self.assertIsNone(mascot.drag_position)

    def test_sent_text_is_cleared_but_the_emitted_request_is_preserved(self):
        mascot = self.window.mascot
        mascot.submitted.disconnect()
        requests = []
        mascot.submitted.connect(lambda value, action: requests.append((value, action)))
        mascot.present('Ready', placeholder='Ask anything…', compact=True)
        mascot.input.setText('Open Teams')
        QTest.mouseClick(mascot.send, Qt.MouseButton.LeftButton)
        self.assertEqual(requests, [('Open Teams', 'chat')])
        self.assertEqual(mascot.input.text(), '')

    def test_long_reply_still_scrolls_from_keyboard(self):
        mascot = self.window.mascot
        mascot.present('\n'.join('Step '+str(i) for i in range(100)), compact=True)
        self.qt.processEvents()
        scrollbar = mascot.bubble.verticalScrollBar()
        self.assertGreater(scrollbar.maximum(), 0)
        mascot.bubble.setFocus()
        QTest.keyClick(mascot.bubble, Qt.Key.Key_PageDown)
        self.assertGreater(scrollbar.value(), 0)

    def test_restore_permission_worker_calls_check_instead_of_request(self):
        from companion.desktop import VisionPermission
        from unittest.mock import Mock
        vision = Mock()
        worker = VisionPermission(vision, request_permission=False)
        worker.run()
        vision.check_access.assert_called_once()
        vision.request_access.assert_not_called()

    def test_first_launch_greets_from_mascot_with_panel_hidden(self):
        self.assertFalse(self.window.isVisible())
        self.assertTrue(self.window.mascot.isVisible())
        self.assertIn('Hey!', self.window.mascot.bubble.toPlainText())
    def test_settings_close_leaves_companion_available(self):
        self.window.reveal()
        self.qt.processEvents()
        self.window.close()
        self.qt.processEvents()
        self.assertFalse(self.window.isVisible())
        self.assertTrue(self.window.mascot.isVisible())
        self.window.mascot.collapse_chat()
        self.qt.processEvents()
        self.assertFalse(self.window.mascot.card.isVisible())
        self.window.mascot.character.click()
        self.qt.processEvents()
        self.assertTrue(self.window.mascot.card.isVisible())

    def test_reported_clicking_her_always_opens_the_chat(self):
        # Reported: clicking her sometimes did not open the chat. Only the face opened it; her margins
        # started a drag, a release a pixel off the face did nothing, and a double click closed it again.
        mascot = self.window.mascot
        left = Qt.MouseButton.LeftButton
        margin = QPoint(4, mascot.character.geometry().center().y())
        for click in (lambda: QTest.mouseClick(mascot, left, pos=margin),
                      lambda: (QTest.mouseClick(mascot.character, left, pos=mascot.character.rect().center()),  # a real double click
                               QTest.mouseDClick(mascot.character, left, pos=mascot.character.rect().center()),
                               QTest.mouseRelease(mascot.character, left, pos=mascot.character.rect().center())),
                      lambda: (QTest.mousePress(mascot.character, left, pos=mascot.character.rect().center()),
                               QTest.mouseRelease(mascot.character, left, pos=QPoint(mascot.character.width()+1, 2)))):
            mascot.collapse_chat()
            self.qt.processEvents()
            self.assertFalse(mascot.card.isVisible())
            click()
            self.qt.processEvents()
            self.assertTrue(mascot.card.isVisible())
        start = mascot.pos()
        QTest.mouseClick(mascot, left, pos=margin)  # clicking her again keeps the chat open and her in place
        self.qt.processEvents()
        self.assertTrue(mascot.card.isVisible())
        self.assertEqual(mascot.pos(), start)

    def test_idle_is_character_only_and_keeps_character_position(self):
        mascot = self.window.mascot
        anchor = mascot.character.mapToGlobal(QPoint())
        mascot.collapse_chat()
        self.qt.processEvents()
        self.assertFalse(mascot.card.isVisible())
        # Collapsed: the title bar and the face, at the same width as when open.
        self.assertEqual(mascot.window_frame.width(), WIDTH)
        self.assertEqual(mascot.width(), WIDTH+2*MARGIN)
        self.assertLessEqual(mascot.height(), mascot.character.height()+TITLE_HEIGHT+2*MARGIN+22+mascot.status.height())  # room above the face, the status line
        self.assertEqual(mascot.character.mapToGlobal(QPoint()), anchor)
        self.assertFalse(any(b.isVisible() for b in mascot.findChildren(QPushButton)))
        mascot.character.click()
        self.qt.processEvents()
        self.assertTrue(mascot.card.isVisible())
        self.assertEqual(mascot.character.mapToGlobal(QPoint()), anchor)

    def test_reply_does_not_reopen_collapsed_conversation(self):
        mascot = self.window.mascot
        mascot.collapse_chat()
        mascot.present('A response arrived while you were working.', [('Continue', 'talk')], placeholder='Reply')
        self.qt.processEvents()
        self.assertFalse(mascot.card.isVisible())
        self.assertFalse(mascot.reply_panel.isVisible())
        self.assertEqual(mascot.width(), WIDTH+2*MARGIN)
        mascot.character.click()
        self.qt.processEvents()
        self.assertIn('A response arrived', mascot.bubble.toPlainText())
        self.assertTrue(mascot.card.isVisible())

    def test_settings_use_sidebar_and_do_not_compete_with_chat_bubble(self):
        self.window.reveal()
        self.qt.processEvents()
        self.assertFalse(self.window.tabs.tabBar().isVisible())
        self.assertEqual(self.window.panel_navigation.count(), self.window.tabs.count())
        self.assertFalse(self.window.mascot.card.isVisible())
        self.window.panel_navigation.setCurrentRow(self.window.navigation_pages.index(2))
        self.assertEqual(self.window.tabs.currentIndex(), 2)
        self.window.tabs.setCurrentIndex(4)
        self.assertEqual(self.window.panel_navigation.currentRow(), self.window.navigation_pages.index(4))
        self.window.hide()
        self.window.mascot_action('talk')
        self.qt.processEvents()
        self.assertTrue(self.window.mascot.card.isVisible())
        self.assertFalse(self.window.isVisible())

    def test_multiline_composer_enter_submits_shift_enter_adds_line(self):
        self.qt.processEvents()
        composer = self.window.mascot.input
        composer.setText('First line')
        QTest.keyClick(composer, Qt.Key.Key_End)
        QTest.keyClick(composer, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
        QTest.keyClicks(composer, 'Second line')
        self.assertIn('\nSecond line', composer.text())
        self.assertIsNone(self.window.worker)
        self.assertFalse(self.window.mascot.context_button.isVisible())
        self.assertFalse(self.window.mascot.vision_button.isVisible())
        self.assertTrue(self.window.mascot.prompt_settings.isVisible())

    def test_prompt_is_compact_and_menu_controls_are_not_in_normal_chat(self):
        mascot = self.window.mascot
        self.assertTrue(mascot.compact)
        self.assertEqual(mascot.window_frame.width(), WIDTH)
        self.assertEqual(mascot.width(), WIDTH+2*MARGIN)
        self.assertFalse(mascot.choice_area.isVisible())
        self.assertFalse(mascot.stage.isVisible())
        self.assertFalse(mascot.settings_button.isVisible())
        self.assertFalse(mascot.vision_button.isVisible())
        self.assertTrue(mascot.prompt_settings.isVisible())

    def test_enabled_vision_sends_fresh_pixels_from_worker(self):
        from companion.ollama import OllamaProvider
        from companion.provider import AIConfig
        from companion.ollama import OllamaProvider
        provider = OllamaProvider(AIConfig(base_url='http://127.0.0.1:11434', model='local-vision'))
        # Hermetic: never ask a real Ollama (CI images may run one) about this made-up model.
        provider.metadata['local-vision'] = {'capabilities': ['completion', 'vision']}
        self.service.provider = provider
        self.window.vision.set_enabled(True)
        frame = {'image_data': 'data:image/png;base64,ACTUAL_FRAME_TEST',
                 'screen': {'x': 0, 'y': 0, 'width': 800, 'height': 800},
                 'windows': [{'app': 'Xcode', 'title': 'Project'}], 'captured_monotonic': time.monotonic()}
        sent = []
        def describe(system, messages, *, schema, image_data=None, **options):
            sent.append(image_data)
            return {'message': 'Xcode is open on Project.'}
        # Hermetic: from source on macOS the installed-app lookup first compiles the Swift
        # desktop helper (up to a minute on CI); this test is about vision, not the app catalog.
        with patch.object(self.service.desktop_context, 'installed_apps', return_value=[]), \
                patch.object(self.service, 'ask', wraps=self.service.ask) as ask, \
                patch.object(self.window.vision, 'capture', return_value=frame), patch.object(provider, 'interpret_request', return_value={'operation': 'inspect_screen', 'app_name': '', 'target': ''}), patch.object(provider, 'complete_structured', side_effect=describe):
            self.window.mascot_submit('What is on my screen?', 'chat')
            deadline = time.monotonic()+15  # slow CI runners
            while self.window.worker.isRunning() and time.monotonic() < deadline:
                self.qt.processEvents()
                QTest.qWait(5)
            self.qt.processEvents()
        if not sent:
            import sys, traceback
            stacks = '\n'.join(''.join(traceback.format_stack(frame)) for frame in sys._current_frames().values())
            self.fail('no model call; the companion showed: '+repr(self.window.mascot.bubble.toPlainText()[:300])
                      +' worker running: '+str(bool(self.window.worker and self.window.worker.isRunning()))+'\nThreads:\n'+stacks[-6000:])
        self.assertEqual(sent, ['data:image/png;base64,ACTUAL_FRAME_TEST'])
        self.assertEqual(ask.call_args.kwargs['mode'], 'assist')  # mascot chat runs in assist mode
        self.assertIn('Xcode is open on Project.', self.window.mascot.bubble.toPlainText())
        self.assertTrue(self.window.mascot.compact)

    def test_a_finished_task_answer_is_remembered_for_follow_ups(self):
        from companion.computer_task import ComputerTask
        task = self.window.computer_task = ComputerTask('check my Teams notifications', automatic=True)
        self.window.receive_computer_step(task, {'status': 'done', 'message': 'Ana mentioned you in General.', 'action': None})
        self.assertEqual(self.service.conversation[-1]['content'], 'Ana mentioned you in General.')

    def test_capture_failure_does_not_ask_model_to_guess(self):
        self.window.vision.set_enabled(True)
        with patch.object(self.window.vision, 'capture', side_effect=RuntimeError('Screen recording permission denied')):
            self.window.mascot_submit('What is on screen?', 'chat')
            deadline = time.monotonic()+3
            while self.window.worker.isRunning() and time.monotonic() < deadline:
                self.qt.processEvents()
                QTest.qWait(5)
            self.qt.processEvents()
        self.assertEqual(self.provider.calls, [])
        self.assertIn('permission denied', self.window.mascot.bubble.toPlainText())

    def test_confirmed_move_action_repositions_only_the_companion(self):
        self.service.preferences.set_explicit(self.service.identity, {'reduced_motion': True})
        self.window.pending_desktop_action = {'type': 'move_companion', 'destination': 'left_edge', 'label': 'Move left'}
        self.window.desktop_action('desktop:apply')
        self.qt.processEvents()
        area = self.window.mascot.screen().availableGeometry()
        self.assertEqual(self.window.mascot.x(), area.left()+16)
        self.assertFalse(self.window.mascot.card.isVisible())
        self.assertFalse(self.window.isVisible())

    def test_terminal_window_holds_face_last_prompt_and_input(self):
        mascot = self.window.mascot
        self.assertFalse(mascot.reply_panel.isVisible())
        self.assertFalse(mascot.header_widget.isVisible())
        self.assertEqual(mascot.window_title.text(), 'a11y')
        line = mascot.input.fontMetrics().lineSpacing()
        self.assertLessEqual(abs(mascot.input.height()-line), 2)
        self.assertEqual(mascot.send.width(), mascot.send.height())
        def bounds(widget):
            return widget.mapTo(mascot, QPoint()), widget.size()
        send_pos, send_size = bounds(mascot.send)
        input_pos, input_size = bounds(mascot.input)
        entry_pos, entry_size = bounds(mascot.entry_row)
        character_pos, character_size = bounds(mascot.character)
        frame = mascot.window_frame.geometry()
        # Text, then send, inside the one input box, under the face.
        self.assertLess(input_pos.x()+input_size.width(), send_pos.x())
        self.assertLessEqual(send_pos.x()+send_size.width(), entry_pos.x()+entry_size.width())
        self.assertLess(character_pos.y()+character_size.height(), entry_pos.y())
        self.assertAlmostEqual(character_pos.x()+character_size.width()/2, frame.center().x(), delta=1,
                               msg=f'face {character_size.width()}px in a {frame.width()}px window; font '
                                   f'{mascot.character.font_.family()!r} {mascot.character.font_.pointSizeF()}pt, '
                                   f'cell {mascot.character.cell.width()}x{mascot.character.cell.height()}')
        self.assertEqual(len([b for b in mascot.findChildren(QPushButton) if b.isVisible()]), 1)
        # The input grows a line at a time, up to PROMPT_LINES, and back.
        height = mascot.height()
        mascot.input.setPlainText('Open Teams and read me every new notification in Activity, please. '*12)
        self.qt.processEvents()
        self.assertEqual(mascot.input.height(), mascot.PROMPT_LINES*line+2)
        self.assertGreater(mascot.height(), height)
        mascot.input.clear()
        self.qt.processEvents()
        self.assertEqual(mascot.height(), height)
        # Reported: one long word (a link) never wrapped, so the box stayed one line.
        mascot.input.setPlainText('https://example.com/'+'a'*150)
        self.qt.processEvents()
        self.assertGreater(mascot.input.height(), line+2)
        mascot.input.clear()
        mascot.input.setPlainText('what is the weather like?')
        QTest.keyClick(mascot.input, Qt.Key.Key_Return)
        self.qt.processEvents()
        self.assertTrue(mascot.last_prompt.isVisible())
        self.assertTrue(mascot.last_prompt.text().startswith('› what is the'))
        self.assertEqual(mascot.last_prompt.toolTip(), 'what is the weather like?')  # the whole of it

    def test_reply_window_lies_under_hers_and_shows_to_the_right(self):
        mascot = self.window.mascot
        area = mascot.screen().availableGeometry()
        mascot.move(area.left()+20, area.top()+40)              # room on her right
        self.window.mascot_home('Here is an answer.')
        mascot.finish_animations()
        self.qt.processEvents()
        self.assertTrue(mascot.reply_panel.isVisible())
        self.assertEqual(mascot.reply_side, 'right')
        mine, reply = mascot.window_frame.geometry(), mascot.reply_panel.geometry()
        self.assertEqual(reply.topLeft(), mine.topLeft())       # same top and left edges
        self.assertGreater(reply.right(), mine.right())         # sticks out to her right
        self.assertLess(reply.height(), mine.height())          # and is shorter
        children = mascot.children()
        self.assertLess(children.index(mascot.reply_panel), children.index(mascot.window_frame))  # drawn underneath
        text = mascot.bubble.mapTo(mascot, QPoint())
        self.assertGreater(text.x(), mine.right())              # text only in the part that shows
        self.assertEqual(mascot.width(), 2*MARGIN+reply.width())
        # By the right edge of the screen it opens to her left instead, and she stays put.
        mascot.hide_reply()
        mascot.move(area.right()-mascot.width()-4, area.top()+40)
        face = mascot.character.mapToGlobal(QPoint())
        self.window.mascot_home('Here is another answer.')
        mascot.finish_animations()
        self.qt.processEvents()
        self.assertEqual(mascot.reply_side, 'left')
        mine, reply = mascot.window_frame.geometry(), mascot.reply_panel.geometry()
        self.assertEqual((reply.top(), reply.right()), (mine.top(), mine.right()))
        self.assertLess(reply.left(), mine.left())
        self.assertLess(mascot.bubble.mapTo(mascot, QPoint()).x()+mascot.bubble.width(), mine.left())
        self.assertEqual(mascot.character.mapToGlobal(QPoint()), face)
        self.assertLessEqual(mascot.geometry().right(), area.right())
        self.assertEqual(mascot.bubble.toPlainText(), 'Here is another answer.')
        mascot.collapse_chat()
        mascot.character.click()
        self.qt.processEvents()
        self.assertTrue(mascot.card.isVisible())
        self.assertFalse(mascot.reply_panel.isVisible())

    def test_opening_and_the_reply_slide_instead_of_jumping(self):
        mascot = self.window.mascot
        mascot.set_reduced_motion(False)
        mascot.collapse_chat(animate=True)
        self.assertTrue(mascot.card.isVisible())                 # still sliding shut
        mascot.finish_animations()
        self.assertFalse(mascot.card.isVisible())
        closed = mascot.height()
        mascot.character.click()
        self.qt.processEvents()
        self.assertLess(mascot.height(), closed+mascot.card.height())  # opening, not open yet
        deadline = time.monotonic()+2
        while mascot.animations['drawer_open'].state() == mascot.animations['drawer_open'].State.Running and time.monotonic() < deadline:
            QTest.qWait(20)
        self.assertEqual(mascot.drawer.height(), mascot.card.height())
        mascot.present('Here it is.', compact=True)
        self.assertLess(mascot.reply_panel.width(), mascot.window_frame.width()+REPLY_WIDTH)  # sliding out
        mascot.finish_animations()
        self.assertEqual(mascot.reply_panel.width(), mascot.window_frame.width()+REPLY_WIDTH)
        mascot.set_reduced_motion(True)
        mascot.collapse_chat(animate=True)
        self.assertFalse(mascot.card.isVisible())                # reduced motion: no slide

    def test_reply_types_out_but_its_whole_text_is_there_at_once(self):
        mascot = self.window.mascot
        mascot.set_reduced_motion(False)
        message = 'Fourteen degrees and cloudy, with light rain after six.'
        mascot.present(message, compact=True)
        self.assertEqual(mascot.bubble.toPlainText(), message)  # screen readers get it all now
        self.assertTrue(mascot.typing.isActive())
        deadline = time.monotonic()+3
        while mascot.typed == 0 and mascot.typing.isActive() and time.monotonic() < deadline:
            QTest.qWait(20)
        self.assertGreater(mascot.typed, 0)  # the real Qt timeout is connected
        # Drive the remaining ticks without depending on background event-loop speed.
        for _ in range(len(message)+1):
            if not mascot.typing.isActive():
                break
            mascot.type_more()
        self.assertEqual(mascot.typed, len(message))
        self.assertFalse(mascot.typing.isActive())
        self.assertEqual(mascot.bubble.toPlainText(), message)
        mascot.set_reduced_motion(True)
        mascot.present('Still.', compact=True)
        self.assertFalse(mascot.typing.isActive())

    def test_moving_stretches_toward_the_spot_then_settles_there(self):
        mascot = self.window.mascot
        mascot.collapse_chat()
        self.qt.processEvents()
        with patch.object(self.service.preferences, 'effective', return_value={'reduced_motion': False}):
            start = QRect(mascot.geometry())
            target = mascot.pos()-QPoint(300, 0)
            done, widest = [], [0]
            self.window.animate_companion_to(target, finished=lambda: done.append(1))
            # Inspect the actual widget at the stretch peak. Under background CPU
            # load Qt can skip that transient frame between wall-clock samples.
            # Keep the real timer/finished signal for the remaining settle phase.
            animation = self.window.move_animation
            animation.pause()
            animation.setCurrentTime(animation.duration()//2)
            widest[0] = mascot.width()
            self.assertTrue(mascot.stretching)
            animation.resume()
            deadline = time.monotonic()+3
            while not done and time.monotonic() < deadline:
                QTest.qWait(10)
                widest[0] = max(widest[0], mascot.width())
        self.assertEqual(done, [1])
        self.assertGreaterEqual(widest[0], start.width()+250)  # one big stretch across
        self.assertEqual(mascot.pos(), target)
        self.assertEqual(mascot.size(), start.size())          # back to her normal size
        self.assertFalse(mascot.stretching)

    def test_grouped_menus_cover_flat_actions_and_refresh_state(self):
        from PyQt6.QtWidgets import QMenu
        mascot = self.window.mascot
        def leaves(entries):
            return [leaf for entry in entries if entry is not None
                    for leaf in (leaves(entry[1]) if isinstance(entry[1], list) else [entry])]
        grouped = mascot.grouped_menu_entries()
        self.assertEqual(set(grouped), {'A11y', 'Chat', 'View'})
        self.assertCountEqual(leaves(mascot.menu_entries()), leaves([entry for group in grouped.values() for entry in group]))
        actions = [entry[1] for entry in leaves(mascot.menu_entries())]
        self.assertEqual(set(actions), {'talk', 'reply', 'computer:prompt', 'computer:stop',
                                      'settings', 'setup:wizard', 'readiness', 'quit', 'vision:toggle', 'voice:toggle'} |
                         {'face:'+key for key in faces.SKIN_NAMES})
        menu = QMenu()
        mascot.fill_menu(menu, grouped['View'])
        mascot.voice_on = True
        mascot.skin = 'robot'
        mascot.refresh_menu(menu, mascot.grouped_menu_entries()['View'])
        self.assertEqual(menu.actions()[-1].text(), 'Turn off voice')
        checked = [item.text() for item in menu.actions()[0].menu().actions() if item.isChecked()]
        self.assertEqual(checked, [faces.SKIN_NAMES['robot']])
        menu.deleteLater()

    def test_mac_menu_roles_shortcuts_and_action_strings(self):
        from PyQt6.QtGui import QAction
        from PyQt6.QtWidgets import QMenu
        mascot = self.window.mascot
        menu = QMenu('A11y')
        mascot.fill_menu(menu, mascot.grouped_menu_entries()['A11y'], native_roles=True)
        settings, personalize, readiness, quit_item = menu.actions()
        self.assertEqual(settings.menuRole(), QAction.MenuRole.PreferencesRole)
        self.assertEqual(quit_item.menuRole(), QAction.MenuRole.QuitRole)
        self.assertFalse(settings.shortcut().isEmpty())
        self.assertFalse(quit_item.shortcut().isEmpty())
        emitted = []
        mascot.action_requested.disconnect(self.window.mascot_action)
        self.addCleanup(lambda: mascot.action_requested.connect(self.window.mascot_action))
        mascot.action_requested.connect(emitted.append)
        for item in (settings, personalize, readiness):
            item.trigger()
        self.assertEqual(emitted, ['settings', 'setup:wizard', 'readiness'])
        menu.deleteLater()

    def test_native_reply_title_keeps_emoji_after_right_padding(self):
        from companion.mascot import Mascot
        from PyQt6.QtWidgets import QWidget
        from types import SimpleNamespace
        # Call the layout method with a stand-in: no native handles are created.
        reply = QWidget()
        owner = SimpleNamespace(reply_window=reply, reply_heading='reply', reply_side='right',
                                frame_width=lambda: 240)
        Mascot.title_reply(owner)
        self.assertTrue(reply.windowTitle().startswith('\u2003'))
        self.assertTrue(reply.windowTitle().endswith('💬 reply'))
        owner.reply_side = 'left'
        Mascot.title_reply(owner)
        self.assertEqual(reply.windowTitle(), '💬 reply')
        reply.deleteLater()

    def test_native_surface_draws_black_rule_under_title_bar(self):
        from companion.mascot import Surface
        from PyQt6.QtGui import QColor
        surface = Surface(self.window.mascot, 'probe', system=True)
        surface.resize(80, 40)
        image = surface.grab().toImage()
        self.assertEqual(QColor(image.pixel(40, 0)).getRgb()[:3], (0, 0, 0))
        self.assertNotEqual(QColor(image.pixel(40, 5)).getRgb()[:3], (0, 0, 0))
        surface.deleteLater()

    def test_offscreen_user_clicks_never_call_appkit(self):
        mascot = self.window.mascot
        self.assertFalse(mascot.native)
        with patch('companion.mac_window.activate_app') as activate, patch('companion.mac_window.make_key') as key:
            mascot.open_chat_from_user()
            mascot.toggle_chat()
            mascot.set_proxy_icon(mascot)
            mascot.minimize_pair()
            mascot.style_titlebar()
        activate.assert_not_called()
        key.assert_not_called()

    def test_face_can_be_changed_from_her_menu_and_is_remembered(self):
        mascot = self.window.mascot
        self.window.mascot_action('face:robot')
        self.assertEqual(mascot.skin, 'robot')
        self.assertEqual(saved_appearance()['face'], 'robot')
        self.assertEqual(self.window.face_select.currentData(), 'robot')
        columns, lines = faces.size('robot')
        self.assertGreaterEqual(mascot.character.width(), columns*mascot.character.cell.width())
        self.window.mascot_action('face:no-such-face')
        self.assertEqual(mascot.skin, 'robot')
        # Reported: changing faces resized her and moved her around.
        size, face = mascot.size(), mascot.character.mapToGlobal(QPoint())
        for key in faces.SKINS:
            self.window.mascot_action('face:'+key)
            self.qt.processEvents()
            self.assertEqual((mascot.size(), mascot.character.mapToGlobal(QPoint())), (size, face), key)

    def test_reported_thinking_does_not_resize_or_move_her(self):
        # Reported: "thinking" appearing under the face resized her.
        mascot = self.window.mascot
        for collapsed in (False, True):
            mascot.collapse_chat() if collapsed else mascot.expand_chat()
            mascot.finish_animations()
            self.qt.processEvents()
            size, face = mascot.size(), mascot.character.mapToGlobal(QPoint())
            for state in ('thinking', 'guiding', 'idle'):
                mascot.set_state(state)
                self.qt.processEvents()
                self.assertEqual((mascot.size(), mascot.character.mapToGlobal(QPoint())), (size, face), state)

    def test_reported_a_confirmed_note_carries_on_with_the_rest(self):
        # Live (v3): "make a note … then read it back to me" created the note and stopped.
        from PyQt6.QtCore import QObject, pyqtSignal
        class Writer(QObject):
            result, failed, finished = pyqtSignal(str), pyqtSignal(str), pyqtSignal()
            def __init__(self, bridge, action, parent):
                super().__init__(parent)
            def start(self):
                self.result.emit('Created the note “A11y test 1651”.')
                self.finished.emit()
        self.window.pending_desktop_action = {'type': 'app_write', 'kind': 'create_note', 'title': 'A11y test 1651', 'body': 'x',
                                              'label': 'Create note', 'remaining_request': 'read it back to me'}
        with patch('companion.desktop.AppWriteWorker', Writer), patch.object(self.window, 'mascot_submit') as submit:
            self.window.desktop_action('desktop:apply')
            QTest.qWait(20)
            self.qt.processEvents()
        submit.assert_called_once_with('read it back to me (the note “A11y test 1651”)', 'chat')

    def test_red_button_closes_the_companion(self):
        mascot = self.window.mascot
        with patch.object(self.window, 'request_quit') as quit:
            mascot.close_dot.click()
        quit.assert_called_once()

    def test_reported_voice_restored_at_startup_fails_quietly(self):
        # Reported: on startup she said she couldn't hear the microphone, unasked.
        mascot = self.window.mascot
        mascot.collapse_chat()
        self.window.voice_asked = False
        with patch.object(self.window, 'speak_now') as speak:
            self.window.voice_failed('I can’t hear anything from the microphone.')
        speak.assert_not_called()
        self.assertFalse(mascot.card.isVisible())
        self.assertIn('microphone', mascot.input.placeholderText())
        self.window.voice_asked = True  # turned on by hand: then she says it
        with patch.object(self.window, 'speak_now') as speak:
            self.window.voice_failed('I can’t hear anything from the microphone.')
        speak.assert_called_once()
        self.assertIn('microphone', mascot.bubble.toPlainText())

    def test_she_falls_asleep_when_left_alone(self):
        character = self.window.mascot.character
        character.set_reduced_motion(False)
        character.set_state('idle')
        character.last_activity = -SLEEP_AFTER_MS-1
        self.assertIn(character.current_face(), faces.SKINS[self.window.mascot.skin]['sleeping'])
        self.window.mascot.expand_chat()
        self.assertNotIn(character.current_face(), faces.SKINS[self.window.mascot.skin]['sleeping'])

    def test_unconfigured_ai_error_is_shown_inside_mascot(self):
        from companion.provider import AIConfig
        from companion.ollama import OllamaProvider
        self.service.provider = OllamaProvider(AIConfig())
        self.qt.processEvents()
        self.window.mascot.input.setText('Hello')
        QTest.keyClick(self.window.mascot.input, Qt.Key.Key_Return)
        deadline = time.monotonic()+3
        while self.window.worker.isRunning() and time.monotonic() < deadline:
            self.qt.processEvents()
            QTest.qWait(5)
        self.qt.processEvents()
        self.assertIn('Select a local model in Settings', self.window.mascot.bubble.toPlainText())
        self.assertFalse(self.window.mascot.busy)
        self.assertFalse(self.window.isVisible())


    def test_guided_setup_saves_details_and_finishes_without_enabling_permissions(self):
        wizard = self.window.setup_dialog
        self.assertTrue(wizard.isVisible())
        wizard.advance()
        wizard.fields['display_name'].setText('Sam')
        wizard.fields['work_role'].setText('Engineer')
        wizard.advance()
        self.assertEqual(self.window.introduction.user_details(), {})
        wizard.fields['detail'].setCurrentIndex(wizard.fields['detail'].findData('concise'))
        wizard.advance()
        self.assertEqual(self.window.introduction.user_details()['display_name'], 'Sam')
        self.assertEqual(self.service.preferences.effective(self.service.identity)['detail'], 'concise')
        wizard.advance()
        wizard.advance()
        self.assertFalse(wizard.isVisible())
        self.assertTrue(self.window.introduction.get()['access_setup_reviewed'])
        self.assertFalse(self.window.desktop_monitor.enabled)
        self.assertFalse(self.window.vision.enabled)
        self.assertEqual(self.provider.calls, [])

    def test_setup_permission_buttons_use_real_consent_paths(self):
        wizard = self.window.setup_dialog
        with patch.object(self.window, 'desktop_action') as context:
            wizard.context_button.click()
            context.assert_called_once_with('context:toggle', consented=True)
        with patch.object(self.window, 'toggle_screen_vision') as vision:
            wizard.vision_button.click()
            vision.assert_called_once_with(consented=True)
        self.assertFalse(self.window.vision.enabled)

    def test_settings_default_to_essentials_with_advanced_pages_hidden(self):
        self.assertEqual(self.window.tabs.currentIndex(), self.window.essentials_tab)
        self.assertEqual(self.window.panel_navigation.item(0).text(), 'Settings')
        for index in self.window.advanced_pages:
            self.assertTrue(self.window.panel_navigation.item(self.window.navigation_pages.index(index)).isHidden())
        self.window.advanced_navigation.setChecked(True)
        for index in self.window.advanced_pages:
            self.assertFalse(self.window.panel_navigation.item(self.window.navigation_pages.index(index)).isHidden())

    def test_settings_resize_without_a_bottom_status_strip(self):
        from PyQt6.QtWidgets import QScrollArea
        self.window.reveal()
        self.window.resize(620, 460)
        self.qt.processEvents()
        self.assertEqual((self.window.width(), self.window.height()), (620, 460))
        self.assertFalse(self.window.statusBar().isVisible())
        self.window.statusBar().showMessage('Saved')
        self.assertFalse(self.window.statusBar().isVisible())
        for index in range(self.window.tabs.count()):
            self.assertIsInstance(self.window.tabs.widget(index), QScrollArea)

    def test_companion_text_is_identical_in_history_and_cloud_without_duplicates(self):
        self.window.say('Reading Teams activity.', compact=True)
        self.window.say('Reading Teams activity.', compact=True)
        self.assertEqual(self.window.mascot.bubble.toPlainText(), 'Reading Teams activity.')
        self.assertEqual(self.window.transcript.toPlainText().count('A11y: Reading Teams activity.'), 1)
        self.window.failed('Could not read the page.')
        message = self.window.mascot.bubble.toPlainText()
        self.assertIn('A11y: '+message, self.window.transcript.toPlainText())
        self.assertNotIn('progress is saved', message)

    def test_own_desktop_tools_highlight_uses_visible_qt_bounds_without_minimizing(self):
        with patch.object(self.window.overlay, 'guide') as guide:
            self.window.show_companion_control(str(self.window.context_tab))
            self.qt.processEvents()
            self.assertTrue(self.window.isVisible())
            self.assertTrue(self.window.advanced_navigation.isChecked())
            guide.assert_called_once()
            self.assertEqual(guide.call_args.args[1]['label'], 'Desktop tools')
            self.assertIn('Highlighted Desktop tools', self.window.mascot.bubble.toPlainText())

    def test_own_setting_request_runs_worker_and_highlights_without_screen_capture(self):
        from test_request_plan import StagedProvider
        provider = self.service.provider = StagedProvider('highlight', 'A11y', 'Desktop tools')
        provider.complete_structured.return_value = {'control_id': str(self.window.context_tab)}
        self.window.input.setPlainText('mark me where Desktop tools are in A11y')
        with patch.object(self.window.vision, 'capture') as capture, patch.object(self.window.overlay, 'guide') as guide:
            self.window.ask(from_mascot=True)
            deadline = time.monotonic()+15  # slow CI runners
            while time.monotonic() < deadline and not guide.called:
                self.qt.processEvents()
                QTest.qWait(5)
            capture.assert_not_called()
            guide.assert_called_once()
            self.assertEqual(guide.call_args.args[1]['label'], 'Desktop tools')
            self.assertTrue(self.window.isVisible())

    def test_desktop_diagnostics_are_collapsed_and_keyboard_accessible(self):
        self.window.reveal()
        self.window.tabs.setCurrentIndex(self.window.context_tab)
        self.assertTrue(self.window.desktop_diagnostics.content.isHidden())
        toggle = self.window.desktop_diagnostics.toggle
        toggle.setFocus()
        QTest.keyClick(toggle, Qt.Key.Key_Space)
        self.assertFalse(self.window.desktop_diagnostics.content.isHidden())
        self.assertEqual(toggle.accessibleDescription(), 'Expanded')
        self.assertTrue(self.window.history_tools.content.isHidden())

    def test_model_picker_applies_without_opening_technical_fields(self):
        self.window.connection_details.toggle.setChecked(False)
        self.window.local_models.addItem('local-test-model', 'local-test-model')
        self.window.local_models.setCurrentIndex(self.window.local_models.count()-1)
        self.window.connect_btn.click()
        self.assertEqual(self.service.provider.config.model, 'local-test-model')
        self.assertTrue(self.window.connection_details.content.isHidden())
        self.assertEqual(self.window.connection_summary.text(), 'local-test-model')


    def test_setup_access_action_has_no_duplicate_confirmation(self):
        wizard = self.window.setup_dialog
        with patch.object(self.window, 'confirm_access') as confirm, patch.object(self.window.desktop_monitor, 'enable') as enable:
            wizard.context_button.click()
            enable.assert_called_once()
            confirm.assert_not_called()

    def test_settings_access_can_be_declined_without_enabling(self):
        with patch.object(self.window, 'confirm_access', return_value=False) as confirm, patch.object(self.window.desktop_monitor, 'enable') as enable:
            self.window.desktop_action('context:toggle')
            confirm.assert_called_once()
            enable.assert_not_called()


    def test_app_change_waits_for_the_button_then_runs_once(self):
        from test_mac_apps import FakeBridge as AppsBridge
        apps = AppsBridge()
        self.window.desktop_monitor.bridge.add_reminder = apps.add_reminder
        self.service.provider.interpret_request = lambda request, recent=(), context=None: {
            'operation': 'add_reminder', 'app_name': '', 'target': 'Call mom | 2030-01-02T18:00', 'reply': '',
            'browser_mode': 'default', 'remaining_request': ''}
        with patch('companion.mac_apps.available', return_value=True):
            self.window.mascot_submit('remind me to call mom', 'chat')
            for _ in range(300):
                self.qt.processEvents()
                QTest.qWait(5)
                if not self.window.worker.isRunning():
                    break
            self.qt.processEvents()
            self.assertEqual(self.window.pending_desktop_action['kind'], 'add_reminder')
            self.assertEqual(apps.calls, [])  # nothing written before the button
            choices = [self.window.mascot.choice_layout.itemAt(i).widget().text() for i in range(self.window.mascot.choice_layout.count())]
            self.assertIn('Add reminder “Call mom” for Wednesday 02 January at 18:00', choices)
            self.window.mascot_action('desktop:apply')
            self.window.app_writer.wait(3000)
            self.qt.processEvents()
        self.assertEqual([c[:2] for c in apps.calls], [('add_reminder', 'Call mom')])
        self.assertEqual(self.window.mascot.bubble.toPlainText(), 'Added the reminder to Reminders.')
        self.assertIsNone(self.window.pending_desktop_action)

    def test_left_then_right_requests_move_without_reusing_old_target(self):
        self.service.preferences.set_explicit(self.service.identity, {'reduced_motion': True})
        self.service.desktop_session.append({'role': 'assistant',
            'content': 'The left Terminal close button is red.'})
        for request, destination in [('move to the left of the screen', 'left_edge'),
                                     ('move to the right of the screen', 'right_edge')]:
            self.provider.invalid = {'message': 'I’ll move to that edge.', 'target': None,
                'desktop_action': {'type': 'move_companion', 'destination': destination}}
            self.window.mascot_submit(request, 'chat')
            deadline = time.monotonic()+3
            while self.window.worker.isRunning() and time.monotonic() < deadline:
                self.qt.processEvents()
                QTest.qWait(5)
            self.qt.processEvents()
            area = self.window.mascot.screen().availableGeometry()
            expected = area.left()+16 if destination == 'left_edge' else area.right()-self.window.mascot.width()-16
            self.assertEqual(self.window.mascot.x(), expected)
            self.assertIsNone(self.window.pending_target)
            self.assertIsNone(self.window.pending_desktop_action)
        self.assertEqual(self.provider.calls, [])  # plain edge moves are decided in code, no model call


    def test_upper_left_changes_both_axes_and_precise_point_centers_character(self):
        self.service.preferences.set_explicit(self.service.identity, {'reduced_motion': True})
        mascot = self.window.mascot
        self.window.move_companion('bottom_right')
        self.window.move_companion('top_left')
        area = mascot.screen().availableGeometry()
        self.assertEqual(mascot.pos(), QPoint(area.left()+16, area.top()+16))
        self.window.move_companion_to(.5, .5, mascot.screen())
        full = mascot.screen().geometry()
        self.assertAlmostEqual(mascot.x()+mascot.width()/2, full.center().x(), delta=1)
        self.assertAlmostEqual(mascot.y()+mascot.height()/2, full.center().y(), delta=2)

    def test_visual_point_response_uses_its_capture_and_rejects_expired_capture(self):
        self.service.preferences.set_explicit(self.service.identity, {'reduced_motion': True})
        screen = self.window.mascot.screen()
        area = screen.geometry()
        response = {'message': 'Moving toward the drawing.', 'target': None, 'desktop_action': {'type': 'move_to_point', 'basis': 'image',
                'x': .5, 'y': .5, 'label': 'Apple drawing'},
            'visual_capture': {'screen': {'x': area.x(), 'y': area.y(), 'width': area.width(), 'height': area.height()},
                               'captured_monotonic': time.monotonic()}}
        self.window.received(response)
        mascot = self.window.mascot
        self.assertAlmostEqual(mascot.x()+mascot.width()/2, area.center().x(), delta=1)
        old = mascot.pos()
        response['desktop_action']['x'] = .1
        response['visual_capture']['captured_monotonic'] -= 100
        self.window.received(response)
        self.assertIn('expired', mascot.bubble.toPlainText())
        self.assertEqual(mascot.pos(), old)


    def test_confirmed_app_action_runs_worker_then_verifies_without_another_action(self):
        self.window.setup_dialog.reject()
        self.window.last_user_request = ('Press New Folder', 'assist', True)
        result = {'type': 'press_control', 'label': 'New Folder', 'status': 'submitted', 'task_completed': False}
        self.provider.invalid = {'request_kind': 'answer', 'message': 'The app changed; please check the folder.',
            'target': None, 'desktop_action': None}
        with patch('companion.desktop.ComputerUse.execute_confirmed', return_value=result) as execute:
            self.window.pending_desktop_action = {'type': 'press_control', 'control_id': 'control-0', 'label': 'New Folder'}
            self.window.desktop_action('desktop:apply')
            deadline = time.monotonic()+3
            while time.monotonic() < deadline:
                self.qt.processEvents()
                QTest.qWait(5)
                if self.provider.calls and not self.window.worker.isRunning():
                    break
            self.qt.processEvents()
        execute.assert_called_once()
        self.assertEqual(self.provider.contexts[-1]['mode'], 'verify')
        self.assertEqual(self.provider.contexts[-1]['last_action_result']['status'], 'submitted')
        self.assertIn('app changed', self.window.mascot.bubble.toPlainText())
        self.assertTrue(self.window.mascot.reply_panel.isVisible())
        self.assertIsNone(self.window.pending_desktop_action)


    def test_post_action_verification_can_use_metadata_when_screen_capture_fails(self):
        self.window.vision.set_enabled(True)
        self.service.last_action_result = {'type': 'focus_app', 'status': 'submitted', 'task_completed': False}
        self.provider.invalid = {'request_kind': 'answer', 'message': 'I can inspect app metadata, but screen capture is unavailable.',
            'target': None, 'desktop_action': None}
        with patch.object(self.window.vision, 'capture', side_effect=RuntimeError('Companion exclusion unavailable')):
            self.window.input.setPlainText('Open Terminal')
            self.window.ask(mode='verify', from_mascot=True)
            deadline = time.monotonic()+3
            while self.window.worker.isRunning() and time.monotonic() < deadline:
                self.qt.processEvents()
                QTest.qWait(5)
            self.qt.processEvents()
        self.assertIsNone(self.provider.calls[-1][2])
        self.assertEqual(self.provider.contexts[-1]['visual_context']['status'], 'unavailable')
        self.assertFalse(self.provider.contexts[-1]['evidence']['fresh_image'])
        self.assertIsNone(self.window.pending_desktop_action)
