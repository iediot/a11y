# A11y handoff: state of the project (2026-10-30)

Read this together with `AGENTS.md` / `CLAUDE.md` (architecture, ground rules, commands). This file is
what a new session needs on top of those: the owner's rules learned the hard way, what was done
recently, measured results, and what is open.

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

## Newest update — 2026-10-30 afternoon (Claude; supersedes "Exact next step" below)

- The prepared viewport-first batch RAN (owner authorized) on build `2385a6e664`:
  **9/13 eligible turns** (was 4/13), 3 complete workflows. Calibration passed. Apple Support 2/2;
  local web passed catalog/scroll-read/plan-again in both attempts, failed "Planning desk" twice;
  Finder selection 1/3. Results: `.data/live_eval/scroll-navigation-live-20261030/summary.json`.
- Finder failures (2, both false "done"): the click on item089 was right, but a selection changes no
  OCR text, so `check_last_click` marked it "nothing changed" → `menu_pick_step` fallback chose
  View › Sort By › Name → rows moved → item088 clicked. Fixed: Finder facts now read the selected
  names (≤5, names only, argv-bound script); `screen_signature()` includes the selection; MET requires
  `finder.selection` to hold the requested name.
- "Planning desk" failures: the click reached `/`, but MET had no before/after address. Click receipts
  in browsers now keep `from_url`; `completion_observation` adds `facts.browser_navigation`
  (from/to/changed + a quotable evidence line).
- Tests: `tests/test_selection_and_link_evidence.py`. Full suite on build `42323b9141`:
  **1,104 tests OK (1 skipped)**. Not yet live-measured; not committed.
- Same 13-turn scope rerun three more times (owner: "test or train but run shit"):
  | build | result | what it showed |
  |---|---|---|
  | `42323b9141` | 5/13 | Finder 3/3 (selection fix works). Web 0/8: the first link click already opened the catalog, but Safari's AXWindow has no AXURL, so `from_url` was never recorded; heading click → menu fallback chose History; every later turn started off-site. |
  | `c1105b8d25` | 6/13 | `goal_met` now also checks the full goal after a link click that changed the address; still no URL → same History cascade. |
  | `52dddfafa8` | **13/13** | `DesktopBridge --targets` reads AXURL from the AXWebArea when the window has none. First web turn 40 s → 14 s. Replies narrated ("The screen shows … The URL …"). |
  Folders: `.data/live_eval/{selection-link,link-goal,page-url}-live-20261030/`. The runners skip native
  calibration (morning calibration passed; the helper change after it touches only the targets walk).
- `ANSWER_SYSTEM` now says the steps already ran, gives two one-line examples, and bans talk of
  screen/URL/window; `screen_answer` also sends `last_step`. Screen-free Qwen check on the live cases:
  3/4 clean ("The equipment catalog is open.", "The collection point is Ridge 6000.", "item089 … is
  selected."); "Planning desk → start" still says it failed (model can't tell `/` is the start).
  Giving it `browser_navigation` facts or `previous_page` made replies worse; reverted. Script:
  scratchpad `answer_check.py` pattern (ComputerTask.screen_answer with a real Ollama provider).
- Answer-style build `7f6cc3da57`: 12/13 (one Finder pass lost to MET `missing: "}"`). Fixed:
  `completion_result` treats punctuation-only `missing` as empty (real evidence still required).
- Build `f3af3418f1`: 13-set 11/13 (Apple Support stopped after clicking the apple.com search result:
  the link-click early check fired on a compound goal). Fixed: the early check needs the goal to name
  the clicked link. Harder suite (first run of the day): **15/30**.
- Build `a5db3b3a7e`: 13-set 12/13; harder **19/30**. Fixes measured here:
  - Reminder titles: router wrote the whole request / the time phrase as title. `Companion.item_title`
    now picks the title from an enum of the request's own word spans (free text dropped digits of long
    test numbers). Screen-free Qwen: 7/8 exact. 5 orphan "Put A11y test …" reminders deleted (test items).
    `live_conversation.clean_items/test_reminders` match the unique marker anywhere in the name.
  - Mascot top corners: eval reported the client rect (a title bar lower than the frame she parks
    16 px from the edge → 44 px vs 40 px tolerance). `eval_control` now reports `frameGeometry()`.
- Next (running): `companion/settings_panes.py` reads Settings panes from
  `/System/Library/ExtensionKit/Extensions/*.appex` (extension point `com.apple.Settings.extension.ui`);
  new action `open_settings_pane` (catalog ID only, `open x-apple.systempreferences:<id>`, navigation,
  macOS only); `ComputerTask.settings_pane_step` lets the text model pick a pane name (enum) once per
  task when Settings is in front. Screen-free Qwen: 12/12 effective. Cause: "Go to About … memory"
  clicked About inside General's content (not sidebar) → correctly needed confirmation → stalled.
  Chain: `.data/live_eval/panes-*-20261030/`, harder → `.data/live_eval/harder3-20261030/`.
- Panes build `870ee690ad`: 13-set 11/13, harder 18/30. `open_settings_pane` fired correctly (About,
  Sound, wifi) but she kept clicking inside the pane ("Memory") → confirmation stall. Fixed: `goal_met`
  checks the full goal right after `open_settings_pane`. Local-web turn 4 hit the History fallback a
  third time after clicking the answer line itself; fixed: `menu_pick_step` first asks `still_missing`
  once and finishes when the goal is met. The failed Finder turn had item089 selected (correct) but the
  harness requires a scroll receipt and the file was already visible — harness strictness, not app.
  Harness note: harder-suite turns judged while `task_status=running` with a pending confirmation can
  still pass (settings_*); the 13-set runner excludes those. Next chain: `after-pane-*`, `harder4`.
- After-pane build `e47962fab5` (units 1,143 OK): **13-set 13/13; harder 20/30**; lookup_memory now
  passes in 10.5 s ("The installed memory is 24GB.").
- TextEdit cases failed all day at harness setup: stale TextEdit alerts ("The document “A11y test …
  packing.txt” could not be opened. The file doesn't exist.") block every Apple Event; each timed-out
  open queues another alert. Six dismissed by hand; `live_harder.dismiss_test_alerts()` now clears only
  alerts naming an A11y test file before TextEdit setup. Harder-only rerun → `harder5-20261030`.
- `harder5` (same build, harness fix): 18/30 — TextEdit still failed. Real cause is this Mac's TextEdit,
  not A11y: it handles open requests 15–70 s late (or not at all) while its iCloud "Open" panel is up,
  and files in `$TMPDIR` give "You don't have permission"; late opens of deleted fixtures then raise
  more alerts. Reproduced with `osascript open`, `open -a TextEdit`, and a file in TextEdit's own
  container. Left TextEdit quit with no alerts. Owner check: TextEdit's iCloud open panel / sandbox.
  Also in harder5: reminders_pair 2 was routed to add_event (Calendar) — title correct, operation noise.
- Router noise, not reproduced screen-free: "Look up nocturnal in Dictionary…" → `move_object` and
  "bring back the little page pictures" (Preview) → `highlight` in the live run; with a mocked desktop
  (Dictionary/Preview in front) both route to `computer_task`. Probe scripts: scratchpad
  `route_check3.py` pattern (Companion + Ollama provider + Mock desktop_context with installed_apps).
- Still failing in harder: finder_info/finder_read (flaky), web_reference 2, web_screen_answer
  (answers with the paragraph, not the heading), preview_sidebar (thumbnails), textedit_* (harness
  setup: `osascript open` in TextEdit times out after 30 s), lookup_dictionary (doesn't type the word).
  The router still sets app_name "A11y" for System Settings requests (harmless when Settings is front).

## Current checkpoint — 2026-10-30 (authoritative)

This checkpoint supersedes every older running-state note below. The owner most
recently requested these Markdown updates and a handoff to Claude; **no new tests
or app launches were started for that request**. Preserve all existing uncommitted
changes; no commits/pushes or model adoption have been authorized.

### Actual state

- Current source build: **`2385a6e664`**. Latest full suite: **1,096 tests in
  301.454 seconds, OK (1 skipped)**. Exit 0; all production/test hashes still
  match that run. Results: `.data/live_eval/scroll-navigation-validation-20261030/`.
- No A11y source app, local test/training worker or caffeinate process was running
  at the checkpoint audit. **Nothing continued overnight automatically.**
- Next live batch is **prepared, NOT launched**:
  `.data/live_eval/scroll-navigation-live-20261030/`. No `progress.json` exists.
  Native helper is compiled and hashes match `native-preflight.json`; script,
  source/binary checks and missing-authorization refusals passed.
- Qwen remains default: local Ollama `qwen3:4b-instruct` text and
  `qwen3-vl:4b-instruct` vision. No new training, downloads or adoption are queued.
- Codex's earlier continuous Goal still reports `blocked`; subsequent work happened
  in owner-triggered turns. A detached shell job is not a continuously working
  agent. Do not promise automatic follow-up without a working completion mechanism.

### What changed in the app

1. **Scrolling direction:** unphased mouse-wheel events were being reversed on this
   Mac, where Mos 4.2.1 has reversal/smoothing enabled. A controlled comparison
   measured old wheel/control **0 correct / 9 wrong / 3 top-boundary no-ops**,
   versus coherent gesture **9 correct / 0 wrong / 3 expected top no-ops**.
   Evidence: `.data/live_eval/scroll-delivery-gesture-v2-20261030/`.
   Mos/preferences were not changed. This is fixture delivery evidence, not a
   universal causal proof or end-to-end success.
2. `native/DesktopBridge.swift` now sends pixel gesture began/changed/ended packets
   using **CGScrollPhase 1/2/4**, with 20ms spacing and focus/window/bounds/title/
   nonsecure-input checks before each packet. AppKit NSEvent phases use different
   raw values (1/4/8): the first experimental candidate got this wrong; an offline
   object test caught it BEFORE GUI use. Do not run the superseded v1 candidate.
3. Latest follow-up changes scroll distance from fixed 60px to **half the validated
   native window height, capped at 480px**, retaining overlap. Seven native packet/
   distance tests pass, including real CoreGraphics-to-AppKit conversion without
   posting input. **The larger distance has not yet been validated live.**
4. `companion/computer_task.py`: every queued input now cancels if the app bundle
   changes; queued scrolling also cancels on a different window title. Same-app
   search Enter may survive a title update, retaining normal review/field guards.
   Five new queue-destination tests pass. Windows input/schema remain unchanged;
   all 23 Windows-input tests passed.
5. Earlier fixes remain: exact whole-value completion evidence; full-goal checks
   after reading/navigation actions; repeated-action recovery safely returns
   needs_user instead of unsupported done; scroll-progress feedback; native named
   link mismatch rejection; mascot native frame/client offset fix; 100-entry
   in-memory Up/Down sent-message recall with draft restoration and Mac keypad
   arrow flags; bounded conversation memory/privacy handling. See dated entries
   and the existing regression tests for details.
6. One intermediate 1,089-test run failed a transient mascot peak-width sample.
   The test now seeks the actual Qt animation midpoint, then resumes the real
   timer; width/position/size/completion assertions remain. **No app animation
   behavior was changed to make that test pass.** Later full runs passed.

### Last actual task result: still not qualified

`.data/live_eval/scroll-gesture-live-20261030/` completed all 13 turns on the
PREVIOUS build `3e25dfffef`: **4/13 eligible passes**, Finder selections **0/3**,
complete four-turn local Safari workflows **0/2**, Apple Support **2/2**.
`qualification.json` records each requirement; original rows/evidence retained.

- Finder made seven small scrolls, selected item040 rather than requested item089,
  or timed out after 13–18 scroll receipts. The file-interaction classifier still
  does not reliably establish the requested filename's identity.
- One local-web turn lost foreground to iloader; keep that interruption separate
  from clean model-only failures. Other turns switched repeatedly to unrelated
  Home/Finder, clicked irrelevant targets or exhausted time/action limits.
- Larger scroll distance and stricter queue scope are responses to those traces,
  **not proven cures**. General target choice, app grounding, planning and recovery
  remain open. Do not increase scores by loosening checks or accepting submissions
  as task completion.
- Gesture submission median was about421ms versus101ms for the old wheel, so there
  is **no measured speed gain yet**. Total task time/success must improve in live
  tests. Keep the actual comparison limitations.

### Exact next step

Run the prepared batch on unchanged build `2385a6e664` once the current screen
session is authorized. It first runs two model-free disposable Finder viewport
checks using a byte-identical copy of the current production helper. Both must
move correctly on all three down gestures, with no reversed/unknown transitions
and confirmed cleanup. Only then does it run:

- Three consecutive Finder offscreen-file selection attempts.
- Two COMPLETE four-turn local Safari workflows: catalog, scroll/read guide,
  return home, find the random collection point again.
- Two Apple Support navigation repeats.

Scope: 13 task turns plus two viewport checks, local Qwen, context8192, seed41;
45-minute global / 20-minute stage bounds. The two-turn browser prefix used in
older diagnostics is NOT a complete workflow. Native paths, selections, randomized
page facts, truthful task status and absence of pending actions/timeouts determine
success. Keep app/native source fixed throughout; never adopt a model automatically.

Entry point (requires actual screen authorization, not merely the flag):

```bash
nice -n 19 /usr/sbin/taskpolicy -b .venv/bin/python \
  .data/live_eval/scroll-navigation-live-20261030/run.py --screen-authorized
```

Launch independently with redirected logs and `start_new_session=True` (or a
properly detached shell), as earlier launches did. The runner owns its scoped
`caffeinate -di`. Before launching, check absent `progress.json`/STOP/process,
current build/full-unit result and helper hashes. Never overwrite or duplicate a
run. Status/results go in that directory; inspect on completion/request rather
than keeping an agent turn alive polling. A STOP file requests an owned clean stop.
Exact-title-only calibration cleanup avoids resolving unrelated Finder aliases;
previous global alias cleanup timed out, then was safely recovered.

Owner wants continuous, completion-driven work and explicitly allows caffeinate.
They most recently said “run continuously please you have access to everything”,
then immediately redirected this turn to updating the handoff. Follow the current
request; do not launch a new test as part of this documentation handoff. Respect
screen availability for subsequent GUI use. A direct yes/run reply to a concrete
screen/test question is authorization for that batch: **do not demand a magic
phrase or repeatedly ask within an already authorized batch/session**. Silence or
an unrelated status question is not screen availability.

### Training, data and rejected approaches

- **All previous router candidates remain rejected.** R3 completed300 steps:
  operations373→380/395, action flag358→349/395,18 critical regressions;
  completion23/36. `.data/live_eval/recursive-10/summary.json` is final, not active.
- Arch pilot completed100steps/53min, validation loss0.252397. Returned adapter
  verified112 finite tensors/3,670,016 parameters; selected-logit loss/gradient
  parity passed8CPU/12CUDA cases. Conversion/merge/Q4 transfer and paired406-row
  Ollama qualification also FINISHED: operations373→369/395, action358→354,
  30 critical regressions,0 parse errors. Test alias `a11y-router-arch-pilot-test`
  retained, NOT adopted. Do not repeat conversion/training. Full pins/paths and
  reports: [ARCH_TRAINING_HANDOFF.md](ARCH_TRAINING_HANDOFF.md).
- Arch access last established via `ssh edi@100.74.40.62`, directory
  `/home/edi/a11y-training`; LAN address was192.168.1.143. Never request/store a
  password. Current remote process state was not rechecked in this docs turn;
  last documented compute jobs finished. Transient user systemd services with
  `--collect` disappear after exit; saved reports are authoritative. tmux attempts
  did not reliably survive logout. Keep Arch on AC/awake for authorized work.
- Original Qwen3-VL-4B weights were explicitly approved, downloaded and verified
  on Arch only. Short and full-prompt BF16/NF4 forward/backward memory probes
  passed; the corrected full probe allocated AdamW states but made ZERO optimizer
  updates. Peak allocated roughly6.38GB/reserved6.94GB. This is capacity/parity
  evidence, NOT convergence or policy quality. No visual model training occurred.
- Public-data inspection:288 Mind2Web train rows (16sites/45trajectories),277 usable
  positive boxes but full-page/viewport alignment unresolved and no scroll labels;
  bounded AgentNet sample6Darwin trajectories/12scrolls, too small/narrow for a
  reliable policy dataset. Browser scroll location must be retained; A11y's
  primitive still targets the window centre, not an explicit model-selected area.
  See `.data/training/gui-data-survey/` and dated entries. No bulk downloads or
  training should start merely because GPU memory fits.
- Holo2 remains experimental: crowded native localization24/39 versus Qwen17/39
  targets, not task success. Other diagnostic sets did not favor Holo2 overall;
  hybrid memory/latency costs remain. Qwen defaults unchanged.
- Rejected experiments remain unadopted: shortened policy prompt; repair grammar
  that produced UP while saying DOWN; filename-identity wording/extractors that
  still missed requested numeric names or invented/shortened names. The opt-in
  `A11Y_SCROLL_REPAIR_SCHEMA` experiment is no longer active. Do not restore these
  from archived `.data` copies without new evidence.

### Workspace and restart notes

All pre-existing dirty changes are preserved. Main current files to inspect:
`native/DesktopBridge.swift`, `companion/computer_task.py`,
`tests/test_mac_scroll_gesture.py`, `tests/test_tool_queue_destination.py`,
`tests/test_native.py`; earlier movement/history/completion/grounding changes span
additional files in git status. Local `.data` artifacts are not included in a
fresh clone; missing files do not imply a job is running or a gate passed.
Checkpoint audit: `.data/handoff/20261030-current/{state.json,git-status-before.txt}`.

To run A11y for the owner from this checkout (currently NOT running):

```bash
A11Y_AI_PROVIDER=ollama .venv/bin/python a11y_desktop.py
```

Check for an existing instance first; do not launch the packaged app in tests.
No commits/pushes, private-app tests or messages. Keep local inference, sensitive
confirmations, consent, target validation, reduced motion and Windows support.

## Dated checkpoint history (superseded by the current checkpoint above)
### Full1096 OK; viewport-first live qualification prepared, not launched — 2026-10-30
New build`2385a6e664` full suite FINISHED: **1,096 tests in301.454s,
OK(skipped1)**, exit0/source-test hashes unchanged. Full log/result:
`.data/live_eval/scroll-navigation-validation-20261030/`. Current counts updated
AGENTS/CLAUDE/ai README; historical results preserved. Half-window scroll step
and queue scope fixes have unit coverage; live task benefit still unmeasured.
Prepared `.data/live_eval/scroll-navigation-live-20261030/` for next batch:
first two model-free owned Finder viewport trials using byte-identical current
production helper; each must show correct movement on all3 down gestures, zero
wrong/unknown transitions, and confirmed cleanup. Native gate then permits same
13taskturns:3Finder selections,2complete four-turn local Safari workflows,2Support
repeats. Local Qwen/seed41/context8192 unchanged.45min global/20min stage bounds.
Native helper recompiled, hashes verified and copied for isolated calibration.
Exact owned-title-only final cleanup now avoids resolving unrelated Finder alias
paths (previous20s cleanup timeout); script compiled offline. Content/name/root
checks constrain fixture removal. No unrelated app/settings/window modifications.
Preparation/hash/missing-screen-authorization refusals passed for both scripts;
no live progress file exists and no GUI/inference/training job running. Source/
model defaults unchanged after full pass; source held fixed when comparison starts.
Fresh availability required for this new Finder/Safari batch. Goal incomplete;
last live task result still4/13 on previous build, not a result for this candidate.
### Live qualification finished4/13; scroll distance and queue scope corrected; units running — 2026-10-30
13-turn batch FINISHED on build3e25dfffef in1,451.834s. Actual eligible results:
Finder0/3 selections; COMPLETE four-turn local Safari workflows0/2; Apple Support
2/2 repeats. Only2 local catalog-navigation turns plus2 Support turns passed
(4/13 total). Goal NOT achieved. Per-requirement qualification.json and original
rows/evidence saved in `.data/live_eval/scroll-gesture-live-20261030/`.
Finder attempt1 made7scrolls then selected item040 instead of requested item089;
attempts2/3 timed out180s after18/13scroll receipts. Local web turn2 attempt1
lost foreground to iloader; classify that interruption explicitly, not as clean
model-only failure. Other web steps switched repeatedly to unrelated Home and
failed full navigation/fact retrieval. Useful general planning still unresolved.
A native direction fix alone did not solve task completion. Follow-up candidate:
scrollDistance computes HALF validated window height, cap480px, preserving overlap
and reducing tiny60px steps. Distance derived from native bounds, never model or
task phrases; finite positive height/1..480 packet validation. Same legitimate
CG1/2/4 phases and per-packet app/window/bounds/title/nonsecure guards retained.
queued_step had exemptions allowing scroll_to and press_key across app changes.
Now EVERY queued input cancels on different bundle; scroll also cancels changed
window title. Search Enter can retain existing same-app title-update behavior
and normal sensitive/field review. No global preferences/Mos changed; Windows
implementation/schema untouched. Native owner ancestor-hit changes preserved.
Tests: native platform suite expanded to7, new5 queue-destination cases; those,
existing computer-task/scroll-progress and23 Windows-input tests all pass. Native
checks postZERO events. Larger amount/queue behavior NOT live-qualified yet.
Launched independent full suite PID76252 at nice19/offscreen/local, scoped
caffeinate,10min bound/source-test hashes:
`.data/live_eval/scroll-navigation-validation-20261030/`. App/native/tests fixed
until completion. Latest full success remains1,089 on OLD build3e25dfffef until
new result. No new GUI batch/training/adoption queued; fresh screen availability
needed for subsequent live batch. No commits or discarded owner changes.
### Owner authorized full live batch; Finder/Safari qualification running — 2026-10-30
Owner answered YEA to pending explicit run-batch/current screen question.
Launched independently PID72734 on unit-validated build3e25dfffef:
`.data/live_eval/scroll-gesture-live-20261030/`.13turns:3 consecutive Finder
scroll/select attempts,2 full four-turn local Safari workflows,2 Apple Support
repeats.45min overall/20min per stage, scoped caffeinate, local Qwen text/vision,
context8192 seed41; no training/downloads/adoption/default change/private apps.
Native helper hashes and full1089-test exit0/same-build result verified; no prior
app/batch/STOP/process present. Existing target/consent/focus/secure-input and
sensitive confirmation guards retained. App/native source fixed while running.
Status/progress.json, driver.log, stage logs, per-turn evidence and summary.json
saved in directory. Let batch run independently; no repeated agent polling or
duplicate jobs. Actual results pending; milestone not proven by launch/unit pass.
### Integrated scroll build full suite1089 OK; live qualification ready — 2026-10-30
Second full-suite batch FINISHED: **1,089 tests in296.047s, OK(skipped1)**,
exit0, production/test hashes unchanged, build`3e25dfffef`. Results/unit.log in
`.data/live_eval/scroll-gesture-validation-2-20261030/`. Native packet regressions
and actual full helper compilation included. First failed full run preserved;
only animation test sampling corrected before this pass, no app animation change.
Updated current unit counts in AGENTS/CLAUDE/ai README; historical counts intact.
13-turn focused live batch prepared at `.data/live_eval/scroll-gesture-live-20261030/`:
three Finder offscreen selections, two COMPLETE four-turn local Safari workflows,
two Apple Support repeats; same seed41/Qwen models/context8192 and independent
native paths/randomized content checks. Installed models verified through local
Ollama metadata only; no inference/downloads or changed defaults. Source native
helper prepared successfully without GUI, matching build3e25dfffef; source and
binary hashes saved in native-preflight.json. No new live batch launched; fresh
current screen availability remains
required. No test worker from full run remains. Goal still incomplete until real
Finder/browser criteria pass on integrated build. No commits or training.
### Full suite1089 failed one animation sampling check; rerun launched — 2026-10-30
First integrated-build full suite FINISHED:1,089 tests in277.986s,
FAILED1failure/1skip, source/test hashes unchanged, build3e25dfffef. Failure:
NativeTests.test_moving_stretches_toward_the_spot_then_settles_there sampled
widest411px vs expected>=500px. New5 native scroll tests passed. No full-success
claim; latest full green still1,084 on previous build. Original moving test
PASSED isolated rerun (0.871s). Test sampled transient peak every10ms while
background load can skip frames. Changed only test to pause Qt animation, seek
actual midpoint, capture real widget width, then resume real timer/finish signal.
Kept same peak-width threshold, finalposition/size, completion and stopped-stretch
assertions; additionally assert stretching at midpoint. Updated test passes.
No app animation code changed or failure assertion weakened.
Launched second full run independently PID72213, same production build,
new test hashes, offscreen/local/nice19/10min bound/scoped caffeinate:
`.data/live_eval/scroll-gesture-validation-2-20261030/`. App/native/tests fixed
while it runs. First failed artifact preserved. No live GUI batch started.
Prepared `.data/live_eval/scroll-gesture-live-20261030/`:13turns=3Finder selections,
2FULL four-turn local Safari workflows (navigate,scroll/read,return,planagain),
2AppleSupport repeats. Exact independent native/content checks and eligible
completion criteria retained. Requires passing second full-suite result on
same build AND fresh screen authorization; missing flag refusal verified.45min
batch bound,20min/stage,STOP/source/focus guards; no auto adoption/training.
Do not substitute prior two-turn browser prefix for complete workflow success.
### Correct scrolling measured; gesture fix integrated; full units running — 2026-10-30
Corrected six-case native fixture batch FINISHED in138.407s. Direction-aware
results: old baseline0correct/9wrong/3top no-ops; matched unphased pixel control
same; phased gesture9correct/0wrong/3expected top no-ops across two fixtures.
Inspected before/after gesture evidence: initial item000..018, down to item012..030.
This proves direction/delivery for those fixtures, not full task success.
Median native submission: baseline101.495ms, unphased459.195ms, gesture421.15ms.
Gesture currently slower per call; total task efficiency unmeasured. No model
inference/training/default change. verdict.json saved in v2 run folder.
Final alias-based Finder cleanup timed out. Recovered by checking exact six
owned window-title counts (all0), verifying all840 disposable filenames/content
and removing only owned temp root. Unrelated Finder window preserved. Original
progress retains cleanup_error; cleanup-recovery.json records actual resolution.
Integrated measured coherent pixel gesture into native/DesktopBridge.swift with
small scrollPacket builder, CGScrollPhase1/2/4, same60px amplitude,20ms phase
spacing. Retained original input guards and rechecks actual same focused window,
app/bounds/title/nonsecure focus before EVERY packet. Existing owner native hit
ancestor changes preserved. Windows input/shared schema unchanged.
Added5 object-only platform regressions in tests/test_mac_scroll_gesture.py:
real CoreGraphics->AppKit phases, only changed packet moves/opposite directions,
continuous/no horizontal movement, destination retention, invalid inputs rejected.
All5 PASSED in6.508s, no screen events or app reads. New full-suite batch launched
independently PID72084, nice19, offscreen Qt/local provider,10min bound,
source/test hashes and scoped caffeinate. Status/results/unit.log:
`.data/live_eval/scroll-gesture-validation-20261030/`. App/native/tests fixed while
running; docs safe. Latest full success remains1,084 on OLD build until this ends.
No new live batch queued. Final integrated build still needs actual repeated
Finder offscreen selections, complete Safari workflows and Apple Support repeats
with current screen availability; goal NOT complete.
### Owner explicitly instructed run now; corrected six-case Finder batch launched — 2026-10-30
After the pending specific six-trial screen/launch question, owner explicitly
instructed run shit bro. Treated this direct launch instruction as authorization
for this prepared batch now; do not falsely quote it as literal screen-is-free
reply. Avoid another redundant permission loop. Launched v2 probe independently
PID71615, scoped caffeinate -di -w PID71616. Six Finder-only cases,
3 variants x2 repeats, both directions, per-wheel receipts, direction-aware
native row scoring, cropped evidence, partial-case progress,15min internal bound,
STOP/source/focus/secure-field guards and owned-fixture cleanup retained.
All source/binary/object-check hashes/states and absent run/STOP/process verified
before launch. No inference, training, private apps, messages or default changes.
Original app/native source remains unchanged and frozen during comparison.
Status/results/batch.log/launch metadata at
`.data/live_eval/scroll-delivery-gesture-v2-20261030/`.
This authorizes this batch, not indefinite future GUI access. No automatic
adoption or other test queued. Let shell run independently; inspect terminal
results on completion/next user request without repeated polling.
### V2 compiled/preflight passed; direction-aware probe scoring added — 2026-10-30
All3 corrected variants compiled successfully; worker71439/caffeinate71440 ended.
Verified source/binary/original native hashes, prepare-only success and missing
screen authorization refusal. No live progress.json exists. Current screen
availability still unconfirmed; no GUI/inference worker or source app running.
Continued offline probe improvement: fixture-only scorer derives visible row
rank from exact owned native filenames and compares EACH event to preceding
observation, not initial image. Correct direction/wrong direction/unchanged/
unknown are separate; any movement alone cannot establish correct scrolling.
Replayed prior saved native observations: baseline0correct/3wrong/3unchanged;
session repeat1 same, repeat2 zero correct/6wrong. Unknown data stays unknown.
This is verifier evidence, not new task/viewport success. scoring-replay.json
and score.py in `.data/live_eval/scroll-delivery-gesture-v2-20261030/`.
Probe now persists partial case and direction/wheel state, native submission ms,
receipts, and directional checks; failed partial trial cannot count complete.
No app/native changes, no adoption, no new screen events or training. Ready six
Finder trials after explicit current screen availability; use v2, not invalid v1.
### Offline platform check caught/fixed wrong phase enum; v2 compiling — 2026-10-30
Performed useful offline validation after continue message; no GUI access.
Original gesture candidate hashes/preflight passed, but object-only Swift/AppKit
check FAILED phase-equality precondition (exit133, zero events posted). Measured
actual conversion then exposed candidate bug: NSEvent.Phase raw1/4/8 sent into
CGEvent field became AppKit began/ended/cancelled, not began/changed/ended.
CoreGraphics SDK CGScrollPhase explicitly defines began1/changed2/ended4.
Original candidate superseded BEFORE any GUI run; verdict/object evidence saved.
Prepared separate `.data/live_eval/scroll-delivery-gesture-v2-20261030/`, preserving
old files. Isolated candidate now uses CGScrollPhase enum; corresponding matched
unphased control also uses correct enum for changed-delta branch. Object-only
check PASSED all6 up/down phase objects: continuous1, correct signed60px changed
delta/zero begin/end delta, CG1/2/4 recognized by AppKit as1/4/8. ZERO events posted.
No app/native source edits or inferred delivery success. Compiling3 corrected
variants independently PID71439, scoped caffeinate -di -w PID71440.
Logs/status/hash provenance in v2 directory. Fresh current screen availability
still unanswered; no live job is queued. Original v1 probe MUST NOT be launched.
Next run v2 only after compilation/preflight and explicit screen availability.
### Oct30 status audit: milestone unfinished; no app or test worker running — 2026-10-30
Owner asked if finished. Rechecked actual saved states and process inventory:
controlled batch FINISHED8cases/0viewport changes; routing batch FAILED7/8
on focus guard with cleanup; gesture candidate compilation FINISHED3variants,
but NO GUI progress.json exists, so six candidate trials never started. No
matching app/probe process running now. The prior owner-requested source launch
PID71062 had successfully started buildff94628a53 with local Ollama on Oct29;
it is no longer running. Do not imply overnight testing or continued background
agent work occurred. Candidate NOT applied; no proven efficiency gain. Full
scrolling/offscreen milestone remains incomplete. Next decisive work is prepared
six-case gesture diagnostic after fresh current screen availability. No automatic
restart of the app, test, training or Goal was performed during status audit.
### Gesture variants compiled; no efficiency claim or live result yet — 2026-10-29
Compile-progress FINISHED with all3 variants in
`.data/live_eval/scroll-delivery-gesture-20261029/`. Worker70942 and scoped
caffeinate70943 exited. No GUI progress.json exists: six Finder trials have
NOT run; fresh availability question remains unanswered. Nothing running or
queued. Production app/native unchanged; this is an isolated candidate only.
Owner asked whether solution is more efficient. No measured efficiency or
end-to-end gain exists yet; do not claim it. Candidate targets correct/predictable
scroll delivery around wheel interception. It introduces three20ms phase waits
per scroll; whether fewer failed/repeated actions improves total task time must
be measured through actual workflows. Current evidence is direction reversal
plus active Mos reverse/smoothing settings, not causally proven Mos attribution.
### Routing batch stopped by focus guard; Mos reversal found; gesture diagnostic compiling — 2026-10-29
Routing/direction probe FAILED after7/8 cases in138.294s: owned Finder fixture
lost focus. Safe guard stopped input, fixture_cleaned=true; worker70473 and
caffeinate70474 ended. Not a complete eight-case pass. Saved per-wheel receipts
submitted=true. Baseline and session up moved list downward; down did nothing at
top or returned toward top. Target-PID variant erratic (movement first repeat,
none second); no routing variant adopted. Verdict saved in that run directory.
Read-only process/preferences inspection: Mos4.2.1 running; reverse=true,
reverseVertical=true, smooth=true, smoothSimTrackpad=true. Global natural scroll
also true. Do NOT simply invert A11y's signs globally: utility interception is
a stronger explanation than an established bare-OS mapping fault. Matching Mos
4.2.1 source commit0ff06a4e787abd5d8568e31865481e75abe550b4 registers tail annotated
session tap and reverses/smooths mouse wheels; it passes coherent trackpad phases.
Source consulted publicly, no project/private data uploaded. Mos/settings not
changed, quit or disabled. Source: github.com/Caldis/Mos/tree/4.2.1/Mos/ScrollCore.
Prepared isolated `.data/live_eval/scroll-delivery-gesture-20261029/` diagnostic:
baseline versus matched pixel packets with/without legitimate began/changed/ended
phases. Same60px amplitude as6-line control, both directions x2 repeats,4s settle
before measuring fresh fixture, native focus/secure-field guards before each
packet. No Mos-specific marker spoofing or preference changes; no app/native
edits. Compiling three variants independently PID70942/caffeinate70943;
no screen access/inference during compile. Fresh availability needed before new
GUI batch. This is a causal hypothesis/prepared candidate, NOT proven fix.
### Fresh screen availability received; guarded routing/direction batch launched — 2026-10-29
Owner explicitly confirmed screen free. Launched follow-up Finder-only probe
independently, PID70473, scoped caffeinate -di -w PID70474; four variants
x two fixtures, both directions, three wheels per direction, per-wheel receipts
and visible-label observations.15-minute internal bound, STOP/source/focus
checks and owned fixture cleanup retained. No inference or private apps.
Status/results/launch/log: `.data/live_eval/scroll-delivery-routing-20261029/`.
Verified hashes and absence of previous run/STOP/process immediately before
launch. App/native source fixed until this batch ends. No adoption or model/
default/training changes. Let batch run independently; no repeated polling.
Completion result and actual movement still pending. No other live batch
queued; any later batch needs fresh availability under current owner rule.
### Follow-up wheel variants compiled and preflight passed — 2026-10-29
All four routing/direction binaries compiled successfully; compilation worker
69896 and scoped caffeinate69897 ended. Verified every source/binary digest,
original native digest, --prepare-only success and missing --screen-authorized
refusal before any fixture creation. No GUI progress file or probe run exists.
`.data/live_eval/scroll-delivery-routing-20261029/preflight.json` records checks.
User said continue now; under the explicit per-batch availability rule this
is not fresh screen availability. The specific follow-up screen question remains
pending. No duplicate compilation/test, app edits, inference or training started.
Prepared next action on explicit availability: run probe.py --screen-authorized
independently with scoped caffeinate and retain per-wheel receipts/evidence.
### Controlled wheel batch finished:0/8 moved; follow-up prepared — 2026-10-29
`.data/live_eval/scroll-delivery-controlled-20261029/progress.json` is FINISHED,
8cases in82.875s, fixture_cleaned=true. All four variants x two repeats had
unchanged native visible filenames after every wheel; inspected baseline saved
before/after images also unchanged. Worker69623/caffeinate69624 ended. No
native-call error occurred (bridge raises on nonzero/error), but experiment
omitted per-event receipts, so do not invent them. No variant adopted.
All trials requested down from top; this does not eliminate direction reversal
or event-routing explanations. Prepared separate follow-up directory
`.data/live_eval/scroll-delivery-routing-20261029/`: same guarded native input,
baseline/HID-source/session-tap/validated-target-PID variants, both directions
in opposite orders across two repeats, per-wheel submitted receipt and labels.
Original source untouched; future GUI phase needs fresh screen availability.
Compiling variants independently; compilation itself has no app/screen access.
Full goal incomplete; no training/default/model changes or commits.
### Fresh screen availability received; controlled Finder wheel batch launched — 2026-10-29
Owner explicitly answered yes the screen is free. Launched the prepared
Finder-only delivery experiment independently, PID69623, scoped
`caffeinate -di -w` PID69624. Status/results/log/launch metadata:
`.data/live_eval/scroll-delivery-controlled-20261029/`.
All original/variant native-source and compiled-binary hashes verified before
launch; absent prior run/STOP/process checked. Four variants x two fresh owned
Finder fixtures, three wheels each,15-minute internal bound and fixture cleanup.
Native focus/target/secure-field validation retained. No inference, training,
private-app tests, model adoption or default change. No app/native edits while
comparison runs. This batch measures wheel delivery/viewport movement only;
Finder task and browser completion remain unverified. Do not poll repeatedly
or start a duplicate; inspect saved terminal state at completion/next turn.
The existing Codex Goal API still reported blocked when checked after the
owner answer; the actual screen blocker is resolved and authorized work has
continued. Do not claim the tool's Goal status was resumed automatically.
### Continuous Goal blocked on fresh screen availability — 2026-10-29
Three consecutive automatic Goal turns reached the same actual impasse:
explicit screen availability remains unanswered, controlled Finder probe has
not run, and no further useful offline step was identified before delivery
measurement. Process checks confirm no probe running. Goal marked blocked to
stop automatic retries; NOT complete. Owner can unblock by answering the pending
Finder-only screen question. No GUI phase or background inference is queued;
owned caffeinate assertion already stopped. Existing source changes preserved.
Prepared experiment: `.data/live_eval/scroll-delivery-controlled-20261029/`.
Do not claim scrolling fixed, end-to-end workflows verified, or final milestone
complete. Required native movement,3 Finder turns,2 complete Safari workflows,
Apple Support repeats and full units on final build remain outstanding.
Audit: `.data/live_eval/continuous-work-20261029/screen-blocker-audit.json`.
### Direct file-reference extraction finished; live delivery experiment awaits screen — 2026-10-29
The final goal-only direct extraction diagnostic FINISHED, build unchanged
`ff94628a53`: `.data/live_eval/file-reference-extract-20261029/`,16 synthetic
development goals, local Qwen3-4B/8192/seed7,12/16 exact role/name sets,
zero transport errors. Correctly extracted item089 from the reported compound
scroll/select goal and Beta from the negated selection case. However, shortened
Packing manifest.txt to manifest.txt, returned malformed/nonliteral names for
Supplies and Café.txt, and treated ordinal first file as a literal filename.
Literal-substring constraints caught only two of those four errors. NOT adopted;
these results do not establish a reliable identity guard or task success.
No app/native edits, actions, screenshots, training, adoption or downloads.
Current full validated build remains `ff94628a53`,1,084 tests OK/1 skip.
No inference worker remains. This Goal turn made measured offline progress and
rules out adopting this extractor as-is. Further prompt-only experiments are
not a substitute for the actual wheel-delivery diagnosis. Requested fresh
availability explicitly for the prepared Finder-only experiment (up to15min).
No GUI phase is queued or authorized until that answer arrives. Goal remains
active; next safe dependent action is the controlled native probe on approval.
Stopped the owned one-hour caffeinate assertion while waiting; future batch
assertions remain scoped to actual owned work. No other assertions affected.

### Offline event encoding verified; file-identity classifier audit running — 2026-10-29

The paired classifier audit FINISHED during this Goal turn: baseline9/18 correct,
allowing all8 wrong-target lookalikes; identity-wording candidate13/18 correct,
still allowing5 wrong targets including the main item008/item089 confusion.
No valid-target refusals or transport errors in either group; one baseline open/
select error improved. Candidate wording NOT adopted: it does not solve the
reported mis-selection. This is a classifier diagnostic, not a click/task score.

Started the next bounded offline diagnostic without app changes:
`.data/live_eval/named-selection-extract-20261029/`, exec session86350. Sixteen
goal-only extraction cases ask whether interaction is solely named selection,
then return exact requested names. The model sees NO observed candidate name;
code checks returned strings literally occur in the goal (Unicode normalization),
and compares to human labels. Includes rejected alternatives, negated open/select,
multiple names, ordinal/ambiguous requests, mixed actions and intermediate reading
goals. This tests a possible additional consistency restriction, never a grant of
new permission or automatic action. Four-minute bound, local Qwen3-4B, no training
or GUI; progress/results/batch.log and launch handle there. Review actual mode/name
errors before deciding whether to implement anything. Current app remains the
validated `ff94628a53`; no production prompt or target guard was changed.

That extraction diagnostic also FINISHED during this turn:14/16 exact mode/name
responses, no errors. Both misses returned other/names=[]: the main compound
scroll-and-select-item089 goal and the negated-selection/choose-Beta case. Thus
this scope-gated extractor would not protect the reported numeric mis-selection;
it is NOT implemented/adopted. Results argue for testing direct goal-only item/
interaction extraction without an initial scope decision, with literal/native
identity checks that only restrict existing proposals and never grant permission.
Do not present14/16 as task success or as a working selection safeguard. Both
diagnostic workers are terminal; no inference job remains. Goal turn made measured
offline progress; live wheel/viewport verification still requires screen availability.

First automatic Goal continuation ran successfully. Previous Goal turn made
progress by enabling the continuation contract/awake assertion. GUI availability
is still unconfirmed; no screen events, capture or app opening were performed.

Inspected the installed CoreGraphics SDK definition: nil event source is supported
and scrollWheelEvent2Source is the non-variadic API. A local Swift object-only
probe produced signed nonzero deltas: line -6 maps point -60; pixel -6 maps point
-6 and continuous=1. Object position setters also retained coordinates. Zero
events posted. This rules out a zero-delta object-encoding explanation under the
tested creation path, not delivery failure. Source/results at
`.data/live_eval/scroll-event-encoding-20261029/`; no native/app edits.

Saved Finder traces independently show FILE_INTERACTION_SYSTEM returned select
for wrong observed item008 while the goal explicitly requested item089. Current
helper classifies a gesture but does not explicitly establish target identity;
interaction=other also currently passes through rather than rejecting a click.
Do not claim that code path blocks wrong-target selections today.

Launched a local-only paired text diagnostic in
`.data/live_eval/file-interaction-identity-20261029/`:18 synthetic development
cases × current prompt / same prompt plus a general referent-identity condition,
36 decisions, greedy Qwen3-4B/context8192. Includes true matches, lookalikes,
numeric/punctuation/Unicode distinctions and required intermediate folders.
No task-specific examples added to production prompts; no active app/source
changes, training or execution. Gold labels/report provenance are explicit;
classifier scores alone cannot establish actual click blocking or task success.
Six-minute bound, STOP/source change abort, caffeinate -di scoped to the worker.

This shell job is tracked by exec session23140, also saved in launch.json;
progress.json/results.json/batch.log there. On continuation consume that handle
or inspect terminal state; do not start a duplicate or repeatedly poll. Next
review whether identity wording rejects wrong targets without refusing valid
ones before considering any general safety/decision-path change. The four
compiled native Finder variants remain ready but unrun pending fresh availability.

### Owner requested continuous completion-driven work; Goal enabled — 2026-10-29

Owner explicitly requested continuous work without manual “continue” prompts,
using independent shell jobs and continuing after completion. Enabled a scoped
Codex Goal for native scrolling/offscreen recovery: diagnose controlled wheel
delivery, validate a general fix, repeat Finder and complete local-browser tasks,
retain Apple Support results and pass full units. This is bounded by the Goal's
runtime controls/blockers; a shell process alone is not an agent wake-up guarantee.
No recursive CLI agents, extra cloud inference or messaging setup was launched.
Long batches still save status/results and run independently; do not busy-poll.

Owner additionally authorized `caffeinate -di` whenever needed. Started one bounded
one-hour owned awake assertion; PID/command in
`.data/live_eval/continuous-work-20261029/awake.json`. Scope future assertions to
owned work; stop this assertion when the Goal completes or screen work is deferred.
Power permission is NOT fresh screen availability. The compiled Finder probe
is ready but has not run; current explicit availability remains required.

Official Goals documentation was checked: Goals maintain a thread objective and
can continue at idle boundaries, stopping for success, user control, budget or
blockers. Actual create_goal succeeded in this thread. No model/default change.

### Constrained-repair candidate rejected; validated build restored; native probe ready — 2026-10-29

Paired `scroll-repair-choice-20261029` comparison FINISHED: baseline six needs_user;
candidate six action proposals, no false completions/errors in either group.
Raw inspection shows ALL six candidate actions were scroll UP while their messages
still said scroll DOWN. This is not useful recovery. Candidate REJECTED, not adopted.
`verdict.json` records direction counts and rejection. Archived experimental source
and schema tests in that run folder, then removed only this experiment from active
app/tests. Current app/native build is EXACTLY `ff94628a53`, matching the completed
1,084-test success. Prior owner changes and the false-completion fixes remain intact.
`A11Y_SCROLL_REPAIR_SCHEMA` is no longer an active app feature. Historical notes
below describe the experiment, not current available behavior.

All FOUR isolated wheel-probe binaries compiled successfully at nice19, no GUI or
model inference. `scroll-delivery-controlled-20261029/compiled.json` records their
source/binary hashes; compile-progress.json finished. Probe uses each compiled
variant's actual binary; original native source remains untouched. Ready for
eight bounded disposable Finder trials (four variants × two repeats), but NONE
have run. Next requires explicit fresh screen availability under the owner's rule;
do not treat generic continued development as an available desktop. No job remains
running or queued. Ask clearly for a short Finder-only screen takeover, then run
the prepared probe independently if approved. No training/download/adoption.

### Full suite1,084 OK; recovery proposals still pause; opt-in grammar comparison — 2026-10-29

Controlled native probe binaries are now compiling independently at nice19,
without any app access, screenshots or inference (`probe.py --compile-only`).
`compile-progress.json`, per-variant compile logs and `compiled.json` binary/source
hashes in that folder; GUI phase requires these verified binaries and fresh screen
authorization. Corrected staged bridge wiring to use each variant's actual binary
for sampling/input, not the default helper. No variant is applied to app/native.

`recovery-validation-2-20261029` FINISHED on unchanged build `ff94628a53`:
**1,084 tests in241.867s, OK (skipped1)**. Six real-Qwen saved-screen decisions
finished without errors/false completion claims, but ALL six returned needs_user.
Raw calls show down-scroll repeated on both repair attempts, then an unsupported
done report rejected by the full-goal verifier. The false-completion escape is
closed in this diagnostic; useful recovery/task success is still unproven.

Prepared a bounded model-free Finder delivery experiment under
`.data/live_eval/scroll-delivery-controlled-20261029/`: original native source
and isolated copies with80ms post-event flush, cleared modifier flags, or pixel
wheel encoding. Two fresh disposable fixtures per variant, three wheel events
each, before/after native targets and cropped evidence, hash/source/focus/STOP
guards and cleanup. NOT COMPILED OR RUN; original app/native files unchanged
by this preparation. `probe.py --prepare-only` and the missing-authorization
refusal passed. Explicit fresh availability is required before --screen-authorized;
no GUI job is queued. This is causal diagnosis, no automatic application of a fix.

Added OFF-BY-DEFAULT experiment `A11Y_SCROLL_REPAIR_SCHEMA=1`. Only after a
submitted scroll is rejected as the identical action on unchanged pixels AND
observations does its repair grammar omit that failed direction (and scroll_to
when downward, because that composite starts with the same primitive). First
attempts, later fresh observations, terminal shapes, native IDs, platform and
sensitive-action guards retain their normal choices. The model chooses the
alternative. No per-site/task rules or default/model changes. Seven new schema
tests, ten recovery tests and29 task tests pass; paired harness mocked-inference
smoke passed. Full1,084 run predates this opt-in addition; no newer full-suite claim.

Launched independent12-decision paired comparison in
`.data/live_eval/scroll-repair-choice-20261029/`: same two owned saved crops/OCR,
reconstructed metadata/history and Qwen/context8192/seed7, three repeats, baseline
flag0 vs candidate1, alternating order. Only repair grammar differs. No actions
execute; record schema hashes, raw replies and pending tool proposals. Repeats
are not independent scenes and useful proposals are not end-to-end success.
No adoption/training follows. Status/log/results/manifest/PID there;10-minute
bound, STOP/source change/error abort. App/native source held fixed during this
comparison; let it run independently, no agent polling. Next review actual
alternative choices, and run the isolated wheel test when screen availability
is explicitly confirmed. Arch training remains stopped pending suitable data.

### Full-suite failures corrected; second offline batch launched — 2026-10-29

`recovery-validation-20261029` FAILED during full units:1,083 tests in281.137s,
2 failures/1 error/1 skip. Saved-screen real-Qwen replay did NOT run. No worker
remained. Latest full success remains the earlier1,070-test run, not this build.

Fixed the repeat fallback: after bounded reconsideration and an unusable terminal
report, a repeated unchanged action now returns needs_user rather than raising
an exception. Other malformed/rejected actions retain their validation errors;
no unsupported result becomes done. Added a negative regression for that case.
The widget flow fixture now supplies real mock-frame 'Accessibility help' evidence
only AFTER confirmed Enter and a matching verifier response; action counts,
re-observation and confirmation assertions remain. This replaces its former
unsupported completion stub, without bypassing the new evidence guard.

The reply-animation failure (typed54/55 at a3s deadline) did not reproduce in
three isolated reruns. Made that unit test check one real Qt timer tick and then
drive remaining ticks deterministically, retaining full-text/accessibility and
completion assertions and adding a stopped-timer assertion. No product mascot
animation code changed. All three formerly failing tests plus ten recovery tests
pass together (13 tests,3.412s). Saved-screen pipeline plumbing also passed with
MOCKED inference in a temporary folder: no model-quality result or GUI actions.

Launched `.data/live_eval/recovery-validation-2-20261029/`: named full units,
THEN six real-Qwen saved-screen proposals only on a pass. Same saved crops/OCR,
bounded context/seed and replay limitations as prior checkpoint. No screen capture,
executed desktop actions, downloads, training or adoption. Progress/logs/results,
file hashes and PID/build there;8/10-minute stage bounds, STOP/source-change/failure
aborts. App/native source held fixed. Let this batch run independently; no polling.
Fresh live-screen availability remains unanswered; no GUI follow-up queued.

### False completion after repeated actions fixed; offline validation running — 2026-10-29

Audited six failed turns from `scroll-recovery-live-20261029`. Both local-web
scroll/read turns submitted one ineffective scroll, then repeated that proposal.
The post-loop ASKS_FOR_FACT shortcut called screen_answer and returned done while
native URL still pointed to /equipment; saved before/after crops are identical
and contain no guide/collection point. This was an actual code escape from full
goal verification, not evidence of successful reading. Finder submitted two
scrolls then clicked wrong visible items (item008/Plain Text); wheel delivery and
that target-choice failure remain unresolved. No new GUI run has started.

`computer_task.py` now repairs an identical action within the existing bounded
model reconsideration loop instead of turning it into a reading success. Repeated
clicks likewise no longer force a done/read response. An acted-on model completion
with readable observations checks the FULL goal even when it contains a question
or reading verb. Verified current information still answers normally. Removed the
obsolete reading-wording exemption. Rejection feedback distinguishes a rejected
proposal from actions already in history. Repeat signatures now include volatile
observed-content/position/document fingerprint as well as image/action, so changed
metadata with stable pixels is not treated as no progress. No per-task rules,
model default changes or sensitive/target-guard bypasses.

Nine new recovery regressions pass;29 computer-task,18 completion and331 reported-
failure tests pass. Old reading fixtures now provide explicit observed evidence
and verifier responses instead of assuming a reading claim requires no proof;
new negative tests ensure an unopened guide/missing answer cannot pass. Prompt-
cache test's changed observations remain allowed without weakening its assertion.
These are control-flow checks, not proof of improved live task completion.

Launched independent offline batch `.data/live_eval/recovery-validation-20261029/`:
full named unit suite, then six real-Qwen proposals through ComputerTask.propose
on two saved failed browser crops (three repeats). OCR reads the saved files via
local Vision; minimal metadata/prior unchanged-scroll receipt are reconstructed,
no native IDs/hit checks invented and no actions executed. Count false completion
claims, errors and proposed actions; this is not a held-out/end-to-end score.
`progress.json`, `units.log`, `replay.log`, `replay-results.json`, kit hashes and
PID/build in that directory.8-minute unit/10-minute replay bounds; STOP/source
change/failure abort. App/native source held fixed during this batch. Let it run
independently, no polling. No training or GUI follow-up is queued.

Fresh screen availability was requested for a focused live rerun after offline
checks; reply is pending. Next inspect full-suite/replay outcomes, then use live
evidence to address wheel delivery and wrong-target choices. Arch capacity probe
finished earlier; real visual training still awaits adequate action/outcome data.

### Both jobs FINISHED; live3/9, corrected capacity probe passed — 2026-10-29

Checked saved reports and processes: no Mac live worker or Arch compute process
remains. `scroll-recovery-live-20261029` completed all9 turns in~401s on unchanged
build `ba43c25fd0`:3 raw/3 eligible passes,2 complete workflows passed. This focused
development diagnostic still has6 failed turns; do not describe broad capability
as fixed. Full per-turn independent checks/receipts/shots remain in the run folders.
Source freeze is over; no further GUI automation is queued or newly authorized.

Arch `gui-policy-full-memory-2` FINISHED successfully with BF16 autocast and
allocated AdamW states:448 image box/2802 tokens peak allocated6,317,907,456 bytes,
reserved6,822,035,456;672 box/2942 tokens allocated6,378,478,592/reserved6,937,378,816.
All checked LoRA gradients finite/present; zero optimizer steps, updates or adapter
saves. The first non-autocast OOM is preserved, not overwritten. Corrected reports
copied to Mac `.data/training/gui-policy-full-memory-2/{progress-arch,memory-arch}.json`.
Measured capacity for these two bounded workloads, not optimizer-step temporary
workspace, longer sequences, convergence or end-to-end task quality. No training
or adoption automatically follows. Next audit the6 live failures before choosing
implementation/training changes; public labelled policy data remains insufficient.

### Live Finder/Safari batch started; BF16 capacity correction on Arch — 2026-10-29

Owner replied “ok then go blud” to the pending live-testing/screen request;
treated as authorization for the nine focused Finder/Safari turns. Launched
`.data/live_eval/scroll-recovery-live-20261029/` on validated build `ba43c25fd0`.
Preflight requires the successful1,070-test result on that same unchanged build;
does not repeat the previously completed full suite. Finder scroll/select3
repeats, local Safari catalog first two turns2 repeats, Apple Support first
turn2 repeats. Seed41/context8192, local Qwen, confirmations and native target
checks intact; no private apps, messaging, training or adoption. Batch is active
in finder-scroll at startup. App/native source held fixed until it finishes.
Progress/logs/strict summary/launch and authorization records in that folder;
45-minute total bound, source-change/STOP/error abort. Let it run independently.

Arch full-prompt capacity probe1 FINISHED FAILED: CUDA OOM before any case
completed, attempted240MiB allocation with~75.75MiB free. AdamW state29,360,128
CUDA bytes allocated; zero optimizer steps. This does not prove every training
configuration fails: probe forward lacked BF16 autocast used by the actual CUDA
trainer (bf16=True). Previous tiny short-prompt result was insufficient evidence
for full workload fit. Original logs/reports preserved remotely and copied to
Mac `.data/training/gui-policy-full-memory-1/{progress-arch,memory-arch}.json`.

Added explicit `--bf16-autocast` option to the memory probe, recording precision
and active-case token counts before forward so failures retain workload sizes.
Four workload-boundary tests pass; script compiles. Launched a separate corrected
probe2 on otherwise identical model/image/prompt/state settings, GPU idle before
launch: service `a11y-gui-full-memory-2.service`, remote
`~/a11y-training/runs/gui-policy-full-memory-2/{progress,memory}.json`, log
`~/a11y-training/logs/gui-policy-full-memory-2.log`; Mac kit
`.data/training/gui-policy-full-memory-2/`. Still zero optimizer/weight updates,
no saves/downloads/adoption; only bounded forward/backward capacity testing.
No training is chained. Let both fixed shell jobs run independently; no polling.

### Full-prompt visual capacity probe launched on Arch — 2026-10-29

Owner asked about testing/training next. Focused GUI batch remains unrun; its
first attempt was terminated during units. Later full suite passed1,070 tests
(skipped1). Current screen availability question is still unanswered; no new
GUI automation launched. Unit/guard results do not establish task-success gains.

Arch reachable over Tailscale, no compute GPU processes;480GiB free, AC online.
The old visual memory check finished but used only173/313 tokens and allocated
no optimizer state. Extended `scripts/gui_policy_memory.py` with a bounded text
workload and explicit optional AdamW-state allocation WITHOUT optimizer steps;
four workload boundary regressions pass, script compiles. No app/native source
or default changes. Full1,070 suite predates these script-only additions.

Launched independent nice19 user service `a11y-gui-full-memory.service` on Arch:
`~/a11y-training/runs/gui-policy-full-memory-1/`. Uses already-verified local4B
visual weights/pinned environment and an existing public image crop; no download.
Full existing policy SYSTEM plus action schema/structural observation (11,280
text characters),448/672 image bounds,8192-token ceiling, one forward/backward
per size with allocated AdamW moment buffers. Zero weight updates, optimizer
steps, adapter saves, desktop actions or adoption. Report excludes optimizer-step
temporary workspace; this is capacity testing, not convergence, dataset quality,
full training feasibility or an exact Ollama serving replay.

Kit hashes checked remotely. Mac kit `.data/training/gui-policy-full-memory-1/`;
remote `progress.json`, `memory.json`, `memory.log`; supervisor log
`~/a11y-training/logs/gui-policy-full-memory-1.log`. Inner10-minute bound, outer
service11 minutes; OOM/error stops, no retries or training chained. Let it run
independently, no agent polling. Next inspect measured memory, then resolve live
wheel/choice failures and curate varied before/action/after data before a visual
training pilot. Reviewed public browser scroll subset remains only6 examples
from3 trajectories on2 sites; not a qualified training set.

### Full validation completed successfully — 2026-10-29

`link-and-scroll-validation-20261029` FINISHED with exit0 on unchanged build
`ba43c25fd0`: **1,070 tests in209.622s, OK (skipped1)**. No running test worker
or GUI follow-up remains. This supersedes the earlier terminated unit attempt
and older suite counts. Scroll-progress feedback and named-link mismatch
validation are implemented and unit-tested; actual wheel delivery is unresolved
and live task-success improvement has not been measured. Current screen
availability was requested separately; no new GUI batch launched without it.

### Named-link grounding check; full-unit termination investigation — 2026-10-29

Previous `scroll-recovery-20261029` batch FAILED during units: child exited with
signal15 after~166s. Zero live turns ran. Its log contains no completed-suite
summary/assertion failure; cause of SIGTERM is unknown, not a passing unit run.
No workers remained. Owner requested continued development; asked separately for
current screen availability before starting another GUI batch. No new live run
has been launched or queued from this checkpoint.

Extended the existing exact native hit-name check to named AXLink targets.
Previously a model-labelled Support click over an AXLink named Store was not
covered by the Finder/menu-only name check. Now planning rejects that mismatch
and returns the actual observed link name to the model for another choice;
execution rechecks and refuses a link that changes between plan and click.
Unnamed links and container/image names remain unknown, not asserted mismatches;
numbered/exact matching links keep the normal capture and consent guards.
Shared code remains compatible with Windows bridges without native hit testing.
No per-site phrase lists, extra model calls, prompt/default changes or guard bypass.

Six new grounding tests pass, plus3 exact-hit,11 gesture and14 focused-grounding
tests (34 total). This validates rejection/feedback mechanics, not a demonstrated
live task-success or speed gain. Wheel delivery is still unresolved; scroll
feedback implementation from the prior checkpoint remains in place.

Launched independent OFFLINE full-suite validation at background priority in
`.data/live_eval/link-and-scroll-validation-20261029/`. Named test starts and a
SIGTERM stack dump are retained to diagnose another termination. Eight-minute
bound, source-change/STOP abort, no GUI follow-up. `progress.json`, `units.log`,
`driver.log`, `launch.json`; source held fixed during validation. Let it run
independently, no agent polling. Next inspect completion/last test and actual
failure evidence; run focused Finder/Safari tests only with fresh availability.

### Scroll feedback fix and focused live batch — 2026-10-29

Owner gave fresh screen availability and authorized continued development/testing.
No prior live/model-choice worker remained. Controlled policy comparison FAILED
its between-call time limit after46/54 decisions (not a completed comparison):
both policies19/23 agreement, no inference errors, no measured gain. Partial
audit saved in `policy-choice-controlled-20261029/partial-audit.json`. No prompt
candidate adopted; production SYSTEM and Qwen defaults unchanged.

Bounded direct native Finder probes reproduced wheel submissions with no visible
row change across four fresh fixtures, without model calls. Additional delivery
modes did not establish a fix. A separately saved owned-window before/after pair
confirms the list remained at the top, and post-event access reported true.
Some diagnostic runs exited with errors after saving partial/results; do not
call every mode a clean successful probe. Pointer movement alone was not proven
to fix delivery. Root cause remains unresolved; native source unchanged.
Evidence/scripts at `.data/live_eval/scroll-delivery-20261029/`, including
`visual-check/shots/`. Disposable files were verified and cleaned; cleanup.json
records the follow-up removal of Finder metadata/remaining owned fixtures.

Implemented a separate measured feedback gap in `computer_task.py`: `scroll_to`
previously queued up to seven wheel events without explicit viewport outcomes.
Now a volatile fingerprint includes observed labels and positions; absent content
is unknown, movement of the same text counts as change, and two unchanged scroll
observations stop that queued tool and return control to the model. Compact model
history receives direction and observed effect, with no success inference.
The model chooses the alternative; no website/task phrase rules or guard bypass.
Eight new scroll-progress regressions pass, including the actual propose/history
path;29 existing computer-task tests pass. Latest earlier full-suite log was
1,045 tests in89.997s (skipped1), not the older1,043/1,030 counts.

Launched fixed-build independent batch `.data/live_eval/scroll-recovery-20261029/`:
full units, then Finder offscreen selection3 repeats; local Safari catalog first
two turns2 repeats; Apple Support first turn2 repeats:9 planned live turns.
Only Finder/Safari selected, local Ollama seed41/context8192, web-answer fallback
off, normal validations/confirmations retained, no model adoption or training.
45-minute total bound; STOP marker/source change/failure aborts and cleans up.
Summary excludes pending actions/timeouts/incomplete tasks from eligible passes;
these are focused development diagnostics, not overall app success rates.

Status `progress.json`, driver/stage logs, `summary.json`, PID/build/command in
`launch.json` under that folder. App/native source is held fixed until this batch
ends. Let it run independently; do not keep an agent turn active polling.
Next inspect full-suite result and live action/outcome traces, especially whether
the model changes method after no-progress feedback and whether wheel delivery
still blocks the task. No automatic follow-up code changes are queued.

### Controlled policy comparison launched; first candidate rejected — 2026-10-29

Reviewed first-repeat raw decisions from `policy-choice-20261029/results.json`.
The replacement prompt falsely returned done/action=null for three requested
visible-menu clicks and produced integer coordinates such as117,898 for visible
targets, incompatible with fractional action coordinates. Both policies also
returned28,260 for the back chevron; baseline gave up after ineffective scrolls,
candidate called the still-unfinished file-location goal done. These are real
observed proposal failures, not evidence of full task outcomes. The first
experiment changed too much: removing the full existing SYSTEM also removed
explicit action/status and coordinate/tool instructions. Its6/27 result does
not isolate the effect of adding visibility/recovery guidance. Rejected.

Launched independent local `policy-choice-controlled-20261029/` comparison:
same9 development cases/images/schema, Qwen, context8192/seed7, three repeats,
54 decisions; candidate preserves the COMPLETE current SYSTEM prefix and only
appends general visibility, unchanged-view recovery and action-evidence checks.
No per-site rules, production changes, executed GUI actions, training or adoption.
Same diagnostic limitations apply: reused development scenes, reconstructed
minimal observations, next-action agreement rather than end-to-end success.
Raw outputs must be reviewed; improvements here require independent validation.
Status/results/log/launch.json in `.data/live_eval/policy-choice-controlled-20261029/`.
Let the fixed batch run independently, no agent polling. No live follow-up queued.

### Paired model-choice diagnostic FINISHED — 2026-10-29

Finished all54 logical decisions in105.7 seconds, no inference errors. Baseline
agreed with the predeclared actions21/27 versus candidate6/27. The shorter policy
regressed on this small development diagnostic; do not apply it to the app.
These are next-action agreement scores, not end-to-end task success. Raw outputs
still need review for scoring exclusions/alternative useful actions. No worker
remains queued; no automatic follow-up or adoption. Details in results.json below.

No live suite or training job was running at startup. Historical R3/Arch router
qualification finished and failed; the visual-model short-image memory probe
passed without optimizer steps or task-quality evidence. Qwen remains default.

Launched an independent local Ollama comparison at background priority:
`.data/live_eval/policy-choice-20261029/`. Nine development cases on three saved,
owned Safari/Finder test-window images, baseline SYSTEM versus a shorter general
visibility/reveal/recovery policy, three repeats each (54 logical decisions).
Uses installed Qwen3-VL-4B, greedy seed7/context8192 and the same existing macOS
action schema for both policies. Alternates policy order by repeat. No screenshots
captured, actions executed, model training/downloads, or app/default changes.

Checks include missing Support in a clipped menu, visible menu/file targets,
wrong-submenu recovery, an offscreen file, and seven ineffective scroll attempts.
Cases and image hashes, both prompts, schema and model/runtime provenance are
saved. Scoring tests: six passed; script compiles. Last documented full suite is
1,043 tests, before the subsequent keypad-history fix; no new full-suite claim.

**Limits:** minimal observations reconstructed from cropped saved images, no
contemporaneous native target IDs. Scores mean agreement with predeclared next
actions, not exhaustive correctness, independent held-out performance or live
task success. Review raw outputs, including alternative useful actions, before
considering any app policy change. Production prompt unchanged. No automatic
adoption or live follow-up is queued. The test has a30-minute between-call limit
and stops after three consecutive inference errors or a STOP marker.

Driver `scripts/policy_choice_eval.py`; status `progress.json`, raw decisions
`results.json`, log `batch.log`, PID/command `launch.json` in that directory.
Let it run independently; inspect saved results when requested, without holding
an agent turn open to poll. Next: compare gains/regressions and latency, then
decide whether broader testing or targeted visual-policy training is warranted.

### Visual-base memory check PASSED; real scroll demonstrations sampling — 2026-10-29

Selective sample **FINISHED**:61,327,551 network bytes,6 completed Darwin
trajectories/12 scroll examples/34 images. Browser-only review subset excludes
Mail/Slack/Discord examples:6 scrolls from3 Chrome trajectories across Trip/Booking.
This is too small/narrow for a qualified policy training set. First before/after
pair manually reviewed: scroll UP at page top, no meaningful viewport movement;
do not use as a positive scroll label just because task_completed=true. Effect
triage saved to `agentnet-scroll-sample/effect-audit.json`; other examples still
need alignment/goal review. Raw source action has moveTo(x,y) BEFORE scroll;
retain the scroll location in data mapping. Current A11y primitive lacks an
explicit scroll-location decision; cannot silently drop this information and
expect training to teach where to scroll. No A11y source changes/new training.
Mac review files under `.data/training/gui-data-survey/agentnet-scroll-review/`.

Approved4B visual download/verification FINISHED, all13 selected files present.
NF4 language model + frozen UNQUANTIZED vision + rank8 LoRA last8 language layers
passed short-image forward/backward checks:448box173tokens peak allocated
5,291,294,720/reserved6,016,729,088 bytes;672box313tokens peak allocated
5,358,113,280/reserved6,010,437,632. No optimizer/state allocation/updates, no
adapter save or convergence/task-quality result. Full prompt + optimizer memory
remains to be measured before a training pilot. Reports copied to Mac
`.data/training/gui-policy-memory-1/`; original weights remain ONLY on Arch.

AgentNet image inventory: win/mac split ZIP124.34GB, Ubuntu73.52GB. Rather than
bulk downloading, confirmed HTTP ranges and ZIP64 central directory39.24MB in
last volume. Launched independent Arch user service
`a11y-agentnet-scroll-sample.service`: bounded160MiB network/15min runtime,
read final8MiB of pinned public trajectory JSONL, select up to6 completed Darwin
scroll tasks, fetch only nearby image members for up to2 scrolls/task. ZIP64
offset/CRC/decompression limits checked; source code never executed. Five range/
ZIP parser regressions pass. This is INSPECTION DATA, not approved training labels:
next frame may include changes beyond one scroll; review alignment/quality first.
Status `data/gui-choice-inspection-1/agentnet-scroll-sample/progress.json`,
examples.json and image files there; log `logs/agentnet-scroll-sample.log`.
Script `scripts/gui_agentnet_sample.py`. No new model downloads or training.
Keep Arch awake; let this fixed data job run independently, no agent polling.

### Approved visual-base download + bounded memory probe — 2026-10-29

Owner explicitly approved downloading original Qwen3-VL-4B-Instruct weights to
Arch (8,875,719,344 weight bytes). Pinned revision
`ebb281ec70b05090aa6165b016eac8ec08e71b17`, new separate directory
`~/a11y-training/models/Qwen3-VL-4B-Instruct`. No default change or long training.
Launched independent user service `a11y-gui-policy-memory.service` with nice19,
two-hour runtime ceiling; Arch was on AC with no competing GPU task and488GiB free.

Separate `.venv-vision` overlays Pillow/torchvision0.26.0+cu128 and reuses existing
torch2.11.0+cu128/transformers4.57.1/PEFT0.17.1 via appended `.pth`; training and
conversion environments remain intact. Worker `scripts/gui_weights_prepare.py`
(remote kit copy `a11y_gui_weights_download.py`) downloads only selected model
assets, caps total at10GiB, verifies sizes and HF LFS SHA256, then runs
`scripts/gui_policy_memory.py`: NF4 language model, unquantized/frozen vision,
rank8 LoRA on last8 language layers; short public-image forward/backward checks at
448/672 image-size bounds. No optimizer steps, saved adapters, adoption or GUI.
Reports must not be called full training-memory or policy-quality proof: optimizer
states/full prompt workloads are not included. OOM/error stops the chain.

Remote status `~/a11y-training/runs/gui-policy-memory-1/progress.json`, verification
manifest `download-manifest.json`, memory results `memory.json`, detail `memory.log`;
launch/setup log `logs/gui-policy-memory-1.log`. Keep Arch plugged in and awake.
Let the shell job run independently and inspect when requested/notified; do not
start duplicate downloads/training or keep an agent turn alive polling.

### Public GUI training-data inspection launched — 2026-10-29

**Inspection FINISHED**:301,696,124 bytes downloaded,288 Mind2Web train rows
across16 sites/45 trajectories;251 CLICK,22 TYPE,15 SELECT, no scroll labels.
AgentNet metadata scroll coverage corrected for macOS's `Darwin` system name:
3,324 Darwin +5,905 Windows tasks; initial `mac` substring check undercounted
Darwin as0. No AgentNet image/trajectory archive downloaded. Image/box audit:
image heights1080–16786px,277 rows with positive boxes,11 without; no positive
boxes outside full images. Full-page vs live-viewport offsets remain unresolved,
so these rows are NOT yet usable scroll/action-policy labels.

Candidate manifest prepared on Arch:180 train/97 validation examples, with
website AND trajectory separation;11 missing-box rows excluded. This split is
within the public TRAIN shard, no benchmark/test split used. Held-out sites:
discogs/kayak/sixflags/spothero. Grounding candidates, not qualified policy data.
Reports at `data/gui-choice-inspection-1/{inspection,annotation-audit,split-audit}.json`;
copies under Mac `.data/training/gui-data-survey/inspection-1/`.

Tiny RANDOM Qwen3-VL multimodal smoke passed on Arch pinned4.57.1/PEFT0.17.1:
four image tokens, two supervised answer/EOS tokens; selected loss exactly matched
native4.68578243, all28 language-LoRA gradient tensors finite/nonzero, maximum
gradient difference2.4214e-8. `scripts/gui_policy_smoke.py`, report
`multimodal-smoke.json`. No optimizer step/pretrained visual weight or NF4 memory
test. Existing text weights cannot substitute for a visual training base.
Official original-weight inventory checked:2B4,255,140,312 bytes;4B8,875,719,344
bytes. Owner was asked for explicit4B download approval (no automatic large
model downloads rule); response pending. No real-model training/download or
GUI/policy experiment is running. Continue data/processor/training-path preparation
independently; await approval before provisioning visual weights.

Owner approved proceeding with action-labelled public data. Arch inventory:
only original text Qwen3-4B weights are installed; no visual training base yet.
No automatic large model download or new training. Independent user service
`a11y-gui-data-inspection.service` prepares a bounded public sample on Arch:
AgentNet metadata18.84MB plus one Multimodal-Mind2Web TRAIN parquet282.86MB,
400MiB total download cap, pinned dataset revisions from catalog.json. No
benchmark/test split download or use for training. It extracts eight image/action
samples and summarizes source websites/action types and AgentNet scroll coverage.
Status: `~/a11y-training/data/gui-choice-inspection-1/progress.json`; report
`inspection.json`; log `logs/gui-data-inspection.log`. Source script
`scripts/prepare_gui_data_inspection.py` on Arch. Let it run independently.

Data review must distinguish full-page screenshots vs actual viewport observations;
Mind2Web action labels do not directly teach scrolling. AgentNet image archives
were not bulk-downloaded. Dataset card declared licenses are not a substitute for
reviewing source component terms. Next: inspect annotations/images, select valid
policy examples and website-disjoint holdouts, then review visual-model training
compatibility/memory before asking owner to provision any additional large weights.
Keep existing model defaults and safety confirmations. Old heavy live test is
stopped; no new live/policy comparison or training is running yet.

### Heavy performance measurement launched — 2026-10-29

**STOPPED at owner's request** to prioritize model choices/recovery. Runner
exited and no live worker remains. Final raw baseline45 recorded/26 raw passes;
strict audit after cleanup:45 recorded/21 eligible passes. Initial audit raced
cleanup and read an older27-row summary; preserved as
`audited-summary-before-final-cleanup.json`, then rerun against final45-row data.
Do not report a completed114-turn run or general success rate.

Owner proposed broad public website imagery for training. Public sources checked:
Multimodal-Mind2Web (screenshots + task/action sequences), AgentNet (cross-platform
human trajectories including scroll), OS-Atlas-data (image/instruction/target boxes,
primarily grounding). Catalog metadata being saved under
`.data/training/gui-data-survey/`; no bulk images/models or new training launched.
Screenshots alone do not supervise when to scroll/recover; action and outcome
labels are needed, with website/trajectory-disjoint holdouts and viewport variation.
The completed Arch pilot trained TEXT ROUTING, not screenshot-driven action policy.
Next visual-policy experiment is separate; existing failed router adapters remain
rejected. New policy comparison is not yet running; don't describe it as active.

Owner scrolling observation audited during fixed-build run: capability-r1
recorded27 turns; batch moved to harder-r1. Finder `scroll_select` actually
executed **7 scrolls**, then selected item008 instead of requested item089 and
ended needs_user (64.63s). Saved owned-window frames shot0001/0015 both show
the same initial Finder rows at top: scroll submission did not establish visible
movement. Inspect native event targeting/hit location and add progress evidence;
do not count receipts as effective scrolling. Native helper currently posts a
line-wheel event at window center; actual underlying cause remains unproven.
Web `scroll_and_read` executed0 scrolls after landing on wrong page, a separate
navigation/window-binding failure. Neither was a pass. Preserve source until
current measurement batch finishes; no hot fix or new GUI job was launched.

Owner explicitly requested heavy real-task testing now; screen use authorized.
Independent bounded driver PID58763:
`.data/live_eval/heavy-performance-20261029/run.py`. Fresh full units, then
capability-r1, harder-r1, capability-r2, harder-r2 at fixed seed37/8K context,
default Qwen text/vision. Pure manifest checked:27 capability +30 harder turns
per repeat, **114 scheduled turns**. No prior training/eval/live worker remained.
Two-hour overall ceiling; STOP marker interrupts owned group for cleanup. Source
changes abort the batch. No autonomous edits, new training, downloads or adoption.

Fixtures cover Finder/TextEdit, scrolling/planning, long conversation memory,
loopback web tasks, Settings navigation, Preview, Dictionary, and marked temporary
Notes/Reminders. Existing confirmations/consent/target validation remain; native
eval guard blocks prohibited apps. Clock/timers excluded (earlier unowned timer
state is unresolved), no Mail/Messages/Photos/Contacts/Teams/WhatsApp/FaceTime.
Test failure is recorded, not converted to a pass or auto-repaired mid-run.

Progress, unit/live logs, per-stage raw runs and summary under that directory.
Separate independent `audit.py` produces `audited-summary.json`: excludes pending
actions, timeouts and non-done computer tasks from raw passes, preserves pure
conversation results despite stale task status, requires all turns for workflow
success, and reports both-repeat reliability/latency. Seven synthetic audit
eligibility checks passed before launch. Raw suite records stay unchanged.
Do not claim scheduled turns were completed or use raw passes as whole-task
success. Inspect final artifacts when asked/notified; no active agent polling.
Keep app/native source unchanged during the fixed-build measurement queue.

### Mac arrow-history follow-up fixed — 2026-10-29

Owner reported Up/Down did nothing. Reproduced with native macOS arrow event
flags: Qt sets KeypadModifier even without Shift/Cmd/etc; initial handler required
NoModifier. Handler now accepts NoModifier OR KeypadModifier, retains normal
modified-arrow behavior. New native-flag regression failed before fix; all11
movement/history tests now pass, including Shift/Ctrl/Alt/Meta + keypad cases.
This corrects the initial test gap (QTest plain arrows omitted the native flag).
Restart source app to load fix; recall is memory-only and starts with messages
submitted in that run. Previous full movement/history suite finished **1,043
tests in90.869s, OK with1 optional parity test skipped**; no new full-suite run
for this one-line key-modifier change. No GUI apps, models or defaults changed.

### Owner movement jump + input history — 2026-10-29

Fixed native-frame/client coordinate mismatch in animate_companion_to: target
move()/pos() coordinates are translated into geometry()/setGeometry() client
coordinates before interpolation. Horizontal motion therefore does not interpolate
through the title-bar offset. begin_stretch now suppresses refits BEFORE finishing
slide animations and stops a queued refit, preventing an unanimated layout move.
Native Mac owned-window probe: X1231→16, Y175→175, zero vertical shift, finished.
No other apps opened or screenshots captured for this probe. Geometry regression
also simulates a 28px native frame offset; frameless/reduced-motion paths retained.

Composer supports unmodified Up/Down recall of the last100 actually submitted
chat/computer prompts, memory-only. Restores the unsent draft after newest entry;
wrapped/multiline interior arrows and selection/modifier editing stay normal.
No empty/busy submissions or setup values recorded; setup fields cannot recall
chat. Clear saved conversation also clears volatile recall without erasing draft.
Nine new Qt movement/history regressions pass. Full suite is running independently:
`/private/tmp/a11y-movement-history-unit.log`; validation/native evidence under
`.data/live_eval/movement-history-fix/`. No new model calls, training or adoption.

Earlier completion-evidence live batch finished **5/6**: Finder sort0/1 then1/1
(60.78s blocked by protected-app input guard, then37.82s pass), Finder Info2/2
(30.10/29.33s), Safari Command-T2/2 (16.04/14.78s). Raw artifacts:
`.data/live_eval/completion-evidence-followup-20261029/`. The sort failure remains
unresolved; do not bypass the guard or call this a stable full-suite improvement.
Source freeze for that completed batch is over. No commits or pushes.


### Arch pilot REJECTED; Qwen completion validation started — 2026-10-29

Arch qualification finished on identical406 held-out rows. No parse failures, but
operation **373→369/395**, action flag **358→354**, reply **337→323**; app
**303→369** and target **356→363** improved. **30 critical per-row regressions**:
11 acts_on_computer,7 operation,7 referent,5 goal_mode. Examples include
new_window→launch_app and delete_reminder→complete_reminder. Follow-up reports
finished/qualifies=false, adopted=false. Qwen remains default. No further training
or live qualification of this candidate. Artifacts and diagnosis:
`.data/live_eval/arch-pilot-1-qualification/{comparison,diagnosis}.json`.
Successful parity/conversion did not establish model quality; undertraining/data
or objective explanations remain hypotheses, not demonstrated causes.

Owner freshly authorized live screen testing. Independent Qwen-only validation
PID56481, `.data/live_eval/completion-evidence-followup-20261029/run.py`: full
unit suite then isolated Finder sort/Get Info/Safari Command-T, two repeats each,
seed29,8K context. Build selection checked purely: exactly3 one-turn cases.
No prohibited apps, cloud inference, downloads, model adoption or training.
Source remains fixed while batch runs. Native result + task-done + honest-reply
checks retained; failures stay failures. Status `progress.json`, unit/live logs,
raw rows and eventual summary in that directory. STOP interrupts owned runner for
cleanup. Run independently and end agent turn, no polling loop. Inspect complete
results next; GUI reliability is still measured separately from router metrics.


### Arch qualification batch launched over Tailscale — 2026-10-29

Latest requested status: Arch conversion finished successfully; Q4 model and
small reports fully transferred to Mac. SHA256, Arch parity/base verification
and frozen data checks passed, and separate `a11y-router-arch-pilot-test` alias
registered. Fresh base406-row evaluation finished (`base.json`); candidate
evaluation is running (`candidate.log`). Follow-up stage:
`paired-ollama-qualification`, output `.data/live_eval/arch-pilot-1-qualification/`.
No comparison verdict yet. Keep app/native source fixed until this paired run
ends. Defaults unchanged; no GUI tests or further training.

Owner-requested status check: systemd service active/running (PID4020), stage
`quantize` at first snapshot. Tool build, base conversion, adapter GGUF conversion
and F16 merge finished. Follow-up inspection found quantization had just finished
in 75.93 s, output2375.91 MiB; no quantizer process remained. These stages use CPU,
so idle GPU is expected. Mac follow-up waits for final success/checksum before
transfer/scoring. Historical first follow-up
traceback remains in its append-only log; current progress reports running.

Startup results confirmed: adapter/base tokenizer and all dataset checks passed.
Arch tiny-model numerical parity PASSED with real PEFT: **8 CPU cases and
12 CUDA cases** (CUDA includes BF16), torch2.11.0+cu128/transformers4.57.1.
These cover loss and gradients with the deployed package pins, not NF4/full-size
memory or task quality. Tool-building stage is active; conversion/scoring results
are not available yet. Reports reside in the Arch output directory and will be
transferred with the quantized artifact after successful conversion.

Tailscale is connected: Mac100.92.233.64, Arch100.74.40.62. Noninteractive SSH
works to `edi@100.74.40.62`; no password/key stored. Arch preflight: AC power,
506 GiB free, 28 GiB available RAM, no GPU compute job; training pins still
torch2.11.0+cu128/transformers4.57.1/peft0.17.1. Source-only kit transferred.
Independent conversion batch is **running as user systemd service
`a11y-pilot-qualify.service`**, MainPID4020 at startup confirmation. Two tmux
attempts exited on SSH disconnect without leaving a worker; do not claim tmux
is supervising this job. Sleep inhibitor was denied interactive authorization;
no system sleep settings changed. Owner should keep Arch awake/on AC/lid open.

Separate `.venv-convert` installed transformers4.57.6/numpy2.2.6 and reuses
training torch via an appended `.pth` path; `.venv` remains unchanged. Original
system-site-packages recipe alone would not expose another venv's torch and
was corrected for the actual setup. Status:
`~/a11y-training/runs/cuda-pilot-1-qualification/progress.json`; setup status:
`qualification-pilot-1/setup-progress.json`; log `logs/pilot-qualification.log`.
Chain verifies adapter/base/tokenizer/data, runs actual Arch CPU/CUDA PEFT parity,
builds pinned CPU conversion tools, converts/merges unquantized GGUF then Q4_K_M.
Any failure stops; no retraining or automatic model adoption.

Independent Mac follow-up waits for successful conversion, transfers ONLY Q4 and
small reports/checksums, then runs `router_arch_qualify.sh`: separate alias
`a11y-router-arch-pilot-test`, fresh paired base/candidate406-row Ollama scores at
8K context with fixed app source. Status:
`.data/training/arch/qualification-pilot-1/followup-progress.json`;
log `followup-driver.log`. First follow-up failed before Arch status existed;
restarted after confirming live service. No GUI tests included. Let both shell
batches run independently; inspect on request/completion, no agent polling loop.

### Arch pilot verification/parity and qualification kit — 2026-10-29

Returned PEFT adapter verified: 112 finite FP32 tensors, all 56 LoRA B matrices
nonzero, 3,670,016 parameters, expected rank/layers/scaling. All split hashes match.
Local numerical parity PASSED eight tiny random Qwen CPU cases against full causal
loss/native model loss and all 25 gradient tensors per case: max loss difference
4.7684e-7, max gradient difference2.7756e-17. Existing tool environment uses torch
2.11.0 / transformers4.57.6; exact Arch4.57.1/PEFT/CUDA check remains pending.
No model download/training. CUDA data tests: 8 pass; numerical unittest: 1 pass
under the separate torch environment (skipped intentionally in app venv).
Prepared source-only conversion kit and separate Mac paired-Ollama qualification
script; paths/pins/commands and limits in ARCH_TRAINING_HANDOFF.md. No remote job
or conversion started: two SSH attempts timed out before authentication. Asked
owner for awake/current-address confirmation, never a password. No automatic
retraining, default change, GUI work, commits or pushes. Artifact is NOT qualified.
Also verified the earlier exact-evidence full validation finished: **1,030 tests
passed in 92.092 s**; no fresh full suite was run for these trainer/tooling edits.


- Arch training continuation is documented in [ARCH_TRAINING_HANDOFF.md](ARCH_TRAINING_HANDOFF.md)
  (2026-10-29): setup, dependency/memory fixes, completed pilot, returned artifacts,
  and pending conversion/qualification. No new job launched by this docs update.

### Arch pilot completed and returned — 2026-10-29

Owner's CUDA pilot completed 100 optimizer steps in 3201.5461 s, best validation
loss 0.252397 at checkpoint-100, interrupted=false. Selected PEFT adapter
(14,694,776 bytes) and run/config/hardware metadata are now under
`.data/training/arch/cuda-pilot-1/`. All three recorded dataset hashes match
recursive-10/r3/data exactly, including unchanged 406-row test SHA37ca9676….
This validates training execution, not router quality. No Ollama qualification
has run for this adapter and no default changed. Mac has only ~26 GiB available;
avoid simultaneous original/fused/F16 exports here. Prefer merging/conversion on
Arch (previously >500 GB free), then transfer only quantized artifact. Conversion
compatibility and loss/gradient numerical parity remain to be checked. No new
training/evaluation job launched by this receipt check.


### Arch CUDA setup and memory fix — 2026-10-28

Owner connected Arch at edi@192.168.1.143. RTX 4060 Laptop GPU 8 GB,
32 GB system RAM; reboot resolved NVIDIA driver/library mismatch. CUDA matrix
and bitsandbytes NF4 round-trip passed. Official Qwen3-4B-Instruct-2507 weights,
trainer helpers and recursive-10/r3 frozen data were transferred/downloaded by
owner. Training has NOT completed any optimizer step: Transformers 5 argument
incompatibility, then datasets/Hub mismatch, then full-logit loss CUDA OOM.
Suggested environment pins: transformers4.57.1, peft0.17.1, datasets4.3.0,
huggingface-hub0.36.0, accelerate1.11.0; torch2.11.0+cu128 and bnb0.50.2.
Trainer now uses Qwen logits_to_keep for positions predicting supervised next
tokens only, preserving full prompts, first answer token, EOS and masked padding.
Microbatch mean loss and Trainer accumulation scaling retained. Eight local
CPU/data tests passed; this Mac environment lacks torch, so numerical loss/gradient
parity and actual CUDA memory relief are still unverified. Owner must transfer
updated trainer and rerun fresh smoke output before longer training. No app/native
changes, model adoption, GUI tests, commits or new long-running training launched.


### Current checkpoint — recursive work, 2026-10-28

- **Next completion safety change applied:** final completion evidence must match
  one complete observed value from the SAME bounded choices supplied to the
  verifier schema (case/whitespace normalization preserved). Previously substring
  matching accepted filename/path prefixes and partial OCR status text, including
  a positive phrase embedded in a negative sentence. Schema and final validation
  now share `completion_values`; non-string, blank, oversized and out-of-budget
  evidence cannot supply proof. No task phrase rules or verifier prompt changes.
  Six new boundary tests reproduce the failures and pass after the fix; 17
  focused completion tests passed before the final blank-value regression was
  added, and all six boundary tests pass on final source. Rechecking saved model
  predictions gives unchanged **19/20, zero false positives**; this is cached
  prediction validation, not new inference or real-task improvement evidence.
- Final full-suite validation is detached under
  `.data/live_eval/exact-completion-evidence/validate.py`; status `progress.json`,
  full output `units.log`. It waits for the prior intermediate unit run before
  starting, refuses intervening source changes and opens no apps. Let it run
  independently. Fresh Finder/Safari screen availability was requested in this
  turn and has not been received; no live tests are authorized by silence.
  Last verified final-source full suite remains the 1,024-test run below until
  this validation finishes. No training, downloads, commits, pushes or adoption.

- **Browser completion validation FINISHED successfully**, build `9ce3305099`.
  Final full suite: **1,024 tests passed in 88.896 s**. Frozen local verifier
  replay: **19/20, zero false positives** (one remaining false negative).
  Isolated Safari Command-T task: **3/3 completed passes**, **21.52 / 17.85 /
  17.65 s**, each independently checked for exactly one extra tab, task_status
  done, no pending action and no timeout. Earlier same-request diagnostic was
  0/2 at 240.25/200.67 s with repeated tab creation; this is a narrow task result,
  not a general GUI success rate or controlled broad performance comparison.
  Final artifacts: `.data/live_eval/browser-completion-fix/validation/summary.json`,
  `units.log`, `safari-first/` and `safari-repeats/`; no validation worker remains.
  Owner-authorized test window cleanup ran normally. No model adoption, commits
  or pushes. Qwen remains default. New live work requires fresh availability.

- **Development resumed; browser completion fix applied, final validation running.**
  Original staged source hash matched before applying; prior edits preserved.
  Native same-window Safari count changes after submitted actions may now invoke
  the full-goal verifier before another action, even with unchanged screenshot
  pixels. Unknown/invalid counts, rejected actions and remaining queues do not
  enable the check. Added readable native count-transition evidence; original
  goal, evidence validation and confirmations remain. The success/empty-missing
  grammar is scoped to observations carrying this native transition. Other
  completion observations retain their original grammar and plan handling.
  Seven new browser regressions and ten existing completion tests pass.
- **Rejected during validation:** applying the success/empty-missing grammar
  globally and supplying planner criteria to every task introduced a selected-
  document false success. A broader prompt-precedence experiment scored 17/20
  with 2 false positives (selected document and excess tab); it is removed.
  Generalized planner-criteria changes to app/diagnostic were also removed after
  a planner changed digits in a filename. Do not treat these experiments as fixes.
  Earlier scoped browser diagnostic was 8/8 with zero false positives; final
  narrowed revision is being rechecked. Completed full runs (1023, 1024, then
  1025 tests) were intermediate revisions, not final-source verification.
- **Fresh screen permission received in this session:** Safari-only live testing
  after offline checks. Detached bounded driver PID 31289:
  `.data/live_eval/browser-completion-fix/validation/run.py`. Stages: final full
  units -> local frozen verifier replay -> one isolated Safari Command-T task ->
  two repeats only if the first independent outcome/completion check passes.
  Offline false positives or any browser-case failure prevent GUI launch. No
  prohibited apps, cloud calls, downloads, model adoption or source changes are
  chained. Progress/logs and eventual summary are in that validation folder;
  STOP marker interrupts the current stage. App source is fixed during this batch.
  Let it run independently; inspect on request/completion, not an agent polling loop.

- **R3 batch FINISHED; rejected for adoption/live qualification.** Final
  `recursive-10/progress.json` reports finished; `summary.json` exists and no
  recovery/training/evaluation worker remains. Paired 406-row Ollama comparison:
  operation **373 -> 380/395**, app **303 -> 367**, target **356 -> 362**, but
  acts_on_computer **358 -> 349** with **18 critical per-row regressions** across
  gated fields. No candidate parse failures; follow-up exact remains 11/11.
  Completion diagnostic **23/36**, **0 false positives / 13 false negatives**
  (historical base diagnostic 30/36, not rerun as a paired completion condition
  in this batch). Improved aggregate fields do not offset these regressions.
  Artifacts: `recursive-10/r3/comparison.json`, `completion-{7,19,86}.json`,
  and `recursive-10/summary.json`. Qwen defaults unchanged; R3 separate alias
  retained, no adoption or GUI tests. Fixed-build batch is over; source edits
  may resume. Next useful development is review/validation of the staged browser
  completion candidate below, with fresh screen permission required for live tests.
  Earlier running-stage notes below are historical snapshots.

- **Latest requested state check:** base Ollama evaluation finished. Progress
  reports `eval-candidate`; supervisor PID 29476 and candidate evaluator PID
  30317 are present. Candidate log has printed its final report, but the batch
  has not yet recorded completion/comparison or produced `summary.json` at this
  snapshot. Completion diagnostics and the final gate verdict remain pending.
  No source/default changes or new jobs during this check.

- **Latest requested state check, ~17:18 EET:** quantization and separate
  `a11y-router-r3-test` registration finished. Supervisor PID 29476 is alive;
  base Ollama evaluation PID 29754 is running (`stage=eval-base`). Candidate
  evaluation, comparison and completion diagnostics remain pending; no final
  summary exists. Training is finished; the whole batch has not ended. No
  source/default changes or additional jobs during this check.

- **Latest requested state check:** R3 training finished all **300 iterations**,
  final adapter saved; run metadata reports `iteration limit`, 3445.04 s.
  Validation selected zero-based iteration **249**, loss **0.02471077**.
  Fusion, conversion-input preparation and F16 GGUF conversion finished.
  Recovery supervisor PID 29476 remains alive; quantizer PID 29667 is active
  (`stage=quantize`). Thus training is finished but the batch is still running,
  with paired Ollama evaluation and completion diagnostics pending. No final
  summary or R3 quality/adoption result yet. Source remains frozen; no new job,
  resume, GUI test or default change was made during this check.

- **Next development candidate staged (not applied):** R3 trainer and recovery
  supervisor were both alive at the requested state check; power heartbeat was
  current. App/native source remains frozen. Offline review found `goal_met`
  unconditionally skips browsers, including submitted shortcut/menu actions after
  a native tab-count change. This is a plausible contributor to the measured
  repeated-Cmd-T failure, not a demonstrated complete explanation.
  Candidate at `.data/queued-fixes/browser-completion-20261028/` includes a patch,
  replacement source, new tests and original/candidate hashes. Working copy:
  `/private/tmp/a11y-browser-completion-candidate/`. It permits the existing
  full-goal verifier after a valid same-Safari-window count change and submitted
  action, supplies the stable planned outcome to all completion checks, and adds
  an exact readable count-transition evidence value (single-digit numbers alone
  are excluded by the existing quote schema). Count changes do not declare success.
  Unknown/invalid/unbound counts, pending queues and rejected actions cannot enable
  this check; original goal, missing-parts check and observed-evidence gate remain.
  **20 staged offline tests passed** (16 completion + 4 browser-count), with mocked
  providers and no inference/GUI. No production application or success-rate claim.
  After R3 finishes, review/apply only if source hashes still match, run current
  unit checks and screen-free verifier probes, then request fresh screen availability
  for real-task validation. No competing GPU job, downloads, commits or pushes.

- **Recovery launched, 17:06 EET:** owner authorized continuing development.
  Detached supervisor PID 29476 (`recursive-10/recover_batch.py`) now monitors
  the **existing** trainer PID 26523; no new training process or resume was
  started. Both are nice 19; supervisor uses taskpolicy background and owns a
  caffeinate assertion. AC power was 100%. Progress now reports
  `train-supervised-recovery`; power.json identifies the supervisor. A singleton
  lock prevents competing recovery supervisors. It checks trainer identity,
  stop markers, power and fixed build, and stops training on guard failure.
  After trainer exit it requires a normal completion reason, final-save metadata
  and matching validation-best adapter copies before executing the original
  conversion/406-row Ollama comparison/36 completion-check chain. As this is an
  attached orphan, its original exit code cannot be collected; the power record
  explicitly reports artifact-based completion rather than inventing exit code 0.
  Script compilation and five isolated completion-gate checks passed. No new full
  unit run, GUI test, model adoption or app/native edit. Recovery log:
  `/private/tmp/a11y-router-r3-recovery.log`. Status and final summary retain the
  original paths. Let this shell batch run independently; no agent polling loop.

- **State audit, 16:58 EET (supersedes running-batch claims below):** R3
  trainer PID 26523 is alive, not SIGSTOP-paused, and orphaned under PPID 1.
  Last reported iteration is 240/300; validation through iteration 199 is saved,
  with best observed loss 0.027908 at zero-based iteration 149. No
  `train_batch.py`, `training_power_guard.py`, or `caffeinate` process remains.
  The cause of their exit is unknown; the outer batch log is empty. Thus the
  training subprocess continues, but the orchestration/power protection is absent
  and the conversion/evaluation chain cannot continue automatically. The JSON
  `running`/`training` statuses are stale as evidence of the whole batch's health.
  No final `summary.json`, STOP, or power.stop marker exists. No R3 Ollama
  comparison/completion result is available; training loss is not quality evidence.
  Confirmed original full-suite log: **1,017 passed in 89.655 s**, not rerun here.
  This audit started/stopped/resumed no job and changed no app/native source.
  Next: decide how to recover guarded orchestration without duplicating the live
  trainer, then explicitly run the remaining offline qualification stages after
  successful training and validation selection. Do not automatically resume or
  restart anything. Keep the source frozen and Qwen defaults unchanged meanwhile.

- **Latest owner instruction:** let the independent shell batch run and end the
  agent turn. No repeated polling, sleep loops or progress messages while it runs.
  Inspect results when asked or on an available completion notification; do not
  imply that an inactive agent will automatically continue coding. This docs-only
  turn did not start, stop or alter the batch. At its single status snapshot R3
  reported iteration 90; use progress/power files for the current state.
- Entry points updated together: root AGENTS.md/CLAUDE.md, root README.md,
  docs/ai/README.md, scripts/router_README.md and the historical Claude brief.
  Do not follow stale "no training run yet" or "run this next" statements below
  as current instructions. No commit/push requested for these edits.


- **LATEST: one actual fresh training batch is now running**, cycle 10, after
  **1017 unit tests passed in 89.655 s**. Owner asked to let a shell batch run
  without frequent agent polling/updates. Driver `recursive-10/train_batch.py`,
  progress `.data/live_eval/recursive-10/progress.json`, power state `power.json`,
  trainer log `r3/training.log`, outer log `/private/tmp/a11y-router-r3-batch.log`.
  Fresh rank-8/8-layer LoRA, 300-step bound with validation early stopping, local
  cached Qwen3-4B, 3e-5 LR, no downloads, HF offline. Training data snapshot orders
  required JSON fields then optional fields as observed in Ollama 0.34; label
  values/prompts preserved, held-out test copied byte-for-byte. New helper
  `scripts/router_canonical_data.py` has two preservation/order regressions.
  This is a format-alignment experiment, **not an established fix**.
- The shell chains: train → validation-best fuse → GGUF/quantize → separate alias
  `a11y-router-r3-test` → paired 406-row Ollama comparison → 36 completion checks.
  No live screen tests or automatic adoption. Nice 19/taskpolicy background;
  power guard stops/saves if unplugged at <=25%. Guard interruption must NOT
  automatically resume/convert. To request stop, create BOTH
  `recursive-10/power.stop` and `recursive-10/STOP`, or SIGINT batch parent so it
  forwards interruption. Do not edit app/native source during fixed-build batch
  (`ed570dba7c`). Checkpoints and original data remain separate and retained.
  Batch-owned `caffeinate -i -w <parent>` keeps system idle awake until it ends;
  the earlier four-hour caffeinate expired. Display can sleep; no screen jobs.
- Cycle 8 JSON fallback and cycle 9 matched-ChatML probes finished without rescuing
  R2. On 24 selected rows (23 routes + 1 follow-up), base grammar operation22/23,
  R2 grammar19/23; JSON fallback base21/R2 20. Matched ChatML R2 remains19/23,
  acts19/23. MLX R2 raw18/23, 5 parse failures. So template/order differences are
  **not proven sole cause**; no serving protocol changed or candidate adopted.


- **Update:** cycle 7 and its completion batch are finished. Saved R2 best was
  converted and registered locally as `a11y-router-r2-test`; no defaults changed.
  On 406 rows: operation 373/395 both, app 303→367, target 356→347, acts flag
  358→324, referent 82→351. Critical regressions include inspect_media→answer,
  new_window→launch_app and click_point→read_mail. **Rejected for adoption/live
  qualification.** Native completion diagnostic: base 30/36 (3 FP/3 FN), R2
  21/36 (0 FP/15 FN). Do not mistake reduced false positives for overall improvement.
  Artifacts `recursive-7/router-r2/comparison.json` and completion-comparison.json.
- Current active job is the **cycle-8 output-order hypothesis probe**, not the
  completed cycle-7 job described below. Training assistant JSON starts operation;
  Ollama constrained predictions start acts_on_computer. Training acts flags are
  balanced 760 false/760 true, so a simple class imbalance claim is unsupported.
  Testing existing provider JSON fallback on a deterministic 24-row paired subset,
  without changing source/defaults or retraining. This is hypothesis testing,
  not evidence the mismatch caused regressions. Progress `recursive-8/progress.json`,
  log `/private/tmp/a11y-router-order-probe.log`; same frozen build `ed570dba7c`.


- **Active job is offline conversion + router evaluation, not gradient training or
  screen control.** `.data/live_eval/recursive-7/progress.json` is authoritative.
  Driver `qualify_saved_router.py`, main log `/private/tmp/a11y-router-r2-qualification.log`.
  Converts the validation-selected saved R2 checkpoint to separate alias
  `a11y-router-r2-test`, then scores base/candidate on identical 406 held-out rows
  (matching SHA256 `37ca9676…c47647`), 8K context. Fixed app build `ed570dba7c`;
  **do not edit app/native source while running.** All conversion inputs/tools
  are already local, HF offline enforced. AC power 100%, 71 GiB free at start.
  Model defaults and original checkpoints retained; no adoption is chained.
- Last complete unit suite **1015 passed in 89.613 s**. Holo2-focused local API
  probe hits Finder Action target after exact text scope extraction (2.34 s,
  no screen action). Bare "Click Action." fell back; no unsafe widening applied.
- Current measured grounding: Holo2 **24/39** vs Qwen **17/39**, all three repeats
  identical over 13 crowded native scenes. Defaults unchanged. Seven differential
  wins visually audited in `recursive-5/holo-wins-audit.jpg`; native descriptions
  include captions/extension names not literally visible, so this is a diagnostic,
  not a general human-request capability score. Holo2 explicit focused selection
  uses one grounder call; full-policy fallback remains hybrid.
- Shortcut fast-path proposals **rejected**, not added to production. 13-case
  enum probes 8/13 then 9/13; boolean probe base 11/13, unsafe accepts repetition
  and another window. Installed trained candidate and Qwen3-VL-8B each 13/13;
  Qwen2.5-VL-7B 12/13 (compound unsafe accept). These small scores do not qualify
  any model for adoption. Older router's full comparison already fails critical
  regression gate (including click → read_mail); current job evaluates the later
  saved checkpoint that had not yet been converted.
- Screen tasks are finished/stopped; Safari still fails stopping despite correctly
  bound native tab counts. Appearance reaches pane but pauses on proposed Light
  click; stricter checker rejects pending/running tasks. Timer preflight corrected
  to include AX descriptions and fail closed; do not rerun it without verified idle
  state. No new gradients, downloads, commits, pushes or messages in this work.
- `caffeinate -dimsu -t 14400` PID 10772 remains the four-hour idle-sleep assertion
  begun ~10:47 local; does not override lid closure. Earlier active/pending notes
  below are chronological history; use current progress files for running jobs.


### Recursive cycle 1 (2026-10-28, active)

- Crowded replay **finished 234/234 calls**: Qwen 51/117 (17/39 every repeat),
  Holo2 72/117 (24/39 every repeat), all parseable; seven target differences all
  favored Holo2. First-scene medians 1.76/1.70 s. Candidate allocations 4.27/4.49
  GB; total resident 8.13/8.35 GB includes other models, not candidate-only cost.
  Offline supplied-target diagnostic, not live-task improvement. Old smaller set
  favored Qwen; default unchanged. Detailed results and limitations GUI_MODELS.md.
- Follow-up integration: explicitly selected Holo2 can use focused single-gesture
  grounding directly after the text scope call, avoiding a Qwen vision policy call
  for that eligible case. Model-info architecture/vision checks and all executor
  guards retained. Other GUI profiles do not silently use Qwen coordinates.
  14 focused tests pass; full suite `/private/tmp/a11y-recursive-6-unit.log` running.
  No additional downloads, saved model changes, training, commits or pushes.

- Cycle 5 full suite **1012 passed in 89.955 s**, build `b5b6cef68c`. Safari
  still **0/2**, 240.25/200.67 s. Direct native check during run confirmed helper
  returns the correct bound Safari window and tab count, but policy keeps issuing
  Cmd-T. Added facts alone did not solve semantic stopping; do not claim success
  or that task completion improved. No active screen jobs now.
- Crowded model replay is now running (not training), with actual 8K context
  recorded. MemorySampler additionally records per-model allocations so retained
  unrelated models are not mistaken for the candidate's cost; 7 benchmark tests
  pass including distinct per-model vs total allocation. Summary will be in
  `recursive-5/dense-analysis.json`. Default model remains Qwen.

- Cycle 4 finished: raw 2/4, but **0/4 after applying the pending/running-task
  eligibility check** (separate interpretation, raw file unchanged). Appearance
  2 runs paused at Light confirmation; Cmd-T 2 runs failed in 217/183 seconds,
  repeatedly creating tabs. Safeguard held and routing survived, completion did
  not. Queued replay deferred to finish the next general evidence fix first.
- Added bounded read-only Safari counts to target observations: window ID and
  tab count only, bound to the observed native window bounds (within 2 points).
  No activation/URLs/tab titles. Errors/permission timeout/mismatched geometry
  return unknown. Safari only; other browsers/Windows retain existing behavior.
  Task keeps initial counts and supplies current counts + same-window delta to
  completion verification and planning context. Does **not** declare success from
  key submission or weaken completion guards. Four regressions pass; AppleScript
  compiles. Final full suite `/private/tmp/a11y-recursive-5-unit.log` running.
- Cycle 5 driver waits for full-suite OK, reruns Safari Cmd-T twice with the stricter
  feature checker, then crowded 39-target Qwen/Holo2 localization (3 repeats).
  Artifacts `.data/live_eval/recursive-5/`; fixed app build during both jobs.

- Cycle 4 raw Appearance reports showed 2 passes, but inspection found both
  task_status=running with pending click Light and empty reply. **These are
  checker false positives, not completed-task passes.** The safeguard worked:
  only Appearance click receipt exists; Light remains pending. Features honest()
  now rejects pending actions and non-done tasks; two checker regressions pass.
  Current running process loaded the old checker, so audit its raw rows explicitly.
- Safari now executes Cmd-T, confirming task conversion repair, but repeatedly
  creates tabs instead of stopping. Missing mechanism: task observations lack
  before/after browser tab counts; screenshot-only verifier cannot establish
  the requested one-tab delta. Owned Safari window isolates cleanup. Next fix
  should add native bounded browser counts/transition evidence, not task wording
  rules or treat key submission as completion.

- Cycle 3 final unit suite **1004 passed in 89.162 s**, build `dbad42f696`.
  Live routing fix confirmed Clock starts a task, but **batch interrupted** before
  scoring: first observed Clock screenshot already showed paused 3-min timer
  at 00:24. Preflight ax_values missed Cancel/Resume because it omitted native
  accessibility descriptions; model resumed/interacted with that timer. Ownership
  cannot be established, so do not treat it as an isolated fixture or run another
  timer case until idle state is verified. Interrupted runner performed its usual
  cleanup; do not guess/restore the original timer state. No valid task-success
  score for cycle 3. Cancelled its queued model comparison before inference.
- Fixed general test AX text reader to include descriptions. Timer preflight now
  requires positive Add/Start UI evidence and rejects Cancel/Pause/Resume or empty
  state. Two regression tests pass. No new production phrase lists.
- Cycle 4 runs **Appearance + Safari Cmd-T twice**, omitting Clock, then the
  corrected crowded Qwen/Holo2 replay. Progress `.data/live_eval/recursive-4/`.
  Final production build remains fixed; do not edit app/native source while active.

- Recursive cycle 2 live rerun finished on build `22fa891a27`: **1/5 turns**,
  `.data/live_eval/recursive-2/features/20261028-122745/`. Clock and Safari Cmd-T
  never started a task; Settings reached Appearance but verification denied it,
  then model attempted Light twice; Preview reached thumbnails but first reply
  incorrectly denied capability (follow-up passed). These are real reliability
  failures; native state alone does not turn the run into a pass.
- Reproduced general conversion bug without model inference: click_point with
  auxiliary companion referent becomes computer_task in preserve_desktop_goal,
  then move_object on the second validation. `with_opening` now always assigns
  destination when promoting an action to a task, even with no opening step.
  Tests cover repeated validation across click/control/input actions, preserved
  pointing and explicit relocation. Live measurement pending, not claimed fixed.
- Settings safeguard: helper hit tests now expose ancestor roles only (no parent
  text/values). Model pixel clicks outside native sidebar/toolbar ancestry are
  treated as changes. Unverified/direct Settings controls require confirmation;
  focused and model click paths propagate risk to review. Some deterministic
  Settings navigation may now request confirmation until structural evidence is
  available; do not weaken this to improve scores. Owner's theme is not to be
  changed or restored by guessing its previous value.
- Motion check refined again: highlight + companion may mean actual requested
  relocation, so effect check still distinguishes it; a computer_task effect may
  not activate a highlight target. Original highlight label retained. Reading
  remains non-activating. New relocation regression covers semantic_plan.
- First cycle-3 unit pass: **1002 passed**; final source revision suite currently
  running at `/private/tmp/a11y-recursive-3-final-unit.log`. Driver waits for OK,
  then runs Clock/Appearance/Cmd-T twice; crowded 39-target Qwen/Holo2 replay
  (3 repeats) follows it, no screen actions. Previous queued replay failed CLI
  argument placement before inference; corrected global --out before subcommand.
  Artifacts/progress under `.data/live_eval/recursive-3/`. No commits/training.

- Cycle 2 started on owner request to proceed recursively. Added eval-only native
  helper restriction for prohibited app bundle IDs (before helper compilation or
  execution), plus Dock target check before pixel clicks. Normal production use
  unchanged. Unit checks cover all prohibited bundle IDs and normal/safe use.
- Context bookkeeping correction: recovery pair v2 was launched with
  `A11Y_OLLAMA_NUM_CTX`, but provider reads `A11Y_OLLAMA_CONTEXT`. Its report's
  hard-coded 8192 field is therefore unreliable; effective context was the saved
  environment/default (normally 16384). Both paired conditions share the same
  process/context, so paired accuracy comparison remains valid. Future runs must
  record the actual request options, not a hard-coded context label.

- Recovery pair v2 **finished**, same 406 rows/build `d9b246d019`. Operation
  remains **373/395 (94.43%)**, app 303/395, target 356/395; every scored
  field unchanged except referent **69 → 82/395**. All 13 changed predictions
  only repair contradictory companion referents on pointing/screen reads;
  no per-row prediction regressions. Mean latency 1.146 → 1.202 s (single
  sequential pair, not a controlled speed claim). This supports the operation-
  preservation correction, not a claim of better overall task success or proof
  of the extra motion classifier on real relocation errors. Live rerun pending.
  Full suite **993 tests passed in 97.021 s**. No model training occurred.
  Earlier running/pending notes below describe the now-completed jobs.

- Recovery follow-up: first 406-row motion-check pair **rejected**: operation
  373/395 → 367/395, target 356/395 → 344/395. It reclassified screen reads
  and two pointing requests as other operations. Reports preserved as
  `router-original-base.json` / `router-rejected-motion.json`. New revision lets
  explicit highlight/inspect_screen operations own their effect, normalizing a
  contradictory companion referent without another model call; regression test
  covers both through semantic_plan. A fresh fixed-build pair is running in
  `/private/tmp/a11y-recursive-router-pair-v2.log`; do not edit app source during it.
- Presence-protocol diagnostic completed: only **18/39 positive native targets
  hit**, with one positive abstention. Not adopted. The 13 candidate negatives
  include actual visible controls (e.g. window zoom), so they are not valid
  negative ground truth and must not be scored as such. No production protocol
  changed based on this experiment.
- Full unit suite rerunning after the operation-preservation fix, output
  `/private/tmp/a11y-recursive-final-unit.log`. No live run started at recovery;
  eval-only prohibited-app launch protection still needs auditing before one.

- Both heavy queues finished: **226 turns / 104 raw passes**. Features 19/55;
  harder 12/30 + 19/60 repeats; capability 18/27 + 36/54 repeats. Triage artifact:
  `.data/live_eval/recursive-1/batch-diagnosis.json`. Categories are provisional:
  18 native-outcome-true/report-rejected, 11 checker errors, 9 setup/runner errors,
  7 confirmed motion misroutes, 7 confirmation/fixture issues, 70 needing visual
  review. No adjusted success score or claim all failures visually diagnosed.
- Cursor dirty-footprint optimization is now applied to `cursor_flight.py`:
  clears old/new painted areas instead of the whole screen per frame. Pixel
  regressions cover every phase and both ordinary/4× pointer size. About 0.36%
  screen damage for adjacent movement; native FPS/lag improvement remains unproven.
- Added a local motion-effect check only for suspicious relocation/referent routes.
  It distinguishes companion relocation, pointing, app work, chat and uncertainty;
  no task phrase list. Corrected app tasks discard stale movement scope/openings,
  pointing/chat cannot inherit an app opening, and uncertainty does not act.
  The separate 12-case scope probe passes. **Not yet accepted pending held-out pair.**
- Fixed a checker bug in harder: TextEdit's native POSIX path string must not be
  coerced to an AppleScript alias. Other-file/missing-file checks remain negative.
  Setup timeouts/modal app faults are still unresolved, not claimed fixed.
- Full suite after initial cycle changes: **991 passed in 89.824 s**. Subsequent
  stale-opening safeguard has six focused regressions passing; final recheck pending.
- Completion schema experiment omitted meaningless missing text for met=true.
  Rejected: on 36 paired diagnostics, baseline 30 correct/3 FP/3 FN vs experiment
  31 correct/**5 FP**/0 FN. **No completion guard was softened or experiment applied.**
- Paired 406-row router evaluation is actively running via
  `.data/live_eval/recursive-1/router_pair.py`, log `/private/tmp/a11y-recursive-router-pair.log`.
  Base is complete: operation **373/395**, app **303/395**, targets **356/395**.
  Candidate pending. Same text model and rows; base bypasses only the new
  motion check using the original Planner method. Frozen app build **14dd624999**;
  **do not edit app source until this pair finishes**, or it rejects the measurement.
- Fresh native replay manifest: `dense-native.jsonl`, **39 targets / 13 real
  Finder, Maps, Safari, Settings scenes**, 25–46 distinct visible target positions
  per scene. Same-frame native geometry; menu-overlay scenes/duplicate target names
  excluded. AX stays enabled in real app tasks; withheld only in offline pixel
  fallback diagnostic. Needs visual audit before a capability claim.
- Queued `visibility_probe.py` waits for that router pair, then tests a candidate
  found/absent coordinate protocol on dense scenes. Not production code yet.
  Its negative rows are **candidate negatives requiring visual audit**, not known
  absent labels; do not score them as confirmed ground truth automatically.
- Owner requested caffeinate after display sleep. No live suite was running at
  recovery; unit tests had completed. `caffeinate -dimsu -t 14400` PID **10772**
  active from ~10:47 +02, expires ~14:47; receipt `recursive-1/awake.pid`.
  Assertions verified. Lid closure is not overridden. Only offline model work is
  currently active. No commits, pushes, training or downloads in this cycle.


### Heavy native validation queue (2026-10-28, active)

- Owner requested harder real macOS tests and less agent usage. The three-icon
  canvas is only a mechanism smoke test, **not a capability benchmark**. Probe-1
  was stopped after discovering a clipped test window. Probe-2 independently
  recorded **4/4 correct pointer hits**; its raw 3/4 score includes a checker race
  requiring Safari still be frontmost after the click. That redundant requirement
  is removed; neither probe is adoption evidence.
- Current frozen app build **da7b064139**. **981 unit tests passed in 89.200 s**.
  Initial live queue is active: features (55 turns), harder (30), capability (27),
  one pass each. Fixed follow-up adds two passes each of harder/capability: **226
  total scheduled real-app turns**, seed 17, 8K context, local-only, background
  priority. No private apps, training, downloads, commits or app-source edits.
- Driver/progress/logs: `.data/live_eval/focused-native-queue/`. `run.py` is the
  initial queue; `repeats.py` waits for successful initial completion, then repeats.
  **Stop both by creating `STOP` in that folder**; the repeat watchdog interrupts
  the active owned runner for cleanup. Source changes/errors stop the follow-up.
  A four-hour ceiling prevents a stuck unattended batch. This is a fixed Python
  test queue, **not unattended agent coding**. Review final results before claims.
- Each run saves runs.jsonl/app.log/cropped owned-window evidence. Opt-in
  `A11Y_EVAL_GROUNDING_EXPORT=1` adds same-frame native target names/boxes, no field
  values/IDs/document paths; private app captures stay excluded. Fully clipped
  windows export no target geometry. Use **crowded real native scenes** for the
  next frozen grounding comparison, not the three-icon fixture. New export has
  pure geometry/privacy regressions. Diagnose every failed task/check after runs.
- Owner also reported broad UI/cursor lag. Observed text+vision model residency
  was ~10.6 GB at 16K with ~4.4 GB system swap. At 8K, runners reserve ~8.2 GB;
  saved default remains unchanged. Test UI inherits nice 19 (a test-time confound).
  Cursor overlay calls full-screen update every animation frame. Candidate dirty
  footprint fix is **staged only**, not applied during measurements, in
  `/private/tmp/a11y-cursor-damage/`: 15 rendered-footprint checks pass, adjacent
  cursor motion requests ~0.36% of screen instead of 100%. This is paint-area
  evidence, not measured native FPS. After the fixed-build queue, review/apply with
  tests and live UI verification. Do not claim all lag is fixed.
- Outstanding after queue: inspect complete results and crowded native sidecars,
  compare repeat reliability, diagnose failures, benchmark focused grounding on
  fresh dense real-app scenes, then address cursor repaint/UI responsiveness.
  Qwen remains default. Holo2 is installed but not adopted (results below).


### Focused Qwen grounding implementation (2026-10-27, continuing)

- Owner approved implementing the focused grounding path, fresh-screen checks and
  capability/features/harder revalidation. No commits/pushes authorized.
- Native/AX, OCR and queued tools stay first. Unresolved explicit pointer goals
  get a small local semantic-scope check, then a supplied-target Qwen3-VL coordinate
  call. Compound/conditional/reference requests retain the original task loop.
  Exact target coverage prevents dropping qualifiers or subsequent instructions.
- New path is limited to verified local Qwen3-VL metadata, first pointer request,
  observed front-window bounds and normal validation/hit/capture/confirmation gates.
  Named native mismatches fall back, Stop/access-generation changes abort. A
  localizer cannot choose another app/action/gesture. Context length matches the
  normal vision policy to avoid context-size reloads. `A11Y_FOCUSED_GROUNDING=0`
  disables the new path for paired comparisons; model defaults stay Qwen.
- Scope diagnostic v1/v2 exposed real errors (gesture scope, condition/reference
  acceptance). General category schema + exact source/coverage checks replace the
  boolean gate; v3 **12/12** local cases pass. No task-specific phrase lists.
- Added `--suite grounding`: four seeded, fresh loopback canvas layouts with three
  unnamed icons each, no target labels in AX/OCR. Browser hit events give independent
  truth; each turn must add exactly one correct hit. Build is pure, fixture-owned
  Safari window/server cleaned up. Pure fixture/safety tests pass.
- Current app build before live checks: `598950d9d1`. Final full unit suite log:
  `/private/tmp/a11y-focused-final2-unit.log` (pending). Do not edit app source while
  live measurements run. Fresh paired tests and full safe suites are next.


### Owner-authorized Holo2 download and benchmark (2026-10-27)

- Installed **a11y-holo2-test:latest**, Q4_K_M + matching F16 projector, using
  pinned `mradermacher/Holo2-4B-GGUF` revision
  `26722cf67095413610e95d086f0bfe9fa216c6c7`. Both file sizes/SHA-256 match the
  release LFS metadata; total source download ~3.55 GB. Registered both files via
  loopback `/api/blobs` + `/api/create`; Ollama 0.34.0 correctly recognizes vision.
  Provenance/inspection/import/smoke receipts: `.data/models/holo2-4b/`.
- Working native cookbook JSON / think=false image inference verified. Model is
  available for explicit opt-in; **no app defaults were changed** and no commits.
- Actual paired replay: `.data/live_eval/gui-holo2-grounding-1/comparison.json`
  and `analysis.json`. 22 legacy targets/eight scenes × three repeats: **Qwen4B
  45/66; Qwen+Holo2 45/66**. Both 15/22 targets pass all repeats. After removing
  two independently pre-flagged ambiguous labels, both 45/60. No parser/transport
  errors. This is proposal grounding, not live task success or an adoption gate.
- Median whole-request time **3.24 s → 4.60 s**; median sampled resident Ollama
  models **4.27 GB → 8.76 GB**. Holo2 improves two targets but regresses two.
  One improvement also involves changed Qwen policy output, not just grounding.
  **Do not adopt this hybrid**: no net accuracy/reliability gain, increased time/RAM.
  Saved-frame visual audit identifies actual wrong controls and ambiguous labels;
  original gold boxes were retained. All inference local; no screen takeover.
- Extra already-installed Qwen3-VL-8B replay: **27/66**, p50 **5.68 s**,
  sampled model residency max **6.90 GB** (`qwen8b.json`). Not recommended for this
  protocol; not a general GUI ranking. No additional weights downloaded.
- Native direct-localizer isolation completed: same supplied targets/bytes/JSON
  grid prompt, one call, no policy. **Qwen4B 54/66 vs Holo2 48/66**, all points
  repeat exactly. Unambiguous subset **51/60 vs 48/60** (one target difference).
  New-scene first-pass median **2.70 vs 2.68 s**; overall cached replay **0.94 vs
  1.16 s** is NOT a changed-screen/live-task latency estimate. Native residency
  **4.27 vs 4.49 GB**. This small set supports no Holo2 adoption; it suggests testing
  a constrained supplied-target prompt for Qwen. No production improvement claimed.
  Artifacts: `native-localizer.json`, `native-analysis.json`, pinned diagnostic driver.
- `scripts/gui_benchmark.py --localization-only` now reproduces this diagnostic
  using explicit row `target`, verified Qwen3-VL/Holo2 grid protocol and one local
  call. Focused tests **23 passed**; a four-call real CLI smoke reproduced expected
  results. Full current suite: **964 passed in 89.290 s**
  (`/private/tmp/a11y-holo2-final-unit.log`); `git diff --check` clean.
- All GPU benchmarking is finished; diagnostic model caches unloaded. Weight files
  and a11y-holo2-test registration retained. No screen takeover, app relaunch,
  defaults changed, commits/pushes, training or further downloads.


### GUI-specialized local models (2026-10-27)

- Requested model investigation/integration extends the existing pipeline; **Qwen3-VL
  remains the default**, no weights downloaded, no commits/pushes. See
  `docs/ai/GUI_MODELS.md` for primary sources, formats, limits and commands.
- Verified all five public checkpoints/configs/processors. Exact Holo3.1 ID is
  `Hcompany/Holo-3.1-9B`; OpenCUA is `xlangai/OpenCUA-7B`. Host: 24 GiB,
  Ollama 0.34.0. Metadata/revisions: `.data/live_eval/gui-models-research/availability.json`.
- Experimental opt-in Holo2 grounder keeps the current Qwen/native policy and uses
  cookbook JSON / 0–1000 coordinates only for pixel clicks. Experimental UI-TARS
  translates one restricted native action with processed-image pixels (28-grid).
  Both use existing loopback-only cancellable Ollama transport, then existing task
  validation/execution/confirmation. Unknown syntax/actions/truncation stop.
- OpenCUA has a custom architecture with no local converter registration: parser
  tests exist, but app selection is disabled. Holo3.1/Holo4 selection is disabled
  until their specific local action/tool protocol and runtime are qualified.
  Holo4's 35B resident weights make it a poor first experiment for 24 GiB.
- Settings connection details and env `A11Y_GUI_PROFILE` / `A11Y_GUI_MODEL` provide
  explicit selection. No alias guessing or automatic downloads. New policy clicks
  use navigation only for exact unambiguous observed targets in read-only goals;
  normal sensitive-label guards still apply. Other external proposals need review.
  Point-targeted scrolling/five-second UI-TARS waits are explicitly unsupported.
- `scripts/gui_benchmark.py` replays frozen permissioned app-like observations without
  acting: strict unsnapped hits/gestures, schema validity, repeat reliability,
  latency/inference timings, model/dataset hashes and sampled Ollama residency.
  Residency is NOT process RSS or true allocation peak. It also summarizes existing
  live runs' independent outcomes/repeated success; live scopes retain model choices.
- Screen-free synthetic smoke `.data/live_eval/gui-models-smoke/report-final.json`:
  installed Qwen3-VL-4B **6/6**, two deliberately simple scenes × three repeats;
  p50 **2.62 s**, sampled total resident Ollama models **5,521,696,357 bytes**.
  This only tests benchmark plumbing, not real-app capability. All five new models
  explicitly skipped (uninstalled or unsupported). **No model ranking/adoption is
  justified** without owner-installed weights and repeated real tasks.
- Final full suite: **961 passed in 90.203 s**. Afterward, a workflow-metric
  regression plus adapter/benchmark/capability checks: **30 passed**. Workflow
  success requires every turn; incomplete runs have no task-success score, and
  single runs do not claim repeat reliability. `git diff --check` is clean.
  No screen automation was started for this integration work.


### Current evaluation session (2026-10-26)

#### Morning continuation (2026-10-27)

#### Gym testing window (2026-10-27, 19:02–21:02 +02)

- Requested work committed locally as **d09ca4a** on main; not pushed. Subsequent
  gym-window changes remain uncommitted. Training stays off to isolate live timings.
- Focused Finder baseline `finder-workflow-6/20261027-190244/`: **2/5** (select and
  explicit double-click pass; nested navigation/document workflow fails). Initial
  interaction correction produced **4/5** in workflow-7, but repeats 8–10 regressed
  to 2/5; do not present that single 4/5 as a stable improvement.
- Current work interprets only open versus select for an already grounded native
  file item using a small, fixed local schema. Exact pointer/target checks and
  sensitive confirmations remain. Duplicate AX labels at the same center are one
  physical target. Explicit click gestures retain their requested behavior.
- Desktop observations now serialize native collection and return an atomic copy,
  so a background basic poll cannot replace the detailed sample before consumption.
  Access disable/Stop uses a separate state lock and remains immediate.
- Verifier experiments exposed two real issues: free-form paragraphs in the exact
  evidence field, and relative instructions being reinterpreted after navigation.
  The final prototype keeps the original verifier instructions and fields, but
  constrains evidence to an enum of real observed values. Assessment-first and
  missing-first replacement protocols were tested and rejected after false
  positives or widespread false negatives. Executed openings retain native
  parent/item identity on their receipt.
  Native path comparison computes whether that precise folder was entered; a false
  comparison prevents completion even if the model says met. Compound outcomes
  still need the independent full-goal check. Boolean completion-shortcut experiments
  were rejected after false positives; no such shortcut remains.
- File-opening transitions now use bounded settling of BOTH native identity and OCR,
  as app launches already did. This addresses metadata/image disagreement after a
  double-click. Seventeen focused tests pass, including Stop during settling and
  rejection of a selected parent/wrong parent. Related reported-failure suites have
  also passed during development; final full suite is still pending.
- Diagnostics 11–13 confirmed `navigation.entered=true` but `met=false`: the
  4B verifier reapplied “continue into” from the new folder and demanded another
  same-named child. The existing planner's original `done_when` now accompanies
  the full original request for that native folder attempt; it is explicitly NOT
  evidence. Exact native values
  can prove completion even when the full path is not rendered on screen.
- `finder-workflow-14/20261027-195958/`, build `800a39890c`: **10/10 turns across
  two complete five-turn conversations**, versus baseline workflow-6 2/5. Select
  8.31–8.56 s; explicit folder double-click 16.24–16.88 s; implicit nested entry
  26.97–30.87 s; document opening 22.24–24.31 s; content answer 25.61–26.82 s.
  Cropped screenshots independently show Supplies open and the manifest content.
  This is a narrow result, not a full-suite success rate. Subsequent OCR settling
  compares sorted text to avoid waiting merely because OCR ordering changed.
- New screen-free `scripts/completion_eval.py` uses the actual verifier prompt,
  schema and evidence check on 12 paired synthetic scenes; `--with-plan` generates
  criteria through the app planner. It reports false positives separately and
  never adopts a model. An exploratory 8B probe got 9/11 but falsely accepted an
  unread compound task and a Settings sidebar label; **not adopted**. Handwritten
  criteria experiments are diagnostics, not an app-quality benchmark.
- Capability manifests now vary counts and destination with the suite seed, and
  their checks derive answers from the fixture. Old constant “Cedar ridge/43”
  cannot pass every run. The conversation-suite CLI now actually forwards `--seed`.
  Random fixture content remains test data only; no production task phrase rules.
- Final screen-free verifier check, `completion-eval-final-seed7.json`: **11/12,
  zero false positives**, using actual planner-generated criteria for folder attempts.
  One readable-document case returns `met=true` but `missing="}"`; the code correctly
  refuses that contradictory response. This is a small development set, not an
  adoption benchmark. The Settings sidebar/native-title guard now applies to the
  final report check too, rather than only early completion.
- Full unit suite before that final verifier simplification: **939 passed in
  89.525 s**; newest focused block **35 passed**. Final full recheck is running.
- Saved artifacts are evaluation-only cropped owned fixtures. Broader capability
  validation and the final full suite are pending. No private apps, cloud inference,
  messaging, push, training, or Arch setup occurred in this window.

- Owner requested a local commit on main. Review/checks: **913 unit tests passed
  in 87.430 s**, plus four focused router-gate tests after adding per-row
  acts_on_computer regression protection (aggregate gains cannot offset a new
  action-classification error). No model adoption. CUDA script remains experimental;
  the attempted real-tokenizer check had no tokenizer at the selected artifact path
  and did not run. No GPU claims are supported for that draft.
- Owner then authorized roughly **two hours of Mac use while at the gym**. Finish
  the requested commit first; resume local live diagnosis on current code, prioritizing
  Finder navigation and false-negative completion verification. No training contention
  or private-app tests; no push unless separately requested.

- Owner asked about deeper training on Arch, then clarified **“not rn js as an
  idea”**. No SSH connection, remote setup, transfer or training is authorized now.
  A draft `scripts/router_train_cuda.py` was prepared before that clarification:
  offline local-HF QLoRA, assistant-only loss, no truncation, full optimizer/RNG
  checkpoint resume checks, validation-only checkpoint selection, no uploads or
  test-set training. Five pure CPU data/resume tests passed; CUDA/PEFT training and
  VRAM fit have **not** been tested. Treat it as experimental, not a working remote
  training setup. No dependency installation or model download occurred for it.
  Resume this idea only when the owner wants to set up the other laptop.

- Powered training run has **finished cleanly** (guard status complete, return 0).
  It stopped early at the 200-step validation boundary of the 300-step continuation
  (about 500 cumulative steps), rather than running all 600. Current adapter weights,
  validation-selected `best/` weights/config and run metadata are preserved under
  `adapter-resumed-300-ac/`. Read `run.json` for exact zero-based best iteration and
  validation loss. No trainer, benchmark or live suite is currently running. No
  conversion/Ollama comparison has been run for this final candidate, so no adoption
  or desktop-quality improvement is established.

- Owner again authorized completing/running training. Power check: **AC, 14%,
  charging**. Long-paused worker 65130 had exceeded its wall budget and was retired.
  Standalone run restarts from saved cumulative step-300 weights for 300 additional
  steps, output `pipeline-20261027T083701Z-62299/adapter-resumed-300-ac/`, log
  `/private/tmp/a11y-router-r2-powered.log`. Fresh optimizer; same frozen data.
  `training_power_guard.py` records `.data/training/router/r2-powered-status.json`
  and cleanly stops if unplugged at <=25% or its `.stop` file appears. No automatic
  resume, live tests, conversion, benchmark or model adoption is chained.
- Trainer now handles interrupt/termination with an atomic save of current LoRA
  weights and records the last reported step, avoiding loss back to a 50-step
  checkpoint on future clean stops. Base weights are explicitly refused by that
  save path. Twelve focused offline training-tool tests passed; no screen activity.

- Owner's manual session exposed “hey ally” → “Which Safari window: Google or
  GitHub?”. Reproduced locally: raw router correctly emits answer/acts=false but
  referent=companion. `semantic_plan` turned it into movement; service also popped
  the action flag, so revalidation lost the conversation classification. Now an
  explicit non-action answer is preserved and the flag survives revalidation.
  Removed the unrelated Safari clarification example from grounding instructions.
- Evaluation confirmations had persisted into the normal messages table.
  Eval-mode Store history is now session-only, while profiles/consent remain in
  their existing store. All history writes (including confirmed app writes) go
  through this boundary. Six exact test exchanges reported by the owner were
  archived under a separate owner with a 0600 SQLite backup and restoration map:
  `.data/backups/history-before-eval-isolation-20261027T124843Z.sqlite3` and matching
  `eval-history-archive-*.json`. No normal conversation was cleared.
- Chat prompt recognizes A11y/Ally as the same name, and questions about the
  companion no longer trigger low-confidence web lookup. Text-only local check
  (temporary memory-only store, no app access) now greets normally and keeps all
  three reported messages free of desktop actions. The final typo/insult still
  gets an overly literal answer; conversation quality is not fully resolved.
  New source build `c19f557c2b`. Final full suite: **904 passed in 87.517 s**.
  Owner approved relaunch after checks; corrected source launched for manual use
  (log `/private/tmp/a11y-manual-session-fixed.log`). No screen tests or training
  were resumed for this diagnosis.

- Owner reported battery drain. **Trainer 65130 is now SIGSTOP-paused** immediately;
  battery probe showed 39%, discharging. Last printed resumed iteration is 40
  (about 340 cumulative); durable resumable checkpoint remains 300 in
  `adapter-resumed-250/`. `adapter-resumed-300/best.safetensors` is its initial
  validation-selected snapshot, not proof of a newly saved 350-step checkpoint.
  Initial full validation took 366 s; another full validation runs every 50 steps.
  No automatic resume. In-memory unsaved progress and memory allocations remain
  while paused; original wall alarm keeps counting and may expire during a long pause.

- **Immediate correction:** owner meant resume TRAINING, not testing. Capability-4
  was interrupted at startup; no suite result. No live-eval/source app worker was
  present on the follow-up process check. Standalone training restarted from the
  saved cumulative step-300 weights for 300 additional steps, output
  `pipeline-20261027T083701Z-62299/adapter-resumed-300/`, log
  `/private/tmp/a11y-router-r2-resumed-300.log`. Fresh optimizer, same frozen data.
  Internal checkpoint validation only; no live tests, conversion or benchmark jobs
  are chained. This correction supersedes the test-resume entry below.

- Owner authorized **resuming testing** after stopping training. Full capability
  rerun is active, `.data/live_eval/capability-4/` (log
  `/private/tmp/a11y-capability-4.log`): Finder gestures/planning, long conversation,
  and loopback web tasks on current code. Training remains stopped; no adoption or
  training job is authorized by this test-resume request. Source stays unchanged
  during each suite. Native reply-button diagnosis remains open afterward.

- **Owner requested stop and save:** resumed worker 63946 has exited cleanly via
  its stop handler. Latest durable weights are resumed step 50 = **300 cumulative
  steps** (`adapter-resumed-250/adapters.safetensors` and numbered checkpoint).
  Last logged work was resumed step 90; later unsaved updates are not retained.
  Best-validation weights are preserved separately, with loadable `best/` config.
  Safetensors headers/offsets and SHA-256 hashes were checked without loading models;
  `adapter-resumed-250/owner-stop.json` records the actual manual-stop reason (the
  handler's generic run.json calls it a wall-limit stop). Optimizer state is not
  checkpointed. No training, benchmark, conversion or live-test worker remains.
  Resume only when the owner asks.

- Owner subsequently authorized **training only, no testing**. The pause exceeded
  the original trainer's SIGALRM wall budget. Old pipeline PID 62299 and paused
  worker 62307 were terminated, preventing all downstream benchmark/live work.
  Training restarted from saved step-250 adapter weights for 350 additional steps,
  output `pipeline-20261027T083701Z-62299/adapter-resumed-250/`, log
  `/private/tmp/a11y-router-r2-resumed.log`. The same frozen data/config are used;
  optimizer state restarts (not an exact step-290 continuation). Internal validation
  still selects checkpoints as part of training. No conversion, Ollama evaluation,
  or live test is chained to this standalone job. No automatic screen activity.

- Owner needs the Mac and proposed moving training to the 32 GB / RTX 4060 laptop.
  **Round-2 trainer PID 62307 is SIGSTOP-paused**, last logged iteration 290,
  durable checkpoint/best weights at iteration 250. Parent pipeline is waiting;
  do not start another GPU job or resume automatically while owner uses this Mac.
  Pausing stops computation but keeps allocated memory. MLX weights need format
  conversion or a fresh CUDA LoRA run to move to NVIDIA; don't claim transparent
  optimizer-state migration. Handoff previously identifies the other laptop as Arch.

- Owner explicitly requested **training and code improvements together** after the
  measurement queue completed. Router round 2 is now running locally in
  `.data/training/router/pipeline-20261027T083701Z-62299/` (driver log
  `/private/tmp/a11y-router-r2.log`). Fresh adapter, 600 planned iterations,
  learning rate 3e-5, same rank/layers. `router_rebalance.py` resamples only existing
  training rows: 760 action versus 160 non-action examples become 760 each;
  33 resolver examples remain, total 1,553. Validation and test files are byte
  identical to round 1, with hashes recorded. This tests the action-bias hypothesis,
  not a proven improvement. Pipeline now includes the conservative paired comparison.
  Offline cache/no-index environment is enforced; model defaults stay unchanged.
- New code fixes: Qt built-in bound methods (notably reply QWidget.close) now get
  weak callback bindings without throwing TypeError halfway through native title-bar
  setup. Native Control-click no longer opens chat on face or margin. Finder rejects
  a second consecutive slow single-selection of the same observed filename, requiring
  an opening gesture or current-selection verification instead of entering rename.
  Twenty focused input/native regression tests passed. Full suite and two arithmetic-
  only native UI live checks are running; no Finder workflow improvement claimed yet.
- Follow-up verification: full suite **897 passed in 91.888 s**, plus eight focused
  mouse/checker regressions after the final checker adjustment. Round-2 trainer
  completed initial full validation (loss 1.7838, 371 s) and reached iteration 40
  (training loss 0.036); this is progress, not a quality/adoption result. Validation
  at each 50-step boundary is slow and may leave the log quiet for several minutes.
- Right-click stimulus was another harness problem: System Events `click at` can
  act as AXPress despite held Control. `eval_mouse.py` now sends real right-button
  events only after an AX hit verifies the exact owned process; no model uses this
  test helper. Right-click passes live in `ui-fixes-3` and `ui-fixes-4`.
  Reply button test still fails to reliably resolve the native reply window through
  AX (Qt's reply_shown flag is not sufficient evidence of native visibility).
  Padding-safe title lookup is tested but does not resolve that failure; don't mark
  reply buttons fixed. Last isolated rerun: `ui-fixes-5/20261027-104811/`.
  No other live task suite is running alongside training.

- **Queue finished (checked 09:42 +02): no live tests or training are currently
  running.** Harder-3: 9/30 raw passes; capability-3: 12/27 raw passes. These are
  baseline results with known checker faults, not adjusted success rates.
- The reviewed candidate patch was applied by the follow-up driver, build
  `4ab0eb100f`. Full unit suite: **886 passed in 252.903 s**. Targeted features
  rerun `.data/live_eval/features-fixes-1/20261027-093449/`: **2/7 passes** (Maps
  and the second Notes turn). Calculator, Finder sort, first Notes turn and both
  UI checks still fail. Do not claim those mechanisms are resolved; inspect each
  saved observation and app.log next. No further screen automation is queued.
- Paired Ollama comparison completed on the same 406 rows. Operation: base
  373/395 (94.43%), candidate 382/395 (96.71%); app names 303/395 (76.71%) versus
  355/395 (89.87%). However targets regress 356→343 and acts_on_computer
  358→322, with individual operation regressions too. **Do not adopt the candidate**
  on these results. Full comparison: router pipeline directory's `comparison.json`.

- Full features baseline finished on build `b0d3bd000b`: **55 recorded turns,
  11 raw passes, 42 failures, one Spaces skip, one drag awaiting owner confirmation**.
  One pass is the deliberately expected first-turn failure in recovery testing;
  this is not 11 successful computer tasks. Provisional per-turn classification:
  `.data/live_eval/features-2/20261027-075542/diagnosis.json`. Several native checks
  are broken (Calendar menu labels, Finder `name column` enum, right-click script,
  missing reply-window setup); do not silently count those as product successes.
- Harder baseline is running at `.data/live_eval/harder-3/20261027-084508/`.
  A static local queue then runs capability and the paired Ollama router scores,
  serially to avoid GPU contention. Progress/stop file live under
  `.data/live_eval/continuation-20261027/`; it stops at the original authorization
  deadline, 2026-10-27 11:07:56 +02, or if source changes. It performs tests only,
  not unattended code changes. No claim of continuous overnight coding is justified.
- Full current-source unit suite: **871 passed in 93.431 s**. Three conversion
  helper tests also passed separately afterward. Production source is held stable
  during baseline runs. Candidate fixes are staged separately in
  `/private/tmp/a11y-next-fixes-20261027/`: app-opening plan idempotence, Notes title
  duplication, and the verified checker faults. They are **not applied** to the
  running source. Focused staged routing/mac-app tests: 44 passed; five focused
  new regressions passed. Staged full suite: **879 tests passed in 98.430 s**;
  three further checker regressions passed afterward (including compiling the
  control-click AppleScript without executing it). Reviewable candidate files,
  original/candidate SHA-256 manifest and `candidate.patch` are also preserved in
  `.data/queued-fixes-20261027/`. Live revalidation remains required. Do not apply
  during the unchanged-build measurement queue.
- Additional staged correction: final native hit validation now requires the exact
  observed name (Unicode normalization allowed), rejecting prefix collisions such
  as `Report` versus `Report backup.txt` and punctuation collisions. Both planning
  and execution share the same check. 345 related tests passed; final full staged
  suite: **885 passed in 94.110 s**. Four checker tests passed separately after
  adding the empty-AX OCR fallback. Cropped Maps shot `retry_maps_place-1/shot-0009.jpg`
  independently confirms Eiffel Tower details were open; its false result is a
  checker failure, not evidence of failed navigation.
- A second static driver, `.data/queued-fixes-20261027/continue.py`, waits for the
  complete baseline/router queue, checks every original file hash, then applies
  **only the reviewed saved candidate files**, runs the full unit suite, and reruns
  six affected feature groups (including both Notes turns). It never adopts a
  router model or commits. It stops on either queue's `STOP` file, an intervening
  edit, a failed stage, or the same 11:07:56 deadline. Progress is in that directory's
  `progress.json`. This is a fixed validation plan, not an agent reasoning/coding
  unattended. If it has applied files, inspect logs/results before claiming fixes.
- Remaining Finder mechanism, **not fixed**: harder-3 nested-folder frames show
  Recipes selected after two single clicks. Observation then has Finder's detached
  `AXTextField`, parent `AXApplication`, with no focused window bounds. The code's
  Escape recovery is rejected by normal input authorization because it cannot bind
  a window. Do not remove that guard; establish the actual native window first or
  prevent the second selection by correctly using observed `open_target`.
- Completion verifier is also rejecting correct native outcomes (Get Info,
  Preview thumbnails, and several Settings panes). It sometimes demands a literal
  phrase such as “Wireless Accessories” even when Bluetooth is the requested view.
  Preserve independent evidence checks; the fix needs semantic goal criteria and
  exact observed facts, not accepting action submission or removing verification.

- The automated overnight goal reached a **session usage limit** after about 53
  active minutes. Do not describe that as 12 hours of continuous work. The owner
  returned in the morning and correctly challenged that claim. No live suites
  had continued during the gap; the background router job later failed at tool setup.
- Finder source prefix rerun: **6/6 turns across three fresh runs**, build
  `b0d3bd000b`, `.data/live_eval/finder-gesture-repeat/20261026-235636/`.
  Single-click selects the exact folder without a filename editor; double-click
  opens it. Times: single 8.69–8.91 s, double 8.33–8.50 s. This is a two-turn
  diagnostic prefix, not a complete capability-suite score.
- Features full run is now active on that unchanged build:
  `.data/live_eval/features-2/20261027-075542/`. Early Calculator/Clock failures
  route to companion `move_object`; Calendar checks also use obsolete menu names
  `Day`/`Week`, while this Mac's native menu lists `By Day`/`By Week`. Final score
  and per-failure classification are pending; do not count these partials as a suite result.
- Router training actually completed 300 iterations (5,378.5 s including pauses),
  best validation checkpoint at iteration 299, loss 0.020032. MLX result on the
  frozen 406 rows: operation 86.08%, 54 invalid/unparseable results, follow-up exact
  90.91%. This does not establish Ollama quality and no app default was changed.
- Conversion recovery: llama.cpp pins NumPy 2.2.6, incompatible with the repo's
  Python 3.14 tool environment. A separate managed Python 3.13.15 and
  `.data/training/tools/venv-py313/` now satisfy the pinned dependencies. A separate
  `gguf-input/` metadata view translates the exported list-valued special-token
  field for Transformers 4.57; the original fused export stays intact. Vocabulary
  hashes and sample token IDs match exactly between original and conversion views.
  F16 GGUF export and Q4_K_M quantization succeeded; `a11y-router-test` is registered
  in local Ollama. Paired base/candidate scoring is pending; app defaults remain
  unchanged. Reproducible metadata preparation lives in `router_gguf_input.py`
  with offline tests. Pipeline now uses `venv-py313` and fails before training if
  its conversion interpreter is incompatible (override `A11Y_CONVERSION_PYTHON`
  or `A11Y_CONVERTER_VENV` for a different compatible installation).

- **Overnight authorization:** owner subsequently authorized up to 12 hours of local
  code/test/training work and use of the Mac, then specifically prioritized Finder's
  accidental renaming. No commits/pushes, private apps or messaging are authorized.
- Added `--suite capability`: isolated Finder primitives, scrolling/planning, a
  long conversation with ten intervening requests, and a loopback-only web workflow.
  Native state and randomized fixture facts determine success. Build is pure.
- Harness now batches cleanup inventories, retains per-turn plans/events and unique
  screenshot folders, and crops saved evidence to the observed test window. Native
  AppleScript probes lift background I/O throttling only; model jobs stay background.
- Finder investigation: generic AX field focus can enter filename editing. Finder
  item clicks now use real mouse events; explicit requested double/right clicks keep
  their gesture after target selection. Read-only filename editors are cancelled;
  renaming keeps confirmation. Exact native filenames supplement unreliable OCR.
  A final native hit check rejects a different file/menu command under the point.
- Live input probe (no inference): after parking the pointer away from traffic-light
  hover menus, one click selected exactly one folder with AXOutline focus (no name
  editor), then a double-click opened that same fixture folder. Evidence:
  `.data/live_eval/input-probe-1.json` and `input-probe-shots/`. Source end-to-end
  validation is still pending; do not claim the whole Finder workflow passes yet.
- Early capability/gesture runs are incomplete diagnostics. A Cold Turkey crash
  notice and later a window-tiling hover menu contaminated some steps. The harness
  now parks the pointer on the test companion before creating fixture windows.
- Conversation retention is extended to 64 bounded messages (18k-character reply
  prompt limit); explicit older-task references get up to 16 prior user requests,
  not app/screen results. Privacy and distant-reference regressions pass offline.
- Router pipeline running locally at background priority in
  `.data/training/router/pipeline-20261026T211046Z-27265/`; training was temporarily
  suspended during live diagnosis, then resumed. It now scores a fresh Ollama base
  on the identical frozen rows. `router_compare.py` rejects mismatched rows/runtime
  and operation/scope regressions; it never changes app defaults.

- Full unit suite run outside the sandbox: **843 tests passed in 85.966 s**.
- Owner handed over the Mac. Features run started on unchanged build `6fd52462ae`:
  `.data/live_eval/features-1/20261026-225657/`. **Stopped at the owner's request**
  after two recorded failed Calculator cases; this is an incomplete run, not a suite
  score. Harder has not been rerun. Resume live testing only when the owner frees
  the Mac again. Local Ollama forced, web-answer fallback disabled, background
  priority applied. No app code changes or commits in this session.
- Baseline caveat: the available `broad-4/20261026-125053/runs.jsonl` has only
  **21 recorded turns / 8 passes**, not the previously summarized 12/33. Preserve
  that distinction when comparing; do not invent missing records. Harder-1 has
  30 recorded turns including two setup failures, 14 recorded passes; five of
  those passes say “I could not finish”.

| Commit | What |
|---|---|
| 0d88122 | Training tools: router label audit, JSON-only answers, `scripts/router_pipeline.sh` (train → fuse → GGUF → `ollama create a11y-router-test` → eval), grounding scorer + notes |
| 8ed1c8c, a6f3006 | `--suite features` (48 cases): owner-requested behaviours + special input + earlier failures with new wording. **Not run yet.** |
| 31651e4 | Task steps read exact facts (AXDocument/AXURL, Finder target/view/sort, focused element text — never secure fields); a navigation/change "done" needs evidence quoted from facts or OCR (`completion_evidence`); reading goals are exempt as before |
| a0bf6b3 | Router field `referent` (companion / destination / visible_target / other) → `semantic_plan`; highlight safety net kept (`highlight_only`: pointing never activates; closing/quitting never let through); Reminders with a time stay native; open_file also searches the front Finder folder and can read text files |
| 1bd6ba0 | Her cursor (`cursor_flight.py`): ~system pointer size (`CURSOR = 18 * system_cursor_scale()`), parks where it clicked between steps; next flight starts from it; she re-forms (`settle_cursor`) only when `say()` has a non-progress message |
| 9abfa4e | `--suite harder` (30 turns extending passed tasks) |
| 2eac7c0 | Title bar: built app's launcher stamped SDK 26 (`vtool` in `build_macos.sh`; PyInstaller's prebuilt launcher is SDK 12 → old taller title bar); black hairline added to the theme frame (QNSView is flipped; AppKit draws a light edge over content); icon/title on the buttons' centre line |
| 929feb8 | **v0.11.1 released** (macOS DMG only). v0.11.0 = cf8d22b. v0.10.0 still listed. |
| earlier | Menu bar menus instead of right-click on macOS; Terminal-style title bars (icon + "a11y", 💬 reply); traffic lights live (yellow minimises her, green toggles input; on the reply yellow closes, green disabled); clicking her opens chat; all Spaces; follow-up resolver (`resolve_follow_up`) and CHAT_SYSTEM never denying computer use; prototype leftovers removed (workflows, web version, company docs, OpenAI-compatible provider, learning preferences) |

Commits after 929feb8 are **local, not pushed** (as of writing).

## Historical measured results

**Live suites** (`.data/live_eval/<suite>-N/…/runs.jsonl`):
- broad: 8/29 → 12/33 (before the evidence work). Persistent failures: Calculator conversion,
  Clock world clock/timer, Calendar day/week view, Maps, Dictionary, Finder sort, TextEdit
  formatting, Notes search/delete, Safari find/zoom/private window, Stocks, Font Book, Activity
  Monitor sort.
- conversation: 15–16/17.
- harder-1: 14/28, and that was flattering — "I could not finish" was counted as a pass (fixed in
  a0bf6b3) and the run spanned two builds (the harness now aborts on a build change). Codex's
  diagnosis: routing misreads (fixed in a0bf6b3), Finder not using the front folder (fixed),
  "done" without evidence (31651e4). Not re-measured since: the rerun was stopped by the owner.

**Router** (text model, 406 held-out rows, family-disjoint split, `.data/training/router/`):
- Ollama qwen3:4b-instruct base: operation 94–97%, app_name 66–79%, target 83–86% (labels since
  audited: 13 gold fixes, 67 scoring equivalences, 175 real model errors; 246 referent labels
  corrected).
- MLX base (no JSON grammar): operation 64%, 146 unparseable.
- LoRA (rank 8, 8 layers, 300 iters, MLX): operation 83% with 56 unparseable (≈97% of parsed),
  app_name 82%. Adapter emitted an empty `<think></think>` prefix (scorer now strips it; training
  data now renders JSON-only assistant turns).
- **Next:** run `bash scripts/router_pipeline.sh` (owner must start it or allow it — Claude Code's
  auto-mode classifier blocked it because it clones/builds llama.cpp). Adopt only if
  `a11y-router-test` beats the base through Ollama on the same rows with no safety/operation
  regression. The app's model is not changed by the pipeline.

**Click grounding** (vision, old pilot adapter `.data/training/adapter`, 241 targets / 12 held-out
scenes): base 67% hit (50 malformed) vs adapter 78% (0 malformed) — but among parsed answers the
base is more accurate (85% vs 78%), and the pilot's prompt (0–1000 points, 20 tokens, no system
prompt) does not match the app (click_target numbers / fractional click_point with schema). Not
proven useful. A fair app-like eval and Ollama serving of a Qwen3-VL LoRA are described in
`scripts/grounding_APP_EVAL.md`. More data needed: ~100–200 independent scenes across 15–20 apps,
collected only when the owner's screen is free.

## Historical backlog (reassess against current scope)

1. Run `--suite features` and `--suite harder` (when the owner allows the screen); compare with
   harder-1 and broad-4.
2. Router pipeline (above), then decide adoption; shipping a fine-tuned model to users would need
   distribution (custom Ollama model) — ask the owner.
3. Remaining computer-task weaknesses: menus/shortcuts only used as a stuck fallback; typing not
   tied to a verified field; change goals sometimes labelled inspect.
4. Windows CI: a Qt slot raises `TypeError: <lambda>() missing 1 required positional argument: 'n'`
   during tests (source unknown; `scripts/ci_tests.py` now prints it). Windows releases skipped.
5. Local `packaging/build_macos.sh` with Homebrew Python 3.14 builds an app that crashes on launch
   (missing `language_tags` data under the voice deps); CI (Python 3.12) is fine.
6. Old releases (v0.10.0, v0.11.0) — owner hasn't said whether to delete.
7. Finder Movies/Music/Pictures open action.

## Second machine

The owner has an Arch laptop (RTX 4060, 32 GB). It can help with LoRA training (router, maybe the
click model) but cannot run live tests; data is the bottleneck, not compute.
