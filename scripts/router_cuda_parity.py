"""Numerical check only: random tiny Qwen, no weights download or optimizer steps."""
import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from router_train_cuda import selected_answer_loss


def check(device='cpu', with_peft=False):
    import torch
    import transformers
    from transformers import Qwen3Config, Qwen3ForCausalLM
    torch.set_num_threads(1)
    torch.manual_seed(7)
    config = Qwen3Config(vocab_size=97, hidden_size=32, intermediate_size=64,
                         num_hidden_layers=2, num_attention_heads=4, num_key_value_heads=2,
                         head_dim=8, attention_dropout=0., pad_token_id=2, eos_token_id=2)
    config._attn_implementation = 'eager'
    model = Qwen3ForCausalLM(config).to(device).eval()
    if with_peft:
        from peft import LoraConfig, get_peft_model
        model=get_peft_model(model,LoraConfig(r=2,lora_alpha=4,lora_dropout=0.,
            target_modules=['q_proj','k_proj','v_proj','o_proj','gate_proj','up_proj','down_proj'],
            task_type='CAUSAL_LM'))
        with torch.no_grad():
            for parameter in model.parameters():
                if parameter.requires_grad:parameter.normal_(0.,.03)
        model.eval()
    # EOS doubles as PAD; only labels and attention_mask distinguish padding.
    batches = [
        dict(input_ids=[[5,6,7,8,2]], attention_mask=[[1,1,1,1,1]], labels=[[-100,-100,7,8,2]]),
        dict(input_ids=[[5,6,7,2,2,2],[8,9,10,11,12,2]],
             attention_mask=[[1,1,1,1,0,0],[1,1,1,1,1,1]],
             labels=[[-100,-100,7,2,-100,-100],[-100,9,10,11,12,2]]),
        dict(input_ids=[[4,2]], attention_mask=[[1,1]], labels=[[-100,2]]),
    ]
    cases=[]
    dtypes=(torch.float64, torch.float32)+( (torch.bfloat16,) if device=='cuda' else ())
    for dtype in dtypes:
        atol,rtol=(1e-4,1e-2) if dtype==torch.bfloat16 else (2e-6,2e-5)
        for name, data in [(f'batch-{i}', [b]) for i,b in enumerate(batches)] + [('accumulation-unequal-tokens', batches)]:
            reference=deepcopy(model).to(dtype=dtype)
            selected=deepcopy(model).to(dtype=dtype)
            ref_losses=[];opt_losses=[];native_losses=[]
            for batch in data:
                inputs={k:torch.tensor(v,device=device,dtype=torch.long) for k,v in batch.items()}
                output=reference(**inputs)
                reference_loss=torch.nn.functional.cross_entropy(
                    output.logits[:,:-1].float().reshape(-1,config.vocab_size),
                    inputs['labels'][:,1:].reshape(-1),ignore_index=-100)
                optimized_loss, _=selected_answer_loss(selected,inputs)
                # Match Trainer's microbatch-mean accumulation, not global token mean.
                (reference_loss/len(data)).backward()
                (optimized_loss/len(data)).backward()
                torch.testing.assert_close(reference_loss,output.loss,atol=atol,rtol=rtol)
                torch.testing.assert_close(optimized_loss,reference_loss,atol=atol,rtol=rtol)
                ref_losses.append(reference_loss.item());opt_losses.append(optimized_loss.item());native_losses.append(output.loss.item())
            maximum=0.;checked=0
            for (key,r),(other,s) in zip(reference.named_parameters(),selected.named_parameters()):
                assert key==other
                assert (r.grad is None)==(s.grad is None),key
                if r.grad is not None:
                    torch.testing.assert_close(s.grad,r.grad,atol=atol,rtol=rtol,msg=key)
                    maximum=max(maximum,(s.grad-r.grad).abs().max().item());checked+=1
            assert checked>0
            cases.append(dict(name=name,dtype=str(dtype),atol=atol,rtol=rtol,reference_losses=ref_losses,
                              selected_losses=opt_losses,native_losses=native_losses,
                              gradient_tensors_checked=checked,max_absolute_gradient_difference=maximum))
    return dict(passed=True,device=device,torch=torch.__version__,transformers=transformers.__version__,
                source_sha256=hashlib.sha256(Path(__file__).with_name('router_train_cuda.py').read_bytes()).hexdigest(),
                peft_tested=with_peft,cases=cases,
                limitations='Tiny unquantized random model, dropout off. No NF4/full-model memory measurement or training quality claim; PEFT only when requested.')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device',choices=('cpu','cuda'),default='cpu')
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--peft',action='store_true',help='also exercise real PEFT LoRA gradients')
    args=parser.parse_args()
    from training_priority import require_background
    require_background()
    report=check(args.device,args.peft)
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='cases'}))


if __name__=='__main__':main()
