"""Tiny random multimodal LoRA/loss-path validation. No pretrained weights or optimizer steps."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
from router_train_cuda import selected_answer_loss


def check():
    import torch,transformers,peft
    from transformers import Qwen3VLConfig,Qwen3VLForConditionalGeneration
    from peft import LoraConfig,get_peft_model
    torch.set_num_threads(1);torch.manual_seed(37)
    config=Qwen3VLConfig(text_config=dict(vocab_size=97,hidden_size=32,intermediate_size=64,
        num_hidden_layers=2,num_attention_heads=4,num_key_value_heads=2,head_dim=8,
        rope_scaling={'rope_type':'default','mrope_section':[1,1,2]},pad_token_id=2,eos_token_id=2),
        vision_config=dict(depth=2,hidden_size=32,intermediate_size=64,num_heads=4,patch_size=16,
        temporal_patch_size=2,spatial_merge_size=2,out_hidden_size=32,num_position_embeddings=64,
        deepstack_visual_indexes=[0,1]),image_token_id=90,video_token_id=89,vision_start_token_id=91,vision_end_token_id=92)
    model=Qwen3VLForConditionalGeneration(config)
    model=get_peft_model(model,LoraConfig(r=2,lora_alpha=4,lora_dropout=0.,task_type='CAUSAL_LM',
        target_modules=r'.*language_model\.layers\.\d+\.(self_attn|mlp)\.(q_proj|k_proj|v_proj|o_proj|gate_proj|up_proj|down_proj)'))
    with torch.no_grad():
        for parameter in model.parameters():
            if parameter.requires_grad:parameter.normal_(0.,.03)
    model.eval();reference=deepcopy(model)
    inputs=dict(input_ids=torch.tensor([[5,91,90,90,90,90,92,7,8,2]]),
                attention_mask=torch.ones((1,10),dtype=torch.long),
                labels=torch.tensor([[-100]*8+[8,2]]),
                pixel_values=torch.randn(16,3*2*16*16),image_grid_thw=torch.tensor([[1,4,4]]))
    output=reference(**inputs)
    loss,_=selected_answer_loss(model,inputs)
    torch.testing.assert_close(loss,output.loss,atol=2e-6,rtol=2e-5)
    loss.backward();output.loss.backward();checked=0;nonzero=0;maximum=0.
    for (key,left),(name,right) in zip(model.named_parameters(),reference.named_parameters()):
        assert key==name
        if not left.requires_grad:continue
        assert 'language_model.layers.' in key and 'lora_' in key,key
        assert left.grad is not None and right.grad is not None,key
        assert torch.isfinite(left.grad).all(),key
        torch.testing.assert_close(left.grad,right.grad,atol=2e-6,rtol=2e-5,msg=key)
        checked+=1;nonzero+=bool(left.grad.count_nonzero());maximum=max(maximum,(left.grad-right.grad).abs().max().item())
    assert checked and nonzero
    return dict(passed=True,torch=torch.__version__,transformers=transformers.__version__,peft=peft.__version__,
                image_tokens=4,supervised_tokens=2,gradient_tensors=checked,nonzero_gradient_tensors=nonzero,
                loss=loss.item(),native_loss=output.loss.item(),max_gradient_difference=maximum,
                limitations='Tiny random unquantized CPU model; no 4B NF4/8GB VRAM fit, processor/tokenizer or conversion qualification. No optimizer steps.')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    from training_priority import require_background
    require_background();report=check();a.out.parent.mkdir(parents=True,exist_ok=True)
    a.out.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))


if __name__=='__main__':main()
