#!/usr/bin/env bash
# Offline conversion only. Run in tmux on Arch after preparing .venv-convert.
set -euo pipefail
root="${1:-$HOME/a11y-training}"
kit="$root/qualification-pilot-1"
out="${2:-$root/runs/cuda-pilot-1-qualification}"
cd "$root"
exec 9>"$kit/conversion.lock"
flock -n 9 || { echo 'Another conversion owns the lock'; exit 1; }
[[ ! -e "$out" ]] || { echo 'Use a fresh output directory; no automatic resume/overwrite'; exit 1; }
mkdir -p "$out"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1 WANDB_DISABLED=true
export OMP_NUM_THREADS=4 MKL_NUM_THREADS=4
stage=preflight
state() {
    python3 - "$out/progress.json" "$1" "$stage" <<'PY'
import json,sys,time
from pathlib import Path
Path(sys.argv[1]).write_text(json.dumps(dict(status=sys.argv[2],stage=sys.argv[3],updated=time.time(),training=False,screen_actions=False,adopted=False),indent=2)+'\n')
PY
}
trap 'code=$?; if (( code != 0 )); then state failed; fi' EXIT
trap 'state interrupted; exit 130' INT TERM
state running
trainpy="$root/.venv/bin/python"
convertpy="$root/.venv-convert/bin/python"
[[ -x "$trainpy" && -x "$convertpy" ]] || { echo 'Prepare separate conversion environment as documented; training environment stays intact'; exit 1; }
"$trainpy" - <<'PY'
import transformers,peft
assert transformers.__version__=='4.57.1'
assert peft.__version__=='0.17.1'
PY
"$convertpy" - <<'PY'
import torch,transformers,numpy,sentencepiece,google.protobuf
assert transformers.__version__=='4.57.6'
assert numpy.__version__.startswith('2.2.')
import shutil
assert shutil.disk_usage('.').free>24*1024**3, 'Need at least 24 GiB free for separate GGUF intermediates'
PY
"$trainpy" -c 'import importlib.metadata as m; print("\n".join(sorted(d.metadata["Name"]+"=="+d.version for d in m.distributions())))' > "$out/training-environment.txt"
"$convertpy" -c 'import importlib.metadata as m; print("\n".join(sorted(d.metadata["Name"]+"=="+d.version for d in m.distributions())))' > "$out/conversion-environment.txt"
base="$root/models/Qwen3-4B-Instruct-2507"
run="$root/runs/cuda-pilot-1"
stage=verify; state running
"$trainpy" "$kit/scripts/router_peft_verify.py" --run "$run" --data "$root/data/router-r3" --base "$base" --out "$out/verification.json" > "$out/verification.log" 2>&1
stage=parity; state running
nice -n 19 "$trainpy" "$kit/scripts/router_cuda_parity.py" --peft --device cpu --out "$out/parity-cpu.json" > "$out/parity-cpu.log" 2>&1
nice -n 19 "$trainpy" "$kit/scripts/router_cuda_parity.py" --peft --device cuda --out "$out/parity-cuda.json" > "$out/parity-cuda.log" 2>&1
# Local source archive created from the exact already-installed Mac checkout.
stage=tools; state running
(cd "$kit" && sha256sum -c tool-source.sha256)
tools="$out/llama.cpp"
mkdir "$tools"
tar -xzf "$kit/llama.cpp.tar.gz" -C "$tools"
cmake -S "$tools" -B "$tools/build" -DGGML_CUDA=OFF -DGGML_NATIVE=OFF -DLLAMA_OPENSSL=OFF -DLLAMA_BUILD_TESTS=OFF > "$out/cmake.log" 2>&1
cmake --build "$tools/build" --target llama-export-lora llama-quantize -j 4 > "$out/build.log" 2>&1
# Convert unquantized HF base directly; never merge into training's NF4 tensors.
stage=base-f16; state running
nice -n 19 "$convertpy" "$tools/convert_hf_to_gguf.py" "$base" --outtype f16 --outfile "$out/base-f16.gguf" > "$out/base-f16.log" 2>&1
stage=adapter-gguf; state running
nice -n 19 "$convertpy" "$tools/convert_lora_to_gguf.py" --base "$base" --outtype f32 --outfile "$out/adapter-f32.gguf" "$run/selected-adapter" > "$out/adapter-gguf.log" 2>&1
stage=merge; state running
nice -n 19 "$tools/build/bin/llama-export-lora" -m "$out/base-f16.gguf" --lora "$out/adapter-f32.gguf" -o "$out/merged-f16.gguf" > "$out/merge.log" 2>&1
stage=quantize; state running
nice -n 19 "$tools/build/bin/llama-quantize" "$out/merged-f16.gguf" "$out/router-arch-pilot-q4_k_m.gguf" Q4_K_M 4 > "$out/quantize.log" 2>&1
(cd "$out" && sha256sum router-arch-pilot-q4_k_m.gguf > model.sha256)
cp "$kit/tool-revision.txt" "$out/tool-revision.txt"
stage=complete; state finished
echo "Complete: $out/router-arch-pilot-q4_k_m.gguf. Transfer only this model plus small reports/checksums. No Ollama registration or training was run."
