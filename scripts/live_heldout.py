"""Private held-out macOS live tasks; imported explicitly by the evaluation driver.
Legacy diagnostic, not an acceptance gate; see docs/ai/TESTING.md

No task confirms a write. Setup only reads data, opens pages, or activates apps.
Ground truth is collected during setup/check, never at import or build time.
Unavailable data fails its check rather than receiving an automatic pass. Do not
publish requests or seed expansions in ordinary development reports.
"""
from datetime import date
import json
from pathlib import Path
import random
import re
import subprocess
import urllib.parse
import urllib.request

import live_eval as le
from live_tasks import Task, answer, front, no_failure, visible

USER_AGENT = 'A11y-heldout-live-evaluation/1.0'
FAMILY_COUNT = 25


def osa(script, *args):
    """All variable AppleScript values are argv, never source interpolation."""
    result = subprocess.run(le.native_command('osascript', '-e', 'on run argv\n' + script + '\nend run', *map(str, args)),
                            capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError('AppleScript ground truth unavailable: '+result.stderr.strip())
    return result.stdout.strip()


def fetch(address):
    request = urllib.request.Request(address, headers={'User-Agent': USER_AGENT, 'Accept': 'application/json'})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read(4_000_000))


def contains(reply, truth):
    normalize = lambda x: re.sub(r'\W+', ' ', str(x).casefold()).strip()
    wanted = normalize(truth)
    return bool(wanted) and wanted in normalize(reply)


def number(reply, expected, tolerance=0):
    values = re.findall(r'(?<!\w)-?\d+(?:[.,]\d+)*', reply)
    for value in values:
        try:
            if abs(float(value.replace(',', '')) - float(expected)) <= tolerance:
                return True
        except ValueError:
            pass
    return expected == 0 and bool(re.search(r'\b(?:zero|none|no items|empty)\b', reply, re.I))


def app_names(app, collection):
    # Literal application terms let osascript compile each app's dictionary.
    readers = {
        ('Notes', 'folder'): 'tell application "Notes" to set names to name of every folder',
        ('Reminders', 'list'): 'tell application "Reminders" to set names to name of every list',
        ('Calendar', 'calendar'): 'tell application "Calendar" to set names to name of every calendar',
        ('TextEdit', 'document'): 'tell application "TextEdit" to set names to name of every document',
    }
    return osa(readers[app, collection] + "\nset AppleScript's text item delimiters to linefeed\nreturn names as text")


def has_date(reply, value):
    day = date.fromisoformat(value)
    if contains(reply, value):
        return True
    # Accept either order, month abbreviations, and ordinal day suffixes.
    month = day.strftime('%B')
    month_pattern = month[:3] + (r'(?:' + month[3:] + r')?\.?')
    d = r'0?' + str(day.day) + r'(?:st|nd|rd|th)?'
    gap = r'[\s,]+'
    patterns = (month_pattern + gap + d, d + gap + month_pattern)
    for pattern in patterns:
        for match in re.finditer(r'\b' + pattern + r'\b', reply, re.I):
            # A supplied year must agree; an omitted year is customary in answers.
            year = re.match(r'[\s,]+(\d{4})\b', reply[match.end():])
            if year is None or int(year[1]) == day.year:
                return True
    return False


def rate_number(reply, expected):
    tolerance = max(.005, expected*.01)
    return any(abs(float(n.replace(',', '.')) - expected) <= tolerance
               for n in re.findall(r'(?<![\w.,])-?\d+(?:[.,]\d+)?(?!\d)', reply))


def destination_open(app, target, fallback=None):
    if contains(le.window_title(app) or '', target):
        return True
    try:
        # The helper reports which tab or option is chosen (fast, any app): the window-wide AppleScript walk
        # timed out on Activity Monitor's process table and failed a correct Memory tab (live).
        from companion.desktop_context import desktop_bridge
        bundle = osa('return id of application (item 1 of argv)', app)
        tabs = [t for t in desktop_bridge(le.ROOT/'.data').sample_targets(bundle).get('targets', [])
                if 'selected' in t and contains(t.get('label', ''), target)]
        if tabs:
            return any(t['selected'] for t in tabs)
    except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired):
        pass
    try:
        selected = osa('''tell application "System Events" to tell process (item 1 of argv)
set found to false
repeat with element in entire contents of front window
    try
        set labelText to (name of element) as text
        if labelText is item 2 of argv then
            try
                set chosen to value of attribute "AXSelected" of element
                if chosen is true then return "yes"
                if chosen is false then set found to true
            end try
            if role of element is "AXRadioButton" then
                if value of element is 1 then return "yes"
                set found to true
            end if
        end if
    end try
end repeat
if found then return "no"
return "unknown"
end tell''', app, target)
        if selected != 'unknown':
            return selected == 'yes'
    except (RuntimeError, subprocess.TimeoutExpired):
        pass
    return le.shows(fallback or target)


def calculator_history_open():
    """Calculator's own View menu says it: "Hide History" is offered exactly while the history shows
    (its sidebar has no "History" heading, and an AX search of the window found nothing, live)."""
    if le.front_app() != 'Calculator':
        return False
    try:
        return osa('tell application "System Events" to tell process "Calculator" to return exists '
                   '(menu item "Hide History" of menu "View" of menu bar item "View" of menu bar 1)') == 'true'
    except RuntimeError:
        return False


def build(eval_window, count, seed):
    """Cycle shuffled families with reproducible wording and parameters."""
    if count < 0:
        raise ValueError('count must be nonnegative')
    rng = random.Random(seed)
    pick = rng.choice
    families = []

    def family(fn):
        families.append(fn)
        return fn

    def task(name, request, check, *, app='', setup=None, timeout=240):
        return Task(name, request, [('live result', check), ('no failure', no_failure)],
                    setup=setup, timeout=timeout, wanted_app=app)

    def screen_task(name, app, target, wordings):
        # A navigation result must expose the requested destination, not merely
        # launch the application or repeat the destination in the assistant reply.
        return task(name, pick(wordings).format(x=target),
                    lambda s, e: front(app)(s, e) and (
                        calculator_history_open() if app == 'Calculator' else
                        destination_open(app, 'Memory' if app == 'Activity Monitor' else target, target)
                        if app in ('Clock', 'App Store', 'Activity Monitor', 'Maps', 'Photos', 'Music') else
                        visible(target)(s, e)), app=app,
                    setup=lambda r: le.activate('Finder'))

    def read_task(name, app, request, reader, matcher=contains):
        truth = {}
        def setup(runner):
            truth['value'] = reader()
            le.activate(app)
        def check(state, events):
            return 'value' in truth and matcher(answer(events), truth['value'])
        return task(name, request, check, app=app, setup=setup)

    def api_task(name, request, reader, matcher=contains, address=None):
        truth = {}
        def setup(runner):
            truth['value'] = reader()
            eval_window.ensure(address or le.NEUTRAL)
        return task(name, request, lambda s, e: 'value' in truth and matcher(answer(e), truth['value']),
                    setup=setup, timeout=360)

    @family
    def folder_item_count():
        folder = pick(['Pictures', 'Movies', 'Music'])
        def read():
            return len([p for p in (Path.home()/folder).iterdir() if not p.name.startswith('.')])
        return read_task('heldout_folder_count', 'Finder', pick([
            'How many visible items are directly inside my {x} folder? Do not include subfolders\' contents.',
            'Count the items in my {x} folder, excluding hidden items and without looking inside folders.'
        ]).format(x=folder), read, lambda reply, truth: number(reply, truth))

    @family
    def finder_location():
        chosen = {}
        def setup(runner):
            candidates = [p for p in (Path.home()/'Library', Path.home()/'Public') if p.is_dir()]
            chosen['path'] = str(pick(candidates))
            subprocess.run(['open', '-a', 'Finder', chosen['path']], check=True, timeout=20)
        return task('heldout_finder_location', pick([
            'What is the full filesystem path of the folder in the front Finder window?',
            'Tell me the complete path of this Finder folder without changing anything.'
        ]), lambda s, e: contains(answer(e), chosen.get('path', '')), app='Finder', setup=setup)

    @family
    def storage_capacity():
        import os
        def read():
            stat = os.statvfs(Path.home())
            return stat.f_blocks * stat.f_frsize
        def match(reply, size):
            units = {'tb': 1e12, 'gb': 1e9, 'terabytes': 1e12, 'gigabytes': 1e9}
            return any(abs(float(n.replace(',', '.'))*units[u.casefold()] - size) < size*.15
                       for n, u in re.findall(r'(\d+(?:[.,]\d+)?)\s*(TB|GB|terabytes|gigabytes)', reply, re.I))
        return read_task('heldout_storage_capacity', 'System Settings', pick([
            'Read the total capacity of my startup disk in Storage settings. Leave all settings as they are.',
            'How large is this Mac\'s startup disk? Look in Storage, without changing anything.'
        ]), read, match)

    @family
    def notes_folders():
        return read_task('heldout_notes_folders', 'Notes', pick([
            'Read me the names of my Notes folders. Do not create or edit anything.',
            'Which folders do I have in Notes? Just list their names.'
        ]), lambda: app_names('Notes', 'folder'),
                         lambda reply, truth: bool(truth) and all(contains(reply, n.strip()) for n in truth.splitlines() if n.strip()))

    @family
    def reminders_lists():
        return read_task('heldout_reminders_lists', 'Reminders', pick([
            'Which reminder lists do I have? Read their names without modifying anything.',
            'List the names of my lists in Reminders; leave the reminders alone.'
        ]), lambda: app_names('Reminders', 'list'),
                         lambda reply, truth: bool(truth) and all(contains(reply, n.strip()) for n in truth.splitlines() if n.strip()))

    @family
    def calendar_sources():
        return read_task('heldout_calendar_sources', 'Calendar', pick([
            'Which calendars are available in Calendar? Tell me their names without editing events.',
            'Read the names of my calendars from the Calendar sidebar.'
        ]), lambda: app_names('Calendar', 'calendar'),
                         lambda reply, truth: bool(truth) and all(contains(reply, n.strip()) for n in truth.splitlines() if n.strip()))

    @family
    def preview_pages():
        truth = {}
        def setup(runner):
            # An already-open document is preferable to opening a private random file.
            truth['count'] = int(osa('''tell application "System Events" to tell process "Preview"
set total to 0
repeat with w in windows
    try
        if subrole of w is "AXStandardWindow" then
            set documentURL to value of attribute "AXDocument" of w
            if documentURL is not missing value and documentURL is not "" then set total to total + 1
        end if
    end try
end repeat
return total
end tell'''))
            le.activate('Preview')
        return task('heldout_preview_windows', pick([
            'How many document windows are open in Preview? Do not close any of them.',
            'Count Preview\'s open windows for me, leaving the documents untouched.'
        ]), lambda s, e: 'count' in truth and number(answer(e), truth['count']), setup=setup, app='Preview')

    @family
    def textedit_document_names():
        def read():
            return app_names('TextEdit', 'document')
        def match(reply, truth):
            return (all(contains(reply, n.strip()) for n in truth.splitlines() if n.strip()) if truth else
                    bool(re.search(r'(?:no|zero|0) (?:open )?documents|none|no files|nothing (?:is )?open', reply, re.I)))
        return read_task('heldout_textedit_open_documents', 'TextEdit', pick([
            'Which documents are currently open in TextEdit? Read their names; do not make a new document.',
            'Tell me the names of the open TextEdit documents without editing or closing them.'
        ]), read, match)

    @family
    def maps_mode():
        return screen_task('heldout_maps_transit', 'Maps', 'Transit', [
            'Show the Transit map in Maps without starting directions.',
            'In Maps, switch the map view to Transit so I can see public transport.'
        ])

    @family
    def weather_details():
        field = pick(['Humidity', 'Visibility', 'Pressure'])
        return screen_task('heldout_weather_details', 'Weather', field, [
            'In Weather, scroll to the {x} card for the current location.',
            'Show me the {x} details in Weather for the location already selected.'
        ])

    @family
    def music_local_library():
        section = pick(['Albums', 'Artists', 'Songs'])
        return screen_task('heldout_music_library', 'Music', section, [
            'Show {x} in my Music library, without playing anything or searching the store.',
            'In Music, browse my local library by {x}. Do not change playback.'
        ])

    @family
    def photos_media():
        section = pick(['Videos', 'Favorites', 'Screenshots'])
        return screen_task('heldout_photos_collection', 'Photos', section, [
            'Show my {x} collection in Photos. Do not edit, delete, or share anything.',
            'In Photos, browse {x}; leave the pictures untouched.'
        ])

    @family
    def calculator_history():
        return screen_task('heldout_calculator_history', 'Calculator', 'History', [
            'Show Calculator\'s calculation history without clearing it or entering anything.',
            'Open the history panel in Calculator so I can review previous calculations.'
        ])

    @family
    def dictionary_sources():
        return task('heldout_dictionary_sources', pick([
            'Show Dictionary\'s settings so I can see the available reference sources. Do not change any selections.',
            'Open the reference-source settings in Dictionary without enabling or disabling anything.'
        ]), lambda s, e: le.front_app() == 'Dictionary' and
                    any(le.shows(label) for label in ('Drag reference sources', 'Select reference sources', 'Sources')) and
                    any(contains(le.window_title('Dictionary'), title) for title in ('Settings', 'Preferences')),
                    app='Dictionary', setup=lambda r: le.activate('Finder'))

    @family
    def contacts_count():
        return read_task('heldout_contacts_count', 'Contacts', pick([
            'How many contact cards are in Contacts? Just read the total; do not edit them.',
            'Read the total number of people in my Contacts address book.'
        ]), lambda: int(osa('tell application "Contacts" to return count of people')),
                         lambda reply, truth: number(reply, truth))

    @family
    def clock_browse():
        section = pick(['Stopwatch', 'Timers', 'Alarms'])
        return screen_task('heldout_clock_view', 'Clock', section, [
            'Show the {x} view in Clock. Do not start, stop, add, or remove anything.',
            'Go to Clock\'s {x} screen just to look at it.'
        ])

    @family
    def app_store_categories():
        section = pick(['Arcade', 'Create', 'Work', 'Play'])
        return screen_task('heldout_app_store_editorial', 'App Store', section, [
            'Browse the {x} section of the App Store without getting or installing anything.',
            'Take me to {x} in the App Store. Just browse; do not download anything.'
        ])

    @family
    def activity_memory():
        return screen_task('heldout_activity_memory', 'Activity Monitor', 'Memory Pressure', [
            'Show the Memory tab in Activity Monitor with the memory pressure graph. Do not quit any processes.',
            'Open Activity Monitor and show memory pressure, leaving all processes running.'
        ])

    @family
    def exchange_rate():
        currency = pick(['GBP', 'CHF', 'JPY', 'CAD'])
        return api_task('heldout_exchange_rate', pick([
            'Check Frankfurter online: how many {x} is one euro worth at the latest published rate? Include the rate date.',
            'Using the latest Frankfurter reference rate, tell me EUR to {x} and the date of that rate.'
        ]).format(x=currency), lambda: fetch('https://api.frankfurter.dev/v1/latest?base=EUR&symbols='+currency),
                        lambda reply, truth: rate_number(reply, truth['rates'][currency])
                        and has_date(reply, truth['date']), address='https://frankfurter.dev/')

    @family
    def earthquake_latest():
        minimum = pick([2.5, 4.5])
        endpoint = 'https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/%s_day.geojson' % minimum
        def read():
            data = fetch(endpoint)['features']
            return max(data, key=lambda x: x['properties']['time'])['properties'] if data else None
        return api_task('heldout_recent_earthquake', pick([
            'On USGS, where was the most recent magnitude {x} or greater earthquake in the past day, and what was its magnitude?',
            'Check the USGS past-day feed for the latest earthquake of magnitude at least {x}. Tell me its place and magnitude.'
        ]).format(x=minimum), read,
                        lambda reply, truth: truth is not None and contains(reply, truth['place']) and number(reply, truth['mag'], .1),
                        address=endpoint)

    @family
    def public_holiday():
        country, code = pick([('Ireland', 'IE'), ('Germany', 'DE'), ('Canada', 'CA')])
        def read():
            today = date.today()
            holidays = fetch('https://date.nager.at/api/v3/PublicHolidays/%d/%s' % (today.year, code))
            upcoming = [h for h in holidays if h['date'] >= today.isoformat()]
            if not upcoming:
                upcoming = fetch('https://date.nager.at/api/v3/PublicHolidays/%d/%s' % (today.year+1, code))
            earliest = min(h['date'] for h in upcoming)
            return [h for h in upcoming if h['date'] == earliest]
        return api_task('heldout_next_public_holiday', pick([
            'Use Nager.Date to find the next public holiday in {x}, including today. Give its name and date.',
            'What is the next public holiday in {x} on Nager.Date? Include its date; count today if it is a holiday.'
        ]).format(x=country), read, lambda reply, truth: any((contains(reply, h['name']) or contains(reply, h.get('localName', ''))) and
                            has_date(reply, h['date']) for h in truth),
                        address='https://date.nager.at/')

    @family
    def daylight_length():
        city, lat, lon = pick([('Dublin', 53.35, -6.26), ('Helsinki', 60.17, 24.94), ('Athens', 37.98, 23.73)])
        def read():
            query = urllib.parse.urlencode({'latitude': lat, 'longitude': lon, 'daily': 'daylight_duration', 'timezone': 'auto', 'forecast_days': 1})
            return fetch('https://api.open-meteo.com/v1/forecast?'+query)['daily']['daylight_duration'][0]/3600
        def match(reply, hours):
            pairs = re.findall(r'(?<![\d:])(\d+)\s*(?:hours?|hrs?|h)\s*(?:(?:and\s*)?(\d+)\s*(?:minutes?|mins?|m))?(?!\w)', reply, re.I)
            pairs += re.findall(r'(?<![\d:])(\d{1,2}):(\d{2})(?![\d:])', reply)
            return any(int(m or 0) < 60 and abs(int(h)+int(m or 0)/60-hours) < .2 for h, m in pairs)
        return api_task('heldout_daylight_duration', pick([
            'Look up today\'s daylight duration for {x} on Open-Meteo. How many hours and minutes of daylight are there?',
            'Using Open-Meteo, tell me how long daylight lasts today in {x}, in hours and minutes.'
        ]).format(x=city), read, match)

    @family
    def cross_app_calendar_names():
        expected = {}
        destination = pick(['Safari', 'Finder'])
        def setup(runner):
            expected['names'] = app_names('Calendar', 'calendar')
            le.activate(destination)
        return task('heldout_calendar_return', pick([
            'Check the names of my calendars in Calendar, then return to {x} and tell me the names. Do not edit events.',
            'Read my calendar list, come back to {x}, and report the calendar names without changing anything.'
        ]).format(x=destination), lambda s, e: front(destination)(s, e) and bool(expected.get('names')) and
                    all(contains(answer(e), n.strip()) for n in expected['names'].splitlines() if n.strip()),
                    setup=setup, app='Calendar', timeout=300)

    @family
    def web_reading_list():
        site, address, destination = pick([
            ('Project Gutenberg', 'https://www.gutenberg.org', 'Top 100'),
            ('Internet Archive', 'https://archive.org', 'Books'),
            ('Open Library', 'https://openlibrary.org', 'Trending')])
        def check(state, events):
            current, title = le.safari_front_tab()
            host = urllib.parse.urlparse(address).hostname
            actual = urllib.parse.urlparse(current).hostname or ''
            return (actual == host or actual.endswith('.'+host)) and (le.shows(destination) or contains(title, destination))
        return task('heldout_book_browse', pick([
            'On {s}, go to {d} so I can browse books. Do not borrow, download, or sign in.',
            'Show the {d} book browsing page on {s}, without downloading or signing in.'
        ]).format(s=site, d=destination), check, setup=lambda r: eval_window.ensure(address), app='Safari', timeout=300)

    @family
    def hardware_memory():
        def read():
            result = subprocess.run(['sysctl', '-n', 'hw.memsize'], capture_output=True,
                                    text=True, check=True, timeout=15)
            return int(result.stdout.strip()) / (1024**3)
        return read_task('heldout_installed_memory', 'System Settings', pick([
            'In About this Mac, read how much memory is installed. Do not change any settings.',
            'How much RAM does this Mac have? Check the hardware information, leaving settings unchanged.'
        ]), read, lambda reply, truth: any(
            abs(float(n.replace(',', '.')) - truth) < .1
            for n in re.findall(r'(\d+(?:[.,]\d+)?)\s*(?:GB|gigabytes)', reply, re.I)))

    assert len(families) == FAMILY_COUNT
    result = []
    while len(result) < count:
        cycle = list(families)
        rng.shuffle(cycle)
        for make in cycle:
            if len(result) == count:
                break
            result.append(make())
    return result
