"""Opt-in macOS metadata and confirmed exact-control actions, independent of Qt/LLM."""
from .input_actions import INPUT_TYPES, authorize_input
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

# macOS bundle IDs and Windows browser ids (see windows_desktop.BROWSERS).
BROWSER_IDS = {'com.apple.Safari', 'com.google.Chrome', 'org.mozilla.firefox', 'com.microsoft.edgemac',
               'browser:edge', 'browser:chrome', 'browser:firefox'}


def default_browser_id():
    """The user's default web browser as a supported browser id, or None.

    Read from system settings (no model): macOS LaunchServices preferences, or the
    Windows https UserChoice. Unsupported browsers return None (Safari/Edge fallback)."""
    try:
        if sys.platform == 'darwin':
            import plistlib
            path = Path.home()/'Library/Preferences/com.apple.LaunchServices/com.apple.launchservices.secure.plist'
            with open(path, 'rb') as handle:
                handlers = plistlib.load(handle).get('LSHandlers', [])
            for scheme in ('https', 'http'):
                for handler in handlers:
                    if isinstance(handler, dict) and handler.get('LSHandlerURLScheme') == scheme:
                        bundle = str(handler.get('LSHandlerRoleAll', ''))
                        return next((known for known in BROWSER_IDS if known.casefold() == bundle.casefold()), None)
            # No browser was ever chosen: macOS uses Safari. Returning None let callers take the first
            # browser in the app list, Google Chrome, so one task opened in Chrome and the next in Safari.
            return 'com.apple.Safari'
        elif sys.platform == 'win32':
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice') as key:
                prog_id = str(winreg.QueryValueEx(key, 'ProgId')[0])
            for prefix, browser in (('MSEdge', 'browser:edge'), ('ChromeHTML', 'browser:chrome'), ('Firefox', 'browser:firefox')):
                if prog_id.startswith(prefix):
                    return browser
    except FileNotFoundError:
        return 'com.apple.Safari' if sys.platform == 'darwin' else None  # never set: the system default
    except (OSError, ValueError, ImportError):
        return None
    return None


# Calendar/Reminders calls may wait for the user to answer the macOS permission prompt.
EVENTKIT_FLAGS = ('--calendar-events', '--reminders', '--add-event', '--add-reminder', '--complete-reminder', '--delete-reminder')


FINDER_FACTS_SCRIPT = """on run argv
if (item 1 of argv) is not "com.apple.finder" then return ""
tell application "Finder"
    if not frontmost or (count of Finder windows) is 0 then return ""
    set w to front Finder window
    set folderPath to POSIX path of (target of w as alias)
    set viewName to current view of w as text
    set columnName to ""
    try
        set columnName to name of sort column of list view options of w as text
    end try
    -- Names only (at most five), never contents: what a selection click selected.
    set picked to ""
    try
        set chosen to selection
        repeat with i from 1 to (count of chosen)
            if i > 5 then exit repeat
            if i > 1 then set picked to picked & (character id 31)
            set picked to picked & (name of item i of chosen)
        end repeat
    end try
    return folderPath & linefeed & viewName & linefeed & columnName & linefeed & picked
end tell
end run"""


def finder_facts(snapshot):
    """Finder only, with a second frontmost check inside the script; no activation."""
    bundle = (snapshot.get('app') or {}).get('bundle_id')
    if sys.platform != 'darwin' or bundle != 'com.apple.finder':
        return {}
    started=time.monotonic()
    outcome='empty'
    try:
        result = subprocess.run(['osascript', '-e', FINDER_FACTS_SCRIPT, bundle],
                                capture_output=True, text=True, timeout=1)
        out = result.stdout[:-1] if result.stdout.endswith('\n') else result.stdout  # osascript's own newline only
        lines = out.split('\n')
        if result.returncode == 0 and len(lines) >= 2 and lines[0]:
            outcome='available'
            facts = dict(zip(('target', 'view', 'sort'), (line[:1024] for line in lines)))
            if len(lines) >= 4:
                facts['selection'] = [name[:255] for name in lines[3].split('\x1f') if name][:5]
            return facts
        if result.returncode:
            outcome='error'
    except subprocess.TimeoutExpired:
        outcome='timeout'
    except OSError:
        outcome='unavailable'
    finally:
        from .timing import timing
        timing('finder.facts',started,status=outcome)
    return {}


SAFARI_COUNTS_SCRIPT = """on run argv
if (item 1 of argv) is not "com.apple.Safari" then return ""
if application "Safari" is not running then return ""
tell application "Safari"
    if (count of windows) is 0 then return ""
    set w to front window
    set b to bounds of w
    return (id of w as text) & linefeed & (count of tabs of w as text) & linefeed & (item 1 of b as text) & linefeed & (item 2 of b as text) & linefeed & (item 3 of b as text) & linefeed & (item 4 of b as text)
end tell
end run"""


def browser_counts(snapshot):
    """Bounded counts only, bound to the observed Safari window. Never activates it."""
    if sys.platform != 'darwin' or (snapshot.get('app') or {}).get('bundle_id') != 'com.apple.Safari':
        return {}
    bounds = (snapshot.get('window') or {}).get('bounds') or {}
    import math
    if not all(type(bounds.get(k)) in (int, float) and math.isfinite(bounds[k]) for k in ('x','y','width','height')) or min(bounds['width'],bounds['height']) <= 0:
        return {}
    try:
        result = subprocess.run(['osascript','-e',SAFARI_COUNTS_SCRIPT,'com.apple.Safari'],
                                capture_output=True,text=True,timeout=1)
        if result.returncode:
            return {}
        parts = result.stdout.strip().splitlines()
        if len(parts) != 6:
            return {}
        window_id,count,left,top,right,bottom = map(int,parts)
        actual = (left,top,right-left,bottom-top)
        if window_id <= 0 or not 1 <= count <= 10000 or any(abs(a-bounds[k])>2 for a,k in zip(actual,('x','y','width','height'))):
            return {}
        return dict(window_id=window_id,tab_count=count)
    except (OSError,ValueError,subprocess.TimeoutExpired):
        return {}


class MacDesktopBridge:
    def __init__(self, directory):
        from .paths import bundled_helper
        self.source = Path(__file__).resolve().parent.parent / 'native' / 'DesktopBridge.swift'
        self.prebuilt = bundled_helper('desktop-bridge')  # packaged app: compiled at build time
        self.binary = self.prebuilt or Path(directory).resolve() / 'desktop-bridge'
        self.lock = threading.Lock()

    def prepare(self):
        if sys.platform != 'darwin':
            raise RuntimeError('Live desktop context currently supports macOS.')
        if self.prebuilt:
            return
        with self.lock:
            if self.binary.exists() and self.binary.stat().st_mtime >= self.source.stat().st_mtime:
                return
            self.binary.parent.mkdir(parents=True, exist_ok=True)
            try:
                result = subprocess.run(['xcrun', 'swiftc', '-O', '-module-cache-path',
                    str(self.binary.parent/'swift-cache'), str(self.source), '-o', str(self.binary)],
                    capture_output=True, timeout=60)
            except (OSError, subprocess.TimeoutExpired):
                raise RuntimeError('macOS bridge needs Xcode Command Line Tools. Install them with xcode-select --install.') from None
            if result.returncode:
                raise RuntimeError('Could not build the macOS desktop bridge. Check Xcode Command Line Tools.')

    def call(self, *args):
        from .eval_control import guard_native_test_call
        guard_native_test_call(args)
        self.prepare()
        try:
            result = subprocess.run([str(self.binary), '--exclude-pid', str(os.getpid()), *args],
                                    capture_output=True, timeout=90 if any(flag in args for flag in EVENTKIT_FLAGS) else 45 if any(flag in args for flag in ('--browse', '--music-state', '--music-play')) or ('--new-window' in args and 'com.apple.Safari' in args) else 4)
            value = json.loads(result.stdout)
            if result.returncode or not isinstance(value, dict) or value.get('error'):
                raise RuntimeError(str((value.get('error') if isinstance(value, dict) else '') or 'Desktop operation failed; the app may have changed.'))
            return value
        except subprocess.TimeoutExpired:
            raise RuntimeError('The operation timed out and may have been submitted. Check the app before retrying.') from None
        except (OSError, ValueError):
            raise RuntimeError('Desktop context is unavailable. Check macOS Accessibility permissions.') from None

    def sample(self):
        return self.call()

    def sample_target(self, bundle_id):
        return self.call('--observe-app', bundle_id)

    def sample_targets(self, bundle_id=None):
        """A sample plus every clickable target in the window (computer-task steps only)."""
        value = self.call('--targets', *(('--observe-app', bundle_id) if bundle_id else ()))
        facts = finder_facts(value)
        if facts:
            value['finder'] = facts
        counts = browser_counts(value)
        if counts:
            value['browser'] = counts
        return value

    def request_access(self):
        return self.call('--request-access')

    def activate(self, bundle_id):
        return self.call('--activate', bundle_id)

    def menu_items(self, bundle_id):
        """The app's enabled menu items as "Menu > Item" paths (names only)."""
        return self.call('--menu-items', bundle_id).get('items', [])

    def hit(self, x, y):
        """What is at a screen point (role, title, the app a Dock icon opens), without pressing it."""
        return self.call('--hit', str(x), str(y))

    def click(self, action, guard):
        screen = guard['screen']
        return self.call('--click', str(screen['x']+action['x']*screen['width']),
                         str(screen['y']+action['y']*screen['height']), str(guard['display_id']), json.dumps(screen), action.get('button', 'left'), str(action.get('click_count', 1)))

    def drag(self, action, guard):
        screen = guard['screen']
        at = lambda fx, fy: (str(screen['x']+fx*screen['width']), str(screen['y']+fy*screen['height']))
        return self.call('--drag', *at(action['x'], action['y']), *at(action['to_x'], action['to_y']), str(guard['display_id']), json.dumps(screen))

    def catalog(self):
        return self.call('--catalog').get('installed_apps', [])

    def app_state(self, bundle_id):
        return self.call('--app-state', bundle_id)

    def media_state(self):
        return self.call('--music-state')

    def calendar_events(self, start, end):
        try:
            return self.call('--calendar-events', str(start), str(end)).get('events', [])
        except RuntimeError as error:
            from .paths import frozen
            # From source, macOS refuses EventKit silently (the terminal declares no calendar use,
            # so no prompt is shown); the packaged app declares it. Ask the Calendar app instead:
            # that is Automation, which macOS does ask about.
            if frozen() or 'Calendars' not in str(error):
                raise
            events = calendar_app_events(start, end)
            if events is None:
                raise
            return events

    def reminders(self):
        return self.call('--reminders').get('reminders', [])

    def add_event(self, title, start, end):
        return self.call('--add-event', title, str(start), str(end))

    def add_reminder(self, title, due):
        return self.call('--add-reminder', title, str(due or 0))

    def complete_reminder(self, identifier):
        return self.call('--complete-reminder', identifier)

    def delete_reminder(self, identifier):
        return self.call('--delete-reminder', identifier)

    def application_action(self, action):
        kind = action['type']
        if kind in INPUT_TYPES:
            return self.call('--input', json.dumps(action))
        if kind == 'play_media':
            return self.call('--music-play', action['query'], action['artist'])
        if kind == 'music_search':
            # Fixed command, no shell: the Music app opens its Apple Music search page.
            from urllib.parse import quote
            url = 'https://music.apple.com/search?term='+quote(action['query'])
            try:
                result = subprocess.run(['open', '-b', 'com.apple.Music', url], capture_output=True, timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                raise RuntimeError('Music could not open Apple Music search.') from None
            if result.returncode:
                raise RuntimeError('Music could not open Apple Music search.')
            return {'submitted': True, 'verification_required': True, 'url': url}
        if kind == 'browse_url':
            from .web_apps import same_site_hosts
            # An open tab of the same site is reused; these are the other hosts it may show.
            return self.call('--browse', action['bundle_id'], action['url'], action.get('browser_mode', 'default'),
                             action.get('window_hint', ''), ','.join(same_site_hosts(action['url'])))
        if kind == 'close_tab':
            return self.call('--close-tab', action['bundle_id'])
        if kind == 'choose_menu':
            return self.call('--menu', action['bundle_id'], action['path'])
        if kind == 'close_window':
            return self.call('--close-window', action['bundle_id'], action['window_id'].rsplit(':', 1)[1],
                             action['window_title'], json.dumps(action['bounds']))
        flag = {'launch_app': '--launch', 'quit_app': '--quit', 'new_window': '--new-window'}[kind]
        return self.call(flag, action['bundle_id'])

    def press(self, action):
        return self.call('--press', action['bundle_id'], action['control_id'], action['label'],
                         json.dumps(action['bounds']), action['window_title'])


def desktop_bridge(directory):
    """Native bridge for this platform. Other platforms keep the macOS bridge, which reports unsupported."""
    if sys.platform == 'win32':
        from .windows_desktop import WindowsDesktopBridge
        return WindowsDesktopBridge(directory)
    return MacDesktopBridge(directory)


class DesktopContext:
    def __init__(self, bridge, *, clock=time.monotonic):
        self.bridge, self.clock = bridge, clock
        self.lock = threading.RLock()
        self.collection_lock = threading.Lock()
        self.enabled = False
        self.snapshot = {}
        self.observed = 0
        self.checked = 0
        self.error = ''
        self.generation = 0
        self.thread = None
        self.stop_event = threading.Event()

    def enable(self, *, request_permission=True):
        with self.lock:
            if self.enabled:
                return
            self.enabled = True
            self.generation += 1
            generation = self.generation
            stop = self.stop_event = threading.Event()
            self.error = 'Preparing desktop access…'
        self.thread = threading.Thread(target=self._run, args=(generation, stop, request_permission), daemon=True)
        self.thread.start()

    def disable(self):
        with self.lock:
            self.enabled = False
            self.generation += 1
            self.stop_event.set()
            self.snapshot = {}
            self.error = ''
            self.observed = self.checked = 0

    def _run(self, generation, stop, request_permission=True):
        try:
            if request_permission:
                self.bridge.request_access()
        except RuntimeError as e:
            with self.lock:
                if generation == self.generation:
                    self.error = str(e)
            return
        while not stop.is_set():
            self.collect_once(generation=generation)
            stop.wait(2)

    def collect_once(self, *, generation=None, target_bundle=None, targets=False):
        # Keep a background poll from publishing over an in-flight detailed
        # sample. Access/Stop uses the separate state lock and remains immediate.
        with self.collection_lock:
            return self._collect_once(generation=generation,target_bundle=target_bundle,targets=targets)

    def _collect_once(self, *, generation=None, target_bundle=None, targets=False):
        with self.lock:
            if not self.enabled:
                return
        try:
            if targets and hasattr(self.bridge, 'sample_targets'):
                value = self.bridge.sample_targets(target_bundle)
            else:
                value = self.bridge.sample_target(target_bundle) if target_bundle and hasattr(self.bridge, 'sample_target') else self.bridge.sample()
            with self.lock:
                if not self.enabled or (generation is not None and generation != self.generation):
                    return
                now = self.clock()
                self.checked = now
                self.error = ''
                if value.get('status') == 'companion_focused' and self.snapshot.get('app'):
                    self.snapshot['companion_focused'] = True
                    self.snapshot['available_apps'] = value.get('available_apps', [])
                    self.snapshot['onscreen_window_candidates'] = value.get('onscreen_window_candidates', [])
                    self.snapshot['app_windows'] = value.get('app_windows', [])
                    self.snapshot['installed_apps'] = value.get('installed_apps', [])
                else:
                    self.snapshot = value
                    self.observed = now
                return self.context()  # copy while holding the state lock, before the next poll
        except RuntimeError as e:
            with self.lock:
                if generation is None or generation == self.generation:
                    self.error = str(e)
                    self.snapshot = {}

    def context(self):
        with self.lock:
            if not self.enabled:
                return {'enabled': False, 'status': 'off'}
            if self.error:
                return {'enabled': True, 'status': 'unavailable', 'reason': self.error}
            if not self.checked or self.clock()-self.checked > 10:
                return {'enabled': True, 'status': 'stale', 'reason': 'Desktop observation is not fresh.'}
            result = deepcopy(self.snapshot)
            result['enabled'] = True
            result['age_seconds'] = round(max(0, self.clock()-self.observed), 1)
            if result['age_seconds'] > 15:
                result['controls'] = []
                result['controls_stale'] = True
            signature = {k: result.get(k) for k in ('app', 'window', 'controls')}
            result['observation_id'] = hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()[:16]
            return result

    def installed_apps(self):
        if hasattr(self.bridge, 'catalog'):
            return self.bridge.catalog()
        return self.snapshot.get('installed_apps', [])

    def app_state(self, bundle_id):
        return self.bridge.app_state(bundle_id) if hasattr(self.bridge, 'app_state') else {}

    def media_state(self):
        return self.bridge.media_state()

    def authorize_action(self, action, *, snapshot=None):
        context = snapshot if snapshot is not None else self.context()
        if isinstance(action, dict) and action.get('type') in INPUT_TYPES:
            return authorize_input(action, context)
        if isinstance(action, dict) and action.get('type') == 'play_media':
            if set(action) != {'type', 'query', 'artist'} or not isinstance(action['query'], str) or not action['query'].strip() or len(action['query']) > 500 or not isinstance(action['artist'], str) or len(action['artist']) > 200 or any(ord(c) < 32 for c in action['query']+action['artist']):
                raise ValueError('Name a song title and optional artist.')
            if not any(app['bundle_id'] == 'com.apple.Music' for app in self.installed_apps()):
                raise ValueError('Music is not installed.')
            return dict(action, bundle_id='com.apple.Music', label='Find and play “'+action['query']+'” in Music')
        if isinstance(action, dict) and action.get('type') == 'open_settings_pane':
            # Navigation only: an exact pane identifier from the system's own catalog, never free text.
            from .settings_panes import pane, settings_app
            entry = pane(action.get('pane')) if set(action) == {'type', 'pane'} else None
            if entry is None:
                raise ValueError('That Settings pane is not in the catalog.')
            app_id, app_name = settings_app()
            return {'type': 'open_settings_pane', 'pane': entry['id'], 'bundle_id': app_id,
                    'label': 'Open '+entry['name']+' in '+app_name}
        if isinstance(action, dict) and set(action) == {'type', 'bundle_id'} and action['type'] == 'launch_app':
            app = next((item for item in self.installed_apps() if item['bundle_id'] == action['bundle_id']), None)
            if app:
                return {'type': 'launch_app', 'bundle_id': app['bundle_id'], 'label': 'Open '+app['name']}
            raise ValueError('That application is not in the installed-app catalog.')
        if action == {'type': 'new_window', 'bundle_id': 'com.apple.Safari'}:
            if any(app['bundle_id'] == 'com.apple.Safari' for app in self.installed_apps()):
                return dict(action, label='Open a new Safari window')
            raise ValueError('Safari is not installed.')
        if not context.get('enabled') or context.get('status') not in ('available', 'companion_focused'):
            raise ValueError('Fresh desktop context is required for this action.')
        if not isinstance(action, dict):
            raise ValueError('Invalid desktop action.')
        kind = action.get('type')
        if kind in ('launch_app', 'quit_app', 'new_window') and set(action) == {'type', 'bundle_id'}:
            records = context.get('installed_apps', []) if kind == 'launch_app' else context.get('available_apps', [])
            app = next((item for item in records if item['bundle_id'] == action['bundle_id']), None)
            if app:
                if kind == 'new_window' and context.get('accessibility') == 'permission_required':
                    raise ValueError('A new window needs macOS Accessibility access. Request app access instead.')
                verb = {'launch_app': 'Open ', 'quit_app': 'Quit ', 'new_window': 'Open a new window in '}[kind]
                return {'type': kind, 'bundle_id': app['bundle_id'], 'label': verb+app['name']}
        elif kind == 'choose_menu' and set(action) == {'type', 'path'} and isinstance(action['path'], str) \
                and 0 < len(action['path']) <= 160 and not any(ord(c) < 32 for c in action['path']):
            # A menu item of the app in front, pressed by its titles ("View > as List").
            app = context.get('app') or {}
            if app.get('bundle_id'):
                path = ' > '.join(part.strip() for part in action['path'].split('>') if part.strip())
                return {'type': kind, 'path': path, 'bundle_id': app['bundle_id'], 'label': 'Choose '+path.replace(' > ', ' › ')+' in '+app.get('name', 'the app')}
        elif kind == 'close_tab' and set(action) == {'type', 'bundle_id'} and action['bundle_id'] in ('com.apple.Safari', 'com.google.Chrome', 'com.microsoft.edgemac'):
            app = next((a for a in context.get('available_apps', []) if a['bundle_id'] == action['bundle_id']), None)
            if app:
                return {'type': kind, 'bundle_id': app['bundle_id'], 'label': 'Close the current '+app['name']+' tab'}
        elif kind == 'close_window' and set(action) == {'type', 'window_id'}:
            window = next((item for item in context.get('app_windows', [])
                           if item['window_id'] == action['window_id'] and item.get('can_close')), None)
            if window:
                return {'type': kind, 'window_id': window['window_id'], 'bundle_id': window['bundle_id'],
                        'window_title': window['title'], 'bounds': window['bounds'],
                        'label': 'Close '+window['name']+' — '+window['title']}
        if kind == 'focus_app'  and set(action) == {'type', 'bundle_id'}:
            app = next((a for a in context.get('available_apps', []) if a['bundle_id'] == action['bundle_id']), None)
            if app:
                return {'type': kind, 'bundle_id': app['bundle_id'], 'label': 'Switch to '+app['name']}
        elif kind in ('highlight_control', 'press_control') and set(action) == {'type', 'control_id'}:
            control = next((c for c in context.get('controls', []) if c['id'] == action['control_id'] and c.get('enabled', True)), None)
            if control:
                return {'type': kind, 'control_id': control['id'], 'label': control['label'],
                        'bounds': control['bounds'], 'observation_id': context['observation_id'],
                        'bundle_id': context.get('app', {}).get('bundle_id', ''),
                        'window_title': context.get('window', {}).get('title', ''),
                        'confirmation_label': ('Press “'+control['label']+'” in '+context.get('app', {}).get('name', 'the app')) if kind == 'press_control' else 'Highlight '+control['label']}
        raise ValueError('The requested desktop target was not observed or is no longer available.')

    def authorize_browser(self, action):
        app = next((app for app in self.installed_apps() if app['bundle_id'] == action['bundle_id'] and app['bundle_id'] in BROWSER_IDS), None)
        if app is None:
            raise ValueError('Choose an installed supported browser.')
        return dict(action)

    def application_action(self, proposal):
        kind = proposal['type']
        if kind in INPUT_TYPES:
            self.collect_once(target_bundle=proposal.get('bundle_id') if self.context().get('companion_focused') else None)
            field = {'type_text': 'text', 'press_key': 'key', 'scroll': 'direction'}[kind]
            current = self.authorize_action({'type': kind, field: proposal[field]})
            for key in ('bundle_id', 'window_title', 'bounds', 'focused_role', 'focused_bounds'):
                if current.get(key) != proposal.get(key):
                    raise ValueError('The input destination changed. Nothing was sent; focus the target and ask again.')
            result = self.bridge.application_action(current)
            if not result.get('submitted'):
                raise RuntimeError('Input submission is uncertain. Check the app before retrying.')
            self.collect_once()
            return result
        if kind == 'open_settings_pane':
            current = self.authorize_action({'type': kind, 'pane': proposal.get('pane')})
            if sys.platform == 'win32':
                # The Windows bridge opens ms-settings:<page> after checking the page against its fixed list.
                result = self.bridge.application_action(current)
                if not result.get('submitted'):
                    raise RuntimeError('Settings could not open that page.')
                self.collect_once()
                return result
            try:
                result = subprocess.run(['open', 'x-apple.systempreferences:'+current['pane']], capture_output=True, timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                raise RuntimeError('System Settings could not open that pane.') from None
            if result.returncode:
                raise RuntimeError('System Settings could not open that pane.')
            self.collect_once()
            return {'submitted': True, 'verification_required': True}
        if kind == 'music_search':
            query = proposal.get('query')
            if not isinstance(query, str) or not query.strip() or len(query) > 300 or any(ord(c) < 32 for c in query):
                raise ValueError('Name a song to search for.')
            if not any(app['bundle_id'] == 'com.apple.Music' for app in self.installed_apps()):
                raise ValueError('Music is not installed.')
            return self.bridge.application_action({'type': 'music_search', 'query': query.strip()})
        if kind == 'play_media':
            from .media import near_match, split_artist
            query, artist = proposal['query'], proposal['artist']
            current = self.authorize_action({'type': 'play_media', 'query': query, 'artist': artist})
            result = self.bridge.application_action(current)
            split = split_artist(query, artist)
            if result.get('outcome') == 'not_found' and split != (query, artist):
                # "Last Christmas by Wham": the whole title is tried first ("Stand by Me"
                # must stay one title), then title and artist separately.
                query, artist = split
                result = self.bridge.application_action(self.authorize_action({'type': 'play_media', 'query': query, 'artist': artist}))
            match = near_match(result, query, artist)
            if match:
                # The library has exactly one track with this title plus credits or a
                # version tag ("Far Away (feat. …)"): play that exact track.
                retry = self.authorize_action({'type': 'play_media', 'query': match[0], 'artist': match[1]})
                result = dict(self.bridge.application_action(retry), matched_title=match[0])
            return result
        if proposal['type'] == 'browse_url':
            result = self.bridge.application_action(self.authorize_browser(proposal))
            if not result.get('submitted'):
                raise RuntimeError('The browser did not confirm accepting that address. Nothing will be retried automatically.')
            self.collect_once()
            return result
        if proposal['type'] == 'choose_menu':
            current = self.authorize_action({'type': 'choose_menu', 'path': proposal['path']})
            if current['bundle_id'] != proposal.get('bundle_id'):
                raise ValueError('A different app is in front now. Nothing was chosen.')
            result = self.bridge.application_action(current)
            if not result.get('submitted'):
                raise RuntimeError('The menu item did not respond.')
            self.collect_once()
            return result
        fields = ('type', 'window_id') if proposal['type'] == 'close_window' else ('type', 'bundle_id')  # close_tab too
        current = self.authorize_action({key: proposal[key] for key in fields})
        if proposal['type'] == 'close_window' and any(current[key] != proposal.get(key) for key in ('window_title', 'bounds', 'bundle_id')):
            raise ValueError('That window changed. Ask again before closing it.')
        result = self.bridge.application_action(current)
        if not result.get('submitted'):
            raise RuntimeError('The app action was not confirmed. Check the app before trying again.')
        self.collect_once()
        return result

    def press(self, proposal):
        with self.lock:
            if not self.enabled or self.error:
                raise ValueError('App awareness must be enabled before pressing a control.')
            signature = {key: self.snapshot.get(key) for key in ('app', 'window', 'controls')}
            current_id = hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()[:16]
            if proposal.get('observation_id') != current_id:
                raise ValueError('The observed window changed. Ask again before pressing this control.')
            if proposal.get('type') != 'press_control' or not proposal.get('bundle_id'):
                raise ValueError('No observed control is available to press.')
        # The native helper re-reads the actual window/control before AXPress, even
        # when the companion is focused. It checks label, bounds and window title.
        result = self.bridge.press(proposal)
        if not result.get('submitted'):
            raise RuntimeError('The application did not accept the press action. Nothing will be retried automatically.')
        self.collect_once()
        return result

    def activate(self, bundle_id):
        action = self.authorize_action({'type': 'focus_app', 'bundle_id': bundle_id})
        result = self.bridge.activate(action['bundle_id'])
        if not result.get('activated'):
            raise RuntimeError('macOS did not activate the app.')
        return result


CALENDAR_SCRIPT = (
    'on run argv',
    '  set base to current date',
    '  set startDate to base + ((item 1 of argv) as integer)',
    '  set endDate to base + ((item 2 of argv) as integer)',
    '  set output to ""',
    '  tell application "Calendar"',
    '    repeat with c in calendars',
    '      repeat with e in (every event of c whose start date < endDate and end date > startDate)',
    '        set output to output & (summary of e) & tab & (((start date of e) - base) as integer) & tab & (((end date of e) - base) as integer) & tab & (allday event of e) & tab & (name of c) & linefeed',
    '      end repeat',
    '    end repeat',
    '  end tell',
    '  return output',
    'end run')


def calendar_app_events(start, end):
    """Events between two epoch times from the Calendar app (AppleScript, values passed as
    argv), in the helper's format; None when Calendar can't be asked. Recurring events show
    only where AppleScript lists them, so this is a fallback for source runs only."""
    import time
    now = int(time.time())
    try:
        result = subprocess.run(['osascript', *sum((['-e', line] for line in CALENDAR_SCRIPT), []),
                                 str(int(start)-now), str(int(end)-now)], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode:
        return None
    events = []
    for line in result.stdout.splitlines():
        parts = line.split('\t')
        if len(parts) != 5:
            continue
        try:
            begins, ends = now+int(parts[1]), now+int(parts[2])
        except ValueError:
            continue
        events.append({'title': parts[0][:300], 'start': begins, 'end': ends, 'all_day': parts[3] == 'true',
                       'location': '', 'calendar': parts[4][:100]})
    return sorted(events, key=lambda event: event['start'])
