#!/usr/bin/env python3
"""Owned Safari/Finder workflows through the real source app, with independent oracles.

--list never imports Qt, starts a server, reads consent, or touches applications.
Native preflight fails closed before fixture setup. No legacy restore routines.
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
import platform
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
CASE_IDS = ('browser_read', 'browser_link', 'browser_catalog', 'browser_history',
            'finder_navigation', 'finder_selection', 'finder_read')
FIXTURE_VERSION = 'owned-safari-finder-v1'


class OwnerTakeover(RuntimeError):
    """Stop the entire run when the desktop leaves the owned fixture."""


def prerequisites():
    if sys.platform != 'darwin':
        return dict(ready=False, reason='These native workflows currently require macOS.')
    import ctypes
    ax = ctypes.CDLL('/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices')
    ax.AXIsProcessTrusted.restype = ctypes.c_bool
    cg = ctypes.CDLL('/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics')
    cg.CGPreflightScreenCaptureAccess.restype = ctypes.c_bool
    checks = dict(accessibility=ax.AXIsProcessTrusted(), screen_recording=cg.CGPreflightScreenCaptureAccess())
    return dict(ready=all(checks.values()), checks=checks,
                reason='' if all(checks.values()) else 'Required OS permission is unavailable; no apps were opened or permissions changed.')


def osa(script, *arguments):
    result = subprocess.run(['osascript', '-e', 'on run argv\n'+script+'\nend run', *map(str, arguments)],
                            capture_output=True, text=True, timeout=15)
    if result.returncode:
        raise RuntimeError('Native fixture/oracle probe failed: '+result.stderr.strip()[:200])
    return result.stdout.strip()


def copy_existing_consent(source, destination):
    """Copy only chosen local models and existing local access choices, never user content."""
    config = {}
    try:
        saved = json.loads((source/'ai.json').read_text())
        config = {k: saved[k] for k in ('model', 'vision_model') if isinstance(saved.get(k), str)}
    except (OSError, ValueError, TypeError):
        pass
    config.update(provider='ollama', web_lookups='off')
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    (destination/'ai.json').write_text(json.dumps(config))
    owner = json.dumps([os.getenv('A11Y_COMPANY_ID', 'demo-company'), os.getenv('A11Y_USER_ID', 'demo-employee')])
    database = source/'employee.sqlite3'
    if not database.is_file():
        return False, config
    try:
        with closing(sqlite3.connect(database.resolve().as_uri()+'?mode=ro', uri=True)) as db:
            row = db.execute('SELECT data FROM desktop_access WHERE owner=?', (owner,)).fetchone()
        saved = json.loads(row[0]) if row else {}
    except (sqlite3.Error, ValueError, TypeError):
        return False, config
    choices = {key: saved.get(key) is True for key in ('remember', 'app_awareness', 'screen_vision')}
    choices['approved'] = {key: (saved.get('approved') or {}).get(key) is True for key in ('app_awareness', 'screen_vision')}
    if not all(choices[key] for key in ('remember', 'app_awareness', 'screen_vision')):
        return False, config
    with closing(sqlite3.connect(destination/'employee.sqlite3')) as db, db:
        db.execute('CREATE TABLE desktop_access (owner TEXT PRIMARY KEY, data TEXT NOT NULL)')
        db.execute('INSERT INTO desktop_access VALUES (?,?)', (owner, json.dumps(choices)))
    return True, config


class AppClient:
    def __init__(self, data, log):
        self.temporary = tempfile.TemporaryDirectory(prefix='a11y-human-')
        self.socket = Path(self.temporary.name)/'eval.sock'
        self.data, self.log, self.process = data, log, None

    def call(self, command):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(25)
            connection.connect(str(self.socket))
            connection.sendall(json.dumps(command).encode()+b'\n')
            response = b''
            while not response.endswith(b'\n'):
                chunk = connection.recv(65536)
                if not chunk:
                    break
                response += chunk
                if len(response) > 8_000_000:
                    raise RuntimeError('Evaluation reply exceeded its bound.')
        value = json.loads(response)
        if 'error' in value:
            raise RuntimeError(value['error'])
        return value

    def launch(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith(('ANTHROPIC_', 'OPENAI_'))}
        env.update(A11Y_DATA_DIR=str(self.data), A11Y_EVAL='1', A11Y_EVAL_SOCKET=str(self.socket),
                   A11Y_AI_PROVIDER='ollama', A11Y_OLLAMA_URL='http://127.0.0.1:11434',
                   A11Y_WEB_ANSWERS='0', A11Y_ROUTES='0', PYTHONUNBUFFERED='1')
        self.stream = self.log.open('a')
        self.process = subprocess.Popen([sys.executable, str(ROOT/'a11y_desktop.py')], cwd=ROOT,
                                        env=env, stdout=self.stream, stderr=subprocess.STDOUT)
        deadline = time.monotonic()+90
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError('Source application exited during startup; see app log.')
            try:
                state = self.call({'cmd': 'state', 'since': 10**9})
                if all(state.get('ready', {}).values()) and state.get('ready') and state.get('idle'):
                    return
            except (OSError, ValueError, RuntimeError):
                pass
            time.sleep(.25)
        raise RuntimeError('Source app did not become ready with existing consent.')

    def ask(self, request, timeout, owns_desktop=None):
        start = time.monotonic()
        offset = self.call({'cmd': 'state', 'since': 10**9})['event_count']
        self.call({'cmd': 'ask', 'request': request})
        events, state, last_check = [], {}, 0
        while time.monotonic()-start < timeout:
            state = self.call({'cmd': 'state', 'since': offset+len(events)})
            events.extend(state.get('events', []))
            if owns_desktop is not None and time.monotonic()-last_check >= 1:
                if not owns_desktop():
                    self.call({'cmd': 'stop'})
                    raise OwnerTakeover('Desktop left the owned fixture; remaining workflows were not started.')
                last_check = time.monotonic()
            if state.get('idle') and state.get('quiet_for', 0) >= .8:
                return state, events, False
            time.sleep(.15)
        self.call({'cmd': 'stop'})
        return state, events, True

    def close(self):
        if self.process and self.process.poll() is None:
            try:
                self.call({'cmd': 'stop'})
                self.call({'cmd': 'quit'})
                self.process.wait(timeout=10)
            except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired):
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=5)
        if hasattr(self, 'stream'):
            self.stream.close()
        self.temporary.cleanup()


class NativeFixture:
    def __init__(self, root, base_url):
        self.root, self.base_url, self.window, self.app = root.resolve(), base_url, None, None
        self.companion_pid = None

    def owns_foreground(self):
        value = osa('tell application "System Events"\nset p to first application process whose frontmost is true\n'
                    'return (unix id of p as text) & linefeed & name of p\nend tell').splitlines()
        if len(value) != 2:
            return False
        if self.companion_pid is not None and value[0] == str(self.companion_pid):
            return True
        if value[1] == self.app:
            return True
        if self.app == 'Finder' and value[1] == 'TextEdit':
            # Reading an owned text fixture may open it. Never inspect document text.
            path = osa('tell application "TextEdit" to return POSIX path of (file of front document as alias)')
            resolved = Path(path).resolve()
            return resolved in (self.root/'packing.txt', self.root/'pickup.txt')
        return False

    def setup(self, browser):
        self.app = 'Safari' if browser else 'Finder'
        if browser:
            self.window = int(osa('tell application "Safari"\nmake new document with properties {URL:item 1 of argv}\n'
                                  'activate\nreturn id of front window\nend tell', self.base_url+'/home'))
        else:
            self.window = int(osa('tell application "Finder"\nset w to make new Finder window to (POSIX file (item 1 of argv) as alias)\n'
                                  'activate\nreturn id of w\nend tell', self.root))
        deadline = time.monotonic()+15
        while time.monotonic() < deadline:
            observed = self.observe()
            if observed.get('owned') and (observed.get('url') == self.base_url+'/home' if browser else Path(observed.get('folder', '')).resolve() == self.root.resolve()):
                return
            time.sleep(.1)
        raise RuntimeError('Owned fixture window did not become ready.')

    def observe(self):
        if not self.owns_foreground():
            return {'owned': False}
        if self.app == 'Safari':
            value = osa('tell application "Safari"\nif (id of front window as text) is not (item 1 of argv) then return "unowned"\n'
                        'return URL of current tab of window id (item 1 of argv as integer)\nend tell', self.window)
            return dict(owned=value.startswith(self.base_url+'/'), url=value if value.startswith(self.base_url+'/') else None)
        value = osa('tell application "Finder"\nif (id of front Finder window as text) is not (item 1 of argv) then return "unowned"\n'
                    'set out to POSIX path of (target of front Finder window as alias)\n'
                    'repeat with f in (selection as alias list)\nset out to out & linefeed & POSIX path of f\nend repeat\nreturn out\nend tell', self.window)
        lines = value.splitlines()
        folder = Path(lines[0]).resolve() if lines and lines[0] != 'unowned' else None
        owned = folder is not None and (folder == self.root.resolve() or self.root.resolve() in folder.parents)
        return dict(owned=owned, folder=str(folder) if owned else None,
                    selection=[str(Path(v).resolve()) for v in lines[1:]] if owned else [])

    def close(self):
        if self.window is None:
            return
        if self.app == 'Safari':
            result = osa('tell application "Safari"\nset w to window id (item 1 of argv as integer)\n'
                         'repeat with t in tabs of w\nif URL of t does not start with (item 2 of argv) then return "preserved"\nend repeat\n'
                         'close w\nreturn "closed"\nend tell', self.window, self.base_url+'/')
        else:
            result = osa('tell application "Finder"\nset w to Finder window id (item 1 of argv as integer)\n'
                         'set p to POSIX path of (target of w as alias)\n'
                         'if p does not start with ((item 2 of argv) & "/") and p is not ((item 2 of argv) & "/") then return "preserved"\n'
                         'close w\nreturn "closed"\nend tell', self.window, self.root)
        if result != 'closed':
            raise RuntimeError('Owned window contains nonfixture content; it was preserved.')


def workflow(case, server, root):
    base, facts = server.base_url, server.facts
    nav = lambda path, http=True: dict(kind='navigate', url=base+path, require_http=http)
    read = lambda value: dict(kind='read', fact=str(value))
    cases = {
        'browser_read': [('What does this page say about delivery?', [read(facts['delivery'])]),
                         ('And what is the pickup information?', [read(facts['pickup'])])],
        'browser_link': [('Open the Delivery details link on this page.', [nav('/delivery')]),
                         ('What delivery information is shown here?', [read(facts['delivery'])])],
        'browser_catalog': [('Open Catalog from this page.', [nav('/catalog')]),
                            (f"Search this catalog for {facts['product']} and tell me its price.",
                             [nav('/catalog?'+urlencode({'q': facts['product']})), read(facts['price'])])],
        'browser_history': [('Go to Delivery details from this page.', [nav('/delivery')]),
                            ('Go back to the previous page.', [nav('/home', False)])],
        'finder_navigation': [('Open Trips in this folder.', [dict(kind='folder', path=str(root/'Trips'))]),
                              ('Now open Booking inside it.', [dict(kind='folder', path=str(root/'Trips'/'Booking'))])],
        'finder_selection': [('Select packing.txt in this folder.', [dict(kind='select', path=str(root/'packing.txt'))]),
                             ('Select pickup.txt instead.', [dict(kind='select', path=str(root/'pickup.txt'))])],
        'finder_read': [('Read packing.txt from this folder.', [read(facts['delivery'])]),
                        ('Now read pickup.txt from the same folder.', [read(facts['pickup'])])],
    }
    return cases[case]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--list', action='store_true')
    parser.add_argument('--runs', type=int, default=3)
    parser.add_argument('--seed', type=int, default=71053)
    parser.add_argument('--only', action='append', choices=CASE_IDS)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--compare', type=Path)
    parser.add_argument('--consent-from', type=Path, help='Copy ONLY existing local consent/model choices from this explicitly selected app data directory.')
    parser.add_argument('--timeout', type=float, default=120)
    args = parser.parse_args(argv)
    if args.list:
        print('\n'.join(CASE_IDS))
        return 0
    if args.runs < 1 or args.timeout <= 0:
        parser.error('--runs and --timeout must be positive')
    from human_eval_core import PROTOCOL, FixtureServer, assess_turn, summarize, compare_runs
    cases = args.only or list(CASE_IDS)
    out = args.output or ROOT/'.data'/'human_eval'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    out.mkdir(parents=True, exist_ok=False)
    preflight = prerequisites()
    source_files = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                    for pattern in ('companion/*.py', 'native/*.swift', 'scripts/human_eval*.py') for p in sorted(ROOT.glob(pattern))}
    manifest = dict(protocol=PROTOCOL, fixture_version=FIXTURE_VERSION, seed=args.seed, runs=args.runs,
                    case_ids=cases, started_utc=datetime.now(timezone.utc).isoformat(), command=sys.argv,
                    prerequisites=preflight, source_files=source_files, python=sys.version,
                    platform=platform.platform(), machine=platform.machine(),
                    model_config={}, condition='Fresh app per workflow; Ollama cache not cleared; no forced cold-model claim.',
                    deadline_seconds=args.timeout, supervision='Ownership checked before each request and about once a second while running.')
    rows, cleanup_errors, complete = [], [], False
    planned = args.runs*len(cases)*2
    try:
        if not preflight['ready'] or args.consent_from is None:
            reason = preflight['reason'] if not preflight['ready'] else 'Choose existing consent explicitly with --consent-from; no consent was granted.'
            for repeat in range(args.runs):
                for case in cases:
                    for turn in range(2):
                        rows.append(dict(workflow=case, repeat=repeat, turn=turn, status='blocked', reason=reason,
                                         seconds=0, false_success=False, model_calls=0))
            complete = True
        else:
            with tempfile.TemporaryDirectory(prefix='a11y-human-owned-') as temporary, FixtureServer(args.seed) as server:
                private = Path(temporary)
                configured, models = copy_existing_consent(args.consent_from, private/'data')
                models.update(model=os.getenv('A11Y_AI_MODEL', models.get('model', '')),
                              vision_model=os.getenv('A11Y_VISION_MODEL', models.get('vision_model', '')),
                              context=os.getenv('A11Y_OLLAMA_CONTEXT', '16384'),
                              thinking=os.getenv('A11Y_OLLAMA_THINKING', 'default'),
                              keep_alive=os.getenv('A11Y_OLLAMA_KEEP_ALIVE', '30m'), generation_seed=7, temperature=0,
                              gui_profile=os.getenv('A11Y_GUI_PROFILE', 'qwen3-vl'), gui_model=os.getenv('A11Y_GUI_MODEL', ''))
                try:
                    with urllib.request.urlopen('http://127.0.0.1:11434/api/tags', timeout=5) as response:
                        installed = json.load(response)['models']
                    models['digests'] = {m['name']: m['digest'] for m in installed if m['name'] in (models['model'], models['vision_model'], models['gui_model'])}
                except (OSError, ValueError, KeyError):
                    models['digests'] = None
                manifest.update(model_config=models, fixture_nonce=server.base_url.rsplit('/', 1)[-1])
                if not configured:
                    raise PermissionError('Existing remembered app-awareness/screen consent is missing; no new consent was granted.')
                for repeat in range(args.runs):
                    for case in cases:
                        root = private/f'A11y test {args.seed}{repeat}{CASE_IDS.index(case)}'
                        root.mkdir(mode=0o700)
                        (root/'Trips'/'Booking').mkdir(parents=True)
                        (root/'Trips archive').mkdir()
                        (root/'packing.txt').write_text(str(server.facts['delivery']))
                        (root/'packing-old.txt').write_text('Outdated fixture: do not use this answer.')
                        (root/'pickup.txt').write_text(str(server.facts['pickup']))
                        native, client = NativeFixture(root, server.base_url), AppClient(private/'data', out/'app.log')
                        phase = 'setup'
                        try:
                            client.launch()
                            native.companion_pid = client.process.pid
                            native.setup(case.startswith('browser_'))
                            for index, (request, expectations) in enumerate(workflow(case, server, root)):
                                started = time.monotonic()
                                before = native.observe()
                                if not before.get('owned'):
                                    raise OwnerTakeover('Fixture ownership was lost before submitting the request.')
                                offset = server.event_count()
                                phase = 'stimulus'
                                state, events, timed_out = client.ask(request, args.timeout, lambda: native.observe().get('owned', False))
                                phase = 'oracle'
                                actual = native.observe()
                                replies = [e['data']['message'] for e in events if e.get('kind') == 'say' and isinstance(e.get('data', {}).get('message'), str)]
                                checks = [assess_turn(state=state, replies=replies, expected=expectation, actual=actual,
                                                      before=before, events=server.events_since(offset), timed_out=timed_out) for expectation in expectations]
                                status = next((c['status'] for c in checks if c['status'] != 'pass'), 'pass')
                                if not actual.get('owned'):
                                    raise OwnerTakeover('Fixture ownership was lost; the run stopped.')
                                row = dict(workflow=case, repeat=repeat, turn=index, request=request, status=status,
                                           seconds=round(time.monotonic()-started, 4), false_success=any(c.get('false_success') for c in checks),
                                           evidence=[c.get('evidence') for c in checks], reasons=[c.get('reason') for c in checks], replies=replies,
                                           model_calls=sum(e.get('kind') == 'timing' and e.get('data', {}).get('event') == 'ollama.finished' for e in events))
                                rows.append(row)
                                with (out/'turns.jsonl').open('a') as stream:
                                    stream.write(json.dumps(row)+'\n')
                                print(f'{case}/{index}: {status} {row["seconds"]}s', flush=True)
                                if status in ('timeout', 'cancelled', 'blocked'):
                                    break
                        except KeyboardInterrupt:
                            rows.append(dict(workflow=case, repeat=repeat, turn=len([r for r in rows if r['workflow'] == case and r['repeat'] == repeat]),
                                             status='cancelled', reason='Interrupted by owner.', seconds=0, false_success=False))
                            raise
                        except OwnerTakeover as error:
                            attempted = len([r for r in rows if r['workflow'] == case and r['repeat'] == repeat])
                            rows.append(dict(workflow=case, repeat=repeat, turn=attempted, status='cancelled',
                                             reason=str(error), seconds=0, false_success=False))
                            raise
                        except Exception as error:
                            attempted = len([r for r in rows if r['workflow'] == case and r['repeat'] == repeat])
                            rows.append(dict(workflow=case, repeat=repeat, turn=attempted,
                                             status='fail' if phase == 'stimulus' else 'setup_error',
                                             reason=phase+': '+type(error).__name__+': '+str(error)[:250],
                                             seconds=round(time.monotonic()-started, 4) if phase == 'stimulus' else 0,
                                             false_success=False))
                        finally:
                            try:
                                client.close()
                            except Exception as error:
                                cleanup_errors.append(dict(workflow=case, error='App cleanup: '+str(error)[:180]))
                            try:
                                native.close()
                            except Exception as error:
                                cleanup_errors.append(dict(workflow=case, error=str(error)[:200]))
                complete = True
    except PermissionError as error:
        rows = [dict(workflow=case, repeat=repeat, turn=turn, status='blocked', reason=str(error), seconds=0, false_success=False)
                for repeat in range(args.runs) for case in cases for turn in range(2)]
        complete = True
    except (OwnerTakeover, KeyboardInterrupt):
        complete = False
    finally:
        result = summarize(rows, planned, complete)
        result.update(manifest=manifest, cleanup_errors=cleanup_errors, finished_utc=datetime.now(timezone.utc).isoformat())
        result['source_changed'] = any(not (ROOT/name).is_file() or hashlib.sha256((ROOT/name).read_bytes()).hexdigest() != digest for name, digest in source_files.items())
        (out/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
        (out/'summary.json').write_text(json.dumps(result, indent=2)+'\n')
        # Complete raw records also include blocked/setup/cancelled attempts.
        (out/'all-turns.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in rows))
    if args.compare:
        comparison = compare_runs(json.loads(args.compare.read_text()), result)
        (out/'comparison.json').write_text(json.dumps(comparison, indent=2)+'\n')
    print(json.dumps({key: value for key, value in result.items() if key != 'manifest'}), flush=True)
    return 0 if result.get('all_workflows_passed') and not cleanup_errors and not result['source_changed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
