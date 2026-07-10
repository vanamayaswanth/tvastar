# Runbook: Store Unreachable

## Trigger Condition

The ConversationWriter emits a `session.degraded` event (published to the `"session.degraded"` topic on the EventBus, or logged as structured JSON to stderr when no EventBus is configured). This means the Store backend failed a write operation and the writer has fallen back to in-memory buffering.

## Severity

**High** — Data is being buffered in memory. If the process restarts before the Store recovers, buffered events will be lost.

## Impact

- Conversation events are not being durably persisted — they exist only in process memory.
- If the process crashes or is restarted, events since the last successful write are permanently lost.
- The Event Log Durability SLO (99.9%) is actively degrading.
- Other Store-dependent operations (checkpoint, circuit breaker state) may also be affected.

## Investigation

1. **Check the degraded event payload** — the event includes `session_id`, `error_message`, `operation`, and `timestamp`. The `error_message` identifies the specific Store failure.
2. **Identify the Store backend** — determine which Store implementation is in use (FileStore, SQLiteStore, InMemoryStore). Check the configuration or startup logs.
3. **For FileStore:** Check disk space (`df -h`), file permissions on the data directory, and whether the filesystem is mounted and responsive.
4. **For SQLiteStore:** Check if the database file is locked by another process, if disk is full, or if the file is corrupted (`sqlite3 <path> "PRAGMA integrity_check"`).
5. **Check for recovery** — look for a subsequent `session.recovered` event. If the Store recovered on its own, determine what caused the transient failure.
6. **Assess blast radius** — check how many sessions are in degraded state. One session degraded is a localized issue; all sessions degraded points to a systemic Store failure.

## Resolution

1. **Restore Store access:**
   - FileStore: free disk space, fix permissions (`chmod`/`chown`), remount filesystem.
   - SQLiteStore: kill competing processes holding the lock, free disk space, or restore from backup if corrupted.
2. **Verify recovery** — once the Store issue is fixed, the next successful `append()` call will trigger a `session.recovered` event automatically. No manual intervention needed on the writer side.
3. **Check for data loss** — if the process was restarted while degraded, in-memory events are lost. Review application logs to estimate the gap and determine if replay is possible from upstream sources.
4. **Prevent recurrence:**
   - Add disk space monitoring with alerts at 80% and 90% thresholds.
   - For SQLiteStore, consider WAL mode if not already enabled.
   - Review whether the Store data path has adequate I/O capacity for the write volume.

## Escalation

- **First responder:** On-call platform engineer — triage within 10 minutes. Data loss risk increases every minute the Store is unreachable.
- **Escalate to:** Infrastructure team — if the issue is disk, filesystem, or host-level.
- **Escalate to:** Team lead — if data loss has occurred (process restarted while degraded) or if more than 5 sessions are simultaneously degraded.
