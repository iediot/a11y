"""Offline CUDA router QLoRA. Local model/data only; no desktop, test-set selection or uploads."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal

os.environ['HF_HUB_OFFLINE']='1'
os.environ['TRANSFORMERS_OFFLINE']='1'
os.environ['HF_HUB_DISABLE_TELEMETRY']='1'
os.environ['WANDB_DISABLED']='true'


def encode_row(row, tokenizer, maximum):
    messages=row['messages']
    if not messages or messages[-1]['role']!='assistant':
        raise ValueError('Training rows must end with an assistant JSON answer')
    json.loads(messages[-1]['content'])
    full=tokenizer.apply_chat_template(messages,tokenize=False,add_generation_prompt=False)
    prefix=tokenizer.apply_chat_template(messages[:-1],tokenize=False,add_generation_prompt=True)
    if full!=prefix+messages[-1]['content']+'<|im_end|>\n':
        raise ValueError('Assistant template mismatch')
    ids=tokenizer.encode(full,add_special_tokens=False)
    start=tokenizer.encode(prefix,add_special_tokens=False)
    if ids[:len(start)]!=start or len(ids)<=len(start):
        raise ValueError('Assistant token boundary mismatch')
    if len(ids)>maximum:
        raise ValueError(f'Row has {len(ids)} tokens; max is {maximum}. Refusing to truncate JSON/EOS.')
    return dict(input_ids=ids,attention_mask=[1]*len(ids),labels=[-100]*len(start)+ids[len(start):])


def pad_rows(rows, pad):
    width=max(len(row['input_ids']) for row in rows)
    result={key:[] for key in ('input_ids','attention_mask','labels')}
    for row in rows:
        extra=width-len(row['input_ids'])
        for key,value in (('input_ids',pad),('attention_mask',0),('labels',-100)):
            result[key].append(row[key]+[value]*extra)
    return result



def supervised_positions(labels):
    """Positions predicting supervised NEXT tokens, shared across padded batch rows."""
    if not labels or not labels[0] or any(len(row) != len(labels[0]) for row in labels):
        raise ValueError('Expected rectangular nonempty labels')
    positions = [i for i in range(len(labels[0])-1)
                 if any(row[i+1] != -100 for row in labels)]
    if not positions:
        raise ValueError('No supervised next tokens')
    return positions


def selected_answer_loss(model, inputs):
    """Assistant-only causal loss without projecting unused prompt positions."""
    import torch
    labels = inputs['labels']
    positions = torch.tensor(supervised_positions(labels.tolist()), device=labels.device)
    outputs = model(**{key: value for key, value in inputs.items() if key != 'labels'},
                    logits_to_keep=positions)
    targets = labels[:, positions + 1]
    loss = torch.nn.functional.cross_entropy(
        outputs.logits.float().reshape(-1, outputs.logits.shape[-1]),
        targets.reshape(-1), ignore_index=-100)
    return loss, outputs


def data_hashes(directory):
    return {name:hashlib.sha256((directory/(name+'.jsonl')).read_bytes()).hexdigest()
            for name in ('train','valid','test')}


def validate_resume(checkpoint, configuration):
    for name in ('trainer_state.json','optimizer.pt','scheduler.pt','rng_state.pth'):
        if not (checkpoint/name).is_file():
            raise ValueError('Resume needs full training state: missing '+name)
    previous=json.loads((checkpoint.parent/'run_config.json').read_text())
    if previous!=configuration:
        raise ValueError('Resume configuration or frozen data changed')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model',type=Path,required=True,help='Local unquantized HF model directory, not MLX weights')
    parser.add_argument('--data',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--resume',type=Path)
    parser.add_argument('--rank',type=int,default=16)
    parser.add_argument('--layers',type=int,default=8)
    parser.add_argument('--epochs',type=int,default=3)
    parser.add_argument('--max-steps',type=int,default=-1,help='Optional bounded smoke run; -1 uses epochs')
    parser.add_argument('--max-length',type=int,default=4096)
    parser.add_argument('--accumulate',type=int,default=8)
    parser.add_argument('--learning-rate',type=float,default=2e-5)
    parser.add_argument('--seed',type=int,default=7)
    args=parser.parse_args()
    from training_priority import require_background
    require_background()
    if not args.model.is_dir() or not (args.model/'config.json').is_file():
        parser.error('--model must be an existing local HF checkpoint')
    if min(args.rank,args.layers,args.epochs,args.max_length,args.accumulate)<1 or args.learning_rate<=0:
        parser.error('Training sizes and learning rate must be positive')
    if args.max_steps==0 or args.max_steps < -1:
        parser.error('--max-steps must be -1 or positive')
    configuration={key:str(value.resolve()) if isinstance(value,Path) else value
                   for key,value in vars(args).items() if key not in ('resume','output','data')}
    configuration['data_sha256']=data_hashes(args.data)
    if args.resume:
        validate_resume(args.resume,configuration)
        if args.resume.parent.resolve()!=args.output.resolve():
            parser.error('Resume output must be the original checkpoint parent')
    elif args.output.exists() and any(args.output.iterdir()):
        parser.error('Use a fresh output directory or --resume; never overwrite a run')
    import torch
    if not torch.cuda.is_available():
        raise RuntimeError('A working NVIDIA CUDA PyTorch installation is required')
    from transformers import (AutoConfig,AutoModelForCausalLM,AutoTokenizer,BitsAndBytesConfig,
                              Trainer,TrainerCallback,TrainingArguments,EarlyStoppingCallback,set_seed)
    from peft import LoraConfig,get_peft_model,prepare_model_for_kbit_training
    from router_chat import install
    set_seed(args.seed)
    dtype=torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    tokenizer=AutoTokenizer.from_pretrained(args.model,local_files_only=True,trust_remote_code=False)
    install(tokenizer)
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token=tokenizer.eos_token
    datasets={}
    for split in ('train','valid'):
        rows=[json.loads(line) for line in (args.data/(split+'.jsonl')).read_text().splitlines() if line.strip()]
        if not rows:
            raise ValueError('Empty '+split+' split')
        datasets[split]=[encode_row(row,tokenizer,args.max_length) for row in rows]
    config=AutoConfig.from_pretrained(args.model,local_files_only=True,trust_remote_code=False)
    if args.layers>config.num_hidden_layers:
        raise ValueError('Requested more adapter layers than the model has')
    args.output.mkdir(parents=True,exist_ok=True)
    (args.output/'run_config.json').write_text(json.dumps(configuration,indent=2)+'\n')
    (args.output/'hardware.json').write_text(json.dumps(dict(
        gpu=torch.cuda.get_device_name(0),vram_bytes=torch.cuda.get_device_properties(0).total_memory,
        torch=torch.__version__,dtype=str(dtype)),indent=2)+'\n')
    quantization=BitsAndBytesConfig(load_in_4bit=True,bnb_4bit_quant_type='nf4',
        bnb_4bit_use_double_quant=True,bnb_4bit_compute_dtype=dtype)
    model=AutoModelForCausalLM.from_pretrained(args.model,local_files_only=True,trust_remote_code=False,
        quantization_config=quantization,device_map={'':0},torch_dtype=dtype,attn_implementation='sdpa')
    model.config.use_cache=False
    model=prepare_model_for_kbit_training(model,use_gradient_checkpointing=True,
                                        gradient_checkpointing_kwargs={'use_reentrant':False})
    model=get_peft_model(model,LoraConfig(r=args.rank,lora_alpha=2*args.rank,lora_dropout=.05,
        target_modules=['q_proj','k_proj','v_proj','o_proj','gate_proj','up_proj','down_proj'],
        layers_to_transform=list(range(config.num_hidden_layers-args.layers,config.num_hidden_layers)),
        bias='none',task_type='CAUSAL_LM'))
    model.print_trainable_parameters()
    stop={'requested':False}
    def request_stop(*_):
        stop['requested']=True
        print('Stop requested: saving full state after the current optimizer step.',flush=True)
    class Progress(TrainerCallback):
        def on_step_end(self,arguments,state,control,**kwargs):
            (args.output/'progress.json').write_text(json.dumps(dict(step=state.global_step,
                max_steps=state.max_steps,best_metric=state.best_metric,stop_requested=stop['requested']),indent=2)+'\n')
            if stop['requested'] or (args.output/'STOP').exists():
                control.should_save=True
                control.should_training_stop=True
            return control
    training=TrainingArguments(output_dir=str(args.output),num_train_epochs=args.epochs,max_steps=args.max_steps,
        per_device_train_batch_size=1,per_device_eval_batch_size=1,gradient_accumulation_steps=args.accumulate,
        learning_rate=args.learning_rate,lr_scheduler_type='cosine',warmup_ratio=.05,
        bf16=dtype==torch.bfloat16,fp16=dtype==torch.float16,gradient_checkpointing=True,
        gradient_checkpointing_kwargs={'use_reentrant':False},optim='paged_adamw_8bit',
        eval_strategy='steps',eval_steps=50,save_strategy='steps',save_steps=50,
        save_only_model=False,load_best_model_at_end=True,metric_for_best_model='eval_loss',
        greater_is_better=False,prediction_loss_only=True,logging_steps=5,report_to=[],
        push_to_hub=False,seed=args.seed,data_seed=args.seed,dataloader_num_workers=0,
        remove_unused_columns=False)
    def collate(rows):
        return {key:torch.tensor(value,dtype=torch.long) for key,value in pad_rows(rows,tokenizer.pad_token_id).items()}
    class AnswerTrainer(Trainer):
        def compute_loss(self, model, inputs, return_outputs=False, num_items_in_batch=None):
            # Qwen's vocabulary projection is large. Prompt logits have no loss,
            # so project only positions predicting supervised answer/EOS tokens.
            loss, outputs = selected_answer_loss(model, inputs)
            return (loss, outputs) if return_outputs else loss

    trainer=AnswerTrainer(model=model,args=training,train_dataset=datasets['train'],eval_dataset=datasets['valid'],
        processing_class=tokenizer,data_collator=collate,callbacks=[Progress(),EarlyStoppingCallback(early_stopping_patience=2)])
    # Loss is a microbatch mean, as in the original trainer. Let Trainer scale
    # gradient accumulation rather than treating it as an already summed loss.
    trainer.model_accepts_loss_kwargs = False
    signal.signal(signal.SIGINT,request_stop)
    signal.signal(signal.SIGTERM,request_stop)
    trainer.train(resume_from_checkpoint=str(args.resume) if args.resume else None)
    trainer.save_model(str(args.output/'selected-adapter'))
    tokenizer.save_pretrained(args.output/'selected-adapter')
    (args.output/'run.json').write_text(json.dumps(dict(global_step=trainer.state.global_step,
        best_checkpoint=trainer.state.best_model_checkpoint,best_loss=trainer.state.best_metric,
        interrupted=stop['requested']),indent=2)+'\n')


if __name__=='__main__':
    main()
