# Handoff — 2026-11-06 (end of day)

Rules: [AGENTS.md](../../AGENTS.md). Packages and gates: [PLAN.md](PLAN.md). How results
are judged: [TESTING.md](TESTING.md). Numbers: [RESULTS.md](RESULTS.md) (start with
"Current standing"). Training: [TRAINING.md](TRAINING.md). Previous checkpoint:
[archive](HANDOFF_ARCHIVE_2026-11-05.md).

Nothing is committed; the whole delivery is uncommitted on `main`. Commit only when the
owner asks. Subagents: pass `model` explicitly (opus, or sonnet/haiku for mechanical
work), never Fable; at most two at once.

## Where things stand

| Package | Status |
|---|---|
| P0 completion-evidence repair | done, reviewed |
| P1 request eval (`scripts/request_eval.py`, v2) | done, reviewed once; v2 fix round not re-reviewed |
| P2 sim eval (`scripts/sim_eval.py`, v2) | done, reviewed once; v2 fix round not re-reviewed. Its tripwire blocks writing under `.data`: write runs to a temp dir and copy |
| P3 baselines | done for HEAD and working tree, both splits (RESULTS) |
| P4 compact router wire schema | done: −12% output tokens per eval run, router held-out unchanged; halving was not possible without changing decisions |
| P5 request mechanisms 1–4 | done, not independently reviewed |
| P6 task-loop fixes A–F | done, reviewed, review fixes applied |
| P7 live confirmation | blocked: Accessibility not granted; owner says the screen is not available |
| P8 legacy live suites | done: private-app cases refused by default, inventory in LEGACY_LIVE_SUITES.md (implemented via kiro-cli) |
| Training | diagnosed, not started (TRAINING.md); Arch needs owner's Tailscale re-auth |

Results: request acceptance 65 → 82 of 127 (stricter scorer), sim dev 16 → 25 of 25 with
false successes 7 → 0, zero unauthorized actions throughout. Sim acceptance moved only
12 → 14: dev is saturated and its fixes generalise poorly. Request fixes and task-loop
round 2 are reviewed once (request) / not yet reviewed (task loop round 2).

## Open defects, highest value first

1. Sim acceptance: 12 fails and 4 false successes remain (aggregate only; do not read
   per-task acceptance detail while fixing). Dev failures to start from: pre-satisfied
   switch toggled off then "done"; confirmed delete not reported as done; two-facts
   reply missing values; works in Chrome when Safari was named; no clarifying question
   for an ambiguous target (the loop has no ask mechanism).
2. A goal clause with no names ("and tick the box") is only caught if the verifier lists
   it; plan parts / done_when are not used for coverage yet.
3. Request dev fails (12): "do the same search in chrome", explicit Chrome window
   placement (helper supports Safari only), memory/follow-up facts, recent downloads,
   volume change handed off as inspect, delete-folder opens Downloads, name question
   regressed after the pending-proposal prompt paragraph.
4. Confirmation coverage gaps (documented, not fixed): buttons exposed as AXGroup or
   OCR-only, DECLINE-labelled buttons, Windows bare-point clicks.
5. Stop on a busy GPU can exceed the sim's 2 s bound.

## Owner-only blockers

- Accessibility for the terminal running Claude Code (live `human_eval.py`).
- Arch: `ssh edi@100.74.40.62` asks for a one-time Tailscale browser check.

## Next actions

1. Independent review (opus) of P5 request changes.
2. Fix open defects 1–2 on dev; confirm on acceptance aggregate.
3. Then decide on training using TRAINING.md's gate.

Disk: unused Ollama models and fine-tune intermediates were deleted on 2026-11-06
(installed: qwen3:4b-instruct, qwen3-vl:4b-instruct, qwen3-vl:2b-instruct). Quantized
router candidates and adapters remain under `.data/`.

## Model exploration (paused by owner, 2026-11-06)

- colibri (disk-streaming MoE engine): its macOS build has no Metal support for the
  qwen36 engine; CPU-only (~6 tok/s published), needs Homebrew libomp, wants ~30 GB RAM.
  Rejected for this Mac; trial files deleted.
- Untested candidate if resumed: an Ollama vision MoE around 30B-A3B (~19-20 GB) replacing
  both 4B models; the open question is memory pressure on 24 GB.
- Do not download or test models until the owner resumes this.
