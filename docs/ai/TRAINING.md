# Training: what was tried, why it misled, and the next attempt

Audit of 2026-11-06 over `scripts/router_*`, the run kits under `.data/training/arch/`
and the qualification reports under `.data/live_eval/`. Older detail:
[Arch history](ARCH_TRAINING_HANDOFF.md), [local tuning notes](ON_DEVICE_TUNING.md).
The owner has authorized the Arch RTX 4060 machine for training.

## Verdict on the earlier router fine-tunes

They do not show that training cannot help. Two were not tested fairly, the data was
thin, and the gate could not be passed by any model that differs from the base.

| Attempt | Data | Trained key order | Seen | Result on the 406-row set |
|---|---|---|---|---|
| `a11y-router-test` (MLX) | 953 rows | operation first | 0.31 epoch | operation 373→382, acts_on_computer 358→322 |
| R2 | same, "false" rows repeated to 1,553 | operation first | 300 steps | operation 373→373, acts 358→324 |
| R3 (MLX) | same 1,553 | alphabetical | 0.19 epoch | operation 373→380, acts 358→349, 18 critical rows |
| Arch pilot | R3 data | alphabetical | 0.52 epoch | operation 373→369, 30 critical rows |
| R4 (Arch) | 450 rows (187 new, 230 replay, 33 resolver) | alphabetical | 114 steps | operation 373→369, 27 critical rows |

Causes, in order of evidence:

1. **Serving mismatch (verified by inference).** The base alias and R3 render plain
   ChatML and emit schema keys alphabetically. The Arch pilot and R4 aliases render an
   extra blank line after the system prompt, lack the `thinking` capability and emit
   keys in declared order, an order they were never trained on. Served that way, the
   untouched base reproduces 4 of 8 sampled R4 "regressions". The first pilot and R2
   had the reverse mismatch, and the field that collapsed was the one that became the
   first token.
2. **Thin, partly contradictory data.** About 115 distinct requests, multiplied by
   eight politeness wrappers (some ungrammatical). Labels come from a per-operation
   table; `acts_on_computer` contradicts the prompt for question-like operations.
   Training payloads lacked `now`, `front_app`, profile and preferences that production
   always sends.
3. **Under-training with an uninformative loss.** Runs saw 0.2–0.5 epoch. Loss is
   dominated by JSON format tokens that the grammar forces anyway; no decision-accuracy
   curve was kept. In the CUDA trainer, early stopping could not fire and the cosine
   schedule reached zero while loss was still falling.
4. **Unattainable, noisy gate.** `router_compare.py` demands zero per-row critical
   regressions and a strictly better `app_name`. The 406 rows are 57 request families;
   373 vs 369 is 7 worse and 3 better (McNemar p = 0.34). The "frozen" set is scored
   with whatever `INTENT_SYSTEM` is current. No converted-but-unadapted base was ever
   scored as a control.

R3, the only like-for-like comparison, improved 430 scored cells and regressed 53.

## Known bugs in the pipeline (unfixed)

- `router_modelfile.py`, `router_arch_qualify.sh`, `router_pipeline.sh`: copy template
  and parameters but never assert serving parity with the base.
- `router_canonical_data.py:17-22`: one key order hard-coded as "Ollama's".
- `router_eval.py:66`: ignores each row's stored system prompt; scores after the
  coverage call while gold is the first completion.
- `router_compare.py:36-42`: hard-coded success field and per-row gate, no clustering.
- `router_data.py:152,261`: merges differently labelled requests into one family;
  `:166-173` generates the ungrammatical wrappers.
- `router_rebalance.py`: balances by repetition.
- `router_train_cuda.py:166-172`: early stopping unreachable at 100 steps.
- `router_lora.yaml`: MLX `scale: 16`, eight times the PEFT run's effective scale.
- `router_peft_verify.py`: pilot step/parameter counts hard-coded.
- `router_arch_qualify.sh:10`: scores at 8K context; production uses 16K.
- `router_r4_data.py` replays old labels under the current prompt; do not build or
  train r4 data until the router prompt and output format are frozen.

## What training should be for

Routing accuracy with the full prompt is not the best-posed goal. The measured cost of
a routed request is output tokens: about 70–90 tokens at 14 ms each behind a cached
2,650-token prompt. So:

1. **First, no training:** a compact router output on the base model (PLAN P4). If
   accuracy holds, the speed goal is met without a fine-tune.
2. **Train only if step 1 loses accuracy, or to shorten the prompt for the cold path.**
   Distil the base (full prompt, full output) into a student with a short prompt and
   compact output.

## Design for one sound attempt

- **Controls before any training.** Convert the unadapted base through the same path and
  score it against `qwen3:4b-instruct`: that difference is the noise floor. Before
  scoring any candidate, fail closed unless it matches the base on prompt token count,
  emitted key order and capabilities. Try serving as `FROM qwen3:4b-instruct` plus
  `ADAPTER` so base weights stay identical (unverified for this Ollama version).
- **Data.** About 6,000 train, 600 validation, 800 dev rows, split by seed: 100–250
  distinct requests per operation (400 for `answer` and `computer_task`), no wrapper
  duplication, production-shaped payloads, 10% with history. Synthetic requests only;
  never private data, never the hidden set.
- **Labels.** The teacher's first completion, kept only when its operation matches the
  generator's intent and deterministic validators pass (slot text present, time computed
  in code, URL parses). Disagreements are dropped, not relabelled. Hand-check 200 rows;
  require at least 97% precision.
- **Format.** Short key-legend system prompt, unchanged user payload, compact JSON plus
  end token as target. Unconstrained output must equal constrained output on at least
  99% of validation rows.
- **Objective and selection.** Answer-only cross-entropy; pick the checkpoint by greedy
  decision accuracy on validation, not by loss.
- **RTX 4060 8 GB.** NF4 QLoRA, rank 16, alpha 32, all layers and projections, max
  length 768, batch 8 × accumulation 2, LR 1e-4 cosine with 5% warmup, 3 epochs,
  evaluation every 100 steps, patience 3. Estimated 1.5–2 hours.
- **Mac 24 GB alternative.** Same shape in mlx-lm on a bf16 base (about 8 GB download,
  needs the owner's approval), `scale: 2.0`, fuse without the 4-bit base, then f16 and
  Q4_K_M.
- **Gate, fixed in advance.** Median warm latency ≤ 0.5 s and ≤ 30 output tokens;
  held-out operations ≥ base − 3 and all-fields ≥ base − 4; family-level critical
  regressions within the control's noise floor; no operation more than 5 points below
  base on dev; zero parse failures; then request eval and sim eval acceptance splits
  must not fall.

Risks: routing rules move from an editable prompt into weights, so each rule change
means retraining; the student inherits the teacher's mistakes; a short prompt drops the
in-context guards against window-title injection, which needs its own adversarial rows.

## Access

Arch is `edi@100.74.40.62` over Tailscale. On 2026-11-06 Tailscale SSH demanded a
browser re-authentication that only the owner can complete; the LAN address had no
route. No training job has been started in this cycle.
