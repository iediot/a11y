# Handoff — 2026-11-09

Rules: [AGENTS.md](../../AGENTS.md). Packages and gates: [PLAN.md](PLAN.md). How results
are judged: [TESTING.md](TESTING.md). Numbers: [RESULTS.md](RESULTS.md) (start with
"Current standing"). Training: [TRAINING.md](TRAINING.md). Previous checkpoint:
[archive](HANDOFF_ARCHIVE_2026-11-05.md).

## How to work (owner instructions, 2026-11-09)

- **Commits:** plain messages on `main`. No `Co-Authored-By`, no "Generated with Claude
  Code", no AI attribution anywhere (commits, PRs, docs). Commit/push only when asked.
- **One new agent per task.** Each task (implement, review, investigate) gets a fresh
  agent with a short brief and an explicit file scope; do not reuse an agent across
  tasks. At most two running at once. Pass `model` explicitly: opus, or sonnet/haiku
  for mechanical work. Never Fable for subagents.
- **Codex for planning and second looks.** When something is uncertain (design choice,
  root cause, whether a fix is general), and for an independent review of any plan or
  diff, ask Codex: `codex exec "<brief>"` (read-only questions) or `codex exec review`
  for the current diff. Treat its answer as a second opinion, not a gate.
- **kiro-cli** (free credits) is also available for delegated jobs:
  `kiro-cli chat --no-interactive --model claude-opus-5.5 --trust-tools=fs_read,fs_write "<prompt>"`
  (no shell; put the full brief inline or in a file it can read).
- **GitHub Actions is unavailable** (owner is out of billing). CI does not run; run the
  full unit suite and the packaged self-test locally before any push.
- Keep only models in use on disk; delete trials afterwards. Windows is not a target for
  new features now (honest "not supported yet" replies). Model exploration is paused.

## Where things stand

All work is committed on local `main` (8 commits ahead of `origin/main`, head
`1335850`). The owner pushes with `git push origin main`.

| Package | Status |
|---|---|
| P0–P6, P8 | done (see RESULTS) |
| P7 live confirmation | blocked: Accessibility for the terminal (Otty) still reads off |
| Q1 direct system controls | done, reviewed: volume, mute, dark mode, Wi-Fi, brightness (DesktopBridge `--brightness`, `--set-brightness`, `--brightness-key`), Bluetooth read-only, Focus/text size open their panes |
| Q2 code-first task loop | done, reviewed: code enumerates options (accessibility targets, controls, menu commands, OCR lines), model picks an id; ~670 tokens/0.56 s per step vs ~3,700/3.0 s; image calls/step 0.93 → 0.29 |
| Q3 stronger last-resort model | paused by owner (colibri rejected: no Metal on macOS) |
| Packaging | fixed: `language_tags` bundled; build + self-test pass 6/6. Build with `PYTHON=.venv/bin/python packaging/build_macos.sh` (system python is externally managed) |
| Training | diagnosed, not started (TRAINING.md); Arch needs owner's Tailscale re-auth |

Results: request acceptance 65 → 82 of 127, 0 false successes, 0 unauthorized; sim dev
16 → 24–25 of 25, false successes 7 → 0; sim acceptance 12 → 14 of 25 (limited by the 4B
model's judgement). Unit suite: 1681 OK.

## Plan (next, in order)

1. **Live check (P7)** — owner has offered the screen. Needs Accessibility (and Screen
   Recording) for Otty; check with `AXIsProcessTrusted` first. Then:
   - Settings: volume, mute, dark mode, brightness, read Wi-Fi state; restore each value.
     Do not turn Wi-Fi off.
   - A few real requests: open an app, open a site, a reminder cancelled before confirm.
   - `scripts/human_eval.py --consent-from .data --runs 1 --output <scratch dir>`, then
     copy results into `.data/eval/<date>/` (the eval tripwire blocks direct writes).
   - Record in RESULTS and here.
2. **Sim acceptance** (largest open gap, 14/25): plan with Codex first. Dev failures to
   start from: pre-satisfied switch toggled off then "done"; confirmed delete not
   reported; two-facts reply missing values; Chrome used when Safari was named; no
   clarifying question for an ambiguous target (the loop has no ask mechanism). Read
   only aggregate acceptance numbers while fixing.
3. **Goal coverage:** a clause with no names ("and tick the box") is only caught if the
   verifier lists it; use plan parts / done_when for coverage.
4. **Request dev fails (12):** "same search in Chrome", Chrome window placement (helper
   is Safari-only), memory/follow-up facts, recent downloads, delete-folder opens
   Downloads, name question regression.
5. **Confirmation gaps** (documented): buttons exposed as AXGroup or OCR-only,
   DECLINE-labelled buttons.
6. Training only after TRAINING.md's gate shows a model limit.

Each item: Codex sanity check of the plan → one implementer agent → one fresh reviewer
agent (optionally `codex exec review` as well) → full suite → instrument rerun with
`--compare` → RESULTS row.

## Owner-only blockers

- Accessibility + Screen Recording for Otty (live eval).
- `git push origin main` (8 commits).
- Arch: `ssh edi@100.74.40.62` needs a one-time Tailscale browser check.

Disk: installed Ollama models are qwen3:4b-instruct, qwen3-vl:4b-instruct,
qwen3-vl:2b-instruct. Quantized router candidates and adapters remain under `.data/`.
