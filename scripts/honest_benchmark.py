"""Honest benchmark: break down detection by failure label category.

This gives us the real numbers for:
1. false_success trajectories (the hard problem — agent lied)
2. ambiguous trajectories (loops/stuck — easier to catch)
3. honest_failure trajectories (agent admitted failure — no detection needed)
"""
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tvastar.bench.silent_failure import (
    FailureLabel,
    adapt_trajectory,
    aggregate_results,
    label_trajectory,
    load_trajectories,
    naive_baseline,
)
from tvastar.detect import default_detectors, run_detectors


def main():
    dataset_path = Path("data/tau2-bench-trajectories.jsonl")
    trajectories = load_trajectories(dataset_path)

    # Filter to failures only (reward=0)
    failures = [t for t in trajectories if t.reward == 0]
    print(f"Total trajectories: {len(trajectories)}")
    print(f"Failures (reward=0): {len(failures)}")

    # Categorize and analyze by label
    by_label = defaultdict(list)
    detectors = default_detectors()

    processed = 0
    errors = 0

    for traj in failures:
        # Get final assistant text for labeling
        final_text = ""
        for msg in reversed(traj.messages):
            if msg.get("role") == "assistant" and msg.get("content"):
                final_text = msg["content"]
                break

        label = label_trajectory(final_text)

        try:
            ctx = adapt_trajectory(traj)
            findings = run_detectors(ctx, detectors)
            baseline = naive_baseline(ctx)

            by_label[label.value].append({
                "id": traj.id,
                "model": traj.model,
                "domain": traj.domain,
                "detectors_fired": [f.detector for f in findings],
                "baseline_fired": len(baseline) > 0,
                "final_text_snippet": final_text[:100],
            })
            processed += 1
        except Exception as e:
            errors += 1

    print(f"\nProcessed: {processed}, Errors: {errors}")
    print(f"\n{'='*70}")
    print("RESULTS BY FAILURE LABEL")
    print(f"{'='*70}")

    for label_name in ["false_success", "ambiguous", "honest_failure"]:
        items = by_label[label_name]
        total = len(items)
        if total == 0:
            print(f"\n{label_name}: 0 trajectories")
            continue

        # How many had at least one detector fire?
        detected = sum(1 for item in items if item["detectors_fired"])
        baseline_detected = sum(1 for item in items if item["baseline_fired"])

        # Per-detector breakdown
        detector_counts = Counter()
        for item in items:
            for det in item["detectors_fired"]:
                detector_counts[det] += 1

        print(f"\n--- {label_name.upper()} ({total} trajectories) ---")
        print(f"  Tvastar detection rate: {detected}/{total} = {detected/total*100:.1f}%")
        print(f"  Baseline detection rate: {baseline_detected}/{total} = {baseline_detected/total*100:.1f}%")
        print(f"  Detection gap: +{(detected-baseline_detected)/total*100:.1f}%")
        print(f"  Per-detector breakdown:")
        for det, count in detector_counts.most_common():
            print(f"    {det}: {count}/{total} = {count/total*100:.1f}%")

        # For false_success: show some examples of what was/wasn't caught
        if label_name == "false_success":
            print(f"\n  Sample DETECTED false-success (unverified_completion):")
            uv_detected = [i for i in items if "unverified_completion" in i["detectors_fired"]]
            for ex in uv_detected[:3]:
                print(f"    [{ex['model']}] {ex['final_text_snippet']}")

            print(f"\n  Sample MISSED false-success (no unverified_completion):")
            uv_missed = [i for i in items if "unverified_completion" not in i["detectors_fired"]]
            for ex in uv_missed[:5]:
                dets = ex["detectors_fired"] or ["NONE"]
                print(f"    [{ex['model']}] fired={dets}")
                print(f"      text: {ex['final_text_snippet']}")

    # Summary table
    print(f"\n{'='*70}")
    print("SUMMARY TABLE (for blog post)")
    print(f"{'='*70}")
    print(f"| Category | Count | Tvastar | Baseline | Gap |")
    print(f"|----------|-------|---------|----------|-----|")
    for label_name in ["false_success", "ambiguous", "honest_failure"]:
        items = by_label[label_name]
        total = len(items)
        if total == 0:
            continue
        detected = sum(1 for item in items if item["detectors_fired"])
        baseline_detected = sum(1 for item in items if item["baseline_fired"])
        print(f"| {label_name} | {total} | {detected/total*100:.1f}% | {baseline_detected/total*100:.1f}% | +{(detected-baseline_detected)/total*100:.1f}% |")


if __name__ == "__main__":
    main()
