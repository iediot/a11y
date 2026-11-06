<div align="center">
<img src="packaging/icons/A11y.png" alt="A11y app icon" width="128">

# A11y

A desktop accessibility companion with local AI, native app tools and a floating terminal-style face.
</div>

Type a request or speak to “Hey Ally.” A11y can answer questions, read supported app
content and carry out bounded desktop tasks. macOS is the primary platform; Windows
adapters exist, but their presence and unit tests do not establish hardware reliability.

The project is rebuilding its evaluation around real user outcomes. Implemented
features are not promises of reliable completion. Current evidence, limitations and
unfinished work live in [the evidence ledger](docs/ai/RESULTS.md) and
[current handoff](docs/ai/HANDOFF.md).

## Run from source

Use Python 3.10+ and, on macOS, Xcode Command Line Tools for the Swift helpers.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python a11y_desktop.py
```

Optional local voice recognition and natural speech:

```bash
.venv/bin/python -m pip install -r requirements-voice.txt
```

The setup window configures Ollama and installs the selected local models and voices
when requested. The standard models are `qwen3:4b-instruct` for text and
`qwen3-vl:4b-instruct` for vision; smaller configurations are available. Existing
model choices are preserved. Training experiments are not shipped defaults.

Windows users can run `run_windows.bat` with native Windows Python. See
[platform parity](docs/ai/PLATFORM_PARITY.md) for implementation and validation gaps.

## Interaction and permissions

The floating companion accepts typed requests; voice uses the same request path.
Settings expose explicit detail/pacing choices, keyboard access and reduced motion.
Ordinary navigation can run directly. Sensitive operations retain confirmation.
Computer tasks have an action budget and a Stop control; an accepted API call is
not proof that the task finished.

macOS permissions depend on the feature: Accessibility, Screen Recording,
Microphone, Automation, Calendars, Reminders and folder access. Source runs inherit
the terminal host's permissions; packaged permissions belong to A11y. Do not reset
existing permissions as routine troubleshooting. Missing permissions must be reported
as unavailable, rather than worked around.

## Capabilities and boundaries

| Area | Implemented path | Verification boundary |
|---|---|---|
| Chat and conversation | Local text model with bounded session memory | Check actual answers and privacy; route labels alone prove nothing |
| Arithmetic, clock, app opening | Deterministic code for exact requests | Check returned values or the actual target application |
| Supported Mac app reads/writes | AppleScript and EventKit adapters | Reads use native data; sensitive writes require confirmation |
| Desktop tasks | Accessibility targets, OCR, guarded clicks, native input | Fresh observations and independent final-state checks |
| Voice | Local Whisper recognition; local/system speech output | Voice end-to-end coverage remains separate from typed tasks |
| Windows | Win32, UI Automation, capture, OCR and input adapters | Real Windows hardware behavior needs separate evidence |

Local Ollama is the default inference provider. Claude is optional and explicitly
selected in Settings. Cloud inference and cloud screen sharing require separate
consent; local screen permission does not authorize sending images to a cloud model.
Private screen/app conversation is excluded from cloud context.

Local inference does not mean every feature is offline: optional factual/weather
lookups use web services, and setup downloads models. The application model never
receives arbitrary shell execution. Proposed actions pass local validation.

## Project map

```text
a11y_desktop.py       application entry point
companion/            UI, service, providers, validation and platform adapters
native/               Swift accessibility/EventKit and screen/OCR helpers
tests/                contracts, regressions and harness integrity tests
scripts/              human workflows, legacy diagnostics and model experiments
packaging/            app builds, signing, icons and PyInstaller configuration
docs/ARCHITECTURE.md   current execution boundaries
docs/ai/              operating rules, plan, testing contract and evidence ledger
```

Source data defaults to ignored `.data/`; `A11Y_DATA_DIR` overrides it. Packaged
builds use the platform's per-user application-data folder. State includes SQLite
settings/history, selected model configuration and cached helpers. Ordinary screen
captures remain transient. Evaluation evidence has separate fixture/privacy rules.

## Testing

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests -q
.venv/bin/python scripts/human_eval.py --list
```

The [test contract](docs/ai/TESTING.md) separates unit behavior, harness integrity,
actual local-model integration and live desktop outcomes. A stubbed response, a
“done” status or the desired words in a reply cannot prove a requested app change.

The new human-workflow runner uses owned loopback browser pages and Finder fixtures,
fresh facts, multi-turn requests and independent outcome checks. Live runs take over
the screen and require current authorization and existing OS/app consent. See its
`--help` and [implementation plan](docs/ai/PLAN.md) for scope and acceptance gates.
No private communications apps are used in tests.

Older `live_eval.py` suites are explicitly selected diagnostics, not the new baseline;
private-app-capable suites are disabled. Historical aggregate totals remain historical.
The router held-out set is reserved for qualification: do not use its requests to
write prompts, training data or special-case rules.

## Build

```bash
packaging/build_macos.sh
# Windows PowerShell: packaging/build_windows.ps1
```

Builds produce the application under `dist/`. GitHub Actions tests and builds macOS
and Windows packages and runs their self-tests. Packaging success does not establish
live task ability. Publishing, committing and pushing require an explicit request.

Contributors and coding agents start with [AGENTS.md](AGENTS.md), then the
[current handoff](docs/ai/HANDOFF.md). Claude Code imports the same canonical rules
from [CLAUDE.md](CLAUDE.md).
