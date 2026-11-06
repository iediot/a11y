#!/usr/bin/env python3
"""Real local-model conversation checks through Companion; never desktop capability evidence.

Native app availability is disabled. Each family starts with empty memory in a
private temporary store; within-family turns retain actual service memory. No
router held-out requests, personal data, screen capture or cloud providers are used.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import random
import re
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.request
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = 'conversation-outcomes-v1'


def scenarios(seed):
    code = 'amber-'+str(random.Random(seed).randint(100000, 999999))
    return [
        ('exact_reply', [('Reply with only the word ready.', 'exact', 'ready')]),
        ('identity', [("What is your name?", 'identity', '')]),
        ('capability', [('Can you open apps on my Mac?', 'capability', '')]),
        ('capability_variant', [('Are you able to operate applications, or do you only chat?', 'capability', '')]),
        ('memory', [(f'My expedition code is {code}.', 'contains', code),
                    ('What is my expedition code?', 'contains', code)]),
    ]


def score(result, kind, expected):
    if not isinstance(result, dict) or result.get('desktop_action') or result.get('target'):
        return False
    reply = result.get('message')
    if not isinstance(reply, str) or not reply.strip():
        return False
    text = reply.casefold()
    if re.search(r"\bi(?:['’]ll| will| am going to|'m going to)\b|\b(?:not sure|whether|perhaps|maybe|might|could be)\b|"
                 r"\b(?:don['’]t|do not|can['’]t|cannot)\s+(?:remember|recall)\b", text):
        return False  # A promise or uncertain mention is not the requested answer.
    if kind == 'exact':
        return text.strip(' .!\n') == expected.casefold()
    if kind == 'contains':
        token = re.escape(expected.casefold())
        return (bool(expected) and bool(re.search(r'(?<![\w-])'+token+r'(?![\w-])', text))
                and not re.search(r"\b(?:not|isn['’]t|wasn['’]t)\s+"+token, text))
    if kind == 'identity':
        if '?' in text or re.search(r'\b(?:not|no)\b', text):
            return False
        claims = re.findall(r'\bmy name is\s+(\w+)', text)
        return (all(name in ('a11y', 'ally') for name in claims)
                and bool(re.search(r"\b(?:my name is|i am|i['’]m|call me)\s+(?:a11y|ally)\b", text)
                         or text.strip(' .!') in ('a11y', 'ally')))
    if kind == 'capability':
        denies = re.search(r"\b(?:cannot|can['’]t|unable|only (?:chat|answer))\b", text)
        affirmative = re.search(r"\bi\s+(?:can|am able to)\b|\bi['’]m able to\b", text)
        capability = re.search(r'\b(?:open|operate|control|interact|launch|manage|perform)\b', text)
        return bool(affirmative and capability and not denies)
    raise ValueError('Unknown oracle kind: '+kind)


def summarize(rows, *, planned_turns, completed):
    counts = {status: sum(r['status'] == status for r in rows) for status in ('pass', 'fail', 'error')}
    return dict(total=len(rows), planned_turns=planned_turns, not_attempted=max(0, planned_turns-len(rows)),
                completed=completed, statuses=counts,
                passed=bool(rows) and completed and len(rows) == planned_turns and counts['pass'] == len(rows),
                latency_p50=statistics.median(r['seconds'] for r in rows) if rows else None,
                model_calls=sum(r['model_calls'] for r in rows),
                note='Screen-free service/model evidence only; small samples and cache conditions limit latency inference.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--source-root', type=Path, default=ROOT)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--runs', type=int, default=3)
    parser.add_argument('--seed', type=int, default=71053)
    parser.add_argument('--model', default='qwen3:4b-instruct')
    args = parser.parse_args(argv)
    if args.runs < 1:
        parser.error('--runs must be positive')
    args.output.mkdir(parents=True, exist_ok=False)
    source = args.source_root.resolve()
    sys.path.insert(0, str(source))
    from companion.ollama import OllamaProvider
    from companion.provider import AIConfig
    from companion.service import Companion
    from companion.storage import Store
    from companion.models import Identity
    from companion.desktop_context import DesktopContext
    from companion.instance import build_id
    from companion import timing

    class LocalModel(OllamaProvider):
        def __init__(self):
            super().__init__(AIConfig(model=args.model, timeout=90))
            self.calls = 0

        def complete(self, *values, **options):
            self.calls += 1
            return super().complete(*values, **options)

    class NoNativeApps:
        def catalog(self):
            return []

    digest = None
    try:
        with urllib.request.urlopen('http://127.0.0.1:11434/api/tags', timeout=5) as response:
            models = json.load(response)['models']
        digest = next((m['digest'] for m in models if m['name'] == args.model), None)
    except (OSError, ValueError, KeyError):
        pass
    git = subprocess.run(['git', '-C', str(source), 'rev-parse', 'HEAD'], capture_output=True, text=True)
    planned_turns = args.runs*sum(len(turns) for _, turns in scenarios(args.seed))
    manifest = dict(protocol=PROTOCOL, source=str(source), source_build=build_id(), git_head=git.stdout.strip() or None,
                    harness_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                    model=args.model, model_digest=digest, runs=args.runs, seed=args.seed,
                    started_utc=datetime.now(timezone.utc).isoformat(), command=sys.argv,
                    condition='First inference may load/reprocess a prompt; model cache is not cleared.',
                    isolation='Temporary Store; mail/mac app availability disabled; no native task executor.')
    manifest['planned_turns'] = planned_turns
    manifest['inference_environment'] = {key: os.environ.get(key) for key in (
        'A11Y_OLLAMA_KEEP_ALIVE', 'A11Y_OLLAMA_CONTEXT', 'A11Y_OLLAMA_THINKING')}
    manifest['request_settings'] = dict(endpoint='http://127.0.0.1:11434', timeout_seconds=90,
                                       thinking_requested=False, thinking_override=os.getenv('A11Y_OLLAMA_THINKING', 'default'),
                                       temperature=0, generation_seed=7,
                                       context=os.getenv('A11Y_OLLAMA_CONTEXT', '16384'),
                                       keep_alive=os.getenv('A11Y_OLLAMA_KEEP_ALIVE', '30m'),
                                       options_source='Pinned source_build companion/ollama.py')
    manifest['source_files'] = {str(path.relative_to(source)): hashlib.sha256(path.read_bytes()).hexdigest()
                                for pattern in ('companion/*.py', 'native/*.swift') for path in sorted(source.glob(pattern))}
    if (source/'baseline.json').is_file():
        manifest['snapshot_provenance'] = json.loads((source/'baseline.json').read_text())
    (args.output/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    rows, timings, completed = [], [], False
    timing.listeners.append(timings.append)
    try:
        with tempfile.TemporaryDirectory(prefix='a11y-conversation-') as folder, \
                patch('companion.mail_reader.available', return_value=False), \
                patch('companion.mac_apps.available', return_value=False), \
                patch.dict(os.environ, {'A11Y_WEB_ANSWERS': '0', 'A11Y_ROUTES': '0'}), \
                (args.output/'turns.jsonl').open('x') as out:
            model = LocalModel()
            for repeat in range(args.runs):
                for family, turns in scenarios(args.seed):
                    app = Companion(Store(Path(folder)/f'{repeat}-{family}', persist_history=False),
                                    Identity('fixture', 'user'), model)
                    app.desktop_context = DesktopContext(NoNativeApps())
                    for index, (request, kind, expected) in enumerate(turns):
                        start, calls, offset = time.monotonic(), model.calls, len(timings)
                        row = dict(family=family, repeat=repeat, turn=index, request=request,
                                   expected=dict(kind=kind, value=expected))
                        try:
                            result = app.ask(request)
                            row.update(status='pass' if score(result, kind, expected) else 'fail', actual=result)
                        except Exception as error:
                            row.update(status='error', error=type(error).__name__+': '+str(error)[:300])
                        row.update(seconds=round(time.monotonic()-start, 4), model_calls=model.calls-calls,
                                   timings=timings[offset:])
                        rows.append(row)
                        out.write(json.dumps(row)+'\n')
                        out.flush()
                        print(f"{family}/{index} repeat={repeat} {row['status']} {row['seconds']}s calls={row['model_calls']}", flush=True)
            completed = True
    finally:
        timing.listeners.remove(timings.append)
        summary = summarize(rows, planned_turns=planned_turns, completed=completed)
        summary.update(finished_utc=datetime.now(timezone.utc).isoformat(), protocol=PROTOCOL,
                       source_changed=build_id() != manifest['source_build'],
                       harness_changed=hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != manifest['harness_sha256'])
        (args.output/'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    print(json.dumps(summary), flush=True)
    return 0 if summary['passed'] and not summary['source_changed'] and not summary['harness_changed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
