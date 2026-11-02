# Notes for AI coding agents

Start with [AGENTS.md](../../AGENTS.md) (or [CLAUDE.md](../../CLAUDE.md); the two are kept in
sync), then [HANDOFF.md](HANDOFF.md): one short current checkpoint with the owner's rules, the
latest measured results and open items. It is authoritative; the other files here are reference
or history and never instructions to restart a completed job.

**Latest (2026-11-01):** build `e47962fab5`, units 1,143 tests OK, Finder/Safari 13-turn set
13/13, harder suite 20/30. Qwen remains the default; no router fine-tune has been adopted.

**Owner preference:** do not keep an agent turn active by polling/sleeping while a shell batch
runs. Let it run, end the turn, and inspect results when requested or when a completion
notification is available. This also applies to other agents.

Local `.data` artifacts are not shipped with a fresh clone; missing files do not establish that
a job is running or a result passed.

## Reference map

- [HANDOFF.md](HANDOFF.md): owner rules, current state, open items, next step.
- [HANDOFF_ARCHIVE_2026-10.md](HANDOFF_ARCHIVE_2026-10.md): every dated checkpoint from
  2026-10-26 to 2026-10-30, with evidence paths (history only).
- [ARCH_TRAINING_HANDOFF.md](ARCH_TRAINING_HANDOFF.md): Arch CUDA setup, the completed and
  rejected 100-step pilot, pins and artifacts.
- [GUI_MODELS.md](GUI_MODELS.md): local GUI model support, protocols and benchmark limits.
- [Router README](../../scripts/router_README.md): router training data and qualification
  workflow. The hand-labelled held-out set is `tests/data/router_heldout.jsonl`, scored by
  `scripts/router_heldout.py`.
- [ON_DEVICE_TUNING.md](ON_DEVICE_TUNING.md): local model and latency settings.
- [PLATFORM_PARITY.md](PLATFORM_PARITY.md): each macOS-first feature (Settings panes, Finder
  selection, browser page address, scrolling) and its Windows status.
- [CLAUDE_HANDOFF.md](CLAUDE_HANDOFF.md): earlier architecture/platform brief; owner
  instructions in HANDOFF take precedence.

People evaluating the product can start with the [main README](../../README.md)
and [architecture](../ARCHITECTURE.md).
