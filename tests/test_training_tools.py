"""Offline checks only: no model loads, screenshots or native app calls."""
import os
import shutil
import sys
from pathlib import Path
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from grounding_eval import point, inside_box
from training_priority import require_background


class TrainingToolsTests(unittest.TestCase):
    def test_grounding_metric(self):
        self.assertEqual(point('(123, 456)'), (123, 456))
        self.assertIsNone(point('cannot locate'))
        self.assertTrue(inside_box((95, 100), [100, 100, 120, 120]))
        self.assertFalse(inside_box((95, 100), [100, 100, 120, 120], 0))
        self.assertFalse(inside_box(None, [0, 0, 1000, 1000]))
        self.assertFalse(inside_box((1001, 500), [0, 0, 990, 1000]))

    @unittest.skipUnless(hasattr(os, 'getpriority'), 'needs POSIX process priorities (os.getpriority)')
    def test_priority_fails_closed(self):
        with patch('training_priority.os.getpriority', return_value=0):
            with self.assertRaisesRegex(RuntimeError, 'Background priority'):
                require_background()
        with patch('training_priority.os.getpriority', return_value=19):
            require_background()

    def test_priority_fails_closed_without_posix_priorities(self):
        # Windows has no os.getpriority: the guard must refuse rather than assume background priority.
        import types
        with patch('training_priority.os', types.SimpleNamespace()):
            with self.assertRaisesRegex(RuntimeError, 'Background priority'):
                require_background()

    def test_early_stop_state(self):
        # No MLX import occurs until actual validation callback invocation.
        from router_train_local import EarlyStop
        state = EarlyStop('/tmp/unused')
        self.assertEqual(state.patience, 2)
        self.assertEqual(state.history, [])




class RouterFormattingTests(unittest.TestCase):
    def test_modelfile_preserves_template_parameters(self):
        from router_modelfile import make_modelfile
        original = 'FROM old\nTEMPLATE """{{ .System }}\nPARAMETER inside template\n{{ .Response }}"""\nPARAMETER stop "<|im_end|>"\nPARAMETER temperature 0\n'
        result = make_modelfile(original, './router.gguf')
        self.assertTrue(result.startswith('FROM ./router.gguf\nTEMPLATE'))
        self.assertEqual(result.split('TEMPLATE', 1)[1], original.split('TEMPLATE', 1)[1])
        with self.assertRaises(ValueError):
            make_modelfile('FROM old\nPARAMETER seed 7\n', 'new')

    def test_chatml_install_and_complete_turn_guard(self):
        from router_chat import install, TEMPLATE, check_training_rows
        from types import SimpleNamespace
        class Tokenizer:
            # Like an HF tokenizer: backend _tokenizer is not the HF wrapper.
            _tokenizer = SimpleNamespace()
            def apply_chat_template(self, messages, tokenize=True, add_generation_prompt=False, **kwargs):
                value = ''.join('<|im_start|>'+m['role']+'\n'+m['content']+'<|im_end|>\n' for m in messages)
                if add_generation_prompt:
                    value += '<|im_start|>assistant\n'
                return list(value.encode()) if tokenize else value
        tokenizer = Tokenizer()
        install(tokenizer)
        self.assertEqual(tokenizer.chat_template, TEMPLATE)
        wrapped = SimpleNamespace(_tokenizer=tokenizer)
        install(wrapped)
        self.assertEqual(wrapped._tokenizer.chat_template, TEMPLATE)
        rows = [{'messages':[{'role':'user','content':'hi'}, {'role':'assistant','content':'{"operation":"answer"}'}]}]
        check_training_rows(rows, tokenizer, 1000)
        with self.assertRaisesRegex(ValueError, 'truncate'):
            check_training_rows(rows, tokenizer, 5)

    def test_mlx_raw_errors_retained_without_gpu(self):
        import json
        from types import SimpleNamespace
        from scripts.router_eval import MLXPlanner, evaluate
        from scripts import router_data as data
        provider = object.__new__(MLXPlanner)
        provider.completions = []
        provider.model = object()
        provider.tokenizer = SimpleNamespace(
            apply_chat_template=lambda *a, **k: 'prompt',
            encode=lambda text, **k: list(text.encode()))
        fake_mlx = SimpleNamespace(generate=lambda *a, **k: '<think></think>{"request": ""}')
        fake_sampler = SimpleNamespace(make_sampler=lambda **k: None)
        row = {'messages': data.messages('how are you', {'request': ''}, last_task='open Safari')}
        with patch.dict(sys.modules, {'mlx_lm': fake_mlx, 'mlx_lm.sample_utils': fake_sampler}):
            report = evaluate([row], provider)
        self.assertEqual(len(report['failures']), 1)
        raw = report['outputs'][0]['completions'][0]
        self.assertTrue(raw['raw'].startswith('<think>'))
        self.assertEqual(raw['max_tokens'], 200)
        self.assertEqual(report['follow_up_exact_accuracy'], 0)

    @unittest.skipUnless(os.name == 'posix' and shutil.which('bash'), 'needs a POSIX bash for the shell pipeline')
    def test_pipeline_syntax(self):
        import subprocess
        root = Path(__file__).resolve().parents[1]
        subprocess.run(['bash', '-n', str(root/'scripts/router_pipeline.sh')], check=True)

    def test_pipeline_scores_base_and_adapter_on_same_snapshot(self):
        root = Path(__file__).resolve().parents[1]
        pipeline = (root/'scripts/router_pipeline.sh').read_text(encoding='utf-8')
        for name,model in [('eval-base-ollama','qwen3:4b-instruct'),('eval-ollama','a11y-router-test')]:
            stage = pipeline.split('stage '+name+' ',1)[1].split('\nstage ',1)[0]
            self.assertIn('--model '+model,stage)
            self.assertIn('--data "$RUN/data/test.jsonl"',stage)


if __name__ == '__main__':
    unittest.main()
