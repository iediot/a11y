import json
from pathlib import Path
import tempfile
import unittest
from scripts.router_rebalance import rebalance


class ResamplingTests(unittest.TestCase):
    def test_only_training_is_resampled_and_no_new_answers_are_created(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder)/'source'; source.mkdir()
            def row(label):
                return {'messages':[{'role':'assistant','content':json.dumps(label)}]}
            original = [row({'acts_on_computer':True,'target':str(i)}) for i in range(3)]
            original += [row({'acts_on_computer':False}), row({'request':'previous task'})]
            (source/'train.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in original))
            for name in ('valid.jsonl','test.jsonl'):
                (source/name).write_bytes(b'held-out sentinel\n')
            out = Path(folder)/'balanced'; result = rebalance(source, out)
            rows = [json.loads(line) for line in (out/'train.jsonl').read_text(encoding='utf-8').splitlines()]
            self.assertTrue(all(r in original for r in rows))
            self.assertEqual(sum(json.loads(r['messages'][-1]['content']).get('acts_on_computer') is False for r in rows),3)
            self.assertEqual(result['sampled_rows'],7)
            for name in ('valid.jsonl','test.jsonl'):
                self.assertEqual((source/name).read_bytes(),(out/name).read_bytes())
            second = Path(folder)/'repeat'; rebalance(source, second)
            self.assertEqual((out/'train.jsonl').read_bytes(),(second/'train.jsonl').read_bytes())

    def test_source_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                rebalance(folder, folder)
