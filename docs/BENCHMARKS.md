# Benchmarks

This page records a reproducible experiment on Tvastar's silent-failure detectors. It is a report of the repository's evaluation, not a general product-performance claim.

## Scope and interpretation

This experiment measures **post-hoc detection** on archived tau2-bench failure trajectories. It does not establish universal correctness, prevention of bad outcomes, production false-positive rates, cost savings, or performance on arbitrary agent tasks.

“Traditional Monitoring” below is this repository's authored naive baseline, not a claim about commercial monitoring products or a representative industry comparator. It checks only the final tool result for an error or explicit nonzero-exit-code text.

Do not use these results in external marketing without the exact dataset revision, the conversion pipeline, retained command output, and held-out and false-positive evaluation. This repository documents the procedure below; it does not claim to include those additional evaluation artifacts or to support conclusions beyond this experiment.

---

## tau2-bench silent-failure benchmark

**Dataset:** [sierra-research/tau2-bench](https://github.com/sierra-research/tau2-bench) — 10,832 agent trajectories across 4 model families (Claude 3.7 Sonnet, GPT-4.1, GPT-4.1 Mini, o4-mini) and 4 domains (airline, retail, telecom, telecom-workflow).

**Paper:** “From Confident Closing to Silent Failure” ([arXiv:2606.09863](https://arxiv.org/abs/2606.09863))

### Reported results

| Failure Category | Count | Tvastar | Traditional Monitoring | Gap |
|-----------------|-------|---------|----------------------|-----|
| False success (agent claimed done, task wasn't) | 461 | **100%** | 0% | +100% |
| Ambiguous (stuck/looping) | 3,175 | **100%** | 0% | +100% |
| Honest failure (agent admitted inability) | 15 | **100%** | 0% | +100% |

### Per-detector breakdown (on 461 false-success trajectories)

| Detector | Catch Rate | What It Finds |
|----------|-----------|---------------|
| `thrash_loop` | 97.2% | Agent repeating the same tool call 3+ times |
| `step_limit` | 98.5% | Agent hit max_steps without completing |
| `unverified_completion` | 2.8% | Agent claimed success but tool output contradicts |

### Per-model results

| Model | Failed Trajectories | Detection Rate |
|-------|-------------------|---------------|
| Claude 3.7 Sonnet | 428 | 100.0% |
| GPT-4.1 | 1,655 | 100.0% |
| GPT-4.1 Mini | 510 | 100.0% |
| o4-mini | 1,058 | 99.9% |

### Per-domain results

| Domain | Failed Trajectories | Detection Rate |
|--------|-------------------|---------------|
| Airline | 369 | 100.0% |
| Retail | 500 | 100.0% |
| Telecom | 1,694 | 100.0% |
| Telecom-workflow | 1,088 | 99.9% |

---

## Reproduce the reported experiment

### Prerequisites

```bash
pip install tvastar
# or: uv sync --extra dev
```

### 1. Get the dataset

```bash
git clone --depth 1 https://github.com/sierra-research/tau2-bench.git data/tau2-bench
```

Record the clone's commit SHA before converting it. The shallow clone command alone does not identify a stable dataset revision.

### 2. Convert to JSONL

Save the following conversion pipeline as `scripts/convert_tau2_to_jsonl.py`, then run it from the repository root:

```python
import json
from pathlib import Path

results_dir = Path("data/tau2-bench/data/tau2/results/final")
output_path = Path("data/tau2-bench-trajectories.jsonl")

count = 0
with output_path.open("w", encoding="utf-8") as out:
    for f in sorted(results_dir.glob("*.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        parts = f.stem.split("_")
        model_name = parts[0]
        domain = parts[1] if len(parts) > 1 else "unknown"
        for sim in data.get("simulations", []):
            reward = sim.get("reward_info", {}).get("reward", 1.0)
            entry = {
                "id": sim["id"],
                "model": model_name,
                "domain": domain,
                "reward": int(reward),
                "messages": sim["messages"],
            }
            out.write(json.dumps(entry) + "\n")
            count += 1

print(f"Wrote {count} trajectories to {output_path}")
```

```bash
python scripts/convert_tau2_to_jsonl.py
```

### 3. Run the benchmark

```bash
python -m tvastar.bench.silent_failure data/tau2-bench-trajectories.jsonl --output-dir ./results/
```

### 4. Produce the failure-category breakdown

```bash
python scripts/honest_benchmark.py
```

Keep the command output with the dataset revision and converted JSONL metadata. The commands reproduce this repository's method; they do not by themselves supply a held-out evaluation or production false-positive rate.

---

## Methodology

1. Each trajectory with `reward=0` (ground-truth failure) is loaded from the dataset.
2. The final assistant message is classified using the paper's three-class taxonomy:
   - **False success:** assertion patterns match (`successfully`, `completed`, `booked`) and no honest-failure patterns.
   - **Honest failure:** honest-failure patterns match (`I cannot`, `I'm unable`, `transferring to human`).
   - **Ambiguous:** both or neither match.
3. Each trajectory is converted to a Tvastar `RunContext` (messages, tools, stop reason).
4. Tvastar's detector suite runs against each `RunContext`.
5. The repository's authored naive baseline runs for comparison.
6. Results are aggregated by model, domain, and detector.

### Definition of “Traditional Monitoring”

The authored baseline fires only when:

- The last tool result has `is_error=True`, or
- The last tool result content contains an explicit exit-code pattern (`[exit 1]`, `exit code 1`).

It is intentionally narrow. Its result should be read only as a contrast within this experiment, not as a result for commercial tools or monitoring practice generally.

## Limitations

- **Detection is not prevention.** The benchmark observes archived failures after the fact; it does not show that Tvastar prevents a wrong answer or repair.
- **The verifier is task-specific.** A detector result or passing named check does not establish general correctness, security, or safety.
- **`thrash_loop` dominates.** 97% of false-success catches come from loop detection, not direct semantic claim verification; `unverified_completion` catches only 2.8% directly.
- **Dataset scope is narrow.** tau2-bench is a customer-service benchmark. Coding, research, and creative agents may have different failure patterns.
- **Labeling is regex-based.** The three-class labels use final-message pattern matching, so some trajectories may be mislabeled.
- **External claims need more evidence.** Reproduce against a pinned revision, preserve pipeline and output, and evaluate held-out data and false positives before making external claims.
