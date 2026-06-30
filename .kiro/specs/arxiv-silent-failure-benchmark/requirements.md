# Requirements Document

## Introduction

This feature implements a benchmark pipeline that takes the "false success" trajectory dataset from the arXiv paper "From Confident Closing to Silent Failure" (arxiv.org/abs/2606.09863) — which studied 9,876 agent trajectories across 8 model families — and runs those trajectories through Tvastar's existing silent-failure detectors. The output is a structured benchmark report and publishable blog post showing detection rates by model family, failure category, and individual detector, demonstrating what Tvastar catches that traditional monitoring misses.

## Glossary

- **Benchmark_Pipeline**: The end-to-end script that ingests the paper's dataset, converts trajectories into RunContext objects, runs detectors, and produces structured results
- **Trajectory**: A single agent run from the paper's dataset, comprising a sequence of messages (user prompts, assistant responses, tool calls, tool results) and metadata about whether the run was a "false success"
- **False_Success**: A trajectory the paper labels as one where the agent asserted task completion but the environment state shows the task was not actually completed
- **RunContext_Adapter**: The component that converts a raw trajectory from the paper's data format into a Tvastar RunContext object suitable for detector consumption
- **Detection_Report**: The structured JSON output containing per-trajectory detection results, aggregate statistics by model family and failure category, and per-detector firing rates
- **Blog_Generator**: The component that transforms the Detection_Report into a publishable Markdown blog post with tables, charts descriptions, and narrative analysis
- **Detector_Suite**: The set of Tvastar detectors applied to each trajectory (unverified_completion, thrash_loop, empty_answer, ignored_tool_error, unknown_tool, schema_mismatch, prompt_injection, step_limit)
- **Model_Family**: One of the 8 model families studied in the paper (e.g. GPT-4, Claude, Gemini, etc.)
- **Failure_Category**: The paper's classification of false-success failure modes (e.g. premature completion, ignoring errors, hallucinated success)

## Requirements

### Requirement 1: Dataset Ingestion

**User Story:** As a benchmark operator, I want to load the paper's trajectory dataset from a local file, so that I can process it through the detection pipeline.

#### Acceptance Criteria

1. WHEN a dataset file path is provided, THE Benchmark_Pipeline SHALL parse the file and produce a list of Trajectory objects
2. WHEN a trajectory entry lacks required fields (messages, model family, or ground-truth label), THE Benchmark_Pipeline SHALL skip that entry and log a warning with the entry identifier
3. THE Benchmark_Pipeline SHALL preserve the paper's metadata for each Trajectory including model family, benchmark source (tau2-bench or AppWorld), task identifier, and ground-truth false-success label
4. WHEN the dataset file does not exist at the specified path, THE Benchmark_Pipeline SHALL raise a FileNotFoundError with the attempted path

### Requirement 2: Trajectory-to-RunContext Conversion

**User Story:** As a benchmark operator, I want each trajectory automatically converted into a Tvastar RunContext, so that the existing detectors can analyze it without modification.

#### Acceptance Criteria

1. WHEN a Trajectory is converted, THE RunContext_Adapter SHALL map the trajectory's message sequence into a list of Tvastar Message objects preserving role, content blocks, and ordering
2. WHEN a trajectory contains tool call entries, THE RunContext_Adapter SHALL produce ToolUseBlock objects with the tool name, input arguments, and a stable identifier
3. WHEN a trajectory contains tool result entries, THE RunContext_Adapter SHALL produce ToolResultBlock objects linked to the corresponding ToolUseBlock by tool_use_id
4. THE RunContext_Adapter SHALL set the RunContext stopped field to "end_turn" for trajectories that completed normally and "max_steps" for those that hit step limits
5. THE RunContext_Adapter SHALL extract the final assistant text from the last assistant message and set it as RunContext final_text
6. THE RunContext_Adapter SHALL construct a ToolRegistry from the set of tool names observed in the trajectory, each with a permissive input schema, so that unknown_tool detection fires only when a tool name is genuinely absent from the trajectory's declared tool set
7. FOR ALL Trajectories with valid message sequences, converting to RunContext and reading back final_text SHALL produce a non-empty string matching the last assistant message text (round-trip property)

### Requirement 3: Detector Execution

**User Story:** As a benchmark operator, I want to run the full Tvastar detector suite against each converted trajectory, so that I can measure which detectors fire on the paper's false-success cases.

#### Acceptance Criteria

1. WHEN a RunContext is prepared from a trajectory, THE Benchmark_Pipeline SHALL invoke run_detectors with the default Detector_Suite and collect all Findings
2. THE Benchmark_Pipeline SHALL record, for each trajectory, the list of Findings including detector name, severity, and message
3. IF a detector raises an exception during execution, THEN THE Benchmark_Pipeline SHALL isolate the failure (per existing run_detectors behavior) and continue processing remaining trajectories
4. THE Benchmark_Pipeline SHALL process all trajectories in the dataset, not stopping on individual detector or conversion errors

### Requirement 4: Result Aggregation

**User Story:** As a benchmark operator, I want detection results aggregated by model family, failure category, and detector, so that I can understand detection coverage across dimensions.

#### Acceptance Criteria

1. THE Detection_Report SHALL include an overall detection rate: the fraction of false-success trajectories where at least one detector fired
2. THE Detection_Report SHALL include per-detector firing rates: for each detector in the Detector_Suite, the fraction of false-success trajectories where that specific detector fired
3. THE Detection_Report SHALL include per-model-family detection rates: for each of the 8 model families, the fraction of that family's false-success trajectories detected
4. THE Detection_Report SHALL include per-failure-category detection rates: for each failure category in the paper's taxonomy, the fraction detected
5. THE Detection_Report SHALL include a confusion-style breakdown: true positives (false-success trajectories detected), false negatives (false-success trajectories missed), and specificity data for non-false-success trajectories if available in the dataset
6. WHEN aggregation completes, THE Detection_Report SHALL be serializable to JSON with a stable schema

### Requirement 5: Blog Post Generation

**User Story:** As a marketing team member, I want an auto-generated blog post from the benchmark results, so that I can publish findings with minimal manual editing.

#### Acceptance Criteria

1. WHEN a Detection_Report is provided, THE Blog_Generator SHALL produce a Markdown document with sections: executive summary, methodology, results by detector, results by model family, results by failure category, comparison with traditional monitoring, and conclusion
2. THE Blog_Generator SHALL include formatted tables showing detection rates for each breakdown dimension
3. THE Blog_Generator SHALL include a methodology section explaining that trajectories are from a peer-reviewed study of 9,876 agent runs, processed through Tvastar's post-hoc detection pipeline without re-running the agents
4. THE Blog_Generator SHALL include inline citations referencing the source paper (arxiv.org/abs/2606.09863)
5. THE Blog_Generator SHALL avoid overstating results by using measured language (e.g. "detects N%" rather than "catches all") consistent with Tvastar's "don't oversell" policy

### Requirement 6: CLI Entry Point

**User Story:** As a benchmark operator, I want a command-line interface to run the full pipeline, so that I can execute it in CI or locally with a single command.

#### Acceptance Criteria

1. WHEN invoked with a dataset path argument, THE Benchmark_Pipeline SHALL execute ingestion, conversion, detection, aggregation, and output in sequence
2. THE Benchmark_Pipeline SHALL accept an --output-dir argument specifying where to write the Detection_Report JSON and blog post Markdown
3. THE Benchmark_Pipeline SHALL accept an optional --format argument with values "json", "markdown", or "both" (defaulting to "both")
4. WHEN processing completes, THE Benchmark_Pipeline SHALL print a summary line to stdout showing total trajectories processed, detection rate, and output file paths
5. IF the dataset path argument is missing, THEN THE Benchmark_Pipeline SHALL print a usage message and exit with code 1

### Requirement 7: Trajectory Format Parser

**User Story:** As a developer, I want a parser that handles the paper's specific data format, so that the benchmark can ingest trajectories regardless of the exact serialization used.

#### Acceptance Criteria

1. WHEN the dataset is in JSON Lines format (one JSON object per line), THE Benchmark_Pipeline SHALL parse each line as a separate trajectory
2. WHEN the dataset is a single JSON array, THE Benchmark_Pipeline SHALL parse the array and treat each element as a trajectory
3. IF a line or entry contains malformed JSON, THEN THE Benchmark_Pipeline SHALL skip that entry, log the line number and error, and continue processing
4. THE Benchmark_Pipeline SHALL support trajectory message formats from both tau2-bench and AppWorld benchmarks as described in the paper
5. FOR ALL valid trajectory JSON objects, parsing then serializing back to JSON then re-parsing SHALL produce an equivalent Trajectory object (round-trip property)

### Requirement 8: Traditional Monitoring Comparison

**User Story:** As a marketing team member, I want the report to quantify what traditional monitoring would miss, so that the comparison highlights Tvastar's value add.

#### Acceptance Criteria

1. THE Detection_Report SHALL define "traditional monitoring" as: exit-code checking, HTTP status code monitoring, and basic error-log scanning (string match for "error", "exception", "traceback" in stdout/stderr)
2. THE Detection_Report SHALL estimate traditional monitoring detection rate by checking whether each false-success trajectory contains an explicit error signal that surface-level monitoring would catch (non-zero exit code or unhandled exception in output)
3. THE Detection_Report SHALL compute the detection gap: Tvastar detection rate minus traditional monitoring detection rate
4. THE Blog_Generator SHALL present the detection gap prominently with a concrete example (e.g. "Traditional monitoring caught X% while Tvastar caught Y% — a Z percentage-point improvement")
