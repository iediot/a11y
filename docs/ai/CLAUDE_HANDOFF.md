# A11y handoff for Claude

> Historical architecture brief. Start with [the current handoff](HANDOFF.md) and
> [agent index](README.md) for owner instructions, active jobs and measured results.
> R3 qualification finished and failed its regression gate on 2026-10-28.
> Qwen remains default. Source development has resumed; see the current handoff.

## Main instruction: support macOS AND Windows

Extend this repository; do not rebuild the product or replace working components.
**Every desktop improvement must account for both macOS and Windows.** Implement
shared orchestration, schemas and UI once, behind explicit platform adapters. Add
the corresponding native implementations and tests for both systems. If a platform
cannot support a capability yet, report that accurately and provide a usable
fallback. Do not present macOS-only computer use as cross-platform functionality.

## Immediate assignment: make existing computer use faster and more reliable

The user wants the companion to actually operate applications, rather than explain
what the user should click. Improve the **tools, observations, schemas and execution
loop already implemented** so the assistant completes real tasks with less waiting
and fewer wrong actions. Start implementing after inspecting the current code.

Prioritize a coherent result over adding more disconnected features. This is not a
request for more app-name rules, canned responses or a different mascot. Keep the
LLM responsible for interpreting goals and choosing actions, with deterministic
validation and platform tools responsible for safe execution.

### What success should look like

- A read-only goal such as “open Teams in a new Safari window and see whether I have
  tasks” opens the requested destination and continues inspecting it. It does not
  stop after opening Safari, navigate to an unrelated composer, or ask whether to
  perform steps already authorized by the request.
- Ordinary navigation clicks run without an extra confirmation button. Sending,
  submitting, deleting, purchasing and sensitive changes retain appropriate review.
- A screenshot/observation is refreshed after actions. Missing accessibility labels
  do not prevent a properly grounded visual click.
- Users can stop a task promptly. Slow or cancelled inference must not trigger a
  late click, and old requests should not monopolize the local model indefinitely.
- Responses shown by the cloud and History agree. Submission is distinguished from
  observed success. “Show/mark where” highlights a target without activating it.
- Measure latency and targeting accuracy on real tasks on **both platforms**.
  Unit tests with doubled model/native responses do not prove live reliability.

## Product and stack

A workplace accessibility companion: floating cloud mascot, text interaction,
optional speech output, explicit accessibility preferences and desktop guidance. Normal use should happen through the mascot.

- Python / PyQt6 desktop entry point: `a11y_desktop.py`.
- Shared service and SQLite stores: `companion/service.py`, `storage.py`,
  `preferences.py`, `onboarding.py`.
- The application is desktop-only; no separate backend is required.
- Default data directory: `.data/`, configurable with `A11Y_DATA_DIR`.
- Windows launcher uses a local Windows venv and `%LOCALAPPDATA%\A11y`.
- Current local model used by the user: `qwen3-vl:8b-instruct` via Ollama.
- Keep provider/model configuration in environment variables/settings. No embedded
  API keys, automatic cloud fallback or unrequested model downloads.

## Read these files first

| Area | Files | Why it matters |
|---|---|---|
| Request routing | `companion/request_plan.py`, `service.py` | Intent, full-goal preservation, deterministic app/browser shortcuts, validation and verification |
| Task loop | `companion/computer_task.py` | Scope classification, fresh observations, image decisions, repair budget and review rules |
| Model transport | `companion/ollama.py`, `claude.py`, `provider.py` | Local inference, output budgets, schema handling, provider errors and timing |
| Desktop controller | `companion/desktop.py` | Qt workers, action scheduling, UI state, cancellation, shared cloud/History messages |
| Screen context | `companion/vision.py`, `desktop_context.py` | Permission state, transient captures, native adapters and click guards |
| macOS tools | `native/DesktopBridge.swift`, `native/ScreenBridge.swift` | AX/window metadata, app/browser actions, input, capture and OCR |
| Windows tools | `companion/windows_desktop.py`, `windows_screen.py` | Win32 app/window actions, Start-menu catalog and GDI capture |
| Input/execution | `companion/input_actions.py`, `computer_use.py`, `models.py` | Allowlisted input, observed target binding, executors and schemas |
| UI | `companion/mascot.py`, `overlay.py`, `settings_ui.py`, `theme.py` | Mascot, rounded menu, white pointer/highlight, compact settings |
| Notes/tests | `docs/ARCHITECTURE.md`, `tests/`, `scripts/evaluate_*.py` | Existing contracts, regression coverage and model evaluation fixtures (change history is in git) |

Read the actual code when notes and implementation disagree. The implementation
plan includes historical milestones and limitations, not a guarantee that every
previously attempted live scenario works.

## Current execution path and contracts

1. `OllamaProvider.interpret_request` produces a structured intent. Simple app and
   browser requests can use direct native tools. A small coverage check protects
   against silently dropping follow-up work.
2. `preserve_desktop_goal` routes compound desktop goals and direct click/control
   requests into `ComputerTask`.
3. The intent call also returns `goal_mode` (`inspect`, `draft` or `change`), carried
   on `start_task` into `ComputerTask`; only tasks started without a plan still make
   the separate classifier call. Inspection rejects compose/post/send/change
   proposals, including actual AX/UIA/OCR labels under model-selected coordinates.
   "Mark/show me where…" requests are forced to `highlight` by
   `request_plan.highlight_only` unless the user also asks to click/close/etc.
4. The task obtains current desktop metadata and one transient screenshot. macOS
   can return OCR text rectangles from the same frame. The app excludes its own
   windows; sensitive/protected app checks remain important.
5. The model receives the screenshot with native accessibility targets and OCR text.
   Deterministic tools resolve exact targets before inference when possible.
   `installed_apps` is sent as names; `launch_app` names resolve to catalog IDs in code.
6. The structured schema separates action responses from terminal responses:
   `status=action` requires one operation; `done`/`needs_user` require `action=null`.
7. At most two decision calls are allowed per observation, shared between correction,
   pause reconsideration and one recheck of a `done` reported when
   only opening/focus/navigation actions have run. Invalid proposals never execute.
   Ollama requests stream; Stop sets the task's cancel event, which closes the socket
   (Ollama then aborts generation) and no late action is proposed.
8. The GUI executes approved navigation automatically, then schedules another fresh
   observation. There is a 24-action bound and visible Stop control / `⌘.` shortcut.
9. Clicks retain capture age, display geometry, target-region hash and window checks.
   Input is rebound to a fresh observed app/window/focused non-password field.

Useful actions currently include `browse_url`, `launch_app`, `focus_app`,
`new_window`, `press_control`, `click_point`, `type_text`, `press_key`, `scroll`,
and `wait`, subject to platform support. Avoid assuming that schema availability
means every platform adapter implements an action.

## Important platform gaps

### macOS

- Uses Swift Accessibility/Quartz/ScreenCaptureKit helpers compiled with Xcode tools.
- Safari URL navigation distinguishes requested new windows and tabs. Preserve this.
- Native mouse clicks, observed AX presses, keyboard shortcuts, focused text input
  and vertical scrolling are implemented, with target checks.
- Request-time OCR now uses Vision `.fast`, with `.accurate` fallback specifically
  when the fast recognizer returns `VNErrorNotImplemented`.
- The new OCR helper compiles, but its offline synthetic-image probe failed in the
  restricted coding environment. **Native OCR runtime performance is unverified.**
  Accessibility targets and image decisions remain available.
- A11y settings highlights use a small selector over actual Qt section IDs and
  current Qt bounds, since screenshot capture intentionally excludes A11y's windows.
  Do not “fix” this by hallucinating controls from a screenshot that excludes them.

### Windows

- Win32 window metadata, installed-app resolution, Store/Start-menu apps, launch,
  focus, observed close and graceful quit are implemented.
- Request-time GDI screen capture and own-window exclusion are implemented. Permission
  checks must not take a screenshot. Known protected windows block capture.
- Implemented (Oct 2026 pass) in `windows_input.py`, `windows_desktop.py`,
  `windows_screen.py`, `windows_ocr.py`:
  - guarded `click_point` through SendInput in physical pixels of the captured
    monitor (capture now returns `display_id` = monitor handle and window ids/bounds;
    refuses a changed display or a point covered by the companion);
  - `type_text` (KEYEVENTF_UNICODE, focus re-checked every 20 UTF-16 units),
    `press_key` (Command shortcuts map to Ctrl) and wheel `scroll`, bound to the
    observed foreground window and focused non-password field; shells/terminals and
    registry/MMC are blocked;
  - UI Automation (comtypes): focused role/bounds and up to 45 named actionable
    controls of the foreground window, `press_control` via Invoke/Select/Toggle/
    Expand/LegacyIAccessible after re-enumeration and label/bounds checks. Without
    comtypes a Win32 focus fallback identifies classic Edit/RichEdit fields only;
  - `browse_url` for Edge/Chrome/Firefox found via App Paths (`browser:*` ids), with
    window/tab placement through fixed browser arguments, never a shell;
  - OCR with the built-in Windows.Media.Ocr engine (pywinrt), falling back to the
    image path when packages or language recognizers are missing.
  - Coordinates use Qt's space per monitor (`logical_rect`), which also fixes window
    bounds on scaled secondary monitors.
- Still missing on Windows: `new_window` (removed from the Windows task grammar),
  reading the browser's current URL after navigation, media playback control.
- **None of this has run on real Windows.** It was written against the documented
  APIs and pywinrt 3.2.1 stubs and is covered only by doubled tests. Validate with
  `scripts/benchmark_pipeline.py` and the scenarios below: DPI 100/150/200%, a
  secondary monitor, elevated target windows (UIPI blocks SendInput: reported as an
  error), Win11 Notepad (RichEditD2DPT/Document), Calculator UIA names and new Teams.

## Observed bottlenecks and suggested priorities

Real user logs showed capture around **0.14–0.18 s**, while image/prompt processing
often took **8–11 s per step**, followed by roughly **1–2 s** generating a short answer.
An earlier request generated 3,000 tokens and failed after about 83 s. The recent
task path caps output at 1,200 tokens and requests thinking off when supported.

These are observations from prior runs, not measured performance of the latest OCR
path. Logs emit `A11y timing` records without screen/prompt/model content.

1. **Benchmark the current pipeline before changing it again.** Measure cold/warm
   classification, metadata, capture/OCR, text/image inference, validation, execution
   and verification separately. Include p50/p95 step latency and task success rates.
2. **Make fast grounding genuinely work on both OSes.** Evaluate native accessibility,
   Windows UI Automation, native/available OCR and targeted image fallback. Do not
   bypass coordinate/focus validation for speed. Reuse native tools where they work.
3. **Improve tool/schema design.** Keep status/action combinations consistent, expose
   only platform-supported tools, avoid large generic grammars when a small tool
   set suffices, and give bounded actionable feedback for rejected proposals.
4. **Reduce inference overhead.** Review redundant intent/coverage/scope passes;
   preserve full goals while consolidating where justified. Current prompt packing
   removes old before/after snapshots, redundant visual window lists and unused
   receipt data; it puts the stable goal/app catalog before changing observations.
   Verify actual prompt-cache behavior instead of assuming it improves latency.
5. **Support prompt cancellation properly.** The current cancellation checks prevent
   subsequent execution, but the non-streaming Ollama HTTP request may continue
   consuming time/model capacity. Investigate cancellable streaming or transport
   cancellation with honest UI state and no late actions.
6. **Use structured browser information where appropriate.** Evaluate tools such as
   Playwright/CDP or browser-use behind a browser adapter, rather than guessing every
   web control from pixels. Check current browser compatibility and permissions.
   Preserve requested Safari window/tab behavior and the user's authenticated session;
   do not silently replace their browser/profile or move data to a hosted service.
7. **Review model suitability after tool improvements.** Compare installed/configured
   models with the same fixtures and latency measurements. Do not claim a larger
   model or another runtime is faster without measuring it on the target machine.

## Code-first steps (from live macOS timings, Oct 23)

Second live run (qwen3:4b-instruct + qwen3-vl:4b-instruct, M5 Pro): steps ~4.5–7 s,
rechecks ~1 s thanks to 97% prompt-cache reuse. Remaining costs: ~3,500–5,700 text
tokens per step at ~1.1 ms/token, plus ~1 s image encoding. Ollama starts Qwen-VL with
`--image-min-tokens 1024`, so images under ~1M pixels are upscaled and cost the same.

- `quick_open.py`: "open X [in <browser> window/tab] [and …]" is resolved in code —
  one installed app, else a known web app/typed address, else a web search. Vague
  targets ("the second email", "downloads folder"), ambiguous apps and playback
  follow-ups go to the model as before.
- `web_apps.py`: common web app addresses, used only when a browser/web is named.
- Task observations are compact strings (`text|x,y` OCR centers,
  `id|role|label|x,y,w,h` controls): about half the characters per step.

Live logs (two Teams runs, qwen3-vl:8b-instruct) measured ~16 s per step: a text
pass that produced no valid action on 19/19 steps (~5.5 s), an image pass (~10 s,
~6,800 prompt tokens) and 2.8 s model reloads after idle. Now:

- Compound goals that start with a named site/app (`browser_open`/`browser_search`/
  `launch_app` + remaining work) carry an `opening` on `start_task`; the first step
  runs without a model call (`ComputerTask.take_opening`). Bare names such as
  "teams" are not turned into guessed addresses; the model handles those.
- After `browse_url`/`launch_app` the loop recaptures every 0.6 s (≤4 times) until
  OCR text is stable instead of spending a model step on `wait`.
- OCR text and accessibility targets accompany every image decision. The model gets a 1280-px image copy (`A11Y_MODEL_IMAGE_WIDTH`); Ollama upscales smaller images to its 1024-token minimum, so going lower saves nothing there.
- `move_geometry` requests naming an edge/corner/center move without a second call.
- Text-only calls use `A11Y_AI_MODEL` and image calls `A11Y_VISION_MODEL`, so a
  small fast text model can handle routing/chat while the VL model does vision.

## Performance/reliability pass (after `0ce04dd`)

- Model calls per task start: intent + (shortcut coverage check) + per-step calls.
  The separate scope-classifier call is gone when the intent supplied `goal_mode`.
- Stable goal/catalog prompt prefix; app catalog sent as names.
- Streaming, cancellable Ollama transport (`OllamaProvider.stream`, `RequestCancelled`).
- `task.step.summary` timing per step (classify/metadata/capture/OCR/context size/each
  model call) and `action.executed` timing; no screen content is logged.
- One recheck when `done` is reported after only opening actions (Calculator/
  Notepad stopping after launch), regression tests in `tests/test_reported_failures.py`.
- Windows adapters listed under Important platform gaps → Windows.
- History records outcome notes shown under the mascot (highlight, stop, errors);
  a Qt test checks that every cloud message of a task is the same text in History.
  Desktop/screen exchanges are still kept out of persistent history by design, so
  after a restart History shows only non-screen conversation.
- `scripts/benchmark_pipeline.py`: cold/warm metadata, catalog, capture, OCR, intent
  and task-step latency (p50/p95) and Ollama prompt/eval counts on the real host.
  It never executes proposals.

These changes followed merged commit `f576bfc` (PRs #2/#3 plus earlier task/UI work):

- Corrected contradictory status/action schemas and added one bounded repair attempt.
- Added goal-scope checks, smaller task context, native OCR and image grounding.
- Unified cloud/History messages and removed duplicate action narration and false
  claims that ephemeral computer-task progress had been saved.
- Added own-settings highlights via Qt bounds and stronger highlight-only intent.
- Simplified settings and Advanced pages, added keyboard-accessible disclosures,
  scrolling and real resize support; removed the bottom status bar/divider.
- **Latest preference: Advanced stays at the bottom of navigation.**
- Startup is mascot-only. Saved screen access restores silently; it must not replace
  a conversation or open the “Screen vision is on…” prompt every launch.

Keep the current visual direction: dark settings, rounded popup with transparent
corners, small white pointer without the blue badge, and one thin white highlight.
The current mascot is a small terminal-style text face; see `CLAUDE.md` for native and frameless window behavior.

## Validation status: be precise

- Prototype-removal validation (Oct 26, 2026): 772 discovered Python tests. The
  sandbox-compatible run passes 752; 20 require unavailable AppKit menus, Notes
  AppleScript resolution or loopback socket binding. These tests remain in the suite.
  Changed Python/spec files pass `py_compile`. No live app or Windows hardware
  validation was performed for this cleanup.
- Streaming/cancellation was verified over real loopback sockets against a local
  NDJSON server (`tests/test_ollama_stream.py`): cancel during a 3 s silent prompt
  phase returns in <1 s and the server observes the disconnect. Not yet measured
  against a real Ollama process.
- Historical image-prompt size for a realistic step (150 installed apps, 30 controls,
  60 OCR lines) fell from 23.6k to 17.6k characters. Re-measure current prompt sizes
  and cache behavior with the benchmark before making new performance claims.
- Tests include actual Qt workers/UI sequencing, but model responses and OS action
  submission are doubled. They do **not** prove real Teams/Calculator/TextEdit success.
- The synthetic native OCR runtime probe failed here; both fast and accurate paths
  remain unverified on the user's host. No live speedup is established.
- Tools in this environment could not connect to the user's local Ollama listener.
- Real Windows input/capture validation is still required.

Add meaningful integration/regression tests for failures actually reported:

- Teams task/activity inspection must not select “Post in channel.”
- “Open Calculator and sum 2+2” must continue after app launch.
- “Open Notepad and write a summary” must create/focus an editor and draft text.
- Highlight a Terminal close button without closing it.
- Highlight A11y's Desktop tools using its actual settings geometry.
- Stop during inference/verification, foreground changes, stale targets, missing OCR,
  icon fallback, malformed outputs and permission pause/restart.
- Verify Windows equivalents rather than skipping them under a macOS-only success claim.

## Run and test

Quit the running companion from its mascot menu before relaunching. A background
launch command returning zero does not prove a new process loaded new code. Compare
the startup build ID in the log with `companion.instance.build_id()` when uncertain.

```sh
# macOS, foreground (useful while debugging)
A11Y_AI_PROVIDER=ollama A11Y_AI_MODEL=qwen3-vl:8b-instruct .venv/bin/python a11y_desktop.py

# App logs when using the user's nohup launcher
tail -f /private/tmp/a11y-desktop.log

# Automated checks
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests -q
.venv/bin/python -m compileall -q companion scripts a11y_desktop.py

# Stage latency on this machine (real capture/OCR/model; never executes actions)
A11Y_AI_MODEL=qwen3-vl:8b-instruct .venv/bin/python scripts/benchmark_pipeline.py --runs 5

# Synthetic model evaluation; does not operate the actual desktop
.venv/bin/python scripts/evaluate_computer_tasks.py --model qwen3-vl:8b-instruct
```

```bat
rem Windows: use native Windows Python, not a WSL GUI session
run_windows.bat
```

The Swift `ScreenBridge` also accepts `--ocr-image <fixture.png>` for an offline
OCR probe without screen capture. Use annotated/synthetic fixtures, and distinguish
that diagnostic result from actual application task completion.

## Collaboration and non-negotiables

- User wants commit/push **one-liners**; do not push directly. Inspect git status and
  preserve all working changes. Do not reset or replace teammates' work.
- Do not claim a task completed from app launch acceptance, a URL receipt or an
  invented model description. Verify the relevant outcome using fresh evidence.
- No arbitrary LLM shell commands, password entry, secret/clipboard capture or
  continuous screen recording. Do not persist screenshots/OCR unnecessarily.
- Preserve consent, access pause/restoration, keyboard alternatives, reduced motion and sensitive-action confirmations.
- If an external dependency is useful, integrate it behind a small tested adapter;
  do not add a new heavyweight agent framework just to wrap existing functionality.
- Start with a short concrete implementation plan, implement it, and report the
  measured results and remaining gaps honestly. The main goal is **faster, more
  capable, reliable computer use on macOS and Windows**, not another UI-only pass.
