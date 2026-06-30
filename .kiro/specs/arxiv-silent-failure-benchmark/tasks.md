# Implementation Plan: arXiv Silent-Failure Benchmark

## Overview

Implement a single-file benchmark pipeline at `src/tvastar/bench/silent_failure.py` that ingests the tau2-bench trajectory dataset from the arXiv paper "From Confident Closing to Silent Failure", converts trajectories into `RunContext` objects, runs Tvastar's detector suite, aggregates results, and generates a publishable Markdown report. Follows the existing `bench/swebench.py` pattern with CLI entry via `python -m tvastar.bench.silent_failure`.

## Tasks

- [x] 1. Create module skeleton and data models
  - [x] 1.1 Create `src/tvastar/bench/silent_failure.py` with imports, dataclasses, and enum
    - Define `RawTrajectory` dataclass with fields: `id`, `model`, `domain`, `reward`, `messages`
    - Define `FailureLabel` enum with values: `false_success`, `honest_failure`, `ambiguous`
    - Define `TrajectoryResult` dataclass with fields: `id`, `model`, `domain`, `label`, `tvastar_findings`, `baseline_findings`
    - Define `ModelStats` and `DomainStats` dataclasses for aggregation
    - Define `AggregatedResults` dataclass with all aggregate fields from design
    - Add standard module docstring following swebench.py style
    - _Requirements: 1.1, 1.3, 4.1, 4.2, 4.3, 4.4, 4.5, 4.6_

  - [x] 1.2 Add `silent_failure` export to `src/tvastar/bench/__init__.py`
    - Import and re-export the public entry points (e.g., `run_benchmark` or equivalent)
    - _Requirements: 6.1_

- [x] 2. Implement dataset loading and format parsing
  - [x] 2.1 Implement `load_trajectories` function
    - Accept a `Path` argument; raise `FileNotFoundError` if path doesn't exist
    - Support JSONL format (one JSON object per line)
    - Support single JSON array format
    - Support directory of JSON files
    - Skip malformed JSON entries with a logged warning including line number/filename
    - Skip entries missing required fields (`messages`, `model`, `reward`) with logged warning
    - Preserve metadata: model family, domain, task identifier, reward label
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 7.1, 7.2, 7.3, 7.4_

  - [ ]* 2.2 Write unit tests for dataset loading
    - Test JSONL parsing with valid and malformed lines
    - Test single JSON array parsing
    - Test FileNotFoundError for missing path
    - Test skipping entries with missing required fields
    - Test round-trip: parse → serialize → re-parse produces equivalent Trajectory
    - _Requirements: 1.1, 1.2, 1.4, 7.1, 7.2, 7.3, 7.5_

- [x] 3. Implement three-class labeling
  - [x] 3.1 Implement `label_trajectory` function
    - Define regex patterns for false-success assertions (e.g., "successfully", "has been processed", "completed")
    - Define regex patterns for honest-failure signals (e.g., "I cannot", "I'm unable", "transferring to a human")
    - Return `FALSE_SUCCESS` when assertion patterns match and honest-failure patterns do not
    - Return `HONEST_FAILURE` when honest-failure patterns match and assertion patterns do not
    - Return `AMBIGUOUS` when both match or neither matches
    - _Requirements: 1.3, 4.4_

  - [ ]* 3.2 Write unit tests for labeling
    - Test clear false-success messages
    - Test clear honest-failure messages
    - Test ambiguous messages (both match or neither match)
    - _Requirements: 1.3_

- [x] 4. Implement trajectory-to-RunContext conversion
  - [x] 4.1 Implement `adapt_trajectory` function
    - Map tau2-bench message dicts to Tvastar `Message` objects preserving role, content, and ordering
    - Convert `tool_calls` entries to `ToolUseBlock` objects with name, input, and stable ID
    - Convert `tool` role messages to `ToolResultBlock` linked by `tool_use_id`
    - Construct a `ToolRegistry` with permissive schemas for all tool names observed in the trajectory
    - Set `stopped` to `"end_turn"` for normally-completed trajectories, `"max_steps"` for step-limited ones
    - Extract final assistant text and set as `final_text`
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6_

  - [ ]* 4.2 Write unit tests for trajectory adaptation
    - Test message mapping preserves role and content ordering
    - Test ToolUseBlock creation with correct name, input, and ID
    - Test ToolResultBlock linking to corresponding ToolUseBlock by tool_use_id
    - Test ToolRegistry construction includes all observed tool names
    - Test final_text extraction from last assistant message
    - Test round-trip: adapt then read final_text produces non-empty string matching last assistant text
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7_

- [x] 5. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 6. Implement detector execution and naive baseline
  - [x] 6.1 Implement pipeline orchestration for detector execution
    - For each trajectory: load → label → adapt → run `default_detectors()` → collect findings
    - Record per-trajectory findings including detector name, severity, and message
    - Isolate failures: if a detector or conversion raises, log and continue with remaining trajectories
    - Process all trajectories in dataset without stopping on individual errors
    - _Requirements: 3.1, 3.2, 3.3, 3.4_

  - [x] 6.2 Implement `naive_baseline` detector function
    - Fire only when last `ToolResultBlock` has `is_error=True`
    - Fire when last `ToolResultBlock` content contains explicit exit-code pattern (`[exit 1]`, `exit code 1`)
    - Intentionally miss semantic failures (that's the point of the comparison)
    - Return a `Finding` with detector name "naive_baseline" when triggered
    - _Requirements: 8.1, 8.2_

  - [ ]* 6.3 Write unit tests for naive baseline and pipeline execution
    - Test naive_baseline fires on explicit error signals
    - Test naive_baseline does NOT fire on semantic failures
    - Test pipeline continues after individual trajectory conversion errors
    - _Requirements: 3.3, 3.4, 8.1, 8.2_

- [x] 7. Implement result aggregation
  - [x] 7.1 Implement `aggregate_results` function
    - Compute overall detection rate: fraction of false-success trajectories where ≥1 detector fired
    - Compute per-detector firing rates across false-success trajectories
    - Compute per-model-family detection rates
    - Compute per-domain (failure category) detection rates
    - Compute baseline detection rate and detection gap (Tvastar rate − baseline rate)
    - Compute confusion-style breakdown: true positives, false negatives, and specificity for non-false-success if available
    - Ensure `AggregatedResults` is JSON-serializable with stable schema
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 8.3_

  - [ ]* 7.2 Write unit tests for aggregation
    - Test overall detection rate calculation with known inputs
    - Test per-detector rates with mixed findings
    - Test per-model breakdown with multiple model families
    - Test JSON serialization of AggregatedResults
    - _Requirements: 4.1, 4.2, 4.3, 4.6_

- [x] 8. Implement report generation
  - [x] 8.1 Implement `generate_report` function
    - Produce Markdown with sections: Executive Summary, Methodology, Per-Detector Analysis, Per-Model-Family Breakdown, Domain Analysis, Traditional Monitoring Comparison, Conclusion
    - Include formatted tables showing detection rates for each breakdown dimension
    - Include methodology section explaining the 9,876 trajectory dataset from peer-reviewed study
    - Include inline citation to arXiv:2606.09863
    - Use measured language ("detects N%") consistent with Tvastar's "don't oversell" policy
    - Present detection gap prominently with concrete example
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 8.4_

  - [ ]* 8.2 Write unit tests for report generation
    - Test report contains all required sections
    - Test report includes arXiv citation
    - Test report includes formatted tables
    - Test report uses measured language (no overstating)
    - _Requirements: 5.1, 5.4, 5.5_

- [x] 9. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 10. Implement CLI entry point
  - [x] 10.1 Implement `main` function with argparse and create `__main__.py`
    - Create `src/tvastar/bench/silent_failure.py` `main(argv)` function using argparse
    - Accept positional dataset path argument; print usage and exit(1) if missing
    - Accept `--output-dir` for where to write JSON report and Markdown blog post
    - Accept `--format` with values "json", "markdown", or "both" (default "both")
    - Execute full pipeline in sequence: ingest → convert → detect → aggregate → output
    - Print summary line to stdout: total trajectories, detection rate, output file paths
    - Write JSON results and/or Markdown report to output-dir based on --format
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5_

  - [x] 10.2 Create `src/tvastar/bench/silent_failure/__main__.py` for module invocation
    - Enable `python -m tvastar.bench.silent_failure` entry point
    - Import and call `main()` from the module
    - Note: this requires restructuring from single file to package if needed, or using the `if __name__ == "__main__"` pattern in the single file
    - _Requirements: 6.1_

  - [ ]* 10.3 Write unit tests for CLI
    - Test missing dataset argument prints usage and exits with code 1
    - Test --output-dir and --format argument parsing
    - Test summary line output format
    - _Requirements: 6.2, 6.3, 6.4, 6.5_

- [x] 11. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- The module is self-contained in `src/tvastar/bench/silent_failure.py` (~400-500 lines)
- Zero third-party runtime dependencies in core; use only stdlib
- Tests use pytest with `asyncio_mode="auto"` and no API keys required
- Follow the existing `bench/swebench.py` pattern for style and structure

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2", "2.1", "3.1"] },
    { "id": 2, "tasks": ["2.2", "3.2", "4.1"] },
    { "id": 3, "tasks": ["4.2", "6.1", "6.2"] },
    { "id": 4, "tasks": ["6.3", "7.1"] },
    { "id": 5, "tasks": ["7.2", "8.1"] },
    { "id": 6, "tasks": ["8.2", "10.1"] },
    { "id": 7, "tasks": ["10.2", "10.3"] }
  ]
}
```
