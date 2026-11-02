"""Windows desktop bridge with the same interface as MacDesktopBridge.

Reads window metadata (titles, owners, bounds) through Win32 and, when UI Automation
is available, the focused element's role and named actionable controls of the
foreground window. Field contents are never read. Computer-task steps also get, as on
macOS, the folder and selected names of a File Explorer window in front and the page
address of a supported browser. Actions: opening catalog apps, supported browsers and
Settings pages from a fixed list, focusing an app, closing an observed window, asking
an app to quit, pressing an observed UIA control, clicking a guarded screen point, and
bounded text/key/scroll input into the observed foreground window.
"""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path, PureWindowsPath
import subprocess
import threading
import time
from urllib.parse import urlsplit

DENIED_EXECUTABLES = {'1password.exe', 'bitwarden.exe', 'keepass.exe', 'keepassxc.exe', 'dashlane.exe',
                      'lastpass.exe', 'credentialuibroker.exe'}
# Hosts and shell surfaces that are not apps the user would ask for by name.
IGNORED_EXECUTABLES = {'textinputhost.exe', 'shellexperiencehost.exe', 'startmenuexperiencehost.exe',
                       'searchhost.exe', 'lockapp.exe', 'systemsettingsbroker.exe'}
# Built-in apps addressed by URI or by an executable on PATH.
BUILTIN_APPS = [
    ('Settings', 'ms-settings:'), ('Calculator', 'calc.exe'), ('Notepad', 'notepad.exe'),
    ('File Explorer', 'explorer.exe'), ('Task Manager', 'taskmgr.exe'), ('Paint', 'mspaint.exe'),
    ('Command Prompt', 'cmd.exe'), ('PowerShell', 'powershell.exe'), ('Control Panel', 'control.exe'),
    ('Snipping Tool', 'ms-screenclip:'), ('Microsoft Store', 'ms-windows-store:'),
    ('Clock', 'ms-clock:'), ('Camera', 'microsoft.windows.camera:'),
]
SKIPPED_SHORTCUT_WORDS = ('uninstall', 'readme', 'read me', 'help', 'documentation', 'license',
                          'release notes', 'website', 'manual')
CATALOG_TTL = 600
# Browsers found through the App Paths registry; ids are stable across machines.
BROWSERS = (('browser:edge', 'Microsoft Edge', 'msedge.exe'), ('browser:chrome', 'Google Chrome', 'chrome.exe'),
            ('browser:firefox', 'Firefox', 'firefox.exe'))
# The same browsers as running windows report them (their executable).
BROWSER_EXECUTABLES = frozenset(executable for _, _, executable in BROWSERS)
EXPLORER = 'explorer.exe'
BLOCKED_INPUT = {'cmd.exe', 'powershell.exe', 'pwsh.exe', 'windowsterminal.exe', 'wt.exe', 'conhost.exe',
                 'openconsole.exe', 'mintty.exe', 'wsl.exe', 'bash.exe', 'regedit.exe', 'mmc.exe'}
GA_ROOT = 2
WM_CLOSE = 0x0010
SW_RESTORE = 9
GW_OWNER = 4
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
DWMWA_CLOAKED = 14
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


class _Win32:
    def __init__(self):
        self.user32 = ctypes.WinDLL('user32', use_last_error=True)
        self.kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        self.dwmapi = ctypes.WinDLL('dwmapi')
        self.version = ctypes.WinDLL('version')
        u, k = self.user32, self.kernel32
        self.EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        u.EnumWindows.argtypes = [self.EnumWindowsProc, wintypes.LPARAM]
        u.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        u.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        u.GetWindowThreadProcessId.restype = wintypes.DWORD
        u.IsWindowVisible.argtypes = [wintypes.HWND]
        u.IsWindow.argtypes = [wintypes.HWND]
        u.IsIconic.argtypes = [wintypes.HWND]
        u.GetWindow.argtypes = [wintypes.HWND, ctypes.c_uint]
        u.GetWindow.restype = wintypes.HWND
        u.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
        u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        u.WindowFromPoint.argtypes = [wintypes.POINT]
        u.WindowFromPoint.restype = wintypes.HWND
        u.GetAncestor.argtypes = [wintypes.HWND, ctypes.c_uint]
        u.GetAncestor.restype = wintypes.HWND
        u.GetForegroundWindow.restype = wintypes.HWND
        u.SetForegroundWindow.argtypes = [wintypes.HWND]
        u.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        u.PostMessageW.argtypes = [wintypes.HWND, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM]
        u.GetDpiForWindow.argtypes = [wintypes.HWND]
        u.GetDpiForWindow.restype = ctypes.c_uint
        u.GetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        u.GetAwarenessFromDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        u.GetAwarenessFromDpiAwarenessContext.restype = ctypes.c_int
        self.dwmapi.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.OpenProcess.restype = wintypes.HANDLE
        k.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        self.version.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
        self.version.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
        self.version.VerQueryValueW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT)]

    def top_level_windows(self):
        found = []
        callback = self.EnumWindowsProc(lambda hwnd, _: found.append(hwnd) or True)
        self.user32.EnumWindows(callback, 0)
        return found

    def title(self, hwnd):
        length = self.user32.GetWindowTextLengthW(hwnd)
        if not length:
            return ''
        buffer = ctypes.create_unicode_buffer(length+1)
        self.user32.GetWindowTextW(hwnd, buffer, length+1)
        return buffer.value

    def pid(self, hwnd):
        value = wintypes.DWORD()
        self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(value))
        return value.value

    def is_app_window(self, hwnd):
        u = self.user32
        if not u.IsWindowVisible(hwnd) or u.GetWindow(hwnd, GW_OWNER):
            return False
        if u.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW:
            return False
        cloaked = wintypes.DWORD()
        self.dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
        return not cloaked.value

    def bounds(self, hwnd):
        rect = wintypes.RECT()
        if not self.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        # Qt and the prompts use logical (device-independent) coordinates. DPI-aware
        # callers (Qt sets per-monitor awareness) get physical pixels; unaware callers
        # already get virtualized logical ones.
        aware = self.user32.GetAwarenessFromDpiAwarenessContext(self.user32.GetThreadDpiAwarenessContext())
        if aware <= 0:
            return {'x': rect.left, 'y': rect.top, 'width': rect.right-rect.left, 'height': rect.bottom-rect.top}
        # Per-monitor mapping, the same as screen captures and UIA controls use, so
        # windows on a scaled secondary monitor line up with OCR and click points.
        from .windows_input import logical_rect
        return logical_rect(self.monitors(), rect.left, rect.top, rect.right, rect.bottom)

    def physical_rect(self, hwnd):
        rect = wintypes.RECT()
        self.user32.GetWindowRect(hwnd, ctypes.byref(rect))
        return rect.left, rect.top, rect.right, rect.bottom

    def root_at(self, x, y):
        """Top-level window under a physical point (what a click would reach)."""
        return self.user32.GetAncestor(self.user32.WindowFromPoint(wintypes.POINT(int(x), int(y))), GA_ROOT)

    def monitors(self):
        if not hasattr(self, '_monitors'):
            from .windows_input import Monitors
            self._monitors = Monitors(self.user32)
        return self._monitors

    def executable(self, pid):
        handle = self.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return ''
        try:
            size = wintypes.DWORD(1024)
            buffer = ctypes.create_unicode_buffer(size.value)
            if self.kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return buffer.value
            return ''
        finally:
            self.kernel32.CloseHandle(handle)

    def description(self, path):
        """FileDescription from version info, e.g. 'Google Chrome'; empty when absent."""
        size = self.version.GetFileVersionInfoSizeW(path, None)
        if not size:
            return ''
        data = ctypes.create_string_buffer(size)
        if not self.version.GetFileVersionInfoW(path, 0, size, data):
            return ''
        pointer, length = ctypes.c_void_p(), wintypes.UINT()
        if not self.version.VerQueryValueW(data, '\\VarFileInfo\\Translation', ctypes.byref(pointer), ctypes.byref(length)) or length.value < 4:
            return ''
        language, codepage = ctypes.cast(pointer, ctypes.POINTER(ctypes.c_ushort*2)).contents
        key = '\\StringFileInfo\\%04x%04x\\FileDescription' % (language, codepage)
        if not self.version.VerQueryValueW(data, key, ctypes.byref(pointer), ctypes.byref(length)) or not length.value:
            return ''
        return ctypes.wstring_at(pointer, length.value).rstrip('\x00').strip()


class WindowsDesktopBridge:
    # SendInput clicks/typing/keys and UI Automation presses are implemented below
    # (windows_input.py). Not yet exercised on real Windows; see docs/ai/CLAUDE_HANDOFF.md.
    supports_input = True

    def __init__(self, directory=None, *, win32=None, monitors=None, sender=None, uia=None, focus=None, shell=None):
        self.win32 = win32 or _Win32()
        self.own_pid = os.getpid()
        self.lock = threading.Lock()
        self.catalog_cache = ([], {}, 0.0)
        self.names = {}
        self._monitors, self._sender, self._uia, self._focus, self._shell = monitors, sender, uia, focus, shell
        self.uia_ready = threading.Event()
        if win32 is None:
            # Reading the Start menu takes a few seconds and the first comtypes import
            # generates UIA wrappers; do both before the first request needs them.
            threading.Thread(target=self.catalog_entries, daemon=True).start()
            threading.Thread(target=self.automation, kwargs={'create': True}, daemon=True).start()
        else:
            self.uia_ready.set()

    # --- Native adapters (created lazily on Windows; doubled in tests) ---
    def monitors(self):
        if self._monitors is None:
            self._monitors = self.win32.monitors()
        return self._monitors

    def sender(self):
        if self._sender is None:
            from .windows_input import SendInput
            self._sender = SendInput(self.win32.user32)
        return self._sender

    def automation(self, *, create=False):
        """UIA adapter once the startup thread has created it; observation never waits for it."""
        if create and self._uia is None:
            from .windows_input import ui_automation
            self._uia = ui_automation(self.monitors()) or False
            self.uia_ready.set()
        return self._uia or None

    def explorer(self):
        """Shell.Application adapter for File Explorer facts; None without comtypes."""
        if self._shell is None:
            from .windows_input import shell_windows
            self._shell = shell_windows() or False
        return self._shell or None

    def focus_reader(self):
        if self._focus is None:
            from .windows_input import Win32Focus
            self._focus = Win32Focus(self.win32.user32, self.monitors())
        return self._focus

    # --- Observation ---
    def app_name(self, path):
        if path not in self.names:
            stem = PureWindowsPath(path).stem
            self.names[path] = (self.win32.description(path) or stem)[:100]
        return self.names[path]

    def windows(self):
        """Visible top-level app windows in z-order (topmost first)."""
        result = []
        for hwnd in self.win32.top_level_windows():
            if not self.win32.is_app_window(hwnd):
                continue
            title = self.win32.title(hwnd)
            pid = self.win32.pid(hwnd)
            if not title or pid == self.own_pid:
                continue
            path = self.win32.executable(pid)
            executable = PureWindowsPath(path).name.lower() if path else ''
            if not executable or executable in DENIED_EXECUTABLES or executable in IGNORED_EXECUTABLES:
                continue
            rect = self.win32.bounds(hwnd)
            if not rect or rect['width'] <= 1 or rect['height'] <= 1:
                continue
            if executable == 'applicationframehost.exe':
                # Store apps (Settings, Calculator…) are hosted; the title names the app.
                bundle_id, name = 'uwp:'+title.casefold(), title[:100]
            else:
                bundle_id, name = executable, self.app_name(path)
            result.append({'hwnd': int(hwnd), 'pid': pid, 'bundle_id': bundle_id, 'name': name,
                           'title': title[:160], 'bounds': rect})
        return result

    def sample(self):
        return self.observe()[0]

    def sample_targets(self, bundle_id=None):
        """A sample plus the exact facts computer-task steps use, as MacDesktopBridge.sample_targets
        gives them: File Explorer's folder and selected names (under 'finder', the key the shared
        task code reads) and a browser's page address (window.url). Each is left out when it cannot
        be read; nothing here asks a model. Windows always observes the window in front."""
        output, front = self.observe()
        if front is None:
            return output
        facts = self.explorer_facts(front)
        if facts:
            output['finder'] = facts
        url = self.page_url(front)
        if url:
            output['window']['url'] = url
        return output

    def explorer_facts(self, window):
        if window['bundle_id'] != EXPLORER:
            return {}
        shell = self.explorer()
        if not shell:
            return {}
        try:
            facts = shell.facts(window['hwnd'])
        except Exception:  # COM errors from a closing or busy Explorer window
            return {}
        result = {}
        if isinstance(facts.get('target'), str) and facts['target']:
            result['target'] = facts['target'][:1024]
        if isinstance(facts.get('selection'), list):
            result['selection'] = [str(name)[:255] for name in facts['selection'] if isinstance(name, str) and name][:5]
        return result

    def page_url(self, window):
        """The browser's page address, only for a supported browser and only a public http(s) one."""
        if window['bundle_id'] not in BROWSER_EXECUTABLES:
            return ''
        automation = self.automation()
        if not automation:
            return ''
        try:
            return page_address(automation.page_url(window['hwnd']))
        except Exception:  # COM errors and timeouts from the browser
            return ''

    def observe(self):
        """(sample, the front window record or None)."""
        windows = self.windows()
        apps, seen = [], set()
        for window in windows:
            if window['bundle_id'] not in seen and len(apps) < 25:
                seen.add(window['bundle_id'])
                apps.append({'name': window['name'], 'bundle_id': window['bundle_id']})
        output = {
            # The installed-app catalog is served by catalog() for launch requests; leaving
            # it out of every observation keeps prompts small enough for local models.
            'platform': 'Windows', 'accessibility': 'granted',
            'available_apps': apps,
            'onscreen_window_candidates': [{'app': w['name'], 'bundle_id': w['bundle_id'], 'bounds': w['bounds']}
                                           for w in windows[:40]],
            'app_windows': [{'window_id': '%s:%d' % (w['bundle_id'], w['hwnd']), 'bundle_id': w['bundle_id'],
                             'name': w['name'], 'title': w['title'], 'bounds': w['bounds'], 'can_close': True}
                            for w in windows[:35]],
            # Control discovery (UI Automation) is not implemented on Windows yet.
            'controls': [],
        }
        foreground = self.win32.user32.GetForegroundWindow()
        if foreground and self.win32.pid(foreground) == self.own_pid:
            output['status'] = 'companion_focused'
            return output, None
        front = next((w for w in windows if w['hwnd'] == int(foreground or 0)), None)
        if front is None:
            output['status'] = 'unavailable'
            return output, None
        output['status'] = 'available'
        output['app'] = {'name': front['name'], 'bundle_id': front['bundle_id'], 'pid': front['pid']}
        output['window'] = {'title': front['title'], 'bounds': front['bounds']}
        focus = self.focused(front)
        if focus:
            # Role and bounds only; field contents are never read.
            output['focused_role'] = focus['role']
            if focus.get('bounds'):
                output['focused_bounds'] = focus['bounds']
        output['controls'] = [descriptor for descriptor, _ in self.controls(front)]
        output['control_source'] = 'uia' if self.automation() else 'unavailable'
        return output, front

    def focused(self, window):
        """Focused element of the foreground window: UIA first, classic Win32 fallback."""
        automation = self.automation()
        focus = automation.focused() if automation else None
        if focus and focus.get('pid') not in (None, window['pid']):
            focus = None
        if focus is None:
            try:
                focus = self.focus_reader().focused(window['hwnd'])
            except (OSError, AttributeError, ValueError):
                focus = None
        return focus

    def controls(self, window):
        automation = self.automation()
        if not automation:
            return []
        try:
            found = automation.controls(window['hwnd'])
        except Exception:  # COM errors and timeouts from the observed app
            return []
        return [(dict(descriptor, id='control-%d' % index), element) for index, (descriptor, element) in enumerate(found)]

    def request_access(self):
        # Window metadata needs no special permission on Windows.
        return {'accessibility': 'granted'}

    # --- Installed-app catalog ---
    @staticmethod
    def shortcut_roots():
        roots = []
        for variable in ('PROGRAMDATA', 'APPDATA'):
            base = os.environ.get(variable)
            if base:
                roots.append(Path(base)/'Microsoft'/'Windows'/'Start Menu'/'Programs')
        return roots

    def catalog_entries(self):
        with self.lock:
            apps, targets, created = self.catalog_cache
            if apps and time.monotonic()-created < CATALOG_TTL:
                return apps, targets, created
            apps, targets, names = [], {}, set()
            for name, target in BUILTIN_APPS:
                bundle_id = 'builtin:'+name.casefold().replace(' ', '-')
                apps.append({'bundle_id': bundle_id, 'name': name})
                targets[bundle_id] = target
                names.add(name.casefold())
            for bundle_id, name, path in self.browser_paths():
                if name.casefold() not in names:
                    apps.append({'bundle_id': bundle_id, 'name': name})
                    targets[bundle_id] = path
                    names.add(name.casefold())
            # Every Start-menu app, including Store/MSIX apps such as the new Teams,
            # which have no .lnk file. Launched through shell:AppsFolder.
            for item in self.start_apps():
                name, app_id = item['name'], item['app_id']
                if (name.casefold() in names or len(apps) >= 400
                        or any(word in name.casefold() for word in SKIPPED_SHORTCUT_WORDS)):
                    continue
                bundle_id = 'startapp:'+app_id
                apps.append({'bundle_id': bundle_id, 'name': name})
                targets[bundle_id] = 'shell:AppsFolder\\'+app_id
                names.add(name.casefold())
            for root in self.shortcut_roots():
                for shortcut in sorted(root.rglob('*.lnk'))[:600] if root.is_dir() else []:
                    name = shortcut.stem.strip()[:100]
                    if (not name or name.casefold() in names or len(apps) >= 400
                            or any(word in name.casefold() for word in SKIPPED_SHORTCUT_WORDS)):
                        continue
                    bundle_id = 'shortcut:'+str(shortcut)
                    apps.append({'bundle_id': bundle_id, 'name': name})
                    targets[bundle_id] = str(shortcut)
                    names.add(name.casefold())
            apps.sort(key=lambda app: app['name'].casefold())
            self.catalog_cache = (apps, targets, time.monotonic())
            return self.catalog_cache

    @staticmethod
    def start_apps():
        """Name and AppID of each Start-menu app, from Windows' own Get-StartApps."""
        script = ('[Console]::OutputEncoding=[Text.Encoding]::UTF8; '
                  'Get-StartApps | Select-Object Name,AppID | ConvertTo-Json -Compress')
        try:
            result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                                    capture_output=True, timeout=15,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            items = json.loads(result.stdout.decode('utf-8-sig') or '[]')
        except (OSError, subprocess.TimeoutExpired, ValueError):
            return []  # .lnk shortcuts below remain a fallback
        if isinstance(items, dict):
            items = [items]
        apps = []
        for item in items if isinstance(items, list) else []:
            name = item.get('Name') if isinstance(item, dict) else None
            app_id = item.get('AppID') if isinstance(item, dict) else None
            if isinstance(name, str) and isinstance(app_id, str) and name.strip() and app_id.strip():
                apps.append({'name': name.strip()[:100], 'app_id': app_id.strip()[:300]})
        return apps

    @staticmethod
    def browser_paths():
        """(id, name, executable) of installed supported browsers from App Paths."""
        try:
            import winreg
        except ImportError:
            return []
        found = []
        for bundle_id, name, executable in BROWSERS:
            for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                try:
                    with winreg.OpenKey(hive, 'SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths\\'+executable) as key:
                        path = winreg.QueryValue(key, None).strip().strip('"')
                except OSError:
                    continue
                if path and os.path.isfile(path):
                    found.append((bundle_id, name, path))
                    break
        return found

    def catalog(self):
        return self.catalog_entries()[0]

    def running_windows(self, bundle_id):
        """Windows of an app, whether addressed by its running id or by its catalog id."""
        windows = self.windows()
        if not bundle_id.startswith(('builtin:', 'startapp:', 'shortcut:')):
            return [w for w in windows if w['bundle_id'] == bundle_id]
        apps, targets, _ = self.catalog_entries()
        name = next((app['name'].casefold() for app in apps if app['bundle_id'] == bundle_id), None)
        target = PureWindowsPath(targets.get(bundle_id, '')).name.casefold()
        return [w for w in windows if name and (w['name'].casefold() == name or w['bundle_id'] in ('uwp:'+name, target))]

    def app_state(self, bundle_id):
        return {'bundle_id': bundle_id, 'running': bool(self.running_windows(bundle_id))}

    # --- Confirmed actions ---
    def activate(self, bundle_id):
        window = next((w for w in self.windows() if w['bundle_id'] == bundle_id), None)
        if window is None:
            raise RuntimeError('The app is no longer available.')
        user32 = self.win32.user32
        if user32.IsIconic(window['hwnd']):
            user32.ShowWindow(window['hwnd'], SW_RESTORE)
        return {'activated': bool(user32.SetForegroundWindow(window['hwnd'])), 'bundle_id': bundle_id}

    def application_action(self, action):
        kind = action['type']
        if kind in ('type_text', 'press_key', 'scroll'):
            return self.input(action)
        if kind == 'browse_url':
            return self.browse(action)
        if kind == 'launch_app':
            target = self.catalog_entries()[1].get(action['bundle_id'])
            if not target:
                raise RuntimeError('That installed application is no longer available.')
            try:
                os.startfile(target)
            except OSError:
                raise RuntimeError('Windows could not open that application.') from None
            # Give the app a moment to show a window before the result is observed.
            deadline = time.monotonic()+3
            while time.monotonic() < deadline and not self.running_windows(action['bundle_id']):
                time.sleep(0.2)
            return {'submitted': True, 'verification_required': True}
        if kind == 'open_settings_pane':
            return self.open_settings_pane(action)
        if kind == 'quit_app':
            windows = [w for w in self.windows() if w['bundle_id'] == action['bundle_id']]
            if not windows:
                raise RuntimeError('That application is no longer running.')
            # Polite close, like macOS terminate(): the app may still ask to save.
            sent = [self.win32.user32.PostMessageW(w['hwnd'], WM_CLOSE, 0, 0) for w in windows]
            self.wait_closed({w['hwnd'] for w in windows})
            return {'submitted': any(sent), 'verification_required': True}
        if kind == 'close_window':
            hwnd = int(action['window_id'].rsplit(':', 1)[1])
            window = next((w for w in self.windows() if w['hwnd'] == hwnd), None)
            expected = action['bounds']
            if (window is None or window['bundle_id'] != action['bundle_id'] or window['title'] != action['window_title']
                    or any(abs(window['bounds'][key]-expected[key]) > 1 for key in ('x', 'y', 'width', 'height'))):
                raise RuntimeError('That window changed or cannot be closed. Request a fresh observation.')
            sent = bool(self.win32.user32.PostMessageW(hwnd, WM_CLOSE, 0, 0))
            self.wait_closed({hwnd})
            return {'submitted': sent, 'verification_required': True}
        raise RuntimeError('New-window automation is currently available only on macOS.')

    def open_settings_pane(self, action):
        """A Settings page from the fixed list (settings_panes.WINDOWS_PANES), opened by its
        ms-settings: URI. Anything else is refused before Windows sees it."""
        from .settings_panes import windows_uri, WINDOWS_APP_IDS
        uri = windows_uri(action.get('pane')) if isinstance(action.get('pane'), str) else None
        if uri is None:
            raise RuntimeError('That Settings page is not in the list.')
        try:
            os.startfile(uri)
        except (OSError, AttributeError):
            raise RuntimeError('Settings could not open that page.') from None
        deadline = time.monotonic()+3
        while time.monotonic() < deadline and not any(w['bundle_id'] in WINDOWS_APP_IDS for w in self.windows()):
            time.sleep(0.2)
        return {'submitted': True, 'verification_required': True}

    def wait_closed(self, handles, timeout=3):
        """Let windows close before the result is observed; an app may also ask to save first."""
        deadline = time.monotonic()+timeout
        while time.monotonic() < deadline and any(self.win32.user32.IsWindow(h) and self.win32.user32.IsWindowVisible(h) for h in handles):
            time.sleep(0.2)

    def foreground_target(self, bundle_id, title):
        """The observed window, brought forward if the companion currently has focus."""
        user32 = self.win32.user32
        foreground = int(user32.GetForegroundWindow() or 0)
        windows = self.windows()
        if foreground and self.win32.pid(foreground) == self.own_pid:
            target = next((w for w in windows if w['bundle_id'] == bundle_id and w['title'] == title), None)
            if target is None or not user32.SetForegroundWindow(target['hwnd']):
                raise RuntimeError('The target window could not be brought forward. Nothing was sent.')
            deadline = time.monotonic()+0.5
            while int(user32.GetForegroundWindow() or 0) != target['hwnd'] and time.monotonic() < deadline:
                time.sleep(0.02)
            foreground = int(user32.GetForegroundWindow() or 0)
        window = next((w for w in windows if w['hwnd'] == foreground), None)
        if window is None or window['bundle_id'] != bundle_id or window['title'] != title:
            raise RuntimeError('Another window became active. Nothing was sent.')
        return window

    def input(self, action):
        window = self.foreground_target(action['bundle_id'], action['window_title'])
        executable = PureWindowsPath(self.win32.executable(window['pid'])).name.lower()
        if executable in BLOCKED_INPUT or executable in DENIED_EXECUTABLES:
            raise RuntimeError('Input is unavailable for that application. Shell and protected-app input is disabled.')
        if any(abs(window['bounds'][key]-action['bounds'][key]) > 1 for key in ('x', 'y', 'width', 'height')):
            raise RuntimeError('The input window moved or changed. Nothing was sent.')
        focus = self.focused(window)
        if not focus or focus['role'] == 'secure_input':
            raise RuntimeError('Input is blocked while a password field or unknown control is focused.')
        sender = self.sender()
        if action['type'] == 'type_text':
            expected = action.get('focused_bounds') or {}
            if (focus['role'] != action.get('focused_role') or not focus.get('bounds')
                    or any(abs(focus['bounds'][key]-expected.get(key, float('inf'))) > 1 for key in ('x', 'y', 'width', 'height'))):
                raise RuntimeError('Focus the same non-password text field before typing.')
            def still_focused():
                current = self.focused(window)
                return (int(self.win32.user32.GetForegroundWindow() or 0) == window['hwnd'] and current is not None
                        and current.get('key') == focus.get('key') and current['role'] != 'secure_input')
            sender.type_text(action['text'], still_focused)
        elif action['type'] == 'press_key':
            sender.press(action['key'])
        else:
            if action['direction'] not in ('up', 'down'):
                raise RuntimeError('Unsupported scroll direction.')
            x, y = self.physical_center(window['hwnd'])
            sender.scroll(x, y, action['direction'])
        return {'submitted': True, 'verification_required': True}

    def physical_center(self, hwnd):
        left, top, right, bottom = self.win32.physical_rect(hwnd)
        return (left+right)//2, (top+bottom)//2

    def browse(self, action):
        url = urlsplit(action.get('url', ''))
        if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password:
            raise RuntimeError('Only a public HTTP(S) address can be opened.')
        path = self.catalog_entries()[1].get(action['bundle_id'])
        if not path or not action['bundle_id'].startswith('browser:'):
            raise RuntimeError('Choose an installed supported browser.')
        mode = action.get('browser_mode', 'default')
        firefox = action['bundle_id'] == 'browser:firefox'
        flags = {'window': ['-new-window' if firefox else '--new-window'],
                 'tab': ['-new-tab'] if firefox else [], 'default': []}.get(mode)
        if flags is None:
            raise RuntimeError('Invalid browser placement.')
        # A fixed executable with the validated URL as one argument; never a shell.
        try:
            subprocess.Popen([path, *flags, url.geturl()], close_fds=True,
                             creationflags=getattr(subprocess, 'DETACHED_PROCESS', 0))
        except OSError:
            raise RuntimeError('Windows could not open that browser.') from None
        return {'submitted': True, 'verification_required': True,
                'tab_placement': mode if mode != 'default' else 'browser_preference'}

    def click(self, action, guard):
        """Click a fraction of the captured monitor, after checking the display is unchanged."""
        screen = guard['screen']
        handle, (mx, my, mw, mh), scale = self.monitors().at(screen['x']+1, screen['y']+1)
        if (handle != guard['display_id'] or abs(mw/scale-screen['width']) > 1 or abs(mh/scale-screen['height']) > 1
                or (mx, my) != (int(screen['x']), int(screen['y']))):
            raise RuntimeError('The display changed. No click was sent.')
        x, y = int(mx+action['x']*mw), int(my+action['y']*mh)
        root = self.win32.root_at(x, y)
        if root and self.win32.pid(root) == self.own_pid:
            raise RuntimeError('The companion is covering that target. Move it aside; no click was sent.')
        self.sender().click(x, y, action.get('button', 'left'), action.get('click_count', 1))
        return {'submitted': True, 'verification_required': True}

    def press(self, action):
        automation = self.automation()
        if not automation:
            raise RuntimeError('Pressing app controls needs Windows UI Automation (install comtypes).')
        window = self.foreground_target(action['bundle_id'], action['window_title'])
        found = dict((descriptor['id'], (descriptor, element)) for descriptor, element in self.controls(window))
        descriptor, element = found.get(action['control_id'], (None, None))
        if (descriptor is None or descriptor['label'] != action['label'] or not descriptor['enabled']
                or any(abs(descriptor['bounds'][key]-action['bounds'][key]) > 1 for key in ('x', 'y', 'width', 'height'))):
            raise RuntimeError('The control changed. Request a fresh observation before pressing it.')
        try:
            invoked = automation.invoke(element)
        except Exception:
            # The app may have started handling it; never retry automatically.
            raise RuntimeError('The press result is uncertain. Check the app before trying again.') from None
        if not invoked:
            raise RuntimeError('That control does not support being pressed. Ask for a visual click instead.')
        return {'submitted': True, 'verification_required': True}


def page_address(text):
    """A browser page address fit for the task's facts: http(s), a host, no credentials, no control
    characters, at most 1024 characters. '' otherwise (edge://, file://, about:blank…)."""
    if not isinstance(text, str) or not text or len(text) > 1024 or any(ord(c) < 32 or ord(c) == 127 for c in text):
        return ''
    try:
        url = urlsplit(text)
        if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password:
            return ''
    except ValueError:
        return ''
    return text
