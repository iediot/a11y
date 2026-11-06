"""Opt-in held-out extensions of measured passes; never run during unit tests.
Legacy diagnostic, not an acceptance gate; see docs/ai/TESTING.md

Thirty turns, using broad's setup/Task/check/cleanup case format and conversation's
finally cleanup runner. See live_harder_tasks.md for the evidence and task manifest.
Build is pure: all native calls and fixture creation happen in setup/check/cleanup.
"""
from datetime import datetime, timedelta
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from urllib.parse import urlparse

import live_eval as le
from live_tasks import Task, answer, no_failure
from live_heldout import osa
from live_broad import menu_mark
from live_conversation import clean_items, note_words, test_reminders


def exact_note(title, body, state=None):
    """Exact title and complete visible body, including the title Notes prepends."""
    count = osa('tell application "Notes" to return count of (every note whose name is (item 1 of argv))', title)
    observed = (state if state is not None else {}).setdefault('_observed', {})
    observed['note_count'] = count
    if count != '1':
        observed['note_html'] = osa('tell application "Notes" to return body of every note whose name is (item 1 of argv)', title)
        return False
    actual = osa('tell application "Notes" to return body of first note whose name is (item 1 of argv)', title)
    observed['note_html'] = actual
    observed['note_text'] = note_words(actual)
    return ' '.join(note_words(actual).split()) == ' '.join((title+' '+body).split())


def at_url(host, path=None):
    def check(s, e):
        address, title = le.safari_front_tab()
        s.setdefault('_observed', {}).update(url=address, title=title)
        parsed = urlparse(address)
        return (parsed.hostname in (host, 'www.'+host) and
                (path is None or parsed.path.rstrip('/') == path.rstrip('/')))
    return check


def dismiss_test_alerts():
    """TextEdit answers no Apple Event while an alert is open. An earlier case's deleted fixture
    ("The document “A11y test … packing.txt” could not be opened") blocked every later TextEdit setup
    (30 s timeouts all day), and each timed-out open queued another alert. Only alerts naming an
    A11y test file are dismissed; anything else stops the loop."""
    if 'TextEdit' not in le.app_names():
        return
    for _ in range(12):
        try:
            text = osa('tell application "System Events" to tell process "TextEdit" to get value of static text 1 of window 1')
        except Exception:  # noqa: BLE001 - no window, or no alert
            return
        if not re.search(r'“A11y test [^”]*” could not be opened', text or ''):
            return
        osa('tell application "System Events" to tell process "TextEdit" to click button "OK" of window 1')
        time.sleep(0.5)


def textedit_at(path, state=None):
    """TextEdit returns a POSIX string; coercing that string to an alias can fail."""
    actual=osa('tell application "TextEdit" to return path of front document')
    if state is not None:
        state.setdefault('_observed',{})['document_path']=actual
    return le.same_path(actual,path)


def build(window, count=30, seed=0):
    cases = []
    marker = 'A11y test %d' % time.time_ns()

    def turn(name, request, app, check):
        return Task(name, request, [('real Mac state', check), ('no failure', no_failure)], wanted_app=app)

    def case(name, turns, setup, cleanup, extends):
        cases.append(dict(name=name, turns=turns, setup=setup, cleanup=list(cleanup),
                          extends=extends, scoped_documents=True))

    def settings_setup(r):
        subprocess.run(['open', 'x-apple.systempreferences:com.apple.GeneralSettings'], check=True, timeout=20)

    def dismiss_menu():
        osa('tell application "System Events" to key code 53')

    # Six turns: indirect wording, a lower pane, and contextual follow-ups.
    for name, first, second, request, follow in [
        ('settings_input', 'Keyboard', 'Sound', 'Take me to the place in System Settings for changing how the keyboard behaves.', 'And now the pane for sound, without changing any values.'),
        ('settings_wireless', 'Bluetooth', 'Wi-Fi', 'I need to see where wireless accessories are managed in System Settings; just show the pane.', 'Now show the Wi-Fi pane there, leaving the connection alone.')]:
        case(name, [turn(name+'_first', request, 'System Settings', lambda s,e,p=first: le.settings_pane_is(p)),
                    turn(name+'_follow', follow, 'System Settings', lambda s,e,p=second: le.settings_pane_is(p))],
             settings_setup, [dismiss_menu], 'broad settings_keyboard/bluetooth/wi-fi; conversation settings_followup')
    for name, pane, request in [
        ('settings_reading', 'Accessibility', 'In System Settings, take me down to Accessibility so I can look at the reading options. Leave everything as it is.'),
        ('settings_look', 'Appearance', 'Show me where the light and dark appearance choices live in System Settings, without switching either one.')]:
        case(name, [turn(name, request, 'System Settings', lambda s,e,p=pane: le.settings_pane_is(p))],
             settings_setup, [dismiss_menu], 'broad settings_appearance; navigation Battery/Bluetooth')

    # Isolated Finder fixture; never browse or sort the owner's real folders.
    for index, (name, kind) in enumerate([('finder_nested', 'nested'), ('finder_sort', 'sort'),
                                          ('finder_info', 'info'), ('finder_read', 'read')]):
        state = {}
        folder_name = marker+str(index)+' orchard'
        def setup(r, state=state, folder_name=folder_name):
            root = Path(tempfile.mkdtemp(prefix='a11y-harder-'))
            state['root'] = root
            folder = root/folder_name
            folder.mkdir()
            (folder/'Recipes').mkdir()
            (folder/'Recipes'/'Saturday').mkdir()
            (folder/(marker+' packing.txt')).write_text('A11y test 314159\nPack the amber lantern.\n')
            state['folder'] = folder
            osa('tell application "Finder" to make new Finder window to (POSIX file (item 1 of argv) as alias)', str(folder))
            state['wid'] = osa('tell application "Finder" to return id of front Finder window')
            le.activate('Finder')
        def cleanup(state=state):
            try:
                if state.get('wid'):
                    osa('tell application "Finder" to close (every Finder window whose id is (item 1 of argv as integer))', state['wid'])
            finally:
                if state.get('root'):
                    shutil.rmtree(state['root'])
        def target(state=state):
            return osa('tell application "Finder" to return POSIX path of (target of front Finder window as alias)').rstrip('/')
        if kind == 'nested':
            request = 'In this Finder folder, go into Recipes and then Saturday.'
            check = lambda s,e,state=state,target=target: le.same_path(target(),state['folder']/'Recipes'/'Saturday')
        elif kind == 'sort':
            request = 'Put this Finder folder into list view and arrange its items by name.'
            def check(s,e,state=state,target=target):
                values = dict(finder_target=target(), finder_view=osa('tell application "Finder" to return current view of front Finder window'),
                              finder_column=osa('tell application "Finder" to return name of sort column of list view options of front Finder window'))
                s.setdefault('_observed', {}).update(values)
                predicates = dict(target_matches=le.same_path(values['finder_target'],state['folder']),
                                  list_view=values['finder_view'] == 'list view', name_column=values['finder_column'].casefold() in ('name', 'name column'))
                s['_observed'].update(predicates)
                return all(predicates.values())
        elif kind == 'info':
            request = 'Show the information window for this orchard folder in Finder, so I can see its details.'
            check = lambda s,e,folder_name=folder_name: osa('tell application "System Events" to tell process "Finder" to return exists window (item 1 of argv)', folder_name+' Info') == 'true'
            def close_info(folder_name=folder_name):
                osa('''tell application "System Events" to tell process "Finder"
if exists window (item 1 of argv) then click (first button of window (item 1 of argv) whose subrole is "AXCloseButton")
end tell''', folder_name+' Info')
            # Close the info window before removing its folder.
            case(name, [turn(name, request, 'Finder', check)], setup, [close_info, cleanup], 'broad finder_folder_info')
            continue
        else:
            request = f'Open the file called {marker} packing.txt from this Finder folder and tell me what it says to pack.'
            def check(s,e,state=state):
                path = state['folder']/(marker+' packing.txt')
                return path.read_text() == 'A11y test 314159\nPack the amber lantern.\n' and textedit_at(path,s) and 'amber lantern' in answer(e).casefold()
        case(name, [turn(name, request, 'Finder', check)], setup, [cleanup],
             'v3 Documents sort/date added; broad finder_downloads; v2 TextEdit typing')

    def web_setup(r):
        window.ensure('https://example.org/')
    def web_cleanup():
        window.close()
    case('web_reference', [
        turn('web_rfc', 'In Safari, take me to rfc-editor.org/rfc/rfc2606 and tell me the RFC number shown there.', 'Safari',
             lambda s,e: (at_url('rfc-editor.org', '/info/rfc2606')(s,e) or at_url('rfc-editor.org', '/rfc/rfc2606')(s,e)) and bool(re.search(r'\b2606\b', answer(e)))),
        turn('web_reference_follow', 'Now use that same Safari window to visit iana.org/domains/reserved.', 'Safari', at_url('iana.org', '/domains/reserved'))],
        web_setup, [web_cleanup], 'broad safari_public_page; conversation browser_followups')
    case('web_apple', [
        turn('web_apple_support', 'Start at apple.com in Safari, then take me to its Support page.', 'Safari', lambda s,e: at_url('support.apple.com')(s,e)),
        turn('web_apple_follow', 'In that window, go to apple.com and find the iPad page instead.', 'Safari', at_url('apple.com', '/ipad'))],
        web_setup, [web_cleanup], 'v3 Apple Support; v2 Apple iPhone; conversation browser_followups')
    case('web_screen_answer', [turn('web_screen_answer', 'Visit example.org in Safari and read me the heading on that page.', 'Safari',
         lambda s,e: at_url('example.org', '/')(s,e) and le.safari_front_tab()[1] == 'Example Domain' and 'example domain' in answer(e).casefold())],
         web_setup, [web_cleanup], 'broad safari_title (broad-2); requests screen inspection')
    case('web_lower_section', [turn('web_lower_section', 'Open python.org in Safari, then follow the link to the downloads page.', 'Safari', at_url('python.org', '/downloads'))],
         web_setup, [web_cleanup], 'v2 Apple iPhone; v3 Apple Support')

    # The tiny PDF fixture is generated without a new dependency.
    pdf = {}
    def preview_setup(r):
        root = Path(tempfile.mkdtemp(prefix='a11y-harder-'))
        pdf['root'] = root
        path = root/(marker+' atlas.pdf')
        pdf['path'] = path
        stream = ('BT /F1 14 Tf 20 250 Td ('+marker+') Tj ET').encode('ascii')
        objects = [b'<< /Type /Catalog /Pages 2 0 R >>',
                   b'<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>',
                   b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] /Resources << /Font << /F1 5 0 R >> >> /Contents 6 0 R >>',
                   b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] /Resources << /Font << /F1 5 0 R >> >> /Contents 6 0 R >>',
                   b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
                   ('<< /Length %d >>\nstream\n' % len(stream)).encode()+stream+b'\nendstream']
        data, offsets = b'%PDF-1.4\n', [0]
        for i, obj in enumerate(objects, 1):
            offsets.append(len(data))
            data += str(i).encode()+b' 0 obj\n'+obj+b'\nendobj\n'
        xref = len(data)
        data += b'xref\n0 7\n0000000000 65535 f \n'
        data += b''.join(('%010d 00000 n \n' % n).encode() for n in offsets[1:])
        data += ('trailer\n<< /Size 7 /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n' % xref).encode()
        path.write_bytes(data)
        subprocess.run(['open', '-a', 'Preview', str(path)], check=True, timeout=20)
        time.sleep(1)
        osa('tell application "System Events" to tell process "Preview" to click menu item "Hide Sidebar" of menu 1 of menu bar item "View" of menu bar 1')
    def preview_check(s,e):
        return pdf['path'].name in (le.window_title('Preview') or '') and menu_mark('Preview', 'View', 'Thumbnails')
    def preview_cleanup():
        try:
            if pdf.get('path'):
                osa('tell application "Preview" to close (every window whose name contains (item 1 of argv))', pdf['path'].name)
        finally:
            if pdf.get('root'):
                shutil.rmtree(pdf['root'])
    case('preview_sidebar', [turn('preview_sidebar', 'In Preview, bring back the little page pictures on the left of this two-page document.', 'Preview', preview_check),
         turn('preview_name', 'Keep those thumbnails showing and tell me the filename of this PDF.', 'Preview', lambda s,e: preview_check(s,e) and pdf['path'].name in answer(e))],
         preview_setup, [preview_cleanup], 'broad preview_thumbnails')

    # Existing saved, isolated plain-text documents: cleanup never sweeps iCloud.
    for i, content in enumerate(['A11y test 271828\nSaturday picnic\nBring pears and a blanket.', 'A11y test 161803\nThe spare umbrella is beside the boots.']):
        state = {}
        name = 'textedit_'+('lines' if i == 0 else 'sentence')
        def setup(r,state=state,name=name):
            root = Path(tempfile.mkdtemp(prefix='a11y-harder-'))
            state['root'] = root
            state['path'] = root/(marker+' '+name+'.txt')
            state['path'].write_text('')
            dismiss_test_alerts()
            osa('tell application "TextEdit" to open POSIX file (item 1 of argv)', str(state['path']))
            le.activate('TextEdit')
        def check(s,e,state=state,content=content):
            return textedit_at(state['path'],s) and osa('tell application "TextEdit" to return text of front document').strip() == content
        def cleanup(state=state):
            try:
                if state.get('path'):
                    osa('''tell application "TextEdit"
repeat with d in documents
    if (path of d as text) is (item 1 of argv) then close d saving no
end repeat
end tell''', str(state['path']))
            finally:
                if state.get('root'):
                    shutil.rmtree(state['root'])
        case(name, [turn(name, 'Type the following exactly into this empty TextEdit file, keeping the line breaks:\n'+content, 'TextEdit', check)],
             setup, [cleanup], 'v2 TextEdit sentence; broad textedit_multiline (proposal check replaced)')

    for kind in ('notes', 'reminders'):
        titles = [marker+('80' if kind == 'notes' else '90')+str(i) for i in range(2)]
        app = 'Notes' if kind == 'notes' else 'Reminders'
        def cleanup_item(title, kind):
            return lambda: clean_items(title, kind)
        if kind == 'notes':
            turns = [turn('note_picnic', f'Please make a note called {titles[0]} with exactly this body: Bring the green blanket and two pears.', app,
                          lambda s,e,title=titles[0]: exact_note(title, 'Bring the green blanket and two pears.', s)),
                     turn('note_follow', f'Make another note like that, titled {titles[1]}, but with exactly this body: Leave the basket by the door.', app,
                          lambda s,e,title=titles[1]: exact_note(title, 'Leave the basket by the door.', s))]
        else:
            def reminder_check(title, hour):
                def check(s,e):
                    rows = test_reminders(title)
                    day = (datetime.now()+timedelta(days=1)).date()
                    return len(rows) == 1 and rows[0]['title'] == title and bool(rows[0].get('due')) and datetime.fromtimestamp(rows[0]['due']) == datetime.combine(day, datetime.min.time()).replace(hour=hour)
                return check
            turns = [turn('reminder_morning', f'Put {titles[0]} in Reminders for tomorrow at 8 in the morning.', app, reminder_check(titles[0], 8)),
                     turn('reminder_follow', f'Add another for tomorrow at 2 in the afternoon, this time called {titles[1]}.', app, reminder_check(titles[1], 14))]
        case(kind+'_pair', turns, lambda r,app=app: le.activate(app), [cleanup_item(title, kind) for title in titles],
             'broad notes_create/reminder_item; conversation two_notes/two_reminders first turns')

    case('lookup_memory', [turn('lookup_memory', 'Go to About in System Settings and read out the installed memory in GB, without changing anything.', 'System Settings',
         lambda s,e: le.settings_pane_is('About') and bool(re.search(r'\b'+str(int(subprocess.check_output(['sysctl','-n','hw.memsize'], text=True).strip())//(1024**3))+r'\s*(?:GB|gigabytes)\b', answer(e), re.I)))],
         settings_setup, [dismiss_menu], 'heldout About memory')
    def dictionary_setup(r):
        subprocess.run(['open', 'dict://orchard'], check=True, timeout=20)
    def dictionary_check(s,e):
        from live_broad import ax_values
        front = le.front_app()
        raw = ax_values('Dictionary')
        fallback = []
        if front == 'Dictionary' and not all(word in raw.casefold() for word in ('nocturnal', 'night')):
            # Only OCR inside Dictionary's actual front window can establish its contents.
            sample = le.desktop_sample()
            if (sample.get('app') or {}).get('name') == 'Dictionary' and (sample.get('window') or {}).get('bounds'):
                fallback = le.screen_lines(window_only=True)
        s.setdefault('_observed', {}).update(dictionary_front=front, dictionary_ax=raw, dictionary_window_ocr=fallback)
        values = (raw+' '+' '.join(fallback)).casefold()
        return front == 'Dictionary' and 'nocturnal' in values and 'night' in values and 'night' in answer(e).casefold()
    case('lookup_dictionary', [turn('lookup_dictionary', 'Look up nocturnal in Dictionary and tell me what it means.', 'Dictionary', dictionary_check)],
         dictionary_setup, [dismiss_menu], 'varied Dictionary quixotic (2/3; partial baseline)')

    for name, directions in [('mascot_corners', [('top left', 'upper-left'), ('bottom right', 'lower-right')]),
                              ('mascot_edges', [('top right', 'upper-right'), ('bottom left', 'lower-left')])]:
        saved = {}
        def setup(r,saved=saved):
            saved['runner'] = r
        def cleanup(saved=saved):
            # The source app is restarted per case; park it using the exact local
            # geometry route rather than asking the model to invent coordinates.
            if 'runner' in saved:
                state, events, timed_out = saved['runner'].run('move to the bottom right', 30)
                if timed_out or not position_check('bottom right')(state, events):
                    raise RuntimeError('could not park mascot after test')
        def position_check(where):
            def check(s,e):
                p, area = s['mascot'], s['screen']
                x = area['x'] if 'left' in where else area['x']+area['width']-p['width']
                y = area['y'] if 'top' in where else area['y']+area['height']-p['height']
                return abs(p['x']-x) <= 40 and abs(p['y']-y) <= 40
            return check
        turns = [turn(name+'_'+str(i), ('Please tuck yourself into the ' if i == 0 else 'Now move yourself over to the ')+spoken+' corner of my screen.', '', position_check(where)) for i,(where,spoken) in enumerate(directions)]
        case(name, turns, setup, [cleanup], 'requests move to the left')
    return cases
