"""Owner-approved pinned visual-base download + verification, then bounded memory probe."""
import hashlib,json,os,subprocess,time
from pathlib import Path
os.environ.update(HF_HUB_OFFLINE='0',HF_HUB_DISABLE_TELEMETRY='1',HF_HUB_DISABLE_XET='1')
root=Path('/home/edi/a11y-training');out=root/'runs/gui-policy-memory-1';out.mkdir(parents=True,exist_ok=False)
state=dict(status='running',stage='inventory',started=time.time(),training=False,adopted=False,repo='Qwen/Qwen3-VL-4B-Instruct',revision='ebb281ec70b05090aa6165b016eac8ec08e71b17')
def save(): (out/'progress.json').write_text(json.dumps(state,indent=2)+'\n')
save()
try:
 from huggingface_hub import HfApi,snapshot_download
 info=HfApi().model_info(state['repo'],revision=state['revision'],files_metadata=True)
 files=[f for f in info.siblings if f.rfilename.endswith(('.safetensors','.json','.txt','.jinja','.model')) or f.rfilename in ('README.md','LICENSE')]
 total=sum(f.size or 0 for f in files)
 if total>10*1024**3:raise RuntimeError('Approved download size cap exceeded')
 state.update(stage='download',expected_bytes=total);save()
 model=root/'models/Qwen3-VL-4B-Instruct'
 snapshot_download(state['repo'],revision=state['revision'],local_dir=model,allow_patterns=[f.rfilename for f in files],max_workers=2)
 state.update(stage='verify');save();manifest=[]
 for f in files:
  p=model/f.rfilename
  if not p.is_file() or p.stat().st_size!=f.size:raise RuntimeError('Size mismatch '+f.rfilename)
  with p.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
  lfs=f.lfs
  expected=getattr(lfs,'sha256',None) if lfs else None
  if expected and digest!=expected:raise RuntimeError('SHA mismatch '+f.rfilename)
  manifest.append(dict(file=f.rfilename,size=f.size,sha256=digest,lfs_verified=bool(expected)))
 (out/'download-manifest.json').write_text(json.dumps(dict(repo=state['repo'],revision=state['revision'],files=manifest),indent=2)+'\n')
 state.update(stage='memory-check');save()
 env=dict(os.environ,HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1')
 with (out/'memory.log').open('w') as log:
  subprocess.run([str(root/'.venv-vision/bin/python'),str(root/'qualification-pilot-1/scripts/gui_policy_memory.py'),'--model',str(model),'--image',str(root/'data/gui-choice-inspection-1/samples/0.image'),'--out',str(out/'memory.json')],env=env,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=600)
 state.update(status='finished',stage='complete',ended=time.time());save()
except BaseException as error:
 state.update(status='failed',error=str(error),ended=time.time());save();raise
