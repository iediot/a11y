"""Verify a returned router PEFT adapter and frozen splits without loading a base model."""
import argparse
import hashlib
import json
from pathlib import Path
import re


def verify(run, data, base=None):
    import torch
    from safetensors import safe_open
    run,data=Path(run),Path(data)
    config=json.loads((run/'run_config.json').read_text())
    result=json.loads((run/'run.json').read_text())
    adapter=run/'selected-adapter'
    peft=json.loads((adapter/'adapter_config.json').read_text())
    hashes={name:hashlib.sha256((data/(name+'.jsonl')).read_bytes()).hexdigest() for name in ('train','valid','test')}
    if hashes!=config['data_sha256']:raise ValueError('Frozen dataset hash mismatch')
    if result['interrupted'] or result['global_step']!=100:raise ValueError('Pilot did not complete normally')
    if peft['peft_type']!='LORA' or peft['r']!=8 or peft['lora_alpha']!=16:raise ValueError('Unexpected pilot adapter rank/scaling')
    if any(peft.get(k) for k in ('use_dora','use_rslora','rank_pattern','alpha_pattern','modules_to_save','lora_bias')):
        raise ValueError('Unsupported nonstandard LoRA configuration')
    modules={'q_proj','k_proj','v_proj','o_proj','gate_proj','up_proj','down_proj'}
    if set(peft['target_modules'])!=modules or peft['layers_to_transform']!=list(range(28,36)):
        raise ValueError('Unexpected target modules/layers')
    weight=adapter/'adapter_model.safetensors';pairs={};parameters=0;dtypes=set();nonzero_b=0
    with safe_open(weight,framework='pt',device='cpu') as tensors:
        for key in tensors.keys():
            match=re.fullmatch(r'base_model\.model\.model\.layers\.(\d+)\.(self_attn|mlp)\.(\w+)\.lora_([AB])\.weight',key)
            if not match:raise ValueError('Unexpected adapter tensor '+key)
            layer,kind,module,side=match.groups();tensor=tensors.get_tensor(key)
            if not tensor.is_floating_point():raise ValueError('Non-floating adapter '+key)
            if side=='B' and tensor.count_nonzero().item():nonzero_b+=1
            if not torch.isfinite(tensor).all().item():raise ValueError('Nonfinite adapter '+key)
            if tensor.ndim!=2:raise ValueError('Unexpected tensor dimensions')
            pairs.setdefault((int(layer),module),{})[side]=tuple(tensor.shape)
            parameters+=tensor.numel();dtypes.add(str(tensor.dtype))
    if not nonzero_b:raise ValueError('Adapter contains no learned LoRA delta')
    expected={(layer,module) for layer in range(28,36) for module in modules}
    if set(pairs)!=expected:raise ValueError('Missing or unexpected LoRA projection pairs')
    for key,pair in pairs.items():
        if set(pair)!={'A','B'} or pair['A'][0]!=8 or pair['B'][1]!=8:raise ValueError('Invalid LoRA pair '+str(key))
    if parameters!=3670016:raise ValueError('Unexpected trainable parameter count')
    tokenizer_checked=False
    if base:
        base=Path(base);model=json.loads((base/'config.json').read_text())
        if model['architectures']!=['Qwen3ForCausalLM'] or model['num_hidden_layers']!=36:
            raise ValueError('Wrong base architecture')
        left=json.loads((base/'tokenizer.json').read_text());right=json.loads((adapter/'tokenizer.json').read_text())
        for key in ('model','added_tokens','normalizer','pre_tokenizer','post_processor','decoder'):
            if left.get(key)!=right.get(key):raise ValueError('Base/adapter tokenizer mismatch: '+key)
        if model.get('quantization_config'):raise ValueError('Base must be unquantized HF weights')
        tokenizer_checked=True
    return dict(verified=True,data_sha256=hashes,adapter_sha256=hashlib.sha256(weight.read_bytes()).hexdigest(),
                tensor_count=2*len(pairs),nonzero_b_tensors=nonzero_b,parameters=parameters,dtypes=sorted(dtypes),
                tokenizer_checked=tokenizer_checked,base_verified=bool(base))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run',type=Path,required=True);p.add_argument('--data',type=Path,required=True)
    p.add_argument('--base',type=Path);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();report=verify(a.run,a.data,a.base)
    a.out.parent.mkdir(parents=True,exist_ok=True);a.out.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))


if __name__=='__main__':main()
