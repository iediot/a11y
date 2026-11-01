"""Bounded, selective public AgentNet ZIP sampling. No model loading or training."""
import argparse,binascii,collections,hashlib,json,re,struct,time,urllib.request,zlib
from pathlib import Path
BASE='https://huggingface.co/datasets/xlangai/AgentNet/resolve/d76ee50a63fad81cfdbe576416757d7c2091ed50/'
PART=5368709120
LAST=855815467


def central_entries(data):
    """Decode central records, including ZIP64 member offsets; never extract paths."""
    result={};pos=0
    while pos<len(data):
        h=struct.unpack_from('<4s6H3L5H2L',data,pos)
        if h[0]!=b'PK\x01\x02':raise ValueError('Invalid central directory')
        n,e,c=h[10:13];start=pos+46
        name=data[start:start+n].decode('utf-8' if h[3]&2048 else 'cp437')
        extra=data[start+n:start+n+e];uncompressed,compressed,offset,disk=h[9],h[8],h[16],h[13]
        cursor=0
        while cursor+4<=len(extra):
            tag,length=struct.unpack_from('<HH',extra,cursor);payload=extra[cursor+4:cursor+4+length];cursor+=4+length
            if tag==1:
                j=0;values=[]
                for value,sentinel,fmt,size in [(uncompressed,0xffffffff,'<Q',8),(compressed,0xffffffff,'<Q',8),(offset,0xffffffff,'<Q',8),(disk,0xffff,'<L',4)]:
                    if value==sentinel:value=struct.unpack_from(fmt,payload,j)[0];j+=size
                    values.append(value)
                uncompressed,compressed,offset,disk=values
        record=dict(name=name,method=h[4],flags=h[3],crc=h[7],compressed=compressed,size=uncompressed,offset=offset,disk=disk)
        result.setdefault(Path(name).name,[]).append(record)
        pos=start+n+e+c
    return result


def decode_member(record,blob):
    if record['flags']&1:raise ValueError('Encrypted member')
    if record['size']>16*1024**2:raise ValueError('Image exceeds decompression cap')
    if record['method']==0:out=blob
    elif record['method']==8:
        decoder=zlib.decompressobj(-15);out=decoder.decompress(blob,record['size']+1)
        if not decoder.eof or decoder.unconsumed_tail:raise ValueError('Invalid/oversized compressed member')
    else:raise ValueError('Unsupported compression method')
    if len(out)!=record['size'] or binascii.crc32(out)&0xffffffff!=record['crc']:raise ValueError('Member CRC/size mismatch')
    return out


class Fetch:
    def __init__(self,budget):self.budget=budget;self.bytes=0
    def get(self,name,start,length):
        if length<0 or self.bytes+length>self.budget:raise ValueError('Network budget exceeded')
        req=urllib.request.Request(BASE+name,headers={'Range':f'bytes={start}-{start+length-1}'})
        with urllib.request.urlopen(req,timeout=60) as r:
            if r.status!=206 or not (r.headers.get('Content-Range') or '').startswith(f'bytes {start}-'):raise ValueError('Range not honored')
            data=r.read(length+1)
        self.bytes+=len(data)
        if len(data)!=length:raise ValueError('Wrong range length')
        return data
    def part(self,disk,offset,length):
        chunks=[]
        while length:
            size=LAST if disk==23 else PART
            if not 0<=disk<=23 or not 0<=offset<size:raise ValueError('Invalid archive disk/offset')
            count=min(length,size-offset)
            name='win_mac_images/'+('images.zip' if disk==23 else f'images.z{disk+1:02d}')
            chunks.append(self.get(name,offset,count));length-=count;disk+=1;offset=0
        return b''.join(chunks)
    def image(self,record):
        header=self.part(record['disk'],record['offset'],30)
        h=struct.unpack('<4s5H3L2H',header)
        if h[0]!=b'PK\x03\x04' or h[3]!=record['method']:raise ValueError('Bad local header')
        offset=record['offset']+30+h[-2]+h[-1];disk=record['disk']
        while offset>=(LAST if disk==23 else PART):offset-=LAST if disk==23 else PART;disk+=1
        return decode_member(record,self.part(disk,offset,record['compressed']))


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root',type=Path,required=True);a=parser.parse_args()
    out=a.root/'agentnet-scroll-sample';out.mkdir(exist_ok=False)
    state=dict(status='running',stage='trajectory-sample',started=time.time(),training=False,budget_bytes=160*1024**2)
    def save(): (out/'progress.json').write_text(json.dumps(state,indent=2)+'\n')
    save();fetch=Fetch(state['budget_bytes'])
    try:
        metadata={}
        with (a.root/'agentnet-meta.jsonl').open() as stream:
            for line in stream:
                r=json.loads(line)
                if r.get('system')=='Darwin' and (r.get('action_frequency') or {}).get('scroll',0):metadata[r['task_id']]=r
        # Final range of the pinned JSONL, discarding its first partial line.
        total=1400605632;amount=8*1024**2
        data=fetch.get('agentnet_win_mac_18k.jsonl',total-amount,amount)
        selected=[]
        for line in data.split(b'\n')[1:]:
            if not line.strip():continue
            r=json.loads(line)
            if r.get('task_id') not in metadata or not r.get('task_completed'):continue
            selected.append(r)
            if len(selected)==6:break
        if not selected:raise ValueError('No completed macOS scroll trajectories in bounded source range')
        # Directory location derived from pinned archive ZIP64 footer.
        state.update(stage='archive-index',selected_trajectories=len(selected));save()
        tail=fetch.part(23,LAST-65557,65557);idx=tail.rfind(b'PK\x06\x06')
        if idx<0:raise ValueError('Missing ZIP64 footer')
        fields=struct.unpack_from('<4sQ2H2L4Q',tail,idx)
        cd_disk,cd_size,cd_offset=fields[5],fields[8],fields[9]
        if cd_size>48*1024**2:raise ValueError('Archive index exceeds cap')
        entries=central_entries(fetch.part(cd_disk,cd_offset,cd_size))
        state.update(stage='selective-images');save();examples=[];images={}
        for trajectory in selected:
            steps=trajectory.get('traj') or [];found=0
            for i,step in enumerate(steps):
                value=step.get('value') or {};code=value.get('code','')
                if not re.search(r'\b(?:scroll|hscroll)\s*\(',code):continue
                if value.get('last_step_correct') is False:continue
                wanted=steps[max(0,i-1):min(len(steps),i+2)]
                paths=[]
                for observation in wanted:
                    image=Path(observation['image']).name
                    if image not in images:
                        matches=entries.get(image,[])
                        if len(matches)!=1:raise ValueError('Missing/ambiguous image '+image)
                        blob=fetch.image(matches[0]);name=hashlib.sha256(blob).hexdigest()+Path(image).suffix
                        (out/name).write_bytes(blob);images[image]=name
                    paths.append(images[image])
                examples.append(dict(task_id=trajectory['task_id'],instruction=trajectory['instruction'],step_index=i,
                    action_code=code,images=paths,metadata=metadata[trajectory['task_id']],
                    history=[(s.get('value') or {}).get('code') for s in steps[max(0,i-3):i]],
                    note='Human trajectory for inspection; next frame may include other state changes. Code is data and never executed.'))
                found+=1
                if found==2:break
        if not examples:raise ValueError('No valid scroll steps in selected trajectories')
        (out/'examples.json').write_text(json.dumps(examples,indent=2)+'\n')
        state.update(status='finished',stage='complete',downloaded_bytes=fetch.bytes,trajectories=len(selected),scroll_examples=len(examples),images=len(images),ended=time.time());save()
    except BaseException as error:
        state.update(status='failed',error=str(error),downloaded_bytes=fetch.bytes,ended=time.time());save();raise


if __name__=='__main__':main()
