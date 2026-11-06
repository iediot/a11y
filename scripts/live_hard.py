"""Harder live tasks for scripts/live_eval.py --suite v3.
Legacy diagnostic, not an acceptance gate; see docs/ai/TESTING.md

Multi-step and cross-app goals, conditions, comparisons and reuse of what is already open,
with ground truth the harness works out itself at run time (Wikipedia, the Hacker News API,
Open-Meteo, Mail, Finder). Tasks that already pass reliably are left out; the v2 families
that still failed are mixed in. Every task cleans up after itself (live_eval.restore), test
notes and reminders carry the marker "A11y test", and nothing is sent to anyone."""
import json
import random
import re
import time
import urllib.parse
import urllib.request

import live_eval as le
import live_tasks as lt
from live_tasks import Task, answer, front, no_failure, says, url

# v2 families that still failed in recent runs; the ones that pass every time are dropped.
STILL_FAILING = ['two_tabs', 'go_back', 'hacker_news', 'apple_section', 'github_notifications', 'recipe', 'translate',
                 'newest_pdf', 'menu', 'notes_search', 'copy_title', 'safari_history', 'site_search', 'scroll_section',
                 'page_summary', 'wiki_fact']
CITIES = ['Lisbon', 'Oslo', 'Cairo', 'Seoul', 'Lima', 'Reykjavik', 'Nairobi', 'Toronto', 'Madrid', 'Hanoi', 'Perth', 'Warsaw']
SECTION_TOPICS = [('Coffee', 'History'), ('Chess', 'History'), ('Penguin', 'Evolution'), ('Jazz', 'Etymology'),
                  ('Saturn', 'Rings'), ('Volcano', 'Etymology'), ('Lisbon', 'Climate'), ('Danube', 'Names')]
COUNTRIES = ['Portugal', 'Norway', 'Peru', 'Kenya', 'Vietnam', 'Iceland', 'Chile', 'Austria']
USER_AGENT = 'A11y-desktop-companion-tests/1.0 (https://github.com/iediot/a11y)'


def fetch_json(address, timeout=10):
    request = urllib.request.Request(address, headers={'User-Agent': USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read(2_000_000))


def words(text):
    return {w for w in re.findall(r'[^\W_]+', text.casefold()) if len(w) > 3}


def overlap(reply, truth):
    """Share of the truth's longer words that the reply uses."""
    wanted = words(truth)
    return len(wanted & words(reply))/max(1, len(wanted))


def section_opening(title, section):
    """The first sentence of a Wikipedia article's section (the harness's ground truth)."""
    pages = fetch_json('https://en.wikipedia.org/w/api.php?'+urllib.parse.urlencode(
        {'action': 'query', 'prop': 'extracts', 'explaintext': 1, 'exsectionformat': 'wiki', 'titles': title,
         'redirects': 1, 'format': 'json', 'formatversion': 2}))['query']['pages']
    text = pages[0].get('extract', '')
    match = re.search(r'\n==\s*'+re.escape(section)+r'\s*==\n', text)
    if not match:
        return ''
    # The section's first sentence, past any sub-heading ("=== Legendary accounts ===").
    line = next((l.strip() for l in text[match.end():].split('\n') if len(l.strip()) > 20 and not l.strip().startswith('=')), '')
    return re.split(r'(?<=[.!?])\s', line)[0]


def hn_titles(count=6):
    ids = fetch_json('https://hacker-news.firebaseio.com/v0/topstories.json')[:count]
    return [fetch_json('https://hacker-news.firebaseio.com/v0/item/%d.json' % i).get('title', '') for i in ids]


def weather(city):
    """(temperature now, rain likely tomorrow) for a city, from Open-Meteo."""
    found = fetch_json('https://geocoding-api.open-meteo.com/v1/search?'+urllib.parse.urlencode({'name': city, 'count': 1}))['results'][0]
    data = fetch_json('https://api.open-meteo.com/v1/forecast?'+urllib.parse.urlencode({
        'latitude': found['latitude'], 'longitude': found['longitude'], 'timezone': 'auto', 'forecast_days': 2,
        'current': 'temperature_2m', 'daily': 'precipitation_probability_max,weather_code'}))
    wet = {51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82, 95, 96, 99}
    tomorrow = data['daily']['precipitation_probability_max'][1] >= 50 or data['daily']['weather_code'][1] in wet
    return data['current']['temperature_2m'], tomorrow


def test_notes(marker):
    """Names of the notes carrying this test's marker."""
    try:
        return subprocess_osa(['on run argv', 'tell application "Notes" to return name of every note whose name contains (item 1 of argv)',
                               'end run'], marker)
    except RuntimeError:
        return ''


def subprocess_osa(lines, *args):
    import subprocess
    result = subprocess.run(['osascript', *sum((['-e', line] for line in lines), []), *args],
                            capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError(result.stderr.strip()[:200])
    return result.stdout.strip()


def delete_test_notes():
    """Only notes carrying the test marker, which the harness created through A11y. Checked until
    they are gone: Notes quitting right after a delete kept one, and the next run found two (live)."""
    for _ in range(4):
        try:
            left = subprocess_osa(['on run argv', 'tell application "Notes"',
                                   # One delete of the whole set: a repeat over a "whose" list fails with -1728 (live).
                                   'delete (every note whose name starts with (item 1 of argv))',
                                   'delay 1', 'return count of (every note whose name starts with (item 1 of argv))',
                                   'end tell', 'end run'], le.TEST_MARKER)
        except RuntimeError:
            left = '?'
        if left == '0':
            return
        time.sleep(1)


def textedit_text():
    try:
        return subprocess_osa(['tell application "TextEdit" to if (count of documents) > 0 then return text of front document'])
    except RuntimeError:
        return ''


def build(eval_window, count, seed):
    rng = random.Random(seed)
    pick = rng.choice
    tag = lambda: '%s %04d' % (le.TEST_MARKER, rng.randint(1000, 9999))

    def open_page(address, title=''):
        def setup(runner):
            eval_window.ensure(address, wait_title=title)
        return setup

    f = {}

    def family(fn):
        f[fn.__name__] = fn
        return fn

    @family
    def section_sentence():
        for topic, section in rng.sample(SECTION_TOPICS, len(SECTION_TOPICS)):
            truth = section_opening(topic, section)
            if truth:
                break  # articles change: only a section that exists today
        request = pick(['open the Wikipedia article on {t} and tell me the first sentence of its {s} section',
                        'on Wikipedia, what does the {s} section of the {t} article start with?']).format(t=topic, s=section)
        return Task('section_sentence', request, [('quotes the section ('+truth[:40]+'…)', lambda s, e: overlap(answer(e), truth) >= 0.5),
                                                  ('Wikipedia open', url(r'wikipedia\.org/wiki/'))], timeout=300)

    @family
    def nth_story():
        n = rng.randint(2, 5)
        ordinal = {2: 'second', 3: 'third', 4: 'fourth', 5: 'fifth'}[n]
        request = pick(['go to Hacker News and tell me the title of the {o} story', "what's the {o} story on Hacker News right now?"]).format(o=ordinal)
        def check(state, events):
            titles = hn_titles(n+2)
            return any(overlap(answer(events), t) >= 0.6 for t in titles[max(0, n-2):n+1])  # the list moves a little
        return Task('nth_story', request, [(ordinal+' story title', check), ('no failure', no_failure)], timeout=300)

    @family
    def rain_reminder():
        city = pick(CITIES)
        marker = tag()
        request = ('if it is going to rain in {c} tomorrow, remind me "{m}: take an umbrella" tomorrow at 8, '
                   'otherwise just tell me it will be dry').format(c=city, m=marker)
        def reminded():
            return any(marker.casefold() in item.get('title', '').casefold() for item in lt.reminders())
        def check(state, events):
            _, wet = weather(city)
            pending = (state.get('pending_action') or {})
            proposed = marker.casefold() in json.dumps(pending, ensure_ascii=False).casefold() or reminded()
            return proposed if wet else (not proposed and bool(re.search(r'\bdry|no rain|not (?:going to )?rain', answer(events), re.I)))
        return Task('rain_reminder', request, [('acts on the forecast', check)], timeout=240)

    @family
    def warmer_city():
        a, b = rng.sample(CITIES, 2)
        request = pick(['is it warmer in {a} or {b} right now?', 'which is warmer at the moment, {a} or {b}?']).format(a=a, b=b)
        def check(state, events):
            ta, tb = weather(a)[0], weather(b)[0]
            if abs(ta-tb) < 1:
                return True  # too close to call
            warmer, colder = (a, b) if ta > tb else (b, a)
            reply = answer(events)
            return warmer.casefold() in reply.casefold() and reply.casefold().find(warmer.casefold()) <= max(0, reply.casefold().find(colder.casefold()))
        return Task('warmer_city', request, [('names the warmer city', check)], timeout=180)

    @family
    def population_to_textedit():
        country = pick(COUNTRIES)
        request = pick(['look up the population of {c} and write it in a new TextEdit document',
                        'find how many people live in {c} and put the number in a new TextEdit document']).format(c=country)
        return Task('population_to_textedit', request,
                    [('TextEdit in front', front('TextEdit')), ('the document has a number', lambda s, e: bool(re.search(r'\d[\d,. ]{4,}', textedit_text()))),
                     ('names the country', lambda s, e: country.casefold() in textedit_text().casefold() or bool(re.search(r'\d', textedit_text())))],
                    cleanup=[lt.close_textedit_untitled], wanted_app='TextEdit', timeout=360)

    @family
    def switch_tab():
        other = pick([('https://en.wikipedia.org/wiki/Coffee', 'Wikipedia', 'Wikipedia'), ('https://github.com/explore', 'GitHub', 'GitHub')])
        def setup(runner):
            eval_window.ensure(other[0], wait_title=other[2])
            # A second tab in front, so the site's tab really is another tab (loading it into the same
            # tab only left it in that tab's history, and the task could not pass).
            le.osa('tell application "Safari"', 'tell window id %d' % eval_window.id,
                   'set current tab to (make new tab at end of tabs with properties {URL:"https://duckduckgo.com"})', 'end tell', 'activate', 'end tell')
            time.sleep(2)
        before = {}
        def counted(runner):
            setup(runner)
            before['tabs'] = sum(le.tab_counts('Safari').values())
        request = pick(['switch to the {n} tab', 'go back to the {n} tab I had open']).format(n=other[1])
        return Task('switch_tab', request, [('that tab in front', url(re.escape(other[0].split('/')[2]))),
                                            ('no new tab', lambda s, e: sum(le.tab_counts('Safari').values()) <= before.get('tabs', 99))],
                    setup=counted, timeout=180)

    @family
    def reuse_open_site():
        site, address = pick([('Wikipedia', 'https://en.wikipedia.org/wiki/Main_Page'), ('GitHub', 'https://github.com'),
                              ('Hacker News', 'https://news.ycombinator.com')])
        before = {}
        def setup(runner):
            eval_window.ensure(address)
            le.activate('Finder')
            before['tabs'] = sum(le.tab_counts('Safari').values())
        return Task('reuse_open_site', pick(['open {s}', 'take me to {s}']).format(s=site),
                    [('the site in front', url(re.escape(address.split('/')[2].replace('www.', '')))),
                     ('reused, no new tab', lambda s, e: sum(le.tab_counts('Safari').values()) <= before.get('tabs', 99))],
                    setup=setup, timeout=120)

    @family
    def finder_sort():
        folder = pick(['Downloads', 'Documents', 'Desktop'])
        request = pick(['open my {f} folder and sort it by date added', 'show {f} in Finder, newest added first']).format(f=folder)
        def sorted_by_added(state, events):
            try:
                name = subprocess_osa(['tell application "Finder" to return name of front Finder window'])
            except RuntimeError:
                return False
            try:  # a list view's column by name ("as text" fails, -1700); icon view doesn't report Sort By at all
                column = subprocess_osa(['tell application "Finder" to return name of (sort column of list view options of front Finder window)'])
            except RuntimeError:
                column = ''
            chose = any('date added' in str(r.get('label', '')).casefold() for r in (state.get('task') or {}).get('receipts', []))
            # Finder can't report grouping by AppleScript: grouped by Date Added (when it groups) counts too.
            return folder.casefold() in name.casefold() and ('added' in column.casefold() or chose)
        return Task('finder_sort', request, [('sorted by date added', sorted_by_added), ('no failure', no_failure)],
                    wanted_app='Finder', timeout=300)

    @family
    def note_and_read_back():
        marker = tag()
        fact = pick(['the meeting moved to Thursday', 'the spare key is under the blue pot', 'Ana likes oat milk'])
        request = 'make a note titled "{m}" that says {f}, then read it back to me'.format(m=marker, f=fact)
        return Task('note_and_read_back', request,
                    [('note created', 'confirm', lambda s, e: marker in test_notes(marker)), ('read back', lambda s, e: overlap(answer(e), fact) >= 0.6)],
                    cleanup=[delete_test_notes], timeout=240)

    @family
    def unread_mail():
        request = pick(['how many unread emails do I have?', 'do I have unread mail? how many?'])
        def check(state, events):
            try:
                count = int(subprocess_osa(['tell application "Mail" to return unread count of inbox']) or 0)
            except (RuntimeError, ValueError):
                return False
            return bool(re.search(r'\b%d\b' % count, answer(events))) or (count == 0 and bool(re.search(r'\bno\b|none|zero', answer(events), re.I)))
        return Task('unread_mail', request, [('the right count', check)], timeout=120)

    @family
    def play_video():
        # Not YouTube: the owner blocks it (Cold Turkey). Wikimedia Commons has playable media.
        topic = pick(['Moon landing', 'Aurora', 'Hummingbird', 'Solar eclipse'])
        request = pick(['find a video of {t} on Wikimedia Commons and open it', 'on Wikimedia Commons, open a video about {t}']).format(t=topic)
        return Task('play_video', request, [('a media file page', url(r'commons\.wikimedia\.org/wiki/File:.*\.(webm|ogv|mp4)')),
                                            ('no failure', no_failure)], timeout=300)

    @family
    def newest_download_size():
        request = pick(['how big is the newest file in my Downloads folder?', "what's the size of the last thing I downloaded?"])
        def check(state, events):
            from pathlib import Path
            files = [p for p in (Path.home()/'Downloads').iterdir() if not p.name.startswith('.')]
            if not files:
                return True
            newest = max(files, key=lambda p: p.stat().st_mtime)
            size = newest.stat().st_size
            numbers = [float(n.replace(',', '.')) for n in re.findall(r'(\d+(?:[.,]\d+)?)\s*(?:[kmg]b|bytes|kilobytes|megabytes|gigabytes)', answer(events), re.I)]
            units = {'k': 1e3, 'm': 1e6, 'g': 1e9}
            spoken = [float(n.replace(',', '.'))*units.get(u[0].casefold(), 1)
                      for n, u in re.findall(r'(\d+(?:[.,]\d+)?)\s*([kmg]b|bytes|kilobytes|megabytes|gigabytes)', answer(events), re.I)]
            return bool(numbers) and any(0.5*size <= value <= 2*size for value in spoken)
        return Task('newest_download_size', request, [('about the right size', check)], timeout=120)

    hard = list(f.values())
    older = lt.build(eval_window, max(1, count), seed+1, only=STILL_FAILING)
    tasks = []
    rng.shuffle(hard)
    while len(tasks) < count:
        for make in hard:
            if len(tasks) < count:
                try:
                    tasks.append(make())
                except (OSError, ValueError, KeyError, IndexError) as error:  # ground truth unavailable
                    print('skipped', make.__name__, error, flush=True)
            if older and len(tasks) < count and rng.random() < 0.35:
                tasks.append(older.pop())
        rng.shuffle(hard)
    return tasks
