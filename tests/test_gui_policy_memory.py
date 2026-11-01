import json
import tempfile
import unittest
from pathlib import Path

from scripts.gui_policy_memory import read_workload


class MemoryWorkloadTests(unittest.TestCase):
    def load(self, value):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'workload.json'
            path.write_text(json.dumps(value))
            return read_workload(path)

    def test_full_policy_and_observation_text_survive_unchanged(self):
        value={'system':'Policy\nDo not invent IDs.', 'user':'{"goal":"Read the visible page"}',
               'answer':'{"status":"needs_user","action":null}'}
        self.assertEqual(self.load(value),value)

    def test_extra_image_or_command_fields_are_rejected(self):
        value={'system':'Policy','user':'Observation','answer':'Response'}
        for extra in ('image','command','remote_url'):
            with self.subTest(extra=extra),self.assertRaises(ValueError):
                self.load(dict(value,**{extra:'unused'}))

    def test_missing_empty_or_non_text_fields_are_rejected(self):
        for value in ({'system':'Policy'},[],{'system':'Policy','user':{},'answer':'Response'},
                      {'system':'Policy','user':' ','answer':'Response'}):
            with self.subTest(value=value),self.assertRaises(ValueError):self.load(value)

    def test_tokenization_cannot_be_fed_an_unbounded_text_file(self):
        with self.assertRaises(ValueError):self.load({'system':'x'*40_001,'user':'Observation','answer':'Response'})
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'oversize.json';path.write_bytes(b' '*100_001)
            with self.assertRaises(ValueError):read_workload(path)


if __name__=='__main__':unittest.main()
