# Tvastar Event Specification v0.1

The one specifiable interface: the event format emitted by any Tvastar-compliant agent harness.

Any tool, dashboard, or analyzer that reads this format is interoperable with any Tvastar implementation — regardless of language.

## Status

**Draft v0.1** — stable enough for tooling to consume, unstable enough that breaking changes are expected before v1.0.

## Event Format

Every Tvastar event is a JSON object with these required fields:

| Field | Type | Description |
|-------|------|-------------|
| `spec_version` | string | Always `"tvastar/0.1"`. Consumers MUST check before parsing. |
| `event_type` | string | One of: `tool_call`, `tool_result`, `detection`, `compaction`, `approval`, `task_start`, `task_end`, `loop_state_change` |
| `run_id` | string | Unique identifier for this run/session |
| `step` | integer | Zero-indexed step number within the run |
| `timestamp` | string | ISO 8601 datetime |

## Optional Fields (present based on event_type)

### Common Context

| Field | Type | Description |
|-------|------|-------------|
| `loop_id` | string | Stable loop identifier (persists across runs) |
| `agent` | string | Name of the AgentSpec |
| `model` | string | Model identifier (e.g. "claude-sonnet-4-6") |
| `parent_span_id` | string\|null | Parent span for nested events |
| `usage.input_tokens` | integer | Input tokens consumed |
| `usage.output_tokens` | integer | Output tokens produced |
| `cost_usd` | number | Estimated cost in USD |

### tool_call / tool_result

```json
{
  "tool": {
    "name": "bash",
    "input": {"command": "pytest -q"},
    "output": "3 passed in 1.2s",
    "is_error": false,
    "duration_ms": 1234
  }
}
```

### detection

```json
{
  "verification": {
    "result": "FAIL",
    "detector": "unverified_completion",
    "severity": "error",
    "message": "final answer claims success but the last tool result shows failure",
    "correction": "Re-run pytest now. Do NOT claim success until all tests pass."
  }
}
```

Result MUST be one of: `PASS`, `FAIL`, `UNVERIFIABLE`.
Severity MUST be one of: `info`, `warning`, `error`.
Correction is the imperative message injected back into the agent (or null if none).

### approval

```json
{
  "approval": {
    "tool": "bash",
    "approved": true,
    "approver": "model:claude-haiku-3",
    "reason": "non-destructive read operation"
  }
}
```

Approver indicates who: `"human"`, `"model:<name>"`, or `"policy"`.

### compaction

```json
{
  "compaction": {
    "messages_before": 47,
    "messages_after": 12,
    "trigger": "threshold"
  }
}
```

Trigger MUST be one of: `threshold`, `overflow`, `manual`.

### loop_state_change

```json
{
  "state_change": {
    "from": "RUNNING",
    "to": "VERIFYING"
  }
}
```

## Design Decisions

- **`additionalProperties: true`** — implementations MAY add custom fields. Consumers MUST ignore fields they don't recognize.
- **No signatures in v0.1** — Ed25519/HMAC signing exists in `ExecutionReceipt` but is not required for event emission. Added when there's a real threat model.
- **No wire format** — events are JSON objects. Transport is implementation choice (file, stdout, HTTP, message queue).
- **Single event per line** — the recommended serialization is JSONL (one JSON object per line).

## Compliance

An implementation is "Tvastar event-compliant" if:
1. It emits JSON objects conforming to this schema for all tool calls and detector findings
2. The `spec_version` field is present and set to `"tvastar/0.1"`
3. The `verification.result` uses the three-value enum (PASS/FAIL/UNVERIFIABLE)
4. Events are valid JSON parseable by any standard JSON parser

That's it. Four rules.

## What This Does NOT Specify

- Loop lifecycle states (still evolving)
- Fleet coordination protocol (still building)
- Memory interface (backend is implementation choice)
- Wire format for loop-to-loop communication (no proven use case yet)
- Language or runtime requirements

These may be specified in future versions when they stabilize through production use.
