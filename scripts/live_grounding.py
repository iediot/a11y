"""Fresh loopback canvas scenes: no target labels in AX/OCR, independent pointer-hit truth."""
import json
import random
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
import live_eval as le
from live_tasks import Task, no_failure


def scene(seed, index):
    rng=random.Random(seed*101+index)
    names=['compass','magnifying glass','gear']
    positions=[(140,180),(360,270),(580,170)]
    rng.shuffle(positions)
    return [dict(name=n,x=x+rng.randint(-25,25),y=y+rng.randint(-20,20),radius=48) for n,(x,y) in zip(names,positions)]


def html(items, token):
    # Fixed test icon artwork, never a production interpretation rule.
    return '''<!doctype html><meta charset="utf-8"><title>A11y test icon panel</title>
<style>body{background:#f3f4f6;color:#222;font:20px sans-serif;margin:28px}canvas{background:white;width:min(720px,95vw);height:auto;border:1px solid #ddd}</style>
<h1>A11y test icon panel</h1><p>Choose an icon.</p><canvas width="720" height="420" aria-hidden="true"></canvas><p id="result">No selection.</p>
<script>
const items=ITEMS, token=TOKEN, canvas=document.querySelector('canvas'),ctx=canvas.getContext('2d');
function paint(selected){ctx.clearRect(0,0,720,420);for(const item of items){const {name,x,y}=item;ctx.save();ctx.translate(x,y);ctx.strokeStyle='#183654';ctx.fillStyle='#183654';ctx.lineWidth=6;
if(name==='compass'){ctx.beginPath();ctx.arc(0,0,37,0,Math.PI*2);ctx.stroke();ctx.beginPath();ctx.moveTo(0,-29);ctx.lineTo(13,17);ctx.lineTo(0,7);ctx.lineTo(-13,17);ctx.closePath();ctx.fill();}
if(name==='magnifying glass'){ctx.beginPath();ctx.arc(-8,-8,24,0,Math.PI*2);ctx.stroke();ctx.beginPath();ctx.moveTo(10,10);ctx.lineTo(36,36);ctx.stroke();}
if(name==='gear'){for(let i=0;i<8;i++){ctx.save();ctx.rotate(i*Math.PI/4);ctx.fillRect(-7,-41,14,20);ctx.restore();}ctx.beginPath();ctx.arc(0,0,30,0,Math.PI*2);ctx.fill();ctx.fillStyle='white';ctx.beginPath();ctx.arc(0,0,12,0,Math.PI*2);ctx.fill();}
if(name===selected){ctx.strokeStyle='#17a56b';ctx.lineWidth=4;ctx.strokeRect(-48,-48,96,96);}ctx.restore();}}
let selections=0;paint(null);canvas.onclick=e=>{const b=canvas.getBoundingClientRect(),x=(e.clientX-b.left)*720/b.width,y=(e.clientY-b.top)*420/b.height;
const hit=items.find(i=>Math.abs(i.x-x)<i.radius&&Math.abs(i.y-y)<i.radius);
fetch('/hit?token='+token+'&name='+encodeURIComponent(hit?hit.name:'miss')).then(()=>{paint(hit?hit.name:null);document.querySelector('#result').textContent='Selections recorded: '+(++selections);});};
</script>'''.replace('ITEMS',json.dumps(items)).replace('TOKEN',json.dumps(token))


def build(window, count=0, seed=0):
    cases=[]
    for index in range(4):
        state={'items':scene(seed,index),'hits':[]}
        def setup(runner,state=state):
            state['hits']=[];token=secrets.token_hex(12);page=html(state['items'],token).encode()
            class Handler(BaseHTTPRequestHandler):
                def log_message(self,*_):pass
                def do_GET(self):
                    u=urlparse(self.path);query=parse_qs(u.query)
                    if u.path=='/hit':
                        if query.get('token')!=[token]:self.send_error(403);return
                        state['hits'].append(query.get('name',['miss'])[0]);body=b'OK'
                    else:body=page
                    self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
            server=ThreadingHTTPServer(('127.0.0.1',0),Handler);state['server']=server
            threading.Thread(target=server.serve_forever,daemon=True).start()
            window.ensure('http://127.0.0.1:%d/'%server.server_port)
            le.osa('tell application "Safari" to set bounds of window id %d to {80,80,1180,820}'%window.id)
            # Let the owned page reflow before either baseline or candidate observes it.
            import time
            time.sleep(.5)
        def cleanup(state=state):
            window.close()
            if state.get('server'):state['server'].shutdown();state['server'].server_close()
        turns=[]
        for item in state['items']:
            target=item['name']
            def before(runner,state=state):state['before']=len(state['hits'])
            def check(result,events,state=state,target=target):
                hits=state['hits'][state['before']:]
                result.setdefault('_observed',{}).update(expected=target,hits=hits)
                return hits==[target]
            turns.append(Task('canvas_icon','Click the '+target+' icon.', [('independent pointer hit',check),('honest completion',no_failure)],setup=before,wanted_app='Safari',timeout=120))
        cases.append(dict(name='canvas_scene_%d'%index,setup=setup,cleanup=[cleanup],scoped_documents=True,turns=turns))
    return cases
