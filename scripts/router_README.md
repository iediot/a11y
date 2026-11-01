# Local router LoRA pilot

## Current status (2026-10-30)

Read [the current handoff](../docs/ai/HANDOFF.md) before starting a job.
R3 finished all 300 iterations and offline qualification; it failed the gate:
operation 373→380/395, action classification 358→349/395, 18 critical regressions.
Completion: 23/36 (0 FP/13 FN). Qwen remains default; no model was adopted.
Do not restart the completed batch or historical pilot commands automatically.

The subsequent Arch100-step CUDA pilot also completed parity, conversion/Q4
transfer and paired406-row qualification, and FAILED: operations373→369/395,
action flag358→354/395,30 critical regressions,0 parse errors. Separate alias
`a11y-router-arch-pilot-test` is diagnostic only. No training job is queued; Qwen
remains default. See [Arch handoff](../docs/ai/ARCH_TRAINING_HANDOFF.md) for pins
and artifacts, and the current HANDOFF for native scrolling work. Do not retrain
or repeat conversion because an older section calls it the next step.

R3 uses a fresh rank-8 LoRA on 8 layers, at most 300 iterations, learning rate 3e-5,
assistant-only loss, validation early stopping and a power guard. Its separate data
snapshot uses `router_canonical_data.py` to match the observed Ollama 0.34 required-
then-optional field order. Prompt/label values are preserved; held-out test bytes
are unchanged. This is an experiment, not a proven improvement. Original data and
R2 checkpoints are retained.

The batch trains → fuses validation-best → converts/quantizes → registers
`a11y-router-r3-test` → compares base/candidate through Ollama on the same 406 rows
→ runs 36 completion diagnostics. No download, GUI test or default-model adoption
is chained. All model loads are offline/cache-only, with background priority.

- State: `.data/live_eval/recursive-10/progress.json` and `power.json`.
- Training log: `.data/live_eval/recursive-10/r3/training.log`.
- Final summary: `.data/live_eval/recursive-10/summary.json` (only after completion).
- Clean stop: create `.data/live_eval/recursive-10/power.stop` **and**
  `.data/live_eval/recursive-10/STOP`; verify stopped state before resuming other work.

R2 is **not adopted**: operation 373/395 (same as base), app names 367/395 versus
303/395, but targets/action flags regress and critical errors remain. Completion
checks: 21/36 versus base 30/36. Low validation loss or a small scope-test pass is
not enough. Qualification only permits further validation; it never proves live
computer-task success.

## Earlier pilot commands

The recipes below explain the original pilot; they are not the active R3 command.
Do not run them merely because an older note calls them the next step.

Generate (offline, no models/apps):

```bash
.venv/bin/python scripts/router_data.py
(cd tests && QT_QPA_PLATFORM=offscreen ../.venv/bin/python -m unittest test_router_data -q)
```

The three JSONL files have only `messages`, with system/user/assistant roles.
`manifest.jsonl` records family, source, kind and validated linked intent plan;
`stats.json` gives counts. It is not a training file. No old grounding images or
private app contents are imported. Contacts, files and note contents are synthetic;
source regression wording is checked-in test text, not data read from this Mac.

Prompt construction calls `Planner.interpret_request` and
`Companion.resolve_follow_up` with a capture-only provider, not copied prompt text.
The follow-up resolver's actual response schema is `{request: string}`; those
assistant labels cannot themselves validate as intent plans. The manifest holds a
separate validated intent plan for those examples. A follow-up negative has an
empty request. Intent labels are the first raw router completion, before the
app's opening-coverage check and later service transformations. Source labels are
manually reviewed from routing/report regressions and the primary live suite;
mocked model outputs are not automatically treated as truth.

Families group operation plus semantic target/app (not row hashes). Repeated
paraphrases of a task stay together, including source regressions that match an
existing family. Five synthetic task variants per operation receive eight
surface forms; short histories, resolver edits and negative conversation examples
are included. Held-out families test different targets/apps for the same operation;
this is not a claim that every app is globally absent from training across all
operations. Low-cardinality operations may have no validation rows. Every operation
has train and test coverage. Harder-suite literals and dynamic request templates
are excluded from train/valid by static AST inspection, without importing or
executing live suite code. This small synthetic pilot is a starting point, not an
exhaustive extraction of all routing expectations or all live suites. Do not use
its score alone as evidence of real-Mac task success.

Train (~two passes over training rows, batch 1, 16 layers, rank 16, lr 5e-5,
checkpointing, 4096-token cap, assistant-only loss; inspect validation loss and
choose a checkpoint before comparing on test):

```bash
bash scripts/router_train.sh
```

Compare the exact same test rows; eval never executes desktop actions:

```bash
.venv/bin/python scripts/router_eval.py --model qwen3:4b-instruct --output .data/training/router/base-ollama.json
.venv-train/bin/python scripts/router_eval.py --mlx --output .data/training/router/base-mlx.json
.venv-train/bin/python scripts/router_eval.py --adapter .data/training/router/adapter --output .data/training/router/lora-mlx.json
```

Ollama uses the real provider/Planner, its schema, greedy temperature 0 and seed 7,
including opening-coverage calls. MLX also goes through Planner with greedy
sampling and seed 7, but does not impose Ollama's JSON grammar: parse failures count
as errors. Thus base-MLX versus adapter-MLX isolates the adapter more cleanly than
cross-runtime comparisons. Latency excludes model initialization but includes
opening-coverage calls; first Ollama inference may include cold loading. Exact
field comparisons are lexical, especially `reply` and free-text targets; the
confusion matrix and failure list should be reviewed alongside aggregate accuracy.
Resolver exact accuracy is reported separately from intent-operation accuracy.

## Fuse and serve later — not run

The cached MLX base uses MLX 4-bit tensors. Do not feed those directly to Ollama's
safetensors importer. First fuse and dequantize into a separate directory:

```bash
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 .venv-train/bin/python -m mlx_lm.fuse \
  --model mlx-community/Qwen3-4B-Instruct-2507-4bit \
  --adapter-path .data/training/router/adapter \
  --save-path .data/training/router/fused --dequantize
```

With a local llama.cpp checkout whose converter supports Qwen3 (replace the path):

```bash
.venv-train/bin/python /path/to/llama.cpp/convert_hf_to_gguf.py \
  .data/training/router/fused --outfile .data/training/router/router-f16.gguf --outtype f16
/path/to/llama.cpp/build/bin/llama-quantize \
  .data/training/router/router-f16.gguf .data/training/router/router-q4_k_m.gguf Q4_K_M
printf 'FROM ./router-q4_k_m.gguf\nPARAMETER temperature 0\nPARAMETER seed 7\nPARAMETER num_ctx 16384\n' > .data/training/router/Modelfile
ollama create a11y-router -f .data/training/router/Modelfile
.venv/bin/python scripts/router_eval.py --model a11y-router --output .data/training/router/lora-ollama.json
```

GGUF conversion/quantization is unverified here and requires a compatible local
converter/build. Budget disk and RAM for dequantized intermediate weights. If your
installed Ollama supports this architecture's standard dequantized safetensors,
you can instead put `FROM ./fused` in the Modelfile and skip GGUF conversion.
Preserve/check the Qwen chat template and EOS metadata during conversion and inspect
`ollama show a11y-router --modelfile` before comparing. Do not change A11y's default
model until offline evaluation and the held-out real-Mac suite improve.

## Previous click-grounding pilot

Read `567349c:scripts/grounding_eval.py` and its generator; the scripts were later
removed in `0296dc7`, with data retained. Current artifacts contain 963 training
rows (45 PNGs, 44 scenes) and 241 validation rows (12 PNGs, 12 scenes); every image
path exists. These are old local screen artifacts and are deliberately not copied
into router data. Adapter files include steps 100 through 900 plus the final
`adapters.safetensors`; config specifies rank 8, scale 2, all 36 language layers.
The existence of checkpoints establishes training occurred, not that it helped.

No grounding score/result file was found in the retained training directory or
references checked in repo docs/history. The old scorer prints only stdout and
never persists a score, so an unrecorded terminal run cannot be ruled out.

To evaluate later, restore the historical scorer as a separate file, using the
already installed `mlx_vlm` in `.venv-train`, the cached
`mlx-community/Qwen3-VL-4B-Instruct-4bit`, and the existing validation data:

```bash
git show 567349c:scripts/grounding_eval.py > scripts/grounding_eval_pilot.py
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 .venv-train/bin/python scripts/grounding_eval_pilot.py --data .data/training/valid.jsonl --limit 241 > .data/training/grounding-base.txt
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 .venv-train/bin/python scripts/grounding_eval_pilot.py --data .data/training/valid.jsonl --limit 241 --adapter .data/training/adapter > .data/training/grounding-adapter.txt
```

The historical metric is a generated point inside the stored normalized 0–1000
box, with ±5 tolerance and per-scene totals. It parses `(x,y)` from the reply.
Default limit is only 200, so explicitly use all 241 for both runs. Preserve those
same rows, screenshot coordinate convention, prompt and model for a fair paired
comparison; record both scores/latency and malformed point counts. The scorer does
not measure live click success, execution safety or routing, and needs no app
opening. This evaluation has not been run.

## Generated counts

| Operation | Train | Valid | Test |
|---|---:|---:|---:|
| add_event | 24 | 8 | 8 |
| add_reminder | 24 | 8 | 9 |
| answer | 24 | 8 | 8 |
| browser_open | 24 | 8 | 9 |
| browser_search | 24 | 8 | 8 |
| capture_once | 24 | 8 | 8 |
| click_point | 24 | 8 | 9 |
| close_tab | 24 | 8 | 8 |
| close_window | 24 | 8 | 8 |
| complete_reminder | 24 | 8 | 8 |
| computer_task | 24 | 8 | 14 |
| create_note | 24 | 8 | 8 |
| delete_reminder | 24 | 8 | 8 |
| find_contact | 24 | 8 | 8 |
| focus_app | 24 | 8 | 8 |
| follow_up | 33 | 11 | 11 |
| highlight | 24 | 8 | 9 |
| inspect_media | 24 | 8 | 8 |
| inspect_screen | 16 | 8 | 17 |
| launch_app | 16 | 8 | 18 |
| list_shortcuts | 24 | 8 | 8 |
| look_up | 24 | 8 | 8 |
| move_geometry | 24 | 7 | 8 |
| move_object | 24 | 8 | 9 |
| new_window | 24 | 8 | 8 |
| open_file | 24 | 8 | 8 |
| play_media | 24 | 8 | 8 |
| press_control | 24 | 8 | 8 |
| press_key | 24 | 8 | 8 |
| quit_app | 24 | 8 | 8 |
| read_calendar | 16 | 8 | 18 |
| read_mail | 16 | 8 | 17 |
| read_notes | 24 | 8 | 8 |
| read_page | 24 | 8 | 8 |
| read_reminders | 24 | 0 | 17 |
| recent_files | 16 | 8 | 16 |
| run_shortcut | 24 | 8 | 8 |
| scroll | 24 | 0 | 16 |
| system_info | 24 | 8 | 8 |
| type_text | 24 | 8 | 9 |
| weather | 24 | 8 | 8 |
| **Total** | 953 | 314 | 406 |

## Background-only measured rerun (2026-10-26)

`router_eval.py` now saves every gold/predicted plan, request and latency for mismatch
review. Scoring is still exact; do not silently treat arbitrary target paraphrases or
conversational replies as correct. Existing aggregate-only JSON cannot be rescored.
The MLX base's historical 146 failures include invalid JSON and opening-coverage
responses; its unconstrained result is not interchangeable with Ollama's grammar score.

Run model jobs with `nice -n 19`. The entry points now verify the effective niceness
before model access: on some managed sessions `nice` warns but still starts the child
at normal priority. These scripts fail closed in that situation.

`bash scripts/router_train.sh` now prepares a separate `adapter-local` (old artifacts
untouched): rank 8, 8 layers, batch 1, assistant-only loss, max 300 iterations,
full-validation loss every 50, patience 2, 6900-second wall limit including load.
Validation callback saves the best weights immediately and exposes a loadable
`adapter-local/best` directory. This runner has offline unit checks but has not been
executed with Metal in the current managed session. Full validation costs time;
the wall limit may stop before 300 iterations. Inspect `validation.json` and `run.json`.
Do not use the test set to choose the router checkpoint.

Restored grounding scorer (stored PNGs only, no capture/app calls):

```bash
nice -n 19 .venv-train/bin/python scripts/grounding_eval.py --limit 241 --output .data/training/grounding-base.json
nice -n 19 .venv-train/bin/python scripts/grounding_eval.py --limit 241 --adapter .data/training/adapter --output .data/training/grounding-adapter.json
```

It saves raw answers, parsed points, per-scene counts, latency, data hash, malformed
counts, historical ±5 hit rate and strict box hit rate after each row. Rebase stored
image paths by filename into `.data/training`. The comparison summary at
`grounding-eval.json` currently records a blocker, not a measured result.

## Reviewed data and end-to-end owner run (2026-10-26)

The latest audit supersedes the earlier blocker-only assessment. See
[router_AUDIT.md](router_AUDIT.md) for per-field audit counts, labeling policy,
JSON rendering and limitations; see [grounding_APP_EVAL.md](grounding_APP_EVAL.md)
for the measured old vision results, app-format mismatch and researched serving steps.

Run the complete new router experiment from an owner terminal with Metal access:

```bash
bash scripts/router_pipeline.sh
```

This snapshots regenerated data into a fresh run, selects the rank-8/8-layer
checkpoint on validation, evaluates MLX, fuses/dequantizes, converts/quantizes to
Q4_K_M, copies the installed base TEMPLATE/PARAMETERS, creates a11y-router-test and
evaluates Ollama. Each stage logs and fails closed on errors/background-priority
failure. Prior manual Modelfile examples above do not copy the full base template;
prefer this pipeline. No pipeline model job has been run in the managed sandbox.
