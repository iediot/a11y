# Notes for AI coding agents

Start with [AGENTS.md](../../AGENTS.md) (or [CLAUDE.md](../../CLAUDE.md)), then
[HANDOFF.md](HANDOFF.md). The newest checkpoint in HANDOFF is authoritative;
older dated entries are history, not instructions to restart completed jobs.

## Current work: 2026-10-30

Current source build **`2385a6e664`** passed **1,096 tests, OK (1 skipped)**.
Phased native scrolling corrects the observed direction reversal; half-window
scroll distance and stricter queued-input app/window scope are implemented and
unit-tested. Their live task benefit is still unmeasured. The previous complete
13-turn batch passed4/13, including2/2 Apple Support but0/3 Finder selections and
0/2 complete local web workflows. Next viewport-first batch is prepared in
`.data/live_eval/scroll-navigation-live-20261030/`, **not launched**. No Mac app or
test/training worker was running at the checkpoint. Start with the authoritative
current checkpoint in [HANDOFF.md](HANDOFF.md); older running notes are history.

R3 training and qualification finished; the candidate failed the regression gate
and was not adopted. Source development has resumed. Qwen remains default.
The retained artifacts, relative to the repository root, are:

| File | Purpose |
|---|---|
| `.data/live_eval/recursive-10/progress.json` | Whole batch stage/status |
| `.data/live_eval/recursive-10/power.json` | Training power guard/status |
| `.data/live_eval/recursive-10/r3/adapter/progress.json` | Last reported training iteration/loss |
| `.data/live_eval/recursive-10/r3/training.log` | Training log |
| `.data/live_eval/recursive-10/summary.json` | Final summary, only when completed |

The shell chains training → validation-selected conversion → separate Ollama test
alias → paired 406-row scoring → completion checks. It never adopts the model or
runs screen tests automatically. Qwen remains the default. Last full unit suite:
**1,096 tests, OK (1 skipped)**. See HANDOFF for measured results, failed experiments and limitations.

**Owner preference:** do not keep an agent turn active by polling/sleeping while a
shell batch runs. Let it run, end the turn, and inspect results when requested or
when a completion notification is available. This also applies to other agents.

The R3 batch is finished. Do not restart its historical recipes automatically.
Local `.data` artifacts are not shipped with a fresh clone; missing files do not
establish that a job is running or a result passed.

## Reference map

- [ARCH_TRAINING_HANDOFF.md](ARCH_TRAINING_HANDOFF.md): Arch CUDA setup, completed
  100-step pilot, completed parity/conversion/qualification, rejection, pins and artifacts.

- [HANDOFF.md](HANDOFF.md): current owner rules, active work, results and open items.
- [GUI_MODELS.md](GUI_MODELS.md): local GUI model support, protocols and benchmark limits.
- [Router README](../../scripts/router_README.md): training data and qualification workflow.
- [ON_DEVICE_TUNING.md](ON_DEVICE_TUNING.md): earlier local model and latency notes.
- [CLAUDE_HANDOFF.md](CLAUDE_HANDOFF.md): historical architecture/platform brief;
  current owner instructions in HANDOFF take precedence.

People evaluating the product can start with the [main README](../../README.md)
and [architecture](../ARCHITECTURE.md).
