# Evidence ledger

One row per finished run. How runs are judged: [TESTING.md](TESTING.md). What is being
worked on: [HANDOFF.md](HANDOFF.md). Run directories are under `.data/eval/` (ignored by
git, so absent in a fresh clone). A failed run is never replaced by a later good one.

Machine for all rows: Apple M5 Pro, 24 GB, macOS 27, Ollama, text model
`qwen3:4b-instruct` (digest `0edcdef3…`), vision model `qwen3-vl:4b-instruct`,
temperature 0, seed 7, context 16384.

## Current standing (2026-11-06, final runs, GPU otherwise idle)

Working tree = P0 completion repair + P4 router wire schema + P5 request fixes (mechanisms
1–4) + P6 task-loop fixes and review round. Unit suite 1566 OK, 2 skipped.

| Eval | Split | HEAD | Working tree | False success | Unauthorized |
|---|---|---|---|---|---|
| Request v2 (pass / fail / handoff) | dev | 70 / 30 / 11 | 89 / 12 / 10 | 0 → 0 | 0 → 0 |
| Request v2 | acceptance | 65 / 51 / 11 | 83 / 36 / 8 | 0 → 0 | 0 → 0 |
| Sim v2 (pass / fail, scored) | dev | 16 / 10 | 20 / 5 | 7 → 1 | 0 → 0 |
| Sim v2 | acceptance | 12 / 13 | 13 / 12 | 5 → 4 | 0 → 0 |

Run dirs in `.data/eval/2026-11-06/`: `req2-{dev,acc}-head-1`, `req2-{dev,acc}-wt-final`,
`sim2-{dev,acc}-head-1`, `sim2-{dev,acc}-wt-final`. Request model-stage p50 1.26 s dev /
1.31 s acceptance; output tokens dev 7,617 → 6,551, acceptance 9,239 → 8,198. Router
held-out unchanged at 124/165 operations. Acceptance sim gains are small: the task-loop
fixes generalise only partly beyond dev wording.

## Follow-up (2026-11-06, after review fixes and task-loop round 2)

| Eval | Split | Previous | Now | False success | Unauthorized |
|---|---|---|---|---|---|
| Request (stricter scorer: cancel promises, echo replies) | acceptance | 83 / 36 / 8 | 82 / 37 / 8 (`req3-acc-final`) | 0 | 0 |
| Sim v2 | dev | 20 / 5 | 25 / 0 (`dev-final`) | 1 → 0 | 0 |
| Sim v2 | acceptance | 13 / 12 | 14 / 11 (`acc-final`) | 4 → 3 | 0 |

Reading: dev is now saturated while acceptance barely moves, so the remaining sim
failures are not reachable by fixing dev cases. Next: new, differently shaped dev
scenarios, or a model limitation (see TRAINING.md). Unit suite 1608 OK.

## Unit suite

| Date | Tree | Result |
|---|---|---|
| 2026-11-06 | Committed HEAD `04d56bb` (exported) | 1215 tests OK, 2 skipped |
| 2026-11-06 | Working tree as inherited from the interrupted session | 1310 tests, 6 failures, 2 errors (task-loop completion change) |
| 2026-11-06 | Working tree after P0 repair and review fix round | 1441 tests OK, 2 skipped |

## Request eval (protocol `request-outcomes-v1`, seed 20261106, 1 run)

Harness v1 was reviewed afterwards and found to have about 6–8 wrong verdicts per
split, a false-success counter that is wrong in both directions, and passes for work
merely handed to the task loop (12–13 per split). Treat these rows as a first
diagnostic, not a baseline; they will be superseded by v2 runs.

| Run dir (`.data/eval/2026-11-06/`) | Tree | Split | Pass / planned | Unauthorized | Model calls | Latency note |
|---|---|---|---|---|---|---|
| `req-dev-head-1` | HEAD | dev | 89 / 112 | 0 | 136 | wall p50 1.39 s, uncontended |
| `req-acc-head-1` | HEAD | acceptance | 83 / 121 | 0 | 159 | wall p50 1.39 s |
| `req-dev-wt-1` | working tree before the chat-shortcut revert | dev | 89 / 112 | 0 | 114 | contended: 104 s of 218 s unaccounted; ignore latency |
| `req-acc-wt-1` | same | acceptance | 87 / 121 | 0 | 138 | wall p50 1.33 s |

The 87 vs 83 acceptance difference is inside the verdict-error margin and from single
runs: not established. The working tree in these two rows still had the first-turn
chat shortcut that was later reverted, so its lower model-call count no longer applies.

## Request eval v2 (protocol `request-outcomes-v2`, seed 20261106, 1 run)

Scorer reworked after review: work handed to the task loop is `handoff` (outside the
pass rate), trivial do-nothing policies pass at most 4.5% of turns each, honesty split
into `false_success` and `wrong_action`. The fix round has not had a second review.
Latency in these rows is invalid: the GPU was shared with other jobs.

| Run dir (`.data/eval/2026-11-06/`) | Tree | Split | Pass / fail / handoff (planned) | Pass of decided | False success | Wrong action | Unauthorized | Model calls |
|---|---|---|---|---|---|---|---|---|
| `req2-dev-head-1` | HEAD | dev | 70 / 30 / 11 (111) | 70% | 0 | 1 | 0 | 135 |
| `req2-acc-head-1` | HEAD | acceptance | 65 / 51 / 11 (127) | 56% | 0 | 7 | 0 | 163 |
| `req2-dev-p4-1` | working tree: P0 + P4 router wire schema + P6 task loop + partial P5 edits | dev | 81 / 20 / 10 (111) | 80% | 0 | 1 | 0 | 126 |
| `req2-acc-p4-1` | same | acceptance | 81 / 38 / 8 (127) | 68% | 0 | 6 | 0 | 160 |

The P4 rows mix changes (the P5 agent had partial edits in `service.py`/`app_names.py` when they ran), so the gain is not attributable to the router schema alone. Router held-out after P4: 124/165 operations, 102/165 all fields, identical to before (`heldout-p4.json`). Output tokens dev 7,617 → 6,662, acceptance 9,239 → 8,167 (−12%; the router is one of several calls per turn). Model-stage p50 1.36 s dev / 1.32 s acceptance, GPU mostly idle.

## Sim eval (protocol `sim-desktop-v1`, seed 71053, 1 run)

Harness v1 was reviewed afterwards: "impossible" tasks pass on any non-done ending,
"already satisfied" tasks pass even when the loop toggles state and restores it,
crashes leave the denominator, and one acceptance task depends on a caret artefact.
The reviewer confirmed 5 of the 6 dev fails as product defects. Superseded by v2 runs.

| Run dir (`.data/eval/2026-11-06/`) | Tree | Split | Pass / scored | False success | Unauthorized | Cancelled cleanly | Model calls |
|---|---|---|---|---|---|---|---|
| `sim-dev-head-1` | HEAD | dev | 14 / 20 | 4 | 0 | 2 / 2 | 138 |
| `sim-acc-head-1` | HEAD | acceptance | 14 / 20 | 2 | 0 | 2 / 2 | 141 |
| `sim-dev-wt-2` | working tree after P0 | dev | 14 / 20 | 4 | 0 | 2 / 2 | 137 |
| `sim-acc-wt-2` | working tree after P0 | acceptance | 14 / 20 | 2 | 0 | 2 / 2 | 140 (harness file changed during run) |

Reading: the P0 repair neither fixed nor broke any simulated task. The dev false
successes are multi-part goals accepted after their first part.

## Sim eval v2 (protocol `sim-desktop-v2`, seed 71053, 1 run, 28 tasks per split)

Oracles tightened after review (honest stops, pre-satisfied state, crashes scored as
fails, Stop within 2 s); five cross-app/ambiguity/recovery tasks added per split.
Not yet independently re-reviewed.

| Run dir (`.data/eval/2026-11-06/`) | Tree | Split | Pass / fail (scored) | False success | Unauthorized | Cancelled cleanly | Model calls |
|---|---|---|---|---|---|---|---|
| `sim2-dev-head-1` | HEAD | dev | 16 / 10 (26) | 7 | 0 | 2 / 2 | 184 |
| `sim2-acc-head-1` | HEAD | acceptance | 12 / 13 (25) | 5 | 0 | 3 / 3 | 180 |

## Conversation probe (protocol `conversation-outcomes-v1`, 18 turns, from the earlier session)

| Run dir | Tree | Pass | Model calls | p50 |
|---|---|---|---|---|
| `conv-baseline` | snapshot before that session's changes | 12 / 18 | 30 | 1.59 s |
| `conv-candidate` | that session's tree (chat shortcut since reverted) | 15 / 18 | 21 | 1.29 s |

## Router profile (2026-11-06, 12 hand-run requests through `Planner.interpret_request`)

Not a benchmark; it located the cost. Prompt 2,644–2,654 tokens, 49–83 ms (cached).
Output 68–91 tokens, 956–1,243 ms, about 14 ms per token; roughly 60 of those tokens
are fixed keys and empty defaults. Shortcut operations add a 13-token coverage call of
about 0.3 s.

## Live workflows

None. `scripts/human_eval.py` preflight on 2026-11-05 and 2026-11-06: Screen Recording
granted, Accessibility not granted to the launching terminal; all 14 turns `blocked`,
no app opened.

## Earlier results

Totals from before 2026-11-05 are in
[the archived checkpoint](HANDOFF_ARCHIVE_2026-11-05.md). They were produced by suites
that graded on replies and touched private apps, and are not a baseline. The router
fine-tune history and why its verdicts were unsound are in [TRAINING.md](TRAINING.md).
