import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from PyQt6.QtCore import Qt,QPoint,QRect,QObject
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication
from companion.desktop import CompanionWindow
from companion.mascot import Mascot,Composer


class MotionHarness(QObject):
    animate_companion_to=CompanionWindow.animate_companion_to
    def __init__(self):
        super().__init__(); self.move_animation=None
        self.frames=[];self.finished=[]
        self.service=SimpleNamespace(identity=None,preferences=SimpleNamespace(effective=lambda _: {'reduced_motion':False}))
        self.mascot=SimpleNamespace(geometry=lambda:QRect(400,328,230,120),pos=lambda:QPoint(400,300),
            begin_stretch=lambda:None,stretch_to=self.frames.append,end_stretch=self.finished.append)


class MovementAndHistoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def test_horizontal_animation_keeps_native_client_frame_offset(self):
        harness=MotionHarness()
        harness.animate_companion_to(QPoint(100,300))
        QTest.qWait(700)
        self.assertTrue(harness.frames)
        self.assertTrue(all(rect.y()==328 for rect in harness.frames),[r.y() for r in harness.frames])
        self.assertEqual(harness.finished,[QPoint(100,300)])

    def test_begin_stretch_finishes_slides_without_refitting_the_window(self):
        mascot=Mascot()
        try:
            mascot.animations['drawer_open'].setStartValue(0.)
            mascot.animations['drawer_open'].setEndValue(1.)
            mascot.animations['drawer_open'].setDuration(500)
            mascot.animations['drawer_open'].start()
            with patch.object(mascot,'fit_on_screen') as fit:
                mascot.begin_stretch()
                fit.assert_not_called()
            self.assertEqual(mascot.drawer_open,1.)
        finally:mascot.close()

    def test_arrows_recall_only_sent_messages_and_restore_draft(self):
        mascot=Mascot();mascot.set_reduced_motion(True)
        try:
            for value in ('first request','second request'):
                mascot.input.setText(value);mascot.submit()
            mascot.input.setText('unsent draft')
            QTest.keyClick(mascot.input,Qt.Key.Key_Up)
            self.assertEqual(mascot.input.text(),'second request')
            QTest.keyClick(mascot.input,Qt.Key.Key_Up)
            self.assertEqual(mascot.input.text(),'first request')
            QTest.keyClick(mascot.input,Qt.Key.Key_Up)
            self.assertEqual(mascot.input.text(),'first request')
            QTest.keyClick(mascot.input,Qt.Key.Key_Down)
            self.assertEqual(mascot.input.text(),'second request')
            QTest.keyClick(mascot.input,Qt.Key.Key_Down)
            self.assertEqual(mascot.input.text(),'unsent draft')
        finally:mascot.close()

    def test_multiline_arrows_keep_editing_inside_text(self):
        field=Composer();field.show()
        try:
            field.remember('old request');field.setText('line one\nline two')
            cursor=field.textCursor();cursor.movePosition(cursor.MoveOperation.End);field.setTextCursor(cursor)
            QTest.keyClick(field,Qt.Key.Key_Up)
            self.assertEqual(field.text(),'line one\nline two')
            self.assertEqual(field.textCursor().blockNumber(),0)
            QTest.keyClick(field,Qt.Key.Key_Up)
            self.assertEqual(field.text(),'old request')
            QTest.keyClick(field,Qt.Key.Key_Down)
            self.assertEqual(field.text(),'line one\nline two')
        finally:field.close()

    def test_busy_or_setup_submissions_are_not_added_to_history(self):
        mascot=Mascot()
        try:
            mascot.set_busy(True);mascot.input.setText('not sent');mascot.submit()
            mascot.set_busy(False);mascot.input.setText('private setup answer');mascot.submit_action='setup';mascot.submit()
            mascot.input.setText('draft');QTest.keyClick(mascot.input,Qt.Key.Key_Up)
            self.assertEqual(mascot.input.text(),'draft')
        finally:mascot.close()

    def test_wrapped_lines_and_modified_arrows_remain_text_navigation(self):
        field=Composer();field.setFixedWidth(120);field.show()
        try:
            field.remember('old');draft='a long wrapped draft ' * 8;field.setText(draft)
            field.moveCursor(field.textCursor().MoveOperation.End);self.app.processEvents()
            QTest.keyClick(field,Qt.Key.Key_Up)
            self.assertEqual(field.text(),draft)
            field.moveCursor(field.textCursor().MoveOperation.End)
            QTest.keyClick(field,Qt.Key.Key_Up,Qt.KeyboardModifier.ShiftModifier)
            self.assertEqual(field.text(),draft)
            self.assertTrue(field.textCursor().hasSelection())
        finally:field.close()

    def test_history_is_bounded_and_edited_recall_does_not_replace_sent_text(self):
        field=Composer()
        try:
            for i in range(150):field.remember('request '+str(i))
            field.setText('draft')
            for _ in range(110):QTest.keyClick(field,Qt.Key.Key_Up)
            self.assertEqual(field.text(),'request 50')
            QTest.keyClicks(field,' edited')
            QTest.keyClick(field,Qt.Key.Key_Down);QTest.keyClick(field,Qt.Key.Key_Up)
            self.assertEqual(field.text(),'request 50')
        finally:field.close()

    def test_setup_field_cannot_recall_chat_messages(self):
        mascot=Mascot();mascot.set_reduced_motion(True)
        try:
            mascot.input.setText('old chat');mascot.submit()
            mascot.present('Setup',value='setup draft',submit_action='setup')
            QTest.keyClick(mascot.input,Qt.Key.Key_Up)
            self.assertEqual(mascot.input.text(),'setup draft')
        finally:mascot.close()

    def test_clearing_history_drops_recall_without_removing_current_draft(self):
        field=Composer()
        try:
            field.remember('old request');field.setText('draft')
            field.clear_sent_history()
            QTest.keyClick(field,Qt.Key.Key_Up)
            self.assertEqual(field.text(),'draft')
        finally:field.close()

    def test_mac_keypad_arrow_flag_recalls_history_and_restores_draft(self):
        from PyQt6.QtCore import QEvent
        from PyQt6.QtGui import QKeyEvent
        field=Composer()
        try:
            field.remember('sent message');field.setText('draft')
            self.app.sendEvent(field,QKeyEvent(QEvent.Type.KeyPress,Qt.Key.Key_Up,
                                              Qt.KeyboardModifier.KeypadModifier))
            self.assertEqual(field.text(),'sent message')
            self.app.sendEvent(field,QKeyEvent(QEvent.Type.KeyPress,Qt.Key.Key_Down,
                                              Qt.KeyboardModifier.KeypadModifier))
            self.assertEqual(field.text(),'draft')
        finally:field.close()

    def test_mac_modified_arrows_do_not_recall_history(self):
        from PyQt6.QtCore import QEvent
        from PyQt6.QtGui import QKeyEvent
        field=Composer()
        try:
            field.remember('sent message')
            for modifier in (Qt.KeyboardModifier.ShiftModifier,Qt.KeyboardModifier.ControlModifier,
                             Qt.KeyboardModifier.AltModifier,Qt.KeyboardModifier.MetaModifier):
                field.setText('draft')
                self.app.sendEvent(field,QKeyEvent(QEvent.Type.KeyPress,Qt.Key.Key_Up,
                                                  modifier | Qt.KeyboardModifier.KeypadModifier))
                self.assertEqual(field.text(),'draft')
        finally:field.close()
