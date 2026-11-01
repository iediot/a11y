"""Her cursor: when she clicks, her window melts into a pointer, the pointer flies to the target on a
curved path (different every time) and a ripple marks the click. Then it stays where it clicked, the
size of the system's pointer, and the next click flies from there; she grows back into her window
beside it only when she has something to tell (form_back). All of it is painted in one click-through overlay over the screen; it never moves
the user's mouse and never takes a click (the real click is sent when the flight ends)."""
import ctypes
import math
import random
import sys

from PyQt6.QtCore import QPointF, QRectF, Qt, QVariantAnimation
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QWidget

MORPH_MS, CLICK_MS = 170, 120  # short: the click waits for the flight, and the screen can change meanwhile


def system_cursor_scale():
    """macOS's Pointer size (Accessibility → Display → Pointer), so her cursor grows with the user's;
    1.0 elsewhere or when it can't be read."""
    if sys.platform != 'darwin':
        return 1.0
    try:
        cf = ctypes.cdll.LoadLibrary('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        cf.CFStringCreateWithCString.restype = ctypes.c_void_p
        cf.CFStringCreateWithCString.argtypes = (ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32)
        cf.CFPreferencesCopyAppValue.restype = ctypes.c_void_p
        cf.CFPreferencesCopyAppValue.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
        cf.CFNumberGetValue.restype = ctypes.c_bool
        cf.CFNumberGetValue.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p)
        cf.CFRelease.argtypes = (ctypes.c_void_p,)
        utf8 = 0x08000100
        key = cf.CFStringCreateWithCString(None, b'mouseDriverCursorSize', utf8)
        domain = cf.CFStringCreateWithCString(None, b'com.apple.universalaccess', utf8)
        value = cf.CFPreferencesCopyAppValue(key, domain)
        scale = ctypes.c_double(1.0)
        if value:
            cf.CFNumberGetValue(value, 13, ctypes.byref(scale))  # kCFNumberDoubleType
            cf.CFRelease(value)
        cf.CFRelease(key)
        cf.CFRelease(domain)
        return max(1.0, min(4.0, scale.value))
    except (OSError, AttributeError, ValueError):
        return 1.0


# About the system arrow's height (17 pt): at most a hair bigger than the real pointer (owner).
CURSOR = 18*system_cursor_scale()


def flight_path(start, end, rng=random):
    """A hand's path from start to end, different every time: 2-3 waypoints thrown off the straight line
    at random distances and sides, a little tremor, and a speed that hesitates. The ends are exact.
    Returns a dict that `along` reads."""
    dx, dy = end.x()-start.x(), end.y()-start.y()
    distance = math.hypot(dx, dy) or 1.0
    nx, ny = -dy/distance, dx/distance  # across the line
    count = rng.randint(2, 3)
    # Spread along the way, always forward (loops looked broken, owner), each thrown off the line its own way.
    stops = [(k+rng.uniform(0.25, 0.75))/count*0.8+0.1 for k in range(count)]
    points = [start]
    for at in stops:
        throw = distance*rng.uniform(0.03, 0.17)*rng.choice((-1, 1))
        points.append(QPointF(start.x()+dx*at+nx*throw, start.y()+dy*at+ny*throw))
    points.append(end)
    tremor = [(rng.uniform(0.5, 1.6), rng.uniform(5, 13), rng.uniform(0, 2*math.pi)) for _ in range(3)]
    hesitation = (rng.uniform(0.03, 0.08), rng.uniform(1.5, 3.5), rng.uniform(0, 2*math.pi))
    return {'points': points, 'tremor': tremor, 'hesitation': hesitation, 'across': (nx, ny)}


def catmull(p0, p1, p2, p3, t):
    t2, t3 = t*t, t*t*t
    return QPointF(0.5*(2*p1.x()+(-p0.x()+p2.x())*t+(2*p0.x()-5*p1.x()+4*p2.x()-p3.x())*t2+(-p0.x()+3*p1.x()-3*p2.x()+p3.x())*t3),
                   0.5*(2*p1.y()+(-p0.y()+p2.y())*t+(2*p0.y()-5*p1.y()+4*p2.y()-p3.y())*t2+(-p0.y()+3*p1.y()-3*p2.y()+p3.y())*t3))


def along(path, t):
    """Where the pointer is at time t (0-1): eased, hesitating, through the waypoints, trembling a little."""
    t = max(0.0, min(1.0, t))
    amount, rate, phase = path['hesitation']
    u = max(0.0, min(1.0, ease(t)+amount*math.sin(math.pi*t)*math.sin(rate*math.pi*t+phase)))
    points = path['points']
    segments = len(points)-1
    index = min(int(u*segments), segments-1)
    local = u*segments-index
    p0, p1 = points[max(index-1, 0)], points[index]
    p2, p3 = points[index+1], points[min(index+2, segments)]
    at = catmull(p0, p1, p2, p3, local)
    fade = math.sin(math.pi*t)  # no tremor at either end: it lands exactly
    wobble = sum(size*math.sin(speed*t*2*math.pi+start) for size, speed, start in path['tremor'])*fade
    nx, ny = path['across']
    return QPointF(at.x()+nx*wobble, at.y()+ny*wobble)


def ease(t):
    """Slow start and end, no overshoot (she never overshoots, owner)."""
    return 4*t*t*t if t < 0.5 else 1-(-2*t+2)**3/2


def travel_ms(start, end):
    return int(max(260, min(600, 200+math.hypot(end.x()-start.x(), end.y()-start.y())*0.4)))


def pointer_path(tip, size=CURSOR):
    """The rounded, tail-less arrowhead (iPad / Codex style): tip at `tip`, pointing up and to the left,
    two equal wings around an axis tilted 30° from vertical, and a soft dip at the back."""
    s = size/22.0
    def toward(degrees, length):
        angle = math.radians(degrees)
        return QPointF(tip.x()+math.sin(angle)*length*s, tip.y()+math.cos(angle)*length*s)
    left, right = toward(8, 20.0), toward(52, 20.0)
    dip = toward(30, 11.5)
    path = QPainterPath(tip)
    path.lineTo(left)
    path.quadTo(dip, right)
    path.closeSubpath()
    return path


def pointer_colors(palette):
    """Her pointer in her own window's colours: its dark blue as the fill, her text colour as the rim."""
    return QColor(palette.background), QColor(palette.text)


def paint_pointer(p, tip, opacity, scale, fill_color, edge_color):
    """The pointer: one rounded outline (the arrowhead grown by a round-joined stroke), filled once,
    slightly see-through, with a thin rim and a soft shadow so it reads on light and dark."""
    if opacity <= 0.01:
        return
    from PyQt6.QtGui import QPainterPathStroker
    p.save()
    p.setOpacity(max(0.0, min(1.0, opacity)))
    stroker = QPainterPathStroker()
    stroker.setWidth(3.0*scale*CURSOR/22.0)
    stroker.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    stroker.setCapStyle(Qt.PenCapStyle.RoundCap)
    path = pointer_path(tip, CURSOR*scale)
    shape = path.united(stroker.createStroke(path)).simplified()
    shadow = QColor(0, 0, 0, 60)
    for offset in (2.2, 1.2):
        p.fillPath(shape.translated(0.6, offset), shadow)
    fill = QColor(fill_color)
    fill.setAlphaF(0.88)
    p.fillPath(shape, fill)
    edge = QColor(edge_color)
    edge.setAlphaF(0.9)
    p.setPen(QPen(edge, 1.3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(shape)
    p.restore()


class CursorFlight(QWidget):
    """One flight at a time, over the screen the flight is on."""

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.Tool | Qt.WindowType.WindowTransparentForInput
                            | Qt.WindowType.WindowDoesNotAcceptFocus | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow, True)
        self.setWindowTitle('A11y cursor')
        self.setAccessibleName('A11y cursor')
        self.fill, self.edge, self.card = QColor('#191D27'), QColor('#E0E0E0'), QColor('#191D27')
        self.snapshot = None
        self.phase, self.progress = None, 0.0
        self.start_rect = self.end_rect = QRectF()
        self.points = None
        self.animation = None
        self.done = None
        self.formed = []  # run when she has grown back into her window
        self._painted_bounds = QRectF()

    def set_colors(self, palette):
        """Her theme: the pointer in her window's dark colour, her text colour as its rim."""
        self.fill, self.edge = pointer_colors(palette)
        self.card = QColor(palette.background)

    def fly(self, screen_rect, start_rect, snapshot, target, end_rect, done):
        """start_rect / end_rect: her window before and after (global); target: the click point (global).
        `done` runs once, right after the click mark; the pointer then stays there (parked). From a parked
        pointer on the same screen the next flight starts where it sits, with no melt."""
        from_parked = self.phase == 'parked' and self.geometry() == screen_rect
        self.halt()
        self.done = done
        origin = QPointF(screen_rect.topLeft())
        if not from_parked:
            self.setGeometry(screen_rect)
            self.start_rect = QRectF(start_rect).translated(-origin)
            self.snapshot = snapshot
        self.end_rect = QRectF(end_rect).translated(-origin)
        tip = QPointF(target)-origin
        self.points = flight_path(self.last if from_parked else self.start_rect.center(), tip)
        self.first, self.last = self.points['points'][0], self.points['points'][-1]
        self.show()
        self.raise_()
        travel = lambda: self.run('travel', int(travel_ms(self.first, tip)*1.1),
                                  lambda: self.run('click', CLICK_MS, self.park))
        if from_parked:
            travel()
        else:
            self.run('melt', MORPH_MS, travel)

    def park(self):
        """The click is marked: the pointer sits at the point until the next flight or form_back."""
        self.phase, self.animation = 'parked', None
        self.update()
        done, self.done = self.done, None
        if done:
            done()

    def form_back(self, end_rect, formed):
        """Grow from the parked pointer back into her window at end_rect (global); `formed` runs after."""
        if self.phase not in ('parked', 'form'):
            formed()
            return
        self.formed.append(formed)
        if self.phase == 'parked':
            self.end_rect = QRectF(end_rect).translated(-QPointF(self.geometry().topLeft()))
            self.run('form', MORPH_MS, self.finish)

    def parked(self):
        """True while she is her cursor between clicks (or growing back from it)."""
        return self.phase in ('parked', 'form')

    def run(self, phase, duration, then):
        self.phase, self.progress = phase, 0.0
        animation = QVariantAnimation(self)
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)
        animation.setDuration(duration)
        animation.valueChanged.connect(self.step)
        animation.finished.connect(then)
        self.animation = animation
        animation.start()

    def step(self, value):
        self.progress = float(value)
        bounds = self.paint_bounds()
        damage = bounds.united(self._painted_bounds).adjusted(-12,-12,12,12).toAlignedRect()
        self._painted_bounds = bounds
        self.update(damage.intersected(self.rect()))

    def paint_bounds(self):
        """The current visual footprint; old and new footprints both need clearing."""
        if self.phase is None or self.points is None:
            return QRectF()
        tip = along(self.points,self.progress) if self.phase == 'travel' else self.first if self.phase == 'melt' else self.last
        bounds = pointer_path(tip,CURSOR).boundingRect()
        if self.phase == 'click':
            bounds = bounds.united(QRectF(tip.x()-20,tip.y()-20,40,40))
        elif self.phase == 'melt':
            bounds = bounds.united(self.shrink(self.start_rect,self.first,ease(self.progress)))
        elif self.phase == 'form':
            bounds = bounds.united(self.shrink(self.end_rect,self.last,1-ease(self.progress)))
        return bounds

    def finish(self):
        self.phase, self.animation = None, None
        self.hide()
        formed, self.formed = self.formed, []
        for then in formed:
            then()

    def halt(self):
        if self.animation is not None:
            self.animation.stop()
            self.animation = None

    def stop(self):
        """Cut a flight short (Stop pressed); its `done` does not run. Anyone waiting for her window
        to re-form gets it now."""
        self.halt()
        self.done, self.phase = None, None
        self.hide()
        formed, self.formed = self.formed, []
        for then in formed:
            then()

    def flying(self):
        return self.phase in ('melt', 'travel', 'click')

    # Painting

    def paintEvent(self, event):
        if self.phase is None or self.points is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        t = self.progress
        if self.phase == 'melt':   # her window shrinks into the pointer where she stood
            self.paint_window(p, self.shrink(self.start_rect, self.first, ease(t)), 1-t)
            self.paint_pointer(p, self.first, min(1.0, t*1.6), 0.5+0.5*t)
        elif self.phase == 'travel':
            self.paint_pointer(p, along(self.points, t), 1.0, 1.0)
        elif self.phase == 'click':  # a ripple where it clicks, the pointer pressing in
            tip = self.last
            ring = QColor(self.fill)
            ring.setAlphaF(0.55*(1-t))
            p.setPen(QPen(ring, 2.0))
            p.setBrush(Qt.BrushStyle.NoBrush)
            radius = 4+12*t
            p.drawEllipse(tip, radius, radius)
            self.paint_pointer(p, tip, 1.0, 1.0-0.12*math.sin(math.pi*t))
        elif self.phase == 'parked':  # sitting where it clicked, until the next step
            self.paint_pointer(p, self.last, 1.0, 1.0)
        elif self.phase == 'form':   # and grows back into her window beside the point
            self.paint_window(p, self.shrink(self.end_rect, self.last, 1-ease(t)), t)
            self.paint_pointer(p, self.last, 1-t, 1.0-0.5*t)

    @staticmethod
    def shrink(rect, point, amount):
        """rect pulled toward point: 0 is the rect, 1 a small square at the point."""
        small = QRectF(point.x()-8, point.y()-8, 16, 16)
        lerp = lambda a, b: a+(b-a)*amount
        return QRectF(lerp(rect.x(), small.x()), lerp(rect.y(), small.y()),
                      lerp(rect.width(), small.width()), lerp(rect.height(), small.height()))

    def paint_window(self, p, rect, opacity):
        if opacity <= 0.01 or rect.width() < 2:
            return
        p.save()
        p.setOpacity(max(0.0, min(1.0, opacity)))
        radius = min(10.0, rect.width()/4, rect.height()/4)
        clip = QPainterPath()
        clip.addRoundedRect(rect, radius, radius)
        p.fillPath(clip, self.card)
        if self.snapshot is not None and not self.snapshot.isNull():
            p.setClipPath(clip)
            p.drawPixmap(rect, self.snapshot, QRectF(self.snapshot.rect()))
            p.setClipping(False)
        edge = QColor(self.edge)
        edge.setAlphaF(0.25)
        p.setPen(QPen(edge, 1.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect, radius, radius)
        p.restore()

    def paint_pointer(self, p, tip, opacity, scale):
        paint_pointer(p, tip, opacity, scale, self.fill, self.edge)
