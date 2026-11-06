"""Offline, synthetic router corpus. No provider, app, or private store is accessed."""
import argparse
import ast
from collections import Counter
import json
import re
from pathlib import Path
from functools import lru_cache
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from companion.ollama import Planner
from companion.request_plan import OPERATIONS, validate_plan
from companion.service import Companion

OUT = ROOT / '.data/training/router'

class Capture(Planner):
    def __init__(self, label):
        self.label = label
        self.messages = None
    def complete(self, system, messages, **kwargs):
        self.messages = [dict(role='system', content=system)] + messages
        # Opening coverage is a separate classifier, not a router training row.
        if 'requires_followup_actions' in kwargs.get('response_schema', {}).get('properties', {}):
            return {'requires_followup_actions': False}
        self.captured = self.messages
        return self.label
    def complete_structured(self, system, messages, **kwargs):
        self.complete(system, messages, **kwargs)
        raise RuntimeError('Offline prompt capture complete')


def plan(op, target='', app='', **extra):
    value = dict(operation=op, app_name=app, target=target, browser_mode='default',
                 remaining_request='', goal_mode='inspect', reply='', when='', acts_on_computer=True)
    if op in ('inspect_screen', 'weather', 'look_up', 'inspect_media', 'system_info', 'read_calendar'):
        value['acts_on_computer'] = False
    if op == 'answer':
        value.update(acts_on_computer=False, reply='You’re welcome.')
    if op in ('type_text', 'create_note'):
        value['goal_mode'] = 'draft'
    if op in ('add_event', 'add_reminder', 'complete_reminder', 'delete_reminder', 'run_shortcut', 'system_control'):
        value['goal_mode'] = 'change'
    # Defaults cover these reviewed synthetic families, not a production router.
    value['referent'] = {
        'move_geometry': 'companion', 'move_object': 'companion',
        'highlight': 'visible_target', 'browser_open': 'destination',
        'browser_search': 'destination', 'open_file': 'destination',
        'launch_app': 'destination', 'focus_app': 'destination',
    }.get(op, 'other')
    value.update(extra)
    return validate_plan(value)


def messages(request, label, recent=(), context=None, last_task=None):
    provider = Capture(label)
    if last_task is None:
        provider.interpret_request(request, recent, context)
    else:
        app = object.__new__(Companion)
        app.provider, app.last_task = provider, last_task
        app.routed_in_code = lambda request: False
        app.resolve_follow_up(request)
    return provider.captured + [dict(role='assistant', content=json.dumps(label, ensure_ascii=False))]


@lru_cache(maxsize=1)
def harder_strings():
    """Conservative superset: exclude every literal, including f-string fragments."""
    tree = ast.parse('\n'.join((ROOT / 'scripts' / name).read_text(encoding='utf-8') for name in ('live_harder.py', 'live_features.py', 'live_capability.py')))
    return {' '.join(n.value.casefold().split()) for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value.strip()}

@lru_cache(maxsize=1)
def harder_patterns():
    """Also protect variable fixture names and concatenated live requests."""
    tree = ast.parse('\n'.join((ROOT / 'scripts' / name).read_text(encoding='utf-8') for name in ('live_harder.py', 'live_features.py', 'live_capability.py')))
    patterns = []
    def pattern(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return re.escape(' '.join(node.value.casefold().split()))
        if isinstance(node, ast.JoinedStr):
            return ''.join(pattern(v) if isinstance(v, ast.Constant) else '.*' for v in node.values)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return pattern(node.left)+pattern(node.right)
        return '.*'
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr) or (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id=='turn' and len(node.args)>1):
            expr = node.args[1] if isinstance(node, ast.Call) else node
            value = pattern(expr)
            if len(value.replace('.*','')) >= 15:
                patterns.append(re.compile(value))
    return patterns

def held_out_wording(wording):
    normalized = ' '.join(wording.casefold().split())
    return normalized in harder_strings() or any(p.fullmatch(normalized) for p in harder_patterns())

# Each target is an entire semantic task family; its eight surface forms stay together.
APPS = ['Terminal', 'Calculator', 'TextEdit', 'Safari', 'Preview']
TITLES = ['water the fern', 'buy rice', 'return the library book', 'check the bicycle', 'pack a lunch']

def seeds():
    for i in range(5):
        app, title = APPS[i], TITLES[i]
        specs = {
            'answer': (['thanks for your help', 'good morning', 'okay', 'hello Ally', 'bye for now'][i], '', ''),
            'inspect_screen': (['describe what is on my screen','what does this window say','can you explain this screen','tell me what is visible','read the screen for me'][i], '', ''),
            'move_object': (f'move yourself beside the {app} window', app, ''),
            'move_geometry': (f'move to the { ["left", "right", "top left", "bottom right", "center"][i]}', ['left','right','top left','bottom right','center'][i], ''),
            'computer_task': (f'find the { ["sound", "display", "battery", "wifi", "Bluetooth"][i]} settings', None, ''),
            'inspect_media': (['what is playing','which song is on','tell me the current track','what music is playing now','who is playing right now'][i], '', ''),
            'play_media': (f'play { ["Morning Light", "River Song", "Blue Sky", "Evening Walk", "Quiet Day"][i]} in Music', ['Morning Light','River Song','Blue Sky','Evening Walk','Quiet Day'][i], 'Music'),
            'type_text': (f'type {title}', title, ''),
            'press_key': (f'press { ["enter", "tab", "escape", "space", "backspace"][i]}', ['enter','tab','escape','space','backspace'][i], ''),
            'scroll': (['scroll down','scroll up','move the page down','move the page up','scroll further down'][i], ['down','up','down','up','down'][i], ''),
            'capture_once': (['take a screenshot once','capture the screen once','use your Capture screen button','take one screen capture','capture my screen one time'][i], '', ''),
            'click_point': (f'click the { ["Help link", "Search button", "File menu", "sidebar", "toolbar"][i]}', ['Help link','Search button','File menu','sidebar','toolbar'][i], ''),
            'browser_search': (f'search the web for {title}', title, ''),
            'browser_open': (f'open https://example.org/{i}', f'https://example.org/{i}', ''),
            'launch_app': (f'open {app}', '', app),
            'new_window': (f'open a new {app} window', '', app),
            'close_window': (f'close the {app} window', '', app),
            'close_tab': (f'close the current tab in { ["Safari", "Chrome", "Firefox", "Edge", "Brave"][i]}', '', ['Safari','Chrome','Firefox','Edge','Brave'][i]),
            'quit_app': (f'quit {app}', '', app),
            'focus_app': (f'switch to {app}', '', app),
            'press_control': (f'press observed accessibility control demo-control-{i}', f'demo-control-{i}', ''),
            'highlight': (f'highlight the {app} close button', f'{app} close button', app),
            'read_mail': (f'read my email number {i+1} counting from the newest', str(i+1), ''),
            'read_calendar': (f'what is on my calendar { ["today", "tomorrow", "this week", "on Monday", "next"][i]}', ['today','tomorrow','week','Monday','next'][i], ''),
            'add_event': (f'add a calendar event called {title}', title, ''),
            'read_reminders': (['list my reminders','show all reminders','what do I have to do today','read my reminders today','what tasks are on my reminders list'][i], ['all','all','today','today','all'][i], ''),
            'add_reminder': (f'remind me to {title}', title, ''),
            'complete_reminder': (f'mark the reminder {title} done', title, ''),
            'delete_reminder': (f'delete the reminder {title}', title, ''),
            'read_notes': (f'read my note titled {title}', title, ''),
            'create_note': (f'create a note titled {title} with text Bring a pencil', title+' | Bring a pencil', ''),
            'find_contact': (f'find the contact Test Person {i+1}', f'Test Person {i+1}', ''),
            'recent_files': (['list recent downloads','list recent desktop files','list recent documents','show my recent files','what did I download recently'][i], ['downloads','desktop','documents','','downloads'][i], ''),
            'open_file': (f'open the file sample-{i}.txt', f'sample-{i}.txt', ''),
            'read_page': (['summarize this page','read this web page','what is this page about','explain the current web page','give me a summary of this page'][i], '', ''),
            'list_shortcuts': (['list my shortcuts','what shortcuts do I have','show available shortcuts','which shortcuts can I run','read the list of shortcuts'][i], '', ''),
            'run_shortcut': (f'run the shortcut Demo {i}', f'Demo {i}', ''),
            'system_info': (['what macOS version is this','how much battery is left','how much storage is free','how much memory does this Mac have','what Mac model is this'][i], ['macos_version','battery','storage','memory','model'][i], ''),
            'system_control': (['set the volume to 30 percent','switch Bluetooth off','switch to light mode','silence the sound','switch Wi-Fi off'][i], ['volume | 30','bluetooth | off','appearance | light','mute | on','wifi | off'][i], ''),
            'weather': (f'what is the weather in { ["Oslo", "Lima", "Tokyo", "Dublin", "Perth"][i]}', ['Oslo','Lima','Tokyo','Dublin','Perth'][i], ''),
            'look_up': (f'what is { ["the current population of Iceland", "the latest lunar mission", "the current mayor of Oslo", "the latest chess championship result", "the current population of Peru"][i]}', ['the current population of Iceland','the latest lunar mission','the current mayor of Oslo','the latest chess championship result','the current population of Peru'][i], ''),
        }
        for op, (request, target, app) in specs.items():
            # Identical semantics share a family even across different seeds.
            family = f'{op}:{target or app or request}'.casefold()
            label = plan(op, request if target is None else target, app)
            if op == 'computer_task':  # All five seed families navigate to Settings panes.
                label['referent'] = 'destination'
            if op == 'read_reminders' and i in (2, 4):
                label['acts_on_computer'] = False
            if op == 'answer':
                label['reply'] = ['You’re welcome.', 'Good morning.', 'Okay.', 'Hello.', 'Bye for now.'][i]
            yield family, i, request, label


def build():
    rows, seen = [], set()
    blocked = harder_strings()
    forms = ['{}', 'please {}', 'could you {}', 'can you {} please', '{} please', 'hey Ally, {}', 'would you {} for me', 'pls {}']
    for family, i, request, label in seeds():
        # Split semantic families, never individual paraphrases.
        split = 'test' if i == 4 else 'valid' if i == 3 else 'train'
        for form in forms:
            wording = form.format(request)
            if held_out_wording(wording):
                continue
            key = (family, wording)
            if key in seen:
                continue
            seen.add(key)
            rows.append(dict(split=split, family=family, request=wording, kind='intent', plan=label,
                             source='handwritten synthetic task families',
                             messages=messages(wording, label, context={'observed_controls': [label['target']]} if label['operation']=='press_control' else None)))
    # Reviewed source expectations. Raw model plans deliberately precede code's
    # preserve_desktop_goal/with_opening transformations. No fixtures are executed.
    source_cases = [
        ('tests/test_request_plan.py', 'open up stremio please', 'launch_app', '', 'Stremio', {}),
        ('tests/test_reported_failures.py', "Mark where the left terminal's close button is", 'highlight', "left terminal close button", 'Terminal', {}),
        ('tests/test_reported_failures.py', 'Find the Help link and then click it', 'computer_task', None, '', {}),
        ('tests/test_reported_failures.py', 'hop over to the clock', 'move_object', 'clock', '', {}),
        ('tests/test_reported_failures.py', 'show me the battery health', 'computer_task', None, '', {}),
        ('tests/test_reported_failures.py', 'show me Battery in System Settings', 'computer_task', None, 'System Settings', {}),
        ('tests/test_reported_failures.py', 'are there any notifications that require my attention', 'computer_task', None, '', {}),
        ('tests/test_reported_failures.py', 'type hello', 'type_text', 'hello', '', {}),
        ('tests/test_reported_failures.py', 'where can I see the sites I visited? open it', 'computer_task', None, '', {}),
        ('scripts/live_eval.py', 'check if I have new notifications on Teams', 'computer_task', None, 'Teams', {}),
        ('scripts/live_eval.py', 'read my second last email', 'read_mail', '2', '', {}),
        ('scripts/live_eval.py', "what's on my calendar tomorrow", 'read_calendar', 'tomorrow', '', {}),
        ('scripts/live_eval.py', 'remind me to call mom at 6', 'add_reminder', 'call mom', '', {'when':'2030-05-20T18:00'}),
        ('scripts/live_eval.py', 'open youtube and search for lo-fi', 'browser_open', 'https://www.youtube.com/results?search_query=lo-fi', '', {}),
        ('scripts/live_eval.py', 'click the first email in Mail', 'click_point', 'first email', 'Mail', {}),
        ('scripts/live_eval.py', "what's on my screen", 'inspect_screen', '', '', {}),
        ('scripts/live_eval.py', 'what do I have to do today', 'read_reminders', 'today', '', {}),
        ('scripts/live_eval.py', 'open the calculator and add 12 and 30', 'launch_app', '', 'Calculator', {'remaining_request':'add 12 and 30'}),
    ]
    # Keep source regression families wholly held out, including exact live wordings.
    for source, wording, op, target, app, extra in source_cases:
        label = plan(op, wording if target is None else target, app, **extra)
        rows.append(dict(split='test', family=next((r['family'] for r in rows if r['kind']=='intent' and
                (r['plan']['operation'],r['plan']['target'],r['plan']['app_name']) == (op,label['target'],app)), 'regression:'+wording.casefold()),
            request=wording, kind='intent', source=source, plan=label,
            messages=messages(wording, label, context={'now':'2030-05-20T12:00', 'front_app':'Safari — Activity | Microsoft Teams'})))
    # Routed follow-ups train the intent stage with its real short history/context.
    for i in range(1):
        wording = 'what about tomorrow?'
        label = plan('read_calendar', 'tomorrow')
        rows.append(dict(split='test', family='calendar-followup', request=wording,
            kind='intent', source='scripts/live_eval.py', plan=label,
            messages=messages(wording, label, ["what's on my calendar today"], {'now':'2030-05-20T12:00'})))
    # Resolver has its own wire schema. Linked intent plans are validated separately.
    for i, title in enumerate(TITLES):
        for hour in (7, 8, 9, 10, 11):
            last = f'remind me to {title} tomorrow at 6'
            resolved = f'remind me to {title} tomorrow at {hour}'
            label = plan('add_reminder', title, when=f'2030-05-21T{hour:02}:00')
            for wording in (f'same thing at {hour}', f'no, at {hour} instead'):
                rows.append(dict(split='test' if i == 4 else 'valid' if i == 3 else 'train',
                    family=f'follow_up:{title}', request=wording, kind='follow_up', plan=label,
                    messages=messages(wording, {'request': resolved}, last_task=last)))
    for i, wording in enumerate(['thanks', 'hello', 'why is the sky blue', 'good night', 'how are you']):
        rows.append(dict(split='test' if i==4 else 'valid' if i==3 else 'train',
            family='follow_up_negative:'+wording, request=wording, kind='follow_up',
            plan=plan('answer'), source='handwritten synthetic conversation',
            messages=messages(wording, {'request':''}, last_task='open https://example.org')))
    # Per-example review for source requests; activating a control is not pointing it out.
    destinations = {
        'show me the battery health', 'show me Battery in System Settings',
        'where can I see the sites I visited? open it',
    }
    for row in rows:
        if row['kind'] != 'intent':
            continue
        label = dict(row['plan'])
        contextual_apps = {
            'Find the Help link and then click it': 'Teams',
            'are there any notifications that require my attention': 'Teams',
            'type hello': 'Safari',
        }
        if row['source'].startswith(('tests/', 'scripts/')) and row['request'] in contextual_apps:
            label['app_name'] = contextual_apps[row['request']]
        if row['request'] in destinations:
            label['referent'] = 'destination'
        if row['request'] == 'what do I have to do today':
            label['acts_on_computer'] = False
        # Wrapper forms of that same question also remain questions.
        if 'what do I have to do today' in row['request']:
            label['acts_on_computer'] = False
        row['plan'] = validate_plan(label)
        row['messages'][-1]['content'] = json.dumps(label, ensure_ascii=False)
    # Deduplicate families across splits conservatively.
    priority = {'train': 0, 'valid': 1, 'test': 2}
    assigned = {}
    for row in rows:
        assigned[row['family']] = max(assigned.get(row['family'], 'train'), row['split'], key=priority.get)
    # Low-cardinality operations (up/down; all/today) still need training
    # coverage. Keep one whole eligible family in train, another held out.
    for op in OPERATIONS:
        families = sorted({r['family'] for r in rows if r['kind']=='intent' and r['plan']['operation']==op})
        if not any(assigned[f]=='train' for f in families):
            eligible = [f for f in families if not any(
                held_out_wording(r['request']) for r in rows if r['family']==f)]
            if eligible:
                assigned[eligible[0]] = 'train'
        if len(families)>1 and not any(assigned[f]=='test' for f in families):
            assigned[next(f for f in reversed(families) if assigned[f]!='train')] = 'test'
    for row in rows:
        row['split'] = assigned[row['family']]
    return rows


def generate(out=OUT):
    rows = build()
    out.mkdir(parents=True, exist_ok=True)
    for split in ('train', 'valid', 'test'):
        with (out / f'{split}.jsonl').open('w', encoding='utf-8') as f:
            for row in rows:
                if row['split'] == split:
                    f.write(json.dumps({'messages': row['messages']}, ensure_ascii=False)+'\n')
    (out/'manifest.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False)+'\n' for r in rows), encoding='utf-8')
    stats = {s: dict(sorted(Counter(r['plan']['operation'] if r['kind']=='intent' else 'follow_up' for r in rows if r['split']==s).items())) for s in ('train','valid','test')}
    (out/'stats.json').write_text(json.dumps(stats, indent=2)+'\n')
    return stats

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=OUT)
    print(json.dumps(generate(parser.parse_args().output), indent=2))
