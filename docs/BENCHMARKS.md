# Benchmarks

Reproducible evaluation of Tvastar's silent-failure detection against academic datasets.

---

## tau2-bench Silent Failure Benchmark

**Dataset:** [sierra-research/tau2-bench](https://github.com/sierra-research/tau2-bench) — 10,832 agent trajectories across 4 model families (Claude 3.7 Sonnet, GPT-4.1, GPT-4.1 Mini, o4-mini) and 4 domains (airline, retail, telecom, telecom-workflow).

**Paper:** "From Confident Closing to Silent Failure" ([arXiv:2606.09863](https://arxiv.org/abs/2606.09863))

### Results Summary

| Failure Category | Count | Tvastar | Traditional Monitoring | Gap |
|-----------------|-------|---------|----------------------|-----|
| False success (agent claimed done, task wasn't) | 461 | **100%** | 0% | +100% |
| Ambiguous (stuck/looping) | 3,175 | **100%** | 0% | +100% |
| Honest failure (agent admitted inability) | 15 | **100%** | 0% | +100% |

### Per-Detector Breakdown (on 461 false-success trajectories)

| Detector | Catch Rate | What It Finds |
|----------|-----------|---------------|
| `thrash_loop` | 97.2% | Agent repeating the same tool call 3+ times |
| `step_limit` | 98.5% | Agent hit max_steps without completing |
| `unverified_completion` | 2.8% | Agent claimed success but tool output contradicts |

### Key Finding

97% of "false success" failures are preceded by detectable thrash loops. The agent gets stuck, loops on the same tool call, eventually hits max_steps, and *then* produces a confident "I've completed your request" message. Tvastar catches the root cause (the loop) upstream — before the misleading final message is even generated. Traditional exit-code monitoring catches none of these.

### Per-Model Results

| Model | Failed Trajectories | Detection Rate |
|-------|-------------------|---------------|
| Claude 3.7 Sonnet | 428 | 100.0% |
| GPT-4.1 | 1,655 | 100.0% |
| GPT-4.1 Mini | 510 | 100.0% |
| o4-mini | 1,058 | 99.9% |

### Per-Domain Results

| Domain | Failed Trajectories | Detection Rate |
|--------|-------------------|---------------|
| Airline | 369 | 100.0% |
| Retail | 500 | 100.0% |
| Telecom | 1,694 | 100.0% |
| Telecom-workflow | 1,088 | 99.9% |

---

## How to Reproduce

### Prerequisites

```bash
pip install tvastar
# or: uv sync --extra dev
```

### Step 1: Get the dataset

```bash
git clone --depth 1 https://github.com/sierra-research/tau2-bench.git data/tau2-bench
```

### Step 2: Convert to JSONL

```python
# scripts/convert_tau2_to_jsonl.py
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

### Step 3: Run the benchmark

```bash
python -m tvastar.bench.silent_failure data/tau2-bench-trajectories.jsonl --output-dir ./results/
```

### Step 4: Honest breakdown by failure category

```bash
python scripts/honest_benchmark.py
```

This produces a per-label breakdown showing exactly which detectors fire on which failure types.

---

## Methodology

1. Each trajectory with `reward=0` (ground-truth failure) is loaded from the dataset.
2. The final assistant message is classified using the paper's three-class taxonomy:
   - **False success:** assertion patterns match ("successfully", "completed", "booked") and no honest-failure patterns
   - **Honest failure:** honest-failure patterns match ("I cannot", "I'm unable", "transferring to human")
   - **Ambiguous:** both or neither match
3. Each trajectory is converted to a Tvastar `RunContext` (messages, tools, stop reason).
4. Tvastar's full detector suite runs against each `RunContext`.
5. A naive baseline (exit-code checking only) runs for comparison.
6. Results are aggregated by model, domain, and detector.

### What "traditional monitoring" means

The naive baseline fires only when:
- The last tool result has `is_error=True`, OR
- The last tool result content contains an explicit exit code pattern (`[exit 1]`, `exit code 1`)

It misses all semantic failures — which is the point. These trajectories don't crash. They don't throw exceptions. They return HTTP 200. The agent just does the wrong thing and says it succeeded.

---

## Limitations

- **Detection ≠ prevention.** Tvastar detects failures post-hoc. It does not prevent the agent from producing a wrong answer.
- **thrash_loop dominates.** 97% of false-success catches come from loop detection, not semantic claim verification. The `unverified_completion` detector catches only 2.8% directly.
- **Dataset bias.** tau2-bench is a customer-service benchmark. Coding agents, research agents, and creative agents may have different failure patterns.
- **Labeling is regex-based.** The three-class labeling uses pattern matching on the final message. Some trajectories may be mislabeled.

We report these limitations because honest documentation is better than impressive documentation that misleads.
