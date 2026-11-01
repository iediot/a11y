#!/usr/bin/env bash
# Mac only: register a separate alias and score frozen paired router rows, never adopt.
set -euo pipefail
root="${1:?repository path required}"
artifacts="${2:?directory with quantized GGUF and model.sha256 required}"
out="${3:?fresh output directory required}"
cd "$root"
[[ ! -e "$out" ]] || { echo 'Output exists; use a fresh directory'; exit 1; }
mkdir -p "$out"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 A11Y_AI_PROVIDER=ollama A11Y_OLLAMA_CONTEXT=8192
python="$root/.venv/bin/python"
model="$artifacts/router-arch-pilot-q4_k_m.gguf"
alias_name=a11y-router-arch-pilot-test
"$python" - "$artifacts" <<'PY'
import hashlib,json,sys
from pathlib import Path
p=Path(sys.argv[1]);f=p/'router-arch-pilot-q4_k_m.gguf'
expected=(p/'model.sha256').read_text().split()
assert len(expected)==2 and expected[1]=='router-arch-pilot-q4_k_m.gguf'
assert hashlib.file_digest(f.open('rb'),'sha256').hexdigest()==expected[0]
for name in ('parity-cpu.json','parity-cuda.json'):
 report=json.loads((p/name).read_text());assert report['passed'] and report['peft_tested']
 assert report['transformers']=='4.57.1'
verification=json.loads((p/'verification.json').read_text())
assert verification['base_verified']
data=Path('.data/live_eval/recursive-10/r3/data')
for split,expected in verification['data_sha256'].items():
 assert hashlib.sha256((data/(split+'.jsonl')).read_bytes()).hexdigest()==expected
assert len((data/'test.jsonl').read_text().splitlines())==406
PY
# Never replace a pre-existing test alias silently.
if ollama show "$alias_name" > /dev/null 2>&1; then echo 'Test alias already exists; inspect it before rerunning'; exit 1; fi
build=$("$python" -c 'from companion.instance import build_id; print(build_id())')
check_build() { [[ "$("$python" -c 'from companion.instance import build_id; print(build_id())')" == "$build" ]]; }
ollama --version > "$out/ollama-version.txt"
ollama show --modelfile qwen3:4b-instruct > "$out/base.Modelfile"
"$python" scripts/router_modelfile.py "$out/base.Modelfile" "$model" "$out/Modelfile"
ollama create "$alias_name" -f "$out/Modelfile" > "$out/register.log" 2>&1
ollama show --modelfile "$alias_name" > "$out/candidate.Modelfile"
# These scripts enforce nice 19. No desktop executor is invoked.
check_build
nice -n 19 taskpolicy -b "$python" scripts/router_eval.py --model qwen3:4b-instruct --data .data/live_eval/recursive-10/r3/data/test.jsonl --output "$out/base.json" > "$out/base.log" 2>&1
check_build
nice -n 19 taskpolicy -b "$python" scripts/router_eval.py --model "$alias_name" --data .data/live_eval/recursive-10/r3/data/test.jsonl --output "$out/candidate.json" > "$out/candidate.log" 2>&1
check_build
"$python" scripts/router_compare.py "$out/base.json" "$out/candidate.json" --output "$out/comparison.json" > "$out/compare.log" 2>&1
printf '%s\n' "$build" > "$out/build-id.txt"
echo "Comparison saved: $out/comparison.json. No model adoption or GUI tests."
