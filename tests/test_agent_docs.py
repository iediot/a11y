"""CLAUDE.md and AGENTS.md stay in sync, and HANDOFF.md stays one short checkpoint with the rules."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


def shared(text):
    """The notes without their title line and the one paragraph about the other agent."""
    body = text.split('\n', 1)[1]
    return re.sub(r'\n(?:You may use the Codex CLI|\*\*Start with `docs/ai/HANDOFF\.md`:\*\*).*?\n\n', '\n\n', body,
                  count=1, flags=re.S)


class AgentDocsTests(unittest.TestCase):
    def test_claude_and_agents_notes_match(self):
        claude = (ROOT / 'CLAUDE.md').read_text(encoding='utf-8')
        agents = (ROOT / 'AGENTS.md').read_text(encoding='utf-8')
        self.assertTrue(claude.startswith('# A11y: notes for Claude Code'))
        self.assertTrue(agents.startswith('# A11y: notes for Codex'))
        self.assertNotEqual(shared(claude), claude.split('\n', 1)[1])  # the agent paragraph was found
        self.assertEqual(shared(claude), shared(agents))

    def test_handoff_is_short_keeps_the_rules_and_points_to_the_archive(self):
        handoff = (ROOT / 'docs/ai/HANDOFF.md').read_text(encoding='utf-8')
        self.assertLess(len(handoff.splitlines()), 160)
        self.assertIn("## The owner's rules (non-negotiable)", handoff)
        for rule in ('**Local only.**', '**No hard-coded per-task phrase lists**', 'Never message anyone',
                     'no Co-Authored-By / AI attribution lines', 'never bypass target validation'):
            self.assertIn(rule, handoff)
        archive = re.search(r'\]\((HANDOFF_ARCHIVE_[\w-]+\.md)\)', handoff)
        self.assertTrue(archive and (ROOT / 'docs/ai' / archive.group(1)).exists())

    def test_linked_agent_docs_exist(self):
        for name in ('CLAUDE.md', 'AGENTS.md', 'docs/ai/README.md', 'docs/ai/HANDOFF.md'):
            text = (ROOT / name).read_text(encoding='utf-8')
            for link in re.findall(r'\]\(([^)#:]+\.md)\)', text):
                self.assertTrue(((ROOT / name).parent / link).resolve().exists(), name + ' → ' + link)


if __name__ == '__main__':
    unittest.main()
