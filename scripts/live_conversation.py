"""Opt-in multi-turn live cases. No app restart or cleanup between turns.
Legacy diagnostic, not an acceptance gate; see docs/ai/TESTING.md"""
from datetime import datetime, timedelta
from html.parser import HTMLParser
import json
import re
import subprocess
import time

import live_eval as le
from live_tasks import Task, answer, installed
from live_heldout import osa


def task_started(events):
    return any(e['kind'] == 'timing' and e['data'].get('event', '').startswith('task.') for e in events)


def denies_control(reply):
    return bool(re.search(r"\b(?:can't|cannot|unable|can’t)\b[^.!?]*\b(?:computer|control)\b", reply, re.I))


def reminder_times(items, day, hours):
    dates = [datetime.fromtimestamp(float(r['due'])) for r in items if r.get('due')]
    return len(items) == len(hours) and sorted((d.date(), d.hour, d.minute) for d in dates) == sorted((day, h, 0) for h in hours)


def page(host, app='Safari'):
    def check(s, e):
        address = osa('tell application "Google Chrome" to return URL of active tab of front window') if app != 'Safari' else le.safari_front_tab()[0]
        from urllib.parse import urlparse
        s.setdefault('_observed', {})['url'] = address
        actual = (urlparse(address).hostname or '').casefold()
        return actual == host.casefold() or actual.endswith('.'+host.casefold())
    return check



def note_words(body):
    class Text(HTMLParser):
        def __init__(self):
            super().__init__()
            self.parts = []
        def handle_data(self, data):
            self.parts.append(data)
    parser = Text()
    parser.feed(body)
    return ' '.join(parser.parts)

def notes(marker):
    return osa('tell application "Notes" to return body of every note whose name starts with (item 1 of argv)', marker)



def parse_reminders(output):
    items = []
    for line in output.splitlines():
        parts = line.split('\t')
        if len(parts) not in (2, 7):
            raise ValueError('invalid test reminder row')
        item = {'id': parts[0], 'title': parts[1]}
        if len(parts) == 7:
            item['due'] = datetime(*map(int, parts[2:])).timestamp()
        items.append(item)
    return items


def test_reminders(marker):
    # The bridge bounds its general inventory at 60; query only this case's items.
    return parse_reminders(osa('''tell application "Reminders"
set output to ""
repeat with folder in lists
    repeat with r in (every reminder of folder whose name contains (item 1 of argv))
        set rowText to (id of r as text) & tab & (name of r as text)
        set d to due date of r
        if d is not missing value then
            set rowText to rowText & tab & (year of d as text) & tab & (month of d as integer as text) & tab & (day of d as text) & tab & (hours of d as text) & tab & (minutes of d as text)
        end if
        set output to output & rowText & linefeed
    end repeat
end repeat
return output
end tell''', marker))

def clean_items(marker, kind='notes'):
    # The per-case marker (a unique long number) anywhere in the name: a whole-request title
    # ("Put A11y test … in Reminders for …") was left behind by a prefix match. Never sweeps other items.
    if kind == 'notes':
        osa('tell application "Notes" to delete (every note whose name contains (item 1 of argv))', marker)
        return
    osa('''tell application "Reminders"
repeat with folder in lists
    delete (every reminder of folder whose name contains (item 1 of argv))
end repeat
end tell''', marker)


def unchanged(before):
    def check(s, e):
        return bool(answer(e)) and not task_started(e) and fingerprint() == before['value']
    return check


def fingerprint():
    return (tuple(sorted(le.app_names())), le.front_app(), le.window_title(le.front_app()), le.safari_front_tab() if le.running('Safari') else None)


def build(window, count=8, seed=0):
    cases = []
    def turn(name, request, check, **kw):
        return Task(name, request, [('real Mac state', check)], **kw)
    def case(name, turns, setup=None, cleanup=()):
        cases.append(dict(name=name, turns=turns, setup=setup, cleanup=cleanup))
    web = [turn('safari', 'open Safari and go to example.com', page('example.com')),
           turn('other_site', 'now do the same with wikipedia.org', page('wikipedia.org'))]
    if 'Google Chrome' in installed():
        web.append(turn('chrome', 'do that again in Chrome', page('wikipedia.org', 'Google Chrome')))
    def browser_setup(a):
        window.ensure('https://www.iana.org/')
        if len(web) == 3:
            osa('tell application "Google Chrome" to make new window')
            window.ensure('https://www.iana.org/')
    case('browser_followups', web, browser_setup)
    if len(web) == 2:
        cases[-1]['skipped'] = 'Chrome follow-up: Google Chrome is not installed'
    case('settings_followup', [turn('sound', 'open the Sound pane in System Settings', lambda s,e: le.settings_pane_is('Sound')),
                              turn('displays', 'now do it for Displays', lambda s,e: le.settings_pane_is('Displays'))], lambda a: le.activate('Finder'))
    marker = 'A11y test %d' % (time.time_ns() // 1000)
    note_state = {}
    def note_ids():
        return set(osa('tell application "Notes" to return id of every note').split(', ')) - {''}
    def note_setup(a):
        note_state.clear()
        note_state['initial'] = note_ids()
        note_state['owned'] = set()
        le.activate('Notes')
    def first_note(s,e):
        ids = (note_ids() - note_state['initial']) & (set(osa('tell application "Notes" to return id of every note whose name starts with (item 1 of argv)', marker).split(', ')) - {''})
        note_state['owned'].update(ids)
        return bool(re.search(r'\bhi\b', note_words(notes(marker))))
    def before_bye(a):
        note_state['before_bye'] = note_ids()
    def second_note(s,e):
        ids = note_ids() - note_state['before_bye']
        s.setdefault('_observed', {})['new_note_ids'] = sorted(ids)
        if len(ids) != 1:
            return False
        body = osa('tell application "Notes" to return body of note id (item 1 of argv)', next(iter(ids)))
        s['_observed']['new_note_text'] = note_words(body)
        found = bool(re.search(r'\bbye\b', note_words(body)))
        if found:
            note_state['owned'].update(ids)
        return found
    def note_cleanup():
        markers = set(osa('tell application "Notes" to return id of every note whose name starts with (item 1 of argv)', marker).split(', ')) - {''}
        note_state['owned'].update(markers - note_state['initial'])
        for nid in note_state.get('owned', set()):
            osa('tell application "Notes" to delete note id (item 1 of argv)', nid)
    case('two_notes', [turn('hi', f'make a note titled {marker} saying hi', first_note),
                       turn('bye', 'and another one saying bye', second_note, setup=before_bye)],
         note_setup, [note_cleanup])
    reminder = marker+'1'
    def due(hour, total):
        def check(s,e):
            items = test_reminders(reminder)
            tomorrow = (datetime.now()+timedelta(days=1)).date()
            s.setdefault('_observed', {}).update(reminders=items, expected_day=str(tomorrow),
                                                expected_hours=[hour] if total == 1 else [9, hour])
            return reminder_times(items, tomorrow, [hour] if total == 1 else [9, hour])
        return check
    case('two_reminders', [turn('nine', f'add a reminder {reminder} for tomorrow at 9am', due(9,1)),
                           turn('ten', 'same for 10am', due(10,2))], lambda a: le.activate('Reminders'), [lambda: clean_items(reminder, 'reminders')])
    from urllib.parse import unquote
    def wiki(topic):
        return lambda s,e: page('en.wikipedia.org')(s,e) and topic.casefold() in unquote(le.safari_front_tab()[0]).casefold()
    case('wikipedia_followup', [turn('bucharest', 'search Wikipedia for Bucharest', wiki('Bucharest')),
                                turn('cluj', 'now for Cluj', wiki('Cluj'))], lambda a: window.ensure())
    for name, request in [('thanks', 'thanks, that was quick'), ('why', 'why did you open that?'), ('correction', "you didn't do it")]:
        before = {}
        def first(s,e, before=before):
            ok = page('example.com')(s,e)
            before['value'] = fingerprint()
            before['done'] = ok
            return ok
        check = unchanged(before)
        if name == 'correction':
            check = lambda s,e: before.get('done', False) and bool(answer(e)) and not denies_control(answer(e)) and page('example.com')(s,e)
        case(name, [turn('open', 'open Safari and go to example.com', first), turn(name, request, check)], lambda a: window.ensure('https://www.iana.org/'))
    return cases


def run(app, window, out, cases, runs=1):
    """Also used by broad: restricted cleanup, and finally even after setup/check errors."""
    records = []
    try:
        for case in cases:
            if case.get('skipped'):
                print('skip:', case['skipped'], flush=True)
            for attempt in range(1, runs+1):
                # Each source process starts its screenshot counter at one.
                # A separate case directory keeps evidence from being overwritten.
                app.shots = out/'shots'/('%s-%d' % (case['name'], attempt))
                app.launch()
                le.park_test_pointer(app)
                before_apps = le.app_names()
                before = {a: le.window_ids(a) for a in le.WINDOW_APPS if a in before_apps}
                empty_windows = []
                for name in SAFE_APPS & before_apps - set(le.WINDOW_APPS):
                    if osa('tell application "System Events" to tell process (item 1 of argv) to return count of windows', name) == '0':
                        empty_windows.append(name)
                started = time.time()
                completed = 0
                try:
                    if case.get('setup'):
                        case['setup'](app)
                    for index, turn in enumerate(case['turns'], 1):
                        if turn.setup:
                            turn.setup(app)
                        start = time.monotonic()
                        state, events, timeout = app.run(turn.request, turn.timeout)
                        state['_observed'] = {}
                        confirmation_error = ''
                        if (state.get('pending_action') or {}).get('type') == 'app_write':
                            cursor = app.call({'cmd': 'state', 'since': 10**9})['event_count']
                            try:
                                app.call({'cmd': 'confirm'})  # socket enforces A11y test marker
                                state, more, extra_timeout = app.wait(45, cursor)
                                events += more
                                timeout |= extra_timeout
                            except RuntimeError as error:
                                confirmation_error = str(error)
                        seconds = round(time.monotonic()-start,2)
                        checks = [('confirmation: '+confirmation_error, False)] if confirmation_error else []
                        for label, fn in turn.checks:
                            try:
                                checks.append((label, bool(fn(state, events))))
                            except Exception as error:
                                state['_observed'][label+' error'] = str(error)
                                checks.append((label+': '+str(error)[:160], False))
                        record = dict(case=case['name'], turn=index, attempt=attempt, request=turn.request,
                                      passed=all(ok for _,ok in checks) and not timeout, seconds=seconds,
                                      reply=answer(events), task_started=task_started(events), checks=checks, timed_out=timeout,
                                      observed=state.get('_observed', {}))
                        record['plan'] = state.get('plan', {})
                        record['task_status'] = (state.get('task') or {}).get('status')
                        record['pending_action'] = state.get('pending_action')
                        record['family'] = turn.family
                        record['observation'] = state.get('observation',{})
                        record['events'] = events
                        if state.get('_observed', {}).get('skipped'):
                            record['status'] = 'skipped'
                            record['skip_reason'] = state['_observed']['skipped']
                        elif state.get('_observed', {}).get('needs_owner'):
                            record['status'] = 'needs_owner'
                        # Keep the executed actions, not just timing events, so a failed
                        # check can be distinguished from a failed task after cleanup.
                        record['receipts'] = (state.get('task') or {}).get('receipts', [])
                        records.append(record)
                        completed = index
                        with (out/'runs.jsonl').open('a') as f:
                            f.write(json.dumps(record, ensure_ascii=False)+'\n')
                        (out/'summary.json').write_text(json.dumps(records, indent=2, ensure_ascii=False))
                        print(record['passed'], case['name'], index, record['seconds'], flush=True)
                except Exception as error:
                    for index, turn in enumerate(case['turns'][completed:], completed+1):
                        record = dict(case=case['name'], turn=index, attempt=attempt, request=turn.request,
                                      passed=False, seconds=None, reply='', task_started=False, error=str(error))
                        records.append(record)
                        with (out/'runs.jsonl').open('a') as f:
                            f.write(json.dumps(record)+'\n')
                finally:
                    try:
                        app.call({'cmd': 'stop'})
                    except RuntimeError:
                        pass
                    for cleanup in case.get('cleanup', ()):
                        try:
                            cleanup()
                        except Exception as error:
                            records.append(dict(case=case['name'], passed=False, cleanup_error=str(error)))
                    window.close()
                    # A single native inventory instead of one AppleScript process per
                    # possible app. On the host each launch cost several seconds.
                    now_apps = le.app_names()
                    # Only close windows created by these suites, never inspect private apps.
                    for name in ('Safari', 'Google Chrome', 'Finder', 'TextEdit', 'Preview'):
                        if name in now_apps:
                            for wid in le.window_ids(name)-before.get(name,set()):
                                try:
                                    script = 'tell application (item 1 of argv) to close (window id (item 2 of argv as integer))'
                                    if name in ('TextEdit', 'Preview'):
                                        script += ' saving no'
                                    osa(script, name, str(wid))
                                except RuntimeError:
                                    pass
                    for name in empty_windows:
                        try:
                            osa('''tell application "System Events" to tell process (item 1 of argv)
repeat with w in windows
    try
        click (first button of w whose subrole is "AXCloseButton")
    end try
end repeat
end tell''', name)
                        except (RuntimeError, subprocess.TimeoutExpired) as error:
                            records.append(dict(case=case['name'], passed=False, cleanup_error=str(error)))
                    for name in (SAFE_APPS - before_apps) & now_apps:
                        try:
                            osa('tell application (item 1 of argv) to quit saving no', name)
                        except (RuntimeError, subprocess.TimeoutExpired) as error:  # an app that won't quit must not end the run
                            records.append(dict(case=case['name'], passed=False, cleanup_error=str(error)))
                    if not case.get('scoped_documents') and any(t.wanted_app == 'TextEdit' for t in case['turns']):
                        le.trash_test_documents(started)
                    app.quit()
    finally:
        app.quit()
        window.close()
    (out/'summary.json').write_text(json.dumps(records, indent=2, ensure_ascii=False))


SAFE_APPS = {'Calculator', 'Clock', 'Calendar', 'Maps', 'Dictionary', 'Preview', 'System Settings',
             'TextEdit', 'Notes', 'Reminders', 'Safari', 'Google Chrome', 'Weather', 'Stocks', 'Font Book', 'Activity Monitor'}
