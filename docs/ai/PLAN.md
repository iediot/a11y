# Plan: work packages

Policy: [AGENTS.md](../../AGENTS.md). How results are judged: [TESTING.md](TESTING.md).
Where things stand right now: [HANDOFF.md](HANDOFF.md). Measured numbers: [RESULTS.md](RESULTS.md).

The product goal is plain: a person says something to Ally, the right thing happens on
their computer, Ally says truthfully what happened, and the wait is short. Every package
below exists to measure that or to move it.

## How a package runs

Each package has one implementer and one independent reviewer (separate agents).

1. **Scope.** The package names the files it may edit. Nothing else is touched. Packages
   with overlapping files run one after another, never in parallel.
2. **Baseline.** Before changing runtime behaviour, run the instrument named in the
   package on the unchanged tree and keep the output directory.
3. **Implement.** Smallest general change that fixes the mechanism. No phrase lists, no
   tuning to individual cases. New behaviour gets a test that fails on the old code.
4. **Review.** The reviewer gets the diff and the package goal, not the implementer's
   conclusions. They look for false passes, false "needs_user", safety regressions
   (confirmation, consent, cancellation), Windows contract breaks and extra model calls.
5. **Fix findings**, then rerun the instrument with the same seed/split (`--compare`).
6. **Gate.** Full unit suite passes; the instrument shows the targeted gain; acceptance
   split shows no loss; false successes and unauthorized actions stay at zero.
7. **Record.** One row in RESULTS with run directories; HANDOFF updated.

Fix-agents may read dev-split failures. They see only aggregate acceptance numbers and
never open `tests/data/router_heldout.jsonl`.

## Instruments

| Instrument | Command | Measures | Needs |
|---|---|---|---|
| Unit suite | `QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests -q` | Contracts, validation, regressions | nothing |
| Request eval | `scripts/request_eval.py --split dev\|acceptance --output DIR` | Spoken/typed request → decision, reply honesty, latency, model calls. Real local model, fake native layer | Ollama |
| Sim eval | `scripts/sim_eval.py --split dev\|acceptance --output DIR` | Whole multi-step tasks in a simulated desktop driven by the real task loop and models; outcome read from world state | Ollama |
| Conversation eval | `scripts/conversation_eval.py --output DIR` | Small chat/memory probe (superseded in breadth by request eval) | Ollama |
| Live workflows | `scripts/human_eval.py --consent-from DATA_DIR --output DIR` | Real Safari/Finder outcomes through the real app | Accessibility + Screen Recording + owner at hand |
| Router held-out | `scripts/router_heldout.py --output FILE` | Aggregate routing score on hidden requests | Ollama |

Run model jobs as `nice -n 19 taskpolicy -b …`, one at a time when latency is being measured.

## Packages

Status values: `todo`, `doing`, `review`, `done`, `blocked`. Current status lives in
[HANDOFF.md](HANDOFF.md); this table is the stable definition.

| ID | Goal | Edit scope | Instrument and gate |
|---|---|---|---|
| P0 | Repair regressions left by the interrupted completion-verification change: 8 suite failures, without rewriting assertions | `companion/computer_task.py`, `companion/task_completion.py`, their tests | Suite green; "done" needs code-observed or quoted on-screen evidence; no extra model call where code already verified |
| P1 | Request eval: ≥70 dev and ≥70 acceptance turns of everyday requests across app opening, web, answers, conversation, reminders/notes/calendar, reading, screen questions, compound, windows, settings, risky, ambiguous, speech-like, cancel | new `scripts/request_eval*.py`, `scripts/request_cases_*.py`, `tests/test_request_eval.py` | Reviewer cannot make a wrong/no-op/unconfirmed outcome pass; tripwire blocks real processes and network |
| P2 | Sim eval: stateful simulated browser, files, form and notes apps rendered to pixels; real `ComputerTask` drives ≥24 tasks | new `scripts/sim_*.py`, `tests/test_sim_world.py` | Reviewer confirms fidelity to the real driver and that oracles read world state only |
| P3 | Baselines for P1 and P2 on committed HEAD and on the working tree | none (runs only) | Rows in RESULTS; every failure classified as product, harness or expectation |
| P4 | Router speed: cut output tokens per routed request (measured: ~70–90 tokens, ~1.0–1.3 s, ~60 of them fixed JSON keys and empty defaults; shortcut operations pay a second ~0.3 s coverage call) | `companion/request_plan.py`, `companion/ollama.py` (router call only), router tests | Request-eval p50 drops materially; dev and acceptance pass counts and router held-out aggregate do not fall |
| P5 | Request quality: fix the mechanisms behind the largest dev-split failure groups found in P3 | `companion/service.py`, `companion/request_plan.py`, `companion/quick_open.py` as needed | Dev failures in the targeted group fixed; acceptance does not fall; zero unauthorized actions |
| P6 | Task loop: fix the mechanisms behind sim-eval failures and remove wasted model calls per step | `companion/computer_task.py`, `companion/task_*.py` | Sim pass count up or steps/model calls down on matched runs; zero false successes |
| P7 | Live confirmation of P4–P6 with `human_eval.py`, three repeats | none (runs only) | Blocked until Accessibility is granted to the launching terminal and the owner authorizes screen takeover |
| P8 | Retire or quarantine legacy `scripts/live_*.py` suites that touch private apps or grade on replies | `scripts/live_*.py`, docs | No default suite opens Mail/Teams/Messages; legacy scripts clearly marked |

Order: P0, P1 and P2 in parallel (disjoint files) → P3 → P4 → P5 → P6 (P4–P6 share
runtime files, so strictly sequential) → P7 when unblocked. P8 any time nothing else
holds its files.

P0–P6 and P8 are done (see RESULTS). P7 waits on the owner.

## Phase 2: Siri first, computer use as fallback, model as last resort

Owner direction (2026-11-06): A11y is a Siri-style assistant with computer use. The model
understands wording and plans in terms of intents; code does the seeing, acting and
checking. A vision model is used only when nothing structured exists, and then it must
be a good one. Layers, cheapest first:

1. Exact code (arithmetic, time, opening apps) — exists.
2. Classic intents on real APIs (reminders, calendar, notes, files, Shortcuts, browser) —
   exists; add direct system controls.
3. Accessibility tree and menu bar: find controls and menu commands by name and role, act
   with accessibility actions, verify from native state.
4. Classic image recognition: Vision OCR text positions, pixel change detection, simple
   element detection, for apps with poor accessibility data.
5. Model as chooser over options layers 3–4 enumerated; free vision only when the list is
   empty.

| ID | Goal | Edit scope | Gate |
|---|---|---|---|
| Q1 | Direct system controls: volume, mute, brightness, dark mode, Wi-Fi, Bluetooth, Do Not Disturb, text size, as a typed operation executed by code (AppleScript/system calls on macOS, honest refusal or Windows adapter elsewhere); reads report current state; changes that are not reversible-by-the-user still confirm | new `companion/system_controls.py`, `companion/service.py` routing, `companion/request_plan.py` prompt/semantic_plan, tests | Request eval `system` family dev and acceptance improve; no screen task for these; zero unauthorized actions |
| Q2 | Code-first task loop: each step enumerates candidate actions (accessibility controls with name/role/state, menu commands, OCR text targets), acts by name through existing bridge calls (`--press`, `--menu`, `--targets`), and asks the model only to choose a candidate id or say "none"; free-form vision step only when the list is empty or every candidate was tried | `companion/computer_task.py`, `companion/task_*.py`, tests | Sim acceptance pass count up from 14/25 with false successes ≤ 3 and zero unauthorized; dev not worse; model calls per step not higher |
| Q3 | Last-resort brain: benchmark a stronger planner (colibri MoE, e.g. Qwen3.6-35B-A3B) for the cases Q2 still hands to the model | scratch only until accepted | Owner approves download; ≥ 20 tok/s on this Mac with the 4B vision model loaded; sim acceptance gain |

Order: Q1 and Q2 in parallel (disjoint files), each with an independent Opus review;
then Q3 sized by how often Q2 still needs free vision.

## Later candidates (not started, need an oracle first)

- Streaming the first words of a reply so feedback starts before generation ends.
- Saved-document editing, Preview navigation and voice entry workflows in live eval.
- Windows hardware runs; current Windows evidence is doubles only.
- Any fine-tune or default-model change: only after P3–P6 show a model limit rather than
  a harness or runtime defect.
