#!/usr/bin/env python3
"""Screen-free request evaluation: a person says X; what does A11y decide and say, and how long does it take?

Every utterance goes through the real `companion.service.Companion` (prepare_request + ask) with the real
planner and a real local `OllamaProvider`. The native layer is a recording fake, so nothing touches this
Mac: a fake desktop bridge (installed apps, front window, Calendar/Reminders), a fake `subprocess.run` that
answers the fixed osascript/open/pmset commands the runtime uses, and fake weather/Wikipedia fetchers. A
tripwire, installed before the runtime is imported, makes any other subprocess or non-loopback connection
raise and marks the turn `error`.

What is emulated instead of run (companion/desktop.py needs Qt and a screen):
  * RequestWorker.run: prepare_request, then ask(mode='assist', screen_geometry, request_plan). Screen vision
    is off, so no screenshot is ever attached and the A11y-settings highlight shortcut is not mirrored.
  * CompanionWindow.received: navigation actions (AUTO_RUN) are executed at once through the real
    ComputerUse/DesktopContext against the fake bridge, the receipt becomes service.last_action_result and
    the service is asked again with mode='verify', as the UI does. Other actions stay pending.
  * A `confirm` turn is the user pressing the pending button: mac_apps.perform for an app_write (then the
    "then …" follow-up is submitted as the UI would), ComputerUse for confirmed input. `decline` clears it.
  * Replies are judged as the UI shows them: desktop.brief(message, 2, 45), copied into the core and compared
    with the source by a unit test. The raw service text is kept in the record.
  * start_task is never run. A well-formed one is a `handoff`: not a pass, not a failure, outside the pass
    rate. With screen vision off, as here, the real UI answers it with "Enable app awareness and local screen
    vision…", so nothing here proves an on-screen outcome.

Statuses: pass, fail (including an exception the UI shows the user), handoff, error (harness fault or
tripwire only). Latency is reported as model-stage time (Ollama's own durations) first and wall time second;
a run in which calls waited for another client of the server is marked contended and its latency is not
compared. `--rescore RUN` scores a finished run's recorded turns again, offline.

No router held-out request, personal data, cloud provider, screen capture or real app is used.
"""
import argparse
import contextlib
from collections import Counter
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import hashlib
import html
import io
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import tempfile
import time
import traceback
from types import SimpleNamespace
import urllib.request
from unittest.mock import patch
from urllib.parse import parse_qs, unquote, urlsplit

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import request_eval_core as core  # noqa: E402

ENDPOINT = 'http://127.0.0.1:11434'
HARNESS_FILES = ('request_eval.py', 'request_eval_core.py', 'request_cases_dev.py', 'request_cases_acceptance.py')
RUNTIME_MODULES = ('timing', 'models', 'provider', 'ollama', 'storage', 'preferences', 'onboarding', 'paths', 'routes',
                   'app_names', 'web_apps', 'quick_open', 'arithmetic', 'request_plan', 'input_actions', 'media',
                   'desktop_context', 'computer_use', 'mac_apps', 'mail_reader', 'web_lookup', 'weather', 'system_controls', 'service')
# Never inherited by the evaluated code: evaluation switches, cloud credentials and model/endpoint overrides.
CLOUD_ENVIRONMENT = ('A11Y_EVAL', 'A11Y_EVAL_SHOTS', 'ANTHROPIC_API_KEY', 'A11Y_AI_MODEL', 'A11Y_VISION_MODEL', 'A11Y_OLLAMA_URL')
GEOMETRY = {'x': 0, 'y': 0, 'width': 1512, 'height': 982, 'usable': {'x': 0, 'y': 38, 'width': 1512, 'height': 944}}
# Confirmed proposals the UI hands to ComputerUse (CompanionWindow.desktop_action, 'desktop:apply').
CONFIRMABLE = ('focus_app', 'press_control', 'launch_app', 'quit_app', 'new_window', 'close_window', 'close_tab', 'choose_menu',
               'browse_url', 'play_media', 'type_text', 'press_key', 'scroll', 'wait')

CATALOG = (
    ('Safari', 'com.apple.Safari'), ('Google Chrome', 'com.google.Chrome'), ('Notes', 'com.apple.Notes'),
    ('Calculator', 'com.apple.calculator'), ('TextEdit', 'com.apple.TextEdit'), ('Music', 'com.apple.Music'),
    ('Reminders', 'com.apple.reminders'), ('Calendar', 'com.apple.iCal'), ('System Settings', 'com.apple.systempreferences'),
    ('Finder', 'com.apple.finder'), ('Preview', 'com.apple.Preview'), ('Mail', 'com.apple.mail'),
    ('Messages', 'com.apple.MobileSMS'), ('Maps', 'com.apple.Maps'), ('Weather', 'com.apple.weather'),
    ('Clock', 'com.apple.clock'), ('App Store', 'com.apple.AppStore'), ('Terminal', 'com.apple.Terminal'),
    ('Spotify', 'com.spotify.client'), ('Slack', 'com.tinyspeck.slackmacgap'), ('Visual Studio Code', 'com.microsoft.VSCode'),
    ('Zoom', 'us.zoom.xos'))
WINDOW_TITLES = {'Finder': 'Documents', 'Notes': 'Notes', 'TextEdit': 'Untitled', 'Calculator': 'Calculator', 'Music': 'Music',
                 'Reminders': 'Reminders', 'Calendar': 'Calendar', 'System Settings': 'General', 'Preview': 'Floor plan.pdf',
                 'Spotify': 'Spotify', 'Slack': 'Slack', 'Zoom': 'Zoom Workplace', 'Visual Studio Code': 'notes.md'}
DEFAULT_WORLD = {
    'front': {'app': 'Finder', 'title': 'Documents'},
    'running': ['Finder', 'Safari', 'Notes'],
    'windows': [],  # extra windows: [{'app', 'title'}] besides one per running app
    'default_browser': 'Safari',
    'page': {'title': 'Fernhill Community Garden – Opening hours', 'url': 'https://garden.example.org/hours',
             'text': 'Fernhill Community Garden is open from 8 am to 6 pm, Tuesday to Sunday. The tool shed closes at 5 pm.'},
    'events': [], 'reminders': [], 'notes': [], 'mail': [], 'unread': 0, 'contacts': [], 'files': {},
    'battery': {'level': 71, 'state': 'discharging'}, 'disk': {'free_gb': 212, 'total_gb': 494},
    'macos': '15.3.1', 'build': '24D70', 'wifi': 'Fernhill-5G', 'memory_gb': 16, 'dark': False, 'chip': 'Apple M2',
    'volume': {'level': 50, 'muted': False}, 'wifi_on': True, 'bluetooth_on': True,
    'music': {'state': 'paused', 'track': {'name': 'Slow Tide', 'artist': 'Mira Vale'}, 'library': []},
    'home_place': 'Lisbon', 'weather': {}, 'wiki': [], 'shortcuts': ['Morning Routine', 'Tidy Desktop'],
}


class FakeWorld:
    """One scenario's computer: state, a recording desktop bridge and fake command/web answers.

    Results mirror what native/DesktopBridge.swift and the fixed commands return, including its refusal of
    an explicit new tab/window in a browser other than Safari. Every call is recorded with the phase it
    happened in: `service` (while the service handled an utterance), `ui_auto` (navigation the UI runs at
    once), `verify`, or `ui_confirm` (after the user pressed the button)."""

    def __init__(self, spec, home, data, modules):
        unknown = set(spec) - set(DEFAULT_WORLD)
        if unknown:
            raise ValueError('Unknown world fields: '+', '.join(sorted(unknown)))
        merged = deepcopy(DEFAULT_WORLD)
        merged.update(deepcopy(spec))
        self.spec, self.home, self.data, self.m = merged, Path(home), Path(data), modules
        self.bundle = dict(CATALOG)
        self.name = {bundle: name for name, bundle in CATALOG}
        self.front = dict(merged['front'])
        self.running = list(dict.fromkeys(list(merged['running'])+[self.front['app']]))
        missing = [name for name in self.running if name not in self.bundle]
        if missing:
            raise ValueError('World runs apps missing from the catalog: '+', '.join(missing))
        self.windows = [{'app': name, 'title': self.front.get('title') if name == self.front['app'] else self.title(name)}
                        for name in self.running]+[dict(window) for window in merged['windows']]
        self.tabs = {name: [merged['page']['url']] for name in ('Safari', 'Google Chrome') if name in self.running}
        self.reminders = [dict(item) for item in merged['reminders']]
        self.notes = [dict(item, id='note-%d' % index) for index, item in enumerate(merged['notes'])]
        self.calls, self.polls, self.phase, self.tripwire = [], Counter(), 'setup', None
        self.bridge = FakeBridge(self)

    # -- setup -----------------------------------------------------------------------------------
    def materialize(self):
        now = time.time()
        for folder in ('Downloads', 'Desktop', 'Documents', 'Movies', 'Pictures', 'Music'):
            (self.home/folder).mkdir(parents=True, exist_ok=True)
        for folder, files in self.spec['files'].items():
            for item in files:
                path = self.home/folder/item['name']
                path.write_bytes(b'x'*int(item.get('size', 1200)) if 'text' not in item else item['text'].encode())
                stamp = now-60*item.get('minutes_ago', 60)
                os.utime(path, (stamp, stamp))
        self.data.mkdir(parents=True, exist_ok=True)
        (self.data/'ai.json').write_text(json.dumps({'home_place': self.spec['home_place'], 'web_lookups': 'on'}))

    def title(self, name):
        return self.spec['page']['title'] if name in ('Safari', 'Google Chrome') else WINDOW_TITLES.get(name, name)

    def record(self, name, **details):
        self.calls.append({'name': name, 'phase': self.phase, 'args': details})

    def state(self):
        return {'front': self.front['app'], 'running': list(self.running), 'windows': len(self.windows),
                'tabs': {name: list(urls) for name, urls in self.tabs.items()}}

    # -- app state -------------------------------------------------------------------------------
    def bring_forward(self, name, title=None):
        if name not in self.running:
            self.running.append(name)
        if not any(window['app'] == name for window in self.windows):
            self.windows.append({'app': name, 'title': title or self.title(name)})
        window = next(window for window in self.windows if window['app'] == name)
        self.front = {'app': name, 'title': title or window['title']}

    def quit(self, name):
        self.running = [app for app in self.running if app != name]
        self.windows = [window for window in self.windows if window['app'] != name]
        self.tabs.pop(name, None)
        if self.front['app'] == name:
            self.front = {'app': 'Finder', 'title': 'Documents'}
            self.bring_forward('Finder')

    def app_windows(self):
        found, counts = [], Counter()
        for index, window in enumerate(self.windows):
            bundle = self.bundle[window['app']]
            found.append({'window_id': '%s:%d' % (bundle, counts[bundle]), 'bundle_id': bundle, 'name': window['app'],
                          'title': window['title'], 'can_close': True,
                          'bounds': {'x': 60+48*index, 'y': 70+36*index, 'width': 920, 'height': 640}})
            counts[bundle] += 1
        return found

    def epoch(self, day, clock):
        hour, minute = (int(part) for part in clock.split(':'))
        return int((datetime.now()+timedelta(days=day)).replace(hour=hour, minute=minute, second=0, microsecond=0).timestamp())

    # -- fake subprocess.run: fixed commands only, nothing is executed ---------------------------
    def run(self, command, *args, **options):
        argv = [str(part) for part in command] if isinstance(command, (list, tuple)) else None
        if not argv or options.get('shell'):
            self.tripwire.trip('subprocess', 'run '+repr(command)[:200])
        as_text = bool(options.get('text') or options.get('universal_newlines') or options.get('encoding'))

        def done(stdout='', code=0, stderr=''):
            return subprocess.CompletedProcess(argv, code, stdout if as_text else stdout.encode(), stderr if as_text else stderr.encode())
        program, rest = os.path.basename(argv[0]), argv[1:]
        if program == 'osascript' and len(rest) >= 2 and rest[0] == '-e':
            return done(self.script(rest[1], rest[2:]))
        if program == 'open' and rest:
            self.record('open', target=rest[-1], reveal='-R' in rest, app=rest[rest.index('-b')+1] if '-b' in rest[:-1] else '')
            return done()
        if program == 'pmset' and rest == ['-g', 'batt']:
            self.record('system_read', what='battery')
            battery = self.spec['battery']
            source = 'AC Power' if battery['state'] in ('charging', 'charged') else 'Battery Power'
            return done("Now drawing from '%s'\n -InternalBattery-0 (id=4653155)\t%d%%; %s; 3:41 remaining present: true\n"
                        % (source, battery['level'], battery['state']))
        if program == 'sw_vers' and rest in (['-productVersion'], ['-buildVersion']):
            self.record('system_read', what='macos_version')
            return done((self.spec['macos'] if rest == ['-productVersion'] else self.spec['build'])+'\n')
        if program == 'sysctl' and rest[:1] == ['-n'] and len(rest) == 2:
            self.record('system_read', what=rest[1])
            answers = {'hw.memsize': str(self.spec['memory_gb']*2**30), 'machdep.cpu.brand_string': self.spec['chip'],
                       'kern.boottime': '{ sec = %d, usec = 0 } Thu Jan  1 00:00:00 1970' % (time.time()-26*3600)}
            if rest[1] in answers:
                return done(answers[rest[1]]+'\n')
        if program == 'defaults' and rest == ['read', '-g', 'AppleInterfaceStyle']:
            self.record('system_read', what='appearance')
            return done('Dark\n') if self.spec['dark'] else done('', 1, 'The domain/default pair does not exist\n')
        if program == 'networksetup' and rest == ['-getairportnetwork', 'en0']:
            self.record('system_read', what='wifi')
            return done('Current Wi-Fi Network: %s\n' % self.spec['wifi'])
        # System settings (companion/system_controls.py): read and set on the world's state, recorded.
        if program == 'networksetup' and rest == ['-listallhardwareports']:
            return done('Hardware Port: Ethernet\nDevice: en1\n\nHardware Port: Wi-Fi\nDevice: en0\nEthernet Address: 00:00:00:00:00:00\n')
        if program == 'networksetup' and rest == ['-getairportpower', 'en0']:
            self.record('system_read', what='wifi_power')
            return done('Wi-Fi Power (en0): %s\n' % ('On' if self.spec['wifi_on'] else 'Off'))
        if program == 'networksetup' and rest[:2] == ['-setairportpower', 'en0'] and len(rest) == 3 and rest[2] in ('on', 'off'):
            self.record('system_set', setting='wifi', value=rest[2])
            self.spec['wifi_on'] = rest[2] == 'on'
            return done()
        if program == 'system_profiler' and rest == ['SPBluetoothDataType']:
            self.record('system_read', what='bluetooth')
            return done('Bluetooth:\n\n      Bluetooth Controller:\n          State: %s\n' % ('On' if self.spec['bluetooth_on'] else 'Off'))
        if program == 'shortcuts' and rest == ['list']:
            self.record('shortcuts_list')
            return done('\n'.join(self.spec['shortcuts'])+'\n')
        if program == 'shortcuts' and rest[:1] == ['run'] and len(rest) == 2:
            self.record('run_shortcut', name=rest[1])
            return done()
        self.tripwire.trip('subprocess', 'unknown command '+' '.join(argv[:3])[:200])

    def script(self, source, arguments):
        apps, mail, controls = self.m.mac_apps, self.m.mail_reader, self.m.system_controls
        volume = self.spec['volume']
        if source == controls.VOLUME_GET:
            self.record('system_read', what='volume')
            return '%d,%s' % (volume['level'], 'true' if volume['muted'] else 'false')
        if source == controls.VOLUME_SET and len(arguments) == 1 and arguments[0].isdigit():
            self.record('system_set', setting='volume', value=int(arguments[0]))
            volume['level'] = max(0, min(100, int(arguments[0])))
            return ''
        if source == controls.MUTE_SET and len(arguments) == 1:
            self.record('system_set', setting='mute', value=arguments[0] == 'true')
            volume['muted'] = arguments[0] == 'true'
            return ''
        if source == controls.APPEARANCE_SET and len(arguments) == 1:
            self.record('system_set', setting='appearance', value='dark' if arguments[0] == 'true' else 'light')
            self.spec['dark'] = arguments[0] == 'true'
            return ''
        sep, end = apps.SEP, apps.END
        if source == apps.NOTES_LIST:
            self.record('notes_list')
            stamp = datetime.now().replace(microsecond=0)
            return ''.join(sep.join((note['id'], note['name'], (stamp-timedelta(minutes=note.get('minutes_ago', 60*(index+1)))).isoformat()))+end
                           for index, note in enumerate(self.notes))
        if source == apps.NOTE_TEXT:
            self.record('note_text', id=arguments[0])
            note = next((note for note in self.notes if note['id'] == arguments[0]), None)
            return (note['name']+'\n'+note['text']) if note else ''
        if source == apps.NOTE_CREATE:
            lines = [html.unescape(re.sub(r'<[^>]+>', '', line)) for line in re.findall(r'<div>(.*?)</div>', arguments[1], re.S)]
            self.record('note_create', title=arguments[0], body='\n'.join(lines[1:]))
            self.notes.insert(0, {'id': 'note-new-%d' % len(self.notes), 'name': arguments[0], 'text': '\n'.join(lines[1:]), 'minutes_ago': 0})
            return arguments[0]
        if source == apps.CONTACTS:
            self.record('contacts', name=arguments[0])
            return ''.join(sep.join((person['name'], person.get('phones', ''), person.get('emails', '')))+end
                           for person in self.spec['contacts'] if arguments[0].casefold() in person['name'].casefold())
        if source == apps.SAFARI_PAGE or source in apps.CHROMIUM_PAGES.values():
            self.record('read_page', browser='Safari' if source == apps.SAFARI_PAGE else 'Chromium')
            page = self.spec['page']
            return sep.join((page['title'], page['url'], page['text'] if source == apps.SAFARI_PAGE else ''))
        if source == mail.LISTING:
            self.record('mail_list')
            stamp = datetime.now().replace(microsecond=0)
            return ''.join('1:%d:%d' % (index+1, 5000+index)+mail.SEPARATOR
                           +(stamp-timedelta(minutes=item.get('minutes_ago', 30*(index+1)))).isoformat()+mail.END
                           for index, item in enumerate(self.spec['mail'][:int(arguments[0])]))
        if source == mail.MESSAGE:
            index = int(arguments[0].split(':')[1])-1
            self.record('mail_message', position=index+1)
            item = self.spec['mail'][index]
            received = (datetime.now()-timedelta(minutes=item.get('minutes_ago', 30*(index+1)))).strftime('%A, %d %B %Y at %H:%M')
            return mail.SEPARATOR.join((item['sender'], item['subject'], received, item['body']))
        if 'unread count of inbox' in source:
            self.record('mail_unread')
            return str(self.spec['unread'])
        if 'front Finder window' in source and 'POSIX path' in source and len(source) < 400:
            self.record('finder_folder')
            return str(self.home/'Documents')+'/' if self.front['app'] == 'Finder' else ''
        self.tripwire.trip('subprocess', 'unknown osascript source ('+hashlib.sha256(source.encode()).hexdigest()[:12]+')')

    def disk_usage(self, path):
        self.record('system_read', what='storage')
        disk = self.spec['disk']
        return SimpleNamespace(total=int(disk['total_gb']*1e9), used=int((disk['total_gb']-disk['free_gb'])*1e9), free=int(disk['free_gb']*1e9))

    # -- fake web: Open-Meteo and Wikipedia answers from the fixture; no request leaves ----------
    def fetch_weather(self, url, timeout=5):
        parts = urlsplit(url)
        query = {key: values[0] for key, values in parse_qs(parts.query).items()}
        places = list(self.spec['weather'].items())
        if parts.hostname == 'geocoding-api.open-meteo.com':
            self.record('web_fetch', host=parts.hostname, sent=query.get('name', ''))
            for index, (name, data) in enumerate(places):
                if name.casefold() == query.get('name', '').strip().casefold():
                    return {'results': [{'name': name, 'country': data.get('country', ''), 'latitude': 10.0+index, 'longitude': 20.0+index}]}
            return {}
        if parts.hostname == 'api.open-meteo.com':
            self.record('web_fetch', host=parts.hostname, sent='coordinates')
            index = int(round(float(query['latitude'])-10))
            data = places[index][1]
            later = data.get('tomorrow', data)
            today, stamp = date.today(), datetime.now()
            return {'current': {'time': stamp.strftime('%Y-%m-%dT%H:%M'), 'temperature_2m': data['temp'], 'weather_code': data['code'], 'wind_speed_10m': 9},
                    'daily': {'time': [(today+timedelta(days=day)).isoformat() for day in range(8)],
                              'weather_code': [data['code']]+[later['code']]*7,
                              'temperature_2m_max': [data['high']]+[later['high']]*7,
                              'temperature_2m_min': [data['low']]+[later['low']]*7,
                              'precipitation_probability_max': [10]*8},
                    'hourly': {'time': ['%sT%02d:00' % (today.isoformat(), hour) for hour in range(24)],
                               'precipitation_probability': [5]*24, 'weather_code': [data['code']]*24}}
        self.record('web_fetch', host=parts.hostname or '', sent='unexpected')
        raise self.m.weather.WeatherError('I couldn’t reach the weather service just now.')

    def fetch_wiki(self, url, timeout=4):
        parts = urlsplit(url)
        query = {key: values[0] for key, values in parse_qs(parts.query).items()}
        host = parts.hostname or ''
        if not host.endswith('.wikipedia.org'):
            self.record('web_fetch', host=host, sent='unexpected')
            raise OSError('unreachable in the fake world')
        articles = self.spec['wiki']

        def article(title):
            return next((item for item in articles if item['title'].casefold() == title.replace('_', ' ').casefold()), None)

        def matching(phrase):
            wanted = {word for word in re.findall(r'[^\W_]+', phrase.casefold()) if len(word) > 3}
            return [item for item in articles if wanted & {word for word in re.findall(r'[^\W_]+', item['title'].casefold()) if len(word) > 3}]
        if parts.path.startswith('/api/rest_v1/page/summary/'):
            found = article(unquote(parts.path.rsplit('/', 1)[1]))
            self.record('web_fetch', host=host, sent=unquote(parts.path.rsplit('/', 1)[1]))
            return {'title': found['title'], 'extract': found['text'].split('\n')[0], 'type': 'standard'} if found else {}
        if query.get('action') == 'opensearch':
            self.record('web_fetch', host=host, sent=query.get('search', ''))
            return [query.get('search', ''), [item['title'] for item in matching(query.get('search', ''))][:1]]
        if query.get('list') == 'search':
            self.record('web_fetch', host=host, sent=query.get('srsearch', ''))
            return {'query': {'search': [{'title': item['title']} for item in matching(query.get('srsearch', ''))][:5]}}
        if query.get('prop') == 'extracts':
            self.record('web_fetch', host=host, sent=query.get('titles', ''))
            found = article(query.get('titles', ''))
            return {'query': {'pages': [{'title': found['title'], 'extract': found['text']}] if found else []}}
        self.record('web_fetch', host=host, sent='unexpected')
        raise OSError('unreachable in the fake world')


class FakeBridge:
    """Stands in for desktop_context.MacDesktopBridge. An unknown method trips the wire."""

    def __init__(self, world):
        self.world = world

    def __getattr__(self, name):
        if name.startswith('__') or name in ('supports_input', 'sample_targets'):
            raise AttributeError(name)
        return lambda *args, **kwargs: self.world.tripwire.trip('native', 'unsupported bridge call '+name)

    def _app(self, bundle_id):
        name = self.world.name.get(bundle_id)
        if name is None:
            raise RuntimeError('That installed application is no longer available.')
        return name

    def catalog(self):
        self.world.polls['catalog'] += 1
        return [{'name': name, 'bundle_id': bundle} for name, bundle in CATALOG]

    def request_access(self):
        return {'accessibility': 'granted'}

    def sample(self):
        world = self.world
        world.polls['sample'] += 1
        windows = world.app_windows()
        front = next((window for window in windows if window['name'] == world.front['app']), None)
        value = {'platform': 'macOS', 'accessibility': 'granted', 'status': 'available', 'installed_apps': self.catalog(),
                 'available_apps': [{'name': name, 'bundle_id': world.bundle[name]} for name in world.running],
                 'onscreen_window_candidates': [{'app': window['name'], 'bundle_id': window['bundle_id'], 'bounds': window['bounds']} for window in windows],
                 'app_windows': windows, 'controls': [],
                 'app': {'name': world.front['app'], 'bundle_id': world.bundle[world.front['app']], 'pid': 4000+list(world.bundle).index(world.front['app'])}}
        if front:
            value['window'] = {'title': world.front.get('title') or front['title'], 'bounds': front['bounds']}
            if world.front.get('focused_role'):
                value['focused_role'] = world.front['focused_role']
                value['focused_bounds'] = {'x': front['bounds']['x']+20, 'y': front['bounds']['y']+80, 'width': 860, 'height': 520}
        return value

    def sample_target(self, bundle_id):
        return self.sample()

    def app_state(self, bundle_id):
        world = self.world
        world.polls['app_state'] += 1
        name = world.name.get(bundle_id)
        running = name in world.running
        return {'bundle_id': bundle_id, 'running': running, 'active': running and world.front['app'] == name,
                'observation': 'process_observed' if running else 'not_observed'}

    def media_state(self):
        world = self.world
        world.record('media_state')
        if 'Music' not in world.running:
            return {'player_state': 'not_running'}
        music = world.spec['music']
        return {'player_state': music['state'], 'track': dict(music['track'], id='track-0')}

    def activate(self, bundle_id):
        name = self._app(bundle_id)
        self.world.record('activate', app=name)
        self.world.bring_forward(name)
        return {'activated': True, 'bundle_id': bundle_id}

    def press(self, action):
        self.world.record('press', label=action.get('label', ''), app=self.world.name.get(action.get('bundle_id'), ''))
        return {'submitted': True, 'verification_required': True}

    def application_action(self, action):
        world, kind = self.world, action['type']
        if kind in ('type_text', 'press_key', 'scroll'):
            world.record('input' if kind != 'scroll' else 'scroll', type=kind, app=world.front['app'],
                         value=action.get('text', action.get('key', action.get('direction', ''))))
            return {'submitted': True, 'verification_required': True}
        if kind == 'music_search':
            world.record('music_search', query=action['query'])
            return {'submitted': True, 'verification_required': True}
        if kind == 'play_media':
            world.record('play_media', query=action['query'], artist=action['artist'])
            world.bring_forward('Music')
            music = world.spec['music']
            tracks = [track for track in music['library'] if track['name'].casefold() == action['query'].casefold()
                      and (not action['artist'] or action['artist'].casefold() in track.get('artist', '').casefold())]
            if len(tracks) != 1:
                return {'submitted': True, 'outcome': 'not_found' if not tracks else 'ambiguous', 'play_submitted': False, 'requested_id': '',
                        'player_state': music['state'], 'track': dict(music['track'], id='track-0'), 'candidates': tracks[:5]}
            music.update(state='playing', track={'name': tracks[0]['name'], 'artist': tracks[0].get('artist', '')})
            return {'submitted': True, 'outcome': 'play_submitted', 'play_submitted': True, 'requested_id': 'track-0',
                    'player_state': 'playing', 'track': dict(music['track'], id='track-0')}
        if kind == 'choose_menu':
            world.record('choose_menu', path=action['path'], app=world.name.get(action['bundle_id'], ''))
            return {'submitted': True, 'path': action['path']}
        if kind not in ('browse_url', 'launch_app', 'quit_app', 'new_window', 'close_tab', 'close_window'):
            world.tripwire.trip('native', 'unsupported application action '+str(kind))
        name = self._app(action['bundle_id'])
        if kind == 'browse_url':
            mode = action.get('browser_mode', 'default')
            if mode in ('tab', 'window') and name != 'Safari':
                world.record('browse_refused', app=name, url=action['url'], mode=mode)
                # DesktopBridge.swift (--browse): explicit placement is implemented for Safari only.
                raise RuntimeError('Explicit window/tab placement currently supports Safari. No fallback navigation was sent.')
            world.record('browse_url', app=name, url=action['url'], mode=mode, window_hint=action.get('window_hint', ''))
            was_running = name in world.running
            tabs = world.tabs.setdefault(name, [])
            wanted = urlsplit(action['url'])
            reused = next((tab for tab in tabs if (tab == action['url'] if wanted.query else
                           urlsplit(tab).hostname == wanted.hostname)), None) if mode == 'default' and was_running else None
            world.bring_forward(name, title=wanted.hostname)
            if reused:
                return {'submitted': True, 'verification_required': True, 'tab_placement': 'existing_tab', 'reused_tab': True,
                        'browser_observation': {'url': reused, 'window_count': 1, 'tab_count': len(tabs)}}
            if mode == 'current' and tabs:
                tabs[-1] = action['url']
            else:
                tabs.append(action['url'])
            if name != 'Safari' and mode == 'default':
                return {'submitted': True, 'verification_required': True, 'tab_placement': 'browser_preference'}
            return {'submitted': True, 'verification_required': True, 'tab_placement': mode if mode != 'default' else 'window',
                    'browser_observation': {'url': action['url'], 'window_count': 1, 'tab_count': len(tabs)}}
        if kind == 'launch_app':
            world.record('launch_app', app=name)
            world.bring_forward(name)
        elif kind == 'quit_app':
            if name not in world.running:
                raise RuntimeError('That application is no longer running.')
            world.record('quit_app', app=name)
            world.quit(name)
        elif kind == 'new_window':
            world.record('new_window', app=name)
            world.windows.append({'app': name, 'title': world.title(name)})
            world.bring_forward(name)
        elif kind == 'close_tab':
            world.record('close_tab', app=name)
            if world.tabs.get(name):
                world.tabs[name].pop()
        elif kind == 'close_window':
            world.record('close_window', app=name, title=action.get('window_title', ''))
            index = int(action['window_id'].rsplit(':', 1)[1])
            own = [window for window in world.windows if window['app'] == name]
            if index >= len(own):
                raise RuntimeError('That window is no longer open.')
            world.windows.remove(own[index])
            if world.front['app'] == name and not any(window['app'] == name for window in world.windows):
                world.front = {'app': 'Finder', 'title': 'Documents'}
                world.bring_forward('Finder')
        return {'submitted': True, 'verification_required': True}

    # Calendar and Reminders (EventKit in the real helper)
    def calendar_events(self, start, end):
        world = self.world
        world.record('calendar_events', start=int(start), end=int(end))
        events = []
        for item in world.spec['events']:
            begins = world.epoch(item.get('day', 0), item.get('start', '09:00'))
            finishes = world.epoch(item.get('day', 0), item.get('end', item.get('start', '09:00')))
            if begins < end and max(finishes, begins+60) > start:
                events.append({'title': item['title'], 'start': begins, 'end': max(finishes, begins+60), 'all_day': bool(item.get('all_day')),
                               'location': item.get('location', ''), 'calendar': item.get('calendar', 'Home')})
        return sorted(events, key=lambda event: event['start'])

    def reminders(self):
        world = self.world
        world.record('reminders')
        found = []
        for index, item in enumerate(world.reminders):
            entry = {'title': item['title'], 'list': item.get('list', 'Reminders'), 'id': item.setdefault('id', 'reminder-%d' % index)}
            if item.get('due'):
                entry['due'] = world.epoch(item['due'].get('day', 0), item['due'].get('time', '09:00'))
            found.append(entry)
        return found

    def add_event(self, title, start, end):
        self.world.record('add_event', title=title, start=int(start), end=int(end))
        return {'submitted': True, 'calendar': 'Home'}

    def add_reminder(self, title, due):
        self.world.record('add_reminder', title=title, due=int(due or 0))
        self.world.reminders.append({'title': title, 'id': 'reminder-new-%d' % len(self.world.reminders)})
        return {'submitted': True, 'list': 'Reminders'}

    def _remove(self, name, identifier):
        item = next((item for item in self.world.reminders if item.get('id') == identifier), None)
        if item is None:
            raise RuntimeError('That reminder no longer exists.')
        self.world.record(name, id=identifier, title=item['title'])
        self.world.reminders.remove(item)
        return {'submitted': True}

    def complete_reminder(self, identifier):
        return self._remove('complete_reminder', identifier)

    def delete_reminder(self, identifier):
        return self._remove('delete_reminder', identifier)


class Session:
    """The part of companion/desktop.py between a request and its result, without Qt or a screen."""

    def __init__(self, service, desktop, world, provider, modules):
        self.service, self.desktop, self.world, self.provider, self.m = service, desktop, world, provider, modules
        self.pending, self.last_request = None, ''

    def say(self, request, observed):
        # CompanionWindow.ask: a new request drops an unconfirmed proposal. RequestWorker.run follows.
        self.pending = None
        self.world.phase = 'service'
        self.desktop.collect_once()  # the app's poller keeps this fresh every two seconds
        try:
            plan = self.service.prepare_request(request)
            options = {'mode': 'assist', 'screen_geometry': deepcopy(GEOMETRY)}
            if plan is not None:
                options['request_plan'] = plan
                observed.setdefault('route', {'operation': plan.get('operation'), 'computed': bool(plan.get('computed')),
                                              'resolved': bool(plan.get('resolved_request'))})
            response = self.service.ask(request, **options)
        except (ValueError, RuntimeError, PermissionError) as error:
            # RequestWorker emits failed(str(error)): the user is shown that text. A product failure, not a harness error.
            observed['service_exception'] = type(error).__name__+': '+str(error)[:400]
            observed['replies'].append(str(error))
            return
        self.received(request, response, observed)

    def received(self, request, response, observed):
        action = response.get('desktop_action')
        if 'decision' not in observed:
            observed['decision'] = action
        observed['replies'].append(response.get('message', ''))
        if response.get('full_text'):
            observed.setdefault('full_text_replies', []).append(len(observed['replies'])-1)  # shown whole by the UI
        if not action:
            return
        kind = action.get('type')
        if kind in core.AUTO_RUN:
            self.execute(request, action, observed, 'ui_auto')
        elif kind == 'start_task':
            # The UI would start the task loop now (CompanionWindow.start_computer_task). With screen vision off, as
            # here, it instead says "Enable app awareness and local screen vision in Settings, then try this task again."
            observed['task_offered'] = True
        elif kind in ('move_companion', 'move_to_point'):
            observed['ui_effect'] = 'companion_moved'
        elif kind == 'click_point':
            observed['replies'].append('A screen click needs a fresh local capture. Enable screen vision and ask again.')
        else:
            self.pending = action  # shown as a button; nothing runs until the user confirms

    def execute(self, request, proposal, observed, phase):
        """CompanionWindow.execute_desktop_action: run it, keep the receipt, ask the service to verify."""
        self.world.phase = phase
        entry = {'type': proposal.get('type'), 'ok': False, 'phase': phase}
        observed['executed'].append(entry)
        try:
            receipt = self.m.computer_use.ComputerUse(self.desktop, vision=None, provider=self.provider).execute_confirmed(proposal)
        except (ValueError, RuntimeError) as error:
            entry['error'] = str(error)[:300]
            observed['replies'].append(str(error))  # CompanionWindow.failed shows it
            return
        entry.update(ok=True, outcome=(receipt.get('native_result') or {}).get('outcome'))
        self.service.last_action_result = receipt
        self.world.phase = 'verify' if phase == 'ui_auto' else phase
        self.desktop.collect_once()
        try:
            verified = self.service.ask(request, mode='verify', screen_geometry=deepcopy(GEOMETRY))
        except (ValueError, RuntimeError, PermissionError) as error:
            observed['service_exception'] = type(error).__name__+': '+str(error)[:400]
            observed['replies'].append(str(error))
            return
        observed['replies'].append(verified.get('message', ''))
        follow = verified.get('desktop_action')
        if follow:
            observed['follow_up_action'] = follow
            if follow.get('type') == 'start_task':
                observed['task_offered'] = True
            else:
                self.pending = follow

    def confirm(self, observed):
        proposal, self.pending = self.pending, None
        if not proposal:
            observed['nothing_pending'] = True
            return
        observed['confirmed'] = proposal
        kind = proposal.get('type')
        self.world.phase = 'ui_confirm'
        if kind == 'app_write':
            # AppWriteWorker, then CompanionWindow.desktop_action's done().
            try:
                message = self.m.mac_apps.perform(proposal, bridge=self.desktop.bridge)
            except (PermissionError, RuntimeError, ValueError) as error:
                observed['confirm_failed'] = str(error)[:300]
                observed['replies'].append('I couldn’t do that. '+str(error))
                return
            self.service.store.add_exchange(self.service.identity, proposal['label'], message)
            observed['replies'].append(message)
            rest = str(proposal.get('remaining_request', '')).strip()
            if rest:
                named = proposal.get('title') or proposal.get('name') or ''
                follow = rest+(' (the '+proposal.get('kind', 'item').split('_')[-1]+' “'+named+'”)' if named else '')
                observed['follow_up_request'] = follow
                self.say(follow, observed)
        elif kind in CONFIRMABLE:
            request = observed.get('last_request') or 'Check the action result.'
            self.execute(request, proposal, observed, 'ui_confirm')
        else:
            observed['unemulated'] = kind  # request_screen, request_app_access, capture_once: consent dialogs and capture

    def decline(self, observed):
        if not self.pending:
            observed['nothing_pending'] = True  # no button to decline: the scenario's proposal never appeared
        observed['declined'] = (self.pending or {}).get('type')
        self.pending = None
        observed['replies'].append('Okay, I won’t do that.')  # CompanionWindow.voice_heard; no service call


class Harness:
    """Owns the tripwire and builds one isolated scenario at a time around the real service."""

    def __init__(self, modules, provider):
        self.m, self.provider, self.world = modules, provider, None
        self.wire = core.Tripwire(run=self._run)

    def _run(self, *args, **options):
        if self.world is None:
            self.wire.trip('subprocess', 'run outside a scenario')
        return self.world.run(*args, **options)

    @contextlib.contextmanager
    def scenario(self, world_spec):
        """(session, world) with a temporary Store, data folder and HOME, the fakes patched in and local
        inference forced. Must be used inside `with harness.wire`."""
        import shutil
        m = self.m
        with tempfile.TemporaryDirectory(prefix='a11y-request-eval-') as folder, contextlib.ExitStack() as stack:
            home, data = Path(folder)/'home', Path(folder)/'data'
            world = FakeWorld(world_spec, home, data, m)
            world.tripwire = self.wire
            world.materialize()
            stack.enter_context(patch.dict(os.environ, {'HOME': str(home), 'A11Y_DATA_DIR': str(data), 'A11Y_WEB_ANSWERS': '1',
                                                       'A11Y_ROUTES': '0', 'A11Y_AI_PROVIDER': 'ollama'}))
            for name in CLOUD_ENVIRONMENT:
                os.environ.pop(name, None)  # restored by patch.dict
            for owner, name, value in ((m.mac_apps, 'available', lambda: True), (m.mail_reader, 'available', lambda: True),
                                       (m.desktop_context, 'default_browser_id', lambda: world.bundle[world.spec['default_browser']]),
                                       (m.web_lookup, 'fetch_json', world.fetch_wiki), (m.weather, 'fetch_json', world.fetch_weather),
                                       (shutil, 'disk_usage', world.disk_usage)):
                stack.enter_context(patch.object(owner, name, value))
            self.world = world
            stack.callback(setattr, self, 'world', None)
            service = m.service.Companion(m.storage.Store(data, persist_history=True), m.models.Identity('fixture', 'user'), self.provider)
            # The app's poller refreshes the observation every two seconds; a fixed clock keeps it fresh
            # however long a model call takes.
            desktop = m.desktop_context.DesktopContext(world.bridge, clock=lambda: 1000.0)
            desktop.enabled = True
            service.desktop_context = desktop
            yield Session(service, desktop, world, self.provider, m), world


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def harness_hashes():
    files = {name: sha256(SCRIPTS/name) for name in HARNESS_FILES}
    return hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest(), files


def source_hashes(source):
    return {str(path.relative_to(source)): sha256(path) for pattern in ('companion/*.py', 'native/*.swift')
            for path in sorted(Path(source).glob(pattern))}


def local_json(path):
    try:
        with urllib.request.urlopen(ENDPOINT+path, timeout=5) as response:
            return json.load(response)
    except (OSError, ValueError):
        return None


def model_digest(model):
    models = (local_json('/api/tags') or {}).get('models') or []
    return next((item.get('digest') for item in models if item.get('name') == model), None)


def inference_settings(source):
    """Decoding options as written in the evaluated companion/ollama.py, plus this process's overrides."""
    try:
        text = (Path(source)/'companion'/'ollama.py').read_text(encoding='utf-8')
    except OSError:
        text = ''

    def found(pattern):
        match = re.search(pattern, text)
        return match.group(1) if match else None
    return {'endpoint': ENDPOINT, 'timeout_seconds': 120,
            'temperature': found(r"'temperature':\s*([\d.]+)"), 'generation_seed': found(r"'seed':\s*(\d+)"),
            'context': os.getenv('A11Y_OLLAMA_CONTEXT') or found(r"A11Y_OLLAMA_CONTEXT',\s*'(\d+)'"),
            'keep_alive': os.getenv('A11Y_OLLAMA_KEEP_ALIVE') or found(r"A11Y_OLLAMA_KEEP_ALIVE',\s*'([^']+)'"),
            'thinking_override': os.getenv('A11Y_OLLAMA_THINKING', 'default'),
            'read_from': 'companion/ollama.py of the evaluated source; None means the pattern was not found there'}


def hardware():
    def read(*command):
        try:
            return subprocess.run(list(command), capture_output=True, text=True, timeout=5).stdout.strip() or None
        except (OSError, subprocess.SubprocessError):
            return None
    memory = read('sysctl', '-n', 'hw.memsize')
    return {'system': platform.platform(), 'machine': platform.machine(), 'python': platform.python_version(),
            'chip': read('sysctl', '-n', 'machdep.cpu.brand_string'), 'model': read('sysctl', '-n', 'hw.model'),
            'memory_gb': round(int(memory)/2**30) if memory and memory.isdigit() else None, 'cpus': os.cpu_count()}


def select(scenarios, only):
    unknown = [family for family in only or () if family not in core.FAMILIES]
    if unknown:
        raise SystemExit('Unknown family: %s. Families: %s' % (', '.join(unknown), ', '.join(core.FAMILIES)))
    return [item for item in scenarios if not only or item['family'] in only]


def planned_by_family(scenarios, runs):
    planned = Counter()
    for item in scenarios:
        planned[item['family']] += len(item['turns'])*runs
    return dict(planned)


def listing(scenarios, split):
    lines = []
    counts = planned_by_family(scenarios, 1)
    for family in core.FAMILIES:
        if family in counts:
            lines.append('%-18s %3d scenarios %3d turns' % (family, sum(item['family'] == family for item in scenarios), counts[family]))
    lines.append('%-18s %3d scenarios %3d turns' % ('total', len(scenarios), core.turn_count(scenarios)))
    trivial = core.trivial_policy_counts(scenarios)
    lines.append('turns a trivial policy passes: '+', '.join('%s %d' % (name, values['pass']) for name, values in trivial.items()))
    if split == 'dev':  # acceptance wording stays out of the terminal
        for item in scenarios:
            lines.append('%s [%s]' % (item['id'], item['family']))
            lines.extend('    '+(turn.get('say') or '('+turn['ui']+')') for turn in item['turns'])
    return '\n'.join(lines)


def import_runtime(source):
    """The evaluated checkout's modules, all imported now so a later edit cannot change the run."""
    sys.path.insert(0, str(source))
    import importlib
    modules = SimpleNamespace(**{name: importlib.import_module('companion.'+name) for name in RUNTIME_MODULES})
    origin = Path(modules.service.__file__).resolve()
    if Path(source).resolve() not in origin.parents:
        raise SystemExit('companion was imported from %s, not from --source-root %s' % (origin, source))
    return modules


def make_provider(modules, model):
    """The app's own local provider on the loopback endpoint; never a cloud provider."""
    return modules.ollama.OllamaProvider(modules.provider.AIConfig(base_url=ENDPOINT, model=model, timeout=120))


def timing_summary(events):
    """(model calls, per-call records, other stages). A call's wall time is measured around the request by the
    runtime; total/load/prompt/eval are Ollama's own figures; unaccounted is the wall time outside them."""
    calls = []
    for event in events:
        if event.get('event') == 'ollama.finished':
            call = {'wall_ms': event.get('elapsed_ms'), 'total_ms': event.get('total_ms'), 'load_ms': event.get('load_ms'),
                    'prompt_ms': event.get('prompt_eval_ms'), 'eval_ms': event.get('eval_ms'), 'prompt_tokens': event.get('prompt_eval_count'),
                    'output_tokens': event.get('eval_count'), 'truncated': event.get('truncated')}
            call['unaccounted_ms'] = core.call_times(call)[2]
            calls.append(call)
    stages = [dict(event) for event in events if not str(event.get('event', '')).startswith('ollama.')]
    return sum(event.get('event') == 'ollama.start' for event in events), calls, stages


def run_turn(session, world, tripwire, timings, turn):
    kind = 'say' if 'say' in turn else turn['ui']
    observed = {'kind': kind, 'replies': [], 'executed': []}
    if kind == 'confirm':
        observed['last_request'] = session.last_request
    offset, call_offset, wire_offset = len(timings), len(world.calls), len(tripwire.violations)
    started_local, started = datetime.now(), time.monotonic()
    captured = io.StringIO()
    try:
        with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
            if kind == 'say':
                session.last_request = turn['say']
                session.say(turn['say'], observed)
            elif kind == 'confirm':
                session.confirm(observed)
            else:
                session.decline(observed)
    except core.TripwireViolation as error:
        observed.update(error='TripwireViolation: '+str(error)[:300], error_kind='tripwire')
    except Exception as error:  # a harness fault, or a runtime exception of a kind the UI only reports generically; see traceback
        observed.update(error=type(error).__name__+': '+str(error)[:400], error_kind='unexpected_exception',
                        traceback=traceback.format_exc()[-1500:])
    seconds = round(time.monotonic()-started, 4)
    observed.pop('last_request', None)
    observed.setdefault('decision', None)
    observed.update(native_calls=world.calls[call_offset:], tripwire=tripwire.violations[wire_offset:], pending=session.pending,
                    apps=dict(world.bundle), started_local=started_local.isoformat(), finished_local=datetime.now().isoformat(),
                    world_after=world.state())
    if observed['tripwire'] and not observed.get('error'):
        observed.update(error='A blocked call was attempted and swallowed by the runtime.', error_kind='tripwire')
    model_calls, calls, stages = timing_summary(timings[offset:])
    return observed, seconds, model_calls, calls, stages


OBSERVED_KEYS = ('full_text_replies', 'service_exception', 'error', 'error_kind', 'traceback', 'tripwire', 'task_offered', 'ui_effect',
                 'follow_up_action', 'follow_up_request', 'confirmed', 'declined', 'nothing_pending', 'confirm_failed', 'unemulated', 'world_after')


def turn_row(repeat, item, index, turn, observed, seconds, model_calls, calls, stages):
    """One immutable record: what was asked and expected, what happened, the verdict and the timings."""
    verdict = core.score_turn(turn, observed)
    return {'run': repeat, 'scenario': item['id'], 'family': item['family'], 'turn': index, 'kind': observed['kind'],
            'request': turn.get('say') or '('+turn['ui']+')', 'expected': turn['expect'],
            'decision': observed.get('decision'), 'replies': observed['replies'], 'reply': verdict['shown'][-1] if verdict['shown'] else '',
            'executed': observed.get('executed') or [], 'native_calls': observed.get('native_calls') or [], 'pending': observed.get('pending'),
            **verdict, 'seconds': seconds, 'model_calls': model_calls, 'calls': calls, 'stages': stages,
            'fake_lookup': any(call.get('name') == 'web_fetch' for call in observed.get('native_calls') or ()),
            'route': observed.get('route'), 'started_local': observed.get('started_local'),
            **{key: observed[key] for key in OBSERVED_KEYS if observed.get(key) not in (None, [], '')}}


def run(args, scenarios, out=print):
    source = args.source_root.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    git = subprocess.run(['git', '-C', str(source), 'rev-parse', 'HEAD'], capture_output=True, text=True)
    dirty = subprocess.run(['git', '-C', str(source), 'status', '--porcelain'], capture_output=True, text=True)
    machine = hardware()
    digest = model_digest(args.model)
    harness_digest, harness_files = harness_hashes()
    sources = source_hashes(source)
    planned = core.turn_count(scenarios)*args.runs
    import uuid
    manifest = {
        'protocol': core.PROTOCOL, 'run_id': uuid.uuid4().hex, 'split': args.split, 'seed': args.seed, 'only': sorted(args.only or []), 'runs': args.runs,
        'case_set_hash': core.case_set_hash(scenarios), 'harness_sha256': harness_digest, 'harness_files': harness_files,
        'source': str(source), 'git_head': git.stdout.strip() or None,
        'git_dirty_files': [line[3:] for line in dirty.stdout.splitlines() if line.strip()],
        'source_files': sources, 'source_identity': hashlib.sha256(json.dumps(sources, sort_keys=True).encode()).hexdigest(),
        'model': args.model, 'model_digest': digest, 'ollama_version': (local_json('/api/version') or {}).get('version'),
        'inference_settings': inference_settings(source),
        'inference_environment': {key: os.environ.get(key) for key in ('A11Y_OLLAMA_KEEP_ALIVE', 'A11Y_OLLAMA_CONTEXT', 'A11Y_OLLAMA_THINKING')},
        'hardware': machine, 'started_utc': datetime.now(timezone.utc).isoformat(), 'command': sys.argv,
        'planned_scenarios': len(scenarios)*args.runs, 'planned_turns': planned, 'planned_by_family': planned_by_family(scenarios, args.runs),
        'trivial_policy_counts': core.trivial_policy_counts(scenarios),
        'condition': 'The app\'s own warm-up runs once before the first turn; the model cache is not cleared between turns.',
        'isolation': 'Tripwire on subprocesses and non-loopback connections from before the runtime is imported; temporary HOME and data folder '
                     'for the whole run and a fresh Store, data folder and HOME per scenario; fake desktop bridge, commands and web answers; '
                     'app awareness on, screen vision off; online lookups answered by fixtures.',
        'emulation': 'UI steps between a request and its result are emulated (see the module docstring); start_task is not executed.'}
    (args.output/'manifest.json').write_text(json.dumps(manifest, indent=2, ensure_ascii=False)+'\n')
    if digest is None:
        out('Model %s is not available from local Ollama at %s; nothing was run.' % (args.model, ENDPOINT))
        (args.output/'summary.json').write_text(json.dumps({'identity': core.identity(manifest), 'completed': False,
                                                            'aborted': 'model unavailable', 'planned_turns': planned}, indent=2)+'\n')
        return 2
    rows, timings, completed, warm, listening = [], [], False, {}, None
    harness = Harness(None, None)
    wire = harness.wire
    try:
        with wire, tempfile.TemporaryDirectory(prefix='a11y-request-eval-run-') as base, \
                patch.dict(os.environ, {'HOME': str(Path(base)/'home'), 'A11Y_DATA_DIR': str(Path(base)/'data'), 'A11Y_AI_PROVIDER': 'ollama',
                                        'A11Y_WEB_ANSWERS': '0', 'A11Y_ROUTES': '0'}), \
                (args.output/'turns.jsonl').open('x') as turns_file:
            for name in CLOUD_ENVIRONMENT:
                os.environ.pop(name, None)  # restored by patch.dict
            (Path(base)/'home').mkdir()
            harness.m = m = import_runtime(source)
            harness.provider = provider = make_provider(m, args.model)
            listening = m.timing.listeners
            listening.append(timings.append)
            started = time.monotonic()
            with contextlib.redirect_stdout(io.StringIO()):
                provider.warm_up([m.request_plan.INTENT_SYSTEM, m.service.SYSTEM+m.service.DESKTOP_INSTRUCTIONS])
            warm = {'seconds': round(time.monotonic()-started, 3), 'model_calls': timing_summary(timings)[0]}
            done = 0
            for repeat in range(args.runs):
                for item in scenarios:
                    with harness.scenario(item['world']) as (session, world):
                        for index, turn in enumerate(item['turns']):
                            row = turn_row(repeat, item, index, turn, *run_turn(session, world, wire, timings, turn))
                            rows.append(row)
                            turns_file.write(json.dumps(row, ensure_ascii=False, default=str)+'\n')
                            turns_file.flush()
                            done += 1
                            if args.split == 'dev':
                                out('%s[%d] run=%d %s %.2fs calls=%d %s' % (item['id'], index, repeat, row['status'], row['seconds'], row['model_calls'],
                                                                            '; '.join(row['reasons'])[:160]))
                            else:
                                out('turn %d/%d' % (done, planned))
            completed = True
    finally:
        if listening is not None:
            listening.remove(timings.append)
        summary = core.summarize(rows, planned=planned, completed=completed, planned_by_family=manifest['planned_by_family'])
        summary.update(identity=core.identity(manifest), run_id=manifest['run_id'], runs=args.runs, warm_up=warm,
                       finished_utc=datetime.now(timezone.utc).isoformat(),
                       source_changed=source_hashes(source) != sources, harness_changed=harness_hashes()[0] != harness_digest,
                       tripwire_violations=len(wire.violations))
        (args.output/'summary.json').write_text(json.dumps(summary, indent=2, ensure_ascii=False)+'\n')
    report(summary, rows, args.split, out)
    code = exit_code(summary)
    if args.compare:
        code = max(code, write_comparison(args.compare, args.output, args.output/'comparison.json', args.split, out))
    return code


def exit_code(summary):
    """0 only for a completed run of unchanged code with no errored turn; 2 incomplete or changed; 4 errors."""
    if not summary.get('completed') or summary.get('source_changed') or summary.get('harness_changed'):
        return 2
    return 4 if summary['statuses']['error'] or summary.get('tripwire_violations') else 0


def report(summary, rows, split, out=print):
    counts, latency = summary['statuses'], summary['latency']
    out('%s: planned %d, attempted %d: pass %d, fail %d, handoff %d, error %d (not attempted %d)' % (
        split, summary['planned_turns'], summary['attempted'], counts['pass'], counts['fail'], counts['handoff'], counts['error'], summary['not_attempted']))
    out('decided (pass+fail) %d, pass rate over decided %s; a handoff is a well-formed start_task that was not run' % (summary['decided'], summary['pass_rate_decided']))
    out('false_success %d, wrong_action %d, unauthorized_action %d, replies trimmed by the UI %d' % (
        summary['false_success'], summary['wrong_action'], summary['unauthorized_action'], summary['replies_trimmed_by_ui']))
    for family, values in summary['families'].items():
        out('  %-18s pass %2d  fail %2d  handoff %2d  error %2d  of %2d planned' % (
            family, values['pass'], values['fail'], values['handoff'], values['error'], values['planned']))
    for name, values in summary['by_route'].items():
        out('  %-18s pass %2d  fail %2d  handoff %2d  error %2d' % (name, values['pass'], values['fail'], values['handoff'], values['error']))
    stage, wall, code = latency['model_stage_seconds'], latency['wall_seconds_model_routed'], latency['wall_seconds_code_routed']
    out('latency, %d model-routed utterances: model stage p50 %s s, p95 %s s; wall p50 %s s, p95 %s s' % (stage['n'], stage['p50'], stage['p95'], wall['p50'], wall['p95']))
    out('latency, %d code-routed utterances (no model call): wall p50 %s s, p95 %s s' % (code['n'], code['p50'], code['p95']))
    out('model calls %d, output tokens %d, unaccounted wait %d ms, calls waiting %d, reloading %d, fake-lookup turns %d%s' % (
        summary['model_calls'], summary['output_tokens'], latency['unaccounted_ms_total'], latency['calls_waiting'], latency['calls_reloading'],
        latency['fake_lookup_turns'], '  CONTENDED: do not compare latency' if latency['contended'] else ''))
    for calls, values in latency['by_model_calls'].items():
        out('  %s model call(s): n %d, model stage p50 %s s, wall p50 %s s, wall p95 %s s' % (
            calls, values['wall_seconds']['n'], values['model_stage_seconds']['p50'], values['wall_seconds']['p50'], values['wall_seconds']['p95']))
    if summary.get('source_changed') or summary.get('harness_changed'):
        out('WARNING: %s changed during the run; do not use this result.' % ('source' if summary.get('source_changed') else 'harness'))
    if split == 'dev':  # reasons and per-turn detail of the acceptance split stay in the output files
        out('reasons: '+', '.join('%s %d' % item for item in summary['reasons'].items()))
        for row in rows:
            if row['status'] in ('fail', 'error'):
                out('- %s[%d] %s: %s' % (row['scenario'], row['turn'], row['status'], '; '.join(row['reasons']) or row.get('error', '')))
                out('    request: %s' % row['request'])
                out('    decision: %s' % json.dumps({key: value for key, value in (row['decision'] or {}).items() if key != 'label'}, ensure_ascii=False)[:300])
                out('    shown: %s' % ' | '.join(row.get('shown') or row['replies'])[:300])


def write_comparison(base_path, candidate_path, destination, split, out=print):
    try:
        base, candidate = core.load_result(base_path), core.load_result(candidate_path)
    except (OSError, ValueError) as error:
        out('No comparison: a result could not be read (%s).' % error)
        return 3
    result = core.compare(base, candidate)
    if not result['comparable']:
        out('No comparison written: '+'; '.join(result['reasons']))
        return 3
    Path(destination).write_text(json.dumps(result, indent=2)+'\n')
    for key in core.STATUSES+('false_success', 'wrong_action', 'unauthorized_action', 'model_calls', 'output_tokens'):
        out('%-20s baseline %s  candidate %s  delta %s' % (key, result[key]['baseline'], result[key]['candidate'], result[key]['delta']))
    if result['latency']['comparable']:
        for key in ('model_stage_p50', 'model_stage_p95', 'wall_p50', 'wall_p95'):
            out('%-20s baseline %s  candidate %s  delta %s' % (key, result['latency'][key]['baseline'], result['latency'][key]['candidate'], result['latency'][key]['delta']))
    else:
        out(result['latency']['reason'])
    for family, values in result['families'].items():
        out('  %-18s pass %s -> %s' % (family, values['baseline'], values['candidate']))
    if 'turn_changes' in result:
        changes = result['turn_changes']
        out('turns regressed %d, improved %d, changed in any run %d' % (len(changes['regressed']), len(changes['improved']), len(changes['flips'])))
        if split == 'dev':
            for name in ('regressed', 'improved'):
                for scenario, turn in changes[name]:
                    out('  %s: %s[%d]' % (name, scenario, turn))
    out('Comparison written to %s' % destination)
    return 0


# Scenarios whose fixture world changed after a protocol version: their recorded turns ran on other data.
RESCORE_WORLD_CHANGED = {'request-outcomes-v1': ('cancel-then-check',)}


def rescore(source_dir, output, out=print):
    """Score the recorded turns of a finished run again with the current scorer and cases, offline. A turn is
    re-scored only if the current case set still has that scenario and turn with the same request; the rest
    are counted as unmatched. Records from before v2 lack the UI's full-text flag (a reply with line breaks is
    taken as shown whole) and Ollama's total duration (load+prompt+eval stands in)."""
    source_dir = Path(source_dir)
    manifest = json.loads((source_dir/'manifest.json').read_text())
    old = [json.loads(line) for line in (source_dir/'turns.jsonl').read_text().splitlines() if line.strip()]
    split = manifest['split']
    scenarios = select(core.load_split(split, manifest['seed']), manifest.get('only'))
    current = {(item['id'], index): (item, turn) for item in scenarios for index, turn in enumerate(item['turns'])}
    recorded = {(record.get('run', 0), record['scenario'], record['turn']): record['request'] for record in old}
    changed_worlds = RESCORE_WORLD_CHANGED.get(manifest.get('protocol'), ())

    def same(run, name, index):
        turn = current.get((name, index), (None, None))[1]
        return turn is not None and (turn.get('say') or '('+turn.get('ui', '')+')') == recorded.get((run, name, index))
    rows, unmatched, flips = [], 0, Counter()
    for record in old:
        item, turn = current.get((record['scenario'], record['turn']), (None, None))
        # The turn and everything said before it in the scenario must be what the current case says, on the same fixture.
        if record['scenario'] in changed_worlds or not all(same(record.get('run', 0), record['scenario'], index) for index in range(record['turn']+1)):
            unmatched += 1
            continue
        observed = {key: record[key] for key in ('kind', 'decision', 'replies', 'executed', 'native_calls', 'pending', 'started_local')+OBSERVED_KEYS
                    if key in record}
        observed['apps'] = dict(CATALOG)
        try:
            observed['finished_local'] = (datetime.fromisoformat(record['started_local'])+timedelta(seconds=record.get('seconds', 0))).isoformat()
        except (KeyError, TypeError, ValueError):
            pass
        if 'full_text_replies' not in record and 'shown' not in record:
            observed['full_text_replies'] = [index for index, reply in enumerate(record['replies']) if '\n' in str(reply).strip()]
        if record.get('error_kind') == 'runtime_exception':  # v1 scored what the UI shows as a failure message as an error
            observed['service_exception'] = observed.pop('error')
            observed.pop('error_kind')
        calls = [dict(call, wall_ms=call.get('wall_ms', call.get('ms'))) for call in record.get('calls') or ()]
        for call in calls:
            call['unaccounted_ms'] = core.call_times(call)[2]
        row = turn_row(record.get('run', 0), item, record['turn'], turn, observed, record.get('seconds', 0), record.get('model_calls', 0), calls,
                       record.get('stages') or [])
        row['previous_status'] = record['status']
        flips[(record['status'], row['status'])] += 1
        rows.append(row)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    harness_digest, harness_files = harness_hashes()
    planned = len(rows)
    manifest.update(protocol=core.PROTOCOL, rescored_from=str(source_dir), rescored_utc=datetime.now(timezone.utc).isoformat(),
                    original_protocol=manifest.get('protocol'), original_harness_sha256=manifest.get('harness_sha256'),
                    harness_sha256=harness_digest, harness_files=harness_files, case_set_hash=core.case_set_hash(scenarios),
                    planned_turns=planned, unmatched_turns=unmatched)
    (output/'manifest.json').write_text(json.dumps(manifest, indent=2, ensure_ascii=False)+'\n')
    (output/'turns.jsonl').write_text(''.join(json.dumps(row, ensure_ascii=False, default=str)+'\n' for row in rows))
    by_family = Counter(row['family'] for row in rows)
    summary = core.summarize(rows, planned=planned, completed=True, planned_by_family=dict(by_family))
    summary.update(identity=core.identity(manifest), rescored_from=str(source_dir), unmatched_turns=unmatched,
                   verdict_changes={'%s->%s' % key: count for key, count in sorted(flips.items()) if key[0] != key[1]},
                   note_rescore='Offline re-score of recorded turns; not a new run. Turns whose case changed are left out.')
    (output/'summary.json').write_text(json.dumps(summary, indent=2, ensure_ascii=False)+'\n')
    out('re-scored %d of %d recorded turns (%d no longer match a current case)' % (len(rows), len(old), unmatched))
    out('verdict changes: '+(', '.join('%s %d' % item for item in summary['verdict_changes'].items()) or 'none'))
    report(summary, rows, split, out)
    if split == 'dev':
        for row in rows:
            if row['previous_status'] != row['status']:
                out('  %s -> %s  %s[%d] %s' % (row['previous_status'], row['status'], row['scenario'], row['turn'], '; '.join(row['reasons'])[:140]))
    return 0


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--split', choices=core.SPLITS, default='dev')
    parser.add_argument('--only', action='append', metavar='FAMILY', help='run only this family (repeatable)')
    parser.add_argument('--runs', type=int, default=1, help='repeats; decoding is greedy with a fixed seed, so repeats mostly measure latency')
    parser.add_argument('--seed', type=int, default=20261106)
    parser.add_argument('--model', default='qwen3:4b-instruct')
    parser.add_argument('--source-root', type=Path, default=ROOT, help='checkout whose companion/ is evaluated')
    parser.add_argument('--output', type=Path, help='new directory for manifest.json, turns.jsonl and summary.json (must not exist)')
    parser.add_argument('--compare', type=Path, metavar='BASELINE', help='a finished output directory or summary.json to compare against')
    parser.add_argument('--against', type=Path, metavar='CANDIDATE', help='with --compare: compare two finished results without running')
    parser.add_argument('--rescore', type=Path, metavar='RUN', help='score a finished run\'s recorded turns again, offline, into --output')
    parser.add_argument('--list', action='store_true', help='list the selected cases; no model, no runtime imports')
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.runs < 1:
        parser.error('--runs must be positive')
    if args.output and args.output.exists():
        parser.error('--output must not exist: '+str(args.output))
    if args.rescore:
        if not args.output:
            parser.error('--rescore needs --output NEW_DIRECTORY')
        return rescore(args.rescore, args.output)
    if args.against:
        if not args.compare or not args.output:
            parser.error('--against needs --compare BASELINE and --output NEW_DIRECTORY')
        try:
            split = (core.load_result(args.against)['identity'] or {}).get('split', 'acceptance')
        except (OSError, ValueError):
            split = 'acceptance'
        args.output.mkdir(parents=True)
        return write_comparison(args.compare, args.against, args.output/'comparison.json', split)
    scenarios = select(core.load_split(args.split, args.seed), args.only)
    if args.list:
        print(listing(scenarios, args.split))
        return 0
    if not args.output:
        parser.error('--output is required')
    if not scenarios:
        parser.error('no scenarios selected')
    return run(args, scenarios)


if __name__ == '__main__':
    raise SystemExit(main())
