"""Thirty-three opt-in everyday tasks; look-only settings and no private applications.

The timer case refuses to start with an existing running timer and cancels its own
timer in cleanup. Checks fail closed if macOS lacks the AX
attribute/menu used as ground truth. TextEdit formatting checks inspect the document.
"""
from pathlib import Path
import tempfile
import re
import subprocess
import time

import live_eval as le
from live_tasks import Task, answer, no_failure
from live_heldout import osa
from live_conversation import notes, clean_items, page, task_started


def menu_mark(app, menu, item, observed=None):
    mark = osa('''tell application "System Events" to tell process (item 1 of argv)
return value of attribute "AXMenuItemMarkChar" of menu item (item 3 of argv) of menu 1 of menu bar item (item 2 of argv) of menu bar 1
end tell''', app, menu, item)
    if observed is not None:
        observed.setdefault('menu_marks', {})[menu+' > '+item] = mark
    return mark not in ('', 'missing value')


def ax_values(app):
    return osa('''tell application "System Events" to tell process (item 1 of argv)
set output to ""
repeat with element in entire contents of front window
    try
        set output to output & (name of element as text) & linefeed
    end try
    try
        set output to output & (value of element as text) & linefeed
    end try
    try
        set output to output & (description of element as text) & linefeed
    end try
end repeat
return output
end tell''', app)


def timer_fixture_is_idle(values):
    """Require positive native UI evidence and no active/paused timer controls."""
    return (bool(re.search(r'\b(?:Add|Start)\b', values))
            and not re.search(r'\b(?:Cancel|Pause|Resume)\b', values))


def selected(app, label):
    # Require an actual selection; a visible sidebar label alone is insufficient.
    return osa('''tell application "System Events" to tell process (item 1 of argv)
repeat with element in entire contents of front window
    set matches to false
    repeat with attr in {"AXTitle", "AXDescription", "AXIdentifier"}
        try
            if (value of attribute attr of element as text) is item 2 of argv then set matches to true
        end try
    end repeat
    try
        if name of element is item 2 of argv then set matches to true
    end try
    if matches then
        try
            if value of attribute "AXSelected" of element is true then return "yes"
        end try
        try
            if role of element is in {"AXRadioButton", "AXTab", "AXButton", "AXSegmentedControl"} then
                if (value of attribute "AXValue" of element as text) is in {"1", "true"} then return "yes"
            end if
        end try
    end if
end repeat
return "no"
end tell''', app, label) == 'yes'


def private_roundtrip(receipts):
    opened = False
    for receipt in receipts:
        label = str(receipt.get('label', '')).lower()
        if 'private' in label and ('new' in label or 'open' in label):
            opened = True
        elif opened and ('close' in label or receipt.get('type') == 'close_window'):
            return True
    return False


def build(window, count=30, seed=0):
    cases = []
    marker = 'A11y test %d' % (time.time_ns() // 1000)
    def add(name, request, app, check, setup=None, cleanup=()):
        def prepare(runner):
            if setup:
                setup(runner)
            else:
                le.activate(app)
        def observed_check(s,e):
            observed = s.setdefault('_observed', {})
            for key, read in [('front_app', le.front_app), ('ax_text', lambda: ax_values(app)[:300])]:
                try:
                    observed[key] = read()
                except Exception as error:
                    observed[key+' error'] = str(error)
            if app == 'Safari':
                try:
                    observed['url'] = le.safari_front_tab()[0]
                except Exception as error:
                    observed['url error'] = str(error)
            try:
                return check(s,e)
            except Exception as error:
                observed['check error'] = str(error)
                raise
        cases.append(dict(name=name, setup=prepare, cleanup=cleanup,
                          turns=[Task(name, request, [('real Mac state', observed_check), ('no failure', no_failure)], wanted_app=app)]))
    def keys(app, key):
        osa('tell application (item 1 of argv) to activate', app)
        osa('tell application "System Events" to keystroke (item 1 of argv) using {command down}', key)
        time.sleep(.5)
    def menu(app, group, item):
        osa('tell application "System Events" to tell process (item 1 of argv) to click menu item (item 3 of argv) of menu 1 of menu bar item (item 2 of argv) of menu bar 1', app, group, item)
    def click_named(app, label):
        osa('''tell application "System Events" to tell process (item 1 of argv)
repeat with element in entire contents of front window
    try
        if name of element is item 2 of argv then
            perform action "AXPress" of element
            return
        end if
    end try
end repeat
error "setup control not found"
end tell''', app, label)
    def try_click(app, label):
        try:
            click_named(app, label)  # setup only: a control that moved must not cancel the task
        except RuntimeError:
            pass
    def calculator_setup(r):
        le.activate('Calculator')
        menu('Calculator','View','Basic')
        osa('tell application "System Events" to key code 53')
    def checked_menu(app, group, item):
        return lambda s,e: menu_mark(app, group, item, s.setdefault('_observed', {}))
    def app_visible(app, label):
        def check(s,e):
            deadline = time.monotonic()+3
            while True:
                values = ax_values(app)
                front = le.front_app()
                source = 'accessibility'
                if not values.strip() and front == app:
                    # Some native apps expose no named AX text. Use the same
                    # cropped current-window OCR that independently sees their UI.
                    values = '\n'.join(le.screen_lines(window_only=True))
                    source = 'window OCR'
                position = values.casefold().find(label.casefold())
                observed = s.setdefault('_observed', {})
                observed['front_app'] = front
                observed['evidence_source'] = source
                observed['ax_text'] = values[max(0, position-100):max(0, position-100)+300]
                if observed['front_app'] == app and position >= 0:
                    return True
                if time.monotonic() >= deadline:
                    return False
                time.sleep(.1)
        return check

    add('calculator_length', 'Convert 12 inches to centimeters in Calculator.', 'Calculator',
        lambda s,e: le.front_app() == 'Calculator' and '30.48' in ax_values('Calculator') and 'Centimeters' in ax_values('Calculator'),
        calculator_setup)
    add('calculator_temperature', 'Use Calculator to turn 68 Fahrenheit into Celsius.', 'Calculator',
        lambda s,e: le.front_app() == 'Calculator' and 'Celsius' in ax_values('Calculator') and '20' in ax_values('Calculator'), calculator_setup)
    add('clock_world', 'Show me the world clock in the Clock app.', 'Clock',
        lambda s,e: selected('Clock', 'World Clock'), lambda r: (le.activate('Clock'), keys('Clock','2')))
    timer = {'owned': False}
    def timer_setup(r):
        le.activate('Clock')
        keys('Clock','4')
        values = ax_values('Clock')
        if not timer_fixture_is_idle(values):
            raise RuntimeError('timer case requires verified idle Clock UI with no active or paused timers')
        timer['owned'] = True
    def timer_cleanup():
        if timer['owned']:
            if 'Cancel' in ax_values('Clock'):
                click_named('Clock','Cancel')
            if 'Cancel' in ax_values('Clock'):
                raise RuntimeError('test timer still running')
            timer['owned'] = False
    add('clock_timer', 'Start a ten-minute timer in Clock.', 'Clock',
        lambda s,e: 'Cancel' in ax_values('Clock') and bool(re.search(r'(?<![\d:])(?:00:)?(?:10:00|09:[0-5]\d|9:[0-5]\d)(?![\d:])', ax_values('Clock'))),
        timer_setup, [timer_cleanup])
    for view, other in [('Day','2'), ('Week','1')]:
        add('calendar_'+view.lower(), f'Switch Calendar to the {view.lower()} view.', 'Calendar',
            checked_menu('Calendar','View','By '+view), lambda r, other=other: (le.activate('Calendar'), keys('Calendar',other)))
    add('maps_place', 'Show me the Eiffel Tower in Maps.', 'Maps',
        app_visible('Maps','Eiffel Tower'), lambda r: subprocess.run(['open','maps://?q=Reykjavik'],check=True,timeout=20))
    # Lookup is a URL navigation, never typing into Dictionary's search field.
    add('dictionary_lookup', 'Look up serendipity in the Dictionary app.', 'Dictionary',
        app_visible('Dictionary','serendipity'), lambda r: subprocess.run(['open','dict://nebula'],check=True,timeout=20))
    downloads = str(Path.home()/'Downloads')
    def finder_new(r):
        osa('tell application "Finder" to make new Finder window to home')
        le.activate('Finder')
    def finder_downloads(r):
        osa('tell application "Finder" to make new Finder window to (POSIX file (item 1 of argv) as alias)', downloads)
        le.activate('Finder')
    def at_downloads():
        return osa('tell application "Finder" to return POSIX path of (target of front Finder window as alias)').rstrip('/') == downloads
    add('finder_downloads', 'Reveal my Downloads folder in a Finder window.', 'Finder', lambda s,e: at_downloads(), finder_new)
    add('finder_sort_kind', 'Sort this folder by kind, as a list.', 'Finder',
        lambda s,e: at_downloads() and osa('tell application "Finder" to return name of sort column of list view options of front Finder window') == 'Kind',
        lambda r: (finder_downloads(r), osa('tell application "Finder" to set current view of front Finder window to icon view')))
    info = {'owned': False}
    def info_open():
        return osa('tell application "System Events" to tell process "Finder" to return exists window "Downloads Info"') == 'true'
    def info_setup(r):
        if info_open():
            raise RuntimeError('Downloads Info already open; leave the owner window alone')
        info['owned'] = True
        finder_downloads(r)
    def info_cleanup():
        if info['owned'] and info_open():
            osa('tell application "System Events" to tell process "Finder" to click (first button of window "Downloads Info" whose subrole is "AXCloseButton")')
        info['owned'] = False
    add('finder_folder_info', 'Show me the info window for the Downloads folder.', 'Finder',
        lambda s,e: info_open(), info_setup, [info_cleanup])
    preview = {}
    def preview_setup(r):
        folder = Path(tempfile.mkdtemp(prefix='a11y-live-'))
        path = folder/(marker+'.pdf')
        objects = [b'<< /Type /Catalog /Pages 2 0 R >>',
                   b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
                   b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] >>']
        data = b'%PDF-1.4\n'
        offsets = [0]
        for index, obj in enumerate(objects,1):
            offsets.append(len(data))
            data += str(index).encode()+b' 0 obj\n'+obj+b'\nendobj\n'
        xref = len(data)
        data += b'xref\n0 4\n0000000000 65535 f \n'
        data += b''.join(('%010d 00000 n \n' % offset).encode() for offset in offsets[1:])
        data += ('trailer\n<< /Size 4 /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n' % xref).encode()
        path.write_bytes(data)
        preview['path'] = path
        subprocess.run(['open','-a','Preview',str(path)],check=True,timeout=20)
        time.sleep(1)
        menu('Preview','View','Hide Sidebar')
    def preview_cleanup():
        path = preview.get('path')
        if path:
            osa('tell application "Preview" to close (every window whose name contains (item 1 of argv))',marker)
            path.unlink(missing_ok=True)
            path.parent.rmdir()
    add('preview_thumbnails', 'Show the page thumbnails in Preview.', 'Preview',
        lambda s,e: marker in (le.window_title('Preview') or '') and menu_mark('Preview','View','Thumbnails', s.setdefault('_observed', {})),
        preview_setup, [preview_cleanup])
    for pane in ('Wi-Fi','Appearance','Keyboard','Bluetooth'):
        add('settings_'+pane.lower(), f'Show me my {pane} settings.', 'System Settings',
            lambda s,e,pane=pane: le.settings_pane_is(pane), lambda r: subprocess.run(['open','x-apple.systempreferences:com.apple.GeneralSettings'],check=True,timeout=20))
    def document(r, text=''):
        osa('tell application "TextEdit" to make new document with properties {text:(item 1 of argv)}', text)
        le.activate('TextEdit')
        if osa('tell application "System Events" to tell process "TextEdit" to return exists menu item "Make Rich Text" of menu "Format" of menu bar item "Format" of menu bar 1') == 'true':
            menu('TextEdit','Format','Make Rich Text')
        if text:
            osa('tell application "TextEdit" to set font of every character of front document to "Helvetica"')
            osa('tell application "TextEdit" to set size of every character of front document to 12')
    def text():
        return osa('tell application "TextEdit" to return text of front document')
    add('textedit_new', f'Create a new TextEdit document containing just {marker} new document.', 'TextEdit',
        lambda s,e: text().strip() == marker+' new document', lambda r: le.activate('Finder'))
    multiline = marker+'\nShopping for Saturday\nBread and apples'
    # Typing waits for the user's confirmation, which the eval channel cannot give: checked by its proposal instead.
    add('textedit_multiline', f'In the blank TextEdit document type these three lines exactly: {multiline}', 'TextEdit',
        lambda s,e: 'Shopping for Saturday' in answer(e) or text().strip() == multiline, document)
    add('textedit_bold', 'Make all the text in this document bold.', 'TextEdit',
        lambda s,e: text().strip() == marker and 'bold' in osa('tell application "TextEdit" to return font of first character of front document').lower(),
        lambda r: document(r,marker))
    add('textedit_size', 'Make the text in this document 24 point.', 'TextEdit',
        lambda s,e: text().strip() == marker and float(osa('tell application "TextEdit" to return size of first character of front document')) == 24,
        lambda r: document(r,marker))
    note_marker = marker+'2'
    def seed_note(r):
        osa('tell application "Notes" to make new note with properties {name:(item 1 of argv), body:(item 1 of argv) & "<br>orchard"}', note_marker)
        le.activate('Notes')
    add('notes_search_marker', f'Search my notes for {note_marker}.', 'Notes',
        lambda s,e: note_marker in ax_values('Notes') and note_marker in osa('''tell application "System Events" to tell process "Notes"
return value of first text field of front window whose subrole is "AXSearchField"
end tell'''), seed_note, [lambda: clean_items(note_marker)])
    create_marker = marker+'3'
    add('notes_create', f'Create a note titled {create_marker} containing orchard picnic.', 'Notes',
        lambda s,e: 'orchard picnic' in notes(create_marker), lambda r: le.activate('Notes'), [lambda: clean_items(create_marker)])
    delete_marker = marker+'4'
    def delete_setup(r):
        osa('tell application "Notes" to make new note with properties {name:(item 1 of argv),body:(item 1 of argv)}',delete_marker)
        le.activate('Notes')
    add('notes_delete', f'Delete only the note titled {delete_marker}.', 'Notes', lambda s,e: not notes(delete_marker), delete_setup, [lambda: clean_items(delete_marker)])
    reminder_marker = marker+'5'
    from live_conversation import test_reminders
    add('reminder_item', f'Add a reminder titled {reminder_marker} check picnic basket, without a due date.', 'Reminders',
        lambda s,e: any(x.get('title') == reminder_marker+' check picnic basket' for x in test_reminders(reminder_marker)),
        lambda r: le.activate('Reminders'), [lambda: clean_items(reminder_marker, 'reminders')])
    def web(r):
        window.ensure('https://example.com/')
    add('safari_public_page', 'Go to rfc-editor.org/rfc/rfc2606 in Safari.', 'Safari',
        lambda s,e: page('www.rfc-editor.org')(s,e) and 'rfc2606' in le.safari_front_tab()[0], web)  # it redirects to /info/rfc2606
    add('safari_find', 'Find the word illustrative on this page.', 'Safari',
        lambda s,e: page('example.com')(s,e) and osa('''tell application "System Events" to tell process "Safari"
repeat with element in entire contents of front window
    try
        if subrole of element is "AXSearchField" and value of element is "illustrative" then return "yes"
    end try
end repeat
return "no"
end tell''') == 'yes', web)
    zoom = {}
    def link_height():
        from companion.desktop_context import desktop_bridge
        targets = desktop_bridge(le.ROOT/'.data').sample_targets('com.apple.Safari').get('targets', [])
        # example.com's one link ("More information…", later "Learn more"): its height grows with the zoom.
        return max(float(t['bounds']['height']) for t in targets if 'link' in str(t.get('role', '')).lower() and t.get('bounds'))
    def zoom_setup(r):
        web(r)
        keys('Safari','0')
        zoom['height'] = link_height()
    add('safari_zoom', 'Make this page bigger.', 'Safari',
        lambda s,e: page('example.com')(s,e) and link_height() > zoom['height'], zoom_setup)
    private = {}
    def private_setup(r):
        web(r)
        private['ids'] = le.window_ids('Safari')
    add('safari_private_roundtrip', 'Open a private window in Safari, then close it again.', 'Safari',
        lambda s,e: le.window_ids('Safari') == private['ids'] and page('example.com')(s,e) and
            task_started(e) and private_roundtrip((s.get('task') or {}).get('receipts', [])), private_setup)
    add('safari_title', 'What is the title of this page?', 'Safari',
        lambda s,e: page('example.com')(s,e) and 'example domain' in answer(e).lower(), web)
    add('weather_units_menu', 'Where in Weather can I switch between Celsius and Fahrenheit? Show me, but do not change it.', 'Weather',
        lambda s,e: osa('tell application "System Events" to tell process "Weather" to return visible of menu "View" of menu bar item "View" of menu bar 1') == 'true', lambda r: (le.activate('Weather'), osa('tell application "System Events" to key code 53')))
    add('stocks_business_news', 'Show me the business news in Stocks.', 'Stocks',
        lambda s,e: selected('Stocks','Business News'), lambda r: (le.activate('Stocks'), try_click('Stocks','Watchlist')))
    add('font_book_computer', 'Show me all the fonts on this computer in Font Book.', 'Font Book',
        lambda s,e: selected('Font Book','Computer'), lambda r: (le.activate('Font Book'), try_click('Font Book','All Fonts')))
    add('activity_cpu_sort', 'Which apps are using the most CPU? Sort Activity Monitor by it.', 'Activity Monitor',
        lambda s,e: osa('''tell application "System Events" to tell process "Activity Monitor"
repeat with element in entire contents of front window
    try
        if name of element is "% CPU" and value of attribute "AXSortDirection" of element is "AXDescendingSortDirection" then return "yes"
    end try
end repeat
return "no"
end tell''') == 'yes', lambda r: (le.activate('Activity Monitor'), try_click('Activity Monitor','CPU'), try_click('Activity Monitor','Process Name')))
    return cases
