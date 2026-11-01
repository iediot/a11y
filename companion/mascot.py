"""The companion: a small terminal window named a11y with a text face.

Her window holds the face, the last prompt and the input. Replies open in a second,
shorter window that lies underneath hers, sharing her top and left edges, so only the part
to her right shows. Both are drawn in one frameless window, so they move together and the
stacking never depends on the window manager. Colours and font follow the user's terminal
(terminal_theme); faces come from faces.py and are interchangeable.
"""
import os
import sys
from .theme import MENU_STYLE, native_mac
from . import faces, mac_window
from .paths import resource_dir
from .terminal_theme import palette as load_palette, saved_appearance
from PyQt6.QtWidgets import (
    QApplication, QWidget, QFrame, QVBoxLayout, QHBoxLayout, QPushButton,
    QPlainTextEdit, QLabel, QAbstractButton, QMenu, QMenuBar, QScrollArea, QGraphicsDropShadowEffect)
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtCore import (Qt, QTimer, QPoint, QPointF, QRectF, QElapsedTimer, pyqtSignal, QRect, QSize, QEvent,
                          QVariantAnimation, QEasingCurve)
from PyQt6.QtGui import (QPainter, QPainterPath, QColor, QPen, QShortcut, QKeySequence, QFontMetrics, QFontMetricsF,
                         QIcon, QPixmap, QAction, QTextLayout, QTextOption, QFont, QFontDatabase, QRawFont, QTextCursor, QTextCharFormat)

MARGIN = 10          # room for her shadow around the windows
TITLE_HEIGHT = 28
RADIUS = 11
WIDTH = 230          # her window, open or collapsed
SEE_THROUGH = 0.82   # her windows show this much of the terminal's opacity: a bit more see-through
REPLY_WIDTH = 280    # how far the reply window shows beside her
TYPE_STEP_MS = 16
TYPE_CHARS = 3       # ~190 characters a second
SLEEP_AFTER_MS = 10*60*1000
OPEN_MS = 220       # the input sliding open under the face
REPLY_OPEN_MS = 260  # the reply window sliding out from under her


def monospace_font(name='', size=12.0):
    """The terminal's font when Qt knows it (Terminal stores PostScript names such as
    JetBrainsMonoNF-SemiBold), otherwise the first installed monospace family."""
    families = QFontDatabase.families()
    weight = QFont.Weight.Normal
    if name:
        base, _, style = name.partition('-')
        weight = {'thin': QFont.Weight.Thin, 'light': QFont.Weight.Light, 'medium': QFont.Weight.Medium,
                  'semibold': QFont.Weight.DemiBold, 'bold': QFont.Weight.Bold}.get(style.lower(), QFont.Weight.Normal)
        squash = lambda text: text.lower().replace(' ', '').replace('nerdfont', 'nf')
        wanted = squash(base)
        matches = [family for family in families if squash(family) == wanted or wanted.startswith(squash(family))]
        if not matches:
            matches = [family for family in families if squash(family).startswith(wanted)]
        if matches:
            name = max(matches, key=len)
        elif name not in families:
            name = ''
    if not name:
        name = next((family for family in ('JetBrainsMono Nerd Font', 'JetBrains Mono', 'SF Mono', 'Menlo',
                                           'Cascadia Mono', 'Consolas') if family in families),
                    QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont).family())
    font = QFont(name)
    font.setStyleHint(QFont.StyleHint.Monospace)
    font.setPointSizeF(max(10.0, min(18.0, size)))
    font.setWeight(weight)
    return font


def rgba(color, alpha):
    value = QColor(color)
    return f'rgba({value.red()},{value.green()},{value.blue()},{alpha})'


def mascot_style(p, family, size):
    small = max(9.0, size-1)
    return f'''
QWidget {{font-family:"{family}";font-size:{size}pt;color:{p.text};}}
QWidget#mascotRoot {{background:transparent;}}
QLabel {{background:transparent;}}
QLabel#windowTitle {{font-weight:600;}}
QLabel#replyTitle,QLabel#activityLabel,QLabel#detailLabel,QLabel#visionBadge {{color:{p.dim};font-size:{small}pt;}}
QLabel#lastPrompt {{color:{p.dim};font-size:{small}pt;border:1px solid {rgba(p.border, 0.9)};border-radius:5px;padding:7px 9px;}}
QLabel#promptMark {{color:{p.green};font-weight:600;}}
QFrame#entryBox {{border:1px solid {rgba(p.border, 0.9)};border-radius:5px;background:transparent;}}
QFrame#entryBox[inputFocused="true"] {{border:1px solid {p.green};}}
QPlainTextEdit {{background:transparent;color:{p.text};border:0;padding:0;
    selection-background-color:{p.selection};selection-color:{p.text};}}
QPushButton {{background:transparent;color:{p.text};border:1px solid {rgba(p.border, 0.9)};border-radius:4px;
    padding:3px 10px;min-height:16px;text-align:left;}}
QPushButton:hover {{background:{rgba(p.selection, 0.6)};}}
QPushButton:focus {{border:1px solid {p.cyan};}}
QPushButton:disabled {{color:{p.dim};}}
QPushButton#choicePill[danger="true"] {{color:{p.red};}}
QPushButton#quietButton {{border:0;padding:2px 4px;color:{p.dim};font-size:{small}pt;}}
QPushButton#quietButton:hover {{color:{p.text};}}
QPushButton#contextButton {{border:0;color:{p.dim};font-size:{small}pt;padding:2px 0;}}
QPushButton#sendButton {{border:0;padding:0;color:{p.dim};text-align:center;}}
QPushButton#sendButton:hover {{color:{p.green};background:transparent;}}
QPushButton#sendButton:focus {{border:1px solid {p.cyan};}}
QScrollArea,QScrollArea > QWidget > QWidget {{background:transparent;border:0;}}
QScrollBar:vertical {{background:transparent;width:5px;margin:3px 0;}}
QScrollBar::handle:vertical {{background:{rgba(p.dim, 0.5)};border-radius:2px;min-height:24px;}}
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical {{height:0;width:0;border:0;background:transparent;}}
QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical {{background:transparent;}}
''' + ('' if native_mac() else MENU_STYLE)


class RoundedMenu(QMenu):
    """A translucent popup with one rounded surface, without a native square frame."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('companionMenu')
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.FramelessWindowHint |
                            Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_MacShowFocusRect, False)
        self.setStyleSheet(MENU_STYLE+'\nQMenu#companionMenu {background:transparent;border:0;padding:7px;}')

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor('#48484e'), 1))
        painter.setBrush(QColor('#2c2c2e'))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(.5, .5, -.5, -.5), 12, 12)
        painter.end()
        super().paintEvent(event)


class Composer(QPlainTextEdit):
    returnPressed = pyqtSignal()
    editingStarted = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._history = []
        self.history_enabled = True
        self._history_index = None
        self._history_draft = ''

    def setText(self, value):
        if value != self.toPlainText():
            self._history_index = None
            self._history_draft = ''
        self.setPlainText(value)

    def text(self):
        return self.toPlainText()

    def clear_sent_history(self):
        self._history.clear()
        self._history_index = None
        self._history_draft = ''

    def remember(self, value):
        """Session-only sent prompts; never persist drafts or setup answers."""
        self._history.append(value[:6000])
        del self._history[:-100]
        self._history_index = None
        self._history_draft = ''

    def recall_history(self, direction):
        if not self.history_enabled or not self._history or direction > 0 and self._history_index is None:
            return False
        cursor = self.textCursor()
        if cursor.hasSelection():
            return False
        block = cursor.block()
        layout = block.layout()
        line = layout.lineForTextPosition(cursor.positionInBlock())
        if direction < 0:
            boundary = block.blockNumber() == 0 and (not line.isValid() or line.lineNumber() == 0)
        else:
            boundary = not block.next().isValid() and (not line.isValid() or line.lineNumber() == layout.lineCount()-1)
        if not boundary:
            return False
        if self._history_index is None:
            self._history_draft = self.toPlainText()
            self._history_index = len(self._history)
        self._history_index = max(0, min(len(self._history), self._history_index + direction))
        if self._history_index == len(self._history):
            value = self._history_draft
            self._history_index = None
            self._history_draft = ''
        else:
            value = self._history[self._history_index]
        self.setPlainText(value)
        self.moveCursor(QTextCursor.MoveOperation.End)
        return True

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.returnPressed.emit()
            event.accept()
        else:
            self.editingStarted.emit()
            # macOS marks ordinary arrows as keypad keys. Real editing
            # modifiers (Shift/Cmd/Ctrl/Alt) must still keep their Qt behavior.
            if (event.modifiers() in (Qt.KeyboardModifier.NoModifier, Qt.KeyboardModifier.KeypadModifier)
                    and event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down)
                    and self.recall_history(-1 if event.key() == Qt.Key.Key_Up else 1)):
                event.accept()
                return
            super().keyPressEvent(event)


class DotButton(QAbstractButton):
    """One of the three title-bar dots. Keyboard-focusable, named for screen readers."""
    def __init__(self, color, name, parent=None):
        super().__init__(parent)
        self.color = QColor(color)
        self.setFixedSize(16, 16)
        self.setAccessibleName(name)
        self.setToolTip(name)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.color.lighter(120) if self.underMouse() else self.color)
        painter.drawEllipse(QPointF(8, 8), 5.5, 5.5)
        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor('#9cbcff'), 1.5))
            painter.drawEllipse(QPointF(8, 8), 7.2, 7.2)


class Surface(QFrame):
    """A terminal window: rounded body, border and the line under its title strip. As her
    real macOS window (system=True) macOS draws the corners, border and shadow."""
    def __init__(self, owner, name, *, system=False):
        super().__init__(owner)
        self.owner = owner
        self.system = system
        self.setObjectName(name)

    def paintEvent(self, event):
        p = self.owner.colors
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        body = QRectF(self.rect()).adjusted(.5, .5, -.5, -.5)
        background = QColor(p.background)
        background.setAlphaF(p.opacity*SEE_THROUGH)
        painter.setPen(QPen(QColor(p.border), 1.2))
        if self.system:  # under a real title bar: the background and Terminal's black rule
            painter.fillRect(self.rect(), background)
            painter.fillRect(QRectF(0, 0, self.width(), 1/self.devicePixelRatioF()), QColor('black'))
            return
        path = QPainterPath()
        path.addRoundedRect(body, RADIUS, RADIUS)
        painter.fillPath(path, background)
        painter.drawPath(path)
        painter.drawLine(QPointF(body.left(), TITLE_HEIGHT), QPointF(body.right(), TITLE_HEIGHT))


def native_window():
    """Her window is a real macOS window (real title bar and traffic lights) on macOS;
    elsewhere, and in tests, a frameless window with drawn dots."""
    return (sys.platform == 'darwin' and QGuiApplication.platformName() == 'cocoa'
            and os.getenv('A11Y_FRAMELESS') != '1')


class Mascot(QWidget):
    opened = pyqtSignal()
    action_requested = pyqtSignal(str)
    submitted = pyqtSignal(str, str)

    PROMPT_LINES = 5   # the input grows to this many lines, then scrolls

    def __init__(self):
        super().__init__()
        self.setWindowTitle('a11y')
        self.native = native_window()
        self.margin = 0 if self.native else MARGIN  # a real window gets its shadow from macOS
        self.titlebar_styled = False
        if self.native:
            # Close only: red collapses her (closeEvent); she can't be minimised or zoomed.
            # Minimise and maximise too: Qt greys yellow and green out otherwise, and they work like Terminal's
            # (yellow minimises, green opens and closes the input; mac_window.prepare_window).
            self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.CustomizeWindowHint |
                                Qt.WindowType.WindowTitleHint | Qt.WindowType.WindowCloseButtonHint |
                                Qt.WindowType.WindowMinimizeButtonHint | Qt.WindowType.WindowMaximizeButtonHint)
        else:
            self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint |
                                Qt.WindowType.Tool | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)  # showing her never takes focus from the user's app
        # Qt Tool windows otherwise hide when another macOS app activates.
        self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow, True)
        self.setAccessibleName('a11y, your desktop companion. Conversation and task controls.')
        self.setObjectName('mascotRoot')
        self.idle_footer = 'Enter to send · Shift+Enter for a new line'
        self.state = 'idle'
        self.submit_action = 'chat'
        self.drag_position = None
        self.busy = False
        self.user_collapsed = False
        self.compact = True
        self.reply_available = False
        self.stretching = False
        self.reduced_motion = False
        self.expanded_width = WIDTH
        appearance = saved_appearance()
        self.skin = appearance['face'] if appearance['face'] in faces.SKINS else faces.DEFAULT_SKIN
        self.colors = load_palette(appearance['theme'])
        self.mono = monospace_font(self.colors.font, self.colors.font_size)

        # Underneath: the reply window. Its text sits in the part that shows beside her. With
        # a real macOS window it is its own window, attached below hers; otherwise both are
        # painted in this one frameless window.
        self.reply_shown = False
        self.reply_window = None
        if self.native:
            # A real window too: "reply" in its title bar, and the three buttons, red closing only
            # the reply. Opened to her right they sit under her; to her left they show.
            self.reply_window = QWidget(None, Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint |
                                        Qt.WindowType.CustomizeWindowHint | Qt.WindowType.WindowTitleHint |
                                        Qt.WindowType.WindowCloseButtonHint | Qt.WindowType.WindowMinimizeButtonHint |
                                        Qt.WindowType.WindowMaximizeButtonHint)
            self.reply_window.setWindowTitle('reply')
            self.reply_window.installEventFilter(self)
            self.reply_window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            self.reply_window.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow, True)
            self.reply_window.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
            self.reply_window.setObjectName('mascotRoot')
            self.destroyed.connect(self.reply_window.deleteLater)
        self.reply_panel = Surface(self, 'replyPanel', system=self.native)
        if self.reply_window is not None:
            self.reply_panel.setParent(self.reply_window)
        self.set_reply_shown(False)
        self.reply_content = QWidget(self.reply_panel)
        reply = QVBoxLayout(self.reply_content)
        self.reply_layout = reply
        reply.setContentsMargins(0, 10 if self.native else 0, 0, 12)
        reply.setSpacing(8)
        self.reply_title = QLabel('reply')
        self.reply_title.setObjectName('replyTitle')
        self.reply_title.setFixedHeight(TITLE_HEIGHT)
        self.reply_title.setVisible(not self.native)  # on macOS the title is in the real title bar
        reply.addWidget(self.reply_title)
        self.bubble = QPlainTextEdit()
        self.bubble.setReadOnly(True)
        self.bubble.setAccessibleName('What your companion says')
        self.bubble.setFrameShape(QFrame.Shape.NoFrame)
        self.bubble.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.bubble.document().setDocumentMargin(0)
        self.bubble.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        reply.addWidget(self.bubble)
        self.stage = QLabel('')
        self.stage.setObjectName('detailLabel')
        self.stage.setWordWrap(True)
        reply.addWidget(self.stage)
        self.choice_box = QWidget()
        self.choice_layout = QVBoxLayout(self.choice_box)
        self.choice_layout.setContentsMargins(0, 0, 0, 0)
        self.choice_layout.setSpacing(6)
        self.choice_area = QScrollArea()
        self.choice_area.setWidgetResizable(True)
        self.choice_area.setFrameShape(QFrame.Shape.NoFrame)
        self.choice_area.setWidget(self.choice_box)
        self.choice_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        reply.addWidget(self.choice_area)
        # Expanded replies add these controls.
        self.header_widget = QWidget()
        header = QHBoxLayout(self.header_widget)
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(4)
        self.settings_button = QPushButton('settings')
        self.settings_button.setObjectName('quietButton')
        self.settings_button.clicked.connect(self.opened)
        header.addWidget(self.settings_button)
        self.vision_button = QPushButton('Screen off')
        self.vision_button.setObjectName('quietButton')
        self.vision_button.setAccessibleName('Screen vision is off. Activate to enable local screen vision.')
        self.vision_button.clicked.connect(lambda: self.action_requested.emit('vision:toggle'))
        header.addWidget(self.vision_button)
        header.addStretch()
        reply.addWidget(self.header_widget)
        self.context_button = QPushButton('Desktop context off  ·  Enable')
        self.context_button.setObjectName('contextButton')
        self.context_button.clicked.connect(lambda: self.action_requested.emit('context:toggle'))
        reply.addWidget(self.context_button)
        self.footer = QLabel(self.idle_footer)
        self.footer.setObjectName('detailLabel')
        self.footer.setWordWrap(True)
        reply.addWidget(self.footer)

        # On top: her window.
        self.window_frame = Surface(self, 'companionWindow', system=self.native)
        frame = QVBoxLayout(self.window_frame)
        frame.setContentsMargins(0, 0, 0, 0)
        frame.setSpacing(0)
        self.title_bar = QWidget()
        self.title_bar.setFixedHeight(TITLE_HEIGHT)
        title = QHBoxLayout(self.title_bar)
        title.setContentsMargins(9, 0, 10, 0)
        title.setSpacing(2)
        self.close_dot = DotButton(self.colors.red, 'Close the companion')
        self.close_dot.clicked.connect(lambda: self.action_requested.emit('quit'))
        self.menu_dot = DotButton(self.colors.yellow, 'Companion actions')
        self.menu_dot.clicked.connect(lambda: self.show_menu())
        self.prompt_settings = DotButton(self.colors.green, 'Open companion settings')
        self.prompt_settings.clicked.connect(self.opened)
        for dot in (self.close_dot, self.menu_dot, self.prompt_settings):
            title.addWidget(dot)
            dot.setVisible(not self.native)
        # The real traffic lights sit here on macOS.
        title.addSpacing(66 if self.native else 8)
        self.window_title = QLabel('a11y')
        self.window_title.setObjectName('windowTitle')
        title.addWidget(self.window_title)
        title.addStretch()
        frame.addWidget(self.title_bar)
        # On macOS the real title bar (traffic lights, "a11y" in the middle) replaces this one.
        self.title_bar.setVisible(not self.native)
        frame.addStretch(1)
        self.character = CharacterButton(self)
        self.character.clicked.connect(self.toggle_chat)
        self.character.setToolTip('Click to chat · Drag to move' + ('' if self.native else ' · Right-click for actions'))
        if not self.native:
            self.character.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            self.character.customContextMenuRequested.connect(
                lambda point: self.show_menu(self.character.mapToGlobal(point)))
        frame.addSpacing(10)  # about half a line of room between the title bar and the face
        frame.addWidget(self.character, 0, Qt.AlignmentFlag.AlignHCenter)
        self.status = QLabel('')
        self.status.setObjectName('activityLabel')
        self.status.setAccessibleName('Companion activity')
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # Always there, empty when idle: "thinking" appearing must not resize her or move the face.
        self.status.setFixedHeight(QFontMetrics(self.mono).lineSpacing())
        frame.addWidget(self.status)
        frame.addStretch(1)
        # The prompt part: the last thing you asked and the input. It sits in a drawer that
        # opens under the face, so opening and collapsing can slide instead of jump.
        self.drawer = QWidget()
        self.card = QWidget(self.drawer)
        self.card.setObjectName('conversationCard')
        card = QVBoxLayout(self.card)
        self.card_layout = card
        card.setContentsMargins(14, 8, 14, 14)
        card.setSpacing(8)
        self.last_prompt = QLabel('')
        self.last_prompt.setObjectName('lastPrompt')
        self.last_prompt.setAccessibleName('Your last request')
        self.last_prompt.hide()
        card.addWidget(self.last_prompt)
        self.entry_row = QFrame()
        self.entry_row.setObjectName('entryBox')
        entry = QHBoxLayout(self.entry_row)
        self.entry_layout = entry
        entry.setContentsMargins(10, 8, 6, 8)
        entry.setSpacing(8)
        self.prompt_mark = QLabel('❯')
        self.prompt_mark.setObjectName('promptMark')
        self.prompt_mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        entry.addWidget(self.prompt_mark, 0, Qt.AlignmentFlag.AlignTop)
        self.input = Composer()
        self.input.setObjectName('composer')
        self.input.setFrameShape(QFrame.Shape.NoFrame)
        self.input.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.input.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.input.document().setDocumentMargin(0)
        # Break a long unbroken word (a link, a pasted code) too, so the box grows for it.
        self.input.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.input.setTabChangesFocus(True)
        self.input.textChanged.connect(self.limit_input)
        self.input.textChanged.connect(self.grow_prompt)
        self.input.setAccessibleName('Reply to your companion')
        self.input.installEventFilter(self)
        self.input.viewport().installEventFilter(self)
        self.entry_row.installEventFilter(self)
        self.card.installEventFilter(self)
        self.input.returnPressed.connect(self.submit)
        self.input.editingStarted.connect(lambda: self.action_requested.emit('computer:interrupt'))
        entry.addWidget(self.input, 1)
        self.send = QPushButton('⏎')
        self.send.setObjectName('sendButton')
        self.send.setToolTip('Send message · Enter')
        self.send.setAccessibleName('Send your message')
        self.send.clicked.connect(self.submit)
        entry.addWidget(self.send, 0, Qt.AlignmentFlag.AlignBottom)
        card.addWidget(self.entry_row)
        frame.addWidget(self.drawer)
        self.drawer_open = 1.0
        self.reply_open = 1.0
        self.reply_side = 'right'
        self.animations = {}
        for name in ('drawer_open', 'reply_open'):
            animation = QVariantAnimation(self)
            animation.setEasingCurve(QEasingCurve.Type.OutCubic)
            animation.valueChanged.connect(lambda value, n=name: self.animation_step(n, value))
            self.animations[name] = animation

        shadow = QGraphicsDropShadowEffect(self.window_frame)
        shadow.setBlurRadius(16)
        shadow.setOffset(0, 3)
        shadow.setColor(QColor(0, 0, 0, 90))
        self.window_frame.setGraphicsEffect(shadow)
        self.typing = QTimer(self)
        self.typing.setInterval(TYPE_STEP_MS)
        self.typing.timeout.connect(self.type_more)
        self.typed = 0
        self.refit = QTimer(self)
        self.refit.setSingleShot(True)
        self.refit.setInterval(0)
        self.refit.timeout.connect(lambda: self.stretching or self.fit_on_screen(self.character.mapToGlobal(QPoint())))
        self.apply_appearance()
        self.relayout()
        self.place_on_screen()
        for key, delta in [('Left',(-20,0)), ('Right',(20,0)), ('Up',(0,-20)), ('Down',(0,20))]:
            shortcut = QShortcut(QKeySequence('Alt+'+key), self)
            shortcut.activated.connect(lambda d=delta: self.move(self.pos()+QPoint(*d)))
        QShortcut(QKeySequence('Escape'), self).activated.connect(lambda: self.collapse_chat(animate=True))
        QShortcut(QKeySequence('Shift+F10'), self).activated.connect(lambda: self.show_menu())
        QShortcut(QKeySequence('Menu'), self).activated.connect(lambda: self.show_menu())
        if self.native:
            self.setup_mac_menus()

    # Appearance

    def apply_appearance(self, *, theme=None, face=None):
        """Re-read colours and font (or use the given theme/skin) and redraw."""
        appearance = saved_appearance()
        face = face or appearance['face']
        self.skin = face if face in faces.SKINS else faces.DEFAULT_SKIN
        self.colors = load_palette(theme or appearance['theme'])
        self.mono = monospace_font(self.colors.font, self.colors.font_size)
        self.setFont(self.mono)
        self.setStyleSheet(mascot_style(self.colors, self.mono.family(), round(self.mono.pointSizeF(), 1)))
        for dot, color in ((self.close_dot, self.colors.red), (self.menu_dot, self.colors.yellow),
                           (self.prompt_settings, self.colors.green)):
            dot.color = QColor(color)
            dot.update()
        self.character.set_font(self.mono)
        if self.reply_window is not None:
            self.reply_window.setFont(self.mono)
            self.reply_window.setStyleSheet(self.styleSheet())
        if self.titlebar_styled:
            self.apply_blur()
        self.input.setFixedHeight(self.line_height()+2)
        # The ❯ mark and ⏎ sit on the first and last line of the input, centred on it.
        self.prompt_mark.setFixedHeight(self.line_height()+2)
        side = self.line_height()+2
        # In the stylesheet too: its QPushButton min-height would otherwise let the row squeeze it.
        self.send.setStyleSheet(f'min-width:{side}px;max-width:{side}px;min-height:{side}px;max-height:{side}px;')
        self.send.setFixedSize(side, side)
        if self.isVisible():
            self.fit_on_screen(self.character.mapToGlobal(QPoint()))
        self.update()

    def line_height(self):
        return QFontMetrics(self.mono).lineSpacing()

    # Layout: both windows are placed by hand, hers on top of the reply's left part.

    def frame_width(self):
        """Her width never changes; opening the input only makes her taller."""
        return self.expanded_width

    def relayout(self):
        if self.stretching:
            return
        width = self.frame_width()
        self.window_frame.setFixedWidth(width)
        self.card.setVisible(not self.user_collapsed or self.drawer_open > 0)
        self.card.setFixedWidth(width)
        for widget in (self.entry_row, self.card):  # measure with the input's new height
            widget.layout().invalidate()
        self.card.layout().activate()
        card_height = self.card.sizeHint().height()
        self.card.setGeometry(0, 0, width, card_height)
        self.drawer.setFixedHeight(round(card_height*self.drawer_open))
        self.window_frame.layout().setContentsMargins(0, 0, 0, round(10*(1-self.drawer_open)))
        self.window_frame.layout().invalidate()
        self.window_frame.layout().activate()
        height = self.window_frame.sizeHint().height()
        showing_reply = self.reply_shown
        beside = showing_reply and not self.native  # the reply is drawn in this window
        m = self.margin
        left = m+(REPLY_WIDTH if beside and self.reply_side == 'left' else 0)
        self.window_frame.setFixedHeight(height)
        self.window_frame.setGeometry(left, m, width, height)
        if beside:
            self.layout_reply(left, m, width, height)
        elif showing_reply:
            self.layout_reply_window(width, height)
        self.setFixedSize(2*m+width+(REPLY_WIDTH if beside else 0), 2*m+height)
        self.reply_panel.lower()
        self.window_frame.raise_()
        self.place_reply_window()

    def layout_reply_window(self, width, height):
        """The reply as its own real window: it grows out from under her as it opens."""
        inner = REPLY_WIDTH-30
        self.reply_content.setFixedWidth(inner)
        self.reply_content.layout().activate()
        wanted = self.reply_content.sizeHint().height()
        reply_height = max(56, min(height-24, wanted))
        shown = round(REPLY_WIDTH*self.reply_open)
        self.reply_panel.setGeometry(0, 0, width+shown, reply_height)
        self.reply_content.setGeometry(width+16 if self.reply_side == 'right' else 14, 0, inner, reply_height)

    def place_reply_window(self):
        """Over her: its top on her top edge and its side on hers."""
        if self.reply_window is None or not self.reply_shown:
            return
        mine = self.frameGeometry()
        size = self.reply_panel.size()
        self.reply_window.resize(size)
        if self.native:
            mac_window.resize_title(self.reply_window)
        x = mine.x() if self.reply_side == 'right' else mine.x()+mine.width()-size.width()
        self.reply_window.move(x, mine.y())
        if self.native and self.reply_window.property('prepared'):
            mac_window.enable_buttons(self.reply_window, zoom=False)

    def set_reply_shown(self, shown):
        self.reply_shown = shown
        self.reply_panel.setVisible(shown)
        if self.reply_window is None:
            return
        if shown:
            self.place_reply_window()
            self.reply_window.show()
            if not self.reply_window.property('prepared'):
                # The reply's yellow closes it like red; its green is off (the input is hers, not the reply's).
                self.reply_window.setProperty('prepared', mac_window.prepare_window(
                    self.reply_window, minimize_owner=self.reply_window.close, zoom=False))
                self.place_reply_window()
            mac_window.keep_below(self, self.reply_window)
            self.apply_blur()
        else:
            mac_window.detach(self, self.reply_window)
            self.reply_window.hide()

    def layout_reply(self, left, top, width, height):
        """Shorter than her window, as tall as its text needs. It shares her top edge and the
        edge on the side it opens from, and slides out from under her (reply_open)."""
        inner = REPLY_WIDTH-30
        self.reply_content.setFixedWidth(inner)
        self.reply_content.layout().activate()
        wanted = self.reply_content.sizeHint().height()
        reply_height = max(TITLE_HEIGHT+56, min(height-24, wanted))
        shown = round(REPLY_WIDTH*self.reply_open)
        if self.reply_side == 'left':
            self.reply_panel.setGeometry(left-shown, top, width+shown, reply_height)
            self.reply_content.setGeometry(14-(REPLY_WIDTH-shown), 0, inner, reply_height)
        else:
            self.reply_panel.setGeometry(left, top, width+shown, reply_height)
            self.reply_content.setGeometry(width+16, 0, inner, reply_height)

    def choose_reply_side(self):
        """Right of her when the screen has room there, else left; but never toward the point she just
        clicked (avoid_point), when the other side has room."""
        area = self.screen().availableGeometry()
        frame_left = self.x()+self.window_frame.x()
        fits_right = frame_left+self.frame_width()+REPLY_WIDTH <= area.right()+1
        fits_left = frame_left-REPLY_WIDTH >= area.left()
        avoid = getattr(self, 'avoid_point', None)
        if avoid is not None and fits_right and fits_left:
            self.reply_side = 'left' if avoid.x() > frame_left+self.frame_width()/2 else 'right'
        elif fits_right or not fits_left:
            self.reply_side = 'right'
        else:
            self.reply_side = 'left'

    def title_reply(self):
        """macOS puts a title right after the buttons. Opened to her right those sit under her,
        so the title is pushed along (em spaces) to start where the reply shows."""
        if self.reply_window is None:
            return
        title = '💬 '+getattr(self, 'reply_heading', 'reply')
        if self.reply_side == 'right':
            metrics = QFontMetrics(QFontDatabase.systemFont(QFontDatabase.SystemFont.TitleFont))
            gap = self.frame_width()-62  # from the end of the hidden buttons to her right edge, plus room
            title = '\u2003'*max(0, -(-gap//max(1, metrics.horizontalAdvance('\u2003'))))+title
        self.reply_window.setWindowTitle(title)

    def open_reply(self):
        """Show the reply window on the side with room, sliding out from under her."""
        if self.reply_shown:
            return
        anchor = self.character.mapToGlobal(QPoint())
        self.choose_reply_side()
        self.title_reply()
        self.reply_open = 0.0
        self.set_reply_shown(True)
        self.fit_on_screen(anchor)
        self.animate('reply_open', 1.0, REPLY_OPEN_MS)

    def animate(self, name, target, duration):
        animation = self.animations[name]
        animation.stop()
        start = getattr(self, name)
        if self.reduced_motion or not self.isVisible() or start == target:
            self.animation_step(name, target)
            return
        animation.setStartValue(float(start))
        animation.setEndValue(float(target))
        animation.setDuration(duration)
        animation.start()

    def animation_step(self, name, value):
        setattr(self, name, float(value))
        if not self.stretching:
            self.fit_on_screen()

    def finish_animations(self):
        """Jump running slides to their end (tests, and before a move)."""
        for name, animation in self.animations.items():
            if animation.state() == QVariantAnimation.State.Running:
                animation.stop()
                self.animation_step(name, animation.endValue())

    def place_on_screen(self):
        area = self.screen().availableGeometry()
        self.move(max(area.left(), area.right()-self.width()-20),
                  max(area.top(), area.bottom()-self.height()-20))

    def fit_on_screen(self, character_anchor=None):
        self.relayout()
        if character_anchor is not None:
            self.move(self.pos()+character_anchor-self.character.mapToGlobal(QPoint()))
        area = self.screen().availableGeometry()
        self.move(max(area.left(), min(self.x(), area.right()-self.width())),
                  max(area.top(), min(self.y(), area.bottom()-self.height())))

    # Movement: one big stretch, like resizing a window yourself, then back.

    def begin_stretch(self):
        # Settle slide values without fitting/repositioning the actual window.
        self.stretching = True
        self.refit.stop()
        self.finish_animations()
        self.set_reply_shown(False)
        self.setMinimumSize(0, 0)
        self.setMaximumSize(16777215, 16777215)
        self.window_frame.setMinimumSize(0, 0)
        self.window_frame.setMaximumSize(16777215, 16777215)

    def stretch_to(self, rect):
        """Show her window filling rect (global coordinates, shadow margin included)."""
        self.setGeometry(rect)
        m = self.margin
        self.window_frame.setGeometry(m, m, rect.width()-2*m, rect.height()-2*m)

    def end_stretch(self, position):
        self.stretching = False
        self.relayout()
        self.move(position)

    # Conversation

    def eventFilter(self, watched, event):
        if (watched is self.reply_window and self.native and event.type() in self.QT_BUTTON_UPDATES
                and self.reply_window.property('prepared')):
            self.relight(self.reply_window, zoom=False)
        if watched is self.reply_window and event.type() == QEvent.Type.Close:
            # Its red button closes the reply only; the conversation and she stay.
            if self.reply_shown:
                self.reply_shown = False
                self.reply_panel.hide()
                mac_window.detach(self, self.reply_window)
            return False
        if not hasattr(self, 'entry_row'):
            return super().eventFilter(watched, event)  # still being built
        if watched in (self.card, self.entry_row) and event.type() == QEvent.Type.MouseButtonPress:
            self.action_requested.emit('computer:interrupt')
            self.activateWindow()
            self.input.setFocus(Qt.FocusReason.MouseFocusReason)
            return True
        if watched in (self.input, self.input.viewport()) and event.type() == QEvent.Type.MouseButtonPress:
            self.action_requested.emit('computer:interrupt')
            self.activateWindow()
            self.input.setFocus(Qt.FocusReason.MouseFocusReason)
        if watched is self.input and event.type() in (QEvent.Type.FocusIn, QEvent.Type.FocusOut):
            self.entry_row.setProperty('inputFocused', event.type() == QEvent.Type.FocusIn)
            self.entry_row.style().unpolish(self.entry_row)
            self.entry_row.style().polish(self.entry_row)
            self.entry_row.update()
            self.character.wake()
        return super().eventFilter(watched, event)

    def present(self, message, choices=(), *, placeholder=None, value='', submit_action='chat', stage='', compact=False):
        draft = self.input.toPlainText() if compact and submit_action == self.submit_action and not value else value
        anchor = self.character.mapToGlobal(QPoint()) if self.isVisible() else None
        self.compact = compact
        self.header_widget.setVisible(not compact)
        self.context_button.setVisible(not compact)
        self.footer.setVisible(not compact)
        self.stage.setText(stage)
        self.stage.setVisible(bool(stage) and not compact)
        self.show_message(message)
        while self.choice_layout.count():
            item = self.choice_layout.takeAt(0)
            item.widget().hide()
            item.widget().deleteLater()
        for title, action in choices:
            choice = QPushButton(title)
            choice.setProperty('companion_action', action)
            choice.setObjectName('choicePill')
            choice.setProperty('danger', action == 'computer:stop')
            choice.clicked.connect(lambda checked=False, a=action: self.action_requested.emit(a))
            self.choice_layout.addWidget(choice, 0, Qt.AlignmentFlag.AlignLeft)
        self.choice_area.setFixedHeight(min(150, len(choices)*32))
        self.choice_area.setVisible(bool(choices))
        working = any(a == 'computer:stop' for _, a in choices)
        self.reply_title.setText('working · esc to stop' if working else 'reply')
        self.reply_heading = 'working' if working else 'reply'
        self.title_reply()
        self.submit_action = submit_action
        self.input.history_enabled = submit_action in ('chat', 'computer')
        voice = getattr(self, 'voice_placeholder', '') if getattr(self, 'voice_on', False) else ''
        self.input.setPlaceholderText(placeholder if placeholder and placeholder != 'Ask anything…' else
                                      (voice or 'Ask anything…'))
        self.input.setText(draft)
        self.update_interaction_state()
        self.entry_row.setVisible(placeholder is not None or compact)
        self.reply_available = bool(message) or bool(choices)
        showing = self.reply_available and not self.user_collapsed
        if not showing:
            self.set_reply_shown(False)
        self.grow_prompt()
        self.fit_on_screen(anchor)
        if showing:
            self.open_reply()
        if placeholder is not None and self.card.isVisible() and self.isActiveWindow():
            self.input.setFocus()

    def show_message(self, message):
        """Put the reply in the reply window. The full text is there at once (for screen
        readers and selection); the part not yet typed is drawn invisible."""
        self.typing.stop()
        self.bubble.setPlainText(message)
        self.bubble.setVisible(bool(message))
        self.size_message(message)
        self.typed = 0
        if message and not self.reduced_motion and len(message) <= 1200 and self.isVisible():
            self.mask_untyped()
            self.typing.start()

    def mask_untyped(self):
        cursor = QTextCursor(self.bubble.document())
        cursor.setPosition(self.typed)
        cursor.movePosition(QTextCursor.MoveOperation.End, QTextCursor.MoveMode.KeepAnchor)
        hidden = QTextCharFormat()
        hidden.setForeground(QColor(0, 0, 0, 0))
        cursor.setCharFormat(hidden)

    def type_more(self):
        length = len(self.bubble.toPlainText())
        start, self.typed = self.typed, min(length, self.typed+TYPE_CHARS)
        cursor = QTextCursor(self.bubble.document())
        cursor.setPosition(start)
        cursor.setPosition(self.typed, QTextCursor.MoveMode.KeepAnchor)
        shown = QTextCharFormat()
        shown.setForeground(QColor(self.colors.text))
        cursor.setCharFormat(shown)
        if self.typed >= length:
            self.typing.stop()

    def finish_typing(self):
        if self.typing.isActive():
            self.typed = len(self.bubble.toPlainText())
            cursor = QTextCursor(self.bubble.document())
            cursor.select(QTextCursor.SelectionType.Document)
            shown = QTextCharFormat()
            shown.setForeground(QColor(self.colors.text))
            cursor.setCharFormat(shown)
            self.typing.stop()

    PROMPT_HEIGHT = 0  # kept for callers; the input box now grows line by line

    def grow_prompt(self):
        """One line of input to start; the box grows a line at a time up to PROMPT_LINES."""
        if self.user_collapsed:
            return
        line = self.line_height()
        width = self.input.viewport().width() if self.input.isVisible() else 0
        if width < 40:  # not laid out yet: her window minus the box padding, the mark and send
            width = self.expanded_width-2*14-10-6-2*8-24-QFontMetrics(self.mono).horizontalAdvance('❯')
        try:
            lines = self.wrapped_lines(width)
        except Exception as error:  # never leave the box stuck at one line because of a measuring error
            print('A11y prompt sizing fell back:', error, file=sys.stderr, flush=True)
            lines = 1+self.input.toPlainText().count('\n')
        height = min(self.PROMPT_LINES, lines)*line+2
        if self.input.height() != height:
            self.input.setFixedHeight(height)
            if self.isVisible() and not self.stretching:
                # After Qt has laid out the box at its new height; measuring now reads stale sizes.
                self.refit.start()

    def wrapped_lines(self, width):
        """Lines the composer shows at this width, laid out with its own font and wrap rules
        (an estimate from font metrics disagreed with the real wrapping on macOS)."""
        option = QTextOption()
        option.setWrapMode(self.input.wordWrapMode())
        count = 0
        block = self.input.document().begin()
        while block.isValid():
            layout = QTextLayout(block.text() or ' ', self.input.font())
            layout.setTextOption(option)
            layout.beginLayout()
            lines = 0
            while True:
                row = layout.createLine()
                if not row.isValid():
                    break
                row.setLineWidth(width)
                lines += 1
            layout.endLayout()
            count += max(1, lines)
            block = block.next()
        return max(1, count)

    def size_message(self, message):
        metrics = QFontMetrics(self.mono)
        rect = metrics.boundingRect(QRect(0, 0, REPLY_WIDTH-36, 5000), Qt.TextFlag.TextWordWrap, message)
        self.bubble.setFixedHeight(max(metrics.lineSpacing()+4, min(8*metrics.lineSpacing(), rect.height()+6)))

    def limit_input(self):
        value = self.input.toPlainText()
        if len(value) > 6000:
            self.input.setPlainText(value[:6000])

    def submit(self, checked=False):
        if self.busy:
            return
        value = self.input.text().strip()
        if value:
            if self.submit_action in ('chat', 'computer'):
                self.input.remember(value)
            self.input.clear()
            self.remember_prompt(value)
            self.submitted.emit(value, self.submit_action)

    def remember_prompt(self, value):
        line = ' '.join(value.split())
        metrics = QFontMetrics(self.last_prompt.font())
        self.last_prompt.setText(metrics.elidedText('› '+line, Qt.TextElideMode.ElideRight, self.expanded_width-2*14-22))
        self.last_prompt.setToolTip(value)
        self.last_prompt.show()

    def set_busy(self, busy):
        self.busy = busy
        self.update_interaction_state()
        self.footer.setText('Thinking locally…' if busy else self.idle_footer)

    def update_interaction_state(self):
        # Drafting and Stop remain usable while inference is running.
        self.input.setEnabled(True)
        self.send.setEnabled(not self.busy)
        self.choice_box.setEnabled(True)
        for index in range(self.choice_layout.count()):
            choice = self.choice_layout.itemAt(index).widget()
            choice.setEnabled(not self.busy or choice.property('companion_action') in ('computer:stop', 'settings', 'stop_speech'))

    # Qt greys yellow and green out again each time it updates the window's size limits, state or
    # activation (opening the input changes her size limits without a resize). Re-enable right after.
    QT_BUTTON_UPDATES = (QEvent.Type.LayoutRequest, QEvent.Type.Resize, QEvent.Type.ActivationChange,
                         QEvent.Type.WindowStateChange, QEvent.Type.Show)

    def event(self, event):
        handled = super().event(event)
        if getattr(self, 'titlebar_styled', False) and self.native and event.type() in self.QT_BUTTON_UPDATES:
            self.relight(self)
        return handled

    def relight(self, window, zoom=True):
        """Re-enable the window's yellow and green once Qt has finished updating it. The timer belongs
        to the window, so it goes with it."""
        timer = window.findChild(QTimer, 'relight')
        if timer is None:
            timer = QTimer(window)
            timer.setObjectName('relight')
            timer.setSingleShot(True)
            timer.setInterval(0)
            timer.timeout.connect(lambda: mac_window.enable_buttons(window, zoom=zoom))
        timer.start()

    def moveEvent(self, event):
        self.character.owner_moved(self.pos())
        self.place_reply_window()
        super().moveEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        if self.native and not self.titlebar_styled:
            self.titlebar_styled = True
            QTimer.singleShot(0, self.style_titlebar)

    def style_titlebar(self):
        """Native compact title bar and chat/minimise traffic lights."""
        if not self.native:
            return
        mac_window.prepare_window(self, toggle_chat=self.toggle_chat, minimize_owner=self.minimize_pair)
        self.set_proxy_icon(self)
        self.apply_blur()

    def minimize_pair(self):
        if self.native:
            mac_window.miniaturize(self)

    def apply_blur(self):
        """Terminal blurs what's behind its active window and (in this profile) not its
        inactive ones; she does the same, and so does her reply."""
        if not self.native:
            return
        blur = self.colors.blur if self.isActiveWindow() else self.colors.blur_inactive
        for window in (self, self.reply_window if self.reply_shown else None):
            if window is not None:
                mac_window.set_blur(window, round(blur*30))

    def changeEvent(self, event):
        if event.type() == QEvent.Type.ActivationChange:
            self.apply_blur()
        super().changeEvent(event)

    def closeEvent(self, event):
        # The red traffic light closes the companion: the app quits through its usual path
        # (which waits for a running request), and that path closes her.
        if self.native and event.spontaneous():
            event.ignore()
            self.action_requested.emit('quit')
            return
        if self.reply_window is not None:
            self.set_reply_shown(False)
        super().closeEvent(event)

    STATUS = {'thinking': 'thinking', 'speaking': 'speaking', 'guiding': 'working', 'listening': 'listening'}

    def set_state(self, state, message=None):
        if state == 'celebrate' and self.state != 'celebrate':
            self.character.hop(height=6.0)
        self.state = state
        self.character.set_state(state)
        self.status.setText(self.STATUS.get(state, ''))
        if message:
            self.show_message(message)
            if self.reply_shown:
                self.fit_on_screen(self.character.mapToGlobal(QPoint()))

    def set_reduced_motion(self, enabled):
        self.reduced_motion = enabled
        if enabled:
            self.finish_typing()
            self.finish_animations()
        self.character.set_reduced_motion(enabled)

    def collapse_chat(self, *, animate=False):
        """Close the input drawer. Code that moves her right after (moves, guidance) collapses
        at once, so her size is final; a click on her slides it shut."""
        anchor = self.character.mapToGlobal(QPoint())
        self.user_collapsed = True
        self.set_reply_shown(False)
        if animate:
            self.fit_on_screen(anchor)
            self.animate('drawer_open', 0.0, OPEN_MS)
        else:
            self.animations['drawer_open'].stop()
            self.drawer_open = 0.0
            self.fit_on_screen(anchor)
        self.character.setFocus()

    def expand_chat(self):
        """Slide the input open under the face."""
        anchor = self.character.mapToGlobal(QPoint())
        self.user_collapsed = False
        self.set_reply_shown(False)
        self.grow_prompt()
        self.fit_on_screen(anchor)
        self.animate('drawer_open', 1.0, OPEN_MS)
        self.character.wake()
        if self.entry_row.isVisible():
            self.input.setFocus()

    def toggle_chat(self):
        if self.native:
            mac_window.activate_app()
        if self.user_collapsed or self.card.isHidden():
            self.expand_chat()
            if self.native:
                mac_window.make_key(self)
        else:
            self.collapse_chat(animate=True)

    def set_screen_vision_indicator(self, enabled):
        if self.prompt_settings.property('screenVision') == enabled:
            return
        self.prompt_settings.setProperty('screenVision', enabled)
        self.prompt_settings.setToolTip('Settings · Screen vision '+('on: each prompt includes a local screen image' if enabled else 'off'))

    def hide_reply(self):
        anchor = self.character.mapToGlobal(QPoint())
        self.set_reply_shown(False)
        self.fit_on_screen(anchor)

    def show_reply(self):
        if self.reply_available:
            self.expand_chat()
            self.open_reply()

    def grouped_menu_entries(self):
        """One source for the macOS menu bar and the keyboard/context menu."""
        return {
            'A11y': [('Settings…', 'settings'), ('Personalize and set up access…', 'setup:wizard'),
                     ('Set up AI and voice…', 'readiness'), ('Quit companion', 'quit')],
            'Chat': [('Talk to me', 'talk'), ('Show last reply', 'reply'), ('Computer task…', 'computer:prompt'),
                     ('Stop computer task', 'computer:stop')],
            'View': [('Face', [(name, 'face:'+key, key == self.skin) for key, name in faces.SKIN_NAMES.items()]),
                     ('Pause screen vision' if self.vision_button.text() == 'Screen on' else 'Enable screen vision…', 'vision:toggle'),
                     ('Turn off voice' if getattr(self, 'voice_on', False) else 'Turn on voice (“Hey Ally”)…', 'voice:toggle')],
        }

    def menu_entries(self):
        """Keep the historical flat menu, including its order and separators."""
        groups = self.grouped_menu_entries()
        entries = {entry[1] if isinstance(entry[1], str) else 'Face': entry
                   for group in groups.values() for entry in group}
        order = ('talk', 'computer:prompt', 'computer:stop', 'reply', None,
                 'vision:toggle', 'voice:toggle', 'setup:wizard', 'readiness', None,
                 'Face', 'settings', None, 'quit')
        return [entries[key] if key else None for key in order]

    def setup_mac_menus(self):
        self.menu_bar = QMenuBar(None)
        self.menu_bar.setNativeMenuBar(True)
        for title in self.grouped_menu_entries():
            menu = self.menu_bar.addMenu(title)
            self.fill_menu(menu, self.grouped_menu_entries()[title], native_roles=True)
            menu.aboutToShow.connect(lambda m=menu, t=title: self.refresh_menu(m, self.grouped_menu_entries()[t]))
        self.menu_bar.addMenu('Window')
        self.menu_bar.addMenu('Help')

    def refresh_menu(self, menu, entries):
        for item, entry in zip(menu.actions(), entries):
            if entry is None:
                continue
            item.setText('Quit' if entry[1] == 'quit' and menu.title() == 'A11y' else entry[0])
            if isinstance(entry[1], list):
                self.refresh_menu(item.menu(), entry[1])
            elif len(entry) > 2:
                item.setChecked(entry[2])

    @staticmethod
    def app_icon_path():
        return resource_dir()/'packaging'/'icons'/'A11y.png'

    def set_proxy_icon(self, window):
        if not self.native:
            return
        path = self.app_icon_path()
        image = QPixmap(str(path))
        from PyQt6.QtCore import QBuffer, QIODevice
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buffer, 'PNG')
        mac_window.set_proxy_icon(window, str(path), bytes(buffer.data()))

    def show_menu(self, position=None):
        position = position or self.character.mapToGlobal(self.character.rect().topLeft())
        entries = self.menu_entries()
        if native_mac():
            # A real macOS menu (Qt's own popups only imitate one).
            chosen = mac_window.popup_menu(entries, position.x(), position.y(),
                                           QGuiApplication.primaryScreen().geometry().height())
            if chosen is not None:
                if chosen:
                    self.action_requested.emit(chosen)
                return
        menu = RoundedMenu(self)
        menu.setAccessibleName('Companion actions')
        self.fill_menu(menu, entries)
        menu.exec(position)
        menu.deleteLater()

    def fill_menu(self, menu, entries, *, native_roles=False):
        for entry in entries:
            if entry is None:
                menu.addSeparator()
            elif isinstance(entry[1], list):
                self.fill_menu(menu.addMenu(entry[0]), entry[1], native_roles=native_roles)
            else:
                item = QAction(entry[0], menu)
                if self.native:
                    item.setMenuRole(QAction.MenuRole.NoRole)
                if native_roles:
                    if entry[1] == 'settings':
                        item.setMenuRole(QAction.MenuRole.PreferencesRole)
                        item.setShortcut(QKeySequence('Ctrl+,'))
                    elif entry[1] == 'quit':
                        item.setText('Quit')
                        item.setMenuRole(QAction.MenuRole.QuitRole)
                        item.setShortcut(QKeySequence('Ctrl+Q'))
                    elif entry[1] == 'computer:stop':
                        item.setShortcut(QKeySequence('Ctrl+.'))
                    elif entry[1] in ('setup:wizard', 'readiness'):
                        item.setMenuRole(QAction.MenuRole.ApplicationSpecificRole)
                menu.addAction(item)
                if len(entry) > 2:
                    item.setCheckable(True)
                    item.setChecked(entry[2])
                item.triggered.connect(lambda checked=False, a=entry[1]: self.action_requested.emit(a))

    def mousePressEvent(self, event):
        if self.native and event.button() == Qt.MouseButton.LeftButton and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.drag_position = None
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_position = event.globalPosition().toPoint()-self.pos()
            self.drag_start, self.body_dragged = event.globalPosition().toPoint(), False

    def mouseMoveEvent(self, event):
        if self.drag_position is not None and event.buttons() & Qt.MouseButton.LeftButton:
            if (event.globalPosition().toPoint()-self.drag_start).manhattanLength() >= QApplication.startDragDistance():
                self.body_dragged = True
            if self.body_dragged:
                self.move(event.globalPosition().toPoint()-self.drag_position)

    def mouseReleaseEvent(self, event):
        # A click anywhere on her opens the chat, not only on the face (reported: clicking her
        # sometimes did nothing; the margins only started a drag). Moving her needs a real drag.
        if self.drag_position is not None and not self.body_dragged and event.button() == Qt.MouseButton.LeftButton:
            self.open_chat_from_user()
        self.drag_position = None

    def open_chat_from_user(self):
        """The user clicked her: open the chat (never close it) and take typing, which a
        non-activating panel does not get by itself."""
        if self.user_collapsed or self.card.isHidden() or self.drawer_open < 1.0:
            self.expand_chat()
        self.set_reply_shown(False)
        if self.native:
            mac_window.activate_app()
            mac_window.make_key(self)
        if self.entry_row.isVisible():
            self.input.setFocus(Qt.FocusReason.MouseFocusReason)


class CharacterButton(QAbstractButton):
    """Her face: a few lines of text in the terminal font, one cell per character.

    The skin and state pick the lines (faces.py); a timer plays blinks, the spinner and a
    talking mouth. Reduced motion stops the timer and shows still faces."""
    FRAME_MS = 110
    BLINK_PERIOD = 4200

    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.state = 'idle'
        self.press_position = None
        self.dragging = False
        self.reduced_motion = False
        self.font_ = QFont(owner.mono)
        self.raw = QRawFont.fromFont(self.font_)
        self.cell = QSize(8, 16)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)  # still reachable by keyboard, no ring drawn
        self.setAttribute(Qt.WidgetAttribute.WA_MacShowFocusRect, False)
        self.clock = QElapsedTimer()
        self.clock.start()
        self.last_activity = 0
        self.hop_started = None
        self.hop_length = 0
        self.hop_height = 0.0
        self.anim_timer = QTimer(self)
        self.anim_timer.timeout.connect(self.update)
        self.anim_timer.start(self.FRAME_MS)
        self.set_font(self.font_)
        self.describe()

    def set_font(self, font):
        self.font_ = QFont(font)
        self.font_.setWeight(QFont.Weight.DemiBold)
        self.raw = QRawFont.fromFont(self.font_)
        metrics = QFontMetricsF(self.font_)
        # Whole-pixel cells like a terminal, so box and block characters join up.
        self.cell = QSize(max(1, round(metrics.horizontalAdvance('M'))), max(1, int(metrics.height())))
        # The same room for every face, so changing faces neither resizes nor moves her.
        columns, lines = (max(values) for values in zip(*(faces.size(name) for name in faces.SKINS)))
        self.setFixedSize(columns*self.cell.width()+28, lines*self.cell.height()+18)
        self.update()

    def set_state(self, state):
        if state != self.state:
            self.state = state
            self.wake()
            self.describe()
        self.update()

    def describe(self):
        mood = {'thinking': 'thinking', 'speaking': 'speaking', 'guiding': 'working on a task',
                'celebrate': 'done', 'listening': 'listening'}.get(self.state, 'ready')
        self.setAccessibleName(f'a11y’s face, {mood}. Activate to open or collapse the conversation; drag to move. '
                               'Right-click or Shift+F10 for actions.')

    def wake(self):
        self.last_activity = self.clock.elapsed()

    def set_reduced_motion(self, enabled):
        self.reduced_motion = enabled
        if enabled:
            self.anim_timer.stop()
            self.hop_started = None
        else:
            self.anim_timer.start(self.FRAME_MS)
        self.update()

    def owner_moved(self, position):
        self.wake()

    def hop(self, duration=900, height=10.0):
        """One small bounce, e.g. for a finished task."""
        if self.reduced_motion or height <= 0:
            return
        self.hop_started, self.hop_length, self.hop_height = self.clock.elapsed(), duration, height

    def lift(self):
        if self.hop_started is None:
            return 0.0
        t = (self.clock.elapsed()-self.hop_started)/max(1, self.hop_length)
        if t >= 1:
            self.hop_started = None
            return 0.0
        return self.hop_height*4*t*(1-t)

    def current_face(self):
        state = faces.STATE_ALIASES.get(self.state, self.state)
        now = self.clock.elapsed()
        if self.reduced_motion:
            return faces.face(self.owner.skin, state, 0)
        if state == 'idle':
            if now-self.last_activity > SLEEP_AFTER_MS:
                state = 'sleeping'
            elif faces.blinking(now, self.BLINK_PERIOD):
                state = 'blink'
        step = 600 if state == 'sleeping' else 180 if state == 'speaking' else self.FRAME_MS
        return faces.face(self.owner.skin, state, now//step)

    def mousePressEvent(self, event):
        self.context_press = bool(self.owner.native and event.button() == Qt.MouseButton.LeftButton
                                  and event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        if self.context_press:
            self.press_position = None
            event.accept()
            return
        self.wake()
        self.press_position = event.globalPosition().toPoint()
        self.owner_position = self.owner.pos()
        self.dragging = self.double_click = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.press_position is not None and event.buttons() & Qt.MouseButton.LeftButton:
            delta = event.globalPosition().toPoint()-self.press_position
            if delta.manhattanLength() >= QApplication.startDragDistance():
                self.dragging = True
            if self.dragging:
                self.owner.move(self.owner_position+delta)
                return
        super().mouseMoveEvent(event)

    def mouseDoubleClickEvent(self, event):
        self.double_click = True  # its release would toggle the chat shut again
        event.accept()

    def mouseReleaseEvent(self, event):
        if getattr(self, 'context_press', False):
            self.context_press = False
            self.setDown(False)
            event.accept()
            return
        if self.dragging or getattr(self, 'double_click', False) or event.button() != Qt.MouseButton.LeftButton:
            self.setDown(False)
        elif self.press_position is not None:
            # A click that ends a pixel off the face still counts (Qt only clicks inside it).
            self.setDown(False)
            self.click()
        else:
            super().mouseReleaseEvent(event)
        self.press_position = None
        self.dragging = self.double_click = False

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        lines = self.current_face()
        columns = max(len(line) for line in lines)
        cw, ch = self.cell.width(), self.cell.height()
        left = (self.width()-columns*cw)//2
        top = (self.height()-len(lines)*ch)//2-round(self.lift())
        ascent = round(QFontMetricsF(self.font_).ascent())
        painter.setFont(self.font_)
        painter.setPen(QColor(self.owner.colors.text))
        for row, line in enumerate(lines):
            y = top+row*ch+ascent
            if all(c == ' ' or self.raw.supportsCharacter(c) for c in line):
                painter.drawText(QPoint(left, y), line)
            else:  # a fallback font may be wider; keep every character in its own cell
                for column, character in enumerate(line):
                    if character != ' ':
                        painter.drawText(QPoint(left+column*cw, y), character)
