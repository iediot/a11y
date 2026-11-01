# A11y: notes for Claude Code

A11y is a desktop accessibility companion: a floating cloud mascot you type or talk to
("Hey Ally"). It answers, reads your Mac apps (Mail, Calendar, Reminders, Notes, Contacts,
Finder, Safari/Chrome pages, Shortcuts) and does on-screen tasks (open apps, click, type,
scroll). Python + PyQt6, with a Swift helper on macOS and native adapters on Windows.
macOS first, Windows second: anything shared must keep working on both.

## Current agent checkpoint (2026-10-30)

Read [docs/ai/HANDOFF.md](docs/ai/HANDOFF.md) before acting; its newest checkpoint
supersedes older status notes. [docs/ai/README.md](docs/ai/README.md) indexes the briefs.

- **Current scroll build `2385a6e664`: 1,096 tests OK (1 skipped).** Direction
  fixed with phased pixel gestures; latest half-window scroll distance/queue scope
  changes are unit-tested but not yet live-qualified. Last full live result was
  4/13 on the previous build. Next viewport-first Finder/Safari batch is prepared
  under `.data/live_eval/scroll-navigation-live-20261030/`, NOT running. Read the
  authoritative current HANDOFF checkpoint before launching anything.
- **Arch CUDA pilot also failed qualification** (2026-10-29): operations
  373→369/395, 30 critical regressions. No adoption/retraining. Qwen remains
  default. See ARCH_TRAINING_HANDOFF.md and current HANDOFF for active tests.
- **R3 qualification finished and failed the regression gate.** Artifacts:
  `.data/live_eval/recursive-10/summary.json` and `r3/comparison.json`.
  Operation improved 373→380/395, but action classification fell 358→349/395
  with 18 critical regressions. Completion: 23/36, 0 FP/13 FN. Qwen remains
  default; no adoption or automatic GUI tests. The fixed-build freeze is over.
- **Owner's latest instruction:** let shell batches run independently. Do not keep
  an agent turn alive with repeated polling, sleeps or status messages. End the
  turn; check results when asked or on an available completion notification.
- Qwen remains the default. R2 failed the regression gate. Holo2 is experimental:
  24/39 versus Qwen 17/39 targets on the crowded offline diagnostic; this is not
  end-to-end task success. Last complete unit run: **1,096 tests, OK (1 skipped)**.
- Live suites require current screen availability. Never open Mail, Messages,
  Photos, Contacts, Teams, WhatsApp or FaceTime in tests; never message anyone.
  Existing uncommitted changes must be preserved. Commit/push only when asked.

## Current priority

Make the **local** stack (Ollama, everything on this computer) capable, fast and
*predictable*. Today it sometimes works well and sometimes doesn't: the same request can
route differently, clicks miss, and steps stop early. Reduce that variance:
- Measure with real tasks on this Mac (success rate and seconds per task), not only unit
  tests.
- Move what can be deterministic into code and native tools: the accessibility tree, OCR
  targets, app adapters, AppleScript and EventKit.
- Constrain what the model has to decide. Keep prompts small and stable, so Ollama's
  prompt cache holds.
The Claude provider is an optional extra for demos; nothing may depend on it, and nothing
may call the cloud by default.

You may use the Codex CLI (`codex exec "<task>"`) alongside you: for a second opinion on a
diagnosis, a review of a diff, or a separate investigation. Treat its output like any
other contributor's: verify it, test it, and keep the ground rules.

## Ground rules (from the owner, always)

- Extend, don't rebuild. Preserve working features, access consent, privacy protections,
  keyboard access and reduced motion.
- Never give the model arbitrary shell execution, and never bypass target validation to
  go faster. The model proposes; code validates and executes.
- Sensitive changes (sending, deleting, purchasing, writing to apps) keep a confirmation.
  Plain navigation (open, switch, click to navigate) runs without one.
- **Code first.** If code can do it reliably (arithmetic, clock, reading mail/calendar,
  moving the mascot, opening a named app), code does it with no model call. The model
  understands the wording; code acts. Don't add hard-coded phrase lists where the model
  should interpret. Use a small, exact rule plus the model for everything else.
- Replies: one or two short, human sentences. No "Goal achieved", no "Looks done!",
  no narrating steps. Reading an email or note shows the full text.
- Every change ships with tests. Run the whole suite before pushing.

## Commands

```bash
python3 -m pip install -r requirements.txt -r requirements-voice.txt   # voice is optional
python3 a11y_desktop.py                         # run from source (data in .data/)
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests -q   # last verified: 1,096 tests, 1 skipped; native app/loopback access required
.venv/bin/python scripts/live_eval.py --runs 3  # real requests against a source run on this Mac
packaging/build_macos.sh                        # → dist/A11y.app and dist/A11y-macOS.dmg
dist/A11y.app/Contents/MacOS/A11y --self-test /tmp/self-test.json   # packaged-app check
```

Run from source, macOS grants Accessibility, Screen Recording and Microphone to the
*terminal app*, not to A11y. Test permissions with the built app. After rebuilding an
ad-hoc signed app, stale permission entries need `tccutil reset Accessibility com.a11y.companion`.

## How a request flows

`CompanionWindow` (`companion/desktop.py`) → `Companion.prepare_request` (`companion/service.py`),
which tries these in order and stops at the first that applies:

1. arithmetic / clock (`arithmetic.py`) → answered in code
2. "never mind" / "no" → drops the pending proposal
3. "read my (second) last email" → `mail_reader.py` (AppleScript), full text, no model
4. "move to the left / top right" → moves the mascot (MOVE_SELF)
5. bare verb ("Open.", "Click.") → asks "Open what?"
6. "click / tap / double-click X" → computer task (CLICK_REQUEST)
7. "close / quit / switch to X" with one running match → `simple_app_command`
8. "open X" → `quick_open.plan` (installed app → known web app → web search);
   a bare app name ("Microsoft Teams.") → `quick_open.bare_name` (exact names only)
   ("open YouTube and search for lo-fi" is one navigation to the site's results page,
   `web_apps.SEARCH_URLS`)
9. otherwise the model routes it: `Planner.interpret_request` (`ollama.py`) with
   `INTENT_SYSTEM` / `INTENT_SCHEMA` (`request_plan.py`). Code then maps mac-app
   operations (`mac_apps.py`), `read_mail`, and so on. Earlier requests are sent only
   when the request points back (`REFERS_BACK`); a built-in read that names another app
   ("notifications on Teams") becomes a computer task (`names_other_app`); reminder and
   event times arrive in `when` and a past time moves to its next occurrence. The router also
   gets `front_app` (the app and window in front), so "any notifications?" means that app.
   Typing needs a typing verb; `read_mail` needs a mail word (or a mail read just before).
   Router context uses reviewed `user_profile` and explicit detail/pacing settings.
   `Preferences` applies only settings the user chose; no request counters or suggestions run.

Conversation: routing stays free of history, but replies to plain conversation come from
`Companion.converse` (`CHAT_SYSTEM`) with short-term memory (`Companion.conversation`, in
memory only): what the user said, what A11y answered, task results. "What did I just say?"
goes straight there. Memory never keeps an email's text (first line only), drops screen/app content (`private`) when access is turned off; that
content never goes to a cloud model. Finished inspection tasks get their answer written
from the screen's OCR text (`screen_answer`).

`Companion.ask` turns the plan into a reply and at most one desktop action, validated
in `models.validate_response`. Plan flags `computed`, `full_text`, `read_aloud` and
`app_write` are set only by code and stripped from model output. "What's on my screen" uses
its own short prompt (`SCREEN_SYSTEM`). Multi-step goals run in
`computer_task.py` (observe → act → re-observe, with a bounded number of steps, Stop is
instant). Before the model, code takes the steps it can do exactly: Calculator keys, the
Activity view for notification goals, a click on the one place labelled X ("click on Chat",
from the accessibility tree or OCR), and the nth row of a list ("click the first email",
from the bridge's `lists`). `press_control` may only name a control in the current
observation (the schema's enum). Each step reads the window's clickable targets
(`DesktopBridge --targets`: buttons, links, tabs, fields, rows, named icons, web pages
included; names only, never values) and numbers them with the OCR lines no target covers
(`screen_marks`); the model answers `click_target` with a number and code clicks its center.
Pixel `click_point` remains for things neither lists. Enter in a search field needs no
confirmation in a read-only goal; elsewhere it still does. Measured (Ollama 0.34): prompt
prefixes are reused for text calls but not for calls with an image, so every image step
re-reads its whole prompt (~3.5 s for ~4k tokens); keep that prompt small. Actions run through `computer_use.py` and the platform bridge:
`desktop_context.py` + `native/DesktopBridge.swift` on macOS, `windows_desktop.py` /
`windows_input.py` on Windows. Screenshots come from `vision.py` (ScreenCaptureKit /
Windows capture) and are never written to disk.

## Where things live

| Area | Files |
|---|---|
| Window, mascot, voice wiring, settings | `desktop.py`, `mascot.py`, `faces.py`, `terminal_theme.py`, `settings_ui.py`, `setup_ui.py`, `readiness_ui.py` |
| Routing, schemas, validation | `service.py`, `request_plan.py`, `models.py`, `quick_open.py`, `app_names.py`, `web_apps.py` |
| Models | `ollama.py` (default, local), `claude.py` (opt-in cloud), `provider.py` (shared configuration, errors and redirect protection) |
| Mac apps in code | `mac_apps.py`, `mail_reader.py`, EventKit in `native/DesktopBridge.swift` |
| Computer use | `computer_task.py`, `computer_use.py`, `desktop_context.py`, `vision.py`, `windows_*.py` |
| Voice | `voice.py` (faster-whisper, tiny.en wake + small.en), `neural_voice.py` (Kokoro) |
| Setup & explicit preferences | `onboarding.py`, `preferences.py`, `setup_ui.py` |
| Consent & storage | `access.py`, `storage.py`, `paths.py` (data dir, saved AI settings) |
| Packaging | `packaging/a11y.spec`, `build_macos.sh`, `build_windows.ps1`, `entitlements.plist`, `self_test.py` |

## The companion's look

She is a small terminal window titled "a11y" (`mascot.py`): a text face, the last prompt and
the input, the same width open or collapsed. On macOS it is a real window with a normal title
bar like Terminal's (`mac_window.py`, AppKit through the Objective-C runtime with ctypes; red
closes the companion, yellow and green are off) and Terminal’s background blur while active. Replies
open in a shorter real window under hers (plain title strip; same top edge and the edge of the
side it opens from, right when there's room, else left), attached with
`addChildWindow:ordered:NSWindowBelow` so it never comes in front. Terminal stores colours in
calibrated RGB: convert them (`mac_window.to_srgb`) or her background reads darker than
Terminal's. On macOS the settings, setup and readiness windows use the native `macos` style
and menus are real NSMenus (`theme.native_mac`). Elsewhere, and in offscreen tests, both are painted in one frameless window with drawn
dots (`A11Y_FRAMELESS=1` forces that on macOS). Faces are multi-line text skins in `faces.py`
(slots `1` eyes, `2` mouth, `3` mark, per state; every line the same width, one cell per
character). Colours and font come from the default Terminal.app profile (`terminal_theme.py`
reads the archived NSColor/NSFont in Terminal's plist) or a built-in theme; the choice lives in
`<data>/appearance.json`. Moving is one stretch toward the spot and a settle
(`animate_companion_to` with `begin_stretch` / `stretch_to` / `end_stretch`); reduced motion
jumps and stops typing, blinking and spinners.

## Models

- **Ollama (default, everything stays local):** qwen3:4b-instruct for text,
  qwen3-vl:4b-instruct for vision (qwen3:1.7b / qwen3-vl:2b-instruct on low-memory machines). The setup window installs Ollama, the models and the
  voices. Choices are saved in `<data>/ai.json`.
- **Claude Sonnet 5.5 (opt-in):** Settings → Advanced → AI model → paste key → Use Claude.
  It uses the official `anthropic` SDK (2.x), streaming, structured outputs
  (`output_config.format`, schema cleaned by `wire_schema`), prompt caching on the system
  prompt, and the server-side refusal fallback beta. Effort is `low` for text and `medium`
  when a screenshot is attached (`A11Y_CLAUDE_EFFORT` overrides).
  - The key is saved in `<data>/anthropic-key` (0600). `ANTHROPIC_API_KEY` wins.
  - User keys (`sk-ant-usr-…`) need a workspace: fill in the Workspace ID field or set
    `ANTHROPIC_WORKSPACE_ID`. Otherwise every request gets HTTP 400. Normal keys are
    `sk-ant-api…`.
  - On a 400 the provider retries without the fallback beta, then with the schema in the
    prompt, remembers what worked, and prints Anthropic's reason to the log.
  - Privacy: requests go to Anthropic after a one-time disclosure (`cloud_ai`). Screen
    images need their own consent (`cloud_screen_vision`), also enforced in
    `vision.capture`. Local screen consent never counts for Claude. Mail, calendar and
    notes reads stay in code.
  - Never commit, log or print a key.

## Measuring on a real Mac

`scripts/live_eval.py` launches a source run with `A11Y_EVAL=1` (local Ollama forced, voice
off) and types each request through `companion/eval_control.py`, an owner-only socket that
cannot confirm or execute anything itself. It checks the result against the Mac (Safari's
page, Mail's selection, Calendar, the mascot's position) and writes `runs.jsonl` and
`summary.json` under `.data/live_eval/`. `--only TEXT` picks requests, `--compare` an earlier
summary. `--suite navigation` runs "take me there" tasks (Settings panes, Finder folders,
websites) checked by window title, Settings' pane header (OCR) or Safari's address; `--suite all`
runs both. Ollama runs greedy with a fixed seed, so repeated runs should match; a request that
passes sometimes is a bug to chase. It needs the terminal's Accessibility, Screen Recording,
Automation and (for the reminders case) Reminders permissions, and it takes over the screen.

## Debugging

- Every stage logs `A11y timing {...}` (event, ms, token counts, never prompt text or
  screenshots). From source it goes to the terminal; the packaged app writes
  `~/Library/Application Support/A11y/a11y.log`.
- The build ID tells you what the user is running: the packaged app shows the 10-character
  commit, a source run shows a file hash.
- Clicks on macOS: the bridge activates the target app, then sends move/down/hold/up from a
  `.hidSystemState` event source. Clicking an inactive app's window otherwise gets swallowed.
- `QT_MAC_SET_RAISE_PROCESS=0` (set in `a11y_desktop.py`): otherwise raising the mascot after
  an action activates A11y and takes keyboard focus from the app being worked in.
- Screen frames are captured at the display's pixel width and scaled to 1440 for the model;
  OCR reads the full-resolution frame. Every frame must be scaled the same way, or click
  guards (pixel hashes) never match and every click is refused as "changed".
- Real-world failures go in `tests/test_reported_failures.py`, with the reported
  wording.

## Gotchas that have broken builds

- A `QThread` that is still running when its window is destroyed aborts the process
  silently on Windows, and connecting to a closed localhost port takes about 2 s there.
  Never start a worker in a code path a test can end, unless it is waited for
  (`finish_workers`).
- Widget tests must not touch real apps: `tests/test_native.py` stubs
  `mail_reader.available` and the Swift helper catalog. Keep it that way.
- Packaged app: `certifi` provides HTTPS certificates; the spec copies `opentelemetry-api`
  metadata (the Anthropic SDK needs its entry points); onnxruntime and Kokoro are
  excluded on Windows. The self-test covers HTTPS, Qt, speech, voice and the Claude SDK.
  If you add a dependency, make the self-test exercise it.
- AppleScripts are compiled on macOS CI (`osacompile`). Pass user values as `argv`,
  never by splicing them into script text.

## Release

1. Bump `CFBundleShortVersionString` / `CFBundleVersion` in `packaging/a11y.spec`.
2. Push to `main`. GitHub Actions "Package apps" tests and builds the macOS and Windows
   apps, and runs each one's self-test.
3. When it's green: `gh workflow run package.yml -f release=v0.11.1` publishes the release with
   `A11y-macOS.dmg` and `A11y-Windows.zip`. Optional signing secrets: `MACOS_SIGNING_P12`,
   `MACOS_SIGNING_PASSWORD`.

Current state, results and open items: `docs/ai/HANDOFF.md`. More history and platform status: `docs/ai/CLAUDE_HANDOFF.md`, `docs/ai/ON_DEVICE_TUNING.md`.
