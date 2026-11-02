#!/usr/bin/env python3
"""Local GUI proposal replay and independently checked live-run summaries. Never acts/pulls.

Replay JSONL: {id, scene, image, messages, schema, targets:[], box:[l,t,r,b]}.
messages/schema must be exported from an authorized app-like observation, not built
from the gold box. box uses fractional image coordinates. Images stay local.
"""
import argparse
from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path
import statistics
import sys
import threading
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from companion.gui_models import PROFILES, computer_step, infer, LOCALIZE_PROMPT, fraction
from companion.computer_task import SYSTEM, resolve_target
from companion.ollama import OllamaProvider
from companion.provider import AIConfig, ProviderError


def distribution(values):
    values = sorted(values)
    return None if not values else dict(n=len(values), p50=statistics.median(values),
                                        p95=values[min(len(values)-1, int(.95*len(values)))], max=max(values))


def score(raw, row):
    """Strict, unsnapped proposal hit. Malformed/unsupported/wrong tools remain failures."""
    if not isinstance(raw, dict):
        return {'hit': False, 'click_valid': False}
    raw = resolve_target(raw, row.get('targets', []))
    action = raw.get('action') or {}
    if raw.get('status') != 'action' or not isinstance(action, dict) or action.get('type') != 'click_point':
        return {'hit': False, 'click_valid': False}
    from companion.computer_task import normalize_click
    action = normalize_click(action)
    x,y = action.get('x'), action.get('y')
    import math
    valid = all(type(v) in (int,float) and math.isfinite(v) and 0 <= v < 1 for v in (x,y))
    from companion.models import validate_response, ValidationError
    try:
        if set(raw) != {'status','message','risk','action'} or raw['risk'] not in ('navigation','change'):
            raise ValidationError('Invalid task envelope.')
        validate_response({'request_kind':'answer','message':raw['message'],'desktop_action':action,'target':None},has_screen=True,desktop=object())  # shape-only: no bridge exists and nothing executes
        schema_valid=True
    except (ValidationError, ValueError, KeyError, TypeError):
        schema_valid=False
    valid = valid and schema_valid
    left,top,right,bottom = row['box']
    hit = valid and left <= x < right and top <= y < bottom
    return dict(hit=bool(hit), click_valid=valid, schema_valid=schema_valid, point=[x,y] if valid else None,
                gesture_correct=action.get('button','left') == row.get('button','left')
                and action.get('click_count',1) == row.get('click_count',1))



def localization_proposal(provider, model, target, image_data):
    """Native grid-localizer diagnostic: supplied target, exactly one model call.

    Qwen3-VL and Holo2 both support this grid/JSON recipe. This bypasses policy
    selection only in offline measurement; it never bypasses an execution guard.
    """
    if not isinstance(target, str) or not target.strip() or len(target)>240:
        raise ProviderError('Localization-only rows require an explicit target label.')
    content, _, _ = infer(provider, 'holo2', model, LOCALIZE_PROMPT+'\n'+target, image_data)
    try:
        value=json.loads(content)
        if not isinstance(value,dict) or set(value)!={'x','y'}:
            raise ValueError()
        x,y=fraction(value['x'],1000),fraction(value['y'],1000)
    except (ValueError,TypeError):
        raise ProviderError('Invalid native localization response.') from None
    return {'status':'action','message':'Click the supplied target.','risk':'navigation',
            'action':{'type':'click_point','x':x,'y':y,'label':target}}


def replay_summary(records):
    groups = defaultdict(list)
    for record in records:
        groups[(record['profile'],record['model'])].append(record)
    report = []
    for (profile,model), rows in groups.items():
        repeated = defaultdict(list)
        for r in rows:
            repeated[r['id']].append(r)
        report.append(dict(profile=profile, model=model, attempts=len(rows),
            hit_rate=sum(r.get('hit',False) for r in rows)/len(rows),
            action_hit_rate=sum(r.get('hit',False) and r.get('gesture_correct',False) for r in rows)/len(rows),
            error_rate=sum('error' in r for r in rows)/len(rows),
            click_schema_valid_rate=sum(r.get('schema_valid',False) for r in rows)/len(rows),
            seconds=distribution([r['seconds'] for r in rows]),
            all_repeats_hit_rate=sum(all(r.get('hit') for r in rs) for rs in repeated.values())/len(repeated),
            point_consistency_rate=sum(all(r.get('click_valid') for r in rs) and len({json.dumps(r.get('point')) for r in rs})==1 for rs in repeated.values())/len(repeated),
            scenes=len({r['scene'] for r in rows}),
            sampled_ollama_resident_bytes=max((r['resident_bytes'] for r in rows if type(r.get('resident_bytes')) is int),default=None)))
    return report


class MemorySampler:
    """/api/ps reports resident models, not process RSS or allocator peak. Label accordingly."""
    def __init__(self, provider):
        self.provider=provider
        self.peak=0
        self.models={}
        self.samples=0
        self.stop=threading.Event()
        self.thread=threading.Thread(target=self.run, daemon=True)
    def sample(self):
        try:
            result=self.provider.request('/api/ps', timeout=2)
            self.peak=max(self.peak,sum(m.get('size_vram',0) for m in result.get('models',[])))
            for model in result.get('models',[]):
                name=model.get('name') or model.get('model')
                if isinstance(name,str):
                    self.models[name]=max(self.models.get(name,0),model.get('size_vram',0))
            self.samples+=1
        except ProviderError:
            pass
    def run(self):
        while not self.stop.is_set():
            self.sample()
            self.stop.wait(.25)
    def __enter__(self):
        self.thread.start()
        return self
    def __exit__(self,*_):
        self.stop.set()
        self.thread.join(3)
        self.sample()


def live_summary(directories):
    reports=[]
    for directory in directories:
        scope=json.loads((directory/'scope.json').read_text())
        rows=[json.loads(s) for s in (directory/'runs.jsonl').read_text().splitlines() if s.strip()]
        groups=defaultdict(list)
        conversations=defaultdict(list)
        for r in rows:
            groups[(r.get('case'),r.get('turn'))].append(r)
            conversations[(r.get('case'),r.get('attempt',1))].append(r)
        runs=scope.get('runs',max((r.get('attempt',1) for r in rows),default=1))
        expected=scope.get('case_turns') or {case:max(r.get('turn',0) for r in rows if r.get('case')==case)
                                           for case,_ in conversations}
        complete=len(rows)==scope.get('scheduled_turns',len(rows))*runs
        case_pass={key:all(r.get('passed') is True for r in rs)
                   and {r.get('turn') for r in rs}==set(range(1,expected.get(key[0],0)+1))
                   for key,rs in conversations.items()}
        reports.append(dict(path=str(directory), scope=scope, turns=len(rows), complete=complete,
            turn_success_rate=sum(r.get('passed') is True for r in rows)/max(1,len(rows)),
            task_success_rate=(sum(case_pass.values())/max(1,len(expected)*runs)) if complete else None,
            all_repeats_task_pass_rate=(sum(all(case_pass.get((case,attempt),False) for attempt in range(1,runs+1))
                                             for case in expected)/max(1,len(expected))) if complete and runs>1 else None,
            all_repeats_turn_pass_rate=(sum(all(r.get('passed') is True for r in rs) for rs in groups.values())/max(1,len(groups)))
                                      if complete and runs>1 else None,
            repeats_per_turn=sorted({len(rs) for rs in groups.values()}),
            seconds=distribution([r['seconds'] for r in rows if type(r.get('seconds')) in (float,int)]),
            timed_out=sum(r.get('timed_out') is True for r in rows)))
    return reports


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',type=Path,required=True)
    sub=p.add_subparsers(dest='mode',required=True)
    replay=sub.add_parser('replay')
    replay.add_argument('--data',type=Path,required=True)
    replay.add_argument('--candidate',action='append',required=True,help='PROFILE=installed-ollama-name; repeat for paired comparison')
    replay.add_argument('--text-model',default='qwen3:4b-instruct')
    replay.add_argument('--vision-model',default='qwen3-vl:4b-instruct')
    replay.add_argument('--runs',type=int,default=3)
    replay.add_argument('--localization-only',action='store_true',help='Qwen3-VL/Holo2 native grid grounding; rows require target; no policy call')
    live=sub.add_parser('summarize-live')
    live.add_argument('directories',type=Path,nargs='+')
    args=p.parse_args()
    if args.mode=='summarize-live':
        args.out.write_text(json.dumps(dict(kind='live-task-outcomes',reports=live_summary(args.directories)),indent=2)+'\n')
        return
    if not 1 <= args.runs <= 20:
        p.error('--runs must be 1..20')
    from training_priority import require_background
    require_background()
    provider=OllamaProvider(AIConfig(model=args.text_model,vision_model=args.vision_model,timeout=180))
    tags=provider.request('/api/tags')
    installed={m['name']:m for m in tags.get('models',[])}
    rows=[json.loads(s) for s in args.data.read_text().splitlines() if s.strip()]
    if not rows or len({r['id'] for r in rows}) != len(rows):
        p.error('Dataset needs unique IDs and at least one row')
    if args.localization_only and any(not isinstance(r.get('target'),str) or not r['target'].strip() for r in rows):
        p.error('--localization-only requires an explicit target in every row')
    digest=hashlib.sha256(args.data.read_bytes())
    for r in rows:
        image=(args.data.parent/r['image']).resolve()
        digest.update(image.read_bytes())
        if len(r['box']) != 4 or not 0 <= r['box'][0] < r['box'][2] <= 1 or not 0 <= r['box'][1] < r['box'][3] <= 1:
            p.error('Gold boxes must be fractional image rectangles')
    from companion.instance import build_id
    report=dict(build_id=build_id(),benchmark_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),kind='native-localizer-only' if args.localization_only else 'unsnapped-proposal-grounding', data_sha256=digest.hexdigest(), runtime=provider.request('/api/version'),
                text_model=args.text_model,vision_model=args.vision_model,runs=args.runs,context=int(os.getenv('A11Y_OLLAMA_CONTEXT','16384')),
                memory_metric='sampled /api/ps total size_vram across resident Ollama models; not process RSS/peak',
                records=[], skipped=[], installed=[{k:m.get(k) for k in ('name','digest','size','details')} for m in installed.values()])
    def save():
        report['summary']=replay_summary(report['records'])
        args.out.write_text(json.dumps(report,indent=2)+'\n')
    import base64
    from unittest.mock import patch
    for candidate in args.candidate:
        profile,sep,model=candidate.partition('=')
        if not sep or profile not in PROFILES:
            p.error('Use PROFILE=installed-ollama-name')
        if args.localization_only and profile not in ('qwen3-vl','holo2'):
            p.error('Native localization supports only the verified Qwen3-VL/Holo2 grid recipe')
        name=model if ':' in model else model+':latest'
        if PROFILES[profile].status=='unsupported' or name not in installed:
            report['skipped'].append(dict(profile=profile,model=model,reason='unsupported protocol' if PROFILES[profile].status=='unsupported' else 'not installed'))
            save()
            continue
        with patch.dict(os.environ,dict(A11Y_GUI_PROFILE=profile,A11Y_GUI_MODEL=model)):
            # Qwen's candidate is the vision model, not the separate GUI override.
            active=OllamaProvider(AIConfig(model=args.text_model,vision_model=model if profile=='qwen3-vl' else args.vision_model,timeout=180))
            for repeat in range(args.runs):
                for row in rows:
                    image='data:image/png;base64,'+base64.b64encode((args.data.parent/row['image']).read_bytes()).decode()
                    record=dict(profile=profile,model=model,id=row['id'],scene=row['scene'],repeat=repeat+1)
                    start=time.monotonic()
                    calls=[]
                    def record_timing(event, started, **metrics):
                        if event in ('ollama.finished','gui.inference'):
                            calls.append(dict(event=event, elapsed_ms=round((time.monotonic()-started)*1000), **metrics))
                    with MemorySampler(active) as memory, patch('companion.ollama.timing',record_timing), patch('companion.gui_models.timing',record_timing):
                        try:
                            raw=(localization_proposal(active,model,row['target'],image) if args.localization_only else
                                 computer_step(active,SYSTEM,row['messages'],schema=row['schema'],image_data=image,targets=row.get('targets',[])))
                            record.update(score(raw,row))
                        except (ProviderError,ValueError,KeyError,TypeError) as error:
                            record.update(error=type(error).__name__,hit=False)
                        record['seconds']=time.monotonic()-start
                        record['inference_calls']=calls
                    record.update(resident_bytes=memory.peak if memory.samples else None,resident_models=memory.models,memory_samples=memory.samples)
                    report['records'].append(record)
                    save()
                    print(profile,row['id'],repeat+1,record['hit'],round(record['seconds'],2),flush=True)
    save()


if __name__=='__main__':
    main()
