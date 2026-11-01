# Local GUI model experiments

Qwen3-VL remains the default. The app never downloads GUI weights automatically.
The owner subsequently authorized a Holo2 test installation (details below). The adapters are experiments, not evidence that a new
model is better. Keep the text router, native tools, target validation, screenshot
consent, completion verifier and sensitive-action confirmations.

## What was verified (2026-10-27)

Public model cards, checkpoint configurations and image processor configurations
were read without loading weights. The host is 24 GiB Apple Silicon, Ollama 0.34.0.
At initial inspection none of the requested checkpoints was installed; the owner
subsequently authorized Holo2 (installation/results below). Availability/revisions are
recorded in `.data/live_eval/gui-models-research/availability.json`.

| Candidate | Verified architecture / format | Integration and runtime status |
|---|---|---|
| Holo2-4B | `qwen3_vl`; cookbook localization JSON `{x,y}`, 0–1000 | Experimental Ollama **grounder**, with the existing policy retained. Native numbered-target decisions bypass it. Owner-authorized Q4_K_M + F16 projector now runs in Ollama 0.34.0; quality results below. |
| UI-TARS-1.5-7B | `qwen2_5_vl`; Thought / Action calls, processed-image pixel coordinates | Experimental Ollama policy adapter. Image dimensions are rounded to a 28-pixel grid, matching its processor. Checkpoint import and actual model output are untested. |
| OpenCUA-7B | **custom `opencua`**, `OpenCUAForConditionalGeneration`; PyAutoGUI-style output | Data-only restricted parser implemented and tested, **selection disabled**. Official deployment needs custom code; the local llama.cpp converter has no registration for this architecture. Qwen2.5 ancestry does not establish compatibility. |
| Holo3.1-9B | Exact ID **`Hcompany/Holo-3.1-9B`**, `qwen3_5`, native function calling | Catalogued, selection disabled. Architecture conversion exists locally, but the checkpoint's action/tool protocol and projector/runtime combination have not been qualified. Do not send the A11y JSON prompt and call that native integration. |
| Holo4-35B-A3B | `qwen3_5_moe`, tool-calling agent | Optional, selection disabled. All 35B parameters need memory despite 3B active per token. Nominal 4-bit language weights alone are ~17.5 GB before quantization overhead, vision, cache, other models and macOS. Poor first choice for 24 GiB; no local fit/speed claim. |

Sources: [Holo2 card](https://huggingface.co/Hcompany/Holo2-4B),
[official localization recipe](https://github.com/hcompai/hai-cookbook/blob/main/holo2/holo_2_localization_huggingface.ipynb),
[UI-TARS release](https://huggingface.co/ByteDance-Seed/UI-TARS-1.5-7B),
[coordinate guide](https://github.com/bytedance/UI-TARS/blob/main/README_coordinates.md),
[OSWorld UI-TARS implementation](https://github.com/xlang-ai/OSWorld/blob/main/mm_agents/uitars_agent.py),
[OpenCUA card](https://huggingface.co/xlangai/OpenCUA-7B),
[OpenCUA agent](https://github.com/xlang-ai/OpenCUA/blob/main/evaluation/agentnetbench/agent/opencua.py),
[Holo3.1 card](https://huggingface.co/Hcompany/Holo-3.1-9B),
[Holo4 card](https://huggingface.co/Hcompany/Holo4-35B-A3B),
[llama.cpp multimodal documentation](https://github.com/ggml-org/llama.cpp/blob/master/docs/multimodal.md).
The configs, rather than model-card architecture prose/tags, determine the
architecture entries above. No hosted inference endpoint is used.

## Selection and supported behavior

Settings → Advanced → AI model → Connection details offers a GUI profile and an
explicit installed Ollama model name. Save with Use model. Chat/vision model fields
retain their existing meanings. Environment overrides are useful for benchmarks:

```bash
A11Y_GUI_PROFILE=holo2 A11Y_GUI_MODEL=OWNER_INSTALLED_ALIAS python3 a11y_desktop.py
A11Y_GUI_PROFILE=ui-tars A11Y_GUI_MODEL=OWNER_INSTALLED_ALIAS python3 a11y_desktop.py
```

`A11Y_GUI_PROFILE=qwen3-vl` restores the normal pipeline; `A11Y_VISION_MODEL`
continues to select its actual vision model. No profile is inferred from an alias.
The operator must verify that an alias contains the intended checkpoint and matching
vision projector/chat template. Ollama must report vision capability. This check
is necessary but does not certify checkpoint provenance or coordinate behavior.

Holo2 uses the cookbook's localization prompt and JSON grammar; it only replaces
coordinates for a pixel click already chosen by the existing policy. Thus its
latency includes the Qwen policy call plus the grounder call. This is a **hybrid**
comparison, not a standalone Holo2 agent score. Numbered targets remain exact code.
Values at/past the image edge are rejected, never clamped into a plausible click.

UI-TARS supports single/double/right clicks, literal typing, supported shortcuts,
drags, finished and call_user. Box-token wrappers and plain point tuples are
accepted. One action only: AST literals are decoded; generated Python is never
executed. Point-targeted scrolling and the documented five-second wait are
currently rejected: A11y's existing primitive cannot faithfully express either.
Unsupported shortcuts, code, multi-action output and malformed/truncated responses
fail closed. OpenCUA's parser supports clicks, literal typing, supported keys and
termination, but is unavailable in the app until its local runtime is qualified.

Read-only clicks can use navigation risk only when the predicted point is inside
an unambiguous observed target; normal sensitive-label checks still apply. Other
external-policy proposals conservatively require review. This limitation must be
reported in live comparisons: review-blocked tasks are not automatic successes.
All translated steps still go through the same ComputerTask validation, native hit
checks, capture guard, cancellation and completion verification. `finished()` is
only a claim for the verifier, never proof of success. Routing, answer generation
and final verification continue to use the existing models.

## Benchmarking

Use `scripts/live_eval.py` for real task outcomes when screen use is authorized.
Run candidates serially with the **same suite, seed, repetitions, code and checker
version**; include Qwen before and after to detect environmental drift:

```bash
A11Y_GUI_PROFILE=qwen3-vl A11Y_WEB_ANSWERS=0 nice -n 19 taskpolicy -b \
  .venv/bin/python scripts/live_eval.py --suite capability --seed 7 --runs 3 --out .data/live_eval/gui-qwen
# After independently qualifying an owner-installed model:
A11Y_GUI_PROFILE=holo2 A11Y_GUI_MODEL=OWNER_INSTALLED_ALIAS A11Y_WEB_ANSWERS=0 \
  nice -n 19 taskpolicy -b .venv/bin/python scripts/live_eval.py \
  --suite capability --seed 7 --runs 3 --out .data/live_eval/gui-holo2
```

Live scope metadata records the GUI profile/alias. Retain app.log, runs.jsonl,
independent outcome checks and consented owned-window evidence. Diagnose checker
faults separately; don't treat model completion text as task success. Summarize
existing runs without taking the screen (workflow success requires every turn;
incomplete runs get no task-success score, and single runs no repeat score):

```bash
.venv/bin/python scripts/gui_benchmark.py --out /tmp/gui-live.json summarize-live \
  .data/live_eval/gui-qwen/TIMESTAMP .data/live_eval/gui-holo2/TIMESTAMP
```

For paired **unsnapped proposal grounding**, provide a frozen, permissioned,
scene-disjoint JSONL manifest. Each row has unique `id`, `scene`, relative `image`,
app-like `messages`, actual `schema`, optional native `targets`, and fractional
`box: [left,top,right,bottom]`. Optional `button` and `click_count` specify gesture.
Use captured contemporaneous observations; do not invent accessibility IDs or put
the gold box into the prompt. See `scripts/grounding_APP_EVAL.md` for collection
and splits. Capture/export remains owner-controlled; this tool never takes the screen.

```bash
nice -n 19 taskpolicy -b .venv/bin/python scripts/gui_benchmark.py \
  --out /tmp/gui-grounding.json replay --data /PATH/manifest.jsonl --runs 3 \
  --candidate qwen3-vl=qwen3-vl:4b-instruct --candidate holo2=OWNER_INSTALLED_ALIAS
```

All candidates receive the same image bytes and task observation; native protocols
and processor-specific resize legitimately differ. Record runtime version, model
digests/quantization, dataset+image hash, prompt/schema manifest, and run count.
The report keeps errors in the denominator, strict in-box hits, gesture-correct
hits, all-repeats success, point consistency, latency distribution and per-inference
timings. It never executes proposals or saves screen/text/model replies.

Memory is sampled from Ollama `/api/ps`: **total resident model `size_vram`**,
including the router/other loaded models. This is not process RSS or true allocator
peak; null/no samples means unavailable. Separate cold-load timing from warm calls
using the recorded load metrics; do not assume the first call was cold. For an
adoption run also record system memory pressure/swap externally and ensure no
training/other GPU load. Uninstalled or unsupported candidates are explicitly
skipped, not assigned a zero accuracy score.

Start with Holo2-4B (lowest expected extra memory), then qualify UI-TARS-7B. A
quantized 7–9B model may fit, but that is a capacity estimate, not a measured speed
or quality result. Prefer a candidate only after repeated live success/reliability
improves without safety regressions, with acceptable warm latency and no sustained
swap. Grounding alone cannot establish the best companion model.

## Validation performed here

Full unit suite: **961 passed in 90.203 s**. A subsequent regression covering
whole-workflow versus turn-level scoring passed with the related focused block
(**30 tests**). The screen-free synthetic Qwen smoke ran two simple scenes three
times each: **6/6 strict hits**, matching gestures and stable points, warm p50
**2.62 seconds**. Sampled total resident Ollama models: **5,521,696,357 bytes**.
Artifacts: `.data/live_eval/gui-models-smoke/report-final.json` and its manifest.
These are plumbing checks, not held-out GUI capability evidence. No new checkpoint
was loaded, no real-app candidate comparison ran, and no new default is recommended.

## Owner-authorized Holo2 installation (2026-10-27)

Installed `a11y-holo2-test:latest` in Ollama 0.34.0 from the pinned community
[Q4_K_M release](https://huggingface.co/mradermacher/Holo2-4B-GGUF), revision
`26722cf67095413610e95d086f0bfe9fa216c6c7`, with its **own F16 mmproj**.
Language weights: 2,716,070,336 bytes; projector: 836,180,640 bytes. Both size and
SHA-256 match the release's LFS metadata. Source files and provenance are under
`.data/models/holo2-4b/`; they are not committed.

Both files were uploaded to the loopback blob API and registered in one
`/api/create` request's `files` map. Ollama correctly classified the projector and
reports vision/thinking/completion/tools. This avoids the ambiguous two-FROM
Modelfile path. No existing model manifest or app default was edited.

A real local inference using the cookbook JSON schema and `think=false` returned
`{"x":211,"y":216}` on the synthetic Cedar image, inside the target, in ~3.04 s
including initial load. This establishes working image inference, not task quality.
The prior table's checkpoint-import limitation is resolved for **this specific
Q4_K_M + F16 projector + Ollama 0.34.0 combination**. Other quants/runtimes remain
unqualified. Use `A11Y_GUI_PROFILE=holo2 A11Y_GUI_MODEL=a11y-holo2-test` explicitly.

Paired repeat benchmark is recorded in `.data/live_eval/gui-holo2-grounding-1/`:
22 frozen targets / eight legacy scenes, three attempts each. No screen capture
or desktop action. The source labels include ambiguities documented in `audit.json`;
this development diagnostic cannot establish end-to-end task success.

### First actual Holo2 comparison

Frozen pixels-only legacy subset: **22 targets / eight scenes × three repeats**.
Both pipelines got **45/66 strict, gesture-correct hits (68.18%)**, and **15/22**
targets passed all three repeats. Removing the two pre-flagged ambiguous labels
leaves both at **45/60**. These repeated attempts are not 66 independent scenes.

| Pipeline | Strict hits | Median request | Median sampled resident models |
|---|---:|---:|---:|
| Qwen3-VL-4B | 45/66 | 3.24 s | 4.27 GB |
| Qwen policy + Holo2-4B grounder | 45/66 | 4.60 s | 8.76 GB |

Both use Ollama, Q4_K_M, context 8192, temperature 0, seed 7, same image bytes/task
observations. Memory means sampled `/api/ps` model `size_vram`, not process RSS or
allocator peak. The Qwen maximum includes an initial old-context transition; the
median above is more representative of steady residency. Holo2 adds ~4.49 GB.

Holo2 improves precipitation and site-search targets, but regresses zoom-out and
a title-bar target (the new points land on different visible controls). In the
site-search case the upstream Qwen policy also changed from a non-click outcome
to a click; the gain cannot be attributed only to localization. Play and Wind
remain ambiguous at the request/fixture level. Several other icon/target failures
remain; no transport/parser errors occurred. The same target outcomes repeat.

**Do not adopt this hybrid on these results:** no net hit-rate/repeat-success gain,
~42% higher median latency and roughly double model residency. This says nothing
conclusive about Holo2 as a differently designed standalone navigation policy.
Default Qwen remains unchanged. Full records, paired analysis, original box audit,
run settings and memory-pressure snapshots are in
`.data/live_eval/gui-holo2-grounding-1/`. No live task success was measured here.

### Isolating the model from task policy

The same 22 supplied target labels/images were also sent **directly** to each
model with the cookbook's JSON/grid prompt: one call, no Qwen policy choosing or
renaming the target. Three repeats each; complete records/driver/hash in
`native-localizer.json`, `native-analysis.json`, `native_localizer_eval.py`.

| Direct localizer | Strict hits | Targets passing all repeats | Median sampled model residency |
|---|---:|---:|---:|
| Qwen3-VL-4B | **54/66 (81.82%)** | **18/22** | 4.27 GB |
| Holo2-4B | **48/66 (72.73%)** | **16/22** | 4.49 GB |

Both produced valid coordinate JSON on every attempt and identical points across
repeats. Without the two pre-flagged ambiguous targets: Qwen **51/60**, Holo2
**48/60**; the remaining gap is only **one target** on this small set. Do not claim
statistical superiority across real applications. Holo2 additionally misses the
compass, while Play accounts for the other raw difference and is ambiguous.

First-pass/new-scene medians are essentially tied: **2.70 s Qwen, 2.68 s Holo2**
(eight scene-first calls each, initial loads included). Overall replay medians
**0.94 s / 1.16 s** benefit from repeatedly reusing unchanged images. They are NOT
an estimate of latency after real actions change the screenshot.

Qwen improves from 45/66 in the full policy protocol to 54/66 as a supplied-target
localizer. This points toward testing a smaller, stable grounding prompt and
separating target choice from coordinates. **That improvement is diagnostic only;
no production click path or model default was changed based on it.** A real-app
and independent larger-scene check is still needed before adopting such a change.

The already-installed Qwen3-VL-8B was also tested through the full-policy replay:
**27/66 (40.91%)**, median **5.68 s**, sampled resident model maximum **6.90 GB**.
It is not recommended for that protocol on this set; this does not establish its
general GUI capability. All models used greedy seed 7, context 8192 and the same
frozen images/requests. No new 8B weights were downloaded.

Reproduce the native diagnostic through the maintained benchmark tool:

```bash
A11Y_OLLAMA_CONTEXT=8192 nice -n 19 taskpolicy -b .venv/bin/python scripts/gui_benchmark.py \
  --out /tmp/native-grounding.json replay \
  --data .data/live_eval/gui-holo2-grounding-1/native-manifest.jsonl --runs 3 \
  --localization-only --candidate qwen3-vl=qwen3-vl:4b-instruct \
  --candidate holo2=a11y-holo2-test
```

Native mode requires an explicit `target` field in each row and permits only the
verified Qwen3-VL/Holo2 grid recipe. It calls the localizer once without a policy;
the report's kind distinguishes it from full-policy/hybrid results. Focused tests
cover this distinction, invalid coordinates and injected/extra fields. A real
four-call CLI smoke also reproduced the native helper's outcomes. Benchmark GPU
jobs are finished and their vision model caches unloaded to free memory. Model
registrations and verified weight files remain available.

Final current-source validation after native-mode addition: **964 unit tests passed
in 89.290 seconds**. No app defaults changed or commits made.


## Crowded native grounding follow-up (2026-10-28)

The larger pixel-localization diagnostic contains **39 targets in 13 real Finder,
Maps, Safari and System Settings screenshots**, with 25–46 distinct native target
positions per scene. Native labels/boxes supply gold coordinates; the localizer
receives only the image and supplied target. This measures the pixel fallback,
not the normal AX-first path or end-to-end task success. Scene provenance and
results: `.data/live_eval/recursive-1/dense-native.jsonl` and
`.data/live_eval/recursive-5/dense-model-comparison.json` / `dense-analysis.json`.

| Native localizer | Hits across 3 repeats | Targets correct every repeat | First-scene median | Candidate allocation median |
|---|---:|---:|---:|---:|
| Qwen3-VL-4B | 51/117 | 17/39 | 1.76 s | 4.27 GB |
| Holo2-4B | 72/117 | 24/39 | 1.70 s | 4.49 GB |

All 234 outputs parsed. The seven differing targets all favored Holo2. Per-repeat
hit counts were identical. Overall replay medians (~0.38/~0.35 s) benefit from
unchanged-image reuse and must not be advertised as changed-screen latency.
Allocations are sampled Ollama per-model `size_vram`, not process RSS or true peak.
This is sequential model order and only 13 scenes; native-derived labels/boxes
still need broader visual auditing before any general capability claim. The old
22-target diagnostic favored Qwen, so do not generalize either set alone.

The focused single-gesture path now supports **explicitly selected Holo2** as well
as Qwen. A small text call extracts the exact requested target; Holo2 then localizes
it directly, without first asking Qwen vision for a pixel action. Full-policy Holo2
fallback remains the earlier hybrid. Native/OCR matches still bypass the model,
and capture/hit/consent/cancellation/confirmation guards remain. No saved profile
or default changed. This is an experimental integration, not an adoption decision;
full task success and correct stopping remain open issues.
