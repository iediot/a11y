"""Measure router wire formats: output tokens, time and decisions on scripts/router_wire_cases.py.

The router (Planner.interpret_request) spends most of its time writing JSON keys and default
values. This probe routes the development requests through the real planner of a source tree,
through local Ollama only (greedy, fixed seed), records every /api/chat call it makes (router,
goal-coverage check, motion check) and never executes a desktop action.

    run      route every case and save one JSON file
             --variant production   the tree's own wire format (use --tree for another copy)
             --variant NAME         an experimental format from VARIANTS, patched into the tree's
                                    planner for this process only
    compare  REFERENCE.json VARIANT.json: tokens, time, per-field agreement, every disagreement
    tokens   FILE.json...: token and time summary only

Typical session (time the reference and the variant back to back; tokens are the comparison
that does not depend on what else the GPU is doing):

    nice -n 19 taskpolicy -b .venv/bin/python scripts/router_wire_probe.py run --tree ../pristine --out ref.json
    nice -n 19 taskpolicy -b .venv/bin/python scripts/router_wire_probe.py run --out new.json
    .venv/bin/python scripts/router_wire_probe.py compare ref.json new.json
"""
import argparse
import importlib
import json
from pathlib import Path
import statistics
import sys
import time

HERE = Path(__file__).resolve().parent
FIELDS = ('operation', 'app_name', 'target', 'browser_mode', 'remaining_request', 'goal_mode', 'when',
          'acts_on_computer', 'referent', 'reply', 'media_artist')
DEFAULTS = {'app_name': '', 'target': '', 'browser_mode': 'default', 'remaining_request': '', 'goal_mode': 'inspect',
            'reply': '', 'when': '', 'referent': 'other'}
OMITTED = (('target', 'remaining_request', 'reply', 'when', 'media_artist'), ('browser_mode', 'default'),
           ('goal_mode', 'inspect'), ('referent', 'other'))


def variant(keys, required, legend, without=()):
    """keys: {field: wire key}; required: fields the grammar forces; legend: text appended to the rules;
    without: fields the router may not write at all."""
    return {'keys': keys, 'required': required, 'legend': legend, 'without': tuple(without)}


LONG = {field: field for field in FIELDS}
# Sorted, these short keys keep the fields in the order the full names sort in today.
SHORT = {'acts_on_computer': 'act', 'app_name': 'app', 'browser_mode': 'browser', 'goal_mode': 'goal', 'operation': 'op',
         'referent': 'ref', 'remaining_request': 'remaining', 'reply': 'reply', 'target': 'target', 'when': 'when',
         'media_artist': 'artist'}
# The operation first: "action" sorts before every other key.
OP_FIRST = dict(SHORT, operation='action', acts_on_computer='computer')


def listed(words):
    words = list(words)
    return words[0] if len(words) == 1 else ', '.join(words[:-1]) + ' and ' + words[-1]


def legend(keys, always, omit=True):
    """The paragraph appended to the routing rules: the short keys, and which fields may be left out."""
    text = '\nOutput format. '
    renamed = [field for field in ('acts_on_computer', 'app_name', 'operation', 'referent', 'remaining_request',
                                   'browser_mode', 'goal_mode', 'media_artist') if keys[field] != field]
    if renamed:
        text += 'Write the JSON with these short keys: %s; target, reply and when keep their names. ' % ', '.join(
            '%s = %s' % (keys[field], field) for field in renamed)
    if omit:
        empty = [keys[field] for field in OMITTED[0] if field not in always]
        rest = ['%s when %s' % (keys[field], default) for field, default in OMITTED[1:] if field not in always]
        text += ('Always write %s. Leave out every other key whose value would be its default: %s when empty; %s. '
                 'A key left out means that default; every rule above still decides its value.'
                 % (listed(keys[field] for field in always), ', '.join(empty[:-1]) + ' or ' + empty[-1], '; '.join(rest)))
    return text


NO_REPLY = (' Never write reply, for any operation: for operation=answer A11y writes the conversational '
            'answer in a separate step, so only classify the request.')


def spec(keys, required, omit=True, always=None, without=()):
    text = legend(keys, always or required, omit)
    if 'reply' in without:
        text = text.replace('; target, reply and when keep', '; target and when keep').replace('remaining, reply, when', 'remaining, when') \
                   .replace('remaining_request, reply, when', 'remaining_request, when') + NO_REPLY
    return variant(keys, required, text, without)


CORE = ('acts_on_computer', 'app_name', 'operation')
# preserve_desktop_goal empties remaining_request for these; "when" is read for TIMED only.
NO_FOLLOW_UP = ('answer', 'inspect_screen', 'inspect_media', 'computer_task', 'highlight', 'move_object', 'move_geometry',
                'capture_once')
TIMED = ('add_event', 'add_reminder', 'weather')
BROWSER = ('browser_open', 'browser_search')


def branched(keys, always, browser=False):
    """One object shape per group of operations: (operations, required fields, optional fields)."""
    def shape(operations, *fields, optional=()):
        return (operations, always + (('browser_mode',) if browser and operations == BROWSER else ()) + fields, optional)
    rest = lambda taken: None  # filled in by wire_schema: every operation not named by another branch
    branches = [shape(('answer',), 'reply', 'target'),
                shape(tuple(op for op in NO_FOLLOW_UP if op != 'answer'), 'target'),
                shape(TIMED, 'remaining_request', 'target', 'when'),
                shape(('play_media',), 'remaining_request', 'target', optional=('media_artist',))]
    if browser:
        branches.append(shape(BROWSER, 'remaining_request', 'target'))
    branches.append(shape(None, 'remaining_request', 'target'))
    return {'keys': keys, 'required': always, 'legend': '', 'without': (), 'branches': branches}
PREFIX = ('acts_on_computer', 'app_name', 'browser_mode', 'goal_mode', 'operation', 'referent')
VARIANTS = {
    # (A) the present names; fields with a default may be left out.
    'long_opt3': spec(LONG, CORE),
    'long_opt5': spec(LONG, CORE + ('referent', 'target')),
    # (B) short keys in today's order; all written, then with defaults left out.
    'short_all': spec(SHORT, tuple(f for f in FIELDS if f != 'media_artist'), omit=False),
    'short_opt3': spec(SHORT, CORE),
    'short_opt5': spec(SHORT, CORE + ('referent', 'target')),
    # Today's exact field order with defaults left out: only the first key can be forced by the grammar.
    'short_opt1': spec(SHORT, ('acts_on_computer',), always=CORE),
    # (A) nothing before referent changes: today's first six keys stay required, in today's order, so only
    # the trailing text fields can be left out.
    'long_tail': variant(LONG, PREFIX, '\nOutput format. Leave remaining_request, reply, target, when and media_artist '
                                       'out of the JSON when they would be empty; a field left out is empty.'),
    # The same grammar with the prompt untouched: does the model leave anything out unasked?
    'long_tail_plain': variant(LONG, PREFIX, ''),
    # (A) as long_tail, with browser_mode and goal_mode moved behind the operation so they can be left out too.
    'long_opt4': variant(LONG, CORE + ('referent',),
                         '\nOutput format. Always write acts_on_computer, app_name, operation and referent. Leave the other '
                         'fields out of the JSON when they would hold their default: browser_mode default, goal_mode inspect, '
                         'and empty remaining_request, reply, target, when or media_artist. A field left out has that default.'),
    # (C) nothing is optional: the grammar writes exactly the fields each operation uses. The prompt is
    # untouched and today's first six keys keep their order, so those decisions cannot change.
    'long_branch': branched(LONG, PREFIX),
    # (C) the same, with capitals moving acts_on_computer, app_name and operation ahead of browser_mode and
    # goal_mode (keys are sorted by byte), so browser_mode is written for browser operations only.
    'cap_branch': branched(dict(LONG, acts_on_computer='Acts_on_computer', app_name='App_name', operation='Operation'),
                           CORE + ('goal_mode', 'referent'), browser=True),
    # (C) the operation decided first.
    'opfirst_opt3': spec(OP_FIRST, ('operation', 'acts_on_computer', 'app_name')),
    # (C) the router only classifies conversation; Companion.converse writes the reply.
    'short_opt3_noreply': spec(SHORT, CORE, without=('reply',)),
    'short_opt5_noreply': spec(SHORT, CORE + ('referent', 'target'), without=('reply',)),
    'long_all_noreply': variant(LONG, tuple(f for f in FIELDS if f not in ('media_artist', 'reply')), '\nOutput format.' + NO_REPLY, ('reply',)),
}


def wire_schema(base, item):
    if item.get('branches'):
        named = {op for operations, _, _ in item['branches'] if operations for op in operations}
        shapes = []
        for operations, required, optional in item['branches']:
            operations = operations or tuple(op for op in base['properties']['operation']['enum'] if op not in named)
            properties = {item['keys'][field]: base['properties'][field] for field in required + optional}
            properties[item['keys']['operation']] = {'enum': list(operations)}
            shapes.append({'type': 'object', 'additionalProperties': False, 'properties': properties,
                           'required': [item['keys'][field] for field in required]})
        return {'anyOf': shapes}
    properties = {item['keys'][field]: base['properties'][field] for field in FIELDS if field not in item['without']}
    return {'type': 'object', 'additionalProperties': False, 'properties': properties,
            'required': [item['keys'][field] for field in item['required']]}


def expand(item, value):
    """The full plan for one wire object; unknown or missing required keys are left for validate_plan."""
    if not isinstance(value, dict):
        return value
    back = {wire: field for field, wire in item['keys'].items()}
    plan = {back.get(key, key): content for key, content in value.items()}
    for field, default in DEFAULTS.items():
        plan.setdefault(field, default)
    return plan


def load_tree(tree):
    """Import companion.* from another source tree (the pristine reference) in this process."""
    sys.path.insert(0, str(tree))
    modules = {name: importlib.import_module('companion.' + name) for name in ('ollama', 'provider', 'request_plan')}
    origin = Path(modules['ollama'].__file__).resolve()
    if tree.resolve() not in origin.parents:
        raise SystemExit('companion was imported from %s, not %s' % (origin, tree))
    return modules


def code_routed(request, tree_modules=None):
    """Why companion/service.py would answer this request before the router, or '' when it reaches it
    (the same checks as tests/test_router_heldout.py; mail and the app catalog are not consulted)."""
    from companion import service, mail_reader
    from companion.arithmetic import answer, clock_answer
    from companion.quick_open import bare_name, plan
    from companion.request_plan import simple_app_command
    moving = service.MOVE_SELF.search(request)
    caught = {'arithmetic': answer(request), 'clock': clock_answer(request), 'never mind': service.NEVER_MIND.match(request),
              'move self': moving and service.geometric_destination(moving.group(0)),
              'bare verb': service.BARE_VERB.match(request), 'click': service.CLICK_REQUEST.match(request),
              'close tab': service.CLOSE_TAB.search(request), 'mail': mail_reader.position(request),
              'unread': mail_reader.UNREAD.search(request), 'conversation': service.ABOUT_CONVERSATION.search(request),
              'simple app': (simple_app_command(request) or {}).get('operation') not in (None, 'launch_app'),
              'quick open': plan(request, []), 'bare name': bare_name(request, [])}
    return ', '.join(name for name, hit in caught.items() if hit)


def install(name, plan_module, provider):
    """Patch an experimental wire format into this process's planner; None for the tree's own."""
    if name == 'production':
        return None
    if name == 'classify':
        # The tree's own format, except that an answer cannot carry a reply either: the router only
        # classifies and Companion.converse would write the reply. Nothing before "reply" changes.
        import copy
        schema = copy.deepcopy(plan_module.INTENT_WIRE_SCHEMA)
        for shape in schema['anyOf']:
            shape['properties']['reply'] = {'const': ''}
        plan_module.INTENT_WIRE_SCHEMA = schema
        return None
    item = VARIANTS[name]
    rules = getattr(plan_module, 'INTENT_RULES', plan_module.INTENT_SYSTEM)  # without any wire legend
    base_schema = plan_module.INTENT_SCHEMA  # the plan's fields
    schema = wire_schema(base_schema, item)
    plan_module.INTENT_SYSTEM = rules + item['legend']
    for constant in ('INTENT_SCHEMA', 'INTENT_WIRE_SCHEMA'):  # whichever the tree's planner sends
        if hasattr(plan_module, constant):
            setattr(plan_module, constant, schema)
    if hasattr(plan_module, 'expand_intent'):  # an implemented tree expands by itself: switch that off
        plan_module.expand_intent = lambda value: value
    complete = provider.complete

    def patched(system, messages, **kwargs):
        result = complete(system, messages, **kwargs)
        if kwargs.get('response_schema') is not schema:
            return result
        return (expand(item, result[0]), result[1]) if isinstance(result, tuple) else expand(item, result)
    provider.complete = patched
    return item


def heldout(args):
    """Aggregate held-out score of a wire format. Rows, requests and misses are never printed or saved."""
    tree = (args.tree or HERE.parent).resolve()
    modules = load_tree(tree)  # before router_heldout, which would import this copy's companion
    import router_heldout
    provider = modules['ollama'].OllamaProvider(modules['provider'].AIConfig(model=args.model, timeout=180))
    install(args.variant, modules['request_plan'], provider)
    tokens = []
    request = provider.request

    def recording(path, body=None, **kwargs):
        result = request(path, body, **kwargs)
        if path == '/api/chat' and body['messages'][0]['content'] == modules['request_plan'].INTENT_SYSTEM:
            tokens.append(result.get('eval_count') or 0)
        return result
    provider.request = recording
    rows = router_heldout.load()
    predictions, seconds = router_heldout.run(rows, provider)
    report = router_heldout.score(rows, predictions, seconds)
    for private in ('misses', 'confusion'):
        report.pop(private)
    report.update(variant=args.variant, tree=str(tree), router_tokens_mean=statistics.mean(tokens), router_tokens_p50=percentile(tokens, .5),
                  router_tokens_p95=percentile(tokens, .95), router_tokens_max=max(tokens))
    args.out.write_text(json.dumps(report, indent=1)+'\n', encoding='utf-8')
    print(json.dumps(report, indent=1))


def call_kind(system, router_system):
    if system == router_system:
        return 'router'
    if system.startswith('Check the FULL'):
        return 'coverage'
    if system.startswith('Identify the requested effect'):
        return 'motion'
    return 'other'


def run(args):
    from router_wire_cases import CASES, context
    tree = (args.tree or HERE.parent).resolve()
    modules = load_tree(tree)
    plan_module, ollama = modules['request_plan'], modules['ollama']
    provider = ollama.OllamaProvider(modules['provider'].AIConfig(model=args.model, timeout=180))
    item = install(args.variant, plan_module, provider)
    router_system = plan_module.INTENT_SYSTEM
    calls = []
    request = provider.request

    def recording(path, body=None, **kwargs):
        started = time.perf_counter()
        result = request(path, body, **kwargs)
        if path == '/api/chat':
            calls.append({'kind': call_kind(body['messages'][0]['content'], router_system),
                          'tokens': result.get('eval_count'), 'eval_ms': round(result.get('eval_duration', 0)/1e6, 1),
                          'prompt_tokens': result.get('prompt_eval_count'),
                          'prompt_ms': round(result.get('prompt_eval_duration', 0)/1e6, 1),
                          'total_ms': round(result.get('total_duration', 0)/1e6, 1),
                          'wall_ms': round((time.perf_counter()-started)*1000, 1),
                          'content': (result.get('message') or {}).get('content', '')})
        return result
    provider.request = recording
    selected = [case for case in CASES if not args.ids or case['id'] in args.ids][:args.limit or None]
    provider.warm_up([router_system])
    calls.clear()
    rows = []
    for case in selected:
        del calls[:]
        started = time.perf_counter()
        try:
            plan, error = provider.interpret_request(case['request'], case['recent'], context=context(case)), None
        except Exception as problem:  # a refused or invalid plan is a result, not a crash
            plan, error = None, '%s: %s' % (type(problem).__name__, problem)
        wall = round((time.perf_counter()-started)*1000, 1)
        raw = None
        router = [call for call in calls if call['kind'] == 'router']
        if router:
            try:
                raw = json.loads(router[-1]['content'])
                if item:
                    raw = expand(item, raw)
                elif hasattr(plan_module, 'expand_intent'):
                    raw = plan_module.expand_intent(raw)
            except ValueError:
                raw = None
        rows.append({'id': case['id'], 'request': case['request'], 'recent': case['recent'], 'ops': list(case['ops']),
                     'code_routed': code_routed(case['request']), 'plan': plan, 'raw': raw, 'error': error,
                     'wall_ms': wall, 'calls': list(calls)})
        print('%s %-13s %3s tok %6.0f ms  %s' % (case['id'], (plan or {}).get('operation', '<error>'),
              router[-1]['tokens'] if router else '-', wall, case['request'][:60]), flush=True)
    report = {'variant': args.variant, 'tree': str(tree), 'model': args.model, 'rows': rows,
              'system_chars': len(router_system), 'legend': item['legend'] if item else None}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=1, ensure_ascii=False)+'\n', encoding='utf-8')
    print(summary(report))


def percentile(values, share):
    values = sorted(values)
    return values[min(len(values)-1, int(round(share*(len(values)-1))))] if values else 0


def spread(values):
    return 'mean %.1f  p50 %.0f  p95 %.0f  max %.0f' % (statistics.mean(values), percentile(values, .5),
                                                         percentile(values, .95), max(values)) if values else 'none'


def effective(plan):
    """An opening folded into a task ("open Calculator and add 2 and 2") is still that opening."""
    plan = dict(plan or {})
    if isinstance(plan.get('opening'), dict):
        plan.update(plan['opening'])
    return plan


def summary(report):
    rows = report['rows']
    router = [call for row in rows for call in row['calls'] if call['kind'] == 'router']
    extra = {kind: [call for row in rows for call in row['calls'] if call['kind'] == kind] for kind in ('coverage', 'motion')}
    compact = [call for call in router if call['content']]
    padding = sum(len(call['content']) - len(json.dumps(json.loads(call['content']), ensure_ascii=False)) for call in compact
                  if call['content'].lstrip().startswith('{') and call['content'].rstrip().endswith('}'))
    lines = ['%s (%s): %d requests, %d errors' % (report['variant'], Path(report['tree']).name, len(rows),
                                                 sum(row['error'] is not None for row in rows)),
             '  router output tokens: ' + spread([call['tokens'] for call in router]),
             '  router eval ms:       ' + spread([call['eval_ms'] for call in router]),
             '  router call total ms: ' + spread([call['total_ms'] for call in router]),
             '  router ms per token:  %.1f' % (sum(call['eval_ms'] for call in router)/max(1, sum(call['tokens'] for call in router))),
             '  router prompt ms:     ' + spread([call['prompt_ms'] for call in router]),
             '  request wall ms:      ' + spread([row['wall_ms'] for row in rows]),
             '  characters beyond compact JSON in all router outputs: %d' % padding]
    for kind, items in extra.items():
        if items:
            lines.append('  %s calls: %d  tokens %s  total ms %s  prompt ms %s' % (
                kind, len(items), spread([c['tokens'] for c in items]), spread([c['total_ms'] for c in items]),
                spread([c['prompt_ms'] for c in items])))
    for label, key in (('raw router decision', 'raw'), ('final plan', 'plan')):
        hits = sum(effective(row[key]).get('operation') in row['ops'] for row in rows)
        reaching = [row for row in rows if not row['code_routed']]
        lines.append('  %s within the labelled operations: %d/%d (requests that reach the router: %d/%d)' % (
            label, hits, len(rows), sum(effective(row[key]).get('operation') in row['ops'] for row in reaching), len(reaching)))
    return '\n'.join(lines)


def shown(plan, field):
    value = effective(plan).get(field, DEFAULTS.get(field, ''))
    if field == 'reply':
        return 'present' if str(value).strip() else 'absent'
    return value.strip() if isinstance(value, str) else value


def compare(args):
    reference, candidate = (json.loads(path.read_text(encoding='utf-8')) for path in (args.reference, args.variant))
    print(summary(reference))
    print(summary(candidate))
    theirs = {row['id']: row for row in candidate['rows']}
    pairs = [(row, theirs[row['id']]) for row in reference['rows'] if row['id'] in theirs]
    for key, label in (('raw', 'raw router decision (before the coverage and motion checks)'), ('plan', 'final plan')):
        print('\nAgreement with the reference, %s, %d requests:' % (label, len(pairs)))
        differing = []
        for field in FIELDS:
            same = sum(shown(a[key], field) == shown(b[key], field) for a, b in pairs)
            print('  %-18s %d/%d' % (field, same, len(pairs)))
        whole = 0
        for a, b in pairs:
            fields = [field for field in FIELDS if shown(a[key], field) != shown(b[key], field)]
            whole += not fields
            if fields:
                differing.append((a, b, fields))
        print('  %-18s %d/%d' % ('every field', whole, len(pairs)))
        if key == 'raw' and not args.raw:
            continue
        print('\nDisagreements (%s): %d' % (label, len(differing)))
        for a, b, fields in differing:
            print('- %s %r%s%s  [labelled: %s]' % (a['id'], a['request'], ' after %r' % a['recent'] if a['recent'] else '',
                                                  '  (code-routed: %s)' % a['code_routed'] if a['code_routed'] else '',
                                                  '|'.join(a['ops'])))
            for field in fields:
                print('    %-18s reference %r -> variant %r' % (field, shown(a[key], field), shown(b[key], field)))
            if 'reply' in fields or 'operation' in fields:
                for name, row in (('reference', a), ('variant', b)):
                    reply = str(effective(row[key]).get('reply', '')).strip()
                    if reply:
                        print('    %s reply: %s' % (name, reply[:160]))
            for name, row in (('reference', a), ('variant', b)):
                if row['error']:
                    print('    %s error: %s' % (name, row['error']))


def confidence(tokens, key):
    """ollama.reply_confidence for another key: the mean probability of the three least likely
    tokens of that string value, or None."""
    import math
    import re
    texts = [token.get('token', '') for token in tokens if isinstance(token, dict)]
    text = ''.join(texts)
    match = re.search(r'"%s"\s*:\s*"' % key, text)
    if not match:
        return None
    start = end = match.end()
    while end < len(text) and not (text[end] == '"' and text[end-1] != '\\'):
        end += 1
    probabilities, position = [], 0
    for token, piece in zip(tokens, texts):
        if position < end and position+len(piece) > start and piece.strip():
            probabilities.append(math.exp(float(token.get('logprob', 0))))
        position += len(piece)
    lowest = sorted(probabilities)[:3]
    return round(sum(lowest)/len(lowest), 3) if lowest else None


def converse(args):
    """The reply Companion.converse would write for each conversational case (first turn: no earlier
    conversation; the same system prompt, schema, context without front_app and token limit)."""
    from router_wire_cases import CASES, context
    tree = (args.tree or HERE.parent).resolve()
    modules = load_tree(tree)
    service = importlib.import_module('companion.service')
    provider = modules['ollama'].OllamaProvider(modules['provider'].AIConfig(model=args.model, timeout=180))
    wanted = {row['id'] for path in args.runs for row in json.loads(path.read_text(encoding='utf-8'))['rows']
              if (row['plan'] or {}).get('operation') == 'answer'}
    last = {}
    request = provider.request

    def recording(path, body=None, **kwargs):
        result = request(path, body, **kwargs)
        if path == '/api/chat':
            last.update(result)
        return result
    provider.request = recording
    provider.warm_up([service.CHAT_SYSTEM])
    rows = []
    for case in CASES:
        if case['id'] not in wanted:
            continue
        about = {key: value for key, value in context(case).items() if key != 'front_app'}
        messages = [{'role': 'user', 'content': earlier} for earlier in ()]
        messages += [{'role': 'user', 'content': json.dumps(about, ensure_ascii=False)}, {'role': 'user', 'content': case['request']}]
        started = time.perf_counter()
        reply, tokens = provider.complete(service.CHAT_SYSTEM, messages, response_schema=service.SCREEN_SCHEMA,
                                          max_tokens=300, thinking=False, logprobs=True)
        rows.append({'id': case['id'], 'request': case['request'], 'message': reply.get('message'),
                     'confidence': confidence(tokens, 'message'), 'tokens': last.get('eval_count'),
                     'eval_ms': round(last.get('eval_duration', 0)/1e6, 1), 'prompt_tokens': last.get('prompt_eval_count'),
                     'prompt_ms': round(last.get('prompt_eval_duration', 0)/1e6, 1),
                     'total_ms': round(last.get('total_duration', 0)/1e6, 1),
                     'wall_ms': round((time.perf_counter()-started)*1000, 1)})
        print('%s %3d tok %6.0f ms conf %s  %s -> %s' % (case['id'], rows[-1]['tokens'], rows[-1]['wall_ms'], rows[-1]['confidence'],
                                                        case['request'][:40], str(reply.get('message'))[:70]), flush=True)
    args.out.write_text(json.dumps({'tree': str(tree), 'model': args.model, 'rows': rows}, indent=1, ensure_ascii=False)+'\n', encoding='utf-8')


def chat(args):
    """Chat turns three ways: the router writes the reply and converse rewrites it; the router's reply
    alone; the router only classifies and converse writes."""
    from companion.web_lookup import plain_question
    said = {row['id']: row for row in json.loads(args.converse.read_text(encoding='utf-8'))['rows']}
    writes, classifies = (json.loads(path.read_text(encoding='utf-8')) for path in (args.writes, args.classifies))
    theirs = {row['id']: row for row in classifies['rows']}

    def router(row):
        return [call for call in row['calls'] if call['kind'] == 'router'][-1]
    turns = [row for row in writes['rows'] if (row['plan'] or {}).get('operation') == 'answer' and row['id'] in said and row['id'] in theirs]
    print('%d chat turns (operation=answer in %s)' % (len(turns), writes['variant']))
    for label, parts in (('router writes reply, converse rewrites it', lambda row: (router(row), said[row['id']])),
                         ('router writes reply only', lambda row: (router(row),)),
                         ('router classifies (%s), converse writes' % classifies['variant'], lambda row: (router(theirs[row['id']]), said[row['id']]))):
        print('  %-58s output tokens %s | model ms %s' % (
            label, spread([sum(part['tokens'] for part in parts(row)) for row in turns]),
            spread([sum(part['total_ms'] for part in parts(row)) for row in turns])))
    print('  converse alone: prompt tokens %s | prompt ms %s | output tokens %s' % (
        spread([said[row['id']]['prompt_tokens'] for row in turns]), spread([said[row['id']]['prompt_ms'] for row in turns]),
        spread([said[row['id']]['tokens'] for row in turns])))
    moved = [row for row in turns if (theirs[row['id']]['plan'] or {}).get('operation') != 'answer']
    print('  chat turns the classifying router sends elsewhere: %d' % len(moved))
    for row in moved:
        print('    %s %r -> %s' % (row['id'], row['request'], (theirs[row['id']]['plan'] or {}).get('operation', '<error>')))
    gained = [row for row in classifies['rows'] if (row['plan'] or {}).get('operation') == 'answer'
              and row['id'] not in {turn['id'] for turn in turns}]
    print('  other turns the classifying router calls answer: %d' % len(gained))
    for row in gained:
        print('    %s %r (labelled %s)' % (row['id'], row['request'], '|'.join(row['ops'])))
    print('\nreply confidence (below 0.7 on a plain question sends the reply to an online check):')
    flips = 0
    for row in turns:
        first, second = (row['plan'] or {}).get('confidence'), said[row['id']]['confidence']
        question = plain_question(row['request'])
        differ = question and first is not None and second is not None and (first < 0.7) != (second < 0.7)
        flips += bool(differ)
        print('  %s %-52r router %-6s converse %-6s%s%s' % (row['id'], row['request'][:50], first, second,
                                                       '  question' if question else '', '  DIFFERENT SIDE' if differ else ''))
    print('  plain questions whose confidence falls on the other side of 0.7: %d' % flips)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)
    runner = commands.add_parser('run')
    runner.add_argument('--tree', type=Path, help='source tree whose companion package routes (default: this one)')
    runner.add_argument('--variant', default='production', choices=['production', 'classify', *VARIANTS])
    runner.add_argument('--model', default='qwen3:4b-instruct', help='local Ollama model')
    runner.add_argument('--out', type=Path, required=True)
    runner.add_argument('--limit', type=int)
    runner.add_argument('--ids', nargs='*')
    comparer = commands.add_parser('compare')
    comparer.add_argument('reference', type=Path)
    comparer.add_argument('variant', type=Path)
    comparer.add_argument('--raw', action='store_true', help='list raw-decision disagreements too')
    tokens = commands.add_parser('tokens')
    tokens.add_argument('files', type=Path, nargs='+')
    hidden = commands.add_parser('heldout', help='aggregate held-out score of a wire format (no rows shown)')
    hidden.add_argument('--tree', type=Path)
    hidden.add_argument('--variant', default='production', choices=['production', 'classify', *VARIANTS])
    hidden.add_argument('--model', default='qwen3:4b-instruct')
    hidden.add_argument('--out', type=Path, required=True)
    talker = commands.add_parser('converse', help='write the Companion.converse reply for every case a run answered')
    talker.add_argument('runs', type=Path, nargs='+')
    talker.add_argument('--tree', type=Path)
    talker.add_argument('--model', default='qwen3:4b-instruct')
    talker.add_argument('--out', type=Path, required=True)
    chatter = commands.add_parser('chat', help='chat-turn cost: router reply, converse reply, or both')
    chatter.add_argument('writes', type=Path, help='a run whose router writes the reply')
    chatter.add_argument('classifies', type=Path, help='a run of a *_noreply variant')
    chatter.add_argument('converse', type=Path, help='output of the converse command')
    args = parser.parse_args(argv)
    if args.command == 'run':
        run(args)
    elif args.command == 'compare':
        compare(args)
    elif args.command == 'heldout':
        heldout(args)
    elif args.command == 'converse':
        converse(args)
    elif args.command == 'chat':
        chat(args)
    else:
        for path in args.files:
            print(summary(json.loads(path.read_text(encoding='utf-8'))))


if __name__ == '__main__':
    sys.path.insert(0, str(HERE))
    main()
