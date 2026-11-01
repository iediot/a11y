# A11y handoff: current checkpoint (2026-11-01)

Read this with `CLAUDE.md` / `AGENTS.md` (architecture, ground rules, commands). It holds only the
current state; everything older, with its evidence paths, is in
[HANDOFF_ARCHIVE_2026-10.md](HANDOFF_ARCHIVE_2026-10.md). Archived "next step" or "running" notes
are history, never instructions to restart a job.

## The owner's rules (non-negotiable)

- **Local only.** Nothing may call the cloud by default; Ollama on this Mac. Claude is an opt-in
  provider for demos. No cloud APIs in tests or training.
- **Ask before** anything that sends, deletes or changes data outside a test note/reminder/fixture.
  Never message anyone. Never open private apps in tests: Mail, Messages, Photos, Contacts, Teams,
  WhatsApp, FaceTime.
- **No hard-coded per-task phrase lists** ("we still dont wanna hardcode shit"). Fix the mechanism
  behind a class of failures; code-first only for exact, common actions; the model interprets.
- Never give the model shell execution; never bypass target validation. Sensitive changes keep a
  confirmation. AppleScript gets user values as argv only. Screenshots are never written to disk by
  the app. Never commit/log/print API keys (an old screenshot under `.data/live_eval` shows a note
  title that looks like a key).
- **Commits:** straight to `main`, **no Co-Authored-By / AI attribution lines**. Commit and push
  only when the owner asks (releases were requested explicitly).
  A session whose own instructions name a branch and ask for a pull request works there instead
  (no push to `main`); the no-attribution rule still applies.
- **Live tests take over the screen.** Run them only when the owner is away/agrees ("no testing now
  bro", "allow me to use my pc"). Never launch `/Applications/A11y.app` in tests. YouTube is blocked
  on this Mac (Cold Turkey). Test notes/reminders/documents contain "A11y test" + digits and must be
  cleaned up; TextEdit files the test created may be moved to the Trash from TextEdit's iCloud
  folder; any other deletion needs asking.
- Don't stop to wait for the owner when nothing is blocked. Windows is second priority ("skip
  windows for now").
- Heavy GPU work (training/eval) runs at background priority: `nice -n 19 taskpolicy -b …`.
  Note: in zsh, `N="nice -n 19"; $N cmd` does not split — use a bash script or a function.

## Using Codex in this project

`codex exec --sandbox read-only|workspace-write -C <repo> -o <final.md> "<prompt>" < /dev/null`
(without `< /dev/null` it hangs on stdin). `-c sandbox_workspace_write.network_access=true` allows
network/loopback. The Codex sandbox **has no Metal and cannot `nice`/`taskpolicy`**, and the full
unit suite aborts there (exit 134 in an AppKit menu test in `test_native`): GPU jobs, Ollama evals
and the whole suite must be run outside it. Review Codex diffs before committing: it has twice
removed safety guards and edited existing tests to match a behaviour change.

## Current state

- **Last measured build `e47962fab5`** (source run, local Qwen, units **1,143 tests OK**):
  - Finder/Safari 13-turn set (Finder offscreen selection, two complete four-turn local Safari
    workflows, Apple Support navigation): **13/13**.
  - Harder suite (`--suite harder`): **20/30**. A harness-only rerun (`harder5`) scored 18/30
    because TextEdit setup on this Mac fails (below).
  - Results: `.data/live_eval/after-pane-*-20261030/` and `harder4`/`harder5-20261030/` (local
    `.data`, not in a fresh clone; a missing file proves nothing about a run).
- Defaults unchanged: local Ollama `qwen3:4b-instruct` (text) and `qwen3-vl:4b-instruct`
  (vision). Every router fine-tune so far (R2, R3, Arch CUDA pilot) failed its regression gate
  and was not adopted; Holo2 is experimental only. Details: archive and
  [ARCH_TRAINING_HANDOFF.md](ARCH_TRAINING_HANDOFF.md).
- No batch, training job or app instance is meant to be running. Check before launching anything.

### What the measured build does (since the old 2385a6e664 checkpoint)

- Scrolling: phased pixel gestures (CGScrollPhase 1/2/4) instead of wheel events, half the
  window height per step (≤480 px); queued input cancels when the app or window changes.
- Finder facts include the selected names; a selection counts as a screen change, and "done"
  requires the requested name to be selected.
- Safari: `DesktopBridge --targets` reads the page address from the AXWebArea; link clicks keep
  `from_url`, and `completion_observation` reports `browser_navigation` (from/to/changed).
- System Settings panes come from the ExtensionKit catalog (`companion/settings_panes.py`);
  `open_settings_pane` opens one by ID and the full goal is checked right after it.
- Reminder titles are picked from the request's own words (`Companion.item_title`).
- Replies after a task (`ANSWER_SYSTEM`) are one plain sentence and don't narrate screen/URL.

### Open items

- Harder-suite failures: finder_info/finder_read (flaky), web_reference 2, web_screen_answer
  (answers with the paragraph, not the heading), preview_sidebar (thumbnails), lookup_dictionary
  (doesn't type the word), textedit_* (harness setup).
- TextEdit on this Mac answers open requests 15–70 s late while its iCloud Open panel is up, and
  files in `$TMPDIR` are refused; owner to check TextEdit's iCloud panel / sandbox.
  `live_harder.dismiss_test_alerts()` clears only alerts naming an A11y test file.
- Router noise seen live but not reproduced screen-free: "Look up nocturnal in Dictionary…" →
  `move_object`; "bring back the little page pictures" (Preview) → `highlight`; a reminders pair
  routed to `add_event`. The router still sets app_name "A11y" for System Settings requests.
- **New: held-out router set** `tests/data/router_heldout.jsonl` (165 hand-labelled requests, none
  in training data, prompts, tests or live suites; all of them reach the router). It covers those
  confusions: dictionary words vs moving her, showing a sidebar vs pointing at it, "add another
  one at 2pm" after a reminder vs an event, inspect vs change, a named app vs the front app.
  Score a local run (needs Ollama; never acts on the desktop):

  ```bash
  .venv/bin/python scripts/router_heldout.py --output .data/live_eval/router-heldout.json
  ```

  It scores the raw `Planner.interpret_request` plan (an opening folded into a task counts as
  that opening); the service's later code corrections are not applied. Not yet run on a Mac.

### Next step

Run the held-out router set on the default model to get a baseline, then fix the router
confusions it shows (prompt wording or a small exact rule, never phrase lists), rerun it, and
confirm with `--suite harder` when the owner allows the screen.
