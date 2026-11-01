import random
import unittest
from unittest.mock import patch
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QPointF,QRectF
from PyQt6.QtGui import QImage,QPainter
from companion import cursor_flight as cursor


class CursorDamageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def setUp(self):
        self.w=cursor.CursorFlight();self.w.setGeometry(0,0,1200,800)
        self.w.start_rect=QRectF(100,100,380,120);self.w.end_rect=QRectF(650,450,380,120)
        self.w.points=cursor.flight_path(QPointF(290,160),QPointF(900,600),random.Random(8))
        self.w.first=self.w.points['points'][0];self.w.last=self.w.points['points'][-1]
    def tearDown(self):self.w.stop();self.w.close()
    def test_dirty_bounds_cover_actual_rendered_pixels_all_phases_and_large_cursor(self):
        for scale in (18,72):
            with patch.object(cursor,'CURSOR',scale):
                for phase in ('melt','travel','click','parked','form'):
                    for progress in (.1,.5,.9):
                        with self.subTest(size=scale,phase=phase,t=progress):
                            self.w.phase=phase;self.w.progress=progress
                            im=QImage(self.w.size(),QImage.Format.Format_ARGB32_Premultiplied);im.fill(0)
                            p=QPainter(im);self.w.render(p);p.end()
                            rgba=im.convertToFormat(QImage.Format.Format_RGBA8888);bits=rgba.bits();bits.setsize(rgba.sizeInBytes())
                            positions=[i for i,a in enumerate(bytes(bits)[3::4]) if a]
                            bounds=self.w.paint_bounds().adjusted(-12,-12,12,12)
                            self.assertTrue(positions)
                            self.assertTrue(all(bounds.contains(QPointF(i%im.width(),i//im.width())) for i in positions))
    def test_small_motion_clears_old_and_new_pointer_without_screen_wide_invalidation(self):
        self.w.phase='travel';self.w.progress=.4;old=self.w.paint_bounds();self.w._painted_bounds=old
        with patch.object(self.w,'update') as update:
            self.w.step(.42);rect=update.call_args.args[0]
        self.assertTrue(QRectF(rect).contains(old));self.assertTrue(QRectF(rect).contains(self.w.paint_bounds()))
        self.assertLess(rect.width()*rect.height()/(1200*800),.02)
    def test_stop_never_executes_pending_action(self):
        from unittest.mock import Mock
        callback=Mock();self.w.done=callback;self.w.stop();callback.assert_not_called()
