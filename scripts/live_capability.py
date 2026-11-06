"""Owner-authorized local capability tests: independent fixtures and long conversations.
Legacy diagnostic, not an acceptance gate; see docs/ai/TESTING.md

Build is pure. Each case owns its temporary files and keeps one app process across
all turns. Native state, not the assistant's claims, determines task success.
No external service, private application, sending or automatic write confirmation.
"""
from pathlib import Path
import re
import random
import shutil
import tempfile
import time
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import live_eval as le
from live_heldout import osa
from live_tasks import Task, answer, no_failure


def contains_answer(expected):
    def check(state, events):
        actual = answer(events)
        state.setdefault('_observed', {}).update(expected=expected, answer=actual)
        return expected.casefold() in actual.casefold()
    return check


def finder_at(path, state):
    actual = osa('''tell application "Finder"
if (count of Finder windows) is 0 then return ""
return POSIX path of (target of front Finder window as alias)
end tell''')
    state.setdefault('_observed', {})['folder'] = actual
    state['_observed']['front_app'] = le.front_app()
    return le.same_path(actual,path) and state['_observed']['front_app'] == 'Finder'


def selected_path(state):
    actual = osa('''tell application "Finder"
set chosen to get selection
if (count of chosen) is not 1 then return ""
return POSIX path of ((item 1 of chosen) as alias)
end tell''')
    state.setdefault('_observed', {})['selected_path'] = actual
    return actual


def action_seen(state, events, kind, **attributes):
    actions = [e.get('data', {}).get('action', {}) for e in events
               if e.get('kind') == 'timing' and e.get('data', {}).get('event') == 'eval.shot']
    state.setdefault('_observed', {})['actions'] = actions
    return any(a.get('type') == kind and all(a.get(k) == v for k, v in attributes.items()) for a in actions)


def build(window, count=0, seed=0):
    cases = []
    rng=random.Random(seed)
    marker = 'A11y test %d' % (time.time_ns() % 100000000)

    def turn(name, request, check, app='', setup=None):
        return Task(name, request, [('independent outcome', check), ('honest completion', no_failure)],
                    wanted_app=app, setup=setup, timeout=180)

    def fixture(name):
        state = dict(amber=rng.randint(11,39),violet=rng.randint(41,79),
                     destination=rng.choice(['Cedar ridge','Juniper pass','Birch valley','Copper summit']))
        def setup(runner):
            root = Path(tempfile.mkdtemp(prefix='a11y-capability-'))
            state['root'] = root
            (root/'Expedition'/'Supplies').mkdir(parents=True)
            (root/'Expedition'/'Supplies'/(marker+' manifest.txt')).write_text(
                f'Equipment inventory\nAmber packs: {state["amber"]}\nViolet packs: {state["violet"]}\nDestination: {state["destination"]}\n')
            for index in range(90):
                (root/('item%03d %s.txt' % (index, marker))).write_text('Fixture item %d\n' % index)
            osa('tell application "Finder" to activate')
            osa('tell application "System Events" to tell process "Finder" to key code 53')
            osa('''tell application "Finder"
make new Finder window to POSIX file (item 1 of argv)
set current view of front Finder window to list view
set sort column of list view options of front Finder window to name column
activate
end tell''', root)
        def cleanup():
            root = state.get('root')
            if root is None:
                return
            try:
                osa('''tell application "Finder"
repeat with w in Finder windows
try
if (POSIX path of (target of w as alias)) starts with ((item 1 of argv) & "/") then close w
end try
end repeat
end tell''', root)
                osa('''tell application "TextEdit"
repeat with d in documents
try
if (path of d) starts with ((item 1 of argv) & "/") then close d saving no
end try
end repeat
end tell''', root)
            finally:
                shutil.rmtree(root)
        case = dict(name=name, turns=[], setup=setup, cleanup=[cleanup], scoped_documents=True)
        cases.append(case)
        return state, case['turns']

    state, turns = fixture('finder_primitives')
    def selected_without_renaming(s,e):
        from companion.computer_task import finder_name_editor
        snapshot = le.desktop_sample()
        s.setdefault('_observed',{})['filename_editor'] = finder_name_editor(snapshot)
        return le.same_path(selected_path(s),state['root']/'Expedition') and not finder_name_editor(snapshot) and le.front_app()=='Finder'
    turns.append(turn('single_folder','Click Expedition.',selected_without_renaming,'Finder'))
    turns.append(turn('double_folder', 'Double-click Expedition in this Finder window.',
                      lambda s,e: finder_at(state['root']/'Expedition', s) and
                      action_seen(s,e,'click_point',click_count=2), 'Finder'))
    turns.append(turn('nested_folder', 'Continue into the Supplies subfolder.',
                      lambda s,e: finder_at(state['root']/'Expedition'/'Supplies', s), 'Finder'))
    turns.append(turn('double_document', f'Double-click {marker} manifest.txt so I can read it in TextEdit.',
                      lambda s,e: document_is(state, s) and action_seen(s,e,'click_point',click_count=2), 'TextEdit'))
    turns.append(turn('read_inventory', 'From that document, tell me the destination and how many packs there are altogether.',
                      lambda s,e: document_is(state, s) and inventory_answer(state,s,e), 'TextEdit'))

    state2, turns2 = fixture('finder_scroll_and_plan')
    turns2.append(turn('scroll_select', f'Scroll down this Finder list and select item089 {marker}.txt. Leave the file closed.',
                       lambda s,e: le.same_path(selected_path(s),state2['root']/('item089 '+marker+'.txt')) and
                       any(r.get('type') == 'scroll' for r in (s.get('task') or {}).get('receipts', [])), 'Finder'))
    turns2.append(turn('multi_level_plan', 'Open Expedition, then Supplies, then read the manifest there and tell me the destination. Work through the folders yourself.',
                       lambda s,e: contains_answer(state2['destination'])(s,e) and
                       (finder_at(state2['root']/'Expedition'/'Supplies', s) or document_is(state2,s)), 'Finder'))

    memory, sequence = fixture('long_conversation')
    code = 'cobalt-%d' % (time.time_ns() % 100000)
    sequence.append(turn('remember_fact', 'For this conversation, my expedition code is '+code+'. Remember it for later; do not write it anywhere.',
                         lambda s,e: bool(answer(e)) and not s.get('pending_action')))
    sequence.append(turn('first_task', 'Open the Expedition folder in this Finder window and leave it open.',
                         lambda s,e: finder_at(memory['root']/'Expedition',s), 'Finder'))
    # Ten intervening requests, including other real desktop tasks. The app is not
    # restarted, and the harness supplies no reminder of the earlier code or goal.
    for i in range(10):
        if i in (2,6):
            pane = 'Sound' if i == 2 else 'Displays'
            sequence.append(turn('intervening_pane_%d'%i,
                'Navigate to the '+pane+' pane in System Settings without changing anything.',
                lambda s,e,pane=pane: le.settings_pane_is(pane), 'System Settings'))
        else:
            a,b = 31+i,7+i
            sequence.append(turn('intervening_math_%d'%i, 'What is %d times %d?'%(a,b),
                                 contains_answer(str(a*b))))
    sequence.append(turn('distant_fact', 'What expedition code did I tell you near the beginning of this conversation?', contains_answer(code)))
    sequence.append(turn('distant_task', 'Return to the Finder folder I asked you to open near the beginning of this conversation.',
                         lambda s,e: finder_at(memory['root']/'Expedition',s), 'Finder'))
    sequence.append(turn('distant_extension', 'In that folder, continue into Supplies and read the manifest. What is the destination?',
                         lambda s,e: contains_answer(memory['destination'])(s,e), 'Finder'))
    sequence.append(turn('fact_after_task', 'Tell me my expedition code again, without opening anything.', contains_answer(code)))

    web = {}
    def web_setup(runner):
        nonce = str(time.time_ns() % 100000)
        web['token'] = 'Ridge '+nonce
        pages = {
            '/': '<h1>Expedition planning desk</h1><a href="/equipment">Equipment catalog</a>',
            '/equipment': '<h1>Equipment catalog</h1><p>Find the route guide below the inventory.</p>'+''.join(
                '<p style="height:90px">Equipment shelf %d</p>'%i for i in range(20))+
                '<a href="/guide">Route guide</a>',
            '/guide': '<h1>Route guide</h1><p>Collection point: '+web['token']+'</p>'+
                '<a href="/">Planning desk</a>',
        }
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):
                pass
            def do_GET(self):
                body = ('<!doctype html><meta charset="utf-8"><title>'+marker+'</title>'+
                        '<style>body{font:24px sans-serif;margin:40px}a{display:block;padding:20px}</style>'+
                        pages.get(self.path,'<h1>Missing fixture page</h1>')).encode()
                self.send_response(200 if self.path in pages else 404)
                self.send_header('Content-Type','text/html; charset=utf-8')
                self.send_header('Content-Length',str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
        web['server'] = server
        web['base'] = 'http://127.0.0.1:%d'%server.server_port
        threading.Thread(target=server.serve_forever,daemon=True).start()
        window.ensure(web['base']+'/')
    def web_cleanup():
        window.close()
        if web.get('server'):
            web['server'].shutdown()
            web['server'].server_close()
    def web_at(path,s):
        address = le.safari_front_tab()[0]
        s.setdefault('_observed',{})['url'] = address
        return address == web['base']+path and le.front_app()=='Safari'
    def guide_check(s,e):
        # The random fact is never in the request. A lucky destination alone is
        # insufficient: read the fixture's current content and report it correctly.
        return web_at('/guide',s) and contains_answer(web['token'])(s,e)
    cases.append(dict(name='local_web_plan',setup=web_setup,cleanup=[web_cleanup],scoped_documents=True,turns=[
        turn('catalog_navigation','Click Equipment catalog on this planning desk.',lambda s,e: web_at('/equipment',s),'Safari'),
        turn('scroll_and_read','Scroll through the equipment catalog, open its Route guide, and tell me the collection point.',
             lambda s,e: guide_check(s,e) and any(r.get('type')=='scroll' for r in (s.get('task') or {}).get('receipts',[])),'Safari'),
        turn('return_home','Use the Planning desk link to return to the start of this local website.',lambda s,e: web_at('/',s),'Safari'),
        turn('plan_again','Find the collection point again by going through the catalog and guide.',guide_check,'Safari'),
    ]))
    return cases


def document_is(fixture, state):
    # Only the front document's path is inspected, never the owner's document text.
    if 'TextEdit' not in le.app_names():
        return False
    path = osa('tell application "TextEdit" to return path of front document')
    state.setdefault('_observed', {})['document_path'] = path
    return le.front_app()=='TextEdit' and le.same_path(path,fixture['root']/'Expedition'/'Supplies'/next(
        p.name for p in (fixture['root']/'Expedition'/'Supplies').iterdir()))


def inventory_answer(fixture,state,events):
    expected=dict(destination=fixture['destination'],total_packs=fixture['amber']+fixture['violet'])
    state.setdefault('_observed',{})['inventory_expected']=expected
    return contains_answer(expected['destination'])(state,events) and bool(
        re.search(r'\b'+str(expected['total_packs'])+r'\b',answer(events)))
