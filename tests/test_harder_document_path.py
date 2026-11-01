import sys
from pathlib import Path
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import live_harder


class DocumentPathCheckerTests(unittest.TestCase):
    def test_native_posix_strings_spaces_and_unicode_do_not_need_alias_coercion(self):
        state={};expected=Path('/tmp/A11y test 123 café.txt')
        with patch.object(live_harder,'osa',return_value=str(expected)) as native:
            self.assertTrue(live_harder.textedit_at(expected,state))
            self.assertEqual(state['_observed']['document_path'],str(expected))
            self.assertNotIn('as alias',native.call_args.args[0])
    def test_missing_or_other_document_still_fails(self):
        for actual in ('','/tmp/other/A11y test 123.txt'):
            with patch.object(live_harder,'osa',return_value=actual):
                self.assertFalse(live_harder.textedit_at(Path('/tmp/A11y test 123.txt')))
