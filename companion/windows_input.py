"""Windows input and UI Automation adapters for the desktop bridge.

Input uses SendInput with the user's real keyboard/mouse semantics; it never runs
commands. UI Automation (through comtypes, when installed) reports actionable
controls, the focused element's role and invokes an observed control. Without
comtypes, a Win32 focus fallback still identifies classic edit fields; controls
are then unavailable and tasks use OCR/image grounding instead.

Coordinates: this process is per-monitor DPI aware (Qt sets it), so Win32 and UIA
report physical pixels. Prompts use Qt's logical space: each monitor's origin in
native pixels, its extent divided by that monitor's scale. ``logical_rect`` maps
between them so controls, OCR regions and captures agree on every monitor.
"""
import ctypes
from ctypes import wintypes
import threading
import time

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0002, 0x0004
MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP = 0x0008, 0x0010
MOUSEEVENTF_WHEEL = 0x0800
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 0x0001, 0x0002, 0x0004
WHEEL_DELTA = 120
VK_CONTROL = 0x11
VK_SHIFT = 0x10
VK_RETURN = 0x0D
# The shared key vocabulary names macOS Command shortcuts; Windows uses Ctrl.
KEYS = {'enter': (0x0D, False), 'tab': (0x09, False), 'escape': (0x1B, False), 'space': (0x20, False),
        'backspace': (0x08, False), 'left': (0x25, True), 'up': (0x26, True), 'right': (0x27, True),
        'down': (0x28, True)}
CTRL_LETTERS = {'cmd_l': 'L', 'cmd_f': 'F', 'cmd_t': 'T', 'cmd_n': 'N', 'cmd_w': 'W', 'cmd_a': 'A', 'cmd_r': 'R', 'cmd_s': 'S'}
MONITOR_DEFAULTTONEAREST = 2
MDT_EFFECTIVE_DPI = 0
GWL_STYLE = -16
ES_PASSWORD = 0x0020

# UI Automation identifiers (UIAutomationClient.h).
UIA_BUTTON, UIA_CHECKBOX, UIA_COMBOBOX, UIA_EDIT, UIA_HYPERLINK = 50000, 50002, 50003, 50004, 50005
UIA_LISTITEM, UIA_MENUITEM, UIA_RADIOBUTTON, UIA_TABITEM = 50007, 50011, 50013, 50019
UIA_TREEITEM, UIA_DOCUMENT, UIA_SPLITBUTTON, UIA_DATAITEM = 50024, 50030, 50031, 50029
ACTIONABLE = {UIA_BUTTON: 'button', UIA_CHECKBOX: 'checkbox', UIA_COMBOBOX: 'combobox', UIA_HYPERLINK: 'link',
              UIA_LISTITEM: 'list item', UIA_MENUITEM: 'menu item', UIA_RADIOBUTTON: 'radio button',
              UIA_TABITEM: 'tab', UIA_TREEITEM: 'tree item', UIA_SPLITBUTTON: 'split button', UIA_DATAITEM: 'item'}
# Roles shared with the macOS bridge so input authorization is platform neutral.
TEXT_ROLES = {UIA_EDIT: 'AXTextField', UIA_DOCUMENT: 'AXTextArea', UIA_COMBOBOX: 'AXComboBox'}
PROP_BOUNDS, PROP_CONTROL_TYPE, PROP_NAME, PROP_ENABLED, PROP_PASSWORD, PROP_OFFSCREEN = 30001, 30003, 30005, 30010, 30019, 30022
PROP_VALUE, PROP_LEGACY_VALUE = 30045, 30093
PATTERN_INVOKE, PATTERN_SELECTION_ITEM, PATTERN_TOGGLE, PATTERN_EXPAND, PATTERN_LEGACY = 10000, 10010, 10015, 10005, 10018
TREE_SCOPE_DESCENDANTS = 4
MAX_CONTROLS = 45
CLASSIC_TEXT_CLASSES = {'edit': 'AXTextField', 'richedit20w': 'AXTextArea', 'richedit50w': 'AXTextArea',
                        'richeditd2dpt': 'AXTextArea', 'scintilla': 'AXTextArea'}


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [('dx', wintypes.LONG), ('dy', wintypes.LONG), ('mouseData', wintypes.DWORD),
                ('dwFlags', wintypes.DWORD), ('time', wintypes.DWORD), ('dwExtraInfo', ctypes.c_size_t)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [('wVk', wintypes.WORD), ('wScan', wintypes.WORD), ('dwFlags', wintypes.DWORD),
                ('time', wintypes.DWORD), ('dwExtraInfo', ctypes.c_size_t)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [('uMsg', wintypes.DWORD), ('wParamL', wintypes.WORD), ('wParamH', wintypes.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [('mi', MOUSEINPUT), ('ki', KEYBDINPUT), ('hi', HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ('u',)
    _fields_ = [('type', wintypes.DWORD), ('u', _INPUTUNION)]


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [('cbSize', wintypes.DWORD), ('flags', wintypes.DWORD), ('hwndActive', wintypes.HWND),
                ('hwndFocus', wintypes.HWND), ('hwndCapture', wintypes.HWND), ('hwndMenuOwner', wintypes.HWND),
                ('hwndMoveSize', wintypes.HWND), ('hwndCaret', wintypes.HWND), ('rcCaret', wintypes.RECT)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [('cbSize', wintypes.DWORD), ('rcMonitor', wintypes.RECT), ('rcWork', wintypes.RECT), ('dwFlags', wintypes.DWORD)]


def utf16_units(text):
    data = text.encode('utf-16-le')
    return [int.from_bytes(data[i:i+2], 'little') for i in range(0, len(data), 2)]


class Monitors:
    """Physical/logical conversion per monitor, matching Qt's coordinate space."""

    def __init__(self, user32=None, shcore=None):
        self.user32 = user32 or ctypes.WinDLL('user32', use_last_error=True)
        self.shcore = shcore or ctypes.WinDLL('shcore')
        u = self.user32
        u.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
        u.MonitorFromPoint.restype = wintypes.HMONITOR
        u.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFO)]
        self.shcore.GetDpiForMonitor.argtypes = [wintypes.HMONITOR, ctypes.c_int, ctypes.POINTER(wintypes.UINT), ctypes.POINTER(wintypes.UINT)]

    def at(self, x, y):
        """(handle, physical rect as x,y,width,height, scale) of the monitor nearest a point."""
        handle = self.user32.MonitorFromPoint(wintypes.POINT(int(x), int(y)), MONITOR_DEFAULTTONEAREST)
        info = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
        if not handle or not self.user32.GetMonitorInfoW(handle, ctypes.byref(info)):
            raise RuntimeError('No display is available.')
        dpi_x, dpi_y = wintypes.UINT(96), wintypes.UINT(96)
        self.shcore.GetDpiForMonitor(handle, MDT_EFFECTIVE_DPI, ctypes.byref(dpi_x), ctypes.byref(dpi_y))
        rect = info.rcMonitor
        return int(handle), (rect.left, rect.top, rect.right-rect.left, rect.bottom-rect.top), (dpi_x.value or 96)/96


def logical_rect(monitors, left, top, right, bottom):
    """Physical rectangle → Qt logical coordinates, using the monitor holding its center."""
    _, (mx, my, _, _), scale = monitors.at((left+right)/2, (top+bottom)/2)
    return {'x': mx+(left-mx)/scale, 'y': my+(top-my)/scale,
            'width': (right-left)/scale, 'height': (bottom-top)/scale}


class SendInput:
    def __init__(self, user32=None):
        self.user32 = user32 or ctypes.WinDLL('user32', use_last_error=True)
        u = self.user32
        u.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
        u.SendInput.restype = wintypes.UINT
        u.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
        u.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        u.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
        u.MapVirtualKeyW.restype = wintypes.UINT

    def send(self, events):
        array = (INPUT*len(events))(*events)
        sent = self.user32.SendInput(len(events), array, ctypes.sizeof(INPUT))
        if sent != len(events):
            # Blocked by UIPI (an elevated target window) or by a secure desktop.
            raise RuntimeError('Windows blocked the input. The target may be running as administrator.')

    @staticmethod
    def mouse(flags, data=0):
        event = INPUT(type=INPUT_MOUSE)
        event.mi = MOUSEINPUT(0, 0, data & 0xFFFFFFFF, flags, 0, 0)
        return event

    def key(self, vk, up=False, extended=False):
        event = INPUT(type=INPUT_KEYBOARD)
        flags = (KEYEVENTF_KEYUP if up else 0) | (KEYEVENTF_EXTENDEDKEY if extended else 0)
        event.ki = KEYBDINPUT(vk, self.user32.MapVirtualKeyW(vk, 0) & 0xFFFF, flags, 0, 0)
        return event

    @staticmethod
    def unicode(unit, up=False):
        event = INPUT(type=INPUT_KEYBOARD)
        event.ki = KEYBDINPUT(0, unit, KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if up else 0), 0, 0)
        return event

    def cursor(self):
        point = wintypes.POINT()
        self.user32.GetCursorPos(ctypes.byref(point))
        return point.x, point.y

    def move(self, x, y):
        if not self.user32.SetCursorPos(int(x), int(y)) or self.cursor() != (int(x), int(y)):
            raise RuntimeError('Windows did not move the pointer to the target. No click was sent.')

    def click(self, x, y, button='left', count=1):
        self.move(x, y)
        down, up = ((MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP) if button == 'left'
                    else (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP))
        for index in range(count):
            self.send([self.mouse(down), self.mouse(up)])
            if index+1 < count:
                time.sleep(0.05)

    def scroll(self, x, y, direction):
        previous = self.cursor()
        self.move(x, y)
        try:
            self.send([self.mouse(MOUSEEVENTF_WHEEL, (3 if direction == 'up' else -3)*WHEEL_DELTA)])
        finally:
            self.user32.SetCursorPos(*previous)

    def press(self, name):
        if name in KEYS:
            vk, extended = KEYS[name]
            self.send([self.key(vk, extended=extended), self.key(vk, up=True, extended=extended)])
        elif name in CTRL_LETTERS:
            vk = ord(CTRL_LETTERS[name])
            self.send([self.key(VK_CONTROL), self.key(vk), self.key(vk, up=True), self.key(VK_CONTROL, up=True)])
        else:
            raise RuntimeError('Unsupported keyboard command.')

    def type_text(self, text, still_focused, chunk=20):
        """Type UTF-16 units in small chunks, stopping if focus moves between chunks."""
        for number, line in enumerate(text.split('\n')):
            if number:
                # A new line in a document: Shift+Enter, which never sends in chat apps (as on macOS).
                if not still_focused():
                    raise RuntimeError('Typing stopped because focus changed. Some text may already have been entered; check before retrying.')
                self.send([self.key(VK_SHIFT), self.key(VK_RETURN), self.key(VK_RETURN, up=True), self.key(VK_SHIFT, up=True)])
            units = utf16_units(line)
            for start in range(0, len(units), chunk):
                if not still_focused():
                    raise RuntimeError('Typing stopped because focus changed. Some text may already have been entered; check before retrying.')
                events = []
                for unit in units[start:start+chunk]:
                    events += [self.unicode(unit), self.unicode(unit, up=True)]
                self.send(events)


class Win32Focus:
    """Classic-control focus details without UI Automation."""

    def __init__(self, user32, monitors):
        self.user32, self.monitors = user32, monitors
        user32.GetGUIThreadInfo.argtypes = [wintypes.DWORD, ctypes.POINTER(GUITHREADINFO)]
        user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]

    def focused(self, foreground):
        info = GUITHREADINFO(cbSize=ctypes.sizeof(GUITHREADINFO))
        thread = self.user32.GetWindowThreadProcessId(foreground, None)
        if not thread or not self.user32.GetGUIThreadInfo(thread, ctypes.byref(info)) or not info.hwndFocus:
            return None
        buffer = ctypes.create_unicode_buffer(64)
        self.user32.GetClassNameW(info.hwndFocus, buffer, 64)
        role = CLASSIC_TEXT_CLASSES.get(buffer.value.casefold())
        if role is None:
            return {'role': 'control', 'bounds': None, 'key': int(info.hwndFocus)}
        if self.user32.GetWindowLongW(info.hwndFocus, GWL_STYLE) & ES_PASSWORD:
            return {'role': 'secure_input', 'bounds': None, 'key': int(info.hwndFocus)}
        rect = wintypes.RECT()
        self.user32.GetWindowRect(info.hwndFocus, ctypes.byref(rect))
        return {'role': role, 'bounds': logical_rect(self.monitors, rect.left, rect.top, rect.right, rect.bottom),
                'key': int(info.hwndFocus)}


class UIAutomation:
    """Small comtypes adapter. Each thread gets its own COM apartment and client."""

    def __init__(self, monitors):
        import comtypes
        import comtypes.client
        comtypes.client.GetModule('UIAutomationCore.dll')
        from comtypes.gen import UIAutomationClient
        self.comtypes, self.client, self.uia = comtypes, comtypes.client, UIAutomationClient
        self.monitors = monitors
        self.local = threading.local()

    def automation(self):
        if not hasattr(self.local, 'automation'):
            try:
                self.comtypes.CoInitializeEx(self.comtypes.COINIT_MULTITHREADED)
            except OSError:
                pass  # Already initialized in this thread.
            automation = self.client.CreateObject(self.uia.CUIAutomation, interface=self.uia.IUIAutomation)
            try:
                # Bound cross-process calls into hung or very large applications.
                second = automation.QueryInterface(self.uia.IUIAutomation2)
                second.TransactionTimeout = 1500
                second.ConnectionTimeout = 1500
            except (OSError, AttributeError, self.comtypes.COMError):
                pass
            self.local.automation = automation
        return self.local.automation

    def rect(self, value):
        if value.right <= value.left or value.bottom <= value.top:
            return None
        return logical_rect(self.monitors, value.left, value.top, value.right, value.bottom)

    def focused(self):
        try:
            element = self.automation().GetFocusedElement()
            kind = element.CurrentControlType
            if element.CurrentIsPassword:
                return {'role': 'secure_input', 'bounds': None, 'key': tuple(element.GetRuntimeId())}
            return {'role': TEXT_ROLES.get(kind, 'control'), 'bounds': self.rect(element.CurrentBoundingRectangle),
                    'pid': element.CurrentProcessId, 'key': tuple(element.GetRuntimeId())}
        except (OSError, AttributeError, ValueError, self.comtypes.COMError):
            return None

    def controls(self, hwnd):
        """Up to MAX_CONTROLS visible, named, actionable descendants, in document order."""
        automation = self.automation()
        root = automation.ElementFromHandle(hwnd)
        kinds = [automation.CreatePropertyCondition(PROP_CONTROL_TYPE, kind) for kind in ACTIONABLE]
        condition = automation.CreateAndCondition(self.any_of(automation, kinds),
                                                  automation.CreatePropertyCondition(PROP_OFFSCREEN, False))
        cache = automation.CreateCacheRequest()
        for prop in (PROP_BOUNDS, PROP_CONTROL_TYPE, PROP_NAME, PROP_ENABLED):
            cache.AddProperty(prop)
        found = root.FindAllBuildCache(TREE_SCOPE_DESCENDANTS, condition, cache)
        result = []
        for index in range(found.Length if found else 0):
            element = found.GetElement(index)
            name = (element.CachedName or '').strip()
            bounds = self.rect(element.CachedBoundingRectangle)
            if not name or bounds is None:
                continue
            result.append(({'role': ACTIONABLE.get(element.CachedControlType, 'control'), 'label': name[:160],
                            'bounds': bounds, 'enabled': bool(element.CachedIsEnabled)}, element))
            if len(result) >= MAX_CONTROLS:
                break
        return result

    @staticmethod
    def any_of(automation, conditions):
        combined = conditions[0]
        for condition in conditions[1:]:
            combined = automation.CreateOrCondition(combined, condition)
        return combined

    def invoke(self, element):
        """Activate with the most specific supported pattern. Never synthesizes a click."""
        uia = self.uia
        for pattern_id, interface, method in ((PATTERN_INVOKE, uia.IUIAutomationInvokePattern, 'Invoke'),
                                              (PATTERN_SELECTION_ITEM, uia.IUIAutomationSelectionItemPattern, 'Select'),
                                              (PATTERN_TOGGLE, uia.IUIAutomationTogglePattern, 'Toggle'),
                                              (PATTERN_EXPAND, uia.IUIAutomationExpandCollapsePattern, 'Expand'),
                                              (PATTERN_LEGACY, uia.IUIAutomationLegacyIAccessiblePattern, 'DoDefaultAction')):
            try:
                pattern = element.GetCurrentPattern(pattern_id)
            except self.comtypes.COMError:
                continue
            if not pattern:
                continue
            getattr(pattern.QueryInterface(interface), method)()
            return True
        return False

    def page_url(self, hwnd):
        """The address of the page a browser window shows: the value of its web document (Chromium's
        root web area, Firefox's document), the counterpart of AXWebArea's AXURL on macOS. Never
        reads the address bar or any other field. '' when there is none; callers validate it."""
        automation = self.automation()
        root = automation.ElementFromHandle(hwnd)
        document = root.FindFirst(TREE_SCOPE_DESCENDANTS, automation.CreatePropertyCondition(PROP_CONTROL_TYPE, UIA_DOCUMENT))
        if not document:
            return ''
        for prop in (PROP_VALUE, PROP_LEGACY_VALUE):
            value = document.GetCurrentPropertyValue(prop)
            if isinstance(value, str) and value:
                return value
        return ''


class ShellWindows:
    """File Explorer windows through the shell's own Shell.Application object: the folder a
    window shows and the names of its selected items (at most five). Never file contents."""

    def __init__(self):
        import comtypes
        import comtypes.client
        self.comtypes, self.client = comtypes, comtypes.client
        self.local = threading.local()

    def shell(self):
        if not hasattr(self.local, 'shell'):
            try:
                self.comtypes.CoInitializeEx(self.comtypes.COINIT_MULTITHREADED)
            except OSError:
                pass  # Already initialized in this thread.
            self.local.shell = self.client.CreateObject('Shell.Application', dynamic=True)
        return self.local.shell

    def facts(self, hwnd):
        """{'target': folder path, 'selection': [names]} for the Explorer window with this handle;
        {} when no Explorer window has it (the desktop, a dialog) or the folder is virtual."""
        windows = self.shell().Windows()
        for index in range(min(int(windows.Count), 50)):
            window = windows.Item(index)
            if window is None or int(window.HWND) != int(hwnd):
                continue
            view = window.Document
            facts = {}
            folder = str(view.Folder.Self.Path or '')
            if is_file_path(folder):
                facts['target'] = folder[:1024]
            items = view.SelectedItems()
            names = []
            for number in range(min(int(items.Count), 5)):
                item = items.Item(number)
                path = str(item.Path or '')
                # The file's real name (with its extension) when it is a file; else the shown name.
                names.append((path.replace('/', '\\').rstrip('\\').rsplit('\\', 1)[-1] if is_file_path(path) else str(item.Name or ''))[:255])
            facts['selection'] = [name for name in names if name]
            return facts
        return {}


def is_file_path(text):
    """A drive or UNC path, not a virtual shell location ("::{GUID}")."""
    return isinstance(text, str) and len(text) <= 1024 and (
        (len(text) >= 3 and text[0].isascii() and text[0].isalpha() and text[1:3] in (':\\', ':/')) or text.startswith('\\\\'))


def shell_windows():
    """Shell.Application adapter, or None when comtypes is unavailable."""
    try:
        return ShellWindows()
    except (ImportError, OSError, AttributeError):
        return None


def ui_automation(monitors):
    """UI Automation adapter, or None when comtypes/UIAutomationCore is unavailable."""
    try:
        return UIAutomation(monitors)
    except (ImportError, OSError, AttributeError):
        return None
