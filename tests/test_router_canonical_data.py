import json
from pathlib import Path
import tempfile
import unittest
from scripts.router_canonical_data import canonical_answer,prepare


class RouterCanonicalDataTests(unittest.TestCase):
    def test_serving_order_preserves_all_values_and_places_optional_fields_last(self):
        answer=dict(operation='answer',target='café',app_name='',acts_on_computer=False,media_artist='')
        rendered=canonical_answer(answer)
        self.assertEqual(json.loads(rendered),answer)
        self.assertEqual(list(json.loads(rendered)),['acts_on_computer','app_name','operation','target','media_artist'])
        self.assertEqual(json.loads(canonical_answer(dict(request='do that again'))),dict(request='do that again'))

    def test_snapshot_preserves_prompts_source_and_heldout_test_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'source';source.mkdir();dest=Path(tmp)/'prepared'
            row=dict(messages=[dict(role='system',content='Stable prompt'),dict(role='user',content='Quoted “name”'),dict(role='assistant',content=json.dumps(dict(operation='answer',acts_on_computer=False,target='',app_name='')))])
            raw=(json.dumps(row)+'\n').encode()
            for split in ('train','valid','test'):(source/(split+'.jsonl')).write_bytes(raw)
            prepare(source,dest)
            self.assertEqual((dest/'test.jsonl').read_bytes(),raw)
            self.assertEqual((source/'train.jsonl').read_bytes(),raw)
            converted=json.loads((dest/'train.jsonl').read_text())
            self.assertEqual(converted['messages'][:-1],row['messages'][:-1])
            self.assertEqual(json.loads(converted['messages'][-1]['content']),json.loads(row['messages'][-1]['content']))
            with self.assertRaises(FileExistsError):prepare(source,dest)
            with self.assertRaises(ValueError):prepare(source,source/'nested')
