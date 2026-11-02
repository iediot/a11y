"""Local pixels-only next-choice diagnostic, never executes actions or trains."""
import argparse,base64,hashlib,json,os,time
from pathlib import Path
from urllib.parse import urlparse
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from companion.computer_task import SYSTEM,schema_for
from companion.ollama import OllamaProvider
from companion.provider import AIConfig


def score(reply,row):
    if not isinstance(reply,dict):return False
    action=reply.get('action') or {}
    if not isinstance(action,dict):return False
    kind=action.get('type')
    if reply.get('status')!='action' or reply.get('risk')!='navigation':return False
    if kind=='click_point' and (action.get('button','left')!='left' or action.get('click_count',1)!=1):return False
    if row['expected']=='visible':
        if kind!='click_point':return False
        x,y=action.get('x'),action.get('y');l,t,r,b=row['box']
        return type(x) in (int,float) and type(y) in (int,float) and l<=x<r and t<=y<b
    if row['expected']=='back':
        if kind=='press_key':return action.get('key')=='escape'
        if kind!='click_point':return False
        x,y=action.get('x'),action.get('y')
        return type(x) in (int,float) and type(y) in (int,float) and 0<=x<.07 and .21<=y<.31
    if row['expected'] in ('reveal','recover'):
        if kind=='press_key':return action.get('key')=='cmd_f'
        if kind=='search_in_app':return matches_target(action.get('query'),row)
        if row['expected']=='recover':return False
        if kind=='scroll':return action.get('direction')=='down'
        if kind=='scroll_to':return matches_target(action.get('text'),row)
        if kind=='browse_url' and row.get('support_urls'):
            if not isinstance(action.get('url'),str):return False
            try:
                url=urlparse(action['url']);host=url.hostname
            except ValueError:return False
            return not url.username and not url.password and url.scheme in ('http','https') and (host=='support.apple.com' or host in ('apple.com','www.apple.com') and url.path.rstrip('/')=='/support')
    return False


def matches_target(value,row):
    target=row.get('target')
    return (isinstance(value,str) and isinstance(target,str) and bool(target.strip())
            and value.strip().casefold()==target.strip().casefold())


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--folder',type=Path,required=True);a=p.parse_args()
    from scripts.training_priority import require_background
    require_background()
    folder=a.folder;rows=json.loads((folder/'cases.json').read_text());candidate=(folder/'candidate-policy.txt').read_text()
    if (folder/'progress.json').exists():raise RuntimeError('Existing run: use a new folder instead of overwriting results.')
    if os.environ.get('A11Y_OLLAMA_CONTEXT')!='8192':raise RuntimeError('This comparison requires A11Y_OLLAMA_CONTEXT=8192.')
    for row in rows:
        if hashlib.sha256(Path(row['image']).read_bytes()).hexdigest()!=row['image_sha256']:raise RuntimeError('Image hash mismatch: '+row['id'])
    provider=OllamaProvider(AIConfig(model='qwen3:4b-instruct',vision_model='qwen3-vl:4b-instruct',timeout=120))
    state=dict(status='running',started=time.time(),training=False,screen_actions=False,planned_calls=len(rows)*6,completed_calls=0)
    def progress():(folder/'progress.json').write_text(json.dumps(state,indent=2)+'\n')
    progress();schema=schema_for('macOS',[],[])
    (folder/'baseline-policy.txt').write_text(SYSTEM)
    (folder/'schema.json').write_text(json.dumps(schema,indent=2)+'\n')
    report=dict(kind='pixels-only next-action agreement diagnostic',context=8192,temperature=0,seed=7,model='qwen3-vl:4b-instruct',
                cases_sha256=hashlib.sha256((folder/'cases.json').read_bytes()).hexdigest(),schema_sha256=hashlib.sha256(json.dumps(schema,sort_keys=True).encode()).hexdigest(),
                policies=dict(baseline_sha256=hashlib.sha256(SYSTEM.encode()).hexdigest(),candidate_sha256=hashlib.sha256(candidate.encode()).hexdigest()),
                limitations='Cropped saved owned windows, reconstructed minimal observations, no native targets/AX, nine development cases on three images. Predeclared admissible-action agreement is NOT exhaustive correctness or end-to-end success. Repeats are not independent scenes. No adoption.',records=[])
    try:
        report['runtime']=provider.request('/api/version')
        report['model_metadata']=provider.model_info('qwen3-vl:4b-instruct')
        report['model_manifest']=[m for m in provider.request('/api/tags').get('models',[]) if m.get('name')=='qwen3-vl:4b-instruct']
        consecutive_errors=0
        for repeat in range(3):
            for row in rows:
                for label,system in ([('baseline',SYSTEM),('candidate',candidate)] if repeat%2==0 else [('candidate',candidate),('baseline',SYSTEM)]):
                    if (folder/'STOP').exists():raise RuntimeError('Stop requested')
                    if time.time()-state['started']>1800:raise RuntimeError('Thirty-minute batch limit reached')
                    path=Path(row['image']);blob=path.read_bytes();image='data:image/jpeg;base64,'+base64.b64encode(blob).decode()
                    messages=[dict(role='user',content=json.dumps(dict(goal=row['goal'],goal_mode='inspect',installed_apps=['Safari','Finder'],authorization='Navigation and reading toward the goal are authorized.'))),
                              dict(role='user',content=json.dumps(dict(desktop=dict(app=row['app'],window=row['window']),actions_taken=row.get('history',[]),visual_context=dict(observation='The current screenshot is attached. It is a crop of the observed app window; use image-relative fractional coordinates. No accessibility IDs are supplied.'))))]
                    start=time.monotonic();record=dict(case=row['id'],policy=label,repeat=repeat+1,image_sha256=hashlib.sha256(blob).hexdigest())
                    try:
                        reply=provider.complete_structured(system,messages,schema=schema,image_data=image,max_tokens=500,thinking=False)
                        record.update(reply=reply,agreement=bool(score(reply,row)))
                        consecutive_errors=0
                    except Exception as error:
                        record.update(error=type(error).__name__+': '+str(error)[:300],agreement=False)
                        consecutive_errors+=1
                    record['seconds']=time.monotonic()-start;report['records'].append(record)
                    state['completed_calls']+=1;progress()
                    (folder/'results.json').write_text(json.dumps(report,indent=2)+'\n')
                    print(label,row['id'],record['agreement'],round(record['seconds'],2),flush=True)
                    if consecutive_errors>=3:raise RuntimeError('Three consecutive inference errors; stopping')
        report['summary']={name:dict(total=sum(r['policy']==name for r in report['records']),agreement=sum(r['policy']==name and r['agreement'] for r in report['records'])) for name in ('baseline','candidate')}
        (folder/'results.json').write_text(json.dumps(report,indent=2)+'\n');state.update(status='finished',ended=time.time());progress()
    except BaseException as error:state.update(status='stopped' if (folder/'STOP').exists() else 'failed',error=str(error),ended=time.time());progress();raise


if __name__=='__main__':main()
