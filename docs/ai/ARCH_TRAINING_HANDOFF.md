# Arch CUDA router training handoff

Checkpoint: 2026-10-30. Read root AGENTS.md and HANDOFF.md first. This file
records the owner's manual Arch setup and completed pilot, not a running job.

## Final pilot verdict — 2026-10-29

Conversion, quantized transfer and paired406-row Ollama qualification FINISHED.
Pilot **failed**: operation373→369/395, acts_on_computer358→354/395,30 critical
per-row regressions; no parse errors. Separate test alias retained for diagnosis,
Qwen remains default. Do not retrain automatically or treat this as qualified for
live tests. Tiny real-PEFT parity passed8CPU/12CUDA cases on Arch; that validates
the selected-logit memory optimization under the tested conditions, not training
adequacy/objective or end-to-end task quality. Reports:
`.data/live_eval/arch-pilot-1-qualification/{comparison,diagnosis}.json`.
Arch service was transient (--collect), so no service/process remains after exit.
Earlier running-state notes below are historical snapshots.

## Historical qualification launch snapshot — 2026-10-29

Noninteractive access now works over Tailscale: `edi@100.74.40.62`.
Qualification runs independently as user systemd service
`a11y-pilot-qualify.service` (not tmux; detached tmux attempts did not survive
SSH disconnect). Inspect with `systemctl --user status a11y-pilot-qualify.service`.
Log: `~/a11y-training/logs/pilot-qualification.log`; stage status/logs:
`runs/cuda-pilot-1-qualification/`. Verification and parity stages passed; tools
build was current at startup audit. No training, GUI tests or default changes.
Keep Arch on AC/awake/lid open: sleep inhibitor permission was denied and no
system sleep settings were changed.

Fresh conversion overlay reuses training torch through an appended `.pth` path,
not system-site-packages (which alone does not expose another venv). Overlay
transformers4.57.6/numpy2.2.6 leave training4.57.1/peft0.17.1 intact. Environment
inventories use importlib.metadata to avoid depending on pip in uv environments.

Mac follow-up `.data/training/arch/qualification-pilot-1/followup_mac.py` waits
for conversion success, transfers only Q4 plus small reports, then registers a
separate alias and runs fixed-source paired406-row Ollama qualification. Status
`followup-progress.json`; log `followup-driver.log` in the same directory. A
failed conversion prevents transfer/scoring. Let these fixed shell batches run
independently; no duplicate jobs or active agent polling loops.

## Historical qualification preparation — 2026-10-29

Returned adapter independently verified: 112 finite FP32 tensors, 3,670,016
parameters, all 56 LoRA B matrices nonzero; expected rank8/alpha16 and layers28–35.
Adapter SHA256 `ca0883549601bd0d198718a7c9ea1869bddfb572a50245d59adaa2c0ba134877`.
All three dataset hashes match again. Report: `cuda-pilot-1/verification-mac.json`.
Original HF base/tokenizer checks still require Arch; no HF base copied to Mac.

Numerical parity now PASSES locally using the existing conversion Python (torch
2.11.0, transformers4.57.6), without downloading any model. Eight tiny random Qwen
CPU cases (FP64/FP32, unequal padding/prompts, first answer/EOS, microbatch-mean
accumulation) compare full causal loss, native model loss and all 25 gradient
tensors per case. Max loss difference 4.7684e-7, max gradient difference 2.7756e-17;
FP32/FP64 tolerance atol2e-6/rtol2e-5. This is not an NF4/full-size memory or training
quality result. Arch's transformers4.57.1 + PEFT + CUDA checks remain pending.
Actual trainer loss was extracted without changing the math into
`selected_answer_loss`, so the numerical test exercises the implementation used
by Trainer, rather than a copied approximation.

Prepared kit: `.data/training/arch/qualification-pilot-1/README.md`. It contains
scripts and a 38.5 MB source-only archive of clean pinned llama.cpp commit
`961c376a2daa42220ec09663b9bde32c5fba1f70`, no model weights. Existing tools support
Qwen3 PEFT direct LoRA GGUF conversion and alpha/rank-aware llama-export-lora.
Proposed chain on Arch: artifact/base-tokenizer checks -> CPU/CUDA PEFT parity ->
build CPU exporter/quantizer -> unquantized HF base F16 GGUF + F32 LoRA GGUF ->
merged F16 -> Q4_K_M. Separate overlay conversion env preserves training pins.
No dependency install, build or conversion was executed remotely. Local shell
syntax checks and CUDA data/numerical tests pass. Do not claim the prepared Linux
workflow has been run end-to-end. It fails closed and retains intermediates.

`router_arch_qualify.sh` is prepared for the Mac: verify transferred model hash,
Arch parity/base reports and all frozen dataset hashes/406-row count; refuse an
existing alias, copy base TEMPLATE/PARAMETERS, register separate
`a11y-router-arch-pilot-test`, score base/candidate serially at 8K context with
fixed source, run existing regression gate. No adoption or GUI test.

**Access blocker:** two BatchMode SSH probes to edi@192.168.1.143 timed out before
authentication. No remote command ran; no password requested/stored. Owner was
asked whether Arch is awake/address changed. Do not repeat connection polling.
Proceed with transfer/execution once connectivity/access is established; otherwise
owner can use the kit's interactive SSH/tmux instructions. No retraining launched.

## Current state and immediate next step — 2026-10-30

Pilot verification, selected-logit loss/gradient parity, conversion/merge/Q4
transfer, separate Ollama registration and paired406-row qualification are ALL
FINISHED. The pilot failed the regression gate; no further training or candidate
live tests are authorized automatically. Qwen remains default. Returned PEFT
artifacts remain `.data/training/arch/cuda-pilot-1/`; qualification reports remain
`.data/live_eval/arch-pilot-1-qualification/`. Do not restart completed jobs.
Current development is native scrolling and general task execution; see the
current HANDOFF checkpoint. Any older preparation/access-blocker statements in
this document describe historical states, not work still pending.

## Machines and access

- Mac project: `/Users/iediot/Projects/a11y`.
- Arch: `edi@192.168.1.143` (LAN address can change), RTX 4060 Laptop GPU,
  8,188 MiB advertised VRAM; torch reports 8,272,019,456 bytes. About 30 GiB RAM,
  15 GiB swap, 521 GiB free disk at setup. GPU power cap shown was 55 W.
- Arch work directory: `/home/edi/a11y-training`.
- Noninteractive SSH was subsequently established over Tailscale at
  `edi@100.74.40.62`; the LAN address above is historical. Never request/store a
  password. Current remote job state was not rechecked during the Oct30 docs update;
  the last documented qualification/capacity jobs finished. Prefer bounded user
  systemd jobs; transient --collect services disappear after exit.
- Mac free space at receipt check: approximately 26 GiB. Recheck before writing
  artifacts. Do not delete models/checkpoints or other owner files to make room.
- Keep Arch on AC and awake during jobs. tmux survives SSH disconnection, not
  reboot, suspend, power loss or disk failure. No sleep inhibitor was configured
  in this setup. Mac does not need to stay awake for an Arch-only job.

## What was done

1. Installed/enabled OpenSSH and used SSH from Mac. Initial noninteractive host-key
   verification failure was resolved by the owner's interactive connection.
2. `nvidia-smi` initially failed with NVML driver/library mismatch. Owner rebooted
   Arch; driver and kernel module then matched at 615.78.08. Matrix multiplication
   on CUDA passed. The driver advertised CUDA 13.4; installed PyTorch uses its
   own CUDA 12.8 runtime.
3. Created an isolated Python 3.11 environment using uv. Arch system Python was
   3.14.8; do not install the training packages into system Python.
4. Installed CUDA PyTorch and training libraries. CUDA NF4 quantization and
   dequantization passed on GPU.
5. Copied existing trainer/helpers and frozen recursive-10/r3 data from Mac.
   The held-out test split is retained for later scoring, never training or
   checkpoint selection. Training reads train/valid only (test is hashed).
6. Owner downloaded official `Qwen/Qwen3-4B-Instruct-2507` weights, about 8 GB,
   into `models/Qwen3-4B-Instruct-2507`. An initial CLI invocation with multiple
   include patterns warned that include was ignored; a full-repository download
   fetched the missing config/tokenizer files. Weights were reused.
7. Fixed dependency mismatches and training memory use, detailed below.
8. Two-step smoke run `runs/cuda-smoke-3` passed in 5.4078 s, training loss
   1.687608. This proved those two examples fit, not general quality.
9. Owner launched 100-step pilot inside tmux session `a11y-train`; it finished
   successfully in 3201.5461 s (53m21s), about 0.52 training epochs.
10. Owner copied selected-adapter, run.json, run_config.json, hardware.json back
    to Mac. Agent checked all three data hashes against the Mac source: identical.

No private apps or live GUI tests were used in this process. Model downloads
are file retrieval; inference/training stays local. No cloud inference/uploads.

## Working environment

```bash
# Arch, after connecting with ssh edi@192.168.1.143
cd ~/a11y-training
source .venv/bin/activate
```

Observed torch: `2.11.0+cu128`; bitsandbytes `0.50.2`; Python `3.11.18`.
The working compatible HF package set was:

```text
transformers==4.57.1
peft==0.17.1
datasets==4.3.0
huggingface-hub==0.36.0
accelerate==1.11.0
```

`uv pip check` passed after these pins. Other dependencies include numpy,
safetensors and their resolved dependencies. Save a fresh complete environment
lock on Arch: earlier environment-lock.txt may predate the final pin changes.
Do not reinstall or upgrade a working environment automatically.

```bash
uv pip freeze > environment-lock.txt
```

### Failures and fixes

- Transformers 5.21.0 removed TrainingArguments `warmup_ratio`. First smoke
  failed before training. Pinning only transformers caused another mismatch.
- Datasets 5.1.0 expected Hub `BucketNotFoundError`, absent after the Hub
  downgrade. Pinning the related packages together resolved trainer imports.
- Full-prompt vocabulary logits/loss exceeded 8 GB VRAM, trying another 1.46 GiB.
  Reducing max length would truncate important router prompts, so it was not used.
- `scripts/router_train_cuda.py` now selects logits at positions predicting
  supervised next tokens using Qwen's tensor `logits_to_keep`, then computes
  cross-entropy only for the answer/EOS. It retains full prompt attention, causal
  shift, first answer token, padding masking, and all answer/EOS tokens.
  `model_accepts_loss_kwargs=False` lets Trainer handle microbatch-mean gradient
  accumulation. Eight CPU/data tests passed in tests/test_router_cuda.py.
- Numerical loss/gradient parity with full-logit reference has NOT been tested;
  the Mac project venv lacks torch. Verify on Arch with a tiny randomly initialized
  Qwen config, dropout disabled, comparing loss and gradients, including unequal
  prompt lengths/padding. Do this before relying on further training experiments.
- The updated trainer was manually scp'd to Arch before successful smoke/pilot.
  Other existing uncommitted changes must be preserved.
- torch_dtype deprecation and PAD/BOS alignment messages were nonfatal. Inspect
  base/adapter tokenizer IDs and actual Ollama template during conversion; do not
  dismiss unexpected tokenizer changes without checking.

## Paths

Arch, all under `~/a11y-training/`:

```text
.venv/
models/Qwen3-4B-Instruct-2507/
scripts/router_train_cuda.py
scripts/router_chat.py
scripts/training_priority.py
data/router-r3/{train,valid,test}.jsonl
runs/cuda-smoke-{1,2,3}/
runs/cuda-pilot-1/selected-adapter/
runs/cuda-pilot-1/checkpoint-50/
runs/cuda-pilot-1/checkpoint-100/
runs/cuda-pilot-1/{run.json,run_config.json,hardware.json,progress.json}
logs/cuda-pilot-1.log
```

Check checkpoint directories on Arch before assuming full resume state exists.
Mac returned artifact directory:
`.data/training/arch/cuda-pilot-1/selected-adapter/` plus the three JSON metadata
files. Adapter safetensors size: 14,694,776 bytes. No original HF base weights
were transferred back. `.data` is local-only, unavailable in a fresh clone.

## Pilot configuration and results

- Base: official unquantized HF Qwen3-4B-Instruct-2507, loaded on GPU with NF4
  double quantization, BF16 compute; gradient checkpointing and SDPA.
- LoRA rank8, alpha16, dropout0.05, last8 layers (28–35).
- Modules: q/k/v/o projections and gate/up/down projections.
- Trainable parameters: 3,670,016 of 4,026,138,112 (0.0912%).
- Batch1, accumulation8, max sequence4096 (reject oversized rows, never truncate).
- Max100 optimizer steps, LR2e-5, cosine schedule, warmup ratio0.05, seed7.
- Validation and full checkpoint save every50 steps; early-stop patience2;
  select best validation loss. No test-set checkpoint selection.
- Final train loss0.72366199, validation loss0.25239700, validation516.5026s.
- Best checkpoint `runs/cuda-pilot-1/checkpoint-100`; interrupted=false.
- Training loss fell about1.67→0.59 by step50. This is optimization progress,
  not evidence of better routing or safer computer actions.

Frozen data SHA256:

```text
train 6b539692b128c2b485b619661c92ce57841d0579aebfb83ff1bda46f8936feec
valid bcefeff27a11d8774bd3c37590c275e2314013e3a3862bd7926cda17b0859e04
test  37ca96762215a9561f3e2c42d2bf82ab2b2799c244ceb5f3198ea6f178c47647
```

## Operating tmux and future runs

```bash
tmux attach -t a11y-train
# Detach: Ctrl+B, release, then D. Never Ctrl+C just to disconnect.
```

A completed tmux session may still contain a shell; session existence does not
mean training is running. Inspect the run metadata/process/log once as needed.
Do not launch duplicate GPU jobs. To inspect without attaching:

```bash
tail -n 60 ~/a11y-training/logs/cuda-pilot-1.log
cat ~/a11y-training/runs/cuda-pilot-1/run.json
```

For historical reproduction ONLY (pilot already finished; do not rerun into
this output directory):

```bash
cd ~/a11y-training
source .venv/bin/activate
mkdir -p logs
set -o pipefail
nice -n 19 python scripts/router_train_cuda.py \
  --model models/Qwen3-4B-Instruct-2507 \
  --data data/router-r3 --output runs/cuda-pilot-1 \
  --rank 8 --layers 8 --max-steps 100 --accumulate 8 \
  2>&1 | tee logs/cuda-pilot-1.log
```

A STOP file in the active output directory requests save/stop at the next
optimizer boundary. SIGINT/SIGTERM are also handled. Avoid kill -9. A future
resume requires full optimizer/scheduler/RNG state and unchanged run config/data;
selected-adapter alone is not a resumable training checkpoint. The current
strict resume guard rejects changing max_steps: do not promise extending this
100-step schedule by simply adding --resume. Inspect/plan a new experiment.
There is no Arch battery guard configured in this workflow.

## Next work, in order

1. Read current rules/handoff and preserve working changes. Verify returned
   safetensors contents/config, dataset hashes and local process state. Record
   model repository revision and dependency lock on Arch if available; exact
   downloaded model revision was not captured in the returned metadata.
2. Run loss/gradient parity check for selected-logit training on Arch. Record
   actual peak memory/time on future runs rather than extrapolating two steps.
3. Investigate available pinned llama.cpp conversion tools. The Mac has
   `.data/training/tools/llama.cpp/convert_lora_to_gguf.py` and
   `convert_hf_to_gguf.py`; inspect the checked-out version and flags. A PEFT
   adapter cannot be passed into the old MLX adapter-fusion path unchanged.
4. Prefer conversion/merge on Arch, where base HF weights already exist and
   disk space is ample. Choose a supported direct LoRA conversion/export or
   CPU unquantized base+PEFT merge then F16 GGUF→Q4_K_M. Do not merge into NF4
   weights as an unnoticed quality-changing shortcut. Check RAM/disk estimates,
   tensor names, layer indices, alpha/scaling, tokenizer and vocabulary identity.
   Do not download additional large models automatically.
5. Reuse pinned tools/dependencies where possible; build/download tooling only
   within the owner's scope and document exact revisions. Capture logs, hashes,
   configuration and stage status. Transfer final quantized artifact to Mac.
6. Register a NEW separate Ollama alias, e.g. a11y-router-arch-pilot-test. Preserve
   installed base template/parameters via existing router_modelfile tooling.
   Do not overwrite old R2/R3 aliases or change A11y defaults.
7. Reuse router_eval.py/router_compare.py and router_README.md. Score freshly
   paired base/candidate against exactly the same 406 rows with identical runtime,
   context and current app code held fixed. Check parse errors, every scored
   field and critical per-row regressions, not aggregate accuracy alone.
8. Run paired completion diagnostics only using the actual intended model path;
   router improvement is not proof of better task completion. Prior MLX R2/R3
   candidates failed despite low loss: R3 operations373→380/395 but action flag
   358→349, 18 critical regressions. Do not lower the gate to justify training.
9. If it fails, diagnose mechanisms and propose a controlled data/objective
   experiment; never tune on held-out labels or add per-task production phrases.
   If it passes, recommend next validation; no automatic adoption/live GUI tests.
10. Update HANDOFF with measured results. Run relevant tests for code changes,
    whole unit suite before pushing. Commit/push only on explicit request.

Owner preference: let authorized long shell batches run independently and end
the agent turn. No repeated polling, sleeps or status chatter. Headroom is the
owner's agent proxy; it is not part of A11y and must not receive A11y's local
model requests or private data.
