import json
from pathlib import Path
import tempfile
import unittest
from scripts.router_train_cuda import encode_row,pad_rows,data_hashes,validate_resume,supervised_positions


class Tokenizer:
    def apply_chat_template(self,messages,tokenize=False,add_generation_prompt=False):
        result=''.join('<|im_start|>'+m['role']+'\n'+m['content']+'<|im_end|>\n' for m in messages)
        return result+('<|im_start|>assistant\n' if add_generation_prompt else '')
    def encode(self,text,add_special_tokens=False):
        return list(text.encode())


class CudaDataTests(unittest.TestCase):
    def row(self):
        return {'messages':[{'role':'user','content':'Navigate to a folder.'},
                            {'role':'assistant','content':'{"operation":"computer_task"}'}]}

    def test_only_complete_assistant_json_and_eos_contribute_to_loss(self):
        tokenizer=Tokenizer(); row=self.row(); encoded=encode_row(row,tokenizer,4096)
        prefix=tokenizer.apply_chat_template(row['messages'][:-1],add_generation_prompt=True)
        self.assertTrue(all(label==-100 for label in encoded['labels'][:len(prefix)]))
        answer=bytes(encoded['labels'][len(prefix):]).decode()
        self.assertEqual(answer,row['messages'][-1]['content']+'<|im_end|>\n')
        with self.assertRaisesRegex(ValueError,'truncate'):
            encode_row(row,tokenizer,len(encoded['input_ids'])-1)

    def test_non_json_or_unfinished_assistant_turn_is_rejected(self):
        row=self.row();row['messages'][-1]['content']='not JSON'
        with self.assertRaises(ValueError):encode_row(row,Tokenizer(),4096)
        with self.assertRaises(ValueError):encode_row({'messages':row['messages'][:-1]},Tokenizer(),4096)

    def test_padding_masks_only_padding_even_when_pad_equals_eos(self):
        first=dict(input_ids=[1,2,9],attention_mask=[1,1,1],labels=[-100,2,9])
        second=dict(input_ids=[1,9],attention_mask=[1,1],labels=[-100,9])
        result=pad_rows([first,second],9)
        self.assertEqual(result['input_ids'][1],[1,9,9])
        self.assertEqual(result['labels'][1],[-100,9,-100])
        self.assertEqual(result['attention_mask'][1],[1,1,0])

    def test_resume_requires_optimizer_state_and_identical_frozen_configuration(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);checkpoint=root/'checkpoint-50';checkpoint.mkdir()
            config={'data_sha256':{'test':'frozen'},'rank':16}
            (root/'run_config.json').write_text(json.dumps(config))
            with self.assertRaisesRegex(ValueError,'optimizer'):
                (checkpoint/'trainer_state.json').write_text('{}')
                validate_resume(checkpoint,config)
            for name in ('optimizer.pt','scheduler.pt','rng_state.pth'):(checkpoint/name).touch()
            validate_resume(checkpoint,config)
            with self.assertRaisesRegex(ValueError,'changed'):
                validate_resume(checkpoint,dict(config,rank=32))

    def test_holdout_hashes_change_if_rows_change(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for name in ('train','valid','test'):(root/(name+'.jsonl')).write_text(name)
            before=data_hashes(root)
            (root/'test.jsonl').write_text('changed')
            self.assertNotEqual(before['test'],data_hashes(root)['test'])


class SupervisedPositionsTests(unittest.TestCase):
    def test_causal_shift_includes_first_answer_and_final_eos(self):
        self.assertEqual(supervised_positions([[-100,-100,42,9]]), [1,2])

    def test_batch_union_preserves_masked_padding_and_different_prompt_lengths(self):
        rows=[[-100,-100,42,9,-100],[-100,7,8,9,10]]
        positions=supervised_positions(rows)
        self.assertEqual(positions,[0,1,2,3])
        self.assertEqual([[r[i+1] for i in positions] for r in rows],
                         [[-100,42,9,-100],[7,8,9,10]])

    def test_unsupervised_and_malformed_batches_fail(self):
        for rows in ([],[[]],[[-100,-100]],[[1],[1,2]]):
            with self.assertRaises(ValueError): supervised_positions(rows)
