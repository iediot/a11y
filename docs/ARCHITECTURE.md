# Runtime architecture

A11y is one Python/PyQt6 desktop application, with native macOS helpers and Windows
adapters. Ollama is the inference default; explicitly selected Claude is optional.
[AGENTS.md](../AGENTS.md) is policy; [the test contract](ai/TESTING.md) defines evidence.

## Request and execution boundaries

```text
typed / spoken request
  -> CompanionWindow + worker              desktop.py, voice.py
  -> Companion.prepare_request / ask        service.py
       exact supported operation in code   arithmetic.py, quick_open.py, mac_apps.py
       or constrained interpretation       request_plan.py, ollama.py / claude.py
  -> response / action validation          models.py, request_plan.py
  -> confirmation when required            desktop.py, computer_task.py
  -> one action or bounded ComputerTask    computer_use.py, computer_task.py
  -> platform validation and execution     desktop_context.py, vision.py, native / windows_*
  -> fresh observation / completion check  task_completion.py, task_answers.py
```

`a11y_desktop.py` configures resources, locking, Qt and opt-in source evaluation.
`desktop.py` coordinates UI/workers, voice, access, confirmations and task progression.
`mascot.py`, `mac_window.py`, faces and theme modules draw the terminal-style companion
and native macOS windows. Windows/offscreen rendering uses the shared Qt path.

`service.py` combines routing, short-term conversation, deterministic operations and
response assembly. Code alone sets trusted computed/full-text/app-write flags.
`request_plan.py` limits intent to supported operations and prevents grounding from
changing that intent. `models.py` validates response shapes and proposed actions.

`ComputerTask` observes, takes exact native steps when possible, otherwise requests
a constrained step, validates, executes and observes again. Steps are bounded and
cancellable. Completion, answers and menu/Settings fallbacks live in the `task_*`
modules. `ComputerUse` executes one approved proposal and produces an observation
receipt. Submitted input does not prove the user's goal happened.

## Native and model adapters

`DesktopContext` tracks access generations, observation age and authorization against
current context. macOS uses `MacDesktopBridge`, Swift Accessibility/input/EventKit
and argv-based AppleScript readers. `ScreenVision` captures/transforms transient
frames and validates pixel targets; `ScreenBridge.swift` uses ScreenCaptureKit/Vision.
Windows supplies Win32/UI Automation, capture/OCR and SendInput adapters.

`provider.py` defines config/errors. `ollama.py` holds local transport, provider
selection and the shared `Planner`; `claude.py` imports that planner. `gui_models.py`
adapts experimental protocols. Weather/factual lookups have separate outbound HTTP
paths: local inference does not make the whole app offline. Provider choice does
not bypass consent or action validation.

## State and privacy

`storage.py` persists reviewed profiles, explicit settings, consent and eligible
history in SQLite. `paths.py` separates bundled/source resources and data.
`instance.py` prevents concurrent app instances sharing stores. `access.py` separates
app awareness, screen, voice and optional cloud consent. Unreviewed onboarding drafts
stay out of context; preferences apply explicit choices.

Conversation is bounded and in process. Screen/app content is private; pausing access
removes private context and cloud conversation filters it. Full mail text is shown,
but memory keeps only its first line. Ordinary screenshots are transient. Optional
source-eval evidence has additional fixture/privacy constraints.

## Coupling and test boundaries

`desktop.py`, `service.py` and `computer_task.py` remain large coordinators. Task mixins
share task state and sometimes import helpers back from `computer_task.py` at call
time. Plans, observations and receipts mostly use runtime-validated dictionaries.
These constraints call for focused fixes backed by reproduced user failures.

`eval_control.py` is source-only opt-in instrumentation. It submits UI-path requests
and diagnostic state; restricted fixture confirmation is not a general executor.
A harness independently establishes outcomes. Legacy `live_*` scripts have uneven
safety/oracle behavior and remain diagnostic until audited. Doubles do not prove live
task success or Windows hardware compatibility. Current claims belong in
[RESULTS](ai/RESULTS.md).
