# Runbook: Handoff Triggered

## Trigger Condition

The Loop has exhausted its retry budget (all `max_iterations` attempts failed) or encountered a permanent failure (AUTH_ERROR, CONTENT_POLICY), and has escalated to the HandoffPolicy. The loop is now in HANDOFF state — meaning an operator must intervene for this agent to resume.

## Severity

**Medium** — The system is working as designed (it detected a failure and escalated). However, the underlying agent is stuck and will not make progress until an operator acts.

## Impact

- The affected loop run has stopped executing. No further iterations will be attempted.
- Users depending on this loop's output will not receive updates until the issue is resolved and the loop is restarted.
- If the handoff delivery itself fails (all 3 delivery attempts exhausted), a fallback file is written to `{store_data_path}/handoff_fallback/{run_id}.json` and the state transitions to HANDOFF_FAILED.

## Investigation

1. **Read the handoff notification** — the HandoffPolicy's `escalate()` call includes the loop name, run ID, failure kind, and error message. Start here.
2. **Check the failure kind:**
   - `AUTH_ERROR` → API credentials are invalid or revoked. No amount of retry will fix this.
   - `CONTENT_POLICY` → The input triggered the model provider's content filter. The prompt or conversation history contains flagged content.
   - `MODEL_ERROR` → Transient errors exhausted the retry budget. The provider may be degraded.
   - `TIMEOUT` → The model consistently took too long to respond.
3. **Review the run history** — check previous iterations for the same run. How many retries occurred? Did errors change between attempts (suggesting an intermittent issue) or stay identical (suggesting a persistent problem)?
4. **Check if a fallback file exists** — look in `{store_data_path}/handoff_fallback/` for a file named `{run_id}.json`. If present, handoff delivery also failed — meaning the operator notification didn't go through.
5. **Assess urgency** — is this a one-off failure or are multiple loops handing off simultaneously? Multiple concurrent handoffs suggest a systemic provider issue.

## Resolution

### For AUTH_ERROR:
1. Verify the API key is valid — check the provider dashboard for key status.
2. Rotate the key if expired or revoked.
3. Update the configuration and restart the loop.

### For CONTENT_POLICY:
1. Review the conversation history that triggered the filter.
2. If the input is legitimate, consider configuring a `fallback_model` in LoopConfig that has different content policies.
3. If the input is genuinely problematic, clean the conversation history before restarting.

### For MODEL_ERROR / TIMEOUT (budget exhausted):
1. Check the model provider's status page for known outages.
2. If the provider is healthy, inspect the specific error messages from each retry attempt.
3. Consider increasing `max_iterations` or `cancel_after` if the failure was close to recovering.
4. Restart the loop once the underlying issue is resolved.

### After resolution:
1. Reset the loop state — the loop will pick up on its next scheduled tick or can be manually triggered.
2. If handoff delivery also failed, acknowledge the fallback file to prevent stale alerts.

## Escalation

- **First responder:** On-call platform engineer — acknowledge within 15 minutes, begin investigation.
- **Escalate to:** Team lead — if the same loop hands off repeatedly (indicating a persistent misconfiguration), or if more than 3 loops hand off within the same hour.
- **Escalate to:** Security team — if AUTH_ERROR is caused by unauthorized key revocation or suspected credential compromise.
