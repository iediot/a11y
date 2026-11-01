"""Resample existing training rows only; preserve frozen validation/test bytes."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import random
import shutil


def rebalance(source, output):
    source, output = Path(source).resolve(), Path(output).resolve()
    if output == source or source in output.parents:
        raise ValueError('Use a separate output directory.')
    rows = [json.loads(line) for line in (source/'train.jsonl').read_text().splitlines() if line.strip()]
    groups = {True: [], False: []}
    other = []
    for row in rows:
        label = json.loads(row['messages'][-1]['content'])
        flag = label.get('acts_on_computer')
        (groups[flag] if type(flag) is bool else other).append(row)
    if not all(groups.values()):
        raise ValueError('Both action and non-action training examples are required.')
    count = max(map(len, groups.values()))
    balanced = list(other)
    for group in groups.values():
        balanced.extend(group[i % len(group)] for i in range(count))
    random.Random(17).shuffle(balanced)
    output.mkdir(parents=True, exist_ok=False)
    (output/'train.jsonl').write_text(''.join(json.dumps(row, ensure_ascii=False)+'\n' for row in balanced), encoding='utf-8')
    hashes = {}
    for name in ('valid.jsonl', 'test.jsonl'):
        shutil.copyfile(source/name, output/name)
        hashes[name] = hashlib.sha256((output/name).read_bytes()).hexdigest()
    report = dict(source=str(source), original_rows=len(rows), sampled_rows=len(balanced),
                  original_classes={str(k):len(v) for k,v in groups.items()},
                  sampled_per_class=count, unchanged_holdout_sha256=hashes, seed=17)
    (output/'resampling.json').write_text(json.dumps(report, indent=2)+'\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    print(json.dumps(rebalance(args.source, args.output), indent=2))
