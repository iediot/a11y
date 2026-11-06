#!/usr/bin/env python3
"""Legacy desktop diagnostics. Prefer scripts/human_eval.py for outcome evaluation.
Legacy diagnostic, not an acceptance gate; see docs/ai/TESTING.md

    .venv/bin/python scripts/live_eval.py --suite harder --runs 3
    .venv/bin/python scripts/live_eval.py --suite capability --runs 1

It launches A11y from source with A11Y_EVAL=1 (see companion/eval_control.py) and local
Ollama forced: nothing calls the cloud. Each request is typed into the companion exactly
as a user would; the harness never confirms a proposal, so writes (a reminder, a note)
stay unconfirmed. Results are checked against the real state of the Mac (Safari's page,
Mail's selection, the mascot's position, Calendar's events) and each run records success,
seconds, the route taken and why it failed. Timing records (model calls, prompt tokens,
step stages) come from the app's own `A11y timing` stream.

These diagnostics can change desktop state and require explicit screen availability.
Suites containing prohibited private applications are disabled, including their setup.
Use an explicitly configured data directory; no access consent is granted by the runner.
Every collected case is also screened: one whose request, setup or cleanup names a
private application is refused unless --allow-private-apps is given (see
refuse_private_cases). Inventory: docs/ai/LEGACY_LIVE_SUITES.md.
Legacy pass counts are diagnostic results, not a current capability baseline."""
import argparse
from datetime import datetime
from decimal import Decimal, InvalidOperation, localcontext
import inspect
import json
import os
from pathlib import Path
import re
import socket
import statistics
import subprocess
import sys
import time
import types

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
NEUTRAL = 'https://example.com/'
ONLY = []  # --only for the task suites too
TEST_MARKER = 'A11y test'  # the only changes the harness may confirm (see companion/eval_control.py)
TEAMS_HOSTS = ('teams.microsoft.com', 'teams.cloud.microsoft', 'teams.live.com')
SAFE_LEGACY_SUITES = frozenset({'answers', 'varied', 'conversation', 'broad', 'harder', 'features', 'capability', 'grounding'})
PROHIBITED_HARNESS_TARGETS = frozenset(name.casefold() for name in (
    'Mail', 'Messages', 'Photos', 'Contacts', 'Microsoft Teams', 'Teams', 'WhatsApp', 'FaceTime',
    'com.apple.mail', 'com.apple.MobileSMS', 'com.apple.Photos', 'com.apple.AddressBook',
    'com.microsoft.teams', 'com.microsoft.teams2', 'net.whatsapp.WhatsApp', 'com.apple.FaceTime',
))


# --- the Mac ---------------------------------------------------------------------------------

def osa(*lines, timeout=30):
    result = subprocess.run(native_command('osascript', *sum((['-e', line] for line in lines), [])),
                            capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(result.stderr.strip()[:300])
    return result.stdout.strip()


def native_command(*arguments):
    guard_harness_script(arguments)
    # Only short native probes, never inference/training. Background I/O policy
    # made each AppleScript launch take seconds while doing no model work.
    return (['/bin/sh', '-c', 'taskpolicy -B -p $$ && exec "$@"', 'native-probe']
            if sys.platform == 'darwin' else [])+list(arguments)


def guard_harness_script(arguments):
    """Reject explicit private-app AppleScript targets before launching osascript.

    This also protects the argv-based entrypoint in live_heldout. It is a guard for
    harness-authored probes, not a parser or sandbox for arbitrary AppleScript.
    """
    if not arguments or Path(str(arguments[0])).name != 'osascript':
        return
    scripts, argv = [], []
    index = 1
    while index < len(arguments):
        argument = str(arguments[index])
        if argument == '-e' and index+1 < len(arguments):
            scripts.append(str(arguments[index+1]))
            index += 2
        elif argument in ('-l', '-s') and index+1 < len(arguments):
            index += 2
        else:
            argv.append(argument)
            index += 1
    source = '\n'.join(scripts)
    targets = re.findall(r'\b(?:application(?:\s+id)?|process)\s+"([^"]+)"', source, re.I)
    for position in re.findall(r'\b(?:application(?:\s+id)?|process)\s+\(?item\s+(\d+)\s+of\s+argv\)?', source, re.I):
        if 0 < int(position) <= len(argv):
            targets.append(argv[int(position)-1])
    if any(target.casefold() in PROHIBITED_HARNESS_TARGETS for target in targets):
        raise RuntimeError('Private applications are prohibited in desktop evaluation; use scripts/human_eval.py with owned fixtures.')


# --- private-application screening of collected cases ---------------------------------------

# Applications tests must never open or script (AGENTS.md), as words in a request or in the
# strings of harness-authored setup/cleanup code. "email" and "inbox" imply Mail. The
# look-behind keeps URL fragments (EvalWindow.ensure's "https://teams" prefix test) and
# identifiers (mail_front) from matching; Teams hosts and bundle identifiers match separately.
PRIVATE_APP_TEXT = re.compile(
    r"(?<![\w./@-])(?:e-?mails?|mails?|inbox|imessages?|messages|photos|contacts|address\s+book|"
    r"whatsapp|facetime|(?:microsoft\s+)?teams)(?![\w-])"
    r"|teams\.(?:microsoft\.com|live\.com|cloud\.microsoft)"
    r"|\bcom\.apple\.(?:mail|mobilesms|photos|addressbook|facetime)\b"
    r"|\bcom\.microsoft\.teams2?\b|\bnet\.whatsapp\b", re.I)
_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
_CO_HAS_DOCSTRING = 0x4000000  # Python 3.14+: a function's docstring is co_consts[0]


def private_app_mentions(value):
    """Private-application names in a request, or reachable from a setup/cleanup callable.

    Static: nothing is called. A callable contributes its string constants (not its
    docstring), string defaults and closure values, and recursively the harness functions
    it names (globals, module attributes such as le.activate, methods of closure objects
    such as EvalWindow.ensure) when they are defined in scripts/ or beside the callable.
    This is a tripwire for harness-authored cases, not a sandbox: a computed string escapes
    it, which is why guard_harness_script still checks every osascript call at run time.
    """
    found = []
    _scan_private(value, found, set(), None)
    return sorted({name.casefold(): name for name in found}.values(), key=str.casefold)


def _scan_private(value, found, seen, home):
    if isinstance(value, str):
        found.extend(match.group(0) for match in PRIVATE_APP_TEXT.finditer(value))
    elif isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            _scan_private(item, found, seen, home)
    elif isinstance(value, dict):
        for item in value.values():
            _scan_private(item, found, seen, home)
    elif inspect.ismethod(value):
        _scan_private(value.__func__, found, seen, home)
    elif inspect.isfunction(value):
        _scan_private_function(value, found, seen, home)


def _scan_private_function(function, found, seen, home):
    code = function.__code__
    if id(function) in seen:
        return
    if home is None:
        home = code.co_filename  # the case's own callable is always read
    elif code.co_filename != home and os.path.dirname(os.path.abspath(code.co_filename)) != _SCRIPTS_DIR:
        return  # library code (subprocess, unittest.mock) is not harness-authored
    seen.add(id(function))
    _scan_private(function.__defaults__ or (), found, seen, home)
    _scan_private(list((function.__kwdefaults__ or {}).values()), found, seen, home)
    objects = []
    for cell in function.__closure__ or ():
        try:
            content = cell.cell_contents
        except ValueError:  # an unassigned free variable
            continue
        if isinstance(content, (str, list, tuple, set, frozenset, dict)) or inspect.isfunction(content) or inspect.ismethod(content):
            _scan_private(content, found, seen, home)
        elif not inspect.ismodule(content) and not isinstance(content, type):
            objects.append(content)
    _scan_private_code(code, function.__doc__, function.__globals__, objects, found, seen, home)


def _nested_docstring(code):
    consts = code.co_consts
    if code.co_name.startswith('<') or not consts or not isinstance(consts[0], str):
        return None
    if sys.version_info >= (3, 14):
        return consts[0] if code.co_flags & _CO_HAS_DOCSTRING else None
    return consts[0]  # before 3.14 a function without a docstring starts with None


def _scan_private_code(code, docstring, namespace, objects, found, seen, home):
    for constant in code.co_consts:
        if isinstance(constant, types.CodeType):
            _scan_private_code(constant, _nested_docstring(constant), namespace, objects, found, seen, home)
        elif isinstance(constant, (str, tuple, frozenset)) and not (docstring is not None and constant == docstring):
            _scan_private(constant, found, seen, home)
    modules = []
    for name in code.co_names:
        target = namespace.get(name)
        if inspect.ismodule(target):
            path = getattr(target, '__file__', None)
            if path and os.path.dirname(os.path.abspath(path)) == _SCRIPTS_DIR:
                modules.append(target)
        elif inspect.isfunction(target):
            _scan_private_function(target, found, seen, home)
    for name in code.co_names:
        for owner in modules:
            attribute = getattr(owner, name, None)
            if inspect.isfunction(attribute):
                _scan_private_function(attribute, found, seen, home)
        for owner in objects:
            attribute = getattr(type(owner), name, None)  # class attribute: no property is evaluated
            if inspect.isfunction(attribute):
                _scan_private_function(attribute, found, seen, home)


def _case_parts(case):
    """(label, [(part, value)]) for a Case, a live_tasks.Task or a conversation-style dict.

    Checks are deliberately not screened: they observe state, and an observing check that
    scripts a private app is still stopped by guard_harness_script. A cleanup step given as
    (request, verify) contributes its request only.
    """
    def steps(values):
        return [step[0] if isinstance(step, tuple) and step else step for step in values or ()]

    def task_parts(task, prefix=''):
        return [(prefix+'request', str(task.request)), (prefix+'setup', getattr(task, 'setup', None)),
                (prefix+'cleanup', steps(getattr(task, 'cleanup', ())))]
    if isinstance(case, dict):
        parts = [('setup', case.get('setup')), ('cleanup', steps(case.get('cleanup')))]
        for index, turn in enumerate(case.get('turns', ()), 1):
            parts += task_parts(turn, 'turn %d ' % index)
        return str(case.get('name', '?')), parts
    return getattr(case, 'family', None) or str(case.request)[:60], task_parts(case)


def private_case_reasons(case):
    """(label, reasons): why a collected case touches a private application, if it does."""
    label, parts = _case_parts(case)
    reasons = []
    for part, value in parts:
        names = private_app_mentions(value)
        if names:
            reasons.append('%s names %s' % (part, ', '.join(names)))
    return label, reasons


def refuse_private_cases(cases, allow=False, report=None):
    """The cases that may run. Without allow, every case naming a private application in its
    request, setup or cleanup is dropped before anything starts, and the reason is printed."""
    report = report or (lambda line: print(line, flush=True))
    kept = []
    for case in cases:
        label, reasons = private_case_reasons(case)
        if reasons and not allow:
            report('refused %s: %s. Tests must not open or script Mail, Messages, Photos, Contacts, Teams, '
                   'WhatsApp or FaceTime (AGENTS.md); --allow-private-apps exists only for an explicit owner '
                   'instruction.' % (label, '; '.join(reasons)))
            continue
        if reasons:
            report('running private-application case %s because --allow-private-apps was given: %s' % (label, '; '.join(reasons)))
        kept.append(case)
    return kept


def arithmetic_result(left, operation, right):
    """Independent synthetic oracle, rounded to ten decimal places for display comparison."""
    with localcontext() as context:
        context.prec = 40
        left, right = Decimal(str(left)), Decimal(str(right))
        if operation == '+':
            value = left+right
        elif operation == '-':
            value = left-right
        elif operation == '*':
            value = left*right
        elif operation == '/':
            value = left/right
        else:
            raise ValueError('Unsupported oracle operation.')
        return value.quantize(Decimal('0.0000000001')).normalize()


NUMBER = re.compile(r'(?<![\w.])[+\-−]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][+\-]?\d+)?(?!\w|\.\d)')


def number_equals(value, expected):
    """Compare one complete signed decimal, never an integer substring."""
    value = str(value).strip().replace('−', '-')
    if not NUMBER.fullmatch(value):
        return False
    try:
        with localcontext() as context:
            context.prec = 40
            actual = Decimal(value.replace(',', ''))
            return actual.is_finite() and actual.quantize(Decimal('0.0000000001')) == Decimal(str(expected))
    except InvalidOperation:
        return False


def number_in_text(message, expected):
    return any(number_equals(match.group(), expected) for match in NUMBER.finditer(str(message)))


def calculator_display():
    """Read a labelled result/display control, excluding keypad and history text.

    Unknown Calculator accessibility layouts fail closed with no display value.
    This probe does not call A11y's desktop or arithmetic implementations.
    """
    return osa('''tell application "System Events" to tell process "Calculator"
repeat with control in entire contents of front window
    try
        set controlRole to role of control
        if controlRole is in {"AXStaticText", "AXTextField", "AXTextArea"} then
            set controlLabel to description of control as text
            try
                set controlLabel to controlLabel & " " & (value of attribute "AXIdentifier" of control as text)
            end try
            if controlLabel contains "result" or controlLabel contains "display" then
                try
                    return value of control as text
                on error
                    return name of control as text
                end try
            end if
        end if
    end try
end repeat
return ""
end tell''')


def calculator_matches(expected):
    return front_app() == 'Calculator' and number_equals(calculator_display(), expected)


def same_path(actual, expected):
    """macOS aliases /var and /tmp to /private; compare filesystem identity paths."""
    if not actual or not expected:
        return False
    try:
        return Path(actual).resolve() == Path(expected).resolve()
    except (OSError,TypeError,ValueError):
        return False


def park_test_pointer(runner):
    """Keep fixture windows from appearing under the pointer's traffic-light hover.

    Move only, to the test companion's observed face, before fixture setup.
    No click, hot corner, OS setting or application data is changed.
    """
    if sys.platform != 'darwin':
        return
    state = runner.call({'cmd':'state','since':10**9})
    face = state.get('ui',{}).get('face')
    if not isinstance(face,list) or len(face)!=2 or any(type(v) not in (int,float) for v in face):
        return
    import ctypes
    class Point(ctypes.Structure):
        _fields_=[('x',ctypes.c_double),('y',ctypes.c_double)]
    core = ctypes.CDLL('/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics')
    core.CGWarpMouseCursorPosition.argtypes=[Point]
    core.CGWarpMouseCursorPosition.restype=ctypes.c_int32
    core.CGWarpMouseCursorPosition(Point(*face))


def running(process):
    return osa('tell application "System Events" to return exists process "'+process+'"') == 'true'


def front_app():
    return osa('tell application "System Events" to return name of first process whose frontmost is true')


def activate(app):
    osa('tell application "'+app+'" to activate')
    time.sleep(0.8)


def mail_front():
    """Mail in front with a message viewer window (it can be open with none)."""
    osa('tell application "Mail"', 'if (count of message viewers) is 0 then make new message viewer', 'activate', 'end tell')
    time.sleep(1.2)


def safari_windows():
    """{window id: tab count}; empty (without launching Safari) when it is not running."""
    if not running('Safari'):
        return {}
    for attempt in range(3):  # a window closing mid-listing makes AppleScript fail
        try:
            out = osa('set out to ""', 'tell application "Safari"', 'repeat with w in windows',
                      'set out to out & (id of w) & ":" & (count of tabs of w) & ","', 'end repeat', 'end tell', 'return out', timeout=15)
            break
        except subprocess.TimeoutExpired:
            return {}  # Safari not answering (it hung once and the whole run crashed): treat as no windows
        except RuntimeError:
            if attempt == 2:
                raise
            time.sleep(0.5)
    return {int(a): int(b) for a, b in (item.split(':') for item in out.split(',') if ':' in item)}


# Apps a run never quits: the desktop itself, the terminal driving the run, A11y (a Python
# process from source) and the assistants working in it.
KEEP_RUNNING = {'Finder', 'Terminal', 'Python', 'A11y', 'Claude', 'Codex', 'Dock', 'SystemUIServer', 'loginwindow'}
# Apps whose windows have ids AppleScript can close one by one.
WINDOW_APPS = {'Safari': 'windows', 'Google Chrome': 'windows', 'Finder': 'Finder windows',
               'TextEdit': 'windows', 'Preview': 'windows'}
TAB_APPS = ('Safari', 'Google Chrome')


def app_names():
    """Names of the running apps that have a Dock presence."""
    try:
        out = osa('tell application "System Events" to return name of every process whose background only is false', timeout=15)
    except (RuntimeError, subprocess.TimeoutExpired):
        return set()
    return {name.strip() for name in out.split(',') if name.strip()}


def window_ids(app):
    try:
        out = osa('tell application "%s" to return id of every %s' % (app, WINDOW_APPS[app][:-1] if WINDOW_APPS[app].endswith('s') else WINDOW_APPS[app]),
                  timeout=8)
    except (RuntimeError, subprocess.TimeoutExpired):
        return set()  # a stuck app (TextEdit behind a save sheet) must not fail the next test
    return {int(item) for item in out.replace(' ', '').split(',') if item.strip().isdigit()}


def tab_counts(app):
    counts = {}
    for window in window_ids(app):
        try:
            counts[window] = int(osa('tell application "%s" to return count of tabs of (window id %d)' % (app, window), timeout=8))
        except (RuntimeError, ValueError, subprocess.TimeoutExpired):
            pass
    return counts


DISCARD_DRAFT = '''on run argv
tell application "System Events" to tell process "Mail"
    set w to window (item 1 of argv)
    if not ((value of every text field of w) as string) contains "test@example.com" then return "not a test draft"
    click (first button of w whose subrole is "AXCloseButton")
    delay 0.8
    click button 2 of sheet 1 of w
end tell
return "discarded"
end run'''


def mail_window_ids():
    try:
        return {int(v) for v in osa('tell application "Mail" to return id of every window', timeout=8).split(', ') if v.strip()}
    except (RuntimeError, ValueError, subprocess.TimeoutExpired):
        return set()


def discard_test_drafts(before):
    """Close compose windows a test opened to the test address, without saving and never sending.
    Mail ignores a scripted close of a compose window and lists it as no "outgoing message", so its
    close button is pressed and the sheet answered “Don’t Save” (button 2: Save, Don’t Save, Cancel)."""
    closed = []
    for window in sorted(mail_window_ids()-before):
        try:
            name = osa('tell application "Mail" to return name of (window id %d)' % window, timeout=8)
            result = subprocess.run(['osascript', '-e', DISCARD_DRAFT, name], capture_output=True, text=True, timeout=15)
        except (RuntimeError, subprocess.TimeoutExpired):
            continue
        if result.stdout.strip() == 'discarded':
            closed.append('test mail draft')
    return closed


TEXTEDIT_FOLDER = Path.home()/'Library'/'Mobile Documents'/'com~apple~TextEdit'/'Documents'
TRASH_NEW = '''on run argv
set cutoff to (current date) - (item 2 of argv as integer)
set out to 0
tell application "Finder"
    try
        set found to (every file of folder (POSIX file (item 1 of argv) as alias) whose creation date > cutoff)
    on error
        return 0
    end try
    repeat with f in found
        delete f
        set out to out + 1
    end repeat
end tell
return out
end run'''


def trash_test_documents(started):
    """TextEdit documents a test created (owner's OK, 2026-10-25): TextEdit saves new documents to its
    iCloud folder on its own, even when quit "saving no". Files there created since the test started go
    to the Trash, by Finder (no iCloud prompt), never deleted outright."""
    seconds = int(time.time()-started)+5
    try:
        result = subprocess.run(['osascript', '-e', TRASH_NEW, str(TEXTEDIT_FOLDER), str(seconds)], capture_output=True, text=True, timeout=30)
        count = int(result.stdout.strip() or 0)
    except (subprocess.TimeoutExpired, ValueError):
        return []
    return ['trashed test document']*count


def snapshot():
    """What is open before a test: running apps, window ids, browser tab counts."""
    apps = app_names()
    return {'apps': apps, 'mail_windows': mail_window_ids() if 'Mail' in apps else set(), 'started': time.time(),

            'windows': {app: window_ids(app) for app in WINDOW_APPS if app in apps},
            'tabs': {app: tab_counts(app) for app in TAB_APPS if app in apps}}


def restore(before):
    """Close what a test opened and leave what was there: tabs added to windows that were
    open, windows that are new, test mail drafts, and apps that weren't running (quit without
    saving). Returns what it closed, for the run record."""
    closed = []
    now = app_names()

    def quietly(script):
        try:
            osa(script, timeout=15)
            return True
        except (RuntimeError, subprocess.TimeoutExpired):
            return False
    for app in TAB_APPS:
        if app in before['apps'] and app in now:
            for window, count in before['tabs'].get(app, {}).items():
                try:
                    current = int(osa('tell application "%s" to return count of tabs of (window id %d)' % (app, window)))
                except (RuntimeError, ValueError):
                    continue
                for index in range(current, count, -1):
                    if quietly('tell application "%s" to close tab %d of (window id %d)' % (app, index, window)):
                        closed.append('%s tab' % app)
    for app, kind in WINDOW_APPS.items():
        if app in before['apps'] and app in now:
            for window in sorted(window_ids(app)-before['windows'].get(app, set())):
                saving = ' saving no' if app in ('TextEdit', 'Preview') else ''
                if quietly('tell application "%s" to close (%s id %d)%s' % (app, kind[:-1], window, saving)):
                    closed.append('%s window' % app)
    if 'Mail' in now and 'Mail' in before['apps']:
        closed += discard_test_drafts(before.get('mail_windows', set()))
    if 'Mail' in now:
        # Only drafts a test wrote (to the test address); never send, never touch other drafts.
        script = ('tell application "Mail"', 'set n to 0', 'repeat with m in (every outgoing message)',
                  'if (address of every to recipient of m) contains "test@example.com" then',
                  'close m saving no', 'set n to n + 1', 'end if', 'end repeat', 'end tell', 'return n')
        try:
            if int(osa(*script, timeout=15) or 0):
                closed.append('test mail draft')
        except (RuntimeError, ValueError, subprocess.TimeoutExpired):
            pass
    # A window left full screen is in its own Space and breaks every task after it (owner): undo it.
    try:
        undone = osa('set out to 0', 'tell application "System Events"', 'repeat with p in (every process whose visible is true)',
                     'repeat with w in (every window of p)', 'try', 'if value of attribute "AXFullScreen" of w is true then',
                     'set value of attribute "AXFullScreen" of w to false', 'set out to out + 1', 'end if', 'end try',
                     'end repeat', 'end repeat', 'end tell', 'return out', timeout=20)
        if undone and undone != '0':
            closed.append('left full screen ×'+undone)
            time.sleep(1.5)  # the window animates back from its Space
    except (RuntimeError, subprocess.TimeoutExpired):
        pass
    for app in sorted(now-before['apps']-KEEP_RUNNING):
        if not quietly('tell application "%s" to quit saving no' % app) and not quietly('tell application "%s" to quit' % app):
            subprocess.run(['pkill', '-x', app])
        closed.append('quit '+app)
    if 'TextEdit' in now:
        time.sleep(1)  # a quitting TextEdit writes its documents last
        closed += trash_test_documents(before.get('started', time.time()))
    return closed


def safari_restore(before):
    """Close the Safari windows and tabs a run added; never one that existed before it.

    Only windows newer than every window seen before the run (Safari numbers them in order)
    and tabs appended after a window's earlier count. Without a snapshot nothing is closed.
    Returns the addresses closed, for the run record."""
    closed = []
    if not before:
        return closed
    newest = max(before)
    def url(script):
        try:
            return osa(script)
        except RuntimeError:
            return '?'
    def ours(address):
        # Only pages these cases open; a tab the user opened meanwhile is left alone.
        return (address in ('', 'missing value', 'favorites://') or address.startswith('safari-extension://')
                or any(host in address for host in TEAMS_HOSTS+('youtube.com', 'example.com', 'duckduckgo.com', 'safari.com')))
    for window, tabs in safari_windows().items():
        if window not in before and window > newest:
            address = url('tell application "Safari" to return URL of current tab of (window id %d)' % window)
            if ours(address) and osa('tell application "Safari" to return count of tabs of (window id %d)' % window) == '1':
                closed.append(address)
                osa('tell application "Safari" to close (window id %d)' % window)
        elif window in before:
            for index in range(tabs, before[window], -1):
                address = url('tell application "Safari" to return URL of tab %d of (window id %d)' % (index, window))
                if ours(address):
                    closed.append(address)
                    osa('tell application "Safari" to close tab %d of (window id %d)' % (index, window))
    return closed


def safari_front_tab():
    """(address, title) of Safari's front tab; Teams' unread count prefix "(2) " is dropped."""
    if not running('Safari'):
        return '', ''
    out = osa('tell application "Safari" to return (URL of current tab of front window) & "\n" & (name of current tab of front window)')
    url, _, title = out.partition('\n')
    return url, re.sub(r'^\(\d+\)\s*', '', title)


class EvalWindow:
    """The harness's own Safari window for web cases."""
    def __init__(self):
        self.id = None

    def ensure(self, url=NEUTRAL, wait_title=None):
        if self.id is None or self.id not in safari_windows():
            self.id = int(osa('tell application "Safari"', 'make new document with properties {URL:"'+url+'"}',
                              'return id of front window', 'end tell'))
        elif not osa('tell application "Safari" to return URL of current tab of window id %d' % self.id).startswith(url.rstrip('/')):
            current = osa('tell application "Safari" to return URL of current tab of window id %d' % self.id)
            if not (wait_title and any(host in current for host in TEAMS_HOSTS) and url.startswith('https://teams')):
                osa('tell application "Safari" to set URL of current tab of window id %d to "%s"' % (self.id, url))
        osa('tell application "Safari"', 'set index of window id %d to 1' % self.id, 'activate', 'end tell')
        deadline = time.monotonic()+30
        while wait_title and time.monotonic() < deadline:
            if wait_title in osa('tell application "Safari" to return name of current tab of window id %d' % self.id):  # noqa
                break
            time.sleep(0.5)
        time.sleep(0.8)

    def close(self):
        if self.id is not None and self.id in safari_windows():
            osa('tell application "Safari" to close (window id %d)' % self.id)


def teams_view(eval_window, view):
    """Teams on the web, frontmost, showing Activity (1) or Chat (2)."""
    eval_window.ensure('https://teams.microsoft.com/', wait_title='Microsoft Teams')
    name = {1: 'Activity', 2: 'Chat'}[view]
    for _ in range(10):  # a cold Teams page ignores shortcuts until it has loaded
        osa('tell application "Safari" to activate')
        time.sleep(0.4)
        osa('tell application "System Events" to keystroke "%d" using {control down, shift down}' % view)
        time.sleep(1.6)
        if safari_front_tab()[1].startswith(name):
            return
    raise RuntimeError('Teams did not switch to '+name)


def mail_newest(n):
    from companion import mail_reader
    found = mail_reader.newest(n)
    return found[1] if found else None


def mail_selection():
    try:
        return osa('tell application "Mail" to return subject of item 1 of (get selection)')
    except RuntimeError:
        return None


def calendar_reply(scope):
    from companion import mac_apps
    from companion.desktop_context import desktop_bridge
    return mac_apps.read_calendar(desktop_bridge(ROOT/'.data'), scope)


def reminders_reply(scope):
    from companion import mac_apps
    from companion.desktop_context import desktop_bridge
    return mac_apps.read_reminders(desktop_bridge(ROOT/'.data'), scope)


def desktop_sample(bundle=None):
    from companion.desktop_context import desktop_bridge
    bridge = desktop_bridge(ROOT/'.data')
    return bridge.sample_target(bundle) if bundle else bridge.sample()


def window_title(app_name):
    """Title of the app's first window, from A11y's own desktop helper (no Automation prompt).
    Finder's "⁨Documents⁩ — Local" is "Documents" (Unicode isolates and the location dropped)."""
    title = next((w['title'] for w in desktop_sample().get('app_windows', []) if w['name'] == app_name and w['title']), None)
    return re.sub('[\u2066-\u2069]', '', title).split(' — ')[0].strip() if title is not None else None


def settings_pane_is(name):
    """True when System Settings shows the pane: its header, right of the sidebar (OCR)."""
    from companion.vision import ScreenVision
    sample = desktop_sample('com.apple.systempreferences')
    # The window's title names the pane ("Battery – Fully Charged"); the header OCR alone failed when
    # her reply window lay over it (live).
    if re.split(r'\s+[–—-]\s+', str((sample.get('window') or {}).get('title', '')))[0].strip() == name:
        return True
    window = (sample.get('window') or {}).get('bounds') or {}
    sidebar = next((item['bounds'] for item in sample.get('lists', []) if item.get('label') == 'Sidebar'), None)
    if not window or not sidebar:
        return False
    vision = ScreenVision((ROOT/'.data').resolve())
    frame = vision.call('--x', '0', '--y', '0', '--ocr')
    screen = frame['screen']
    for region in frame.get('text_regions', []):
        x = screen['x']+(region['x']+region['width']/2)*screen['width']
        y = screen['y']+(region['y']+region['height']/2)*screen['height']
        if (region['text'].strip() == name and sidebar['x']+sidebar['width'] < x < window['x']+window['width']
                and window['y'] <= y < window['y']+window['height']*0.4):
            return True
    return False


def quit_settings():
    subprocess.run(['pkill', '-x', 'System Settings'])
    time.sleep(1)


def screen_lines(window_only=True):
    """OCR lines on screen, by default only those inside the front window (A11y's helper)."""
    from companion.vision import ScreenVision
    sample = desktop_sample()
    frame = ScreenVision((ROOT/'.data').resolve()).call('--x', '0', '--y', '0', '--ocr')
    screen, bounds = frame['screen'], (sample.get('window') or {}).get('bounds')
    lines = []
    for region in frame.get('text_regions', []):
        x = screen['x']+(region['x']+region['width']/2)*screen['width']
        y = screen['y']+(region['y']+region['height']/2)*screen['height']
        if not window_only or not bounds or (bounds['x'] <= x < bounds['x']+bounds['width'] and bounds['y'] <= y < bounds['y']+bounds['height']):
            lines.append(region['text'])
    return lines


def shows(text, window_only=True):
    """True when the text (letters and digits only, any case) is on screen in the front window,
    allowing fast OCR's one-letter slips in longer words ("ideo" for "idea")."""
    import difflib
    wanted = re.sub(r'[^\w]', '', text.casefold())
    lines = [re.sub(r'[^\w]', '', line.casefold()) for line in screen_lines(window_only)]
    if any(wanted in line for line in lines) or wanted in ''.join(lines):
        return True
    if len(wanted) < 4:
        return False
    for line in lines:
        for start in range(0, max(1, len(line)-len(wanted)+1)):
            if difflib.SequenceMatcher(None, wanted, line[start:start+len(wanted)]).ratio() >= 1-1.5/len(wanted):
                return True
    return False


def settings_panes():
    """The panes in System Settings' sidebar on this Mac (read from the app, not assumed)."""
    subprocess.run(['open', '-a', 'System Settings'])
    time.sleep(2.5)
    sample = desktop_sample('com.apple.systempreferences')
    sidebar = next((item['bounds'] for item in sample.get('lists', []) if item.get('label') == 'Sidebar'), None)
    names = []
    from companion.desktop_context import desktop_bridge
    targets = desktop_bridge(ROOT/'.data').sample_targets('com.apple.systempreferences').get('targets', [])
    for target in targets:
        b = target['bounds']
        if sidebar and sidebar['x'] <= b['x']+b['width']/2 < sidebar['x']+sidebar['width'] and target['role'] == 'AXButton' \
                and ',' not in target['label'] and len(target['label']) < 30:
            names.append(target['label'])
    quit_settings()
    return names


# --- the app ---------------------------------------------------------------------------------

class App:
    def __init__(self, data, log):
        import tempfile
        # A private folder: macOS limits socket paths to 104 characters, too short for some data folders.
        self.socket = Path(tempfile.mkdtemp(prefix='a11y-eval-'))/'eval.sock'
        # Passing ROOT/'.data' keeps the historical setup. Isolated callers must
        # explicitly configure consent/settings in their own directory first.
        self.data = Path(data).resolve()
        self.log = log
        self.process = None
        from companion.instance import build_id
        self.build = build_id()

    def check_build(self):
        from companion.instance import build_id
        if build_id() != self.build:
            raise SystemExit('Build ID changed during live run; aborting mixed-build results.')

    def call(self, command, timeout=25):
        self.check_build()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(timeout)
            connection.connect(str(self.socket))
            connection.sendall(json.dumps(command).encode()+b'\n')
            data = b''
            while not data.endswith(b'\n'):
                chunk = connection.recv(1 << 20)
                if not chunk:
                    break
                data += chunk
        if not data:
            raise RuntimeError('A11y did not answer (exit code %s)' % (self.process.poll() if self.process else '?'))
        self.check_build()
        reply = json.loads(data)
        if 'error' in reply:
            raise RuntimeError('A11y: '+reply['error'])
        return reply

    def launch(self):
        self.check_build()
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(('ANTHROPIC_', 'OPENAI_')) and key not in ('A11Y_AI_BASE_URL', 'A11Y_AI_KEY')}
        if getattr(self, 'shots', None):
            env['A11Y_EVAL_SHOTS'] = str(self.shots)
        env.update(A11Y_EVAL='1', A11Y_EVAL_SOCKET=str(self.socket), A11Y_DATA_DIR=str(self.data), A11Y_AI_PROVIDER='ollama', A11Y_WEB_ANSWERS=os.getenv('A11Y_WEB_ANSWERS', '1'),
                   A11Y_ROUTES='0', PYTHONUNBUFFERED='1')  # a test run neither learns nor follows routes (they leaked between runs)
        if self.socket.exists():
            self.socket.unlink()
        self.process = subprocess.Popen([sys.executable, str(ROOT/'a11y_desktop.py')], cwd=ROOT, env=env,
                                        stdout=open(self.log, 'a'), stderr=subprocess.STDOUT)
        deadline = time.monotonic()+90
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise SystemExit('A11y exited at launch; see '+str(self.log))
            try:
                state = self.call({'cmd': 'state', 'since': 10**9})
                if all(state['ready'].values()) and state['idle']:
                    return state
            except (OSError, ValueError, RuntimeError):
                pass
            time.sleep(0.5)
        self.process.terminate()  # never leave a second A11y running behind (it happened, live)
        raise SystemExit('A11y did not get ready: configure app awareness and screen vision explicitly in '+str(self.data)+'. The harness does not grant consent.')

    def quit(self):
        if self.process and self.process.poll() is None:
            try:
                self.call({'cmd': 'stop'})
                self.call({'cmd': 'quit'})
                self.process.wait(15)
            except (OSError, RuntimeError, subprocess.TimeoutExpired, SystemExit):
                self.process.terminate()
        self.check_build()

    def run(self, request, timeout):
        """Type the request, wait until the companion is quiet, return what happened."""
        start = self.call({'cmd': 'state', 'since': 10**9})['event_count']
        while True:
            try:
                self.call({'cmd': 'ask', 'request': request})
                break
            except RuntimeError as error:
                if 'busy' not in str(error):
                    raise
                time.sleep(0.3)
        return self.wait(timeout, start)

    def wait(self, timeout, start=None):
        """Until the companion is quiet: (state, events since start, timed out)."""
        if start is None:
            start = self.call({'cmd': 'state', 'since': 10**9})['event_count']
            time.sleep(0.3)
        deadline, events, state = time.monotonic()+timeout, [], {}
        samples = []
        while time.monotonic() < deadline:
            state = self.call({'cmd': 'state', 'since': start+len(events)})
            events.extend(state['events'])
            if 'ui' in state:
                samples.append({'t': state['now'], 'steps': (state.get('task') or {}).get('steps', 0), **state['ui']})
                state['_ui_samples'] = samples
            if state['idle'] and state['quiet_for'] >= 1.6:
                return state, events, False
            time.sleep(0.2)
        self.call({'cmd': 'stop'})
        return state, events, True


# --- cases -----------------------------------------------------------------------------------

def said(events):
    return [event['data']['message'] for event in events if event['kind'] == 'say']


def final_message(events):
    """The last thing said that is not a progress line."""
    from companion.desktop import progress_message
    lines = [message for message in said(events) if not progress_message(message)]
    return lines[-1] if lines else ''


def failed_reply(message):
    return bool(re.search(r"\b(?:I (?:could not|couldn[’']t|cannot|can[’']t) (?:finish|do|find|work out|confirm|see|identify|produce)|I stopped|so I stopped|nothing (?:was done|(?:on the screen )?changed)|not (?:marked complete|on the screen)|same action was proposed|stopped at the .*action limit|result could not be verified)", message, re.I))


def task_status(state):
    return (state.get('task') or {}).get('status')


def check_task_done(state, events):
    status = task_status(state)
    if status != 'done':
        return 'task ended '+str(status)+': '+final_message(events)[:160]
    return None


class Case:
    def __init__(self, request, check, *, setup=None, timeout=120, routes=(), quiet=False):
        self.request, self.check, self.setup, self.timeout, self.routes = request, check, setup, timeout, routes
        self.quiet = quiet  # answered in place: opening or switching to anything is a failure
        self.restore_after = True


def build_answers(eval_window):
    """Questions she should just answer (weather, facts, things that changed since the model
    was trained), and the few requests that should open something."""
    def says(pattern):
        def answer_check(state, events):
            message = final_message(events)
            return None if re.search(pattern, message, re.I) else 'reply: '+message[:160]
        return answer_check

    def searched(pattern):
        def search_check(state, events):
            url, _ = safari_front_tab()
            return None if front_app() == 'Safari' and re.search(pattern, url, re.I) else 'front '+front_app()+' '+url[:80]
        return search_check

    def shows(app):
        def app_check(state, events):
            return None if front_app() == app else 'front is '+front_app()
        return app_check
    quiet = [('what is the weather like?', r'°'), ('will it rain in Paris tomorrow?', r'Paris'),
             ('what is the temperature in Tokyo right now?', r'Tokyo.*°|°.*Tokyo'),
             ("what's the weather in New York this Friday?", r'Friday'),
             ('who wrote Romeo and Juliet?', r'Shakespeare'), ('what is the capital of Australia?', r'Canberra'),
             ('who won the 2025 Eurovision Song Contest?', r'Austria|JJ|Wasted Love'),
             ('who is the CEO of Bitdefender?', r'Talpe'), ('what is the population of Cluj-Napoca?', r'28\d|286'),
             ('how many moons does Neptune have?', r'16'), ('when did the Berlin Wall fall?', r'1989'),
             ('who directed Anatomy of a Fall?', r'Triet'), ('how tall is Mount Everest?', r'8,?8[0-9]{2}')]
    cases = [Case(request, says(pattern), quiet=True, timeout=90) for request, pattern in quiet]
    cases += [Case('search for romeo and juliet', searched(r'romeo')),
              Case('open safari and search for lasagna recipe', searched(r'lasagna')),
              Case('show me my reminders', shows('Reminders'), timeout=150)]
    return cases


def build_cases(eval_window):
    def teams_open_check(state, events):
        url, _ = safari_front_tab()
        if front_app() != 'Safari' or not any(host in url for host in TEAMS_HOSTS):
            return 'front is '+front_app()+' '+url[:80]
        return None

    def chat_check(state, events):
        _, title = safari_front_tab()
        if not title.startswith('Chat'):
            return 'Teams shows '+title.split('|')[0].strip()
        return check_task_done(state, events)

    def activity_check(state, events):
        _, title = safari_front_tab()
        if not title.startswith('Activity'):
            return 'Teams shows '+title.split('|')[0].strip()
        if task_status(state) != 'done' or failed_reply(final_message(events)):
            return 'task ended '+str(task_status(state))+': '+final_message(events)[:160]
        return None

    def second_email_check(state, events):
        subject = mail_newest(2)
        message = final_message(events)
        if not subject or subject not in message:
            return 'reply lacks subject “'+str(subject)[:60]+'”: '+message[:120]
        return None

    def calendar_check(state, events):
        expected = calendar_reply('tomorrow')
        message = final_message(events)
        if message.strip() != expected.strip():
            return 'reply “'+message[:100]+'” ≠ “'+expected[:100]+'”'
        return None

    def reminder_check(state, events):
        action = state.get('pending_action') or {}
        label = action.get('label') or ''
        # The next 6 o'clock: this evening, or tomorrow (morning or evening) after 18:00.
        times = ('today at 18:00',) if datetime.now().hour < 18 else ('tomorrow at 06:00', 'tomorrow at 18:00')
        if action.get('type') != 'app_write' or 'call mom' not in label.casefold() or not any(t in label for t in times):
            return 'proposal: '+(label or final_message(events))[:160]
        return None

    def youtube_check(state, events):
        # A site blocker on this Mac (Cold Turkey) replaces YouTube with its own page, so what
        # A11y controls is where it navigated: the results page for the query.
        url, _ = safari_front_tab()
        wanted = lambda value: 'youtube.com/results' in value and re.search(r'lo(?:-|\+|%20)?fi', value, re.I)
        sent = [e['data']['message'] for e in events if e['kind'] == 'say' and 'youtube.com/results' in e['data']['message']]
        if wanted(url) or (url.startswith('safari-extension://') and any(wanted(message) for message in sent)):
            return None
        return 'Safari shows '+url[:100]

    def new_window_check(state, events):
        return None  # judged against the window list in run_case

    def move_left_check(state, events):
        mascot, screen = state['mascot'], state['screen']
        if mascot['x'] > screen['x']+40:
            return 'mascot at x=%d' % mascot['x']
        return None

    def first_email_check(state, events):
        clicked = [r for r in (state.get('task') or {}).get('receipts', []) if r['type'] in ('click_point', 'press_control')]
        if not clicked:
            return 'no click: '+final_message(events)[:120]
        selected, newest = mail_selection(), mail_newest(1)
        if not selected or selected != newest:
            return 'selected “'+str(selected)[:50]+'”, newest “'+str(newest)[:50]+'”'
        if failed_reply(final_message(events)):
            return final_message(events)[:160]
        return None

    def screen_check(state, events):
        message = final_message(events)
        if (state.get('plan') or {}).get('operation') != 'inspect_screen':
            return 'route '+str((state.get('plan') or {}).get('operation'))
        if (failed_reply(message) or not re.search(r'\b(mail|inbox|e-?mails?|messages?)\b', message, re.I)
                or re.search(r'\bnot (?:currently )?(?:visible|open|shown)\b', message, re.I)):
            return 'reply: '+message[:160]
        return None

    def reminders_check(state, events):
        expected, message = reminders_reply('today'), final_message(events)
        if message.strip() != expected.strip():
            return 'reply “'+message[:100]+'” ≠ “'+expected[:100]+'”'
        return None

    def calculator_check(state, events):
        message = final_message(events)
        if not number_in_text(message, 42) or failed_reply(message) or not calculator_matches(42):
            return 'Calculator display or reply does not show 42: '+message[:160]
        return None

    def follow_up_check(state, events):
        expected, message = calendar_reply('tomorrow'), final_message(events)
        if message.strip() != expected.strip():
            return 'reply “'+message[:100]+'” ≠ “'+expected[:100]+'”'
        return None

    def remembered_check(state, events):
        message = final_message(events)
        if not re.search(r'\b14\b|fourteen', message, re.I):
            return 'reply: '+message[:160]
        return None

    def name_check(state, events):
        message = final_message(events)
        if 'Ana' not in message:
            return 'reply: '+message[:160]
        return None

    def teams_neutral():
        eval_window.ensure(NEUTRAL)
        activate('Finder')

    def at_bottom_right(app):
        app.run('move to the bottom right', 30)

    return [
        Case('Microsoft Teams.', teams_open_check, setup=lambda app: teams_neutral(), timeout=60),
        Case('click on Chat', chat_check, setup=lambda app: teams_view(eval_window, 1)),
        Case('check if I have new notifications on Teams', activity_check, setup=lambda app: teams_view(eval_window, 2), timeout=180),
        Case('read my second last email', second_email_check, timeout=90),
        Case("what's on my calendar tomorrow", calendar_check, timeout=60),
        Case('remind me to call mom at 6', reminder_check, timeout=60),
        Case('open youtube and search for lo-fi', youtube_check, setup=lambda app: teams_neutral(), timeout=180),
        Case('open a new Safari window', new_window_check, setup=lambda app: eval_window.ensure(NEUTRAL), timeout=60),
        Case('move to the left', move_left_check, setup=at_bottom_right, timeout=30),
        Case('click the first email in Mail', first_email_check, setup=lambda app: mail_front(), timeout=180),
        Case("what's on my screen", screen_check, setup=lambda app: mail_front(), timeout=120),
        Case('what do I have to do today', reminders_check, timeout=60),
        Case('open the calculator and add 12 and 30', calculator_check, setup=lambda app: activate('Finder'), timeout=120),
        Case('what about tomorrow?', follow_up_check, setup=lambda app: app.run("what's on my calendar today", 60), timeout=60),
        # A conversation, as reported: Teams just opened, then a question that does not name it.
        Case('are there any notifications that require my attention', activity_check,
             setup=lambda app: (teams_view(eval_window, 2), app.run('open teams in a new safari window', 60)), timeout=180),
        Case('what number did I just say', remembered_check, setup=lambda app: app.run('14', 60), timeout=60),
        Case('my name is Ana. what is my name?', name_check, timeout=60),
    ]


def build_navigation(eval_window):
    """Getting somewhere by observing and clicking: native apps and websites, read-only."""
    def pane(name):
        def check(state, events):
            if failed_reply(final_message(events)):
                return final_message(events)[:160]
            return None if settings_pane_is(name) else 'Settings does not show '+name+': '+final_message(events)[:120]
        return check

    def folder(name):
        def check(state, events):
            title = window_title('Finder')
            return None if title == name else 'Finder shows '+str(title)
        return check

    def url(pattern):
        def check(state, events):
            address, _ = safari_front_tab()
            return None if re.search(pattern, address, re.I) else 'Safari shows '+address[:100]
        return check

    def settings_open(app):
        quit_settings()
        subprocess.run(['open', '-a', 'System Settings'])
        time.sleep(3)

    def finder_home(app):
        subprocess.run(['open', str(Path.home())])
        time.sleep(1.5)

    def web(app):
        eval_window.ensure(NEUTRAL)
        activate('Finder')

    def teams_activity(app):
        teams_view(eval_window, 1)

    def other_result(state, events):
        address, _ = safari_front_tab()
        if not address.startswith('http') or 'duckduckgo.com' in address or 'example.com' in address:
            return 'Safari shows '+address[:100]
        return None

    def teams_calendar(state, events):
        _, title = safari_front_tab()
        return None if title.startswith('Calendar') else 'Teams shows '+title.split('|')[0].strip()

    return [
        Case('open System Settings and go to Battery', pane('Battery'), setup=lambda app: quit_settings(), timeout=180),
        Case('show me my Wi-Fi settings', pane('Wi-Fi'), setup=lambda app: quit_settings(), timeout=180),
        Case('in System Settings, open Displays', pane('Displays'), setup=settings_open, timeout=180),
        Case('find the Bluetooth settings', pane('Bluetooth'), setup=lambda app: quit_settings(), timeout=180),
        Case('open Finder and go to the Downloads folder', folder('Downloads'), setup=finder_home, timeout=180),
        Case('show me my Documents folder', folder('Documents'), setup=finder_home, timeout=180),
        Case('go to Wikipedia and open the article about Romania', url(r'wikipedia\.org/wiki/Romania'), setup=web, timeout=240),
        Case('open github.com and go to the Explore page', url(r'github\.com/explore'), setup=web, timeout=240),
        Case('open apple.com and go to the Mac page', url(r'apple\.com/(?:\w+/)?mac/?(?:$|[?#])'), setup=web, timeout=240),
        Case('open Hacker News and open the comments of the first story', url(r'news\.ycombinator\.com/item\?id='), setup=web, timeout=240),
        Case('search DuckDuckGo for weather in Bucharest and open the first result', other_result, setup=web, timeout=240),
        Case('on Wikipedia, search for Bucharest and open the article', url(r'wikipedia\.org/wiki/Bucharest'), setup=web, timeout=240),
        Case('open the Calendar in Teams', teams_calendar, setup=teams_activity, timeout=240),
    ]


def build_varied(eval_window, count, seed):
    """Fresh tasks every run: random apps, places, numbers and wording. Read-only, except typing
    into search fields and a new TextEdit document (skipped when TextEdit is already open)."""
    import random
    rng = random.Random(seed)

    def pick(*options):
        return rng.choice(options)

    def calculator():
        a, b = rng.randint(2, 999), rng.randint(2, 99)
        op, words, symbol = pick(('+', 'plus', 'add'), ('-', 'minus', 'subtract'), ('*', 'times', 'multiply'), ('/', 'divided by', 'divide'))
        request = pick('open calculator and compute {a} {w} {b}', 'use the calculator to work out {a} {w} {b}',
                       'in Calculator, calculate {a} {w} {b}', 'open the calculator app and do {a} {w} {b} for me').format(a=a, w=words, b=b)
        result = arithmetic_result(a, op, b)
        def check(state, events):
            message = final_message(events)
            return None if number_in_text(message, result) and calculator_matches(result) else 'expected Calculator display and reply '+str(result)+': '+message[:120]
        return Case(request, check, setup=lambda app: activate('Finder'), timeout=180)

    def settings():
        panes = [p for p in PANES if p not in ('General',)] or ['Battery']
        name = rng.choice(panes)
        request = pick('open the {p} settings', 'show me {p} in System Settings', 'take me to the {p} settings',
                       'go to System Settings and open {p}', 'I want to see my {p} settings').format(p=name)
        def check(state, events):
            return None if settings_pane_is(name) else 'Settings does not show '+name+': '+final_message(events)[:100]
        return Case(request, check, setup=lambda app: quit_settings(), timeout=180)

    def finder():
        name = pick('Downloads', 'Documents', 'Desktop', 'Applications', 'Pictures', 'Movies', 'Music')
        request = pick('open my {f} folder', 'show me {f} in Finder', 'go to the {f} folder', 'open Finder and go to {f}').format(f=name)
        def check(state, events):
            title = window_title('Finder')
            return None if title == name else 'Finder shows '+str(title)
        return Case(request, check, setup=lambda app: subprocess.run(['open', str(Path.home())]), timeout=180)

    def wikipedia():
        topic = pick('Paris', 'Albert Einstein', 'Giraffe', 'Mount Everest', 'Jazz', 'Python (programming language)', 'Danube',
                     'Leonardo da Vinci', 'Photosynthesis', 'Tokyo', 'Black hole', 'Chess', 'Vienna', 'Coffee', 'Moon')
        short = topic.split(' (')[0]
        request = pick('go to Wikipedia and open the article about {t}', 'look up {t} on Wikipedia', 'find the Wikipedia page for {t}',
                       'on wikipedia, search for {t} and open the article').format(t=short)
        pattern = r'wikipedia\.org/wiki/'+re.escape(topic.replace(' ', '_')).replace('\\_', '_')
        def check(state, events):
            address, _ = safari_front_tab()
            return None if re.search(pattern.replace('\\(', '%28').replace('\\)', '%29'), address, re.I) or \
                re.search(r'wikipedia\.org/wiki/'+re.escape(short.replace(' ', '_')), address, re.I) else 'Safari shows '+address[:100]
        return Case(request, check, setup=lambda app: (eval_window.ensure(NEUTRAL), activate('Finder')), timeout=240)

    def app_search():
        app, thing, request = pick(
            ('Maps', pick('Brasov', 'Lisbon', 'Kyoto', 'Reykjavik', 'Cluj-Napoca'), 'open Maps and find {x}'),
            ('Weather', pick('London', 'Madrid', 'Oslo', 'Sydney'), 'open the Weather app and show me the weather in {x}'),
            ('App Store', pick('Spotify', 'Notion', 'Slack', 'Duolingo'), 'search the App Store for {x}'),
            ('Dictionary', pick('serendipity', 'ephemeral', 'lucid', 'quixotic'), 'look up the word {x} in the Dictionary app'),
            ('Music', pick('Daft Punk', 'Adele', 'Coldplay', 'Inna'), 'open Music and search for {x}'))
        request = request.format(x=thing)
        def check(state, events):
            if front_app() != app:
                return 'front app is '+front_app()+', not '+app
            return None if shows(thing) else app+' does not show '+thing+': '+final_message(events)[:100]
        return Case(request, check, setup=lambda app: activate('Finder'), timeout=240)

    def calendar_month():
        month = pick('January', 'March', 'May', 'July', 'September', 'December')
        year = rng.choice((2026, 2027))
        request = pick('open Calendar and go to {m} {y}', 'show me {m} {y} in Calendar').format(m=month, y=year)
        def check(state, events):
            if front_app() != 'Calendar':
                return 'front app is '+front_app()
            return None if shows(month) and shows(str(year)) else 'Calendar does not show '+month+' '+str(year)
        return Case(request, check, setup=lambda app: activate('Finder'), timeout=240)

    def menu():
        app, menu_name, item = pick(('Safari', 'History', 'Show All History'), ('Safari', 'Bookmarks', 'Show Bookmarks'),
                                     ('Finder', 'Go', 'Recents'), ('Finder', 'View', 'as Icons'), ('Safari', 'View', 'Zoom In'))
        request = pick('in {a}, open the {m} menu', 'open the {m} menu in {a}').format(a=app, m=menu_name)
        def setup(runner):
            if app == 'Safari':
                eval_window.ensure(NEUTRAL)
            else:
                subprocess.run(['open', str(Path.home())])
                time.sleep(1)
        def check(state, events):
            found = shows(item, window_only=False)
            subprocess.run(['osascript', '-e', 'tell application "System Events" to key code 53'])  # close the menu
            return None if found else 'menu item '+item+' not visible'
        return Case(request, check, setup=setup, timeout=180)

    def this_window():
        query = pick('romeo and juliet', 'banana bread recipe', 'how tall is the eiffel tower', 'best hiking boots')
        request = pick('in this window, search for {q}', 'search for {q} here in this tab', 'use this Safari tab to search for {q}').format(q=query)
        before = {}
        def setup(runner):
            eval_window.ensure(NEUTRAL)
            before.update(safari_windows())
        def check(state, events):
            after = safari_windows()
            address, _ = safari_front_tab()
            if after != before:
                return 'opened a new tab or window ('+str(sum(after.values()))+' tabs, before '+str(sum(before.values()))+')'
            words = [w for w in re.findall(r'\w+', query) if len(w) > 2]
            return None if all(w in address.casefold() for w in words) else 'Safari shows '+address[:100]
        return Case(request, check, setup=setup, timeout=180)

    def close_tab():
        before = {}
        def setup(runner):
            eval_window.ensure(NEUTRAL)
            osa('tell application "Safari"', 'tell window id %d' % eval_window.id,
                'set current tab to (make new tab at end of tabs with properties {URL:"https://example.com/?tab"})', 'end tell', 'activate', 'end tell')
            time.sleep(1.5)
            before.update(safari_windows())
        def check(state, events):
            after = safari_windows()
            if eval_window.id not in after:
                return 'closed the whole window'
            return None if after[eval_window.id] == before.get(eval_window.id, 0)-1 else 'tabs: %s → %s' % (before.get(eval_window.id), after.get(eval_window.id))
        return Case(pick('close this tab', 'close this Safari tab', 'close the current tab'), check, setup=setup, timeout=60)

    def textedit():
        sentence = pick('The quick brown fox jumps over the lazy dog', 'Meeting moved to Thursday at noon', 'Buy milk eggs and bread')
        request = pick("open TextEdit, make a new document and type '{s}'", "write '{s}' in a new TextEdit document").format(s=sentence)
        def check(state, events):
            if front_app() != 'TextEdit':
                return 'front app is '+front_app()+': '+final_message(events)[:100]
            return None if shows(sentence) else 'TextEdit does not show the sentence: '+final_message(events)[:100]
        return Case(request, check, setup=lambda app: activate('Finder'), timeout=240)

    generators = [calculator, calculator, settings, settings, finder, wikipedia, app_search, app_search, calendar_month, menu, this_window, close_tab]
    if not running('TextEdit'):
        generators.append(textedit)
    rng.shuffle(generators)
    return [generators[i % len(generators)]() for i in range(count)]


PANES = []

# --- running and reporting -------------------------------------------------------------------

def metrics(events):
    timings = [event['data'] for event in events if event['kind'] == 'timing']
    calls = [record for record in timings if record['event'] == 'ollama.finished']
    return {'model_calls': len(calls), 'model_ms': sum(record.get('elapsed_ms', 0) for record in calls),
            'prompt_tokens': sum(record.get('prompt_eval_count', 0) for record in calls),
            'prompt_ms': sum(record.get('prompt_eval_ms', 0) for record in calls),
            'load_ms': sum(record.get('load_ms', 0) for record in calls),
            'image_calls': sum(1 for record in timings if record['event'] == 'ollama.start' and record.get('image')),
            'invalid_steps': sum(1 for record in timings if record['event'] == 'task.step.invalid'),
            'reconsiders': sum(1 for record in timings if record['event'] == 'task.reconsider.start'),
            'steps': [{key: record.get(key) for key in ('step', 'status', 'total_ms', 'settle_ms', 'capture_ms', 'model_ms', 'model_calls', 'context_chars')}
                      for record in timings if record['event'] == 'task.step.summary']}


def route_of(state, events):
    plan = state.get('plan') or {}
    route = str(plan.get('operation'))+('·code' if plan.get('computed') else '')
    task = state.get('task')
    if task and any(e['kind'] == 'timing' and e['data']['event'].startswith('task.') for e in events):
        route += ' → '+' / '.join((r['type']+(':'+r['label'] if r.get('label') else ''))[:40] for r in task['receipts'])
    return route


def run_case(app, case, attempt):
    """One request: setup, the request, its check; then everything the case or its setup
    opened is closed again, even when the case fails or the harness errors."""
    opened = snapshot()  # before setup, so the pages a setup opens are closed too
    case.restore_after = True
    try:
        return run_case_steps(app, case, attempt, opened)
    finally:
        try:
            if case.restore_after:
                restore(opened)
        except (RuntimeError, OSError, subprocess.SubprocessError):
            pass


def run_case_steps(app, case, attempt, opened):
    if case.setup:
        case.setup(app)
    before = safari_windows()
    front_before = front_app()
    state, events, timed_out = app.run(case.request, case.timeout)
    asked = next((event['t'] for event in events if event['kind'] == 'ask'), None)
    # Until the companion went quiet: the last reply, action or animation, whichever ended last.
    seconds = round(state['now']-state['quiet_for']-asked, 2) if asked is not None and 'now' in state else None
    failure = 'timeout' if timed_out else None
    if failure is None:
        if case.check.__name__ == 'new_window_check':
            added = [w for w in safari_windows() if w not in before]
            failure = None if len(added) == 1 else 'new Safari windows: %d' % len(added)
            if not failure:
                url = osa('tell application "Safari" to return URL of current tab of window id %d' % added[0])
                if url.startswith('http'):
                    failure = 'the new window opened '+url[:80]  # a new window, not a website
        else:
            try:
                failure = case.check(state, events)
            except Exception as error:
                failure = 'check failed: '+str(error)[:160]
    record = {'request': case.request, 'attempt': attempt, 'success': failure is None, 'seconds': seconds,
              'route': route_of(state, events), 'failure': failure, 'reply': final_message(events)[:300],
              'task_status': task_status(state), **metrics(events)}
    record['front_app'] = front_app()
    if state.get('task') and state['task']['status'] == 'running':
        app.call({'cmd': 'stop'})
    try:
        record['closed'] = restore(opened)
        case.restore_after = False  # done; run_case need not do it again
    except (RuntimeError, subprocess.SubprocessError) as error:
        record['closed'] = ['error: '+str(error)[:100]]
    # The terminal driving the run (or A11y itself) coming forward is not something she opened.
    if case.quiet and record['success'] and (record['closed'] or record['front_app'] not in (front_before, 'Python', 'Terminal')):
        record.update(success=False, failure='opened '+(', '.join(record['closed']) or record['front_app'])+' for a question')
    return record


def percentile(values, fraction):
    values = sorted(values)
    return values[min(len(values)-1, round(fraction*(len(values)-1)))] if values else None


def summarize(records):
    summary = {}
    for request in dict.fromkeys(record['request'] for record in records):
        runs = [record for record in records if record['request'] == request]
        times = [record['seconds'] for record in runs if record['seconds'] is not None]
        summary[request] = {'runs': len(runs), 'success': sum(record['success'] for record in runs),
                            'rate': round(sum(record['success'] for record in runs)/len(runs), 2),
                            'median_s': round(statistics.median(times), 1) if times else None,
                            'p95_s': round(percentile(times, .95), 1) if times else None,
                            'routes': sorted(set(record['route'] for record in runs)),
                            'failures': [record['failure'] for record in runs if record['failure']]}
    return summary


def table(summary, earlier=None):
    lines = ['%-46s %7s %8s %8s' % ('request', 'success', 'median', 'p95')+('   before' if earlier else '')]
    for request, row in summary.items():
        line = '%-46s %3d/%-3d %7ss %7ss' % (request[:46], row['success'], row['runs'], row['median_s'], row['p95_s'])
        old = (earlier or {}).get(request)
        if old:
            line += '   %d/%d %ss %ss' % (old['success'], old['runs'], old['median_s'], old['p95_s'])
        lines.append(line)
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--runs', type=int, default=3)
    parser.add_argument('--only', action='append', help='run only requests containing this text (repeatable)')
    parser.add_argument('--out', default=str(ROOT/'.data'/'live_eval'))
    parser.add_argument('--compare', help='an earlier summary.json to show next to the results')
    parser.add_argument('--suite', choices=('requests', 'navigation', 'varied', 'v2', 'v3', 'heldout', 'answers', 'conversation', 'broad', 'harder', 'features', 'capability', 'grounding', 'all'), required=True,
                        help='requests: the reported requests; navigation: getting somewhere by clicking; '
                             'varied: new random tasks across many apps every run')
    parser.add_argument('--tasks', type=int, default=14, help='varied suite: how many random tasks')
    parser.add_argument('--seed', type=int, help='varied suite: repeat an earlier run (printed at the start)')
    parser.add_argument('--max-turns',type=int,help='diagnostic prefix only: first N turns of each selected conversation')
    parser.add_argument('--allow-private-apps', action='store_true',
                        help='also run collected cases whose request, setup or cleanup names Mail, Messages, Photos, Contacts, '
                             'Teams, WhatsApp or FaceTime; only on an explicit owner instruction. Disabled suites stay disabled.')
    args = parser.parse_args()
    if args.suite not in SAFE_LEGACY_SUITES:
        parser.error('That legacy suite contains private-application tasks and is disabled. Use scripts/human_eval.py with owned fixtures, or explicitly select a safe diagnostic suite.')
    if args.max_turns is not None and args.max_turns<1:
        parser.error('--max-turns must be positive')
    print('Legacy diagnostic suite; results are not a human-workflow capability baseline.', flush=True)
    # A sleeping display locks the screen: its password field pauses screen vision and every task
    # failed as "protected" (live, 2026-10-26). Keep the Mac awake for as long as this run lasts.
    subprocess.Popen(['caffeinate', '-dimsu', '-w', str(os.getpid())])
    ONLY[:] = args.only or []
    out = Path(args.out)/datetime.now().strftime('%Y%m%d-%H%M%S')
    out.mkdir(parents=True, exist_ok=True)
    from companion.instance import build_id
    print('build', build_id(), '→', out, flush=True)
    app = App(Path(os.getenv('A11Y_DATA_DIR', str(ROOT/'.data'))), out/'app.log')
    eval_window = EvalWindow()
    if args.suite in ('conversation', 'broad', 'harder', 'features', 'capability', 'grounding'):
        import importlib
        from live_conversation import run
        cases = importlib.import_module('live_'+args.suite).build(eval_window,seed=args.seed if args.seed is not None else 0)
        cases = refuse_private_cases(cases, args.allow_private_apps)
        if ONLY:
            cases = [case for case in cases if any(text.casefold() in turn.request.casefold()
                     for turn in case['turns'] for text in ONLY)]
        full_turns=sum(len(case['turns']) for case in cases)
        if args.max_turns:
            cases=[dict(case,turns=case['turns'][:args.max_turns]) for case in cases]
        from companion.gui_models import selection
        gui_profile, gui_model = selection()
        from companion.ollama import OllamaProvider
        eval_model_config = OllamaProvider().config
        (out/'scope.json').write_text(json.dumps(dict(focused_grounding=os.getenv('A11Y_FOCUSED_GROUNDING','1'),runs=args.runs,case_turns={case['name']:len(case['turns']) for case in cases},gui_profile=gui_profile,gui_model=gui_model,
            vision_model=eval_model_config.vision_model or eval_model_config.model,text_model=eval_model_config.model,suite=args.suite,seed=args.seed if args.seed is not None else 0,full_turns=full_turns,
            scheduled_turns=sum(len(case['turns']) for case in cases),max_turns=args.max_turns,only=ONLY),indent=2))
        return run(app, eval_window, out, cases, args.runs)
    if args.suite in ('v2', 'v3', 'heldout'):
        return run_v2(app, eval_window, out, args.tasks, args.seed if args.seed is not None else int(time.time()) % 100000,
                      hard={'v2': False, 'v3': 'live_hard', 'heldout': 'live_heldout'}[args.suite],
                      allow_private=args.allow_private_apps)
    seed = args.seed if args.seed is not None else int(time.time()) % 100000
    if args.suite in ('varied', 'all'):
        PANES[:] = settings_panes()
        print('varied seed', seed, '·', len(PANES), 'settings panes', flush=True)
    suites = {'requests': build_cases, 'navigation': build_navigation, 'answers': build_answers,
              'varied': lambda window: build_varied(window, args.tasks, seed)}
    chosen = [suites[name](eval_window) for name in (suites if args.suite == 'all' else [args.suite])]
    cases = [case for group in chosen for case in group if not args.only or any(text.casefold() in case.request.casefold() for text in args.only)]
    cases = refuse_private_cases(cases, args.allow_private_apps)
    records = []
    app.launch()
    try:
        for case in cases:
            for attempt in range(1, args.runs+1):
                try:
                    record = run_case(app, case, attempt)
                except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as error:
                    record = {'request': case.request, 'attempt': attempt, 'success': False, 'seconds': None,
                              'route': '', 'failure': 'harness: '+str(error)[:200], 'reply': ''}
                    if app.process and app.process.poll() is not None:
                        record['failure'] = 'A11y exited (code %s): see app.log' % app.process.poll()
                        app.launch()  # keep going with a fresh app
                records.append(record)
                with open(out/'runs.jsonl', 'a') as file:
                    file.write(json.dumps(record, ensure_ascii=False)+'\n')
                print('%s %-44s %6ss  %s%s' % ('✓' if record['success'] else '✗', case.request[:44], record['seconds'],
                      record['route'][:70], '' if record['success'] else '  ← '+str(record['failure'])[:120]), flush=True)
    finally:
        app.quit()
        eval_window.close()
    summary = summarize(records)
    summary_all = {'build': build_id(), 'when': datetime.now().isoformat(timespec='seconds'), 'runs': args.runs, 'requests': summary}
    (out/'summary.json').write_text(json.dumps(summary_all, indent=1, ensure_ascii=False))
    earlier = json.loads(Path(args.compare).read_text())['requests'] if args.compare else None
    print('\n'+table(summary, earlier))
    total = sum(row['success'] for row in summary.values()), sum(row['runs'] for row in summary.values())
    print('\noverall %d/%d' % total)


def run_v2(app, eval_window, out, count, seed, hard=False, allow_private=False):
    """Varied tasks, each once (scripts/live_tasks.py; with hard, scripts/live_hard.py: harder,
    multi-step tasks plus the older ones that still fail); writes runs.jsonl and report.md."""
    import live_tasks
    PANES[:] = settings_panes()
    if hard:
        # live_hard (v3) or live_heldout: the held-out set is written by someone else and only its
        # total score is looked at, so fixes can't be tuned to its tasks.
        import importlib
        tasks = importlib.import_module(hard).build(eval_window, count, seed)
    else:
        tasks = live_tasks.build(eval_window, count, seed)
    tasks = refuse_private_cases(tasks, allow_private)
    if ONLY:
        tasks = [task for task in tasks if any(text.casefold() in task.request.casefold() for text in ONLY)]
    print(hard or 'v2', 'seed', seed, '·', len(tasks), 'tasks', flush=True)
    app.shots = out/'shots'
    records = []
    app.launch()
    try:
        for task in tasks:
            try:
                record = live_tasks.run_task(app, task, app.shots)
            except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as error:
                record = {'family': task.family, 'request': task.request, 'success': False, 'score': 0, 'seconds': None, 'checks': [],
                          'route': '', 'reply': 'harness: '+str(error)[:200], 'steps': 0, 'detours': 0, 'shots': [], 'model_calls': 0, 'cleanup': []}
                if app.process and app.process.poll() is not None:
                    app.launch()
            records.append(record)
            with open(out/'runs.jsonl', 'a') as file:
                file.write(json.dumps(record, ensure_ascii=False)+'\n')
            print('%s %.2f %-58s %6ss %2s steps %s detours  %s' % ('✓' if record['success'] else '✗', record['score'], task.request[:58],
                  record['seconds'], record['steps'], record['detours'], '; '.join(n for n, ok in record['checks'] if not ok)[:90]), flush=True)
            live_tasks.report(records, out/'report.md', seed)
    finally:
        app.quit()
        eval_window.close()
    done = sum(r['success'] for r in records)
    print('\noverall %d/%d · mean score %.2f · detours %d · report %s' % (done, len(records), sum(r['score'] for r in records)/max(1, len(records)),
          sum(r['detours'] for r in records), out/'report.md'))


if __name__ == '__main__':
    main()
