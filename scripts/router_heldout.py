"""Score the router against the held-out request set (tests/data/router_heldout.jsonl).

The set is hand-labelled, never used for training or in a prompt, and covers the known
confusions (dictionary lookups vs moving the companion, showing a sidebar vs pointing at
it, "add another one" after a reminder vs an event, inspect vs change, a named app vs the
front app). Each row gives the request, earlier user requests, the router context
(`now`, `front_app`, observed controls) and the expected fields; only the fields listed
in `expect` are scored.

Loading and scoring need no model. A run calls the real `Planner.interpret_request`
through local Ollama (greedy, fixed seed) and never executes a desktop action:

    .venv/bin/python scripts/router_heldout.py --output .data/live_eval/router-heldout.json

`--predictions FILE` scores saved router outputs (JSONL of {"id", "plan"}) instead.
"""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from companion.request_plan import OPERATIONS, OPENING_FIELDS

DATA = ROOT / 'tests' / 'data' / 'router_heldout.jsonl'
EXPECT_FIELDS = ('operation', 'app_name', 'target', 'browser_mode', 'media_artist', 'goal_mode',
                 'when', 'referent', 'acts_on_computer')
ROW_FIELDS = {'id', 'category', 'request', 'recent_user_requests', 'context', 'expect'}
CONTEXT_FIELDS = {'now', 'front_app', 'observed_controls'}
# Vendor prefixes people drop when they name an app ("Teams" for Microsoft Teams).
VENDOR = re.compile(r'^(?:microsoft|google|apple|mozilla)\s+')


def load(path=DATA):
    """The rows, checked against the router's schema. Raises ValueError on a bad row."""
    rows = [json.loads(line) for line in Path(path).read_text(encoding='utf-8').splitlines() if line.strip()]
    ids = set()
    for row in rows:
        problem = check_row(row)
        if problem or row['id'] in ids:
            raise ValueError('%s: %s' % (row.get('id', '?'), problem or 'duplicate id'))
        ids.add(row['id'])
    return rows


def check_row(row):
    if not isinstance(row, dict) or set(row) != ROW_FIELDS:
        return 'fields must be exactly %s' % sorted(ROW_FIELDS)
    if not isinstance(row['request'], str) or not row['request'].strip():
        return 'empty request'
    if not isinstance(row['recent_user_requests'], list) or not all(isinstance(item, str) for item in row['recent_user_requests']):
        return 'recent_user_requests must be a list of strings'
    if not isinstance(row['context'], dict) or set(row['context']) - CONTEXT_FIELDS:
        return 'unknown context field'
    expect = row['expect']
    if not isinstance(expect, dict) or 'operation' not in expect or set(expect) - set(EXPECT_FIELDS):
        return 'expect needs operation and only router fields'
    operations = expect['operation'] if isinstance(expect['operation'], list) else [expect['operation']]
    if not operations or any(op not in OPERATIONS for op in operations):
        return 'unknown operation %r' % (expect['operation'],)
    if 'acts_on_computer' in expect and type(expect['acts_on_computer']) is not bool:
        return 'acts_on_computer must be true or false'
    if expect.get('goal_mode', 'inspect') not in ('inspect', 'draft', 'change'):
        return 'bad goal_mode'
    if expect.get('referent', 'other') not in ('companion', 'destination', 'visible_target', 'other'):
        return 'bad referent'
    if expect.get('browser_mode', 'default') not in ('window', 'tab', 'default'):
        return 'bad browser_mode'
    return None


def effective(plan):
    """What the router decided: an opening step the planner folded into a task
    ("open Calculator and add 2 and 2") is still that opening operation."""
    plan = dict(plan or {})
    opening = plan.get('opening')
    if isinstance(opening, dict):
        plan.update({key: opening[key] for key in OPENING_FIELDS if key in opening})
    return plan


def _text(value):
    return ' '.join(str(value).casefold().replace('’', "'").split()).strip(' .!?"\'')


def _app(value):
    return VENDOR.sub('', _text(value))


def _url(value):
    return re.sub(r'^(?:https?://)?(?:www\.)?', '', _text(value)).rstrip('/')


def field_ok(field, expected, got, operation):
    if field == 'operation':
        return got in (expected if isinstance(expected, list) else [expected])
    if field == 'acts_on_computer':
        return got is expected
    if got is None:
        got = '' if field not in ('referent', 'goal_mode', 'browser_mode') else {
            'referent': 'other', 'goal_mode': 'inspect', 'browser_mode': 'default'}[field]
    if field == 'app_name':
        return _app(got) == _app(expected)
    if field == 'target' and operation == 'browser_open':
        return _url(got) == _url(expected)
    if field == 'when':  # the router writes seconds; the same minute is the same time
        return _when(got) == _when(expected)
    return _text(got) == _text(expected)


def _when(value):
    return re.sub(r'(T\d\d:\d\d):00$', r'\1', _text(value).upper())


def score_row(row, plan):
    """{field: bool} for every expected field. A wrong operation fails its other fields too."""
    plan = effective(plan)
    expect = row['expect']
    result = {'operation': field_ok('operation', expect['operation'], plan.get('operation'), None)}
    for field in EXPECT_FIELDS[1:]:
        if field in expect:
            result[field] = result['operation'] and field_ok(field, expect[field], plan.get(field), plan.get('operation'))
    return result


def score(rows, predictions, seconds=None):
    """Summary of predictions ({row id: plan, or None for an error}) against the rows."""
    fields = defaultdict(lambda: [0, 0])
    categories = defaultdict(lambda: [0, 0])
    confusion = defaultdict(Counter)
    misses = []
    exact = 0
    for row in rows:
        plan = predictions.get(row['id'])
        checks = score_row(row, plan)
        for field, ok in checks.items():
            fields[field][0] += ok
            fields[field][1] += 1
        categories[row['category']][0] += all(checks.values())
        categories[row['category']][1] += 1
        exact += all(checks.values())
        expected = row['expect']['operation']
        gold = '|'.join(expected) if isinstance(expected, list) else expected
        actual = effective(plan).get('operation', '<error>') if plan is not None else '<error>'
        confusion[gold][actual] += 1
        if not all(checks.values()):
            misses.append({'id': row['id'], 'category': row['category'], 'request': row['request'],
                           'expect': row['expect'], 'failed': sorted(k for k, ok in checks.items() if not ok),
                           'got': {k: effective(plan).get(k) for k in row['expect']} if plan is not None else None})
    total = len(rows)
    report = {'rows': total, 'all_fields_correct': exact,
              'operation_correct': fields['operation'][0],
              'operation_accuracy': fields['operation'][0] / max(1, total),
              'fields': {k: {'correct': v[0], 'total': v[1]} for k, v in fields.items()},
              'categories': {k: {'correct': v[0], 'total': v[1]} for k, v in sorted(categories.items())},
              'confusion': {k: dict(v) for k, v in sorted(confusion.items())},
              'errors': sum(1 for row in rows if predictions.get(row['id']) is None),
              'misses': misses}
    if seconds:
        report['mean_seconds'] = sum(seconds) / len(seconds)
    return report


def run(rows, provider):
    """Route every row through provider.interpret_request; returns (predictions, seconds)."""
    predictions, seconds = {}, []
    for row in rows:
        start = time.perf_counter()
        try:
            predictions[row['id']] = provider.interpret_request(row['request'], row['recent_user_requests'], dict(row['context']))
        except Exception as error:  # a refused or invalid plan is a miss, not a crash
            predictions[row['id']] = None
            print('%s: %s' % (row['id'], error), file=sys.stderr)
        seconds.append(time.perf_counter() - start)
    return predictions, seconds


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--data', type=Path, default=DATA)
    parser.add_argument('--model', default='qwen3:4b-instruct', help='local Ollama model')
    parser.add_argument('--predictions', type=Path, help='score saved {"id", "plan"} JSONL instead of running a model')
    parser.add_argument('--category', action='append', help='only these categories')
    parser.add_argument('--limit', type=int)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    rows = load(args.data)
    if args.category:
        rows = [row for row in rows if row['category'] in args.category]
    if args.limit:
        rows = rows[:args.limit]
    if not rows:
        parser.error('No rows selected')
    seconds = None
    if args.predictions:
        predictions = {}
        for line in args.predictions.read_text(encoding='utf-8').splitlines():
            if line.strip():
                item = json.loads(line)
                predictions[item['id']] = item.get('plan')
    else:
        from companion.ollama import OllamaProvider
        from companion.provider import AIConfig
        predictions, seconds = run(rows, OllamaProvider(AIConfig(model=args.model, timeout=120)))
    report = score(rows, predictions, seconds)
    report['model'] = None if args.predictions else args.model
    text = json.dumps(report, indent=2, ensure_ascii=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + '\n', encoding='utf-8')
    print('%d/%d operations, %d/%d rows with every field right' % (
        report['operation_correct'], report['rows'], report['all_fields_correct'], report['rows']))
    if not args.output:
        print(text)
    return report


if __name__ == '__main__':
    main()
