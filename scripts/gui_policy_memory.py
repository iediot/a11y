"""Bounded NF4 visual-LoRA memory check; no optimizer steps, saves or adoption."""
import argparse,gc,json,os,re,time,subprocess
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
from router_train_cuda import selected_answer_loss


def read_workload(path):
    """Load bounded text only; screenshots remain a separate explicit local input."""
    raw=Path(path).read_bytes()
    if len(raw)>100_000:raise ValueError('Memory workload exceeds 100 KB')
    value=json.loads(raw)
    if not isinstance(value,dict) or set(value)!={'system','user','answer'}:
        raise ValueError('Memory workload requires only system, user and answer text')
    if any(not isinstance(v,str) or not v.strip() or len(v)>40_000 for v in value.values()):
        raise ValueError('Memory workload fields must be nonempty bounded strings')
    return value


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--model',type=Path,required=True);p.add_argument('--image',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--workload',type=Path,help='Bounded JSON system/user/answer text for a full-prompt capacity check')
    p.add_argument('--allocate-adam-state',action='store_true',help='Allocate AdamW state buffers, without calling step or updating weights')
    p.add_argument('--bf16-autocast',action='store_true',help='Match the CUDA trainer mixed-precision forward context')
    a=p.parse_args()
    from training_priority import require_background
    require_background()
    workload=read_workload(a.workload) if a.workload else None
    os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1')
    import torch,transformers,peft
    from PIL import Image
    from transformers import AutoProcessor,Qwen3VLForConditionalGeneration,BitsAndBytesConfig
    from peft import LoraConfig,get_peft_model,prepare_model_for_kbit_training
    started=time.time();report=dict(status='running',stage='load',torch=torch.__version__,transformers=transformers.__version__,peft=peft.__version__,training_steps=0,optimizer_allocated=False,bf16_autocast=a.bf16_autocast,workload='provided full prompt' if workload else 'short synthetic prompt',cases=[])
    a.out.parent.mkdir(parents=True,exist_ok=True)
    def save():a.out.write_text(json.dumps(report,indent=2)+'\n')
    save()
    try:
        assert torch.cuda.is_available()
        if a.bf16_autocast and not torch.cuda.is_bf16_supported():raise RuntimeError('CUDA device does not support BF16 autocast')
        probe=subprocess.run(['nvidia-smi','--query-compute-apps=pid','--format=csv,noheader,nounits'],capture_output=True,text=True,check=True)
        if any(int(pid.strip())!=os.getpid() for pid in probe.stdout.splitlines() if pid.strip()):raise RuntimeError('Competing GPU process; inspect before retrying')
        torch.cuda.reset_peak_memory_stats()
        processor=AutoProcessor.from_pretrained(a.model,local_files_only=True,trust_remote_code=False)
        model=Qwen3VLForConditionalGeneration.from_pretrained(a.model,local_files_only=True,trust_remote_code=False,
            device_map={'':0},torch_dtype=torch.bfloat16,attn_implementation='sdpa',
            quantization_config=BitsAndBytesConfig(load_in_4bit=True,bnb_4bit_quant_type='nf4',bnb_4bit_use_double_quant=True,bnb_4bit_compute_dtype=torch.bfloat16,llm_int8_skip_modules=['visual','lm_head']))
        model.config.use_cache=False
        model=prepare_model_for_kbit_training(model,use_gradient_checkpointing=True,gradient_checkpointing_kwargs={'use_reentrant':False})
        count=model.config.text_config.num_hidden_layers
        layers='|'.join(str(i) for i in range(count-8,count))
        target=rf'.*language_model\.layers\.({layers})\.(self_attn|mlp)\.(q_proj|k_proj|v_proj|o_proj|gate_proj|up_proj|down_proj)'
        model=get_peft_model(model,LoraConfig(r=8,lora_alpha=16,lora_dropout=0.,task_type='CAUSAL_LM',target_modules=target))
        names=[n for n,v in model.named_parameters() if v.requires_grad]
        assert names and all('language_model.layers.' in n and 'lora_' in n for n in names)
        report.update(trainable_parameters=sum(v.numel() for v in model.parameters() if v.requires_grad),stage='forward-backward',loaded_allocated_bytes=torch.cuda.memory_allocated(),loaded_reserved_bytes=torch.cuda.memory_reserved(),vision_quantized=any('visual' in n and '4bit' in type(m).__name__.lower() for n,m in model.named_modules()))
        if report['vision_quantized']:raise RuntimeError('Vision was unexpectedly quantized')
        if a.allocate_adam_state:
            optimizer=torch.optim.AdamW([v for v in model.parameters() if v.requires_grad],lr=0.)
            for group in optimizer.param_groups:
                for parameter in group['params']:
                    optimizer.state[parameter].update(step=torch.tensor(0.),
                        exp_avg=torch.zeros_like(parameter),exp_avg_sq=torch.zeros_like(parameter))
            report.update(optimizer_allocated=True,optimizer_state_cuda_bytes=sum(v.numel()*v.element_size()
                for state in optimizer.state.values() for v in state.values() if isinstance(v,torch.Tensor) and v.is_cuda),
                optimizer_steps=0)
        save();model.train()
        source=Image.open(a.image).convert('RGB')
        source=source.crop((0,0,source.width,min(source.height,720)))
        for size in (448,672):
            model.zero_grad(set_to_none=True);gc.collect();torch.cuda.empty_cache();torch.cuda.reset_peak_memory_stats()
            image=source.copy();image.thumbnail((size,size))
            messages=[dict(role='user',content=[dict(type='image'),dict(type='text',text='Choose one safe next action from the visible interface. Do not invent an unseen target. This is a structural memory check, not a real task.')])]
            answer='{"status":"needs_user","message":"Memory check only.","risk":"navigation","action":null}'
            if workload:
                messages=[dict(role='system',content=[dict(type='text',text=workload['system'])]),
                          dict(role='user',content=[dict(type='image'),dict(type='text',text=workload['user'])])]
                answer=workload['answer']
            full=processor.apply_chat_template(messages+[dict(role='assistant',content=answer)],tokenize=False,add_generation_prompt=False)
            prefix=processor.apply_chat_template(messages,tokenize=False,add_generation_prompt=True)
            encoded=processor(text=[full],images=[image],return_tensors='pt')
            prefix_ids=processor(text=[prefix],images=[image],return_tensors='pt')['input_ids']
            length=prefix_ids.shape[1]
            if encoded['input_ids'].shape[1]>8192:raise RuntimeError('Memory workload exceeds the 8192-token capacity-check bound')
            if not torch.equal(encoded['input_ids'][:,:length],prefix_ids):raise RuntimeError('Assistant/image token boundary mismatch')
            labels=encoded['input_ids'].clone();labels[:,:length]=-100
            assert (labels!=-100).sum()>0
            inputs={k:v.to('cuda') for k,v in encoded.items()};inputs['labels']=labels.to('cuda')
            report['active_case']=dict(image_box=size,tokens=inputs['input_ids'].shape[1],supervised_tokens=int((labels!=-100).sum()))
            save();begin=time.monotonic()
            with torch.autocast(device_type='cuda',dtype=torch.bfloat16,enabled=a.bf16_autocast):
                loss,outputs=selected_answer_loss(model,inputs)
            if not torch.isfinite(loss):raise RuntimeError('Nonfinite loss')
            loss.backward();torch.cuda.synchronize()
            gradients=[v.grad for n,v in model.named_parameters() if v.requires_grad]
            if any(g is None or not torch.isfinite(g).all() for g in gradients):raise RuntimeError('Missing/nonfinite LoRA gradient')
            report['cases'].append(dict(image_box=size,actual_image_size=list(image.size),tokens=inputs['input_ids'].shape[1],supervised_tokens=int((labels!=-100).sum()),loss=float(loss.detach()),seconds=time.monotonic()-begin,peak_allocated_bytes=torch.cuda.max_memory_allocated(),peak_reserved_bytes=torch.cuda.max_memory_reserved()))
            save();del loss,outputs,inputs,encoded,labels,gradients
        report.update(status='finished',stage='complete',ended=time.time(),seconds=time.time()-started,
            limitations='Forward/backward at two bounded image sizes. AdamW states allocated only when explicitly requested; no optimizer step/workspace measured, weight updates, convergence or task-quality evidence. Provided text workload is a capacity probe, not training data or an exact Ollama serving replay. No adapter saved or defaults changed.');save()
    except BaseException as error:
        report.update(status='oom' if isinstance(error,torch.cuda.OutOfMemoryError) else 'failed',error=str(error),ended=time.time());save();raise


if __name__=='__main__':main()
