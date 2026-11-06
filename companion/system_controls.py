"""System settings read and changed in code: volume, mute, appearance, Wi-Fi, Bluetooth, and
brightness through the desktop helper, and the settings A11y cannot switch itself (Focus, text
size, Bluetooth on/off), which open their exact Settings pane instead.

The router chooses the operation ``system_control`` with target ``"setting | change"``; this
module maps the setting to one of SETTINGS and the change to a fixed step or value, then runs
fixed commands with every value passed as data (AppleScript ``argv`` or an argument list, never
spliced into a script or shell). Every change reads the state before and after, so the reply
says what the computer reports ("Volume is now 40%."), not what was intended. These settings
are reversible by the user and nothing here sends, deletes or purchases, so they run without a
confirmation, like navigation (owner decision 2026-11-06, Wi-Fi included). macOS only for now;
on Windows every setting is refused honestly.
"""
import re
import subprocess
import sys

SETTINGS = ('volume', 'mute', 'brightness', 'appearance', 'wifi', 'bluetooth', 'focus', 'text_size')
# Relative amounts as fixed steps, in percent: "a bit", plain "up/down", "a lot".
SMALL_STEP, STEP, LARGE_STEP = 10, 20, 40

VOLUME_GET = 'set s to get volume settings\nreturn ((output volume of s) as text) & "," & ((output muted of s) as text)'
VOLUME_SET = 'on run argv\nset volume output volume ((item 1 of argv) as integer)\nend run'
MUTE_SET = 'on run argv\nset volume output muted ((item 1 of argv) is "true")\nend run'
APPEARANCE_SET = ('on run argv\ntell application "System Events" to tell appearance preferences to '
                  'set dark mode to ((item 1 of argv) is "true")\nend run')

# The System Settings pane each setting lives in (macOS extension identifier).
MAC_PANES = {'volume': 'com.apple.Sound-Settings.extension', 'mute': 'com.apple.Sound-Settings.extension',
             'brightness': 'com.apple.Displays-Settings.extension', 'appearance': 'com.apple.Appearance-Settings.extension',
             'wifi': 'com.apple.wifi-settings-extension', 'bluetooth': 'com.apple.BluetoothSettings',
             'focus': 'com.apple.Focus-Settings.extension', 'text_size': 'com.apple.Accessibility-Settings.extension'}
NAMES = {'volume': 'the volume', 'mute': 'mute', 'brightness': 'the screen brightness', 'appearance': 'dark mode',
         'wifi': 'Wi-Fi', 'bluetooth': 'Bluetooth', 'focus': 'Focus and Do Not Disturb', 'text_size': 'the text size'}

# Setting names as the router may write them, most specific first ("mute the volume" is mute).
_SETTING_WORDS = (
    ('mute', r'\b(?:un)?mut(?:e|ed|ing)\b|\bsilen(?:ce|t)\b'),
    ('text_size', r'\b(?:text|font|type)(?:[\s_-]*size)?\b|\btext_size\b'),
    ('brightness', r'\bbright(?:ness|er)?\b|\bdim(?:mer)?\b'),
    ('appearance', r'\bappearance\b|\bdark\b|\blight mode\b|\btheme\b|\bnight mode\b'),
    ('wifi', r'\bwi[\s-]?fi\b|\bwireless\b'),
    ('bluetooth', r'\bbluetooth\b'),
    ('focus', r'\bfocus\b|\bdo[\s-]?not[\s-]?disturb\b|\bdnd\b'),
    ('volume', r'\bvolume\b|\bsound\b|\bloud(?:er)?\b|\bquiet(?:er)?\b|\baudio\b|\bspeakers?\b'),
)
_UP = r'\b(?:up|raise|increase|higher|more|louder|bigger|larger|brighter|turn up|boost)\b'
_DOWN = r'\b(?:down|lower|decrease|less|quieter|softer|smaller|dimmer|reduce)\b'
_SMALL = r'\b(?:a bit|a little|little|slightly|a touch|a tad|a notch|somewhat|bit)\b'
_LARGE = r'\b(?:a lot|lots|way|much|loads|significantly|really)\b'
# Turning something off, whatever it is named: "disable dark mode", "stop muting", "no more mute".
_NEGATE = (r'\b(?:off|disable[ds]?|deactivate[ds]?|stop(?:ped)?|cancel|exit|leave|get out of|no more|false|'
           r'turn(?:ed)? (?:\w+ )?off|switch(?:ed)? (?:\w+ )?off)\b')
_AFFIRM = r'\b(?:on|enable[ds]?|activate[ds]?|true|start)\b'
_QUESTION = re.compile(r'\?\s*$|^\W*(?:is|are|was|what|what\'s|whats|how|which|do(?!\s+not)|does|did|tell me)\b', re.I)
# "Could you turn off Wi-Fi?" is a polite command; "can you tell me if Wi-Fi is on?" is a question.
_POLITE = re.compile(r'^\W*(?:(?:hey|ok(?:ay)?|ally|a11y|please)\W+)*(?:(?:could|can|would|will)\s+you|please)\b'
                     r'(?!\s+(?:tell|check|see|say|let me know|find out)\b)(?!.*\b(?:if|whether)\b)', re.I)
# What a request must say for a setting to be meant, so "turn down the offer" or "dark chocolate"
# never changes one even if the router picked a setting.
_NAMED_IN_REQUEST = {
    'mute': r'\b(?:un)?mut(?:e|ed|ing)\b|\bsilen(?:ce|t)\b',
    'text_size': r'\b(?:text|font|type|letters?)\b',
    'brightness': r'\bbright(?:ness|er)?\b|\bdim(?:mer)?\b|\bscreen\b|\bdisplay\b',
    'appearance': r'\bappearance\b|\b(?:dark|light|night)(?:\s+|-)(?:mode|theme|appearance)\b|\btheme\b',
    'wifi': r'\bwi[\s-]?fi\b|\bwireless\b',
    'bluetooth': r'\bbluetooth\b',
    'focus': r'\bfocus\b|\bdo[\s-]?not[\s-]?disturb\b|\bdnd\b',
    'volume': r'\bvolume\b|\bsound\b|\bloud(?:er|ly)?\b|\bquiet(?:er)?\b|\baudio\b|\bspeakers?\b|\bsofter\b',
}


class ControlError(RuntimeError):
    pass


def is_question(request):
    request = str(request or '')
    return bool(_QUESTION.search(request)) and not _POLITE.search(request)


def named_setting(target):
    """The supported setting the router's target names, or None."""
    name = str(target or '').partition('|')[0].casefold()
    exact = name.strip().replace(' ', '_').replace('-', '')
    if exact in SETTINGS:
        return exact
    return next((key for key, pattern in _SETTING_WORDS if re.search(pattern, name)), None)


def parse(target, request=''):
    """(setting, change) from the router's target. change is a dict: {'read': True},
    {'set': n} (absolute 0..100), {'by': ±n}, {'on': bool} or {'toggle': True}. The setting must be
    named by the target and by the request; direction and amount words the target dropped are taken
    from the request ("down" for "down a bit")."""
    target, request = str(target or ''), str(request or '')
    setting = named_setting(target)
    if setting is None or (request.strip() and not re.search(_NAMED_IN_REQUEST[setting], request, re.I)):
        return None, {'read': True}
    wording = (target.partition('|')[2] if '|' in target else target).strip().casefold()
    command = request.strip() and not is_question(request)
    change = _change(setting, wording or (request.casefold() if command else ''), request)
    if change.get('read') and command:
        # "Turn on dark mode" came back as target "appearance |" or "appearance | dark mode": a
        # command is not a question, so its direction is the user's own word for it.
        change = _change(setting, request.casefold(), request)
    return setting, change


def _change(setting, wording, request):
    said = request.casefold()
    negated = bool(re.search(_NEGATE, wording)) and not re.search(r'\bturn(?:ed)? (?:\w+ )?on\b', wording)
    if setting == 'mute':
        if re.search(r'\bunmute', wording) or negated or re.search(r'\bsound back\b', wording):
            return {'on': False}
        if re.search(r'\btoggle\b', wording):
            return {'toggle': True}
        if re.search(_AFFIRM+r'|\bmute', wording) or not is_question(request):
            return {'on': True}
        return {'read': True}
    if setting == 'appearance':
        dark, light = re.search(r'\b(?:dark|night)\b', wording), re.search(r'\blight\b', wording)
        if dark or light:
            return {'on': bool(dark) != negated}  # "turn off light mode" is dark mode
    number = re.search(r'(\d{1,3})(?:\s*%|\s*percent)?', wording)
    up, down = re.search(_UP, wording), re.search(_DOWN, wording)
    if number and re.search(r'\bto\s+\d', wording):
        return {'set': max(0, min(100, int(number.group(1))))}  # "up to 80", "set to 30": a level
    if number and (up or down or re.search(r'\bby\s+\d', wording)):
        return {'by': (-1 if down and not up else 1)*min(100, int(number.group(1)))}
    if number:
        return {'set': max(0, min(100, int(number.group(1))))}
    if negated:
        return {'on': False}
    if re.search(_AFFIRM, wording):
        return {'on': True}
    if re.search(r'\btoggle\b|\bswitch\b|\bflip\b', wording):
        return {'toggle': True}
    if re.search(r'\b(?:max(?:imum)?|full|all the way up|highest)\b', wording):
        return {'set': 100}
    if re.search(r'\b(?:min(?:imum)?|all the way down|lowest|zero)\b', wording):
        return {'set': 0}
    if re.search(r'\bhalf(?:way)?\b', wording):
        return {'set': 50}
    if up or down:
        size = (SMALL_STEP if re.search(_SMALL, wording) else LARGE_STEP if re.search(_LARGE, wording)
                else SMALL_STEP if re.search(_SMALL, said) else LARGE_STEP if re.search(_LARGE, said) else STEP)
        return {'by': size if up and not down else -size}
    return {'read': True}


def leftover(rest, setting):
    """What to do with work the router left after a setting: 'same' (it only restates the setting or
    its amount), ('setting', target) for another setting to run in code, or 'task'."""
    rest = str(rest or '').strip()
    if not rest:
        return 'same'
    other = named_setting(rest)
    if other is not None and re.search(_NAMED_IN_REQUEST[other], rest, re.I):
        return 'same' if other == setting else ('setting', other+' | '+rest)
    leftover_words = re.sub('|'.join((_UP, _DOWN, _SMALL, _LARGE, _AFFIRM, _NEGATE, r'\d+\s*%?', r'\b(?:please|it|the|a|and|then|to|by|percent)\b')), ' ', rest.casefold())
    return 'same' if not re.search(r'\w', leftover_words) else 'task'


def _out(runner, command, timeout=10):
    try:
        result = runner(list(command), capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        raise ControlError('unreachable') from None
    if result.returncode:
        if '-1743' in (result.stderr or ''):
            raise PermissionError('automation')
        raise ControlError('failed')
    return (result.stdout or '').strip()


def _script(runner, source, *args):
    return _out(runner, ['osascript', '-e', source, *[str(arg) for arg in args]])


# --- macOS readers and writers ------------------------------------------------------------------

def _volume(runner):
    level, _, muted = _script(runner, VOLUME_GET).partition(',')
    if not level.strip().isdigit():
        raise ControlError('no volume')  # "missing value": an output device without a volume control
    return int(level), muted.strip() == 'true'


def _dark(runner):
    """Dark mode from the global AppleInterfaceStyle key; the key is absent in light mode. Any other
    failure is unknown, not light."""
    try:
        result = runner(['defaults', 'read', '-g', 'AppleInterfaceStyle'], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        raise ControlError('unreachable') from None
    if result.returncode == 0:
        return (result.stdout or '').strip() == 'Dark'
    if 'does not exist' in (result.stderr or ''):
        return False
    raise ControlError('failed')


def _wifi_device(runner):
    text = _out(runner, ['networksetup', '-listallhardwareports'])
    found = re.search(r'Hardware Port:\s*(?:Wi-Fi|AirPort)\s*\nDevice:\s*(\S+)', text)
    if not found:
        raise ControlError('no wifi')
    return found.group(1)


def _wifi(runner, device):
    state = re.search(r':\s*(On|Off)\s*$', _out(runner, ['networksetup', '-getairportpower', device]), re.I)
    if not state:
        raise ControlError('no wifi state')
    return state.group(1).casefold() == 'on'


def _bluetooth(runner):
    state = re.search(r'\bState:\s*(On|Off)\b', _out(runner, ['system_profiler', 'SPBluetoothDataType'], timeout=20), re.I)
    return None if not state else state.group(1).casefold() == 'on'


def _open_mac_pane(setting, runner):
    """Open the setting's System Settings pane, only from the system's own catalog."""
    from .settings_panes import pane
    entry = pane(MAC_PANES[setting])
    if entry is None:
        return None
    try:
        result = runner(['open', 'x-apple.systempreferences:'+entry['id']], capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return None if result.returncode else entry['name']


def _on_off(value):
    return 'on' if value else 'off'


def _mac(setting, change, runner, native=None):
    if setting == 'volume' and 'on' in change:
        setting, change = 'mute', {'on': not change['on']}  # "turn the sound off" mutes it; "on" unmutes
    if setting in ('volume', 'mute'):
        level, muted = _volume(runner)
        before = {'level': level, 'muted': muted}
        if change.get('read'):
            return before, before, 'Volume is at %d%%%s.' % (level, ', and sound is muted' if muted else '')
        if setting == 'mute':
            wanted = (not muted) if change.get('toggle') else change.get('on', True)
            if wanted == muted:
                return before, before, ('Sound is already muted.' if muted else 'Sound is already on, at %d%%.' % level)
            _script(runner, MUTE_SET, 'true' if wanted else 'false')
            level, muted = _volume(runner)
            after = {'level': level, 'muted': muted}
            if muted != wanted:
                return before, after, 'I tried, but sound is still %s.' % ('on' if not muted else 'muted')
            return before, after, 'Sound is muted.' if muted else 'Sound is on again, at %d%%.' % level
        if 'toggle' in change:
            return before, before, 'Volume is at %d%%. Tell me a level, or say louder or quieter.' % level
        goal = change['set'] if 'set' in change else max(0, min(100, level+change['by']))
        if goal == level and not (muted and goal > 0):
            limit = ' already' if 'by' in change else ''
            return before, before, 'Volume is%s at %d%%.' % (limit, level)
        if goal != level:
            _script(runner, VOLUME_SET, goal)
        if muted and goal > 0:
            _script(runner, MUTE_SET, 'false')  # turning it up means wanting to hear it, as the volume keys do
        level, muted = _volume(runner)
        after = {'level': level, 'muted': muted}
        if abs(level-goal) > 2:
            return before, after, 'I set the volume to %d%%, but it now reads %d%%.' % (goal, level)
        return before, after, 'Volume is now %d%%.' % level+(' Sound is still muted.' if muted and goal > 0 else '')
    if setting == 'appearance':
        dark = _dark(runner)
        before = {'dark': dark}
        if change.get('read'):
            return before, before, 'Dark mode is on.' if dark else 'Dark mode is off; the Mac uses light mode.'
        if 'on' not in change and 'toggle' not in change:
            return before, before, 'Say dark mode or light mode.'
        wanted = (not dark) if change.get('toggle') else change['on']
        if wanted == dark:
            return before, before, 'Dark mode is already on.' if dark else 'Light mode is already on.'
        _script(runner, APPEARANCE_SET, 'true' if wanted else 'false')
        dark = _dark(runner)
        after = {'dark': dark}
        if dark != wanted:
            return before, after, 'I asked for %s mode, but the Mac still reports %s mode.' % ('dark' if wanted else 'light', 'dark' if dark else 'light')
        return before, after, 'Dark mode is now on.' if dark else 'Light mode is now on.'
    if setting == 'wifi':
        device = _wifi_device(runner)
        on = _wifi(runner, device)
        before = {'on': on}
        if change.get('read') or not ('on' in change or 'toggle' in change):
            return before, before, 'Wi-Fi is %s.' % _on_off(on)
        wanted = (not on) if change.get('toggle') else change['on']
        if wanted == on:
            return before, before, 'Wi-Fi is already %s.' % _on_off(on)
        _out(runner, ['networksetup', '-setairportpower', device, _on_off(wanted)])
        on = _wifi(runner, device)
        after = {'on': on}
        if on != wanted:
            return before, after, 'I tried to turn Wi-Fi %s, but it is still %s.' % (_on_off(wanted), _on_off(on))
        return before, after, 'Wi-Fi is now on.' if on else 'Wi-Fi is now off. Online lookups won’t work until it is back on.'
    if setting == 'bluetooth':
        on = _bluetooth(runner)
        before = {'on': on}
        state = '' if on is None else 'Bluetooth is %s. ' % _on_off(on)
        if change.get('read') and on is not None:
            return before, before, state.strip()
        wanted = None if change.get('read') else (not on if change.get('toggle') and on is not None else change.get('on'))
        if wanted is not None and wanted == on:
            return before, before, 'Bluetooth is already %s.' % _on_off(on)
        opened = _open_mac_pane(setting, runner)
        where = 'I opened Bluetooth settings so you can switch it there.' if opened else 'You can switch it in System Settings → Bluetooth.'
        reason = 'I could not read the Bluetooth state. ' if on is None else 'macOS doesn’t let me switch Bluetooth directly. '
        return before, before, state+reason+where
    if setting == 'brightness' and native is not None:
        return _brightness(change, native)
    # Focus and text size (and brightness without the helper) have no command this Mac offers.
    where = {'brightness': 'Displays', 'focus': 'Focus', 'text_size': 'Accessibility'}[setting]
    if change.get('read'):
        return {}, {}, 'I can’t read %s on this Mac. You’ll find it in System Settings → %s.' % (NAMES[setting], where)
    opened = _open_mac_pane(setting, runner)
    hint = ' Text size is under Display there.' if setting == 'text_size' else ''
    if opened:
        return {}, {}, 'I can’t change %s directly on this Mac, so I opened %s settings.%s' % (NAMES[setting], where, hint)
    return {}, {}, 'I can’t change %s directly on this Mac. You’ll find it in System Settings → %s.%s' % (NAMES[setting], where, hint)


KEY_STEPS = 16  # the brightness keys move the built-in display in sixteenths


def _brightness(change, native):
    """Built-in display brightness through the desktop helper (DisplayServices), read back after a
    change. Where that fails (an external display, the private symbol missing) the brightness keys
    are pressed instead, and the reply says the level could not be confirmed."""
    def level():
        try:
            value = native('--brightness').get('brightness')
        except RuntimeError:
            return None
        return round(value*100) if isinstance(value, (int, float)) and 0 <= value <= 1 else None
    before = level()
    if change.get('read'):
        if before is None:
            return {}, {}, 'I can’t read the brightness of this display.'
        return {'level': before}, {'level': before}, 'Screen brightness is at %d%%.' % before
    if 'on' in change or 'toggle' in change:
        return {}, {}, 'Tell me a brightness level, or say brighter or dimmer.'
    if before is not None:
        goal = change['set'] if 'set' in change else max(0, min(100, before+change['by']))
        if goal == before:
            return {'level': before}, {'level': before}, 'Brightness is%s at %d%%.' % (' already' if 'by' in change else '', before)
        try:
            after = native('--set-brightness', '%.3f' % (goal/100)).get('brightness')
            after = round(after*100) if isinstance(after, (int, float)) and 0 <= after <= 1 else None
        except RuntimeError:
            after = None
        if after is not None:
            if abs(after-goal) > 3:
                return {'level': before}, {'level': after}, 'I set the brightness to %d%%, but it now reads %d%%.' % (goal, after)
            return {'level': before}, {'level': after}, 'Brightness is now %d%%.' % after
        delta = goal-before
    else:
        delta = change['by'] if 'by' in change else (KEY_STEPS*100 if change['set'] >= 100 else -KEY_STEPS*100 if change['set'] <= 0 else None)
        if delta is None:
            return {}, {}, 'I can’t read this display’s brightness, so I can’t set an exact level. Say brighter or dimmer.'
    presses = max(1, min(KEY_STEPS, round(abs(delta)*KEY_STEPS/100)))
    try:
        native('--brightness-key', 'up' if delta > 0 else 'down', str(presses))
    except RuntimeError:
        return {}, {}, 'I couldn’t change the brightness of this display.'
    return {}, {}, 'I pressed brightness %s %d time%s, but I can’t read this display’s level to confirm it.' % (
        'up' if delta > 0 else 'down', presses, '' if presses == 1 else 's')


# --- Windows -------------------------------------------------------------------------------------
# Not a target yet (owner, 2026-11-06): every setting is refused honestly, nothing is run.

def _windows(setting, change):
    verb = 'check' if change.get('read') else 'change'
    return {}, {}, 'I can’t %s %s on Windows yet. You’ll find it in Settings.' % (verb, NAMES[setting])


def control(target, request='', *, runner=None, platform=None, native=None):
    """Read or change one setting. Returns {'setting', 'change', 'before', 'after', 'message',
    'changed'}; 'message' is the reply and states only what was observed. `native` calls the desktop
    helper (MacDesktopBridge.call) for brightness."""
    runner = runner or subprocess.run
    platform = platform or sys.platform
    setting, change = parse(target, request)
    result = {'setting': setting, 'change': change, 'before': {}, 'after': {}, 'changed': False}
    if setting is None:
        return dict(result, message='Which setting? I can check or change volume, mute, brightness, dark mode and Wi-Fi.')
    try:
        if platform == 'darwin':
            before, after, message = _mac(setting, change, runner, native)
        elif platform == 'win32':
            before, after, message = _windows(setting, change)
        else:
            before, after, message = {}, {}, 'Changing system settings works on macOS and Windows only.'
    except PermissionError:
        message = 'Allow A11y to control System Events in System Settings → Privacy & Security → Automation, then ask again.'
        before = after = {}
    except ControlError as error:
        message = ('This output device has no volume control I can read.' if str(error) == 'no volume'
                   else 'I couldn’t find a Wi-Fi adapter on this Mac.' if str(error) == 'no wifi'
                   else 'I couldn’t read or change %s on this computer at the moment.' % NAMES[setting])
        before = after = {}
    return dict(result, before=before, after=after, message=message, changed=before != after)
