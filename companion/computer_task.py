"""Bounded observe/act/re-observe sessions; screenshots never enter persistent history."""
from copy import deepcopy
import hashlib
import json
import re
import math
import threading
import time
from .models import RESPONSE_SCHEMA, ValidationError, validate_response, text  # noqa: F401 - RESPONSE_SCHEMA re-exported
from .timing import timing
# The step grammar and prompts, completion checks, answer writing and the menu/Settings fallbacks
# live in their own modules; every name stays importable from here.
from .task_prompts import (  # noqa: F401 - re-exported: imported and patched as computer_task.<name>
    TOOLS, SCHEMA, _action_step, _terminal_step, UNSUPPORTED, schema_for,
    FILE_INTERACTION_SYSTEM, FILE_INTERACTION_SCHEMA, COMMIT_SYSTEM, COMMIT_SCHEMA, PLAN_SYSTEM, PLAN_SCHEMA, REPORT_PASS,
    IMAGE_PASS, SYSTEM)
from .task_completion import (  # noqa: F401 - re-exported: imported and patched as computer_task.<name>
    MET_SYSTEM, MET_SCHEMA, CHECK_KINDS, NOTHING_MISSING, NEGATIONS, structured_facts, completion_evidence, completion_values,
    completion_schema, completion_result, listed_checks, required_checks, check_proven, unproven_parts, uncovered_names,
    _words, _tokens, _asked, _asked_text, _given_names, _negated, _hint_tried, _finder_place, CompletionChecks)
from .task_answers import (  # noqa: F401 - re-exported: imported and patched as computer_task.<name>
    ANSWER_SYSTEM, ANSWER_SCHEMA, AnswerWriting)
from .task_fallbacks import (  # noqa: F401 - re-exported: imported and patched as computer_task.<name>
    PANE_SYSTEM, PANE_SYSTEM_WINDOWS, MENU_SYSTEM, MENU_UNSAFE, MENU_CONTENT, Fallbacks)


# "Any new notifications?" means opening the app's notification view, not reading whatever
# feed is on screen. Apps name that view one of these (Teams, Slack: Activity).
NOTIFICATION_GOAL = re.compile(r'\b(notifications?|activity(?!\s+monitor)|alerts?|mentions?|unread)\b', re.I)  # not the Activity Monitor app
NOTIFICATION_VIEWS = ('activity', 'notifications')

OPENING_ACTIONS = {'launch_app', 'focus_app', 'browse_url', 'new_window', 'wait', 'open_settings_pane'}
SETTLE_AFTER = {'launch_app', 'browse_url', 'open_settings_pane'}


# A click A11y chooses in code answers whatever the screen asks: "OK" can confirm a deletion. These
# confirming or changing labels wait for the user, besides the sensitive ones in needs_review.
CONFIRMING = re.compile(r'\b(ok|okay|yes|confirm|continue|proceed|archive|save|apply|done|finish|move|mark|empty|erase|trash|'
                        r'discard|replace|overwrite|close|quit|leave|end|reset|restore|sign out|log ?out|unsend|forward)\b', re.I)


def search_field_focused(snapshot):
    """True when the focused field is a search field: its role, or its accessible name
    ("Search Wikipedia", Safari's "smart search field"). Never reads what was typed."""
    focused = snapshot.get('focused_bounds') or {}
    if snapshot.get('focused_role') == 'AXSearchField':
        return True
    for target in snapshot.get('targets') or ():
        b = target.get('bounds') or {}
        if (target.get('role') in ('AXTextField', 'AXSearchField', 'AXComboBox')
                and all(abs((b.get(key) or 0)-(focused.get(key) or -99)) <= 2 for key in ('x', 'y', 'width', 'height'))):
            return target.get('role') == 'AXSearchField' or 'search' in (target.get('label') or '').casefold()
    return False


# Buttons that only decline or dismiss ("Don't Allow", "Not now", "Reject all", "Close"): never a change.
DECLINE = re.compile(r"^\W*(?:don'?t allow|do not allow|not now|no,? thanks|reject(?: all)?|decline|deny|close|cancel|dismiss|skip|"
                     r"maybe later|later|only (?:necessary|essential)(?: cookies)?|(?:necessary|essential) only|use necessary cookies only|got it)\W*$", re.I)
# Menu items that change or end something; the user confirms them.
MENU_CHANGES = re.compile(r'\b(quit|delete|rename|empty|erase|log ?out|sign ?out|restart|shut ?down|sleep|force quit|move to (?:bin|trash)|send|remove|format|reset)\b', re.I)


def needs_review(action, risk):
    if action.get('type') == 'choose_menu':
        return risk != 'navigation' or bool(MENU_CHANGES.search(action.get('path', '')))
    if action.get('type') in ('click_point', 'press_control') and DECLINE.match(action.get('label', '')):
        return False
    if risk != 'navigation':
        return True
    if action.get('type') == 'press_key' and action.get('key') in ('enter', 'cmd_w', 'cmd_s'):
        return True  # ⌘S writes a file: always the user's OK (owner: "why can't a11y press save")
    if action.get('type') == 'drag' and not action.get('selects_text'):
        return True  # a drag can move files, windows or items; selecting text in a field is only selecting
    import re
    return bool(re.search(r'\b(send|post|delete|rename|remove|buy|pay|purchase|subscribe|install|download|upload|allow|permission|password|sign.?in|log.?in|submit|'
                          r'turn on|turn off|enable|disable|accept|block|unsubscribe|save|continue (?:as|with))\b', action.get('label', ''), re.I))


def screen_text(visual, snapshot):
    """Bound OCR to the observed window when possible; never retain screenshot text."""
    import math
    result = []
    regions = visual.get('text_regions', [])
    for region in regions[:200] if isinstance(regions, list) else []:
        if not isinstance(region, dict) or not isinstance(region.get('text'), str):
            continue
        values = [region.get(key) for key in ('x', 'y', 'width', 'height')]
        if any(type(value) not in (int, float) or not math.isfinite(value) for value in values):
            continue
        x, y, width, height = values
        if not (0 <= x < 1 and 0 <= y < 1 and width > 0 and height > 0):
            continue
        result.append({'text': region['text'][:160], 'x': x, 'y': y,
                       'width': min(width, 1-x), 'height': min(height, 1-y)})
    screen, window = visual.get('screen', {}), snapshot.get('window', {}).get('bounds', {})
    if all(key in screen and key in window for key in ('x', 'y', 'width', 'height')):
        scoped = [r for r in result if window['x'] <= screen['x']+(r['x']+r['width']/2)*screen['width'] < window['x']+window['width']
                  and window['y'] <= screen['y']+(r['y']+r['height']/2)*screen['height'] < window['y']+window['height']]
        result = scoped or result
    return result[:60]


def compact_regions(regions):
    """OCR lines as 'text|x,y' with x,y the line's center in image fractions."""
    return ['%s|%.3f,%.3f' % (r['text'], r['x']+r['width']/2, r['y']+r['height']/2) for r in regions]


ROLE_NAMES = {'AXButton': 'button', 'AXLink': 'link', 'AXCheckBox': 'checkbox', 'AXRadioButton': 'tab or option', 'AXPopUpButton': 'menu',
              'AXMenuButton': 'menu', 'AXTab': 'tab', 'AXDisclosureTriangle': 'expander', 'AXMenuItem': 'menu item', 'AXTextField': 'field',
              'AXTextArea': 'field', 'AXSearchField': 'search field', 'AXComboBox': 'field', 'AXSlider': 'slider', 'AXSwitch': 'switch',
              'AXCell': 'item', 'AXRow': 'row'}


def screen_marks(targets, regions, screen, limit=160):
    """Numbered things on screen to click: accessible targets (buttons, links, fields, rows,
    named icons, web pages included), then OCR lines no target already covers. Each is
    {'id', 'kind', 'label', 'x', 'y', 'width', 'height'} in image fractions."""
    marks = []
    if screen and screen.get('width') and screen.get('height'):
        for target in targets or ():
            b = (target.get('bounds') or {}) if isinstance(target, dict) else {}
            if not isinstance(target.get('label'), str) or not all(isinstance(b.get(k), (int, float)) for k in ('x', 'y', 'width', 'height')):
                continue
            x, y = (b['x']-screen.get('x', 0))/screen['width'], (b['y']-screen.get('y', 0))/screen['height']
            width, height = b['width']/screen['width'], b['height']/screen['height']
            if 0 <= x+width/2 < 1 and 0 <= y+height/2 < 1:
                marks.append({'kind': ('file or folder' if target.get('file_item') else ('page ' if target.get('page') else '')+ROLE_NAMES.get(target.get('role'), 'control'))
                              + (', selected' if target.get('selected') is True else ''), 'label': target['label'][:80],
                              'x': x, 'y': y, 'width': width, 'height': height})
    covered = list(marks)
    for region in regions:
        cx, cy = region['x']+region['width']/2, region['y']+region['height']/2
        if not any(m['x'] <= cx < m['x']+m['width'] and m['y'] <= cy < m['y']+m['height'] for m in covered):
            marks.append({'kind': 'text', 'label': region['text'][:80], 'x': region['x'], 'y': region['y'],
                          'width': region['width'], 'height': region['height']})
    marks = marks[:limit]
    for number, mark in enumerate(marks, 1):
        mark['id'] = 't%d' % number
    return marks


def compact_marks(marks):
    """'t12|link|19 comments|0.270,0.154': id, kind, label and center (image fractions)."""
    return ['%s|%s|%s|%.3f,%.3f' % (m['id'], m['kind'], m['label'], m['x']+m['width']/2, m['y']+m['height']/2) for m in marks]


def has_window(snapshot, bundle_id):
    """Whether the app shows a window of its own. Finder's desktop is listed as an untitled window
    the size of the screen; it is not one (Finder with only the desktop stalled tasks, live)."""
    def real(window):
        return not (bundle_id == 'com.apple.finder' and not str(window.get('title') or '').strip())
    front = snapshot.get('window') if (snapshot.get('app') or {}).get('bundle_id') == bundle_id else None
    return bool(front and real(front)) or any(w.get('bundle_id') == bundle_id and real(w) for w in snapshot.get('app_windows') or [])


def already_typed(text, typed):
    """Whether every line of text was typed already in this task (case and spacing aside)."""
    words = lambda value: ' '.join(value.casefold().split())
    lines = [words(line) for line in text.split('\n') if line.strip()]
    done = {words(line) for value in typed for line in value.split('\n') if line.strip()}
    return bool(lines) and all(line in done for line in lines)


def resolve_target(raw, marks):
    """click_target → the click at that target's center, labelled with its name; drag_target → a drag
    between two targets (lines of text: from the first's start to the second's end, which selects them)."""
    action = raw.get('action') if isinstance(raw, dict) else None
    if isinstance(action, dict) and action.get('type') == 'drag_target':
        first = next((m for m in marks if m['id'] == action.get('from_target')), None)
        second = next((m for m in marks if m['id'] == action.get('to_target')), None)
        if first is None or second is None:
            raise ValidationError('Those target numbers are not on the current screen. Choose listed targets.')
        text = first['kind'] == 'text' and second['kind'] == 'text'
        x, y = (first['x']+0.001 if text else first['x']+first['width']/2), first['y']+first['height']/2
        to_x, to_y = (second['x']+second['width']-0.001 if text else second['x']+second['width']/2), second['y']+second['height']/2
        label = ('Select from “%s” to “%s”' if text else 'Drag “%s” to “%s”') % (first['label'][:40], second['label'][:40])
        return dict(raw, action={'type': 'drag', 'x': round(x, 4), 'y': round(y, 4), 'to_x': round(to_x, 4), 'to_y': round(to_y, 4), 'label': label})
    if not isinstance(action, dict) or action.get('type') not in ('click_target','open_target'):
        return raw
    mark = next((m for m in marks if m['id'] == action.get('target_id')), None)
    if mark is None:
        raise ValidationError('That target number is not on the current screen. Choose a listed target.')
    if action['type']=='open_target' and not mark.get('kind','').startswith('file or folder'):
        raise ValidationError('open_target requires an observed file or folder, not another control.')
    click = {'type': 'click_point', 'x': round(mark['x']+mark['width']/2, 4), 'y': round(mark['y']+mark['height']/2, 4), 'label': mark['label']}
    click.update({key: action[key] for key in ('button', 'click_count') if key in action})
    if action['type']=='open_target':
        click.update(button='left',click_count=2)
    return dict(raw, action=click)


def observe_desktop(desktop, **kwargs):
    value = desktop.collect_once(**kwargs)
    return value if isinstance(value,dict) else desktop.context()


def on_site(page, wanted):
    """Whether the address `page` is on the site the address `wanted` names: its host, a subdomain
    of it, or a host that site is known to use. An empty or host-less address is never a match."""
    from urllib.parse import urlsplit
    from .web_apps import same_site_hosts
    host = lambda value: (urlsplit(str(value)).hostname or '').removeprefix('www.')
    hosts = {host(wanted)} | {h.removeprefix('www.') for h in same_site_hosts(wanted)}
    return bool(host(page)) and (host(page) in hosts or any(host(page).endswith('.'+h) for h in hosts if h))


def observation_state(snapshot,visual):
    window=snapshot.get('window') or {}
    finder=snapshot.get('finder') or {}
    return (tuple(sorted(str(r.get('text','')) for r in visual.get('text_regions') or [] if isinstance(r,dict))),
            (snapshot.get('app') or {}).get('bundle_id'),window.get('title'),window.get('document'),
            finder.get('target'),finder.get('view'),finder.get('sort'))


def scroll_view_state(snapshot, regions):
    """A volatile fingerprint of visible content AND position, never an action receipt."""
    import hashlib
    text = [(r['text'], *(round(r.get(k, 0), 2) for k in ('x', 'y', 'width', 'height')))
            for r in regions[:200]]
    targets = [(t.get('label', ''), t.get('role', ''),
                *(round((t.get('bounds') or {}).get(k, 0)/4) for k in ('x', 'y', 'width', 'height')))
               for t in (snapshot.get('targets') or [])[:150]]
    if not text and not targets:
        return None  # absent observations cannot prove that a viewport stayed still
    window = snapshot.get('window') or {}
    state = [(snapshot.get('app') or {}).get('bundle_id'), window.get('title'),
             window.get('document'), window.get('url'), window.get('bounds'), sorted(text), sorted(targets)]
    return hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()[:16]


def screen_signature(regions, snapshot):
    """What the screen reads, plus what is selected. A selection changes no text: selecting a Finder
    row read as "nothing changed", and the fallback menu pick sorted the list under it (live, scroll batch)."""
    chosen = [str(name) for name in (snapshot.get('finder') or {}).get('selection') or []] + \
        [str(e.get('name') or e.get('text') or '') for e in snapshot.get('selected') or [] if isinstance(e, dict)]
    # The chosen tab or option, as the accessibility snapshot reports it: switching tabs can change no word.
    chosen += ['\x1e'+str(t.get('label', '')) for t in snapshot.get('targets') or [] if isinstance(t, dict) and t.get('selected') is True]
    return hashlib.sha256('\n'.join(sorted(r.get('text', '') for r in regions)+['\x1f']+chosen).encode()).hexdigest()[:16]


# Controls that hold a state of their own. Pressing one changes how it looks (a tick, a knob, a
# highlighted tab), often without changing a single word on screen.
STATE_KINDS = frozenset({'checkbox', 'switch', 'tab or option', 'tab'})
STATE_ROLES = frozenset(role for role, kind in ROLE_NAMES.items() if kind in STATE_KINDS)


def region_look(image_data, rect):
    """A fingerprint of how one rectangle of the capture looks (rect in image fractions): colours
    on a grid of at most a few thousand points, coarsened so that compression noise is not a
    change. None when the capture cannot be read. Only the hash is kept, never pixels."""
    import base64
    try:
        from PyQt6.QtGui import QImage
        image = QImage.fromData(base64.b64decode(image_data.split(',', 1)[1]))
        left, top = int(rect['x']*image.width()), int(rect['y']*image.height())
        width, height = int(rect['width']*image.width()), int(rect['height']*image.height())
    except (ImportError, ValueError, IndexError, AttributeError, TypeError, KeyError):
        return None
    if image.isNull() or width < 2 or height < 2:
        return None
    stride = max(2, math.isqrt(width*height//4000))
    colours = bytearray()
    for y in range(max(0, top), min(image.height(), top+height), stride):
        for x in range(max(0, left), min(image.width(), left+width), stride):
            colours.extend(((image.pixel(x, y) >> 4) & 0x0F0F0F).to_bytes(3, 'big'))
    return hashlib.sha256(colours).hexdigest()[:16] if colours else None


def state_control(action, marks, snapshot, screen):
    """Where the state-holding control this click or press acts on is: {'label', 'rect'} with rect in
    image fractions, or None when the target is not a checkbox, switch, tab or option."""
    def kind(mark):
        return mark.get('kind', '').removeprefix('page ').split(',')[0]
    if action.get('type') == 'click_point' and action.get('button', 'left') == 'left':
        inside = [m for m in marks if kind(m) in STATE_KINDS and m['x'] <= action.get('x', -1) < m['x']+m['width']
                  and m['y'] <= action.get('y', -1) < m['y']+m['height']]
        if inside:
            mark = min(inside, key=lambda m: m['width']*m['height'])
            return {'label': mark['label'], 'rect': {key: mark[key] for key in ('x', 'y', 'width', 'height')}}
    if action.get('type') == 'press_control' and screen and screen.get('width') and screen.get('height'):
        control = next((c for c in (snapshot.get('controls') or []) if isinstance(c, dict) and c.get('id') == action.get('control_id')), None)
        b = (control or {}).get('bounds') or {}
        if control and control.get('role') in STATE_ROLES and all(isinstance(b.get(k), (int, float)) for k in ('x', 'y', 'width', 'height')):
            return {'label': control.get('label', ''), 'rect': {'x': (b['x']-screen.get('x', 0))/screen['width'], 'y': (b['y']-screen.get('y', 0))/screen['height'],
                                                             'width': b['width']/screen['width'], 'height': b['height']/screen['height']}}
    return None


def compact_desktop(snapshot):
    """Current app, window and controls in a few tokens each; same facts as before."""
    result = {key: snapshot[key] for key in ('status', 'focused_role', 'companion_focused', 'menus') if key in snapshot}
    app, window = snapshot.get('app') or {}, snapshot.get('window') or {}
    if app:
        result['app'] = '%s (%s)' % (app.get('name', ''), app.get('bundle_id', ''))
    if window:
        result['window'] = window.get('title', '')
    facts = structured_facts(snapshot)
    if facts:
        result['facts'] = facts
    controls = []
    for control in snapshot.get('controls') or []:
        b = control.get('bounds') or {}
        entry = '%s|%s|%s' % (control.get('id', ''), control.get('role', '').removeprefix('AX').lower(), control.get('label', ''))
        if all(key in b for key in ('x', 'y', 'width', 'height')):
            entry += '|%d,%d,%d,%d' % (b['x'], b['y'], b['width'], b['height'])
        if control.get('enabled') is False:
            entry += '|disabled'
        controls.append(entry)
    if controls:
        result['controls'] = controls
    apps = ['%s=%s' % (a.get('name', ''), a.get('bundle_id', '')) for a in snapshot.get('available_apps') or []]
    if apps:
        result['running_apps'] = apps
    windows = ['%s (%s)' % (w.get('title', ''), w.get('name', '')) for w in (snapshot.get('app_windows') or [])[:12]]
    if windows:
        result['open_windows'] = windows
    return result


def browser_launch_as_navigation(action, goal, catalog):
    """Launching only the browser for "open <web app> in <browser>" becomes the
    navigation itself, instead of a rejected step."""
    from .app_names import resolve_app
    from .desktop_context import BROWSER_IDS
    from .web_apps import find_site, browser_mode
    site = find_site(goal or '')
    if not site:
        return None
    ids = [action['bundle_id']] if action['bundle_id'] in BROWSER_IDS else [app['bundle_id'] for app in resolve_app(action['bundle_id'], catalog)]
    if len(ids) != 1 or ids[0] not in BROWSER_IDS:
        return None
    name = next((app['name'] for app in catalog if app['bundle_id'] == ids[0]), 'the browser')
    return {'type': 'browse_url', 'bundle_id': ids[0], 'url': site[1], 'label': 'Open '+site[0].title()+' in '+name,
            'browser_mode': browser_mode(goal)}


LABEL_FILLER = frozenset('icon tab button link the a menu item left right top bottom sidebar rail bar on in at of '
                         'click app navigation nav section option panel pane label text or'.split())


HISTORY_LIMIT = 4  # back/forward steps in a row before it counts as a loop ("go back two pages" is fine)


def ordinal(n):
    return {1: 'first', 2: 'second', 3: 'third'}.get(n, '%dth' % n)


def label_words(label):
    import re
    return tuple(word for word in re.findall(r"[\w']+", label.casefold()) if word not in LABEL_FILLER)


# A goal that is one click on a named thing: "click on Chat", "double-click Report.pdf".
CLICK_GOAL = re.compile(r'^\W*(?:(?:please|ok(?:ay)?|can you|could you)\W+)*(?P<verb>double[- ]?click|right[- ]?click|click|tap)'
                        r'\s+(?:on\s+)?(?P<target>.+?)[\s.!?]*$', re.I)


def click_goal(goal):
    """(label words, button, click count) when the whole goal is one click on a named thing, else None."""
    match = CLICK_GOAL.match(goal or '')
    if not match:
        return None
    want = label_words(match.group('target'))
    if not want or len(want) > 24:
        return None
    verb = match.group('verb').casefold()
    return want, 'right' if verb.startswith('right') else 'left', 2 if verb.startswith('double') else 1


def scoped_click_request(goal, app_name):
    """Remove only an observed app/window qualifier, preserving the literal target text."""
    if not app_name:
        return goal
    scope = r'\s+(?:in|on)\s+(?:(?:this|the|my)\s+)?(?:(?:current|front|open)\s+)?(?:'+re.escape(app_name)+r'\s+)?window\s*[.!?]*$'
    return re.sub(scope,'',goal,flags=re.I)


def scoped_click_goal(goal, app_name):
    return click_goal(scoped_click_request(goal,app_name))


def requested_gesture(action, goal):
    """Keep the user's explicit gesture when the model identifies its named target.

    Qualifiers and subsequent work can make a click goal too long for the exact
    shortcut. They do not turn a requested double-click into a single-click.
    Only the named prefix matches: intermediate toolbar/navigation clicks keep
    the model's gesture. Target validation still runs after this normalization.
    """
    if action.get('type') != 'click_point':
        return action
    match = CLICK_GOAL.match(goal or '')
    if not match:
        return action
    label = label_words(str(action.get('label','')).strip('“”\"'))
    wanted = label_words(match.group('target').strip('“”\"'))
    if not label or wanted[:len(label)] != label:
        return action
    verb = match.group('verb').casefold()
    return dict(action, button='right' if verb.startswith('right') else 'left',
                click_count=2 if verb.startswith('double') else 1)


def finder_name_editor(snapshot):
    """A native filename editor inside Finder's item tree, never its search box."""
    snapshot = snapshot or {}
    return ((snapshot.get('app') or {}).get('bundle_id') == 'com.apple.finder'
            and snapshot.get('focused_role') == 'AXTextField'
            and (bool({'AXCell','AXRow'} & set(snapshot.get('focused_container_roles') or []))
                 or snapshot.get('focused_container_roles')==['AXApplication'] and not (snapshot.get('window') or {}).get('bounds')))


MENU_GOAL = re.compile(r"^\W*(?:please\s+)?(?:open|show(?: me)?|click(?: on)?)\s+(?:the\s+)?(?P<menu>[\w&'-]+(?:\s+[\w&'-]+)?)\s+menu"
                       r"(?:\s+(?:in|of)\s+(?P<app>[\w .&'-]+?))?[\s.!?]*$|^\W*(?:in|on)\s+(?P<app2>[\w .&'-]+?),?\s+(?:open|show(?: me)?)\s+the\s+"
                       r"(?P<menu2>[\w&'-]+(?:\s+[\w&'-]+)?)\s+menu[\s.!?]*$", re.I)
ORDINALS = {'first': 1, 'second': 2, 'third': 3, 'fourth': 4, 'fifth': 5, 'sixth': 6, 'seventh': 7, 'eighth': 8,
            'ninth': 9, 'tenth': 10, '1st': 1, '2nd': 2, '3rd': 3, '4th': 4, '5th': 5, '6th': 6, '7th': 7, '8th': 8, '9th': 9, '10th': 10}
# A goal that asks for a fact ("…and tell me what year she was born"), not only to get somewhere.
ASKS_FOR_FACT = re.compile(r"\b(?:tell|show)\s+me\s+(?:what|which|when|who|where|how|the|if|whether)\b|\bwhat(?:'s|s| is| are| was| were| year| time| day)\b"
                           r"|\b(?:which|when|who|how\s+(?:many|much|long|old|big))\b|\?\s*$", re.I)
# "The History section of the Coffee article", "take me to the History part": a section of the page.
SECTION = re.compile(r"\b(?:the|its|this|that)\s+(?P<name>[\w'’-]+(?:\s+[\w'’-]+){0,3}?)\s+(?:section|part|heading)\b", re.I)
# What a person types into the address bar to go somewhere: a full address or a bare domain ("open-meteo.com/en").
ADDRESS = re.compile(r'(?:https?://\S+|(?:[a-z0-9-]+\.)+[a-z]{2,24}(?:[/?#]\S*)?)', re.I)
# "A new TextEdit document", "another window": a new one, never the one already open.
NEW_DOCUMENT = re.compile(r'\b(?:new|another|fresh|blank|empty)\s+(?:[\w-]+\s+){0,2}(?:document|doc|window|file|page)\b', re.I)
# A window goes full screen or into the Dock only when the goal says so.
WINDOW_SIZE_WORDS = re.compile(r'\b(?:full[\s-]?screen|zoom|maximi[sz]e|minimi[sz]e|bigger window|enlarge)\b', re.I)
FULL_SCREEN_MENU = re.compile(r'\b(?:enter|exit|toggle)?\s*full[\s-]?screen\b|\bminimi[sz]e\b|\bzoom\b', re.I)
# "Sort my Documents by date added", "show Downloads in Finder, newest added first": Finder's own
# View > Sort By menu, in code (the model clicked around for 24 steps, live v3).
FINDER_SORT = re.compile(r'\bsort(?:ed)?\b.*?\bby\s+(?P<column>name|kind|date added|date modified|date created|date last opened|last opened|size|tags)\b'
                         r'|\b(?P<newest>newest|latest|most recently)\s+(?:added|first)\b', re.I)
SORT_TITLES = {'name': 'Name', 'kind': 'Kind', 'date added': 'Date Added', 'date modified': 'Date Modified', 'date created': 'Date Created',
               'date last opened': 'Date Last Opened', 'last opened': 'Date Last Opened', 'size': 'Size', 'tags': 'Tags'}
# Finder's own name for a list view's sort column ("name column"), per asked column. Date Added and
# Date Last Opened have no such name, so code cannot compare them (see sort_observed).
SORT_COLUMNS = {'Name': 'name', 'Kind': 'kind', 'Date Modified': 'modification date', 'Date Created': 'creation date',
                'Size': 'size', 'Tags': 'label'}
SORT_COLUMN_NAMES = set(SORT_COLUMNS.values()) | {title.casefold() for title in SORT_COLUMNS} | {'version', 'comment', 'none'}
# "What's the fifth story on Hacker News?": the line numbered 5 on screen, read in code (the model
# clicked a story instead of reading the list, live v3).
NTH_ITEM = re.compile(r"\b(?:what(?:'s|s| is| are)|tell me|read(?: me)?|which is|give me)\b.*?\bthe\s+(?P<n>first|second|third|fourth|fifth|"
                      r"sixth|seventh|eighth|ninth|tenth|\d{1,2}(?:st|nd|rd|th)?)\s+(?:top\s+)?(?:story|post|headline|result|item|article|link|entry)\b", re.I)
WORD_NUMBERS = {'first': 1, 'second': 2, 'third': 3, 'fourth': 4, 'fifth': 5, 'sixth': 6, 'seventh': 7, 'eighth': 8, 'ninth': 9, 'tenth': 10}
# "Switch to the GitHub tab", "go back to the Wikipedia tab I had open": that site's open tab.
# What is left of "open X and Y in two separate tabs" once the sites are taken out: opening, nothing more.
ONLY_OPENING = re.compile(r'^\W*(?:please\s+|can\s+you\s+|could\s+you\s+)?(?:open|go\s+to|show\s+me|pull\s+up|bring\s+up|load)\s+'
                          r'(?:both\s+)?\S+(?:\s*(?:,|and|&)\s*\S+)+'
                          r'(?:\s+(?:in|as|on)\s+(?:\w+\s+){0,2}(?:tabs?|windows?))?(?:\s+(?:please|for\s+me))?\W*$', re.I)
SWITCH_TAB = re.compile(r'^\W*(?:please\s+)?(?:switch|go\s+back|get\s+back|return|go|take\s+me)\s+(?:back\s+)?to\s+(?:the\s+|my\s+)?(?P<site>.+?)\s+tab\b', re.I)
# "Go back", "go back in Safari", "go forward two pages": the browser's own back/forward, in code.
HISTORY_GOAL = re.compile(r'^\W*(?:please\s+|can you\s+)?(?:go|take me|bring me|navigate|move)\s+(?P<way>back|forward)(?:\s+(?P<count>one|a|two|three|1|2|3)\s+pages?)?'
                          r'(?:\s+(?:a\s+page|to\s+the\s+(?:previous|last|next)\s+page))?(?:\s+(?:in|on)\s+(?P<app>safari|chrome|google chrome|the browser))?\W*$', re.I)
ROW_GOAL = re.compile(r'^(?:the\s+|my\s+)?(?P<ordinal>' + '|'.join(ORDINALS) + r')\s+(?P<noun>[\w-]+)(?:\s+(?:in|on)\s+(?P<app>[\w .-]+))?$', re.I)


def row_goal(goal):
    """(position, noun, button, click count, app or '') for "click the first email (in Mail)", else None."""
    match = CLICK_GOAL.match(goal or '')
    row = ROW_GOAL.match(match.group('target').strip()) if match else None
    if not row:
        return None
    verb = match.group('verb').casefold()
    return (ORDINALS[row.group('ordinal').casefold()], row.group('noun'), 'right' if verb.startswith('right') else 'left',
            2 if verb.startswith('double') else 1, (row.group('app') or '').strip())


def list_row(lists, position, window, screen, noun=''):
    """Image point of the nth visible row of the list the noun names ("mailbox" → Mail's
    "mailboxes"), else of the window's main list: a table before an outline or list (Mail's
    messages, not its mailboxes), then the widest. None when it is not visible."""
    def inside(b, area):
        x, y = b['x']+b['width']/2, b['y']+b['height']/2
        return area['x'] <= x < area['x']+area['width'] and area['y'] <= y < area['y']+area['height']
    candidates = [item for item in lists or () if isinstance(item, dict) and isinstance(item.get('rows'), list)
                  and all(isinstance((item.get('bounds') or {}).get(key), (int, float)) for key in ('x', 'y', 'width', 'height'))]
    if not candidates or not screen or not screen.get('width'):
        return None
    forms = lambda word: {word, word+'s', word+'es'}
    noun = noun.casefold()
    named = [item for item in candidates if noun and any(word in forms(noun) or noun in forms(word)
                                                         for word in re.findall(r'\w+', (item.get('label') or '').casefold()))]
    main = named[0] if len(named) == 1 else min(candidates, key=lambda item: (item.get('role') != 'AXTable', -item['bounds']['width']))
    rows = [b for b in main['rows'] if isinstance(b, dict) and all(isinstance(b.get(key), (int, float)) for key in ('x', 'y', 'width', 'height'))
            and b['height'] > 6 and (not window or inside(b, window)) and inside(b, screen)]
    if position > len(rows):
        return None
    b = rows[position-1]
    x = (b['x']+b['width']/2-screen.get('x', 0))/screen['width']
    y = (b['y']+b['height']/2-screen.get('y', 0))/screen['height']
    return (x, y) if 0 <= x < 1 and 0 <= y < 1 else None


def labelled_targets(want, regions, controls, screen):
    """Places on screen labelled exactly `want`: [(x, y, control or None)], one per place.

    An accessible control and the OCR line of its own title are the same place."""
    found = []
    for control in controls or ():
        bounds = (control.get('bounds') or {}) if isinstance(control, dict) else {}
        if (control.get('enabled', True) and label_words(control.get('label', '')) == want and screen and screen.get('width')
                and all(isinstance(bounds.get(key), (int, float)) for key in ('x', 'y', 'width', 'height'))):
            x = (bounds['x']+bounds['width']/2-screen.get('x', 0))/screen['width']
            y = (bounds['y']+bounds['height']/2-screen.get('y', 0))/screen['height']
            if 0 <= x < 1 and 0 <= y < 1 and not any(abs(x-fx)<.001 and abs(y-fy)<.001 for fx,fy,_ in found):
                found.append((x, y, control))
    for region in regions:
        if label_words(region.get('text', '')) == want:
            x, y = region['x']+region['width']/2, region['y']+region['height']/2
            if not any(abs(x-fx) < .03 and abs(y-fy) < .03 for fx, fy, _ in found):
                found.append((x, y, None))
    return found


def named_on_screen(label, regions, snapshot):
    """Whether something on screen carries this name: a line of text or an accessible target that has
    all of the label's words, or most of them and nothing else ("Zelcroft project" for "Zelcroft
    project folder"), the way snap_click matches a label to a place."""
    want = label_words(label)
    if not want:
        return True  # nothing to look for
    texts = [r.get('text', '') for r in regions if isinstance(r, dict)]
    texts += [t.get('label', '') for key in ('targets', 'controls') for t in snapshot.get(key) or [] if isinstance(t, dict)]
    for text in texts:
        words = label_words(text)
        if words and (set(want) <= set(words) or len(''.join(words)) >= 4 and set(words) <= set(want) and len(words)*2 >= len(want)):
            return True
    return False


def pixel_model(provider):
    """Qwen2.5-VL answers clicks in pixels of the image it was shown (Qwen3-VL uses a 0-1000 grid)."""
    name = (getattr(getattr(provider, 'config', None), 'vision_model', '') or '').casefold()
    return name.startswith(('qwen2.5vl', 'qwen2.5-vl'))


def pixels_to_fractions(action, image_data):
    """A click in the model image's pixels as image fractions; fractions pass through."""
    x, y = action.get('x'), action.get('y')
    if not all(isinstance(n, (int, float)) and not isinstance(n, bool) for n in (x, y)) or (x <= 1 and y <= 1):
        return action
    import base64
    try:
        from PyQt6.QtGui import QImage
        image = QImage.fromData(base64.b64decode(image_data.split(',', 1)[1]))
    except (ImportError, ValueError, IndexError):
        return action
    if image.isNull() or not image.width() or not image.height():
        return action
    return dict(action, x=min(x/image.width(), 0.999), y=min(y/image.height(), 0.999))


def normalize_click(action):
    """Accept the 0–1000 grid Qwen-VL models are trained on as well as fractions; anything else stays invalid."""
    numbers = [action.get(key) for key in ('x', 'y')]
    if all(isinstance(n, (int, float)) and not isinstance(n, bool) and math.isfinite(n) for n in numbers) \
            and all(0 <= n < 1000 for n in numbers):
        if all(n > 1 or n == 0 for n in numbers):  # a mix of a fraction and a grid value stays invalid
            return dict(action, x=numbers[0]/1000, y=numbers[1]/1000)
        if any(n == 1 for n in numbers):  # the far edge written as 1.0
            return dict(action, x=min(numbers[0], 0.999), y=min(numbers[1], 0.999))
    return action


def snap_click(action, text_regions, controls=(), screen=None):
    """Move a click onto the visible text or accessible control its label names.

    The model decides what to click; OCR (Apple Vision / Windows.Media.Ocr) and the
    accessibility tree, which measure the real screen, decide where. With several
    matches the one nearest the model's point wins; with none the point is kept."""
    want = label_words(action.get('label', ''))
    if not want or not all(isinstance(action.get(key), (int, float)) for key in ('x', 'y')):
        return action
    candidates = []
    for region in text_regions:
        if isinstance(region, dict) and all(isinstance(region.get(key), (int, float)) for key in ('x', 'y', 'width', 'height')):
            candidates.append((region.get('text', ''), region['x']+region['width']/2, region['y']+region['height']/2))
    if screen and screen.get('width') and screen.get('height'):
        for control in controls:
            bounds = (control.get('bounds') or {}) if isinstance(control, dict) else {}
            if all(isinstance(bounds.get(key), (int, float)) for key in ('x', 'y', 'width', 'height')) and bounds['width'] > 0:
                x = (bounds['x']+bounds['width']/2-screen.get('x', 0))/screen['width']
                y = (bounds['y']+bounds['height']/2-screen.get('y', 0))/screen['height']
                if 0 <= x < 1 and 0 <= y < 1:
                    candidates.append((control.get('label', ''), x, y))
    exact, partial = [], []
    for text, x, y in candidates:
        words = label_words(text)
        if not words:
            continue
        if words == want:
            exact.append((x, y))
        elif len(''.join(words)) >= 4 and set(words) <= set(want) and len(words)*2 >= len(want):
            partial.append((x, y))
    found = exact or partial
    if not found:
        return action
    x, y = min(found, key=lambda point: (point[0]-action['x'])**2+(point[1]-action['y'])**2)
    return dict(action, x=round(x, 4), y=round(y, 4))


def validate_step(raw, desktop, snapshot, *, goal_mode=None, screen=None, text_regions=(), catalog=(), goal=None, snap=True):
    if not isinstance(raw, dict) or set(raw) != {'status', 'message', 'risk', 'action'} or raw['status'] not in ('action', 'done', 'needs_user') or raw['risk'] not in ('navigation', 'change'):
        raise ValidationError('The task model returned an invalid step. No action was sent.')
    message = text(raw['message'], 'task step', 256)
    if raw['status'] != 'action':
        if raw['action'] is not None:
            raise ValidationError('A finished or blocked task cannot include another action.')
        return dict(raw, message=message)
    action = raw['action']
    if not isinstance(action, dict) or not isinstance(action.get('type'), str) or action['type'] not in TOOLS:
        raise ValidationError('That task action is not supported.')
    action = requested_gesture(action, goal)
    if action['type'] == 'launch_app' and isinstance(action.get('bundle_id'), str):
        navigation = browser_launch_as_navigation(action, goal, catalog)
        if navigation:
            action = navigation
    if action['type'] == 'launch_app' and isinstance(action.get('bundle_id'), str):
        from .desktop_context import BROWSER_IDS
        from .app_names import resolve_app
        resolved = [app['bundle_id'] for app in resolve_app(action['bundle_id'], catalog)] if catalog else []
        if action['bundle_id'] in BROWSER_IDS or (len(resolved) == 1 and resolved[0] in BROWSER_IDS):
            raise ValidationError('Do not launch the browser as a separate step. browse_url opens the browser itself: '
                                  'navigate directly to the requested site with browser_mode as requested.')
    if action['type'] == 'launch_app' and isinstance(action.get('bundle_id'), str) and catalog:
        if not any(app['bundle_id'] == action['bundle_id'] for app in catalog):
            from .app_names import resolve_app
            matches = resolve_app(action['bundle_id'], catalog)
            if len(matches) != 1:
                raise ValidationError('launch_app must name exactly one app from installed_apps.')
            action = dict(action, bundle_id=matches[0]['bundle_id'])
    if action['type'] == 'click_point' and catalog and isinstance(action.get('label'), str):
        # Clicking an app's name ("TextEdit", "Calendar", "Safari icon in dock") when that app is
        # not in front only ever meant opening it (live: two such clicks, then a stop).
        import re
        from .app_names import normalized
        name = normalized(re.sub(r'\b(?:app|application|icon|in (?:the )?dock|dock icon)\b', '', action['label'], flags=re.I))
        app = next((a for a in catalog if normalized(a['name']) == name), None)
        front = ((snapshot.get('app') or {}).get('bundle_id') if isinstance(snapshot, dict) else None)
        if app and app['bundle_id'] != front:
            running = {a.get('bundle_id') for a in (snapshot.get('available_apps') or [])} if isinstance(snapshot, dict) else set()
            action = {'type': 'focus_app' if app['bundle_id'] in running else 'launch_app', 'bundle_id': app['bundle_id']}
    if action['type'] == 'click_point' and snap:  # a numbered target's position is already exact
        action = snap_click(normalize_click(action), text_regions, snapshot.get('controls', []) if isinstance(snapshot, dict) else (), screen)
    validated = validate_response({'request_kind': 'answer', 'message': message, 'desktop_action': action,
        'target': None}, has_screen=True, desktop=desktop, desktop_snapshot=snapshot)
    action = validated['desktop_action']
    if finder_name_editor(snapshot) and (action['type'] == 'type_text' or action['type'] == 'press_key' and action.get('key') == 'enter'):
        raw = dict(raw, risk='change')  # changing/committing a filename always needs review
    if action['type'] == 'choose_menu' and FULL_SCREEN_MENU.search(action.get('path', '')) and not WINDOW_SIZE_WORDS.search(goal or ''):
        raise ValidationError('Leave the window as it is: full screen moves it to its own Space. Choose another way.')
    if goal_mode == 'inspect' and action['type'] == 'choose_menu' and MENU_CHANGES.search(action.get('path', '')):
        raise ValidationError('This goal only authorizes looking. That menu item changes something; choose another way or stop.')
    if goal_mode == 'inspect':
        labels = [action.get('label', '')]
        if action['type'] == 'click_point':
            labels.extend(region['text'] for region in text_regions if region['x'] <= action['x'] < region['x']+region['width']
                          and region['y'] <= action['y'] < region['y']+region['height'])
        if action['type'] == 'click_point' and screen:
            x, y = screen['x']+action['x']*screen['width'], screen['y']+action['y']*screen['height']
            for control in snapshot.get('controls', []):
                bounds = control.get('bounds') or {}
                if all(key in bounds for key in ('x', 'y', 'width', 'height')) and bounds['x'] <= x < bounds['x']+bounds['width'] and bounds['y'] <= y < bounds['y']+bounds['height']:
                    labels.append(control.get('label', ''))
        import re
        sensitive = (r'\b(post|send|reply|compose|delete|rename|remove|buy|pay|purchase|subscribe|install|download|upload|allow|permission|password|sign.?in|log.?in|'
                     r'submit|turn on|turn off|enable|disable|accept|block|unsubscribe|save|continue (?:as|with))\b|\bnew message\b')
        found = {match.group(0).casefold().replace(' ', '').replace('-', '') for label in labels if not DECLINE.match(label)
                 for match in re.finditer(sensitive, label, re.I)}
        # What the goal itself asks for ("sign me in") is proposed for the user's confirmation
        # (needs_review), not refused: refusing it left the user with no way forward (live).
        asked = {word for word in found if re.search(r'\b'+'.?'.join(map(re.escape, word.replace('signin', 'sign in').replace('login', 'log in').split()))+r'\b',
                                                     re.sub(r'\bsign (\w+ )?in\b', 'sign in', re.sub(r'\blog (\w+ )?in\b', 'log in', goal or '', flags=re.I), flags=re.I), re.I)}
        if action['type'] in ('click_point', 'press_control') and found and not found <= asked:
            raise ValidationError('This is an inspection-only goal. Do not open a composer, post/send, or change anything. Choose a reading/navigation target, or report a genuine blocker with action=null.')
        if raw['risk'] == 'change' and not (found and found <= asked) and not DECLINE.match(action.get('label', '')):
            raise ValidationError('This goal only authorizes inspection. Choose a navigation action or pause with action=null; do not propose a change.')
    return dict(raw, message=message, action=action)


def cancel_argument(method, cancel):
    """{'cancel': cancel} for a planner method that declares it (plan_task, classify_task_goal), else {}.
    Stop then ends the request itself instead of waiting for the reply."""
    import inspect
    try:
        return {'cancel': cancel} if 'cancel' in inspect.signature(method).parameters else {}
    except (TypeError, ValueError):
        return {}


def accepts_cancel(provider):
    import inspect
    try:
        parameters = inspect.signature(provider.complete_structured).parameters
    except (TypeError, ValueError):
        return False
    return 'cancel' in parameters or any(p.kind is p.VAR_KEYWORD for p in parameters.values())


class ComputerTask(CompletionChecks, AnswerWriting, Fallbacks):
    def __init__(self, goal, *, automatic=False, max_steps=24, goal_mode=None, opening=None, looking_at=None):
        self.goal = text(goal, 'computer task', 2000)
        # The app and window in front when the user asked, for a goal that names no app.
        self.looking_at = text(looking_at, 'task context', 300) if looking_at else ''
        self.automatic = bool(automatic)
        self.max_steps = max_steps
        self.receipts = []
        self.status = 'running'
        self.cancelled = threading.Event()
        self.pending = None
        self.last_proposal = None
        # Apps where the text-only pass already failed to produce an action. Live
        # logs showed it failing on every Teams step, doubling each step's cost.
        self.stale_retries = 0
        # The request interpreter can classify scope in the same call that routes
        # the goal; only tasks started without that plan need a separate call.
        self.timings = []
        self._catalog = None
        # An already-authorized first step (opening the app or site the user named),
        # executed without a model call. See ComputerTask.take_opening.
        self.opening = dict(opening) if isinstance(opening, dict) else None
        # "Open Calculator and add 5 + 5": the keys are pressed in code, by accessible name.
        from .arithmetic import keypad
        sequence = keypad(self.goal)
        self.keypad, self.keypad_expr = (sequence[0], sequence[1]) if sequence else (None, None)
        self.keypad_pressed = []  # labels of the keys proposed so far; each needs its receipt (see keypad_step)
        self.goal_mode = goal_mode if goal_mode in ('inspect', 'draft', 'change') else None
        self.view_opened = not NOTIFICATION_GOAL.search(self.goal)
        # "Click on Chat": one exactly labelled target is clicked in code (see click_step).
        self.click = click_goal(self.goal)
        self.clicked_text = None
        self.plan = None
        from . import routes
        self.worked_before = routes.recall(self.goal)  # how a similar goal was reached last time
        # Primitive steps a tool still has to do (type after clicking a field, Enter after typing,
        # scrolling until something shows); code runs them without asking the model again.
        self.queue = []
        self.screen_seen = None  # what the screen read when the latest step was chosen (see repeats)
        self.page_seen = ''  # the browser address when the latest step was chosen
        self.dead_targets = set()  # (screen, label) clicked with no effect (see check_last_click)
        self.planned = opening is not None  # a known opening needs no plan for where to start
        # "Click the first email": the nth visible row of the window's list (see row_step).
        self.row = row_goal(self.goal)
        self.row_label = None

    def app_names(self, desktop, snapshot):
        # Names only: a 150-app macOS catalog with bundle IDs is about 11k characters
        # of every step's prompt. Names are resolved to catalog IDs after inference.
        if self._catalog is None:
            catalog = snapshot.get('installed_apps') or []
            if not catalog:
                try:
                    catalog = desktop.installed_apps()
                except (RuntimeError, ValueError, OSError):
                    catalog = []
            self._catalog = [app for app in catalog if isinstance(app, dict) and app.get('name') and app.get('bundle_id')][:400]
        return list(dict.fromkeys(app['name'] for app in self._catalog))

    def stop(self, status='stopped'):
        self.status = status
        self.pending = None
        self.findings = []  # lines read from earlier screens (keep_findings) end with the task
        self.cancelled.set()

    def keypad_step(self, desktop, snapshot):
        """Next Calculator key as a validated press, the finished result, or None for the model."""
        app = (snapshot.get('app') or {}).get('name', '')
        if 'calc' not in app.casefold():
            return None  # Not in Calculator yet: let the model open or focus it.
        from .arithmetic import evaluate, find_key, formatted
        if not self.keypad:
            self.keypad = None
            # Every key this method proposed must have come back as a press Calculator accepted,
            # in order. A press refused before it ran leaves no receipt while its key is already
            # consumed; then the sum was not entered and the model looks at the display instead.
            # (Only key presses are lined up: switching back to Calculator between two keys is not one.)
            pressed = self.keypad_pressed
            presses = [receipt for receipt in self.receipts if receipt.get('type') == 'press_control'][-len(pressed):] if pressed else []
            if (not pressed or [receipt.get('label') for receipt in presses] != pressed
                    or any(receipt.get('status') == 'failed' for receipt in presses)):
                return None
            return {'status': 'done', 'action': None,
                    'message': 'Entered '+self.keypad_expr+' in Calculator. The answer is '+formatted(evaluate(self.keypad_expr))+'.'}
        controls = snapshot.get('controls') or []
        control = find_key(self.keypad[0], controls)
        if control is None and self.keypad[0] == 'clear':
            self.keypad.pop(0)
            control = find_key(self.keypad[0], controls) if self.keypad else None
        if control is None:
            self.keypad = None  # Unknown layout: the model takes over from here.
            return None
        step = validate_step({'status': 'action', 'message': 'Pressing '+control['label']+'.', 'risk': 'navigation',
                              'action': {'type': 'press_control', 'control_id': control['id']}},
                             desktop, snapshot, goal_mode=self.goal_mode)
        self.keypad.pop(0)
        action = dict(step['action'], sequence=True)
        self.keypad_pressed.append(action.get('label'))
        self.pending = action
        return {'status': 'action', 'message': step['message'], 'action': action, 'review': not self.automatic,
                'visual_capture': None}

    def view_step(self, desktop, snapshot, regions, visual, image_data, vision):
        """For notification goals, open the visible Activity/Notifications view in code before
        the model reads anything (it tended to report the current feed as the answer)."""
        if self.view_opened:
            return None
        if any((receipt.get('label') or '').casefold().strip() in NOTIFICATION_VIEWS for receipt in self.receipts):
            self.view_opened = True
            return None
        control = next((c for c in snapshot.get('controls') or [] if isinstance(c, dict)
                        and (c.get('label') or '').casefold().strip() in NOTIFICATION_VIEWS and c.get('id')), None)
        # Leftmost match: navigation rails sit at the window edge, page text with the same word does not.
        region = min((r for r in regions if isinstance(r, dict) and (r.get('text') or '').casefold().strip(' .') in NOTIFICATION_VIEWS
                      and all(isinstance(r.get(k), (int, float)) for k in ('x', 'y', 'width', 'height'))),
                     key=lambda r: r['x'], default=None)
        if control is None and region is None:
            return None  # Not visible (yet): the model navigates; this is retried next step.
        # view_opened is set once the click has a receipt (above): a click refused because the
        # screen changed is proposed again instead of leaving the model to find the view.
        name = (control or {}).get('label') or region['text'].strip(' .')
        action = ({'type': 'press_control', 'control_id': control['id']} if control else
                  {'type': 'click_point', 'x': region['x']+region['width']/2, 'y': region['y']+region['height']/2, 'label': name})
        step = validate_step({'status': 'action', 'message': 'Opening '+name+'.', 'risk': 'navigation', 'action': action},
                             desktop, snapshot, goal_mode=self.goal_mode, screen=visual.get('screen'), text_regions=regions)
        action = step['action']
        if action['type'] == 'click_point':
            action['capture_guard'] = vision.click_guard(image_data, visual, action)
        self.pending = action
        return {'status': 'action', 'message': step['message'], 'action': action,
                'review': not self.automatic, 'visual_capture': visual}

    def click_step(self, desktop, snapshot, regions, visual, image_data, vision, provider=None):
        """The click a click-only goal asks for, when exactly one place carries that label.

        The model is not needed to find "Chat" when the accessibility tree or OCR shows one
        "Chat" (live: five model calls and ~20 s for this click). Anything else is left to it."""
        if self.receipts:
            return None
        self.click = scoped_click_goal(self.goal,(snapshot.get('app') or {}).get('name',''))
        if self.click is None:
            return None
        want, button, count = self.click
        found = labelled_targets(want, regions, (snapshot.get('controls') or [])+(snapshot.get('targets') or []), visual.get('screen'))
        if len(found) != 1:
            self.click = None  # none or several: the model chooses, from now on
            return None
        x, y, control = found[0]
        if control is not None and control.get('id') and button == 'left' and count == 1:
            action = {'type': 'press_control', 'control_id': control['id']}
        else:
            action = {'type': 'click_point', 'x': round(x, 4), 'y': round(y, 4), 'label': ' '.join(want).capitalize()}
            if button != 'left' or count != 1:
                action.update(button=button, click_count=count)
        step = validate_step({'status': 'action', 'message': 'Clicking '+action.get('label', control and control.get('label') or '')+'.',
                              'risk': 'navigation', 'action': action},
                             desktop, snapshot, goal_mode=self.goal_mode, screen=visual.get('screen'), text_regions=())
        action = step['action']
        if (count==2 and control is not None and control.get('file_item')
                and (snapshot.get('finder') or {}).get('target')):
            self.file_step_context={'file_parent':snapshot['finder']['target'],'file_name':control['label']}
        if action['type'] == 'click_point':
            guard = action['capture_guard'] = vision.click_guard(image_data, visual, action)
        else:
            try:
                guard = vision.click_guard(image_data, visual, {'x': x, 'y': y})
            except ValueError:
                guard = {}  # a press needs no pixel guard; its result is judged by text alone
        # What the screen looked like: its text, and the pixels around the target (a checkbox can
        # change without any text changing).
        self.clicked_text = (sorted(region.get('text', '') for region in regions), (x, y), guard.get('region_hash'))
        review = not self.automatic or self.review_needed(action, 'navigation', snapshot) or bool(CONFIRMING.search(action.get('label', '')))
        if not review and provider is not None:
            # "Click Place order": the user named the button, and pressing it still commits something.
            kind = ROLE_NAMES.get(control.get('role')) if control is not None else self.press_kind(action, [], snapshot, desktop, visual)
            review = self.commits(action, kind, snapshot, provider, {'cancel': self.cancelled} if accepts_cancel(provider) else {})
            if self.cancelled.is_set():
                raise ValueError('Computer task stopped.')
        self.pending = action
        return {'status': 'action', 'message': step['message'], 'action': action, 'review': review, 'visual_capture': visual}

    def focused_click_step(self, desktop, snapshot, regions, visual, image_data, vision, provider):
        """Unresolved single pointer request: interpret scope, then locate, never plan actions."""
        if self.receipts or self.row is not None or getattr(self, 'focused_attempted', False):
            return None
        gesture = scoped_click_goal(self.goal, (snapshot.get('app') or {}).get('name', ''))
        if gesture is None or not callable(getattr(type(provider), 'prepare_single_click', None)):
            return None
        want, button, count = gesture
        # Never let a coordinate guess override a native/OCR match or an ambiguity.
        if labelled_targets(want, regions, (snapshot.get('controls') or [])+(snapshot.get('targets') or []), visual.get('screen')):
            return None
        self.focused_attempted = True
        dg, vg = desktop.generation, vision.generation
        def active():
            if self.cancelled.is_set() or not desktop.enabled or not vision.enabled or desktop.generation != dg or vision.generation != vg:
                raise ValueError('Computer task stopped or access was paused. No action was submitted.')
        from .provider import ProviderError
        from .ollama import RequestCancelled
        started = time.monotonic()
        try:
            active()
            target = provider.prepare_single_click(self.goal, cancel=self.cancelled)
            active()
            if not target:
                return None
            # Exact coverage prevents dropping a condition or subsequent instruction.
            literal = CLICK_GOAL.match(scoped_click_request(self.goal,(snapshot.get('app') or {}).get('name',''))).group('target')
            def normalized(value):
                import unicodedata
                value = ' '.join(unicodedata.normalize('NFC',value).casefold().strip(' \"“”').split())
                return re.sub(r'^(?:the|a|an)\s+','',value)
            if normalized(literal) != normalized(target):
                return None
            scale = getattr(type(vision), 'model_image', None)
            image = scale(image_data) if callable(scale) else image_data
            point = provider.localize_target(target, image, cancel=self.cancelled)
            active()
            action = dict(type='click_point', label=target, button=button, click_count=count, **point)
            # Keep a localizer scoped to the observed front window; it cannot choose an app.
            box, screen = (snapshot.get('window') or {}).get('bounds') or {}, visual.get('screen') or {}
            if not all(k in box and k in screen for k in ('x','y','width','height')):
                return None
            if not (box['x'] <= screen['x']+point['x']*screen['width'] < box['x']+box['width']
                    and box['y'] <= screen['y']+point['y']*screen['height'] < box['y']+box['height']):
                return None
            bridge = getattr(desktop, 'bridge', None)
            if hasattr(bridge, 'hit'):
                try:
                    hit = bridge.hit(screen['x']+point['x']*screen['width'], screen['y']+point['y']*screen['height'])
                except (RuntimeError, OSError):
                    return None  # unverified point: the full policy decides instead
                if (isinstance(hit, dict) and (hit.get('role') in ROLE_NAMES or hit.get('finder_item'))
                        and hit.get('title') and label_words(hit['title']) != label_words(target)):
                    return None
            step = validate_step({'status':'action','message':'Clicking '+target+'.','risk':'navigation','action':action},
                                 desktop, snapshot, goal_mode=self.goal_mode or 'inspect', screen=screen,
                                 text_regions=regions, snap=False, goal=self.goal)
            step = self.app_click(step, desktop, snapshot, visual)
            if step['action']['type'] != 'click_point':
                return None
            active()
            action = step['action']
            guard = action['capture_guard'] = vision.click_guard(image_data, visual, action)
            active()
            self.click = gesture
            self.clicked_text = (sorted(r.get('text','') for r in regions), (point['x'],point['y']), guard.get('region_hash'))
            self.pending = action
            return {'status':'action','message':step['message'],'action':action,'visual_capture':visual,
                    'review':not self.automatic or self.review_needed(action,step.get('risk','navigation'),snapshot,navigation_verified=step.get('settings_navigation',False)) or bool(CONFIRMING.search(target))}
        except RequestCancelled:
            raise
        except (ProviderError, ValidationError, KeyError, TypeError):
            active()
            return None  # existing full policy still handles unsupported/uncertain cases
        finally:
            timing('task.focused_grounding', started)

    def row_step(self, desktop, snapshot, visual, image_data, vision):
        """The nth row of the window's list for "click the first email": the accessibility tree
        says where rows are; the model clicked the window title instead (live, Mail)."""
        if self.row is None or any(receipt.get('type') not in OPENING_ACTIONS for receipt in self.receipts):
            return None
        position, noun, button, count, app = self.row
        if app:
            from .app_names import normalized
            front = normalized((snapshot.get('app') or {}).get('name', ''))
            if not front or normalized(app) not in front and front not in normalized(app):
                return None  # the named app is not in front yet: the model opens it first
        point = list_row(snapshot.get('lists'), position, (snapshot.get('window') or {}).get('bounds'), visual.get('screen'), noun)
        if point is None:
            self.row = None
            return None
        self.click = None  # the row is the target; no label search
        label = next(word for word, value in ORDINALS.items() if value == position).capitalize()+' '+noun
        action = {'type': 'click_point', 'x': round(point[0], 4), 'y': round(point[1], 4), 'label': label}
        # The ordinal is grounded in the observed list geometry. Bind a Finder
        # row to its current native name too, so the execution hit guard can
        # reject a changed item without mistaking 'first file' for a filename.
        if hasattr(desktop.bridge,'hit'):
            try:
                screen=visual['screen']
                hit=desktop.bridge.hit(screen['x']+point[0]*screen['width'],screen['y']+point[1]*screen['height'])
                if isinstance(hit,dict) and hit.get('finder_item') and hit.get('title'):
                    action['label']=hit['title']
            except (RuntimeError,ValueError,OSError,KeyError,TypeError):
                pass
        if button != 'left' or count != 1:
            action.update(button=button, click_count=count)
        step = validate_step({'status': 'action', 'message': 'Clicking the '+label.lower()+'.', 'risk': 'navigation', 'action': action},
                             desktop, snapshot, goal_mode=self.goal_mode, screen=visual.get('screen'), text_regions=())
        action = step['action']
        action['capture_guard'] = vision.click_guard(image_data, visual, action)
        self.row_label = label
        self.pending = action
        return {'status': 'action', 'message': step['message'], 'action': action,
                'review': not self.automatic or self.review_needed(action, 'navigation', snapshot), 'visual_capture': visual}

    def worked(self):
        """Something beyond opening ran: a click, key, typing or menu command."""
        return any(receipt.get('type') not in OPENING_ACTIONS for receipt in self.receipts)


    def click_result(self, regions, image_data=None, visual=None, vision=None):
        """After a code click: done when the window changed, else say so. None otherwise."""
        if self.row_label is not None:
            if self.receipts and (self.receipts[-1].get('label') or '') == self.row_label:
                # Selecting a row is the whole goal, even when it was already selected.
                return {'status': 'done', 'action': None, 'message': 'Clicked the '+self.row_label.lower()+'.'}
            self.row_label = None  # refused before it ran: row_step proposes it again
            return None
        if self.clicked_text is None:
            return None
        if not self.receipts:
            # The click was refused before it ran (the screen changed): look again and retry.
            self.clicked_text = None
            return None
        label = self.receipts[-1].get('label') or 'it'
        text, point, region_hash = self.clicked_text
        try:
            pixels_same = vision is None or image_data is None or vision.click_guard(image_data, visual, {'x': point[0], 'y': point[1]}).get('region_hash') == region_hash
        except (ValueError, KeyError, TypeError):
            pixels_same = True
        if sorted(region.get('text', '') for region in regions) == text and pixels_same:
            return {'status': 'needs_user', 'action': None, 'message': 'I clicked '+label+', but nothing on the screen changed.'}
        return {'status': 'done', 'action': None, 'message': 'Clicked '+label+'.'}

    def nth_item_answer(self, regions):
        """The title on the screen's line numbered n ("5. Some title (example.com)"), or None."""
        match = NTH_ITEM.search(self.goal)
        if not match or len(regions) < 5:
            return None
        word = match.group('n').casefold()
        n = WORD_NUMBERS.get(word) or int(re.sub(r'\D', '', word))
        ordered = sorted(regions, key=lambda r: (round(r.get('y', 0), 3), r.get('x', 0)))
        title = None
        for index, region in enumerate(ordered):
            text = str(region.get('text', '')).strip()
            inline = re.match(r'^%d[.)]\s+(.{3,})$' % n, text)
            if inline:
                title = inline.group(1)
                break
            if re.fullmatch(r'%d[.)]?' % n, text):
                # The number in its own box: the title is the nearest text to its right on that line.
                beside = [r for r in ordered if r is not region and abs(r.get('y', 0)-region.get('y', 0)) < 0.012 and r.get('x', 0) > region.get('x', 0)]
                if beside:
                    title = str(min(beside, key=lambda r: r.get('x', 0)).get('text', '')).strip()
                    break
        if not title:
            return None
        title = re.sub(r'\s*\((?:[\w-]+\.)+[a-z]{2,}(?:/\S*)?\)\s*$', '', title)  # "(example.com)"
        return {'status': 'done', 'action': None, 'message': 'The %s %s is “%s”.' % (word if word in WORD_NUMBERS else ordinal(n),
                                                                                      match.group(0).split()[-1].casefold(), title)}

    def view_answer(self, provider, snapshot, regions, cancel_kwargs, desktop, vision, desktop_generation, vision_generation):
        """"Any new notifications?" once code opened the Activity view: the answer is that view's
        text. More model clicking there wandered between Activity and the bell (live)."""
        last = self.receipts[-1] if self.receipts else {}
        if (NOTIFICATION_GOAL.search(self.goal) is None or len(regions) < 3 or self.goal_mode not in (None, 'inspect')
                or (last.get('label') or '').casefold().strip() not in NOTIFICATION_VIEWS):
            return None
        def check_active():
            if self.cancelled.is_set() or not desktop.enabled or not vision.enabled or desktop.generation != desktop_generation or vision.generation != vision_generation:
                raise ValueError('Computer task stopped or access was paused. No next action was submitted.')
        # A reading goal: the reply is written from this view's own text, so no second
        # model call judges it (a verifier here made it two calls and could refuse a view
        # that had opened).
        answer = self.screen_answer(provider, snapshot, regions, '', cancel_kwargs)
        check_active()
        return {'status': 'done', 'message': answer, 'action': None} if answer else None

    def finder_sort_step(self, desktop, snapshot):
        """Finder in front, the folder the goal names open (Go menu), then View > Sort By > column."""
        match = FINDER_SORT.search(self.goal)
        if not match or not re.search(r'\b(?:finder|folder|downloads|documents|desktop)\b', self.goal, re.I):
            return None
        column = 'Date Added' if match.group('newest') else SORT_TITLES[match.group('column').casefold()]
        if self.receipts and self.receipts[-1].get('type') == 'choose_menu' and 'Sort By' in str(self.receipts[-1].get('label', '')):
            return {'status': 'done', 'action': None, 'message': 'Sorted by '+column.lower()+'.'}
        folder = next((name for name in ('Downloads', 'Documents', 'Desktop') if name.casefold() in self.goal.casefold()), '')
        front = snapshot.get('app') or {}
        window = ((snapshot.get('window') or {}).get('title') or '')
        if front.get('bundle_id') != 'com.apple.finder':
            proposal = {'type': 'focus_app', 'bundle_id': 'com.apple.finder'}
        elif folder and folder.casefold() not in window.casefold():
            proposal = {'type': 'choose_menu', 'path': 'Go > '+folder}
        else:
            # Finder has no "Sort By" while it groups items; then grouping by the same column orders them.
            proposal = {'type': 'choose_menu', 'path': 'View > Sort By > '+column+' | View > Group By > '+column}
        try:
            action = validate_step({'status': 'action', 'message': 'Sorting.', 'risk': 'navigation', 'action': proposal},
                                   desktop, snapshot)['action']
        except ValidationError:
            return None
        self.pending = action
        return {'status': 'action', 'message': action.get('label') or 'Sorting.', 'action': action,
                'review': not self.automatic, 'visual_capture': None}

    def check_last_click(self, snapshot=None, image_data=None):
        """Did the last click change anything? If the screen reads the same as when it was chosen, it
        did nothing: the model is told so, and that target is left out while the screen stays the same,
        so the next step tries something else instead of the same thing again.

        A checkbox, switch, tab or option is judged by itself, not by the words around it: ticking
        "Remind me about due dates" changed no text, was reported as "nothing changed", and the next
        click unticked it (sim, form). Its own rectangle looking different is the change."""
        last = self.receipts[-1] if self.receipts else None
        if (not last or last.get('type') not in ('click_point', 'press_control') or 'effect' in last
                or last.get('screen') is None or self.screen_seen is None):
            return
        unchanged = last['screen'] == self.screen_seen
        look = last.get('look')
        if look and 'state_changed' not in last and image_data:
            now = region_look(image_data, look['rect'])
            # With other words on screen too (a switch that also reads "On"), the rectangle is the same
            # control only while a target of that name is still there.
            label = ' '.join(str(last.get('label', '')).casefold().split())
            still = unchanged or any(' '.join(str(t.get('label', '')).casefold().split()) == label
                                     for key in ('targets', 'controls') for t in (snapshot or {}).get(key) or [] if isinstance(t, dict))
            last['state_changed'] = bool(now) and now != look['look'] and still
            if last['state_changed'] and unchanged:
                last['effect'] = 'its own state changed (a tick or switch adds no text): clicking it again would undo that'
                return
        if unchanged and (snapshot or {}).get('focused_role') in ('AXTextField', 'AXSearchField', 'AXTextArea', 'AXComboBox'):
            # Clicking into a field changes nothing visible but worked: say so, so the next step types
            # (the Notes search field was clicked twice and the task stopped, live).
            last['effect'] = 'the text field is focused: type into it now'
            return
        if not unchanged:
            return  # said only when nothing changed: "the screen changed" after every click kept the model clicking (A/B, live)
        last['effect'] = 'nothing changed on screen'
        if last.get('label'):
            self.dead_targets.add((self.screen_seen, str(last['label']).casefold().strip()))

    def check_last_scroll(self, snapshot, regions):
        """Return a stalled scroll tool to the model with actual viewport feedback."""
        self.scroll_seen = scroll_view_state(snapshot, regions)
        last = self.receipts[-1] if self.receipts else None
        if (not last or last.get('type') != 'scroll' or 'scroll_progress' in last
                or not last.get('scroll_view') or not self.scroll_seen):
            return
        changed = last['scroll_view'] != self.scroll_seen
        last['scroll_progress'] = 'changed' if changed else 'unchanged'
        last['effect'] = ('Visible content or its position changed; task completion still needs evidence.' if changed else
                          'No visible scroll progress: the observed viewport stayed the same.')
        recent = self.receipts[-2:]
        if (len(recent) == 2 and all(r.get('type') == 'scroll' and r.get('scroll_progress') == 'unchanged'
                                    and r.get('scroll_view') == self.scroll_seen for r in recent)
                and self.queue and 'scroll_to' in self.queue[0]):
            self.queue = []
            last['effect'] += ' The queued scroll was stopped; choose another way to locate the target.'

    def live_marks(self, marks):
        """Numbered targets without the ones already clicked to no effect on this same screen."""
        dead = {label for screen, label in self.dead_targets if screen == self.screen_seen}
        return [m for m in marks if str(m.get('label', '')).casefold().strip() not in dead] if dead else marks

    def app_click(self, step, desktop, snapshot, visual):
        """A click that would land on an app's Dock icon or its name in the menu bar: switch to that app
        (a click there does the same, unconfirmed), or, when it is already in front, refuse with why.
        macOS says what is under the point, so this holds for every app, whatever the model calls it.
        It clicked "Safari|0.048,0.016", Safari's menu-bar name, over and over (live)."""
        action = step.get('action') or {}
        bridge = getattr(desktop, 'bridge', None)
        screen = visual.get('screen') or {}
        if step.get('status') != 'action' or action.get('type') != 'click_point' or not hasattr(bridge, 'hit') or not screen.get('width'):
            return step
        try:
            hit = bridge.hit(screen['x']+action['x']*screen['width'], screen['y']+action['y']*screen['height'])
        except (RuntimeError, ValueError, OSError, KeyError, TypeError):
            return step
        if not isinstance(hit, dict):
            return step
        self.point_hit = (action['x'], action['y'], hit)  # reused by press_kind: one native lookup per click
        # System Settings content controls can change preferences even when the
        # model labels a click as navigation. Only native sidebar/toolbar ancestry
        # establishes a plain navigation click; other content needs confirmation.
        if hit.get('owner') == 'com.apple.systempreferences' and hit.get('role') not in ('AXMenuItem', 'AXMenuBarItem'):
            if {'AXOutline', 'AXToolbar'} & set(hit.get('ancestor_roles') or []):
                step = dict(step, settings_navigation=True)
            else:
                step = dict(step, risk='change')
        from .vision import ScreenVision
        if ScreenVision.named_hit_conflict(action, hit):
            if hit.get('role') == 'AXLink':
                raise ValidationError('That point is on the observed link '+json.dumps(str(hit.get('title', ''))[:100])+
                                      ', not the requested '+json.dumps(str(action.get('label', ''))[:100])+'. '
                                      'Choose its actual listed target, or reveal the intended link before clicking.')
            raise ValidationError('That point is a different Finder item or menu command. Use its actual visible name; '
                                  'scroll or navigate until the requested item is visible instead of guessing its position.')
        last = self.receipts[-1] if self.receipts else {}
        if (hit.get('finder_item') and action.get('button', 'left') == 'left'
                and action.get('click_count', 1) == 1 and last.get('type') == 'click_point'
                and last.get('button', 'left') == 'left' and last.get('click_count', 1) == 1
                and str(last.get('label', '')).casefold() == str(action.get('label', '')).casefold()):
            raise ValidationError('That file or folder was just selected. Another slow single click can rename it. '
                                  'Use the observed open_target or a double-click to open it; '
                                  'if selection was the goal, verify the current selection instead.')
        window_button = {'AXFullScreenButton': 'full-screen', 'AXZoomButton': 'full-screen', 'AXMinimizeButton': 'minimise'}.get(hit.get('subrole'))
        skipped = [r for r in self.receipts[-3:] if r.get('type') == 'wait' and 'skipped' in str(r.get('label', ''))]
        if (window_button and not WINDOW_SIZE_WORDS.search(self.goal) or hit.get('app') == (snapshot.get('app') or {}).get('bundle_id')) and skipped:
            # Skipped once already: the model kept clicking Finder's menu-bar name and every click became a
            # wait, 24 steps of them (live). Now it has to choose something else.
            raise ValidationError('That point is the app’s own name, icon or window button again; it does nothing for this goal. '
                                  'Use a numbered target inside the window, a tool (search_in_app, type_into), or report the result.')
        if window_button and not WINDOW_SIZE_WORDS.search(self.goal):
            # The green button sent windows full screen, into their own Space, and nothing lined up after
            # (owner, live). A window's size changes only when the goal asks for it.
            self.dead_targets.add((self.screen_seen, str(action.get('label', '')).casefold().strip()))
            return dict(step, action={'type': 'wait', 'milliseconds': 500, 'label': 'the window’s '+window_button+' button skipped'},
                        message='Left the window as it is.')
        app = hit.get('app')
        if not app:
            return step
        name = hit.get('title') or hit.get('owner_name') or 'that app'
        where = 'name in the menu bar' if hit.get('app_name_item') else 'Dock icon'
        if app == (snapshot.get('app') or {}).get('bundle_id'):
            # Already in front: nothing to click there. A short wait instead, never a refusal that ends the
            # task (that ended tasks at step one, live); the model is told why and the target is dropped.
            self.dead_targets.add((self.screen_seen, str(action.get('label', '')).casefold().strip()))
            return dict(step, action={'type': 'wait', 'milliseconds': 500, 'label': name+' is already in front; '+where+' skipped'},
                        message=name+' is already in front.')
        running = {a.get('bundle_id') for a in snapshot.get('available_apps') or []}
        switched = validate_step({'status': 'action', 'message': 'Switching to '+name+'.', 'risk': 'navigation',
                                  'action': {'type': 'focus_app' if app in running else 'launch_app', 'bundle_id': app}},
                                 desktop, snapshot, catalog=self._catalog or (), goal=self.goal)
        return dict(step, action=switched['action'], message=switched.get('message', step.get('message')))


    def scroll_for_unseen(self, step, desktop, snapshot, regions, visual):
        """A click at a guessed point, labelled with a name from the goal that nothing on screen carries:
        the thing is out of view, so scroll until its name shows instead of clicking where it is not.

        "Open the Zelcroft project folder" in a long list: the model clicked the window's title bar,
        the click did nothing, and the menu fallback chose Go > Enclosing Folder (sim). Only for a name
        the user gave (a description of an icon is the model's own wording), once per name, and only
        when macOS says what is under the point and it is not the thing: nothing pressable, or something
        with another name. Text recognition can miss a name that is there, so a missing name alone (or
        a platform that cannot say what is under a point) leaves the click as the model chose it."""
        action = step.get('action') or {}
        label = str(action.get('label', ''))
        want = label_words(label)
        tried = getattr(self, 'scrolled_for', set())
        bridge, screen = getattr(desktop, 'bridge', None), visual.get('screen') or {}
        if (step.get('status') != 'action' or action.get('type') != 'click_point' or action.get('button', 'left') != 'left'
                or len(''.join(want)) < 3 or want in tried or self.queue or not (regions or snapshot.get('targets'))
                or named_on_screen(label, regions, snapshot) or not hasattr(bridge, 'hit') or not screen.get('width')
                or not set(want) <= set(re.findall(r"[\w']+", self.goal.casefold()))):
            return step
        try:
            hit = bridge.hit(screen['x']+action['x']*screen['width'], screen['y']+action['y']*screen['height'])
        except (RuntimeError, ValueError, OSError, KeyError, TypeError):
            return step
        if not isinstance(hit, dict):
            return step
        title = label_words(str(hit.get('title') or ''))
        if title and set(want) <= set(title) or not title and (hit.get('role') in ROLE_NAMES or hit.get('finder_item') or hit.get('app')):
            return step  # the named thing itself, or an unnamed control that may be the icon meant
        try:
            scroll = validate_step({'status': 'action', 'message': 'Scrolling to find '+label[:80]+'.', 'risk': 'navigation',
                                    'action': {'type': 'scroll', 'direction': 'down'}}, desktop, snapshot, goal_mode=self.goal_mode)
        except (ValueError, ValidationError):
            return step
        self.scrolled_for = tried | {want}
        self.next_queue = [{'scroll_to': label[:120], 'left': 6}]
        return dict(step, action=scroll['action'], message=scroll['message'], risk='navigation')

    def section_step(self, desktop, snapshot):
        """"The first sentence of its History section": the page's own anchor for that heading (#History),
        one navigation in the current tab. The model scrolled nine times past it (live, v3); scrolling
        until "History" shows stops at the contents list. A page without the anchor stays as it is."""
        match = SECTION.search(self.goal)
        app = snapshot.get('app') or {}
        from .desktop_context import BROWSER_IDS
        if getattr(self, 'section_done', False) == 'shown':
            self.section_done = True
            if self.receipts and self.receipts[-1].get('type') == 'browse_url' and self.goal_mode == 'inspect' and ASKS_FOR_FACT.search(self.goal):
                # The section is on screen: read it. The model clicked "History of coffee" (another article) instead (live, v3).
                return {'status': 'done', 'message': 'Read from the screen.', 'action': None}
            return None
        if not match or getattr(self, 'section_done', False) or app.get('bundle_id') not in BROWSER_IDS:
            return None
        name = match.group('name').strip()
        if name.casefold() in ('first', 'top', 'last', 'next', 'main', 'comments', 'same', 'this', 'that'):
            return None
        from . import mac_apps
        try:
            _, url, _ = mac_apps.read_page(app['bundle_id'])
        except (mac_apps.AppError, RuntimeError, OSError, ValueError):
            return None
        from urllib.parse import quote, urlsplit
        parts = urlsplit(url)
        if parts.scheme not in ('http', 'https') or parts.query:
            return None  # still a search page: wait for the article
        self.section_done = True
        anchor = quote((name[0].upper()+name[1:]).replace(' ', '_'), safe="_-.()'")
        address = url.split('#', 1)[0]+'#'+anchor
        try:
            action = validate_step({'status': 'action', 'message': 'Going to the section.', 'risk': 'navigation',
                                    'action': {'type': 'browse_url', 'bundle_id': app['bundle_id'], 'url': address,
                                               'label': 'Go to the '+name+' section', 'browser_mode': 'current'}},
                                   desktop, snapshot, catalog=self._catalog or (), goal=self.goal)['action']
        except (ValueError, ValidationError):
            return None
        self.pending = action
        self.section_done = 'shown'
        return {'status': 'action', 'message': action.get('label', 'Going to the section')+'…', 'action': action,
                'review': not self.automatic, 'visual_capture': None}

    def address_typed(self, step, desktop, snapshot):
        """The model typing an address into the browser (⌘L, type, Enter: it fumbled this in every run of
        Open-Meteo and Open Library, review 2026-10-25) becomes one navigation in the current tab, done in code."""
        action = step.get('action') or {}
        app = (snapshot.get('app') or {}).get('bundle_id')
        from .desktop_context import BROWSER_IDS
        if step.get('status') != 'action' or action.get('type') != 'type_text' or app not in BROWSER_IDS:
            return step
        text = str(action.get('text', '')).strip()
        if not ADDRESS.fullmatch(text):
            return step
        address = text if '://' in text else 'https://'+text
        try:
            converted = validate_step({'status': 'action', 'message': 'Opening '+address+'.', 'risk': 'navigation',
                                       'action': {'type': 'browse_url', 'bundle_id': app, 'url': address,
                                                  'label': 'Open '+address.split('://', 1)[1], 'browser_mode': 'current'}},
                                      desktop, snapshot, catalog=self._catalog or (), goal=self.goal)
        except (ValueError, ValidationError):
            return step
        self.next_queue = []  # its Enter is not needed
        return dict(step, action=converted['action'], message=converted.get('message', step.get('message')))

    def navigation_check(self, desktop, snapshot):
        """Did the last navigation happen? An app opened or switched to must be in front; an address must
        be the browser's page. If not, it is tried once more; after that the model is told (review 2026-10-25:
        navigation was submitted and never observed)."""
        if not self.receipts or getattr(self, 'navigation_retried', None) is self.receipts[-1]:
            return None
        last = self.receipts[-1]
        wanted = last.get('wanted') or {}
        kind = last.get('type')
        front = (snapshot.get('app') or {}).get('bundle_id')
        happened = True
        if kind in ('launch_app', 'focus_app', 'new_window') and wanted.get('bundle_id'):
            happened = front == wanted['bundle_id']
        elif kind == 'browse_url' and wanted.get('url') and '?' not in wanted['url'] and front == wanted.get('bundle_id'):
            # (a search or "first result" address redirects elsewhere by design: not checked)
            # 'arrived' is set only when an address was compared and is on the site: an unreadable
            # address proves nothing (see receipt_step). The observation's own address can confirm it,
            # and is the only source for Windows browsers and Firefox, whose page read below is
            # unavailable. It never starts a retry: that stays with the page read, as before.
            if on_site((snapshot.get('window') or {}).get('url') or '', wanted['url']):
                last['arrived'] = True
            else:
                from . import mac_apps
                try:
                    _, url, _ = mac_apps.read_page(front)
                except (mac_apps.AppError, RuntimeError, OSError, ValueError):
                    return None
                happened = not url or on_site(url, wanted['url'])
                if url and happened:
                    last['arrived'] = True
        if happened:
            return None
        self.navigation_retried = last
        if last.get('effect'):
            return None
        last['effect'] = 'it did not happen: '+('the app is not in front' if kind != 'browse_url' else 'the page is elsewhere')
        retry = {'type': kind, 'bundle_id': wanted['bundle_id']}
        if kind == 'browse_url':
            retry.update(url=wanted['url'], label='Open '+wanted['url'].split('://', 1)[-1], browser_mode='current')
        try:
            action = validate_step({'status': 'action', 'message': 'Trying that again.', 'risk': 'navigation', 'action': retry},
                                   desktop, snapshot, catalog=self._catalog or (), goal=self.goal)['action']
        except (ValueError, ValidationError):
            return None
        self.pending = action
        return {'status': 'action', 'message': 'Trying again: '+action.get('label', '')+'…', 'action': action,
                'review': not self.automatic, 'visual_capture': None}

    def new_document_step(self, desktop, snapshot):
        """"In a new TextEdit document": after launching, a new document. TextEdit reopened last time's
        document on launch and the population went into it (live, v3)."""
        bundle = getattr(self, 'new_after_launch', None)
        if not bundle or (snapshot.get('app') or {}).get('bundle_id') != bundle:
            return None
        self.new_after_launch = None
        try:
            action = validate_step({'status': 'action', 'message': 'A new document.', 'risk': 'navigation',
                                    'action': {'type': 'new_window', 'bundle_id': bundle}}, desktop, snapshot,
                                   catalog=self._catalog or (), goal=self.goal)['action']
        except (ValueError, ValidationError):
            return None
        self.pending = action
        return {'status': 'action', 'message': action.get('label', 'A new document')+'…', 'action': action, 'review': not self.automatic, 'visual_capture': None}

    def sites_step(self, desktop, snapshot):
        """"Open apple.com and duckduckgo.com in two tabs": each named site opens in code, one after
        the other (an open tab of the site is reused), then done when opening was all it asked. The
        model clicked a Favourites icon ten times instead of opening the second site (live, v2)."""
        from .web_apps import named_addresses
        addresses = named_addresses(self.goal)
        if len(addresses) < 2:
            return None
        opened = getattr(self, 'sites_opened', [])
        waiting = [address for address in addresses if address not in opened]
        if not waiting:
            from .web_apps import site_mentions
            plain = self.goal
            for start, end, _ in reversed(site_mentions(self.goal)):
                plain = plain[:start]+'SITE'+plain[end:]
            if ONLY_OPENING.match(plain):
                return {'status': 'done', 'action': None, 'message': 'Opened them in separate tabs.'}
            return None
        from .desktop_context import BROWSER_IDS, default_browser_id
        from .web_apps import browser_named
        front = (snapshot.get('app') or {}).get('bundle_id')
        wanted = browser_named(self.goal) or ''
        browser = next((a for a in self._catalog or [] if wanted and a['name'] == wanted), None) or \
            next((a for a in self._catalog or [] if a['bundle_id'] == (front if front in BROWSER_IDS else default_browser_id())), None) or \
            next((a for a in self._catalog or [] if a['bundle_id'] in BROWSER_IDS), None)
        if browser is None:
            return None
        try:
            action = validate_step({'status': 'action', 'message': 'Opening.', 'risk': 'navigation',
                                    'action': {'type': 'browse_url', 'bundle_id': browser['bundle_id'], 'url': waiting[0],
                                               'label': 'Open '+waiting[0].split('://', 1)[-1]+' in '+browser['name'], 'browser_mode': 'default'}},
                                   desktop, snapshot, catalog=self._catalog or (), goal=self.goal)['action']
        except (ValueError, ValidationError):
            return None
        self.sites_opened = opened+[waiting[0]]
        self.pending = action
        return {'status': 'action', 'message': action.get('label', 'Opening')+'…', 'action': action, 'review': not self.automatic, 'visual_capture': None}

    def tab_step(self, desktop, snapshot):
        """"Switch to the Wikipedia tab": open the site, which brings its open tab forward (the bridge
        reuses it), then done. The model clicked Back twice after the tab was already in front (live, v3)."""
        match = SWITCH_TAB.match(self.goal)
        if not match:
            return None
        from .web_apps import find_site
        site = find_site(match.group('site'))
        if not site:
            return None
        if any(receipt.get('type') == 'browse_url' for receipt in self.receipts):
            return {'status': 'done', 'action': None, 'message': 'Switched to the '+match.group('site').strip()+' tab.'}
        from .desktop_context import BROWSER_IDS, default_browser_id
        front = (snapshot.get('app') or {}).get('bundle_id')
        wanted = front if front in BROWSER_IDS else default_browser_id()  # the browser in front, else the default one
        browser = next((a for a in self._catalog or [] if a['bundle_id'] == wanted), None) or \
            next((a for a in self._catalog or [] if a['bundle_id'] in BROWSER_IDS), None)
        if browser is None:
            return None
        try:
            action = validate_step({'status': 'action', 'message': 'Switching tabs.', 'risk': 'navigation',
                                    'action': {'type': 'browse_url', 'bundle_id': browser['bundle_id'], 'url': site[1],
                                               'label': 'Switch to '+site[1], 'browser_mode': 'default'}}, desktop, snapshot,
                                   catalog=self._catalog or (), goal=self.goal)['action']
        except (ValueError, ValidationError):
            return None
        self.pending = action
        return {'status': 'action', 'message': 'Switching to the tab.', 'action': action, 'review': not self.automatic, 'visual_capture': None}

    def history_step(self, desktop, snapshot):
        """"Go back in Safari" is one press of ⌘[ in the browser, then done. The model went back
        13 times in a row instead (v3 suite, live)."""
        match = HISTORY_GOAL.match(self.goal)
        if not match:
            return None
        wanted = {'one': 1, 'a': 1, 'two': 2, 'three': 3}.get((match.group('count') or '1').casefold(), None) or int(match.group('count') or 1)
        done = 0
        for receipt in reversed(self.receipts):
            if not self.is_history(receipt):
                break
            done += 1
        way = match.group('way').casefold()
        if done >= wanted:
            return {'status': 'done', 'action': None, 'message': 'Went %s%s.' % (way, ' %d pages' % wanted if wanted > 1 else '')}
        from .desktop_context import BROWSER_IDS
        front = snapshot.get('app') or {}
        named = (match.group('app') or '').casefold()
        browser = 'Google Chrome' if 'chrome' in named else 'Safari'
        try:
            if front.get('bundle_id') not in BROWSER_IDS:
                action = validate_step({'status': 'action', 'message': 'Switching to '+browser+'.', 'risk': 'navigation',
                                        'action': {'type': 'focus_app', 'app_name': browser}}, desktop, snapshot)['action']
            else:
                action = validate_step({'status': 'action', 'message': 'Going '+way+'.', 'risk': 'navigation',
                                        'action': {'type': 'press_key', 'key': 'cmd_[' if way == 'back' else 'cmd_]'}}, desktop, snapshot)['action']
        except ValidationError:
            return None
        self.pending = action
        return {'status': 'action', 'message': action.get('label') or 'Going '+way+'.', 'action': action,
                'review': not self.automatic, 'visual_capture': None}


    def menu_step(self, desktop, snapshot):
        """"Show me the View menu in Finder": choose_menu in code once that app is in front (the
        model clicked "icon view" twice instead, live). Done after it opened."""
        match = MENU_GOAL.match(self.goal)
        if not match:
            return None
        menu = match.group('menu') or match.group('menu2')
        app = (match.group('app') or match.group('app2') or '').strip()
        front = (snapshot.get('app') or {}).get('name', '')
        if self.receipts and self.receipts[-1].get('type') == 'choose_menu':
            return {'status': 'done', 'action': None, 'message': 'The '+menu+' menu is open.'}
        if app and app.casefold() not in front.casefold():
            return None  # the planner or model brings the app forward first
        menus = [m.casefold() for m in snapshot.get('menus') or []]
        if menu.casefold() not in menus:
            return None
        try:
            action = validate_step({'status': 'action', 'message': 'Opening the '+menu+' menu.', 'risk': 'navigation',
                                    'action': {'type': 'choose_menu', 'path': menu}}, desktop, snapshot)['action']
        except ValidationError:
            return None
        self.pending = action
        return {'status': 'action', 'message': 'Opening the '+menu+' menu.', 'action': action, 'review': not self.automatic, 'visual_capture': None}

    def receipt_step(self, desktop, snapshot):
        """The code steps that report done from their own receipts, as (step, observed).

        A submitted action is not its result. For a step that says done, observed is
        True   when native state shows the result: the browser is on each address this task opened
               (navigation_check compared them), the page address changed with every back/forward
               press, or Finder's list is sorted by the asked column;
        a str  when native state shows it did not happen (what it shows, in words). Nothing may
               overrule that, the verifier included;
        None   when native state cannot say (an opened menu, an unreadable address, icon view or
               grouping): the caller (propose) checks the screen with the verifier."""
        last = self.receipts[-1] if self.receipts else {}
        def judged(step, observed):
            if step['status'] == 'done' and last.get('status') == 'failed':
                return step, 'the last step did not go through'
            return step, observed
        step = self.sites_step(desktop, snapshot)
        if step is not None:
            from .web_apps import named_addresses
            arrived = {(receipt.get('wanted') or {}).get('url') for receipt in self.receipts
                       if receipt.get('type') == 'browse_url' and receipt.get('arrived') is True}
            return judged(step, all(address in arrived for address in named_addresses(self.goal)) or None)
        step = self.tab_step(desktop, snapshot)
        if step is not None:
            # The switch itself must be the last thing that ran: an earlier arrival is not this one.
            return judged(step, (last.get('type') == 'browse_url' and last.get('arrived') is True) or None)
        step = self.history_step(desktop, snapshot)
        if step is not None:
            pages = [str((snapshot.get('window') or {}).get('url') or '')[:1024]]
            for receipt in reversed(self.receipts):
                if not self.is_history(receipt):
                    break
                pages.insert(0, receipt.get('from_url'))
            if len(pages) < 2 or not all(pages):
                return judged(step, None)
            moved = all(before != after for before, after in zip(pages, pages[1:]))  # every press, not just one
            return judged(step, True if moved else 'the page address did not change')
        step = self.finder_sort_step(desktop, snapshot)
        if step is not None:
            return judged(step, self.sort_observed(snapshot) if step['status'] == 'done' else None)
        step = self.menu_step(desktop, snapshot)
        return (step, None) if step is None else judged(step, None)

    def sort_observed(self, snapshot):
        """Finder's own sort column against the asked one: True, the contradiction in words, or None
        when that fact does not describe the result. It belongs to the list view, so it says nothing
        in icon view, when the items were grouped instead (Finder has no Sort By then), or for a
        column or a value code has no certain name for."""
        match = FINDER_SORT.search(self.goal)
        column = 'Date Added' if match.group('newest') else SORT_TITLES[match.group('column').casefold()]
        finder = snapshot.get('finder') or {}
        shown = ' '.join(str(finder.get('sort') or '').casefold().split()).removesuffix(' column')
        ran = str(((self.receipts[-1] if self.receipts else {}).get('native_result') or {}).get('path') or '')
        if (column not in SORT_COLUMNS or shown not in SORT_COLUMN_NAMES or 'Group By' in ran
                or 'list' not in str(finder.get('view') or '').casefold()):
            return None
        return True if shown in (SORT_COLUMNS[column], column.casefold()) else 'the folder is not sorted by '+column.casefold()

    def expand_tool(self, raw, marks, snapshot=None):
        """type_into / search_in_app / scroll_to → their first primitive now, the rest queued."""
        action = raw.get('action') if isinstance(raw, dict) else None
        if not isinstance(action, dict):
            return raw
        kind = action.get('type')
        from .desktop_context import BROWSER_IDS
        if kind in ('click_target', 'type_into') and ((snapshot or {}).get('app') or {}).get('bundle_id') in BROWSER_IDS:
            mark = next((m for m in marks if m['id'] == action.get('target_id')), None)
            if mark and mark['kind'] in ('field', 'search field') and re.search(r'address|search|url', mark['label'], re.I):
                # The browser's own address bar: a click left focus on the page, so typing was refused
                # and the bar was clicked again and again (live). Command-L focuses it for real.
                if kind == 'type_into' and isinstance(action.get('text'), str) and action['text'].strip():
                    self.next_queue = [{'type': 'type_text', 'text': action['text'][:2000]}, {'type': 'press_key', 'key': 'enter'}]
                return dict(raw, action={'type': 'press_key', 'key': 'cmd_l'})
        if kind == 'type_into' and isinstance(action.get('text'), str) and action['text'].strip():
            self.next_queue = [{'type': 'type_text', 'text': action['text'][:2000]}]
            return dict(raw, action={'type': 'click_target', 'target_id': action.get('target_id')})
        if kind == 'search_in_app' and isinstance(action.get('query'), str) and action['query'].strip():
            self.next_queue = [{'type': 'type_text', 'text': action['query'][:300]}, {'type': 'press_key', 'key': 'enter'}]
            field = next((m for m in marks if 'search field' in m['kind']), None) or \
                next((m for m in marks if 'field' in m['kind'] and 'search' in m['label'].casefold()), None)
            if field:
                return dict(raw, action={'type': 'click_target', 'target_id': field['id']})
            return dict(raw, action={'type': 'press_key', 'key': 'cmd_f'})
        if kind == 'scroll_to' and isinstance(action.get('text'), str) and action['text'].strip():
            self.next_queue = [{'scroll_to': action['text'][:120], 'left': 6}]
            return dict(raw, action={'type': 'scroll', 'direction': 'down'})
        return raw

    def queued_step(self, desktop, snapshot, regions, visual, image_data):
        """The next step a tool queued, validated against the current screen, or None."""
        while self.queue:
            item = self.queue[0]
            if (item.get('bundle') != (snapshot.get('app') or {}).get('bundle_id')
                    or item.get('window') != (snapshot.get('window') or {}).get('title') and item.get('type') != 'press_key'):
                # Search typing can update a title before its queued Enter. No tool
                # may carry input into another app; scrolling also stays in its window.
                self.queue = []
                return None
            if item.get('type') == 'type_text' and self.goal_mode == 'inspect' and not search_field_focused(snapshot):
                self.queue = []  # a look-only goal types only into search fields
                return None
            if 'scroll_to' in item:
                wanted = item['scroll_to'].casefold()
                seen = any(wanted in (r.get('text') or '').casefold() for r in regions) or \
                    any(wanted in (t.get('label') or '').casefold() for t in snapshot.get('targets') or []) or \
                    named_on_screen(item['scroll_to'], regions, snapshot)
                if seen or item['left'] <= 0:
                    self.queue.pop(0)
                    continue
                item['left'] -= 1
                action = {'type': 'scroll', 'direction': 'down'}
            else:
                action = {key: value for key, value in self.queue.pop(0).items() if key not in ('bundle', 'window', 'risk')}
            risk = item.get('risk', 'navigation')
            try:
                step = validate_step({'status': 'action', 'message': 'Continuing.', 'risk': risk, 'action': action},
                                     desktop, snapshot, goal_mode=self.goal_mode, screen=visual.get('screen'), text_regions=())
            except ValidationError:
                self.queue = []  # the screen is not what the tool expected: the model decides again
                return None
            action = step['action']
            self.pending = action
            return {'status': 'action', 'message': action.get('label', 'Continuing')+'…', 'action': action,
                    'review': not self.automatic or self.review_needed(action, risk, snapshot), 'visual_capture': visual}
        return None

    def review_needed(self, action, risk, snapshot, *, navigation_verified=False):
        """needs_review, except Enter that searches (a search field in a read-only goal) or that
        starts a new line in a document being drafted."""
        if (snapshot.get('app') or {}).get('bundle_id') == 'com.apple.systempreferences' and (action['type'] == 'press_control' or action['type'] == 'click_point' and not navigation_verified):
            return True  # no structural navigation evidence on this action
        if action['type'] == 'press_key' and action.get('key') == 'enter' and risk == 'navigation':
            if self.goal_mode == 'inspect' and search_field_focused(snapshot):
                return False
            if self.goal_mode == 'draft' and snapshot.get('focused_role') == 'AXTextArea':
                return False
        return needs_review(action, risk)

    def press_kind(self, action, marks, snapshot, desktop, visual):
        """What a left click or a press lands on, in the words of ROLE_NAMES ('button', 'link', 'file or
        folder', ...), from the accessibility snapshot or, for a bare point, from what macOS reports
        under it. None when nothing says."""
        if action.get('type') == 'press_control':
            control = next((c for c in snapshot.get('controls') or [] if isinstance(c, dict) and c.get('id') == action.get('control_id')), None)
            return ROLE_NAMES.get((control or {}).get('role'))
        if action.get('type') != 'click_point' or action.get('button', 'left') != 'left':
            return None
        inside = [m for m in marks if m.get('kind') != 'text' and m['x'] <= action['x'] < m['x']+m['width'] and m['y'] <= action['y'] < m['y']+m['height']]
        if inside:
            return min(inside, key=lambda m: m['width']*m['height'])['kind'].removeprefix('page ').split(',')[0]
        x, y, hit = getattr(self, 'point_hit', None) or (None, None, None)
        bridge, screen = getattr(desktop, 'bridge', None), visual.get('screen') or {}
        if (x, y) != (action['x'], action['y']) and hasattr(bridge, 'hit') and screen.get('width'):
            try:
                hit = bridge.hit(screen['x']+action['x']*screen['width'], screen['y']+action['y']*screen['height'])
            except (RuntimeError, ValueError, OSError, KeyError, TypeError):
                hit = None
        if not isinstance(hit, dict):
            return None
        return 'file or folder' if hit.get('finder_item') else ROLE_NAMES.get(hit.get('role'))

    def commits(self, action, kind, snapshot, provider, cancel_kwargs):
        """Whether pressing this button would commit something (order, book, send, save, delete), so
        that the user confirms it whatever risk the step model gave its own proposal.

        A "Place order" click labelled navigation ran unconfirmed: needs_review rests on that label
        and a word list. This asks the text model one narrow question about the button alone (its
        label, app and window, never the goal), once per button, and only for buttons and menu
        items: links, tabs, fields, list rows and files move around or select, and back/forward and
        decline buttons are known. commit and unsure both wait for the user; a failed check does too.
        Controls that commit without being exposed as buttons are not covered here."""
        label = ' '.join(str(action.get('label', '')).split())
        if (action.get('type') not in ('click_point', 'press_control') or kind not in ('button', 'menu item')
                or sum(c.isalpha() for c in label) < 2 or DECLINE.match(label) or self.is_history(action)):
            return False
        app = snapshot.get('app') or {}
        window = str((snapshot.get('window') or {}).get('title', ''))[:120]
        # Everything the answer was given for is in the key: the same "Apply" in the same window, once a
        # sheet with a card-number field is up, is another question (review).
        fields = sum(1 for t in snapshot.get('targets') or [] if isinstance(t, dict) and not t.get('file_item')
                     and t.get('role') in ('AXTextField', 'AXTextArea', 'AXComboBox', 'AXCheckBox'))
        key = (app.get('bundle_id'), window, label.casefold(), fields > 0)
        known = getattr(self, 'commit_checks', {})
        started, asked = time.monotonic(), key not in known
        if asked:
            try:
                reply = provider.complete_structured(COMMIT_SYSTEM, [{'role': 'user', 'content': json.dumps(
                    {'button': label[:80], 'app': app.get('name', ''), 'window': window, 'form_fields_on_screen': fields > 0}, ensure_ascii=False)}],
                    schema=COMMIT_SCHEMA, max_tokens=24, thinking=False, **cancel_kwargs)
            except (RuntimeError, ValueError, OSError, TypeError):
                if self.cancelled.is_set():
                    raise
                reply = {'effect': 'unsure'}  # not known to be navigation: the user decides
            effect = reply.get('effect') if isinstance(reply, dict) else None
            # A reply that is not one of the three answers is not "navigation": the user decides.
            known[key] = effect if effect in ('navigate', 'commit', 'unsure') else 'unsure'
            self.commit_checks = known
        timing('task.commit_check', started, asked=asked, effect=str(known[key]))
        return known[key] in ('commit', 'unsure')

    def named_app(self, snapshot):
        """The installed app the goal names (longest name wins; not browsers), None if none, False if
        it is already in front."""
        from .app_names import normalized
        # Not inside quotes: "remind me 'A11y test 1234: …'" launched the installed A11y (live, v3).
        unquoted = re.sub(r'"[^"]*"|“[^”]*”|\'[^\']*\'', ' ', self.goal)
        words = re.findall(r"[\w.&+'-]+", unquoted.casefold())
        spans = {normalized(' '.join(words[i:i+n])) for n in (1, 2, 3) for i in range(len(words)-n+1)}
        from .desktop_context import BROWSER_IDS
        apps = [a for a in self._catalog or [] if len(normalized(a['name'])) > 3 and normalized(a['name']) in spans
                and a['name'] != 'A11y' and a['bundle_id'] not in BROWSER_IDS | {'com.a11y.companion'}]  # a browser named is about a site; never A11y itself
        from .web_apps import find_site
        site = find_site(unquoted)
        if site:
            # "Hacker News" is a website, not Apple's News app (it launched News, live v3), and "the Chess
            # article on Wikipedia" is a page, not the Chess app (live v3): with a site named, an app counts
            # only as "the Chess app" or "in Chess".
            apps = [a for a in apps if normalized(a['name']) == normalized(site[0]) or re.search(
                r'\b'+re.escape(a['name'].casefold())+r'\s+app\b|\b(?:in|with|using)\s+'+re.escape(a['name'].casefold())+r'\b', unquoted.casefold())]
        if not apps:
            return None
        app = max(apps, key=lambda a: len(a['name']))
        # In front already: nothing to open, unless the goal wants a new document (TextEdit was in front and
        # the population went into the open one, live v3).
        # In front without a window (Finder showing only the desktop) is not ready either: it gets one (the
        # model said "the Finder window is not visible, please open it" and stopped, live held-out).
        windowed = has_window(snapshot, app['bundle_id']) or 'app_windows' not in snapshot and bool(snapshot.get('window'))
        new = NEW_DOCUMENT.search(self.goal) and self.goal_mode != 'inspect'  # "do not make a new document" is look-only
        return False if app['bundle_id'] == (snapshot.get('app') or {}).get('bundle_id') and windowed and not new else app

    def plan_start(self, desktop, snapshot, provider):
        """Plan once, with the text model, then take the start in code: open the app, go to the
        website or run the search. Without it the first vision steps clicked an app's name, a page
        title or Finder (live, varied suite). None when there is nothing to open."""
        if self.planned or self.receipts or self.click is not None or self.row is not None:
            return None
        self.planned = True
        names = self.app_names(desktop, snapshot)
        front = (snapshot.get('app') or {}).get('name', '')
        named = self.named_app(snapshot)
        if named:
            # The goal names an installed app ("in the Calculator app", "a new TextEdit document"):
            # open it in code. The model kept asking the user to open it from the Dock (live).
            running = {a.get('bundle_id') for a in snapshot.get('available_apps') or []}
            windowed = has_window(snapshot, named['bundle_id'])
            # Running without a window (TextEdit after its document closed): a new window, as launching it
            # would give; switching to it left nothing to type into ("Input is unavailable", live).
            # "In a new TextEdit document": a new window even when one is open. Switching to the open one
            # wrote the list into an existing document (live, v2).
            # A look-only goal never creates a document ("which documents are open… do not make a new document"
            # got one, live held-out); Finder's windows are only views of folders.
            creates = self.goal_mode != 'inspect' or named['bundle_id'] == 'com.apple.finder'
            kind = 'launch_app' if named['bundle_id'] not in running else 'focus_app' if not creates or windowed and not NEW_DOCUMENT.search(self.goal) else 'new_window'
            choices = [kind]+(['focus_app'] if kind == 'new_window' and windowed else [])  # no new_window (Windows): switch
            for kind in choices:
                try:
                    action = validate_step({'status': 'action', 'message': 'Opening.', 'risk': 'navigation',
                                            'action': {'type': kind, 'bundle_id': named['bundle_id']}}, desktop, snapshot,
                                           catalog=self._catalog or (), goal=self.goal)['action']
                    break
                except (ValueError, ValidationError):
                    action = None
            if action is None:
                return None
            if action['type'] == 'launch_app' and NEW_DOCUMENT.search(self.goal) and self.goal_mode != 'inspect':
                self.new_after_launch = named['bundle_id']  # a launch reopens last time's windows: ⌘N follows
            self.pending = action
            return {'status': 'action', 'message': action.get('label', 'Opening')+'…', 'action': action, 'review': not self.automatic,
                    'visual_capture': None}
        if named is False:
            return None  # the named app is already in front: nothing to plan for where to start
        from .web_apps import find_site, site_address
        from .desktop_context import BROWSER_IDS, default_browser_id
        site, address = find_site(self.goal), site_address(self.goal)
        window = ((snapshot.get('window') or {}).get('title') or '').casefold()
        if site and address and not ((snapshot.get('app') or {}).get('bundle_id') in BROWSER_IDS and site[0].split()[0] in window):
            # The goal names a known site and it isn't in front: open it, no planning call. The
            # planner picked an app that isn't installed for Hacker News and the task ended (live, v3).
            from .web_apps import browser_mode, browser_named
            wanted = browser_named(self.goal) or ''
            browser = next((a for a in self._catalog or [] if wanted and a['name'] == wanted), None) or \
                next((a for a in self._catalog or [] if a['bundle_id'] == default_browser_id()), None) or \
                next((a for a in self._catalog or [] if a['bundle_id'] in BROWSER_IDS), None)
            if browser:
                try:
                    action = validate_step({'status': 'action', 'message': 'Opening.', 'risk': 'navigation',
                                            'action': {'type': 'browse_url', 'bundle_id': browser['bundle_id'], 'url': address,
                                                       'label': 'Open '+address+' in '+browser['name'], 'browser_mode': browser_mode(self.goal)}},
                                           desktop, snapshot, catalog=self._catalog or (), goal=self.goal)['action']
                except (ValueError, ValidationError):
                    action = None
                if action:
                    self.sites_opened = [address]
                    self.pending = action
                    return {'status': 'action', 'message': action.get('label', 'Opening')+'…', 'action': action,
                            'review': not self.automatic, 'visual_capture': None}
        planner = getattr(type(provider), 'plan_task', None)
        if planner is None:
            return None
        try:
            plan = planner(provider, {'goal': self.goal, 'front_app': front, 'front_window': (snapshot.get('window') or {}).get('title', '')[:120],
                                      'installed_apps': names}, **cancel_argument(planner, self.cancelled))
        except (RuntimeError, ValueError, OSError):
            if self.cancelled.is_set():
                raise  # stopped while planning: nothing more is observed or asked
            return None
        if not isinstance(plan, dict) or plan.get('start') not in ('app', 'website', 'search', 'current'):
            return None
        self.plan = {'steps': [str(step)[:160] for step in plan.get('steps') or []][:6], 'done_when': str(plan.get('done_when', ''))[:200]}
        from .app_names import resolve_app
        from .desktop_context import BROWSER_IDS, default_browser_id
        from urllib.parse import urlencode, urlsplit
        action = None
        start = plan['start']
        from .web_apps import site_address
        named_site = site_address(self.goal)
        if start == 'current' and not plan.get('url') and (snapshot.get('app') or {}).get('bundle_id') in BROWSER_IDS:
            # The planner stays in the browser in front but names the address in its steps ("In Safari, navigate
            # to https://open-meteo.com"); the model then fumbled ⌘L, typing and Enter (live, held-out). Open it.
            written = re.search(r'https?://[^\s\'"<>)]+', ' '.join(str(step) for step in plan.get('steps') or [])+' '+str(plan.get('query', '')))
            if written:
                start, plan = 'website', dict(plan, url=written.group(0).rstrip('.,;'))
        if start == 'current' and not plan.get('url') and (snapshot.get('app') or {}).get('bundle_id') in BROWSER_IDS:
            # The planner stays in the browser in front but names the address in its steps ("In Safari, navigate
            # to https://open-meteo.com"); the model then fumbled ⌘L, typing and Enter (review 2026-10-25). Open it.
            written = re.search(r'https?://[^\s\'"<>)]+', ' '.join(str(step) for step in plan.get('steps') or [])+' '+str(plan.get('query', '')))
            if written:
                start, plan = 'website', dict(plan, url=written.group(0).rstrip('.,;'))
        if start == 'current' and named_site and (snapshot.get('app') or {}).get('bundle_id') not in BROWSER_IDS:
            # "On Wikipedia, what does the History section of the Coffee article start with?" with
            # Preview in front: the planner kept the current app and the task gave up (live, v3).
            start, plan = 'website', dict(plan, url=named_site)
        if start == 'app' and plan.get('url') and any(a['bundle_id'] in BROWSER_IDS for a in resolve_app(str(plan.get('app', '')), self._catalog or [])):
            start = 'website'  # "Google Chrome" + an address is the address, in the user's own default browser
        if start == 'app':
            matches = resolve_app(str(plan.get('app', '')), self._catalog or [])
            if len(matches) == 1 and matches[0]['name'] != front:
                running = {a.get('bundle_id') for a in snapshot.get('available_apps') or []}
                windowed = has_window(snapshot, matches[0]['bundle_id'])
                # Running without a window (Finder with only the desktop): a new one, or nothing to work in (live).
                creates = self.goal_mode != 'inspect' or matches[0]['bundle_id'] == 'com.apple.finder'
                kind = ('focus_app' if windowed or not creates or 'app_windows' not in snapshot else 'new_window') if matches[0]['bundle_id'] in running else 'launch_app'
                action = {'type': kind, 'bundle_id': matches[0]['bundle_id']}
            elif not matches:
                # Not installed: its website, else a search for it (GitHub went to Mail without one, live).
                start = 'website' if plan.get('url') else 'search'
                plan = dict(plan, query=plan.get('query') or plan.get('app') or self.goal)
                if not plan.get('url') and str(plan.get('app', '')).strip():
                    # Its own website: DuckDuckGo's "\" goes to the first result, the product's site.
                    # "I want to use Notion" (not installed) ended on a results page (live, v2).
                    plan = dict(plan, query='\\'+str(plan['app']).strip())
        if start in ('website', 'search') and action is None:
            address = str(plan.get('url', '')).strip() if start == 'website' else ''
            if start == 'website' and '://' not in address and '.' in address:
                address = 'https://'+address
            if start == 'search' or not urlsplit(address).hostname:
                query = str(plan.get('query') or self.goal)[:200]
                address = 'https://duckduckgo.com/?'+urlencode({'q': query})
            preferred = default_browser_id()
            browser = next((a for a in self._catalog or [] if a['bundle_id'] == preferred), None) or \
                next((a for a in self._catalog or [] if a['bundle_id'] in BROWSER_IDS), None)
            if browser and urlsplit(address).scheme in ('http', 'https'):
                action = {'type': 'browse_url', 'bundle_id': browser['bundle_id'], 'url': address, 'label': 'Open '+address+' in '+browser['name'],
                          'browser_mode': 'default'}
        if action is None:
            return None
        try:
            action = validate_step({'status': 'action', 'message': 'Opening.', 'risk': 'navigation', 'action': action}, desktop, snapshot,
                                   catalog=self._catalog or (), goal=self.goal)['action']
        except (ValueError, ValidationError):
            return None
        self.pending = action
        return {'status': 'action', 'message': action.get('label', 'Opening')+'…', 'action': action, 'review': not self.automatic,
                'visual_capture': None}


    def take_opening(self):
        """The predetermined opening step, once, before any model-chosen step."""
        if self.opening is None or self.receipts or self.cancelled.is_set():
            return None
        action, self.opening = self.opening, None
        self.pending = action
        return {'status': 'action', 'message': action.get('label', 'Opening')+'…', 'action': action,
                'review': not self.automatic or action['type'] not in ('browse_url', 'launch_app'), 'visual_capture': None}

    def cycled(self, action):
        """The same click on a screen this task was on before, not just the step before: a loop through
        other screens back to here (a toggle opened and closed, a page left and returned to)."""
        if action.get('type') not in ('click_point', 'press_control') or not self.screen_seen:
            return False
        label = ' '.join(str(action.get('label', '')).casefold().split())
        return bool(label) and any(r.get('screen') == self.screen_seen and r.get('type') == action['type']
                                   and ' '.join(str(r.get('label', '')).casefold().split()) == label
                                   for r in self.receipts[:-1])

    def repeat_check(self, action):
        """The step just taken worked, and the model proposes the very same step again.

        On a checkbox, switch, tab or option whose own state changed, the same click undoes the
        result: it is refused (the tick was removed again, sim form). Any other step that visibly
        worked (the page or the screen changed) may be what the goal needed: "go back to the page
        before this one" went back twice (sim). The model is asked once per target whether the goal
        is already reached; a second identical proposal runs, so "next page" three times still works."""
        last = self.receipts[-1] if self.receipts else {}
        kind = action.get('type')
        clicks = ('click_point', 'press_control')
        same_label = ' '.join(str(action.get('label', '')).casefold().split()) == ' '.join(str(last.get('label', '')).casefold().split())
        if kind in clicks and last.get('type') in clicks and last.get('state_changed') and same_label and action.get('label'):
            self.undo_refused = str(action['label'])[:80]
            raise ValidationError('“'+str(action['label'])[:80]+'” was just clicked and its own state changed. Clicking it again would undo that. '
                                  'If the goal is reached now, report status=done; otherwise choose a different target.')
        history = self.is_history(action) and self.is_history(last)
        if not history and not (kind in clicks and last.get('type') == kind and same_label and len(str(action.get('label', '')).strip()) > 1
                                and action.get('click_count', 1) == last.get('click_count', 1) and action.get('button', 'left') == last.get('button', 'left')):
            return
        moved = bool(last.get('from_url')) and bool(self.page_seen) and last['from_url'] != self.page_seen
        if last.get('status') == 'failed' or not (moved or last.get('screen') is not None and last.get('screen') != self.screen_seen):
            return  # it did nothing visible: the "nothing changed" and repeat limits handle that
        key = 'history' if history else ' '.join(str(action.get('label', '')).casefold().split())
        asked = getattr(self, 'repeat_asked', set())
        if key in asked:
            return
        self.repeat_asked = asked | {key}
        what = str(last.get('label') or action.get('label') or 'That step')[:80]
        raise ValidationError('“'+what+'” already ran and it worked: '+(
            'the page changed from '+str(last['from_url'])[:200]+' to '+self.page_seen[:200] if moved else 'the screen changed')+
            '. Do it again only if the goal needs it once more. If what the goal asks for is reached now, report status=done.')

    def repeats(self, action):
        """How many times this task already clicked/pressed this target: in a row, or anywhere
        when it was more than once ("Activity (1)" and "Bell icon" alternated for 24 steps, live).
        Badge counts are ignored: "Activity (1)" is "Activity"."""
        if self.history_steps(action) >= HISTORY_LIMIT:
            return 2  # back, back, back: each page differs, but it is still a loop (live, 13 times)
        if action['type'] not in ('click_point', 'press_control'):
            return 0
        label = action.get('label', '').casefold().strip()
        if len(label) <= 1:
            return 0  # Calculator digits and keys can legitimately repeat.
        target = tuple(word for word in label_words(label) if not word.isdigit())
        # Only when the screen looked the same as at that click: "next month" five times to reach
        # March changes the screen each time and is progress, not a loop (live).
        same = lambda receipt: (receipt.get('type') in ('click_point', 'press_control')
                                and tuple(word for word in label_words(receipt.get('label') or '') if not word.isdigit()) == target
                                and (receipt.get('screen') is None or self.screen_seen is None or receipt.get('screen') == self.screen_seen))
        count = 0
        for receipt in reversed(self.receipts):
            if not same(receipt):
                break
            count += 1
        # Twice in a row, or three times anywhere (opening two new tabs is not a loop).
        return max(count, (sum(map(same, self.receipts))-1) if target else 0)

    @staticmethod
    def is_history(step):
        """A back/forward step: the Back button, or ⌘[ / ⌘]."""
        if step.get('type') == 'press_key':
            # Executed presses come back labelled "Press ⌘+[ in Safari" (live: 24 presses, none counted).
            key = str(step.get('key') or step.get('target') or '').casefold()
            return key in ('cmd_[', 'cmd_]') or bool(re.search(r'⌘\+?[\[\]]', str(step.get('label') or '')))
        words = set(label_words(step.get('label') or ''))
        return bool(words & {'back', 'forward'}) and words <= {'go', 'back', 'forward', 'previous', 'next', 'page', 'button', 'navigate'}

    def history_steps(self, action):
        """How many back/forward steps in a row this one would make."""
        if not self.is_history(action):
            return 0
        count = 1
        for receipt in reversed(self.receipts):
            if not self.is_history(receipt):
                break
            count += 1
        return count

    def file_interaction(self, step, marks, provider, cancel_kwargs, snapshot=None):
        """Interpret open versus select only after code has identified a native file target."""
        action = step.get('action') or {}
        self.file_step_context=None
        if (step.get('status') != 'action' or action.get('type') != 'click_point'
                or action.get('button','left') != 'left'):
            return step  # explicit click gestures always keep the user's choice
        found = [m for m in marks if m.get('kind','').startswith('file or folder')
                 and str(m.get('label','')).casefold() == str(action.get('label','')).casefold()
                 and m['x'] <= action['x'] < m['x']+m['width']
                 and m['y'] <= action['y'] < m['y']+m['height']]
        distinct=[]
        for mark in found:
            if not any(abs(mark['x']+mark['width']/2-other['x']-other['width']/2)<.001
                       and abs(mark['y']+mark['height']/2-other['y']-other['height']/2)<.001 for other in distinct):
                distinct.append(mark)
        found=distinct
        if len(found) != 1:
            timing('task.file_interaction',time.monotonic(),candidates=len(found),decision='not_grounded')
            return step
        name = found[0]['label']
        folder = ((snapshot or {}).get('finder') or {}).get('target','')
        if action.get('click_count',1)==2:
            self.file_step_context={'file_parent':folder,'file_name':name} if folder else None
            return step
        if CLICK_GOAL.match(self.goal):
            return step
        key = (folder,name)
        decisions = getattr(self,'file_interactions',{})
        if key not in decisions:
            try:
                result = provider.complete_structured(FILE_INTERACTION_SYSTEM,
                    [{'role':'user','content':json.dumps({'goal':self.goal,'observed_name':name,'current_folder':folder},ensure_ascii=False)}],
                    schema=FILE_INTERACTION_SCHEMA,max_tokens=64,thinking=False,**cancel_kwargs)
                decision = result.get('interaction') if isinstance(result,dict) else None
            except (RuntimeError,ValueError,OSError):
                if self.cancelled.is_set():
                    raise
                decision = None
            decisions[key] = decision if decision in ('open','select') else 'other'
            self.file_interactions = decisions
        timing('task.file_interaction',time.monotonic(),candidates=1,decision=decisions[key])
        if decisions[key] == 'open':
            self.file_step_context={'file_parent':folder,'file_name':name} if folder else None
            return dict(step,action=dict(action,click_count=2))
        return step

    def record(self, receipt):
        if (self.pending or {}).get('type') in ('launch_app', 'focus_app', 'new_window') and receipt.get('status') != 'failed':
            self.brought = getattr(self, 'brought', set()) | {self.pending.get('bundle_id')}
        if (self.pending or {}).get('type') == 'type_text' and receipt.get('type') == 'type_text' and receipt.get('status') != 'failed':
            self.typed = getattr(self, 'typed', [])+[self.pending.get('text', '')]
        gesture = {key:(self.pending or {}).get(key,receipt.get(key)) for key in ('button','click_count')
                   if key in (self.pending or {}) or key in receipt}
        wanted = {key: (self.pending or {}).get(key) for key in ('bundle_id', 'url', 'browser_mode') if (self.pending or {}).get(key)}
        file_context=getattr(self,'file_step_context',None)
        if (file_context and (self.pending or {}).get('type')=='click_point'
                and (self.pending or {}).get('click_count')==2
                and str((self.pending or {}).get('label','')).casefold()==file_context['file_name'].casefold()):
            wanted.update(file_context)
        if (self.pending or {}).get('type')=='click_point':
            timing('task.click_receipt',time.monotonic(),double_click=(self.pending or {}).get('click_count')==2,
                   file_context='file_parent' in wanted)
        # (press_key too: back/forward is a key, and its page change is the only native sign that it worked)
        page = ({'from_url': self.page_seen} if getattr(self, 'page_seen', '') and receipt.get('type') in ('click_point', 'press_control', 'press_key')
                else {})
        scroll = ({'scroll_view': getattr(self, 'scroll_seen', None),
                   'direction': (self.pending or {}).get('direction', receipt.get('direction'))}
                  if receipt.get('type') == 'scroll' else {})
        look = getattr(self, 'pending_look', None)
        look = {'look': look} if look and receipt.get('type') in ('click_point', 'press_control') and receipt.get('status') != 'failed' else {}
        self.pending_look = None
        self.file_step_context=None
        self.pending = None
        self.receipts.append(dict({key: deepcopy(receipt.get(key)) for key in ('type', 'label', 'key', 'direction', 'status', 'before', 'after', 'native_result', 'effect')
                                   if key != 'effect' or key in receipt},
                                  screen=self.screen_seen, wanted=wanted,
                                  **scroll, **gesture, **page, **look))

    def propose(self, desktop, vision, provider, geometry, *, progress=None):
        # Verification is reused only within this observation/decision call.
        # Do not retain private evidence on the task after return or cancellation.
        self._completion_cache = {}
        self._completion_stats = {'calls': 0, 'ms': 0}
        self._found = []
        try:
            return self._propose(desktop, vision, provider, geometry, progress=progress)
        finally:
            self.__dict__.pop('_completion_cache', None)
            self.__dict__.pop('_completion_stats', None)
            found = self.__dict__.pop('_found', [])
            if found and not self.cancelled.is_set():
                # Requested facts this screen proved (keep_findings), for the following steps; a few lines.
                self.findings = (list(getattr(self, 'findings', []))+[line for line in found if line not in getattr(self, 'findings', [])])[-4:]

    def _propose(self, desktop, vision, provider, geometry, *, progress=None):
        if self.cancelled.is_set():
            raise ValueError('Computer task stopped.')
        if len(self.receipts) >= self.max_steps:
            return {'status': 'needs_user', 'message': f'Stopped at the {self.max_steps}-action limit. The task is not marked complete.', 'action': None}
        if not desktop.enabled or not vision.enabled:
            raise ValueError('Computer tasks need app awareness and local screen vision. Enable them in Settings.')
        desktop_generation, vision_generation = desktop.generation, vision.generation
        def check_active():
            if self.cancelled.is_set() or not desktop.enabled or not vision.enabled or desktop.generation != desktop_generation or vision.generation != vision_generation:
                raise ValueError('Computer task stopped or access was paused. No next action was submitted.')
        step_started = time.monotonic()
        stages = {}
        observed_at = time.monotonic()
        timing('task.observe.start', observed_at, step=len(self.receipts)+1)
        snapshot = observe_desktop(desktop,targets=True)
        if snapshot.get('companion_focused'):
            snapshot = observe_desktop(desktop,target_bundle=snapshot.get('app', {}).get('bundle_id'),targets=True)
        timing('task.metadata.finished', observed_at, step=len(self.receipts)+1)
        stages['metadata_ms'] = round((time.monotonic()-observed_at)*1000)
        if not self.receipts and snapshot.get('browser'):
            self.initial_browser_counts = dict(snapshot['browser'])
        started = self.plan_start(desktop, snapshot, provider)
        if started is not None:
            try:
                check_active()  # planning is a model call: stopped or paused meanwhile, its opening is not offered
            except ValueError:
                self.pending = None
                raise
            timing('task.step.summary', step_started, step=len(self.receipts)+1, status='action', code='plan_start')
            return started
        if self.keypad is not None:
            keyed = self.keypad_step(desktop, snapshot)
            if keyed is not None:
                # Done here is code's own evidence (keypad_step): each exact key has an accepted
                # press and the result is computed, so no screenshot or model call is spent.
                if keyed['status'] != 'action':
                    return {'status': keyed['status'], 'message': keyed['message'], 'action': None}
                return keyed
        capture_at = time.monotonic()
        visual = vision.capture(provider, x=geometry['x'], y=geometry['y'], desktop=snapshot, fast_text=True)
        timing('task.capture.finished', capture_at, step=len(self.receipts)+1)
        stages['capture_ms'] = round((time.monotonic()-capture_at)*1000)
        if isinstance(visual.get('ocr_ms'), int):
            stages['ocr_ms'] = visual['ocr_ms']
        last = self.receipts[-1] if self.receipts else {}
        file_open=(last.get('wanted') or {}).get('file_parent') and last.get('click_count')==2
        if (last.get('type') in SETTLE_AFTER or file_open) and not last.get('settled'):
            # Instead of spending a model step deciding to "wait", watch the screen:
            # recapture until its text stops changing (bounded to about 3 s).
            last['settled'] = True
            settle_at = time.monotonic()
            previous = observation_state(snapshot,visual)
            for _ in range(4):
                time.sleep(0.6)
                if self.cancelled.is_set() or not desktop.enabled or not vision.enabled or desktop.generation != desktop_generation or vision.generation != vision_generation:
                    raise ValueError('Computer task stopped or access was paused. No next action was submitted.')
                snapshot = observe_desktop(desktop,targets=True)
                visual = vision.capture(provider, x=geometry['x'], y=geometry['y'], desktop=snapshot, fast_text=True)
                current = observation_state(snapshot,visual)
                if current == previous and len(current[0]) >= 3:
                    break
                previous = current
            stages['settle_ms'] = round((time.monotonic()-settle_at)*1000)
        image_data = visual.pop('image_data')
        if self.cancelled.is_set() or not desktop.enabled or not vision.enabled or desktop.generation != desktop_generation or vision.generation != vision_generation:
            raise ValueError('Computer task stopped or access was paused. No next action was submitted.')
        regions = screen_text(visual, snapshot)
        self.screen_seen = screen_signature(regions, snapshot)
        self.page_seen = str((snapshot.get('window') or {}).get('url') or '')[:1024]
        cancel_kwargs = {'cancel': self.cancelled} if accepts_cancel(provider) else {}
        def finish(step, code, *, observed=True):
            """Where a step leaves once the screen was read: one cancellation check, one timing record.

            A 'done' arrives with its evidence already established by the path that made it: code
            observed the result (a changed screen after its click, a numbered line it read, an
            address it compared), the verifier quoted current data (goal_met, still_missing), or
            the reply was written from the text of the view just opened (view_answer, a section).
            The exception is a code step that knows only that its own action was submitted
            (receipt_step), which passes what native state says about the result as `observed`:
            True stands; a contradiction (a str) ends the step without asking anyone; None is
            checked here by the verifier, once. A path that has evidence never pays for that call."""
            check_active()
            if step['status'] == 'done' and isinstance(observed, str):
                step = {'status': 'needs_user', 'action': None, 'message': 'That was sent, but '+observed+'.'}
            elif step['status'] == 'done' and observed is not True:
                missing = self.still_missing(provider, snapshot, regions, cancel_kwargs)
                check_active()
                if missing:
                    step = {'status': 'needs_user', 'action': None,
                            'message': 'That was sent, but I could not confirm the result: '+missing[:160].rstrip('. ')+'.'}
            stats = self._completion_stats
            stages['verification_calls'] = stats['calls']
            stages['verification_ms'] = stats['ms']
            if 'model' in stages:
                # Only steps the step model decided are kept on the task (benchmark_pipeline reads them).
                stages['total_ms'] = round((time.monotonic()-step_started)*1000)
                self.timings.append(stages)
            timing('task.step.summary', step_started, step=len(self.receipts)+1, status=step['status'], code=code,
                   **{key: value for key, value in stages.items() if key != 'model'},
                   model_calls=len(stages.get('model', [])),
                   model_ms=[call['ms'] for call in stages.get('model', [])],
                   model_modes=[call['mode'] for call in stages.get('model', [])])
            return step
        self.pending_look = None
        self.check_last_click(snapshot, image_data)
        self.check_last_scroll(snapshot, regions)
        result = self.click_result(regions, image_data, visual, vision)
        if result is not None:
            for _ in range(3):  # a page can take a moment to show what the click opened
                if result['status'] == 'done':
                    break
                time.sleep(0.5)
                if self.cancelled.is_set() or not desktop.enabled or not vision.enabled or desktop.generation != desktop_generation or vision.generation != vision_generation:
                    raise ValueError('Computer task stopped or access was paused. No next action was submitted.')
                desktop.collect_once()
                snapshot = desktop.context()
                visual = vision.capture(provider, x=geometry['x'], y=geometry['y'], desktop=snapshot, fast_text=True)
                image_data = visual.pop('image_data', None)
                regions = screen_text(visual, snapshot)
                result = self.click_result(regions, image_data, visual, vision)
            self.clicked_text = None
            # "Clicked X." is said after code saw the text or the pixels at X change. A list-row
            # click is the one case that reports the click from its receipt alone (click_result).
            return finish(result, 'click_result')
        from .eval_control import step_shot
        step_shot(image_data, len(self.receipts)+1, None, 'observed', observation=snapshot, screen=visual.get('screen'))  # evaluation only: what each step saw, code steps too
        if self.goal_mode == 'inspect' and finder_name_editor(snapshot):
            # A slow second click on Finder's selected filename starts renaming.
            # Escape cancels that transient editor; never type/commit a new name.
            step = validate_step({'status':'action', 'message':'Leaving the filename unchanged.',
                                  'risk':'navigation', 'action':{'type':'press_key','key':'escape'}},desktop,snapshot)
            self.pending = step['action']
            return dict(step, review=not self.automatic, visual_capture=visual)
        met = self.goal_met(provider, snapshot, regions, cancel_kwargs, check_active=check_active)
        if met is not None:
            return finish(met, 'goal_met')
        viewed = self.view_step(desktop, snapshot, regions, visual, image_data, vision)
        if viewed is not None:
            return viewed
        listed = self.nth_item_answer(regions) if self.receipts else None  # once the page is open
        if listed is not None:
            return finish(listed, 'nth_item')
        read = self.view_answer(provider, snapshot, regions, {'cancel': self.cancelled} if accepts_cancel(provider) else {},
                                desktop, vision, desktop_generation, vision_generation)
        if read is not None:
            return finish(read, 'view_answer')
        opened = (self.navigation_check(desktop, snapshot)
                  or self.settings_pane_step(desktop, snapshot, provider, cancel_kwargs)
                  or self.new_document_step(desktop, snapshot) or self.section_step(desktop, snapshot))
        observed = True
        if opened is None:
            opened, observed = self.receipt_step(desktop, snapshot)
            for _ in range(3):  # a page or a folder can take a moment to show what the key or command did
                if opened is None or opened['status'] != 'done' or not isinstance(observed, str):
                    break
                time.sleep(0.5)
                check_active()
                snapshot = observe_desktop(desktop, targets=True)
                visual = vision.capture(provider, x=geometry['x'], y=geometry['y'], desktop=snapshot, fast_text=True)
                image_data = visual.pop('image_data', None)
                check_active()
                regions = screen_text(visual, snapshot)
                self.screen_seen = screen_signature(regions, snapshot)
                self.page_seen = str((snapshot.get('window') or {}).get('url') or '')[:1024]
                opened, observed = self.receipt_step(desktop, snapshot)
        if opened is None:
            opened, observed = self.menu_pick_step(desktop, snapshot, provider, cancel_kwargs, regions), True  # its done is already verified
        if opened is not None:
            if opened['status'] == 'done' and opened.get('message') == 'Read from the screen.' and regions:
                answer = self.screen_answer(provider, snapshot, regions, opened['message'], cancel_kwargs)
                opened = dict(opened, message=answer or 'The section is open on the page.')
            return finish(opened, 'code_step', observed=observed)
        clicked = (self.row_step(desktop, snapshot, visual, image_data, vision)
                   or self.click_step(desktop, snapshot, regions, visual, image_data, vision, provider))
        if clicked is not None:
            from .eval_control import step_shot
            step_shot(image_data, len(self.receipts)+1, clicked.get('action'), 'code', observation=snapshot, screen=visual.get('screen'))
            timing('task.step.summary', step_started, step=len(self.receipts)+1, status='action', code='click')
            return clicked
        queued = self.queued_step(desktop, snapshot, regions, visual, image_data)
        if queued is not None:
            from .eval_control import step_shot
            step_shot(image_data, len(self.receipts)+1, queued.get('action'), 'tool', observation=snapshot, screen=visual.get('screen'))
            timing('task.step.summary', step_started, step=len(self.receipts)+1, status=queued['status'], code='tool')
            return queued
        focused = self.focused_click_step(desktop, snapshot, regions, visual, image_data, vision, provider)
        if focused is not None:
            from .eval_control import step_shot
            step_shot(image_data, len(self.receipts)+1, focused['action'], 'grounded', observation=snapshot, screen=visual.get('screen'))
            timing('task.step.summary', step_started, step=len(self.receipts)+1, status='action', code='focused_click')
            return focused
        if self.goal_mode is None:
            classify_at = time.monotonic()
            classifier = getattr(type(provider), 'classify_task_goal', None)
            self.goal_mode = classifier(provider, self.goal, **cancel_argument(classifier, self.cancelled)) if classifier else 'unspecified'
            stages['classify_ms'] = round((time.monotonic()-classify_at)*1000)
            if self.cancelled.is_set() or not desktop.enabled or not vision.enabled or desktop.generation != desktop_generation or vision.generation != vision_generation:
                raise ValueError('Computer task stopped or access was paused. No next action was submitted.')
        stages['text_regions'] = len(regions)
        stages['controls'] = len(snapshot.get('controls') or [])
        if progress:
            progress('Deciding the next step…')
        # Before/after snapshots in old receipts duplicate the current observation
        # and grow every turn. Keep only compact action outcomes in model context.
        history = []
        for receipt in self.receipts[-4:]:
            outcome = {key: receipt.get(key) for key in ('type', 'label', 'status', 'direction', 'effect')
                       if key not in ('effect', 'direction') or receipt.get(key)}
            result = receipt.get('native_result') or {}
            outcome['native_result'] = {key: result[key] for key in
                ('submitted', 'activated', 'running', 'url', 'window_id', 'tab_count', 'new_window') if key in result}
            history.append(outcome)
        model_desktop = compact_desktop(snapshot)
        model_visual = {'screen': visual['screen']} if 'screen' in visual else {}
        marks = screen_marks(snapshot.get('targets'), regions, visual.get('screen')) if snapshot.get('targets') is not None else []
        live = self.live_marks(marks)
        if live and len(live) < len(marks):
            marks = [dict(m, id='t%d' % (i+1)) for i, m in enumerate(live)]  # renumbered: ids stay consecutive
        if marks:
            # Everything clickable, numbered: the model picks a number and code knows where it is.
            # The same names would otherwise appear again as controls and text lines.
            model_visual['targets'] = compact_marks(marks)
            model_desktop.pop('controls', None)
        elif regions:
            model_visual['text_regions'] = compact_regions(regions)
        # Stable goal/authorization/catalog first so Ollama reuses that prefix across
        # steps; the changing observation follows. Observations are compact strings:
        # a live log showed ~3,500 of ~4,800 prompt tokens per step spent on verbose
        # JSON for OCR lines and controls.
        # The goal and app list stay the same for the whole task, in their own message, so Ollama
        # keeps them cached; only the observation after them is read again each step (a step
        # re-read ~4,000 prompt tokens, 2.8 s, when they shared one message).
        messages = [{'role': 'user', 'content': json.dumps({'goal': self.goal, 'goal_mode': self.goal_mode,
                                                                        'initial_browser_counts':getattr(self,'initial_browser_counts',{}),
                         **({'user_was_looking_at': self.looking_at} if self.looking_at else {}),
                         **({'plan': self.plan} if self.plan else {}),
                         **({'worked_before': self.worked_before} if self.worked_before else {}),
                         'authorization': 'The user requested this goal. Opening apps and browser navigation toward it are already authorized.',
                         'installed_apps': self.app_names(desktop, snapshot)}, ensure_ascii=False, separators=(',', ':'))},
                    {'role': 'user', 'content': json.dumps({'actions_taken': history,
                         # Lines the completion check already accepted on earlier screens (keep_findings): the model
                         # went back for the ticket price it had read instead of reporting both facts (sim).
                         **({'already_found_for_this_goal_on_earlier_screens': list(self.findings)} if getattr(self, 'findings', None) else {}),
                         'desktop': model_desktop, 'visual_context': model_visual},
                                                           ensure_ascii=False, separators=(',', ':'))}]
        context_chars = sum(len(message['content']) for message in messages)
        timing('task.context.prepared', observed_at, step=len(self.receipts)+1, text_chars=context_chars)
        stages['context_chars'] = context_chars
        stages['model'] = []
        cancel_kwargs = {'cancel': self.cancelled} if accepts_cancel(provider) else {}
        model_image = None
        correction = None
        control_ids = [] if marks else [c['id'] for c in snapshot.get('controls') or [] if isinstance(c, dict) and c.get('id') and c.get('enabled', True)]
        target_ids = [m['id'] for m in marks]
        for attempt in range(2):
            check_active()
            if attempt and progress:
                progress('Double-checking…')
            check_active()
            inference_at = time.monotonic()
            if model_image is None:
                scale = getattr(type(vision), 'model_image', None)
                model_image = scale(image_data) if callable(scale) else image_data
                stages['image_scaled_ms'] = round((time.monotonic()-inference_at)*1000)
                inference_at = time.monotonic()
            final = ((correction+'\n\n') if correction else '')+IMAGE_PASS
            request_messages = messages + [{'role': 'user', 'content': final}]
            try:
                step_method = getattr(type(provider), 'complete_computer_step', None)
                complete_step = provider.complete_computer_step if callable(step_method) else provider.complete_structured
                raw = complete_step(SYSTEM, request_messages,
                                                   schema=schema_for(snapshot.get('platform'), control_ids, target_ids,
                                                                     [m['id'] for m in marks if m['kind'].startswith('file or folder')]),
                                                   image_data=model_image,
                                                   max_tokens=1200, thinking=False,
                                                   **({'targets': marks} if callable(step_method) else {}), **cancel_kwargs)
            finally:
                stages['model'].append({'mode': 'image', 'ms': round((time.monotonic()-inference_at)*1000)})
            timing('task.model.finished', inference_at, step=len(self.receipts)+1, attempt=attempt+1, mode='image')
            check_active()
            if pixel_model(provider) and isinstance(raw, dict) and isinstance(raw.get('action'), dict) \
                    and raw['action'].get('type') == 'click_point' and model_image:
                raw = dict(raw, action=pixels_to_fractions(raw['action'], model_image))
            if desktop.context().get('status') == 'stale':
                # A slow model answer outlived the observation (10 s): valid steps were rejected as
                # "Fresh desktop context is required" (live). Read the desktop again; execution
                # re-checks the target in any case.
                desktop.collect_once(targets=True)
                snapshot = dict(desktop.context(), targets=snapshot.get('targets'))
            unchanged_action = False
            self.undo_refused = None
            try:
                self.next_queue = []
                raw = self.expand_tool(raw, marks, snapshot)
                numbered = isinstance(raw, dict) and isinstance(raw.get('action'), dict) and raw['action'].get('type') in ('click_target','open_target')
                raw = resolve_target(raw, marks)
                step = validate_step(raw, desktop, snapshot, goal_mode=self.goal_mode, screen=visual.get('screen'), snap=not numbered,
                                     text_regions=regions, catalog=self._catalog or (), goal=self.goal)
                step = self.file_interaction(step, marks, provider, cancel_kwargs, snapshot)
                check_active()
                if not numbered:  # a numbered target is on screen by definition
                    step = self.scroll_for_unseen(step, desktop, snapshot, regions, visual)
                step = self.app_click(step, desktop, snapshot, visual)
                step = self.address_typed(step, desktop, snapshot)
                if step['status'] == 'action' and step['action']['type'] == 'type_text' and self.goal_mode != 'inspect' and already_typed(step['action'].get('text', ''), getattr(self, 'typed', [])):
                    # The shopping list was typed, then typed again five times (live, v2): the model doesn't see it worked.
                    raise ValidationError('That text is already typed in this task. If the goal is done, report status=done; '
                                          'otherwise type only what is still missing.')
                if step['status'] == 'action':
                    self.repeat_check(step['action'])
                if step['status'] == 'action' and self.cycled(step['action']):
                    # Calculator: Show Sidebar, Hide Sidebar, Show Sidebar… (owner, live held-out). Whatever the
                    # buttons are called: the same click on a screen this task already left by that click.
                    raise ValidationError('You already clicked “'+step['action'].get('label', '')+'” on this same screen earlier, and it led '
                                          'back here. Do not repeat the cycle: if the screen shows what the goal asks for, report status=done '
                                          'and quote it; otherwise choose something different.')
                if step['status'] == 'action' and self.repeats(step['action']) >= 2:
                    raise ValidationError('“'+step['action'].get('label', step['action']['type'])+'” already ran twice in a row and the goal '
                        'did not progress. Do not repeat it. If the current screen already shows the requested information, '
                        'report it with status=done and quote the evidence; otherwise choose a different target.')
                if step['status'] == 'action' and self.receipts and step['action']['type'] != 'wait':
                    import hashlib
                    signature = (hashlib.sha256(image_data.encode()).hexdigest(), self.scroll_seen,
                                 json.dumps(raw['action'], sort_keys=True))
                    if self.last_proposal == signature:
                        unchanged_action = True
                        raise ValidationError('The same action was already submitted on this unchanged screen without observed progress. '
                                              'Choose another useful authorized method. Only report done if the FULL goal has current evidence; '
                                              'a target being absent is not completion.')
            except ValidationError as error:
                timing('task.step.invalid', inference_at, step=len(self.receipts)+1, attempt=attempt+1,
                       status_action_mismatch=isinstance(raw, dict) and raw.get('status') in ('done', 'needs_user') and raw.get('action') is not None,
                       action_type=str(((raw.get('action') if isinstance(raw, dict) else None) or {}).get('type', ''))[:20],
                       reason=str(error).split('“')[0][:90],  # code's own wording, cut before any quoted screen text
                       focused=str(snapshot.get('focused_role', ''))[:30], companion_focused=bool(snapshot.get('companion_focused')))
                if attempt:
                    answer = self.report_only(provider, messages, model_image or image_data, cancel_kwargs, check_active, snapshot, regions)
                    if answer is None and getattr(self, 'undo_refused', None):
                        # Twice the model wanted to click the box it had just switched. Nothing is wrong on
                        # screen: the completion check says whether the goal is reached, and the task ends
                        # as done or as "could not confirm", not in an error (review).
                        missing = self.still_missing(provider, snapshot, regions, cancel_kwargs)
                        check_active()
                        step = ({'status': 'done', 'action': None, 'message': 'I switched “'+self.undo_refused+'”.'} if not missing else
                                {'status': 'needs_user', 'action': None, 'message': 'I switched “'+self.undo_refused+'”, but could not confirm the rest: '+missing[:160]+'.'})
                        raw = dict(step, risk='navigation')
                        break
                    if answer is None and unchanged_action:
                        step = {'status': 'needs_user', 'action': None, 'message':
                                'The same action was proposed on an unchanged screen. I stopped rather than repeat an uncertain action.'}
                        raw = dict(step, risk='navigation')
                        break
                    if answer is None:
                        raise ValidationError('I could not find a safe next step, so nothing was done. ('+str(error)[:300]+')') from error
                    step, raw = answer, dict(answer, action=None)
                    break
                correction = ('The previous proposal was rejected before execution: '+str(error)[:500]+
                    (' No action from this rejected proposal has run.' if self.receipts else ' No action has run.')+
                    ' Re-examine the attached screenshot and return ONE corrected JSON object. '
                    'A click or other operation requires status=action and one non-null action. '
                    'If done or blocked, action MUST be null. Do not infer completion from a rejected proposal. '
                    'Use fractional image coordinates for clicks, or an exact current control_id. '
                    'Do not change the goal or bypass permission, login, sensitive-action or target checks.')
                continue
            if step['status'] == 'needs_user' and not attempt:
                # One reconsideration shares the repair budget; at most two
                # inference calls, and no unvalidated proposal is ever executed.
                correction = ('Recheck the proposed pause: '+step['message']+
                    '. A fresh screenshot is attached. A visible target without an accessibility ID '
                    'can use click_point at its center. Opening requested apps and websites is authorized. '
                    'Keep needs_user for missing permission, login, data, or an ambiguous/unsafe target; '
                    'action must be null. Do not ask the user to repeat their request.')
                timing('task.reconsider.start', inference_at, step=len(self.receipts)+1)
                continue
            if (step['status'] == 'done' and not attempt
                    and all(receipt.get('type') in OPENING_ACTIONS for receipt in self.receipts)):
                # Reported failures stopped right after an app or page opened. Opening is
                # not the outcome of a compound goal, so recheck once against the image.
                correction = ('Recheck the reported completion: '+step['message']+
                    '. So far only opening, focusing or navigation has run'+(' (nothing yet)' if not self.receipts else '')+
                    '. Is the FULL goal visibly achieved: the requested value, drafted text or information shown? '
                    'If more work is needed (pressing buttons, focusing an editor and typing, opening the '
                    'relevant view), return status=action with the next step. Only report done with concrete current evidence.')
                timing('task.reconsider.start', inference_at, step=len(self.receipts)+1, reason='opening_only_done')
                continue
            if step['status'] == 'done':
                # The model's own "done" is checked against current evidence: "Sorted by kind" without the list
                # view, a selected folder instead of an open one, or a read/navigation goal still on the
                # wrong page. A question in a compound goal does not waive its other requirements.
                # This holds whatever ran before and whatever was read: a done after nothing but opening,
                # or with no screen text at all, used to pass on the model's word. The window title
                # and native facts can still be quoted then; with nothing observable it is not done.
                missing = self.still_missing(provider, snapshot, regions, cancel_kwargs)
                check_active()
                if missing and not attempt:
                    correction = ('Not done yet: the screen does not show '+missing+'. Take the next step toward it: open the link, '
                                  'page, tab or folder where it would be, scroll to it, or use the app\'s menus and keyboard shortcuts; '
                                  'or report needs_user if it cannot be done.')
                    timing('task.reconsider.start', inference_at, step=len(self.receipts)+1, reason='missing')
                    continue
                if missing:
                    step = {'status': 'needs_user', 'action': None, 'message': 'I could not finish: '+missing[:200]+' is not on the screen.'}
            break
        if step['status'] == 'done' and self.goal_mode == 'inspect' and regions:
            check_active()
            answer = self.screen_answer(provider, snapshot, regions, step['message'], cancel_kwargs)
            check_active()  # access paused meanwhile: the screen's text is not shown
            step = dict(step, message=answer or step['message'])
        if step['status'] == 'action' and getattr(self, 'next_queue', None):
            # Only a validated first step starts a tool's queue; the rest stays in this app and
            # window, with the risk the model gave.
            self.queue = [dict(item, bundle=(snapshot.get('app') or {}).get('bundle_id'), window=(snapshot.get('window') or {}).get('title'),
                               risk=raw.get('risk', 'navigation')) for item in self.next_queue]
            self.next_queue = []
        elif step['status'] == 'action' and step['action']['type'] == 'type_text' and self.goal_mode == 'inspect' and search_field_focused(snapshot):
            # Typing a query into a search field searches: Enter follows without asking again.
            self.queue = [{'type': 'press_key', 'key': 'enter', 'bundle': (snapshot.get('app') or {}).get('bundle_id'),
                           'window': (snapshot.get('window') or {}).get('title'), 'risk': 'navigation'}]
        from .eval_control import step_shot
        step_shot(image_data, len(self.receipts)+1, step.get('action'), step['status'], observation=snapshot, screen=visual.get('screen'))
        if step['status'] != 'action':
            return finish({'status': step['status'], 'message': step['message'], 'action': None}, 'model_terminal')
        action, message = step['action'], step['message']
        import hashlib
        signature = (hashlib.sha256(image_data.encode()).hexdigest(), self.scroll_seen,
                     json.dumps(raw['action'], sort_keys=True))
        self.last_proposal = signature
        if action['type'] in ('click_point', 'drag'):
            action['capture_guard'] = vision.click_guard(image_data, visual, action)
        target = state_control(action, marks, snapshot, visual.get('screen'))
        if target:
            # How the checkbox, switch or tab looks now; check_last_click compares after the click.
            look = region_look(image_data, target['rect'])
            self.pending_look = {'rect': target['rect'], 'look': look} if look else None
        if action['type'] == 'drag':
            # Selecting text inside the focused field or document is only selecting; any other drag can move things.
            box, screen = snapshot.get('focused_bounds') or {}, visual.get('screen') or {}
            inside = lambda fx, fy: all(k in box for k in ('x', 'y', 'width', 'height')) and screen.get('width') and \
                box['x'] <= screen['x']+fx*screen['width'] <= box['x']+box['width'] and box['y'] <= screen['y']+fy*screen['height'] <= box['y']+box['height']
            action['selects_text'] = snapshot.get('focused_role') in ('AXTextArea', 'AXTextField') and bool(inside(action['x'], action['y'])) \
                and bool(inside(action['to_x'], action['to_y']))
        self.pending = action
        # Enter in a search field during a read-only goal is a search, not a submission: it ran
        # nowhere before (Wikipedia, live) because every Enter waited for a confirmation.
        review = not self.automatic or self.review_needed(action, step.get('risk', raw['risk']), snapshot, navigation_verified=step.get('settings_navigation',False))
        if not review:
            # Only a press that would otherwise run unconfirmed is asked about, and only a button's.
            review = self.commits(action, self.press_kind(action, marks, snapshot, desktop, visual), snapshot, provider, cancel_kwargs)
            check_active()
        return finish({'status': 'action', 'message': message, 'action': action, 'review': review, 'visual_capture': visual}, 'model_action')
