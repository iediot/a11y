"""Check documentation entry points and evidence navigation, not duplicated prose."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
ENTRY_POINTS = (
    'AGENTS.md', 'CLAUDE.md', 'docs/ai/README.md', 'docs/ai/HANDOFF.md',
    'docs/ai/PLAN.md', 'docs/ai/TESTING.md', 'docs/ai/RESULTS.md', 'docs/ARCHITECTURE.md',
)


def markdown_links(text):
    return re.findall(r'\]\(([^)]+)\)', text)


class AgentDocsTests(unittest.TestCase):
    def test_claude_imports_the_canonical_policy(self):
        imports = re.findall(r'^@([^\s]+)\s*$', (ROOT / 'CLAUDE.md').read_text(), re.M)
        self.assertEqual(imports, ['AGENTS.md'])
        self.assertTrue((ROOT / imports[0]).is_file())
        self.assertLess(len((ROOT / 'CLAUDE.md').read_text().splitlines()), 20)

    def test_local_document_links_resolve_inside_repository(self):
        for name in ENTRY_POINTS:
            source = ROOT / name
            self.assertTrue(source.is_file(), name)
            for link in markdown_links(source.read_text()):
                if '://' in link or link.startswith('#'):
                    continue
                target = (source.parent / link.split('#', 1)[0]).resolve()
                with self.subTest(source=name, link=link):
                    self.assertTrue(target.is_relative_to(ROOT.resolve()))
                    self.assertTrue(target.is_file(), name + ' -> ' + link)

    def test_handoff_navigation_reaches_plan_contract_and_evidence(self):
        handoff = ROOT / 'docs/ai/HANDOFF.md'
        targets = {(handoff.parent / link).resolve()
                   for link in markdown_links(handoff.read_text()) if '://' not in link}
        for name in ('AGENTS.md', 'docs/ai/PLAN.md', 'docs/ai/TESTING.md', 'docs/ai/RESULTS.md'):
            self.assertIn((ROOT / name).resolve(), targets)

    def test_previous_checkpoint_is_preserved_as_history(self):
        handoff = ROOT / 'docs/ai/HANDOFF.md'
        archives = [link for link in markdown_links(handoff.read_text())
                    if Path(link).name.startswith('HANDOFF_ARCHIVE_')]
        self.assertTrue(archives)
        for link in archives:
            self.assertTrue((handoff.parent / link).is_file())

    def test_documented_python_entry_commands_exist(self):
        guide = (ROOT / 'AGENTS.md').read_text()
        scripts = re.findall(r'(?:python3|\.venv/bin/python)\s+([^\s`]+\.py)\b', guide)
        self.assertTrue(scripts)
        for script in scripts:
            self.assertTrue((ROOT / script).is_file(), script)


if __name__ == '__main__':
    unittest.main()
