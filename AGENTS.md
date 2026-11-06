# A11y: operating guide for coding agents

A11y is a Python/PyQt6 accessibility companion with local Ollama inference, native
macOS helpers and Windows adapters. The objective is useful, predictable human
workflows with short waits. A high unit-test count does not establish that objective.

## Start here

1. Read [the current handoff](docs/ai/HANDOFF.md): current work, evidence and next action.
2. Read [the implementation plan](docs/ai/PLAN.md) and [test contract](docs/ai/TESTING.md).
3. Check `git status --short` and active processes before editing or launching a job.
4. Read [architecture](docs/ARCHITECTURE.md) for the relevant boundary; inspect the code.

This is the canonical operating policy. `CLAUDE.md` imports it. HANDOFF is current
state, not a second policy. [The index](docs/ai/README.md) distinguishes references
from history. Archives are never instructions to resume jobs. Current user
instructions take precedence over repository guidance.

## Owner rules

- Preserve existing uncommitted work. Do not reset, overwrite or silently absorb it.
- Extend working features; preserve consent, privacy, keyboard access and reduced
  motion. Improve mechanisms; avoid broad rewrites without an observed need.
- Ollama is the application default. Application tests, evaluation and training use
  local models; remote credentials must never silently select a cloud provider.
  Claude is an explicitly selected optional application provider. The owner's request
  for Claude Code development delegation permits that development tool, not sharing
  private app data or changing the application's default.
- Cloud inference requires explicit `cloud_ai` disclosure/approval. Sending screen
  images additionally requires `cloud_screen_vision`; local screen consent never
  authorizes cloud sharing. Private app/screen conversation stays out of cloud context.
- Never give the application model arbitrary shell execution or bypass target
  validation. The model proposes supported intent; code validates and executes.
- Sensitive application actions (sending, deleting, purchasing and writes) retain
  confirmation. Plain navigation runs without a new confirmation.
- Never message anyone. Never open Mail, Messages, Photos, Contacts, Teams, WhatsApp
  or FaceTime in tests, including setup, cleanup and model-directed actions.
- Ask before changing data outside an owned test fixture. Test notes, reminders and
  documents contain `A11y test` plus digits. Clean up only run-created objects whose
  identity is recorded in an ownership manifest. Timestamps or broad name matches
  do not prove ownership. Do not sweep iCloud folders or quit user apps.
- Live tests take over the screen. Run only with current owner authorization and
  stop when the owner needs the computer. Never launch `/Applications/A11y.app` in
  tests. YouTube is blocked on this Mac and is not a useful test dependency.
- Ordinary screenshots stay transient. Optional evaluation evidence must be enabled
  explicitly, fixture-scoped and free of unrelated/private text. Never commit, log
  or print credentials. Do not inspect personal `.data` to build a benchmark.
- Pass user values to AppleScript as `argv`, never splice them into script source.
- Code handles exact operations (arithmetic, time, known app opening, native reads).
  The model interprets other wording. Do not add per-task phrase lists or train on
  benchmark requests to make a score improve.
- Product replies are normally one or two short human sentences; full email/note
  reads retain full text. Do not claim completion without observed evidence.
- Behavioral changes need tests of meaningful outcomes. Preserve existing assertions
  unless requirements changed and the reason is recorded. Run the complete suite
  before a push; a changed test expectation is not evidence of a fixed defect.
- Commit and push only when asked. Use `main` unless the session names another branch
  or requests a PR. No `Co-Authored-By` or AI attribution lines in commits.
- Do not keep a turn alive with repeated polling, sleeps or status messages while
  a shell batch runs. Let it run independently, end the turn when other useful work
  is done, and inspect on completion notification or when asked.
- Heavy GPU evaluation/training runs at background priority:
  `nice -n 19 taskpolicy -b ...` on macOS. Do not train before a credible baseline
  identifies a model limitation rather than a harness/runtime defect.

## Working method

Specify an observable outcome and independent oracle before implementation. Establish
a baseline, make a small general fix, repeat matched cases, then review adverse
cases. Record failures and uncertainty. The test contract defines scoring and gates.

Use independent agents when authorized. Give each an explicit file scope. A reviewer
must challenge false passes and safety boundaries; an implementer's report is not
its own acceptance gate. Preserve ownership of pre-existing dirty files.

Keep `tests/data/router_heldout.jsonl` requests out of prompts, training, handcrafted
rules and debugging examples. Router scores are diagnostic; they do not establish
that an application task succeeded. Keep development and acceptance cases separate.

## Runtime map

| Boundary | Files |
|---|---|
| Startup and UI | `a11y_desktop.py`, `companion/desktop.py`, `mascot.py`, `mac_window.py` |
| Interpretation | `service.py`, `request_plan.py`, `quick_open.py`, `app_names.py` |
| Local/optional cloud models | `ollama.py`, `claude.py`, `provider.py`, `gui_models.py` |
| Contracts and execution | `models.py`, `computer_use.py`, `desktop_context.py`, `vision.py` |
| Bounded task loop | `computer_task.py`, `task_prompts.py`, `task_completion.py`, `task_answers.py`, `task_fallbacks.py` |
| Native operations | `native/DesktopBridge.swift`, `native/ScreenBridge.swift`, `mac_apps.py`, `windows_*.py` |
| Consent and persistence | `access.py`, `storage.py`, `paths.py`, `preferences.py`, `onboarding.py` |
| Voice | `voice.py`, `neural_voice.py` |
| Verification/delivery | `tests/`, `scripts/`, `packaging/`, `.github/workflows/package.yml` |

Unqualified runtime files are under `companion/`. macOS is the primary validation
platform. Shared changes retain Windows contracts; doubles do not establish Windows
hardware behavior. Read source instead of assuming reference docs are current.

## Commands and environment

```bash
python3 a11y_desktop.py
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests -q
.venv/bin/python scripts/request_eval.py --split dev --output .data/eval/DATE/req-dev-N   # requests -> decisions, real local model, fake native layer
.venv/bin/python scripts/sim_eval.py --split dev --output .data/eval/DATE/sim-dev-N       # whole tasks in a simulated desktop, real task loop
.venv/bin/python scripts/human_eval.py --consent-from .data --output .data/eval/DATE/human-N  # live Safari/Finder; needs Accessibility and the owner's go-ahead
.venv/bin/python scripts/router_heldout.py --output .data/live_eval/router-heldout.json
packaging/build_macos.sh
dist/A11y.app/Contents/MacOS/A11y --self-test /tmp/a11y-self-test.json
```

Dependencies are in `requirements.txt` and optional `requirements-voice.txt`. Do not
install or upgrade them merely to inspect the project. Source data defaults to
`.data/`; packaged data uses the platform's application-data directory. Isolate
`A11Y_DATA_DIR` for tests and force local inference.

Source runs receive macOS permissions through the terminal host; packaged permissions
belong to the app identity. Do not reset a user's TCC grants as routine setup.
The full suite has previously aborted in a sandboxed AppKit test; a crash is not a
pass. Use the approved host environment and record unavailable checks. Widget tests
stub real app/catalog access and wait for workers; an unwaited `QThread` can abort
Windows. Keep Swift compilation out of widget fixtures. New dependencies must be
exercised by the packaged self-test.

Update [RESULTS.md](docs/ai/RESULTS.md) only from a finished identifiable run and keep
[HANDOFF.md](docs/ai/HANDOFF.md) short enough for the next session. Historical totals
must not substitute for current evidence.
