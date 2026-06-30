# Implementation Plan: Vidura BA Analysis of Tvastar

## Overview

This implementation plan covers the comprehensive Vidura BA Analysis of Tvastar — ensuring all 24 requirements, 35 correctness properties, and associated acceptance criteria are verified through property-based tests, unit tests, integration tests, and adversarial tests. The plan is structured to build from foundational core-loop tests outward through safety, compliance, infrastructure, and serving layers.

## Tasks

- [x] 1. Set up property-based testing infrastructure
  - [x] 1.1 Create PBT test module structure and shared fixtures
    - Create `tests/pbt/` directory with `conftest.py` containing shared Hypothesis strategies
    - Define reusable strategies: `st_messages`, `st_findings`, `st_tool_specs`, `st_run_results`, `st_agent_specs`
    - Configure Hypothesis settings (min 100 examples, deadline=None for async tests)
    - _Requirements: REQ-LOOP-001, REQ-DETECT-001, CON-006_

- [x] 2. Agent loop execution tests
  - [x] 2.1 Implement unit tests for Session agent loop
    - Test prompt → model.generate → tool execution → append → repeat cycle
    - Test END_TURN produces RunResult with stopped="end_turn"
    - Test concurrent tool execution within a single model response
    - Verify message history grows correctly per iteration
    - _Requirements: 1.1, 1.2, 1.3_

  - [x] 2.2 Write property test for loop termination (Property 1)
    - **Property 1: Agent loop termination**
    - For any AgentSpec with max_steps=N and a model always returning TOOL_USE, Session terminates with stopped="max_steps" after exactly N steps
    - Use `st.integers(min_value=1, max_value=50)` for max_steps
    - **Validates: Requirements 1.4**

  - [x] 2.3 Write property test for tool execution message growth (Property 2)
    - **Property 2: Tool execution grows message history**
    - For any ModelResponse with TOOL_USE containing T tool calls, Session appends one assistant message and one tool-results message per loop iteration
    - **Validates: Requirements 1.2**

  - [x] 2.4 Write property test for non-overflow exception propagation (Property 3)
    - **Property 3: Non-overflow exceptions propagate**
    - For any exception raised by model.generate that is NOT a context overflow, Session propagates it to the caller
    - **Validates: Requirements 1.5**

  - [x] 2.5 Write property test for context overflow single retry (Property 4)
    - **Property 4: Context overflow triggers single retry**
    - For any context overflow error when CompactionPolicy is configured, Session compacts and retries exactly once
    - **Validates: Requirements 1.6**

- [x] 3. Silent-failure detection tests
  - [x] 3.1 Implement unit tests for all seven detectors
    - Test `unverified_completion` with success claim + failure evidence
    - Test `thrash_loop` with repeated tool+args above threshold
    - Test `unknown_tool` with tool names not in registry
    - Test `schema_mismatch` with arguments violating input schema
    - Test `prompt_injection` with tool output matching injection patterns
    - Test `ignored_tool_error` with END_TURN after unrecovered tool error
    - Test `empty_answer` with empty final assistant text
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7_

  - [x] 3.2 Write property test for detector execution completeness (Property 5)
    - **Property 5: Detector execution completeness**
    - For any completed RunResult and N configured detectors, all N execute and their combined findings appear in RunResult.findings
    - **Validates: Requirements 2.8**

  - [x] 3.3 Write property test for unverified completion detection (Property 6)
    - **Property 6: Unverified completion detection**
    - For any RunResult where final assistant text contains success claim AND last tool result contains failure signal, emit Finding with Severity.ERROR
    - **Validates: Requirements 2.1**

  - [x] 3.4 Write property test for thrash loop detection (Property 7)
    - **Property 7: Thrash loop detection**
    - For any message history where same tool+args combination appears > threshold times, emit Finding with Severity.WARNING
    - **Validates: Requirements 2.2**

- [x] 4. Quality scoring tests
  - [x] 4.1 Implement unit tests for score_run function
    - Test deduction arithmetic: 30/ERROR, 10/WARNING, 20/max_steps, 50/error
    - Test clamping to minimum of 0
    - Test grade boundaries: PASS≥80, WARN≥60, FAIL<60
    - Test pure function: same input always produces same output
    - Test lazy computation of RunResult.quality property
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6_

  - [x] 4.2 Write property test for quality score arithmetic (Property 8)
    - **Property 8: Quality score arithmetic**
    - For any RunResult with E errors, W warnings, and stopped reason S: score = max(0, 100 - 30E - 10W - penalty(S))
    - Use `st.lists(st.sampled_from([Severity.ERROR, Severity.WARNING]))` for findings
    - **Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5**

- [x] 5. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 6. Sandbox execution safety tests
  - [x] 6.1 Implement unit tests for SecurityPolicy enforcement
    - Test denied_substrings matching raises SecurityViolation
    - Test allowed_commands enforcement for first token
    - Test denied_commands matching for first token
    - Test timeout handling returns ExecResult with timed_out=True
    - _Requirements: 4.1, 4.2, 4.3, 4.5_

  - [x] 6.2 Write property test for SecurityPolicy enforcement (Property 9)
    - **Property 9: SecurityPolicy enforcement**
    - For any command and SecurityPolicy, if command matches denied_substrings OR first token not in allowed_commands OR first token in denied_commands, then SecurityViolation is raised
    - **Validates: Requirements 4.1, 4.2, 4.3**

  - [x] 6.3 Write property test for CredentialFilter completeness (Property 10)
    - **Property 10: CredentialFilter completeness**
    - For any environment variable set and CredentialFilter with glob patterns, no variable whose name matches any pattern remains after filtering
    - **Validates: Requirements 4.4**

- [x] 7. Tool masking and governance tests
  - [x] 7.1 Implement unit tests for ToolPolicy and GovernancePolicy
    - Test ToolPolicy receives MaskContext and returns subset
    - Test GovernancePolicy.is_allowed blocks tool calls when False
    - Test GovernancePolicy with ApprovalGate requests approval before blocking
    - Test error ToolResultBlock returned for blocked tools
    - _Requirements: 5.1, 5.3, 5.4_

  - [x] 7.2 Write property test for ToolPolicy subset invariant (Property 11)
    - **Property 11: ToolPolicy subset invariant**
    - For any ToolPolicy and MaskContext with available tools A, the returned set intersected with A is a subset of A — policy can never grant tools not in available set
    - **Validates: Requirements 5.1**

  - [x] 7.3 Write property test for masking failure fallback (Property 12)
    - **Property 12: Masking failure fallback**
    - For any ToolPolicy that raises an exception, apply_policy() returns None (all tools exposed)
    - **Validates: Requirements 5.2**

  - [x] 7.4 Write property test for governance fails closed (Property 13)
    - **Property 13: Governance fails closed on unknown phases**
    - For any GovernancePolicy and any phase name not in its phases dictionary, is_allowed returns False for all tool names
    - **Validates: Requirements 5.5**

  - [x] 7.5 Write property test for GovernancePolicy copy independence (Property 14)
    - **Property 14: GovernancePolicy copy independence**
    - For any GovernancePolicy, copy() produces an instance where set_phase() on the copy does not affect the original
    - **Validates: Requirements 5.6**

- [x] 8. Prompt-injection detection and content boundary tests
  - [x] 8.1 Implement unit tests for scan_for_injection and wrap_untrusted
    - Test each of the five injection patterns: override_instructions, role_reassignment, exfiltration, fake_system_turn, reveal_system_prompt
    - Test wrap_untrusted produces sentinel delimiters and no-follow notice
    - Test scan_for_injection is a pure function with no side effects
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6_

  - [x] 8.2 Write property test for injection pattern detection completeness (Property 15)
    - **Property 15: Injection pattern detection completeness**
    - For any text matching one of the five injection patterns, scan_for_injection returns a list containing the matched pattern name(s)
    - Generate strings from pattern templates
    - **Validates: Requirements 6.1, 6.2, 6.3, 6.4, 6.5**

  - [x] 8.3 Write property test for untrusted content wrapping (Property 16)
    - **Property 16: Untrusted content wrapping**
    - For any content string, wrap_untrusted returns a string containing TVASTAR_UNTRUSTED_CONTENT sentinels and do-not-follow notice with original content preserved
    - **Validates: Requirements 6.6**

- [x] 9. Loop engineering tests
  - [x] 9.1 Implement unit tests for Loop state machine
    - Test state transitions: IDLE → TRIGGERED → RUNNING → VERIFYING → PASS/FAIL
    - Test FAIL → RETRY with backoff when iteration < max_iterations
    - Test FAIL → HANDOFF when retries exhausted
    - Test FAIL → SUSPENDED when consecutive_failures >= circuit_breaker_limit
    - Test crash recovery: orphaned RUNNING → INTERRUPTED
    - Test HandoffPolicy.escalate failure retries up to 3 times
    - Test cron expression validation at construction
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.8_

  - [x] 9.2 Write property test for loop state checkpointing (Property 17)
    - **Property 17: Loop state checkpointing**
    - For any Loop with a configured Store, every state transition is persisted to the Store
    - **Validates: Requirements 7.7**

  - [x] 9.3 Write property test for exponential backoff calculation (Property 18)
    - **Property 18: Exponential backoff calculation**
    - For any LoopConfig with backoff_base=B and failed iteration I, retry delay = B * 2^(I-1)
    - **Validates: Requirements 7.2**

  - [x] 9.4 Write property test for circuit breaker activation (Property 19)
    - **Property 19: Circuit breaker activation**
    - For any Loop with consecutive_failures >= circuit_breaker_limit, state transitions to SUSPENDED
    - **Validates: Requirements 7.4**

- [x] 10. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 11. Verifiable execution and audit trail tests
  - [x] 11.1 Implement unit tests for ExecutionReceipt and TrustLog
    - Test receipt creation with all required fields (run_id, agent name, model name, etc.)
    - Test receipt.verify() returns True for unmodified receipts
    - Test receipt.verify() returns False for tampered receipts
    - Test TrustLog chain linking via prev_hash
    - Test TrustLog.verify_chain() detects broken links
    - Test on_breach callback invocation
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7_

  - [x] 11.2 Write property test for receipt sign-verify round-trip (Property 20)
    - **Property 20: Receipt sign-verify round-trip**
    - For any valid RunResult data and signing key K, creating an ExecutionReceipt and calling verify(K) returns True; modifying any field and verifying returns False
    - **Validates: Requirements 8.3, 8.4**

  - [x] 11.3 Write property test for TrustLog chain integrity (Property 21)
    - **Property 21: TrustLog chain integrity**
    - For any sequence of N receipts appended to a TrustLog, verify_chain() returns True; tampering causes it to return False with identification
    - **Validates: Requirements 8.5, 8.6**

- [x] 12. PII/PHI sanitization tests
  - [x] 12.1 Implement unit tests for TokenVault
    - Test tokenize replaces sensitive patterns with opaque placeholders
    - Test rehydrate restores original values from placeholders
    - Test HIPAA policy redacts PHI entity types
    - Test Presidio policy for ML-powered detection
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.6, 9.7_

  - [x] 12.2 Write property test for TokenVault round-trip (Property 22)
    - **Property 22: TokenVault round-trip**
    - For any valid input string containing sensitive patterns, vault.rehydrate(vault.tokenize(text, policy)) produces the original string
    - **Validates: Requirements 9.5**

- [x] 13. Context compaction tests
  - [x] 13.1 Implement unit tests for CompactionPolicy
    - Test compaction triggers when message count exceeds max_messages
    - Test keep_last messages are preserved unchanged after compaction
    - Test min_messages threshold prevents compaction
    - Test force-compact on context overflow and single retry
    - Test 30-second cooldown between forced compaction attempts
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.7_

  - [x] 13.2 Write property test for compaction preserves tail (Property 23)
    - **Property 23: Compaction preserves tail**
    - For any message list M with len(M) > keep_last, after compaction the last K messages are identical to original
    - **Validates: Requirements 10.2**

  - [x] 13.3 Write property test for compaction respects min_messages (Property 24)
    - **Property 24: Compaction respects min_messages**
    - For any message list where len < min_messages, should_compact() returns False
    - **Validates: Requirements 10.4**

  - [x] 13.4 Write property test for compaction failure safety (Property 25)
    - **Property 25: Compaction failure does not break run**
    - For any compaction failure, Session continues with original message list unchanged
    - **Validates: Requirements 10.6**

- [x] 14. Durable execution and crash recovery tests
  - [x] 14.1 Implement unit tests for Checkpointer and resume
    - Test checkpoint saves message history after every tool turn
    - Test harness.resume() restores Session with full message history
    - Test corrupted checkpoint store returns None from resume()
    - Test checkpoint format includes session ID, messages, sandbox state
    - Test resumed Session continues loop from restored state
    - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5_

  - [x] 14.2 Write property test for checkpoint round-trip (Property 26)
    - **Property 26: Checkpoint round-trip**
    - For any session with messages M and a Store, after checkpointing, harness.resume(session_id) produces a session with messages equivalent to M
    - **Validates: Requirements 11.1, 11.2**

- [x] 15. Cost tracking and budget enforcement tests
  - [x] 15.1 Implement unit tests for Cost and BudgetPolicy
    - Test Cost computation from input_tokens, output_tokens, and model name
    - Test BudgetExceeded raised when on_exceed="raise"
    - Test RunResult with stopped="budget" when on_exceed="stop"
    - Test approval request when on_exceed="approve"
    - Test RunResult.cost reflects total token cost
    - _Requirements: 12.1, 12.2, 12.3, 12.4, 12.5, 12.6_

  - [x] 15.2 Write property test for cost monotonicity (Property 27)
    - **Property 27: Cost monotonicity**
    - For any sequence of model.generate calls within a run, cumulative cost.usd is monotonically non-decreasing
    - **Validates: Requirements 12.1, 12.6**

  - [x] 15.3 Write property test for budget enforcement (Property 28)
    - **Property 28: Budget enforcement**
    - For any BudgetPolicy with max_usd=X, when cost >= X: on_exceed="raise" raises BudgetExceeded; on_exceed="stop" returns stopped="budget"
    - **Validates: Requirements 12.2, 12.3**

- [x] 16. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 17. Observability and tracing tests
  - [x] 17.1 Implement unit tests for Tracer and Exporters
    - Test span emission for model.generate, tool.invoke, session.prompt
    - Test OTelExporter conforms to GenAI semantic conventions
    - Test JSONLExporter appends newline-delimited JSON records
    - _Requirements: 13.1, 13.2, 13.3, 13.5, 13.6_

  - [x] 17.2 Write property test for tracer failure isolation (Property 29)
    - **Property 29: Tracer failure isolation**
    - For any Tracer or Exporter that raises during span export, Session swallows the error and completes the run normally
    - **Validates: Requirements 13.4**

- [x] 18. DAG task execution tests
  - [x] 18.1 Implement unit tests for TaskGraph
    - Test tasks with no dependencies execute immediately in parallel
    - Test tasks start when all dependencies complete
    - Test GraphResult contains results keyed by task name
    - Test dependency results injection into task prompt context
    - Test structured output parsing for tasks with result=Schema
    - _Requirements: 15.1, 15.2, 15.4, 15.5, 15.6_

  - [x] 18.2 Write property test for DAG topological execution (Property 30)
    - **Property 30: DAG topological execution**
    - For any TaskGraph, no task begins before all its dependencies complete; independent tasks are eligible for concurrent execution
    - **Validates: Requirements 15.1, 15.2**

  - [x] 18.3 Write property test for DAG cycle rejection (Property 31)
    - **Property 31: DAG cycle rejection**
    - For any task dependency set forming a cycle, TaskGraph construction raises ValueError
    - **Validates: Requirements 15.3**

- [x] 19. Task delegation and depth control tests
  - [x] 19.1 Implement unit tests for session.task() delegation
    - Test child session creation using named AgentProfile
    - Test child spec resolution precedence: task override > profile > parent
    - Test router selects best-matching profile when no agent specified
    - Test cancel_after raises asyncio.TimeoutError
    - Test ValueError for non-existent profile names
    - _Requirements: 18.1, 18.3, 18.4, 18.5, 18.6_

  - [x] 19.2 Write property test for task delegation depth bound (Property 32)
    - **Property 32: Task delegation depth bound**
    - For any chain of session.task() delegations, task_depth never exceeds MAX_TASK_DEPTH (4); at the limit, RuntimeError is raised
    - **Validates: Requirements 18.2**

  - [x] 19.3 Write property test for child spec precedence (Property 33)
    - **Property 33: Child spec precedence**
    - For any combination of parent AgentSpec, AgentProfile, and task() overrides, child spec resolution follows: task override > profile > parent
    - **Validates: Requirements 18.3**

- [x] 20. Structured output tests
  - [x] 20.1 Implement unit tests for structured output
    - Test JSON schema instruction injection when result= specified
    - Test successful parse populates RunResult.data
    - Test retry up to _STRUCTURED_RETRIES on parse failure
    - Test fallback to raw text with WARNING finding after retries exhausted
    - Test support for Pydantic v2, v1, dataclasses, dict, callable validators
    - _Requirements: 19.1, 19.2, 19.3, 19.4, 19.5_

  - [x] 20.2 Write property test for structured output schema injection (Property 34)
    - **Property 34: Structured output schema injection**
    - For any schema passed as result=, Session injects JSON schema instruction; valid model JSON matching schema is parsed into RunResult.data
    - **Validates: Requirements 19.1, 19.2**

- [x] 21. Tool retry with backoff tests
  - [x] 21.1 Implement unit tests for ToolRetryPolicy
    - Test retry up to max_attempts on tool failure
    - Test retryable predicate filtering
    - Test tool-level policy overrides harness-wide default
    - Test final exception returned as ToolResultBlock with is_error=True
    - _Requirements: 23.1, 23.4, 23.5, 23.6_

  - [x] 21.2 Write property test for tool retry backoff formula (Property 35)
    - **Property 35: Tool retry backoff formula**
    - For any ToolRetryPolicy with backoff_base=B, backoff_max=M, jitter=J, delay for attempt A = min(M, B * 2^A) + random(0, J)
    - **Validates: Requirements 23.2, 23.3**

- [x] 22. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 23. Model adapter conformance tests
  - [x] 23.1 Implement conformance tests for Model adapters
    - Test Model ABC defines generate() with expected parameters
    - Test AnthropicModel maps thinking_level to budget_tokens
    - Test OpenAIModel passes thinking_level as reasoning_effort
    - Test MockModel accepts any generate() and returns scripted responses
    - Test ImportError with clear install instruction for missing SDKs
    - Test LiteLLMModel supports model_list routing with fallback
    - _Requirements: 16.1, 16.2, 16.3, 16.4, 16.5, 16.6_

- [x] 24. Human-in-the-loop approval tests
  - [x] 24.1 Implement unit tests for ApprovalGate
    - Test require_approval() pauses execution and requests approval
    - Test denial raises ApprovalDenied
    - Test timeout raises ApprovalTimeout
    - Test granted approval recorded in receipt
    - Test multiple backends: CLI, webhook, event
    - Test GovernancePolicy + ApprovalGate requests approval instead of blocking
    - _Requirements: 17.1, 17.2, 17.3, 17.4, 17.5, 17.6_

- [x] 25. MCP tool server integration tests
  - [x] 25.1 Implement integration tests for MCPClient
    - Test connect_mcp_server(command=...) starts subprocess and discovers tools
    - Test connect_mcp_server(url=...) connects via HTTP
    - Test MCPClient.tools returns Tool objects with correct name, description, input_schema
    - Test model invokes MCP tool and receives result
    - Test client.close() terminates subprocess/HTTP connection
    - Test MCP tools addable to AgentSpec alongside native tools
    - _Requirements: 20.1, 20.2, 20.3, 20.4, 20.5, 20.6_

- [x] 26. Workflow and durable pipelines tests
  - [x] 26.1 Implement unit tests for @workflow decorator
    - Test .run() creates WorkflowRun with status RUNNING
    - Test completion transitions to COMPLETED with output
    - Test exception transitions to FAILED with error message
    - Test RunRegistry persists and retrieves runs by run_id
    - Test WorkflowContext.init(spec) provides WorkflowHarness
    - _Requirements: 21.1, 21.2, 21.3, 21.4, 21.5_

- [x] 27. Zero-dependency core verification
  - [x] 27.1 Implement automated check for zero-dependency constraint
    - Verify pyproject.toml [project].dependencies remains empty list
    - Test lazy imports raise ImportError with correct extra name
    - Test core test suite passes with zero optional extras installed
    - _Requirements: 22.1, 22.2, 22.3, 22.4_

- [x] 28. HTTP serving tests
  - [x] 28.1 Implement integration tests for HTTP server
    - Test POST /sessions/{id}/prompt returns expected response shape
    - Test WS /sessions/{id}/stream for bidirectional streaming
    - Test GET /sessions/{id}/stream for SSE-based streaming
    - Test GET / returns agent health and info
    - Test ImportError with install instructions when serve extra not installed
    - _Requirements: 24.1, 24.2, 24.3, 24.4, 24.5, 24.6_

- [x] 29. Trace viewer UI accessibility tests
  - [x] 29.1 Implement accessibility verification for trace viewer
    - Verify colour contrast ratios meet WCAG 1.4.3 (4.5:1 text, 3:1 large text)
    - Verify keyboard navigation works for all interactive elements (WCAG 2.1.1)
    - Verify visible focus indicators present (WCAG 2.4.7)
    - Verify semantic HTML elements and ARIA attributes (WCAG 4.1.2)
    - Verify colour is not sole means of conveying severity (WCAG 1.4.1)
    - Verify HTML-escaping of dynamic content from tool output (XSS prevention)
    - Verify text alternatives for non-text content (WCAG 1.1.1)
    - _Requirements: 14.1, 14.2, 14.3, 14.4, 14.5, 14.6, 14.7_

- [x] 30. Edge case tests
  - [x] 30.1 Implement edge case tests from Vidura analysis
    - Test EDGE-001: Model returns TOOL_USE with zero tool calls → treated as END_TURN
    - Test EDGE-002: Tool returns empty string → valid ToolResultBlock
    - Test EDGE-003: Concurrent session.task() calls get independent child sessions
    - Test EDGE-004: CompactionPolicy.keep_last > len(messages) → no compaction
    - Test EDGE-005: Budget check between concurrent tools reflects only model tokens
    - Test EDGE-006: GovernancePolicy.set_phase() during in-flight tool → affects next call
    - Test EDGE-007: MAX_TOKENS mid-sentence → RunResult.text is truncated
    - Test EDGE-008: TrustLog file deleted between append and verify_chain
    - Test EDGE-009: resume() with session_id from different AgentSpec
    - Test EDGE-010: Loop trigger() while SUSPENDED raises RuntimeError
    - Test EDGE-011: MCP server crash mid-tool-call returns error ToolResultBlock
    - Test EDGE-012: auto_topology with unreachable nodes → GraphResult.ok=False
    - _Requirements: EDGE-001 through EDGE-012_

- [x] 31. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document
- Unit tests validate specific examples and edge cases
- All PBT uses MockModel — no API keys required
- The project uses pytest with `asyncio_mode = "auto"` and Hypothesis for property-based tests
- Tests must pass on Python 3.10–3.13 per CON-003

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["2.1", "3.1", "4.1", "6.1", "7.1", "8.1"] },
    { "id": 2, "tasks": ["2.2", "2.3", "2.4", "2.5", "3.2", "3.3", "3.4", "4.2", "6.2", "6.3", "7.2", "7.3", "7.4", "7.5", "8.2", "8.3"] },
    { "id": 3, "tasks": ["9.1", "11.1", "12.1", "13.1", "14.1", "15.1"] },
    { "id": 4, "tasks": ["9.2", "9.3", "9.4", "11.2", "11.3", "12.2", "13.2", "13.3", "13.4", "14.2", "15.2", "15.3"] },
    { "id": 5, "tasks": ["17.1", "18.1", "19.1", "20.1", "21.1"] },
    { "id": 6, "tasks": ["17.2", "18.2", "18.3", "19.2", "19.3", "20.2", "21.2"] },
    { "id": 7, "tasks": ["23.1", "24.1", "25.1", "26.1", "27.1", "28.1", "29.1", "30.1"] }
  ]
}
```
