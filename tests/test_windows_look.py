"""The Windows 11 title bar and accents (theme.windows_look). Offscreen widgets only; no real apps."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from unittest.mock import patch
from PyQt6.QtCore import Qt, QPointF, QVariantAnimation
from PyQt6.QtGui import QEnterEvent
from PyQt6.QtWidgets import QApplication
from companion import theme
from companion.mascot import Mascot, CaptionButton, DotButton, HOVER_MS


def focus_order(mascot, buttons):
    names = {id(button): name for name, button in buttons.items()}
    order, widget = [], mascot
    for _ in range(500):
        widget = widget.nextInFocusChain()
        if id(widget) in names:
            order.append(names[id(widget)])
        if widget is mascot:
            break
    return order


def hover(button):
    button.enterEvent(QEnterEvent(QPointF(5, 5), QPointF(5, 5), QPointF(5, 5)))


class ThemeTests(unittest.TestCase):
    def test_preview_switch_turns_the_windows_look_on_elsewhere(self):
        with patch.dict(os.environ, {'A11Y_STYLE': 'windows'}), patch('companion.theme.native_mac', return_value=False):
            self.assertTrue(theme.windows_look())
            self.assertIn('#0078D4', theme.menu_style()+theme.panel_style())
            self.assertNotIn('#0a84ff', theme.menu_style()+theme.panel_style())

    def test_macos_never_takes_the_windows_look(self):
        with patch.dict(os.environ, {'A11Y_STYLE': 'windows'}), patch('companion.theme.native_mac', return_value=True):
            self.assertFalse(theme.windows_look())
            self.assertEqual(theme.panel_style(), theme.MAC_PANEL_STYLE)
            self.assertEqual(theme.menu_style(), theme.MENU_STYLE)

    def test_other_platforms_keep_the_existing_styles(self):
        env = {k: v for k, v in os.environ.items() if k != 'A11Y_STYLE'}
        with patch.dict(os.environ, env, clear=True), patch('companion.theme.native_mac', return_value=False), \
             patch('companion.theme.sys.platform', 'linux'):
            self.assertFalse(theme.windows_look())
            self.assertEqual(theme.menu_style(), theme.MENU_STYLE)
            self.assertEqual(theme.panel_style(), theme.PANEL_STYLE)


class WindowsTitleBarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def make(self, windows):
        with patch('companion.mascot.windows_look', return_value=windows), \
             patch('companion.theme.windows_look', return_value=windows):
            mascot = Mascot()
        self.addCleanup(mascot.deleteLater)
        return mascot

    def buttons(self, mascot):
        return {'close': mascot.close_dot, 'menu': mascot.menu_dot, 'settings': mascot.prompt_settings}

    def test_caption_buttons_are_named_focusable_and_in_visual_order(self):
        mascot = self.make(True)
        buttons = self.buttons(mascot)
        self.assertTrue(all(isinstance(b, CaptionButton) for b in buttons.values()))
        self.assertEqual({n: b.accessibleName() for n, b in buttons.items()},
                         {'close': 'Close the companion', 'menu': 'Companion actions',
                          'settings': 'Open companion settings'})
        for button in buttons.values():
            self.assertEqual(button.focusPolicy(), Qt.FocusPolicy.TabFocus)
            self.assertIs(button.parentWidget(), mascot.title_bar)
            self.assertFalse(button.isWindow())
        mascot.title_bar.layout().activate()
        xs = [buttons[n].geometry().x() for n in ('menu', 'settings', 'close')]
        self.assertEqual(xs, sorted(xs))
        self.assertLess(mascot.window_title.geometry().x(), xs[0])
        # Tab moves through them as they read, left to right.
        self.assertEqual(focus_order(mascot, buttons), ['menu', 'settings', 'close'])

    def test_caption_buttons_keep_the_dot_actions(self):
        mascot = self.make(True)
        actions, opened = [], []
        mascot.action_requested.connect(actions.append)
        mascot.opened.connect(lambda: opened.append(True))
        with patch.object(mascot, 'show_menu') as menu:
            mascot.menu_dot.click()
            menu.assert_called_once()
        mascot.prompt_settings.click()
        mascot.close_dot.click()
        self.assertEqual((actions, opened), (['quit'], [True]))

    def test_hover_fades_over_120_ms(self):
        button = self.make(True).close_dot
        hover(button)
        self.assertEqual(HOVER_MS, 120)
        self.assertEqual(button.fade.duration(), HOVER_MS)
        self.assertEqual(button.fade.state(), QVariantAnimation.State.Running)
        self.assertLess(button.hover, 1.)

    def test_reduced_motion_shows_and_hides_the_hover_at_once(self):
        mascot = self.make(True)
        mascot.set_reduced_motion(True)
        button = mascot.close_dot
        hover(button)
        self.assertEqual(button.fade.state(), QVariantAnimation.State.Stopped)
        self.assertEqual(button.hover, 1.)
        button.leaveEvent(None)
        self.assertEqual(button.fade.state(), QVariantAnimation.State.Stopped)
        self.assertEqual(button.hover, 0.)

    def test_turning_reduced_motion_on_mid_fade_jumps_to_the_end(self):
        mascot = self.make(True)
        button = mascot.menu_dot
        hover(button)
        self.assertEqual(button.fade.state(), QVariantAnimation.State.Running)
        mascot.set_reduced_motion(True)
        self.assertEqual(button.fade.state(), QVariantAnimation.State.Stopped)
        self.assertEqual(button.hover, 1.)

    def test_other_platforms_keep_the_three_dots_in_their_order(self):
        mascot = self.make(False)
        buttons = self.buttons(mascot)
        self.assertTrue(all(type(b) is DotButton for b in buttons.values()))
        mascot.title_bar.layout().activate()
        xs = [buttons[n].geometry().x() for n in ('close', 'menu', 'settings')]
        self.assertEqual(xs, sorted(xs))
        self.assertGreater(mascot.window_title.geometry().x(), xs[-1])
        self.assertEqual(focus_order(mascot, buttons), ['close', 'menu', 'settings'])
        mascot.set_reduced_motion(True)  # dots have no fade; nothing to stop


if __name__ == '__main__':
    unittest.main()
