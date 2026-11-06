#!/usr/bin/env python3
"""Whole computer tasks through the real task loop and real local models, on a simulated desktop.

ComputerTask, its validation and DesktopContext/ScreenVision run unmodified against the stateful
apps in sim_world; success is read from the simulated world's state and exact seeded facts in the
final reply, never from what the assistant claims and never by a model. Local Ollama only. Nothing
is sent to the real machine: no subprocess, screen capture or input (sim_world.GUARD fails closed).

    scripts/sim_eval.py --split dev --output <new folder>
    scripts/sim_eval.py --compare <baseline folder> <candidate folder>

Results are bounded by sim_world.DEVIATIONS (copied into every manifest): this is a matched
baseline/candidate instrument for task-loop correctness and latency, not live evidence.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import statistics
import sys
import threading
import time
import urllib.request

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import sim_cases  # noqa: E402
import sim_world  # noqa: E402

STATUSES = ('pass', 'fail', 'timeout', 'error', 'cancelled')
OLLAMA = 'http://127.0.0.1:11434'
HARNESS_FILES = ('sim_world.py', 'sim_cases.py', 'sim_eval.py')
# Inference settings a run inherits from the environment; recorded, never changed here.
INHERITED = ('A11Y_OLLAMA_CONTEXT', 'A11Y_OLLAMA_KEEP_ALIVE', 'A11Y_OLLAMA_THINKING', 'A11Y_MODEL_IMAGE_WIDTH', 'A11Y_FOCUSED_GROUNDING')
# Set for the whole run: local provider, no saved model choice, default GUI profile, no evaluation side channels.
CLEARED = ('A11Y_GUI_PROFILE', 'A11Y_GUI_MODEL', 'A11Y_EVAL', 'A11Y_EVAL_SHOTS', 'A11Y_EVAL_GROUNDING_EXPORT', 'ANTHROPIC_API_KEY', 'A11Y_CLAUDE_MODEL')
# Content-free runtime timing records worth keeping per step, besides the inference calls.
RUNTIME_EVENTS = ('task.completion', 'task.file_interaction', 'task.commit_check', 'task.reconsider.start', 'task.focused_grounding', 'task.step.summary',
                  'finder.facts')
ENTRY = 'prompt: ComputerTask(goal, automatic=True) as from the companion’s computer prompt (no goal_mode, opening or looking_at)'
SMALL_FAMILY = 3          # acceptance results of a family with fewer distinct tasks are only reported merged
CONTENDED_SHARE = 0.15    # queueing or model loading above this share of inference time marks a run's timings as contended
SPEED_TOLERANCE = 0.2     # two runs whose median generation speed differs by more ran under different load
DETAIL_NOTE = ('Per-task results of an ACCEPTANCE run. Agents that change the product must not read this folder: '
               'use summary.json, which holds totals only. It is kept for the owner and for audits of the harness.\n')


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def harness_hash():
    return hashlib.sha256(''.join(sha256(HERE/name) for name in HARNESS_FILES).encode()).hexdigest()


def source_files(source):
    return {str(path.relative_to(source)): sha256(path) for pattern in ('companion/*.py', 'native/*.swift') for path in sorted(Path(source).glob(pattern))}


def git_head(root):
    """The checked-out commit, read from .git files (no git process); None for a folder that is not a checkout."""
    try:
        git = Path(root)/'.git'
        if git.is_file():
            git = (Path(root)/git.read_text().split('gitdir:', 1)[1].strip()).resolve()
        head = (git/'HEAD').read_text().strip()
        if not head.startswith('ref:'):
            return head
        ref = head.split(None, 1)[1]
        common = git/'commondir'
        base = (git/common.read_text().strip()).resolve() if common.is_file() else git
        if (base/ref).is_file():
            return (base/ref).read_text().strip()
        for line in (base/'packed-refs').read_text().splitlines():
            if line.endswith(' '+ref):
                return line.split()[0]
    except (OSError, IndexError):
        pass
    return None


def ollama_get(path, timeout=5):
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(OLLAMA+path, timeout=timeout) as response:
        return json.load(response)


def model_digests(names):
    try:
        listed = {item.get('name'): item.get('digest') for item in ollama_get('/api/tags').get('models', [])}
    except (OSError, ValueError):
        return {name: None for name in names}
    return {name: listed.get(name) for name in names}


def server_reachable():
    """Whether the local model server answers at all (tells a timed-out request from a dead server)."""
    try:
        return bool(ollama_get('/api/version', timeout=3))
    except (OSError, ValueError):
        return False


def percentile(values, fraction):
    """Nearest-rank percentile; None without samples."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered)-1, max(0, -(-round(fraction*100)*len(ordered)//100)-1))]


def spread(values):
    """p50 and max always; p95 only from 20 samples up, where it means something."""
    values = [value for value in values if value is not None]
    return {'n': len(values), 'p50': percentile(values, 0.5), 'p95': percentile(values, 0.95) if len(values) >= 20 else None,
            'max': max(values) if values else None}


def compact_action(action):
    keep = ('type', 'label', 'x', 'y', 'button', 'click_count', 'key', 'text', 'direction', 'url', 'browser_mode', 'bundle_id', 'path', 'control_id', 'milliseconds')
    return {key: action[key] for key in keep if key in action}


def model_calls(events):
    """One record per inference from the companion.timing stream (content-free). `unexplained` marks a failed
    request that was not immediately retried as the runtime's own JSON-mode fallback."""
    calls, started, failed = [], None, None
    for event in events:
        name = event.get('event')
        if name == 'ollama.start':
            if failed is not None and event.get('json_fallback'):
                failed['unexplained'] = False   # the schema was rejected and this is the retry in JSON mode
            failed, started = None, event
        elif name in ('ollama.finished', 'ollama.failed', 'ollama.cancelled'):
            call = {'outcome': name.split('.')[1], 'ms': event.get('elapsed_ms'), 'image': bool((started or {}).get('image'))}
            for source, target in (('prompt_eval_count', 'prompt_tokens'), ('eval_count', 'output_tokens'), ('prompt_eval_ms', 'prompt_ms'),
                                   ('eval_ms', 'output_ms'), ('load_ms', 'load_ms'), ('total_ms', 'server_ms'), ('truncated', 'truncated')):
                if source in event:
                    call[target] = event[source]
            if name == 'ollama.failed':
                call['unexplained'] = True
                failed = call
            calls.append(call)
            started = None
        elif name == 'gui.inference':
            calls.append({'outcome': 'finished', 'ms': event.get('elapsed_ms'), 'image': True, 'kind': 'grounding',
                          'prompt_tokens': event.get('prompt_eval_count'), 'output_tokens': event.get('eval_count')})
    return calls


def run_task(scenario, provider, *, max_steps=24, task_timeout=600, pause=0.45, brief=None, frames=None, data_dir=None, reachable=server_reachable):
    """Drive one scenario the way CompanionWindow drives a task (desktop.py: mascot_submit 'computer' ->
    offer_computer_task -> next_computer_step / receive_computer_step / execute_desktop_action), with a scripted
    user for confirmations and, in cancellation scenarios, a scripted Stop."""
    from companion import routes, timing
    from companion import computer_task
    from companion.computer_use import ComputerUse
    from companion.models import ValidationError
    from companion.ollama import OllamaRequestError, RequestCancelled
    from companion.provider import ProviderError
    world = scenario.world
    events, steps = [], []
    outcome = {'loop_status': None, 'message': '', 'raw_message': '', 'stop_source': None, 'pending_effects': None, 'declined': None, 'budget_exhausted': False,
               'wall_exceeded': False, 'error': None, 'runtime_exception': None, 'model_failure': None, 'cancel': None,
               'reviews': {'asked': 0, 'approved': 0, 'declined': 0}}
    plan = scenario.cancel if scenario.expect == 'cancel' else None
    cancel = {'delivered': False, 'at': None, 'timer': None} if plan else None
    timing.listeners.append(events.append)
    started = time.monotonic()
    sim = None

    def model_trouble(error):
        """A model or server failure: 'environment' when the server cannot serve, else the product's own failure."""
        text = str(error)
        down = (isinstance(error, OllamaRequestError) and error.category in ('memory', 'missing')) or ('not reachable' in text and not reachable())
        if down:
            outcome['error'] = 'local model server unavailable: '+text[:200]
        outcome['model_failure'] = 'environment' if down else 'model'

    try:
        sim = sim_world.SimDesktop(world, data_dir)
        with sim:
            native = sim.native
            task = computer_task.ComputerTask(scenario.request, automatic=True, max_steps=max_steps)

            def stop_now():
                if not cancel['delivered']:
                    world.mark_stop()
                    cancel.update(delivered=True, at=time.monotonic())
                    task.stop()   # what "Stop" does (stop_computer_task)

            def on_event(event):
                if (plan and plan['at'] == 'propose' and cancel['timer'] is None and len(task.receipts) >= plan['after_actions']
                        and event.get('event') in ('ollama.start', 'task.context.prepared')):
                    cancel['timer'] = threading.Timer(plan.get('delay', 0.3), stop_now)
                    cancel['timer'].start()
            timing.listeners.append(on_event)
            wall = threading.Event()
            watchdog = threading.Timer(task_timeout, lambda: (wall.set(), task.stop()))
            watchdog.daemon = True
            watchdog.start()

            def finish(status, message, source=None):
                outcome.update(loop_status=status, raw_message=message, stop_source=source)

            try:
                while not task.cancelled.is_set() and len(steps) < max_steps*3+6:
                    record = {'index': len(steps)+1}
                    steps.append(record)
                    mark, inputs_before, step_started = len(events), len(world.inputs), time.monotonic()
                    step = failure = source = None
                    try:
                        step = task.take_opening() or task.propose(sim.desktop, sim.vision, provider, {'x': 0, 'y': 0}, progress=lambda message: None)
                    except ProviderError as error:   # TaskStepWorker.failed -> CompanionWindow.failed
                        failure, source = str(error), 'runtime'
                        if not isinstance(error, RequestCancelled) and not task.cancelled.is_set():
                            model_trouble(error)
                    except ValidationError as error:   # the loop's own "I could not find a safe next step"
                        failure, source = str(error), 'propose'
                    except (ValueError, RuntimeError) as error:
                        failure, source = str(error), 'runtime'
                    except Exception as error:   # noqa: BLE001 - the worker reports any other error the same way
                        failure, source = 'The computer task could not plan a valid next step. No next action was sent.', 'runtime'
                        outcome['runtime_exception'] = 'runtime exception in propose: %s: %s' % (type(error).__name__, str(error)[:300])
                    record['propose_ms'] = round((time.monotonic()-step_started)*1000)
                    if cancel and cancel['timer'] is not None:
                        cancel['timer'].join()   # Stop lands while this step is being decided, before anything executes
                        if cancel['delivered'] and 'returned' not in cancel:
                            cancel['returned'] = 'raised' if failure is not None else (step or {}).get('status', 'nothing')
                            cancel['stop_to_return_ms'] = max(0, round((step_started+record['propose_ms']/1000-cancel['at'])*1000))
                    proposed = events[mark:]
                    summary = next((e for e in reversed(proposed) if e.get('event') == 'task.step.summary'), {})
                    record.update(observed={key: native.last_served.get(key) for key in ('app', 'window', 'url', 'focused_role')},
                                  targets=list(native.last_served.get('targets') or [])[:80], text=list(native.last_served.get('text') or [])[:80],
                                  code_path=summary.get('code'), model_calls=model_calls(proposed),
                                  rejected_proposals=[{key: e.get(key) for key in ('attempt', 'action_type', 'reason')} for e in proposed if e.get('event') == 'task.step.invalid'],
                                  runtime_events=[e for e in proposed if e.get('event') in RUNTIME_EVENTS],
                                  inputs_while_deciding=len(world.inputs)-inputs_before)
                    if frames is not None and native.last_capture:
                        (frames/('%s-step%02d.png' % (scenario.id, record['index']))).write_bytes(native.last_capture)
                    if task.cancelled.is_set():
                        record['proposal'] = {'status': 'dropped', 'message': failure or (step or {}).get('message', '')}
                        break   # receive_computer_step ignores a step that arrives after Stop
                    if failure is not None:
                        record['proposal'] = {'status': 'failed', 'message': failure}
                        task.stop('blocked')
                        finish('exception' if outcome['runtime_exception'] else 'blocked', failure, source)
                        break
                    record['proposal'] = {'status': step['status'], 'message': step['message'], 'review': step.get('review'),
                                          **({'action': compact_action(step['action'])} if step.get('action') else {})}
                    if step['status'] != 'action':
                        task.stop(step['status'])
                        if step['status'] == 'done':
                            routes.remember(task.goal, task.receipts)
                        finish(step['status'], step['message'], 'loop')
                        break
                    action = step['action']
                    if action['type'] == 'click_point' and (not step.get('visual_capture') or not action.get('capture_guard')):
                        task.stop('blocked')   # CompanionWindow.received drops a click without a fresh capture
                        finish('blocked', 'A screen click needs a fresh local capture. Enable screen vision and ask again.', 'runtime')
                        break
                    effects, changes = world.consequences(action)
                    record['previewed_effects'], record['previewed_changes'] = effects, [['/'.join(map(str, key)), value] for key, value in changes]
                    if step['review']:   # only the product decides that a step needs confirmation; the user only answers
                        outcome['reviews']['asked'] += 1
                        approved, why = sim_cases.user_decision(scenario, effects, changes)
                        record['user'] = 'approved' if approved else 'declined'
                        outcome['reviews']['approved' if approved else 'declined'] += 1
                        if not approved:
                            # The confirmation button is never pressed: the proposal stays pending and the person stops the task.
                            outcome.update(pending_effects=effects, declined=why)
                            finish('awaiting_confirmation', step['message'], 'loop')
                            task.stop()
                            break
                        world.grant, world.reviewed = tuple(effects), True   # exactly what was previewed and confirmed
                    else:
                        record['user'] = 'not_asked'
                    execution = {'inputs': [], 'effects': []}
                    record['execution'] = execution
                    inputs_before, effects_before, exec_started = len(world.inputs), len(world.effects), time.monotonic()
                    retry = False
                    world.executing = len(steps)
                    if plan and plan['at'] == 'execute' and not cancel['delivered'] and len(task.receipts) >= plan['after_actions']:
                        native.before_input = stop_now   # Stop lands as this action's input reaches the app
                    try:
                        result = ComputerUse(sim.desktop, vision=sim.vision, provider=provider).execute_confirmed(dict(action))
                    except (ValueError, RuntimeError) as error:   # ComputerActionWorker.failed
                        message = str(error)
                        execution.update(status='failed', message=message)
                        if (('covers that point' in message or 'changed' in message or 'did not arrive' in message)
                                and task.stale_retries < 2 and not task.cancelled.is_set()):
                            task.stale_retries += 1
                            task.pending = None
                            retry = True
                            execution['status'] = 'retry'
                        elif not task.cancelled.is_set():
                            task.stop('blocked')
                            finish('blocked', message, 'execute')
                    except Exception as error:   # noqa: BLE001
                        execution.update(status='failed', message='The action result is uncertain. Check the app before trying again.')
                        outcome['runtime_exception'] = 'runtime exception in execute: %s: %s' % (type(error).__name__, str(error)[:300])
                        task.stop('blocked')
                        finish('exception', execution['message'], 'execute')
                    else:
                        execution['status'] = result.get('status', 'submitted')
                        task.stale_retries = 0
                        task.record(result)   # finished(): an action already under way is recorded even after Stop
                    finally:
                        native.before_input = None
                        world.grant, world.reviewed, world.executing = (), False, 0
                        execution['ms'] = round((time.monotonic()-exec_started)*1000)
                        execution['inputs'] = [{key: value for key, value in entry.items() if key not in ('t', 'seq', 'front')} for entry in world.inputs[inputs_before:]]
                        execution['effects'] = [{'effect': e['effect'], 'authorized': e['authorized']} for e in world.effects[effects_before:]]
                        record['step_ms'] = round((time.monotonic()-step_started)*1000)
                        if cancel and cancel['delivered'] and 'returned' not in cancel:
                            cancel.update(returned='action finished', stop_to_return_ms=max(0, round((time.monotonic()-cancel['at'])*1000)))
                    if outcome['loop_status'] or task.cancelled.is_set():
                        break
                    if plan and plan['at'] == 'pause' and not cancel['delivered'] and len(task.receipts) >= plan['after_actions']:
                        timer = threading.Timer(max(0.01, pause/3), stop_now)
                        timer.start()
                        time.sleep(pause)
                        timer.join()
                        cancel.update(returned='idle', stop_to_return_ms=0)
                    else:
                        time.sleep(0.3 if retry else pause)   # the QTimer pauses before next_computer_step
                    if plan and plan['at'] == 'between' and not cancel['delivered'] and len(task.receipts) >= plan['after_actions']:
                        stop_now()
                        cancel.update(returned='idle', stop_to_return_ms=0)
                else:
                    if not task.cancelled.is_set():
                        outcome['error'] = 'driver safety limit: the task loop did not end'
                        task.stop('blocked')
            finally:
                watchdog.cancel()
                timing.listeners.remove(on_event)
                if cancel and cancel['timer'] is not None:
                    cancel['timer'].join()
            if steps:
                steps[-1].setdefault('step_ms', steps[-1].get('propose_ms'))
            if wall.is_set() and not (cancel and cancel['delivered']):
                outcome['wall_exceeded'] = True
                outcome['loop_status'] = outcome['loop_status'] or 'wall_limit'
            elif task.cancelled.is_set() and outcome['loop_status'] is None:
                outcome['loop_status'] = 'cancelled'
            outcome['budget_exhausted'] = len(task.receipts) >= task.max_steps
            outcome['actions'] = len(task.receipts)
            if cancel and cancel['delivered']:
                # A step timer that fires after Stop (QTimer -> next_computer_step) must get nothing from the task.
                try:
                    task.propose(sim.desktop, sim.vision, provider, {'x': 0, 'y': 0})
                    cancel['late_propose_refused'] = False
                except (ValueError, RuntimeError):
                    cancel['late_propose_refused'] = True
                time.sleep(0.05)   # anything still in flight would land now; the world's own log decides
            if cancel:
                outcome['cancel'] = {key: cancel[key] for key in ('delivered', 'late_propose_refused', 'returned', 'stop_to_return_ms') if key in cancel}
                outcome['cancel'].update(point=plan['at'], inputs_after_stop=len(world.late_inputs()))
            unexplained = sum(bool(call.get('unexplained')) for call in model_calls(events))
            if unexplained and not outcome['model_failure']:
                # A request the runtime swallowed (a verification that "could not be checked"): the product's failure
                # unless the server itself is gone.
                if reachable():
                    outcome['model_failure'] = 'model'
                else:
                    outcome.update(model_failure='environment', error='local model server unavailable: %d request(s) failed' % unexplained)
            outcome['unsupported_native_calls'] = list(world.unsupported)
            outcome['frames_captured'] = native.frames
    except sim_world.Tripwire as error:
        outcome['error'] = str(error)
        outcome['loop_status'] = outcome['loop_status'] or 'exception'
    except Exception as error:   # noqa: BLE001 - a harness failure is an error status, never a pass or fail
        outcome['error'] = 'harness exception: %s: %s' % (type(error).__name__, str(error)[:300])
        outcome['loop_status'] = outcome['loop_status'] or 'exception'
    finally:
        timing.listeners.remove(events.append)
    violations = sim.violations() if sim is not None else []
    if violations:   # wherever it happened, including the app-awareness poll thread
        outcome['error'] = 'tripwire: '+'; '.join(violations[:3])
    outcome['tripwire'] = violations
    raw = outcome['raw_message']
    if outcome['loop_status'] in ('done', 'needs_user') and brief is not None:
        shown = brief(raw)   # what the companion says (receive_computer_step)
        outcome['message'] = shown if outcome['loop_status'] == 'done' or shown.startswith('I ') else 'I stopped: '+shown
    else:
        outcome['message'] = raw
    outcome['unreviewed_changes'] = sum(not change['reviewed'] for change in world.changes)
    outcome['page_reads'] = world.page_reads
    outcome['wall_seconds'] = round(time.monotonic()-started, 3)
    outcome['steps'] = steps
    return outcome


def task_record(scenario, outcome, verdict, run):
    steps = outcome.pop('steps')
    calls = [call for step in steps for call in step.get('model_calls', [])]
    return {'id': scenario.id, 'family': scenario.family, 'split': scenario.split, 'run': run, 'request': scenario.request, 'status': verdict['status'],
            'reasons': verdict['reasons'], 'false_success': verdict['false_success'], 'missed_success': verdict['missed_success'],
            'unauthorized_actions': verdict['unauthorized_actions'], 'expected': verdict['expected'], 'actual': verdict['actual'],
            'final': {key: outcome.get(key) for key in ('loop_status', 'message', 'raw_message', 'stop_source', 'pending_effects', 'declined', 'budget_exhausted',
                                                        'wall_exceeded', 'error', 'runtime_exception', 'model_failure', 'actions', 'unreviewed_changes',
                                                        'page_reads', 'unsupported_native_calls', 'frames_captured', 'tripwire')},
            'reviews': outcome.get('reviews'), 'cancel': outcome.get('cancel'), 'wall_seconds': outcome.get('wall_seconds'),
            'step_count': len(steps), 'model_call_count': len(calls),
            'model_seconds': round(sum(call.get('ms') or 0 for call in calls)/1000, 3),
            'server_seconds': round(sum(call.get('server_ms') or 0 for call in calls)/1000, 3), 'steps': steps}


def summarize(rows, planned, small=()):
    """Counts with every denominator. `planned` is [(id, family)] for every task-run that should exist; families
    named in `small` are reported only merged (acceptance families with too few tasks to hide a single result)."""
    def timings(selected):
        calls = [call for row in selected for step in row['steps'] for call in step.get('model_calls', [])]
        return {'seconds_per_task': spread([row.get('wall_seconds') for row in selected]),
                'seconds_per_step': spread([step['step_ms']/1000 for row in selected for step in row['steps'] if step.get('step_ms') is not None]),
                'model_seconds_per_task': spread([row.get('server_seconds') for row in selected]),
                'model_seconds_per_call': spread([call['server_ms']/1000 for call in calls if call.get('server_ms') is not None])}

    def block(selected, expected):
        counts = {status: sum(row['status'] == status for row in selected) for status in STATUSES}
        scored = counts['pass']+counts['fail']+counts['timeout']
        timed = [row for row in selected if row['family'] != 'cancel' and row['status'] in ('pass', 'fail', 'timeout')]
        step_counts = [row['step_count'] for row in timed]
        calls = sum(row['model_call_count'] for row in selected)
        steps = sum(row['step_count'] for row in selected)
        return {'planned': expected, 'attempted': len(selected), 'not_attempted': max(0, expected-len(selected)), 'statuses': counts,
                'scored_attempts': scored, 'pass_rate_of_scored': round(counts['pass']/scored, 4) if scored else None,
                'pass_rate_of_planned': round(counts['pass']/expected, 4) if expected else None,
                'false_success': sum(bool(row['false_success']) for row in selected), 'missed_success': sum(bool(row['missed_success']) for row in selected),
                'tasks_with_unauthorized_action': sum(row['unauthorized_actions'] > 0 for row in selected),
                'unauthorized_actions': sum(row['unauthorized_actions'] for row in selected),
                'unreviewed_changes': sum((row.get('final') or {}).get('unreviewed_changes') or 0 for row in selected),
                'runtime_exceptions': sum(bool((row.get('final') or {}).get('runtime_exception')) for row in selected),
                'model_failures': sum((row.get('final') or {}).get('model_failure') == 'model' for row in selected),
                'unsupported_native_calls': sum(len((row.get('final') or {}).get('unsupported_native_calls') or []) for row in selected),
                'steps_per_task': {'mean': round(statistics.mean(step_counts), 2) if step_counts else None, 'p50': percentile(step_counts, 0.5),
                                   'max': max(step_counts) if step_counts else None, 'n': len(step_counts)},
                'model_calls': calls, 'model_calls_per_step': round(calls/steps, 2) if steps else None,
                # Timing populations are kept apart: cancelled and errored attempts are not timing samples, and a failed
                # task that gives up early is not comparable with a pass.
                'timing': {'passed': timings([row for row in timed if row['status'] == 'pass']),
                           'not_passed': timings([row for row in timed if row['status'] != 'pass'])}}

    families = sorted({family for _, family in planned})
    shown = [family for family in families if family not in small]
    merged = [family for family in families if family in small]
    cancels = [row for row in rows if row['family'] == 'cancel']
    reviews = {key: sum((row.get('reviews') or {}).get(key, 0) for row in rows) for key in ('asked', 'approved', 'declined')}
    stop_ms = [row['cancel']['stop_to_return_ms'] for row in cancels if (row.get('cancel') or {}).get('stop_to_return_ms') is not None]
    calls = [call for row in rows for step in row['steps'] for call in step.get('model_calls', []) if call.get('outcome') == 'finished' and call.get('server_ms') is not None]
    client, server, load = (sum(call.get(key) or 0 for call in calls) for key in ('ms', 'server_ms', 'load_ms'))
    speeds = [call['output_tokens']/(call['output_ms']/1000) for call in calls if call.get('output_ms') and (call.get('output_tokens') or 0) >= 8]
    queue_share = round(max(0, client-server)/client, 3) if client else None
    load_share = round(load/server, 3) if server else None
    result = {'overall': block(rows, len(planned)),
              'families': {family: block([row for row in rows if row['family'] == family], sum(f == family for _, f in planned)) for family in shown},
              'cancellation': {'planned': sum(family == 'cancel' for _, family in planned), 'delivered': sum(bool((row.get('cancel') or {}).get('delivered')) for row in cancels),
                               'clean': sum(row['status'] == 'cancelled' for row in cancels),
                               'inputs_after_stop': sum((row.get('cancel') or {}).get('inputs_after_stop', 0) for row in cancels),
                               'stop_to_return_ms': dict(spread(stop_ms), limit=sim_cases.STOP_BOUND_MS)},
              'confirmation_prompts': reviews,
              'load': {'inference_seconds': round(client/1000, 1), 'server_seconds': round(server/1000, 1), 'queue_share': queue_share, 'model_load_share': load_share,
                       'output_tokens_per_second_p50': round(percentile(speeds, 0.5), 1) if speeds else None,
                       'contended': bool((queue_share or 0) > CONTENDED_SHARE or (load_share or 0) > CONTENDED_SHARE),
                       'meaning': 'queue_share is inference time the client waited beyond what the server reports; model_load_share is server time spent '
                                  'loading or swapping models. Above %.0f%% either marks the timings as contended.' % (CONTENDED_SHARE*100)},
              'note': 'pass/fail/timeout are scored attempts (a product crash or failed model request is a fail); error is a harness fault, a tripwire or an '
                      'unavailable model server; cancelled is a cancellation scenario that stopped cleanly (a dirty one is fail). pass_rate_of_scored excludes '
                      'cancellation scenarios and errors. p95 is given only from 20 samples.'}
    if merged:
        result['families_merged'] = dict(block([row for row in rows if row['family'] in merged], sum(f in merged for _, f in planned)), families=merged)
    return result


def compare(first, second):
    """Baseline and candidate summaries side by side; refuses runs that are not matched or not clean."""
    loaded = []
    for folder in (first, second):
        try:
            loaded.append((json.loads((Path(folder)/'manifest.json').read_text()), json.loads((Path(folder)/'summary.json').read_text())))
        except (OSError, ValueError) as error:
            return 2, {'error': 'Could not read a finished run in %s: %s' % (folder, error)}
    (base, base_summary), (candidate, candidate_summary) = loaded
    problems = [key for key in ('protocol', 'split', 'seed', 'runs', 'case_hash', 'task_ids', 'harness_hash', 'entry') if base.get(key) != candidate.get(key)]
    digests = lambda manifest: {role: (model.get('name'), model.get('digest')) for role, model in (manifest.get('models') or {}).items()}
    if digests(base) != digests(candidate) or any(digest is None for _, digest in digests(base).values()):
        problems.append('model digests')
    if base.get('settings') != candidate.get('settings'):
        problems.append('settings')
    for name, summary in (('baseline', base_summary), ('candidate', candidate_summary)):
        if not summary.get('completed'):
            problems.append('an unfinished run (%s)' % name)
        if summary.get('harness_changed') or summary.get('source_changed'):
            problems.append('files changed during the %s run' % name)
        if summary.get('tripwire_violations'):
            problems.append('tripwire violations in the %s run' % name)
        if (summary.get('overall') or {}).get('statuses', {}).get('error'):
            problems.append('error rows in the %s run' % name)
    if problems:
        return 2, {'error': 'These runs are not matched and cannot be compared: '+', '.join(problems)}

    def row(name, a, b):
        return {'scope': name, 'planned': a['planned'], **{status: [a['statuses'][status], b['statuses'][status]] for status in STATUSES},
                'pass_delta': b['statuses']['pass']-a['statuses']['pass'],
                **{key: [a.get(key), b.get(key)] for key in ('false_success', 'missed_success', 'unauthorized_actions', 'unreviewed_changes', 'runtime_exceptions',
                                                              'model_failures', 'unsupported_native_calls', 'model_calls_per_step')},
                'false_success_delta': (b.get('false_success') or 0)-(a.get('false_success') or 0)}
    report = {'baseline': str(first), 'candidate': str(second), 'split': base['split'], 'source_changed': base.get('source_files') != candidate.get('source_files'),
              'rows': [row('overall', base_summary['overall'], candidate_summary['overall'])]+[
                  row(family, base_summary['families'][family], candidate_summary['families'][family]) for family in sorted(base_summary['families'])]}
    if base_summary.get('families_merged'):
        report['rows'].append(row('small families together', base_summary['families_merged'], candidate_summary['families_merged']))
    loads = (base_summary.get('load') or {}), (candidate_summary.get('load') or {})
    speeds = [load.get('output_tokens_per_second_p50') for load in loads]
    why = []
    if any(load.get('contended') for load in loads):
        why.append('a run was contended (queueing or model loading)')
    if not all(speeds) or abs(speeds[0]-speeds[1])/max(speeds) > SPEED_TOLERANCE:
        why.append('generation speed differs between the runs (%s vs %s tokens/s): different load' % tuple(speeds))
    if why:
        report['latency'] = {'compared': False, 'why': '; '.join(why)}
    else:
        pick = lambda summary, group, key: summary['overall']['timing'][group][key]
        report['latency'] = {'compared': True, **{group+'_'+key: [pick(base_summary, group, key), pick(candidate_summary, group, key)]
                                                  for group in ('passed', 'not_passed') for key in ('seconds_per_task', 'seconds_per_step', 'model_seconds_per_task')}}
    if base['split'] == 'dev':   # per-task detail only for the development set
        def statuses(folder, manifest):
            result = {}
            for line in (Path(folder)/manifest['artifacts']['tasks']).read_text().splitlines():
                item = json.loads(line)
                result[(item['id'], item['run'])] = item['status']
            return result
        before, after = statuses(first, base), statuses(second, candidate)
        report['changed_tasks'] = [{'id': key[0], 'run': key[1], 'baseline': before[key], 'candidate': after.get(key)} for key in sorted(before) if before[key] != after.get(key)]
    return 0, report


def select(split, only):
    chosen = sim_cases.ids(split)
    if only:
        wanted = [item.strip() for item in only.split(',') if item.strip()]
        entries = {entry['id']: entry for entry in sim_cases.CASES}
        chosen = [case_id for case_id in chosen if any(token == case_id or token == entries[case_id]['family'] or token in case_id for token in wanted)]
    return chosen


def host_facts():
    """Hardware and system identity without starting a process (platform.platform() runs uname and file)."""
    import platform
    uname = os.uname()
    try:
        memory = round(os.sysconf('SC_PHYS_PAGES')*os.sysconf('SC_PAGE_SIZE')/2**30, 1)
    except (ValueError, OSError):
        memory = None
    return {'system': uname.sysname, 'release': uname.release, 'machine': uname.machine, 'macos': platform.mac_ver()[0], 'cpus': os.cpu_count(),
            'memory_gb': memory, 'python': platform.python_version()}


def main(argv=None, *, _provider=None, _digests=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--split', choices=('dev', 'acceptance'), default='dev')
    parser.add_argument('--only', help='comma-separated task ids, id fragments or family names')
    parser.add_argument('--runs', type=int, default=1)
    parser.add_argument('--seed', type=int, default=71053)
    parser.add_argument('--model', default='qwen3:4b-instruct')
    parser.add_argument('--vision-model', default='qwen3-vl:4b-instruct')
    parser.add_argument('--source-root', type=Path, default=ROOT, help='the checkout whose companion package is evaluated')
    parser.add_argument('--entry', choices=('prompt', 'interpreter'), default='prompt',
                        help='how a task starts: prompt = the companion’s computer prompt (the only entry this harness covers)')
    parser.add_argument('--max-steps', type=int, default=24, help='ComputerTask action budget (the app uses 24)')
    parser.add_argument('--task-timeout', type=float, default=600, help='wall-clock limit per task, seconds')
    parser.add_argument('--output', type=Path, help='new folder for manifest.json, summary.json and the per-task records')
    parser.add_argument('--frames', action='store_true', help='also save each step’s rendered frame (fixture pixels only)')
    parser.add_argument('--no-warm', action='store_true', help='skip the uncounted model warm-up')
    parser.add_argument('--list', action='store_true')
    parser.add_argument('--compare', nargs=2, metavar=('BASELINE', 'CANDIDATE'), type=Path)
    args = parser.parse_args(argv)
    if args.compare:
        code, report = compare(*args.compare)
        print(json.dumps(report, indent=2))
        return code
    chosen = select(args.split, args.only)
    if args.list:
        for case_id in chosen:
            entry = next(item for item in sim_cases.CASES if item['id'] == case_id)
            line = '%s\t%s\t%s' % (case_id, entry['family'], entry.get('expect', 'complete'))
            if args.split == 'dev':   # acceptance wording stays out of terminals and logs
                line += '\t'+sim_cases.build(case_id, args.seed).request
            print(line)
        return 0
    if args.output is None:
        parser.error('--output is required')
    if args.entry != 'prompt':
        parser.error('--entry interpreter is not covered: the interpreter can route a request to app readers, files and web lookups this simulation '
                     'does not model. Use scripts/request_eval.py for the interpreter and --entry prompt here.')
    if args.runs < 1 or args.max_steps < 1 or not chosen:
        parser.error('nothing to run: check --runs, --max-steps and --only')
    if sys.platform != 'darwin':
        parser.error('the simulated desktop drives the macOS adapters; run it on macOS')
    if args.output.exists():
        parser.error('--output must be a folder that does not exist yet; a finished run is never overwritten')
    source = args.source_root.resolve()
    quiet = args.split == 'acceptance'
    say = lambda *values: print(*values, file=sys.__stdout__, flush=True)

    # Everything that can still refuse the run happens before anything is created or armed.
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    os.environ.update(A11Y_AI_PROVIDER='ollama', A11Y_OLLAMA_URL=OLLAMA, A11Y_AI_MODEL=args.model, A11Y_VISION_MODEL=args.vision_model,
                      A11Y_ROUTES='0', A11Y_WEB_ANSWERS='0')
    for name in CLEARED:
        os.environ.pop(name, None)
    import tempfile
    import uuid
    host = host_facts()
    digests = _digests if _digests is not None else model_digests([args.model, args.vision_model])
    if any(digest is None for digest in digests.values()):
        parser.error('a requested model is not installed in local Ollama (or Ollama is not running): '+', '.join(name for name, digest in digests.items() if digest is None))
    try:
        server_version = ollama_get('/api/version').get('version') if _digests is None else 'not asked'
    except (OSError, ValueError):
        server_version = None
    sys.path.insert(0, str(source))
    from PyQt6.QtCore import PYQT_VERSION_STR
    from PyQt6.QtGui import QGuiApplication
    sim_world.qt()
    if QGuiApplication.platformName() != 'offscreen':
        parser.error('Qt is not offscreen; refusing to run')
    state = Path(tempfile.mkdtemp(prefix='a11y-sim-'))
    os.environ['A11Y_DATA_DIR'] = str(state/'startup')
    import companion
    if Path(companion.__file__).resolve().parent.parent != source:
        import shutil
        shutil.rmtree(state, ignore_errors=True)
        parser.error('companion was not imported from --source-root')
    from companion.desktop import brief
    from companion.ollama import OllamaProvider, provider_from_env
    from companion.gui_models import selection
    small = ()
    if quiet:
        tasks_per_family = {}
        for case_id in chosen:
            family = next(item['family'] for item in sim_cases.CASES if item['id'] == case_id)
            tasks_per_family[family] = tasks_per_family.get(family, 0)+1
        small = tuple(family for family, count in tasks_per_family.items() if count < SMALL_FAMILY)
    rows, completed, aborted, summary = [], False, None, None
    planned = [(case_id, next(item['family'] for item in sim_cases.CASES if item['id'] == case_id)) for _ in range(args.runs) for case_id in chosen]
    detail = Path('detail') if quiet else Path('.')
    real_stdout, log = sys.stdout, None
    # From here on the process may only read/write its own folders and talk to loopback Ollama.
    sim_world.GUARD.arm(ollama_url=OLLAMA, forbidden=sim_world.private_folders(source, ROOT))
    try:
        provider = _provider if _provider is not None else provider_from_env()
        if not isinstance(provider, OllamaProvider) or (_provider is None and (type(provider) is not OllamaProvider or provider.config.base_url != OLLAMA)):
            raise SystemExit('the evaluation provider must be local Ollama')
        args.output.mkdir(parents=True)
        (args.output/detail).mkdir(exist_ok=True)
        if quiet:
            (args.output/detail/'README.txt').write_text(DETAIL_NOTE)
        files = source_files(source)
        manifest = {'protocol': sim_world.PROTOCOL, 'run_id': str(uuid.uuid4()), 'split': args.split, 'seed': args.seed, 'runs': args.runs, 'task_ids': chosen,
                    'planned': len(planned), 'entry': ENTRY,
                    'content': 'each task-run builds its world from sha256(seed|task id|run index): repeats use fresh names and facts',
                    'case_hash': sim_cases.case_hash(), 'harness_hash': harness_hash(), 'harness_files': {name: sha256(HERE/name) for name in HARNESS_FILES},
                    'source_root': str(source), 'git_head': git_head(source), 'source_files': files,
                    'source_identity': hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest(),
                    'models': {'text': {'name': provider.config.model, 'digest': digests.get(args.model)},
                               'vision': {'name': provider.config.vision_model, 'digest': digests.get(args.vision_model)}},
                    'model_selection': {'how': 'companion.ollama.provider_from_env with A11Y_AI_PROVIDER=ollama, A11Y_AI_MODEL and A11Y_VISION_MODEL set and no saved '
                                               'ai.json; text calls use the text model, image calls the vision model', 'gui_profile': selection()[0], 'endpoint': OLLAMA,
                                        'ollama_version': server_version},
                    'settings': {'max_steps': args.max_steps, 'task_timeout_seconds': args.task_timeout, 'step_pause_seconds': 0.45, 'warm_up': not args.no_warm,
                                 'stop_bound_ms': sim_cases.STOP_BOUND_MS, 'inference_environment': {name: os.environ.get(name) for name in INHERITED},
                                 'request_options': 'temperature 0, seed 7 (companion/ollama.py)'},
                    'host': dict(host, pyqt=PYQT_VERSION_STR), 'started_utc': datetime.now(timezone.utc).isoformat(), 'command': [sys.executable]+sys.argv,
                    'condition': 'Models may be loaded or swapped by Ollama during the run; load time is in each call record. Other jobs may share the GPU.',
                    'not_covered': ['interpreter entry (goal_mode, opening and looking_at from service.py)', 'follow-up turns that refer to an earlier request'],
                    'deviations': list(sim_world.DEVIATIONS),
                    'artifacts': {'tasks': str(detail/'tasks.jsonl'), 'summary': 'summary.json', 'runtime_log': str(detail/'runtime.log'),
                                  **({'frames': str(detail/'frames')} if args.frames else {})}}
        (args.output/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
        frames = None
        if args.frames:
            frames = args.output/detail/'frames'
            frames.mkdir()
        log = (args.output/detail/'runtime.log').open('w')   # the runtime's own content-free timing lines
        sys.stdout = log
        if not args.no_warm:
            from companion.computer_task import SYSTEM
            import base64
            warm = time.monotonic()
            provider.warm_up([SYSTEM])
            try:
                provider.complete(SYSTEM, [{'role': 'user', 'content': '{}'}], image_data='data:image/png;base64,'+base64.b64encode(
                    sim_world.png(sim_world.paint([], '', 'Finder', (), window=False))).decode(), response_schema={'type': 'object'}, max_tokens=1, thinking=False)
            except RuntimeError:
                pass   # a one-token reply is cut off by design
            say('warm-up %.1fs' % (time.monotonic()-warm))
        with (args.output/detail/'tasks.jsonl').open('x') as out:
            for index, (case_id, family) in enumerate(planned):
                run = index//len(chosen)
                scenario = sim_cases.build(case_id, args.seed, run)
                problems = sim_cases.precheck(scenario)
                data_dir = state/('task-%04d' % index)
                data_dir.mkdir()
                if problems:
                    outcome = {'loop_status': 'not_started', 'message': '', 'raw_message': '', 'error': 'invalid starting world: '+'; '.join(problems), 'steps': [],
                               'reviews': {'asked': 0, 'approved': 0, 'declined': 0}, 'wall_seconds': 0}
                else:
                    outcome = run_task(scenario, provider, max_steps=args.max_steps, task_timeout=args.task_timeout, brief=brief, frames=frames, data_dir=data_dir)
                verdict = sim_cases.score(scenario, scenario.world, outcome)
                row = task_record(scenario, outcome, verdict, run)
                row['world'] = scenario.world.state()
                rows.append(row)
                out.write(json.dumps(row, ensure_ascii=False, default=str)+'\n')
                out.flush()
                if quiet:
                    say('task %d/%d finished' % (index+1, len(planned)))
                else:
                    say('%-38s run=%d %-9s steps=%-2d calls=%-2d %6.1fs %s' % (case_id, run, row['status'], row['step_count'], row['model_call_count'],
                                                                             row['wall_seconds'] or 0, '; '.join(row['reasons'])[:110]))
                if sim_world.GUARD.violations:
                    aborted = 'tripwire: '+'; '.join(sim_world.GUARD.violations[:3])
                    break
        completed = aborted is None and len(rows) == len(planned)
    except sim_world.Tripwire as error:
        aborted = str(error)
    except KeyboardInterrupt:
        aborted = 'interrupted'
    finally:
        sys.stdout = real_stdout
        if log is not None:
            log.close()
        sim_world.GUARD.disarm()
        import shutil
        shutil.rmtree(state, ignore_errors=True)   # the run's own temporary data folders
        if args.output.is_dir() and (args.output/'manifest.json').is_file():
            summary = summarize(rows, planned, small)
            summary.update(protocol=sim_world.PROTOCOL, split=args.split, seed=args.seed, completed=completed, aborted=aborted,
                           finished_utc=datetime.now(timezone.utc).isoformat(), tripwire_violations=list(sim_world.GUARD.violations),
                           harness_changed=harness_hash() != manifest['harness_hash'], source_changed=source_files(source) != manifest['source_files'])
            (args.output/'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    if summary is None:
        return 1
    overall = summary['overall']
    report = {'split': args.split, 'completed': completed, 'aborted': aborted, 'planned': overall['planned'], 'attempted': overall['attempted'],
              'statuses': overall['statuses'], 'false_success': overall['false_success'], 'missed_success': overall['missed_success'],
              'unauthorized_actions': overall['unauthorized_actions'], 'unreviewed_changes': overall['unreviewed_changes'],
              'runtime_exceptions': overall['runtime_exceptions'], 'model_failures': overall['model_failures'], 'cancellation': summary['cancellation'],
              'timing': overall['timing'], 'load': {key: summary['load'][key] for key in ('queue_share', 'model_load_share', 'output_tokens_per_second_p50', 'contended')},
              'families': {family: dict(block['statuses'], planned=block['planned'], false_success=block['false_success'],
                                        unauthorized_actions=block['unauthorized_actions']) for family, block in summary['families'].items()},
              'output': str(args.output)}
    if quiet:   # acceptance: totals, and only families large enough not to give single tasks away
        for key in ('cancellation', 'timing'):
            report.pop(key)
    say(json.dumps(report, indent=2))
    if summary['harness_changed'] or summary['source_changed']:
        say('WARNING: source or harness files changed during the run; this run is not a clean measurement.')
    return 0 if completed and not summary['tripwire_violations'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
