# On-device speed tuning (macOS, Apple Silicon)

The coding sessions that built this app cannot download or run models, so model
choice and real latency must be measured on your Mac. This page gives starting
models, settings, and a prompt for a coding agent (Codex, Claude Code) running
locally in this repository.

## Models to try

Text calls (understanding requests, chat) and image calls (computer use) use
separate models, so a small fast text model can sit next to a vision model:

| Role | Env variable | Default | Low-memory default / faster option |
|---|---|---|---|
| Text: routing, chat | `A11Y_AI_MODEL` | `qwen3:4b-instruct` | `qwen3:1.7b` |
| Vision: computer use | `A11Y_VISION_MODEL` | `qwen3-vl:4b-instruct` | `qwen3-vl:2b-instruct` |

The setup window picks these (`companion/installer.py`); choices are saved in `<data>/ai.json`.

Tags change; confirm with `ollama pull <name>` (an unknown tag fails with an error)
and `ollama list`. Keep a Qwen VL model for vision: the task loop asks for click
coordinates as fractions of the screenshot, which the Qwen VL family is trained to
produce. Smaller models are faster but may click less accurately; measure both.

```sh
ollama pull qwen3:4b-instruct
ollama pull qwen3-vl:4b-instruct
ollama pull qwen3-vl:2b-instruct
```

## Settings

```sh
# Ollama server (set before `ollama serve`, or with launchctl setenv for the app)
export OLLAMA_MAX_LOADED_MODELS=2      # keep text and vision models loaded together
export OLLAMA_FLASH_ATTENTION=1        # faster prompt processing on Apple Silicon
export OLLAMA_KV_CACHE_TYPE=q8_0       # smaller KV cache (needs flash attention)

# A11y
export A11Y_AI_MODEL=qwen3:4b-instruct
export A11Y_VISION_MODEL=qwen3-vl:4b-instruct
export A11Y_MODEL_IMAGE_WIDTH=1280     # Ollama upscales smaller images to ~1024 tokens anyway
export A11Y_OLLAMA_KEEP_ALIVE=30m
```

Check the log after every change: a source run prints to its terminal, the packaged app writes
`~/Library/Application Support/A11y/a11y.log`. The first line of a run shows the build ID; `task.step.summary` lines show where each step's time goes
and `ollama.finished` lines show `prompt_eval_count`/`prompt_eval_ms` per call.

## Prompt for a local coding agent

Copy this into Codex or Claude Code opened in this repository on your Mac:

```text
You are working in the A11y repository on my Mac (Apple Silicon) with Ollama
installed. Read AGENTS.md (or CLAUDE.md), docs/ai/HANDOFF.md and docs/ai/ON_DEVICE_TUNING.md first.
Goal: make computer-use tasks fast and reliable on THIS machine, measured, not guessed.

1. Confirm the running code: quit A11y, relaunch it, and check that the build ID
   on the first line of the log (terminal, or ~/Library/Application Support/A11y/a11y.log) matches
   `python -c "import companion.instance as i; print(i.build_id())"`.
2. Baseline: run `python scripts/benchmark_pipeline.py --runs 5` with the current
   models and save the JSON. It never executes actions.
3. Pull the candidate models in docs/ai/ON_DEVICE_TUNING.md (verify each tag exists).
   For each text/vision pair, rerun the benchmark with A11Y_AI_MODEL and
   A11Y_VISION_MODEL set, plus A11Y_MODEL_IMAGE_WIDTH 1280 vs 1440 for the vision
   model (Ollama upscales anything smaller to ~1024 image tokens). Record p50/p95 step time, model calls per step, prompt_eval_count,
   prompt_eval_ms and eval_ms. Also try OLLAMA_FLASH_ATTENTION=1 and
   OLLAMA_KV_CACHE_TYPE=q8_0 on the Ollama server.
4. With me watching, run these real tasks with each promising pair and record
   steps, total seconds and whether the result was correct:
   - "open teams in a new safari window and see if there are any new tasks for me"
   - "open calculator and sum 2+2"
   - "open textedit and write a two-sentence summary of my day"
   - "show me where the close button of the front window is" (must only highlight)
   Read the `A11y timing` lines from the log for each run.
5. Pick the fastest pair whose results are correct, and set it as the documented
   default. Only change code where measurements show a bottleneck; prefer moving
   predictable steps into code over adding model calls.
6. Keep all tests passing:
   QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests -q

Rules: never give the model shell access, never bypass target/focus validation,
consent, password-field or sensitive-action review to gain speed. Do not claim a
task worked without seeing its result on screen. Report a table of measured
numbers per model pair and what you changed.
```
