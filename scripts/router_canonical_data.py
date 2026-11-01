"""Prepare a separate router training snapshot in observed Ollama output order.

Required fields precede optional ones, each sorted, as in Ollama 0.34's JSON
schema output. Values and prompt messages are preserved. Test bytes are copied
unchanged; this does not add examples or modify the original dataset.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from companion.request_plan import INTENT_SCHEMA


def canonical_answer(value):
    if not isinstance(value,dict):
        raise ValueError('Assistant answer must be a JSON object')
    required=set(INTENT_SCHEMA['required']) if 'operation' in value else set()
    keys=sorted(required & value.keys())+sorted(value.keys()-required)
    return json.dumps({key:value[key] for key in keys},ensure_ascii=False)


def prepare(source,destination):
    source,destination=Path(source).resolve(),Path(destination).resolve()
    if source==destination or source in destination.parents:
        raise ValueError('Use a separate snapshot outside the source data directory')
    for name in ('train.jsonl','valid.jsonl','test.jsonl'):
        if not (source/name).is_file():raise ValueError('Missing '+name)
    destination.mkdir(parents=True,exist_ok=False)
    report=dict(format='ollama-0.34-required-then-optional-sorted',sources={},rows={})
    for split in ('train','valid','test'):
        src=source/(split+'.jsonl');out=destination/src.name
        report['sources'][split]=hashlib.sha256(src.read_bytes()).hexdigest()
        if split=='test':
            shutil.copyfile(src,out)
            continue
        rows=[]
        for line in src.read_text(encoding='utf-8').splitlines():
            if not line.strip():continue
            row=json.loads(line)
            if not row.get('messages') or row['messages'][-1].get('role')!='assistant':
                raise ValueError('Training row must end in an assistant answer')
            answer=json.loads(row['messages'][-1]['content'])
            row['messages'][-1]['content']=canonical_answer(answer)
            rows.append(json.dumps(row,ensure_ascii=False))
        out.write_text('\n'.join(rows)+'\n', encoding='utf-8');report['rows'][split]=len(rows)
    (destination/'format.json').write_text(json.dumps(report,indent=2)+'\n')
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path);parser.add_argument('destination',type=Path)
    args=parser.parse_args();print(json.dumps(prepare(args.source,args.destination),indent=2))
