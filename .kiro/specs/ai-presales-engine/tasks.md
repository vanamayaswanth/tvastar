# Implementation Plan: AI Pre-Sales Execution Engine

## Overview

Build the AI Pre-Sales Execution Engine as a multi-tenant SaaS platform from scratch. The implementation follows dependency order: infrastructure and shared libraries → data layer → core services → integrations → voice pipeline → dashboard → observability. Python (FastAPI) for all backend services, TypeScript (Next.js) for the dashboard.

## Tasks

- [ ] 1. Project scaffolding and shared infrastructure
  - [x] 1.1 Create monorepo structure, dependency management, and shared configuration
    - ✅ COMPLETED — scaffold already exists
    - Backend structure: `backend/core/` (domain logic), `backend/ports/` (abstract interfaces), `backend/adapters/` (postgres, nats, valkey, livekit, whatsapp, s3, temporal, crm), `backend/api/` (FastAPI routes + middleware), `backend/voice_agent/`, `backend/workers/`
    - Frontend structure: `frontend/src/app/` (Next.js app router), `frontend/src/features/` (domain features), `frontend/src/shared/` (UI components, hooks, utils)
    - Docker Compose for local dev (PostgreSQL + pgvector, Valkey, NATS, Temporal, MinIO, LiveKit)
    - _Requirements: 20.1, 20.4_

  - [ ] 1.2 Implement shared algebraic types, enums, and domain models
    - Create `packages/shared/types.py` with all enums: `LeadStage`, `ConsentStatus`, `CallDisposition`, `LeadClassification`
    - Create sum types: `CallOutcome`, `AssignmentResult`, `CoolingOffResult`
    - Create frozen dataclasses: `TenantContext`, `LeadCreatePayload`, `CallSummary`, `LeadScore`
    - _Requirements: 4.1, 6.3, 6.4, 7.1_

  - [ ] 1.3 Implement tenant-aware middleware and dependency injection
    - Create FastAPI middleware that extracts tenant_id from JWT, sets `TenantContext`
    - Create DB session factory that sets `app.current_tenant_id` on connection
    - Create Valkey client wrapper with tenant-namespaced key helper (`t:{tenant_id}:...`)
    - Create NATS publisher helper with tenant-scoped subject (`tenant.{id}.domain.event`)
    - _Requirements: 1.2, 1.6, 20.2, 20.3, 20.4_

  - [ ] 1.4 Implement core pure-function utilities (rate limiter, backoff, call window, state machine)
    - `sliding_window_rate_limiter(key, limit, window_seconds)` using Valkey sorted sets
    - `exponential_backoff(attempt, base_delay, max_delay)` → delay in seconds
    - `is_within_call_window(timestamp, call_window_config)` → bool
    - `lead_stage_transition(current_stage, event)` → Result[LeadStage, Error]
    - `site_visit_status_transition(current, event)` → Result[SiteVisitStatus, Error]
    - _Requirements: 1.4, 3.6, 3.9, 4.1, 4.4, 9.4, 19.8_

  - [ ]* 1.5 Write property tests for core utilities
    - **Property 2: Call Window Enforcement** — `is_within_call_window` returns true iff timestamp in [start, end) in configured tz
    - **Property 4: Sliding Window Rate Limiter** — accepts first `limit` requests, rejects rest within window
    - **Property 5: Exponential Backoff** — delay = d * 2^(n-1) capped at max, strictly monotonic
    - **Property 6: Lead Stage Machine Validity** — valid transitions accepted, invalid rejected
    - **Validates: Requirements 1.4, 3.6, 3.9, 4.1, 4.4, 9.4**

- [ ] 2. Database schema and data layer
  - [ ] 2.1 Create PostgreSQL schema migrations with RLS policies
    - Create all 15 entity tables with `tenant_id` column per ERD
    - Enable RLS on all tenant-scoped tables
    - Create `tenant_isolation` policy on each table: `USING (tenant_id = current_setting('app.current_tenant_id')::uuid)`
    - Create indexes: composite on (tenant_id, project_id), phone_number, stage, created_at
    - Use Alembic for migrations
    - _Requirements: 1.1, 1.2, 20.1, 20.2, 20.3_

  - [ ]* 2.2 Write property test for tenant data isolation
    - **Property 1: Tenant Data Isolation** — query in tenant B context returns zero rows from tenant A
    - **Validates: Requirements 1.2, 1.6, 20.2, 20.3**

  - [ ] 2.3 Implement SQLAlchemy models and repository layer
    - Create SQLAlchemy ORM models for all 15 entities in `backend/adapters/postgres/models.py`
    - Use `fastcrud` for standard CRUD operations — define model → fastcrud auto-generates create/read/update/delete/list with pagination
    - Manual repositories only for complex queries: cross-table joins (lead + calls + site_visits), aggregations (scoring stats, billing rollups), custom filtering logic
    - Specific manual repos: `LeadRepository` (joins + stage transition logic), `CallRecordRepository` (aggregations), `SiteVisitRepository` (status joins), `AssignmentRepository` (round-robin pointer queries)
    - _Requirements: 3.1, 4.1, 20.1_

  - [ ] 2.4 Implement Valkey caching and distributed lock patterns
    - Create `CoolingOffService`: set/check per-phone TTL keys (4h TTL)
    - Create `CallCapacityService`: atomic increment/decrement with max cap
    - Create `RoundRobinPointer`: atomic get-and-increment for fair distribution
    - Create `RateLimitService`: sliding window implementation per tenant
    - _Requirements: 7.7, 5.2, 11.1, 3.9, 20.4_

- [ ] 3. Checkpoint - Ensure infrastructure tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 4. Authentication, RBAC, and audit service
  - [ ] 4.1 Implement authentication with `fastapi-users` and RBAC
    - Use `fastapi-users` v13+ with SQLAlchemy backend and JWT strategy
    - Custom user model extending `SQLAlchemyBaseUserTableUUID` with `tenant_id`, `role`, `project_ids` fields
    - Configure JWT transport (cookie + bearer), lifetime, and refresh token rotation
    - Implement role hierarchy: Super_Admin > Tenant_Admin > Sales_Manager > Salesperson
    - Create permission matrix middleware (role × action × resource) as FastAPI dependency
    - Implement session invalidation on role change (revoke within 60s via Valkey token blocklist)
    - _Requirements: 16.1, 16.2, 16.3, 16.4, 16.5, 16.9_

  - [ ] 4.2 Implement audit logging service
    - Create `AuditLogService` with immutable append-only writes
    - Log: timestamp, actor_id, actor_role, action, resource_type, resource_id, tenant_id, IP
    - Add audit decorator for FastAPI endpoints (auto-log significant actions)
    - Implement 12-month retention policy
    - _Requirements: 16.6, 16.7, 16.8_

- [ ] 5. Lead API and CRM integration
  - [ ] 5.1 Implement lead API routes (CRUD via `fastcrud`, custom logic for transitions)
    - Use `fastcrud` to auto-generate create/read/update/delete/list endpoints from SQLAlchemy Lead model — includes pagination, filtering out of the box
    - Override only where business logic differs:
      - `POST /leads` — add rate limit enforcement (200/min/tenant), publish `lead.created` to NATS after create
      - `PATCH /leads/{id}/stage` — validate state machine transition before update, publish event
    - Standard `GET /leads/{id}`, `GET /leads` with filters (stage, project, date range) handled by fastcrud
    - Return 429 with Retry-After when rate limit exceeded
    - _Requirements: 3.1, 3.3, 3.4, 3.7, 3.8, 3.9, 4.2_

  - [ ]* 5.2 Write property test for lead payload validation
    - **Property 3: Lead Payload Validation** — accepts iff all required fields present and non-empty; rejects with specific field names
    - **Validates: Requirements 3.3, 3.4**

  - [ ] 5.3 Implement crm-adapter service
    - Create webhook endpoint `POST /webhook/{crm_type}` for inbound lead events
    - Implement `CRMAdapter` protocol with `transform_inbound` (field mapping per CRM type)
    - Implement `sync_outcome` — push call outcomes back to CRM within 10s
    - Implement `sync_site_visit` — push site visit status changes to CRM
    - Record failures in `failed_sync` table with retry metadata
    - _Requirements: 3.1, 3.2, 3.5, 3.6, 9.6_

- [ ] 6. Lead workflow engine (Temporal)
  - [ ] 6.1 Implement LeadWorkflow with Temporal
    - Create `LeadWorkflow` orchestrating stages: received → calling → qualified → assigned → callback_tracking → site_visit_booked
    - Activity: `check_consent` — block if pending/revoked, send opt-in WhatsApp if pending
    - Activity: `send_warmup_message` — send WhatsApp warm-up, wait configurable delay (default 5min)
    - Activity: `schedule_call` — check call window, enqueue with priority
    - Activity: `process_call_outcome` — branch on CallOutcome sum type
    - Activity: `assign_lead` — trigger round-robin assignment
    - Activity: `track_callback` — monitor salesperson callback SLA
    - Schedule first call within 2 minutes of lead creation (if within Call_Window)
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 4.10, 17.2, 19.1_

  - [ ] 6.2 Implement RNRRetryWorkflow
    - Create retry workflow with configurable policy (7 retries, increasing intervals)
    - Enforce cooling-off (4h per phone), call window, and max concurrent retry invariant
    - Cancel remaining retries on successful connection
    - Mark lead as `unreachable` after max retries exhausted, notify salesperson
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7_

  - [ ]* 6.3 Write property tests for cooling-off and retry concurrency
    - **Property 10: Cooling-Off Period** — CoolingOffActive for [T, T+4h), CoolingOffClear for ≥T+4h
    - **Property 11: Retry Concurrency Invariant** — at most one pending retry per lead at any time
    - **Validates: Requirements 7.5, 7.6, 7.7**

  - [ ] 6.4 Implement CRMSyncWorkflow
    - Create sync workflow with exponential backoff (max 5 attempts)
    - Publish to dead-letter subject on exhaustion
    - Track sync lag metric for observability
    - _Requirements: 3.5, 3.6_

  - [ ] 6.5 Implement SLA tracking and breach detection
    - Timer-based SLA checks per workflow stage
    - Generate alerts on breach (configurable thresholds per stage)
    - Publish `sla.breached` event to NATS
    - _Requirements: 4.8, 6.8, 10.5, 11.6_

  - [ ]* 6.6 Write property test for SLA breach detection
    - **Property 8: SLA Breach Detection** — returns true iff (current - entry) > threshold
    - **Validates: Requirements 4.8, 6.8, 10.5, 11.6**

- [ ] 7. Checkpoint - Ensure workflow tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 8. Consent, engagement lock, and compliance
  - [ ] 8.1 Implement consent management
    - Create consent record CRUD in lead-api
    - Implement consent hard-gate: block all outbound if status ∈ {pending, revoked}
    - Implement opt-out keyword detection (case-insensitive: STOP, unsubscribe, "don't message", configured-language equivalents)
    - Revoke consent within 5 seconds of opt-out detection
    - Send single opt-in WhatsApp for consent-pending leads
    - _Requirements: 17.1, 17.2, 17.3, 17.4, 8.8_

  - [ ]* 8.2 Write property tests for consent and engagement lock
    - **Property 12: Consent Hard-Gate** — all outbound blocked when consent ∈ {pending, revoked}
    - **Property 13: Opt-Out Keyword Detection** — opt-out keywords trigger revocation
    - **Property 7: Engagement Lock Invariant** — all automated outbound returns blocked when engagement_locked=true
    - **Validates: Requirements 4.6, 8.8, 11.8, 17.2, 17.3**

  - [ ] 8.3 Implement engagement lock logic
    - Set `engagement_locked=true` on lead assignment
    - Guard all automated outbound functions (schedule_call, send_automated_whatsapp, send_warmup)
    - Implement explicit release by salesperson to resume automation
    - _Requirements: 4.6, 11.8_

- [ ] 9. Lead scoring and assignment
  - [ ] 9.1 Implement lead-scorer service
    - Create scoring function: extract signals from `CallSummary` (budget, timeline, location, urgency)
    - Compute `LeadScore` as weighted signal aggregation
    - Classify as Hot/Warm/Cold based on configurable thresholds
    - Auto-adjust thresholds per project after 100 completed calls
    - Trigger immediate notification for Hot_Lead (within 30s)
    - _Requirements: 6.3, 6.4, 6.5_

  - [ ] 9.2 Implement assignment engine with round-robin
    - `assign_round_robin`: skip salespersons with status ∈ {offline, busy, at_capacity}
    - Use Valkey atomic pointer for fair distribution across N salespersons
    - Notify salesperson within 30s of assignment
    - Redistribute unacknowledged leads when salesperson removed from project
    - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5, 11.7_

  - [ ]* 9.3 Write property tests for scoring and assignment
    - **Property 14: Round-Robin Fair Distribution** — N consecutive assignments distribute exactly one to each available salesperson before repeating
    - **Property 9: Priority Queue Ordering** — Hot before Warm before Cold, FIFO within classification
    - **Validates: Requirements 4.9, 11.1, 11.3**

  - [ ] 9.4 Implement priority call queue
    - Priority ordering: Hot_Lead first, then Warm, then Cold; oldest first within class
    - Dequeue when capacity becomes available
    - Integrate with dialer-service scheduling
    - _Requirements: 4.9, 5.2, 19.7_

- [ ] 10. Dialer service and call management
  - [ ] 10.1 Implement dialer-service
    - Create FastAPI app with endpoints: `POST /calls/schedule`, `GET /calls/capacity`
    - Implement call capacity tracking (5 concurrent per tenant, designed to scale to 20)
    - Enforce cooling-off period check before scheduling
    - Enforce call window check before dialing
    - Integrate with LiveKit SIP SDK to initiate outbound calls
    - Store call records with duration, disposition, recording path
    - _Requirements: 5.1, 5.2, 5.6, 7.7, 4.4_

  - [ ] 10.2 Implement inbound call handling
    - Log inbound calls with timestamp, duration, caller phone
    - Match caller phone to existing Lead records
    - Create opportunity note on matched lead, or unmatched record + notify Sales_Manager
    - No AI conversation on inbound (log only)
    - _Requirements: 12.1, 12.2, 12.3, 12.4, 12.5, 12.6_

  - [ ] 10.3 Implement call recording storage and retention
    - Upload recordings to S3/MinIO with tenant-prefixed paths (`/{tenant_id}/recordings/{call_id}.wav`)
    - Set 90-day TTL via lifecycle rules or scheduled cleanup job
    - Track `recording_expires_at` on call record
    - _Requirements: 5.6, 5.7, 20.6_

- [ ] 11. Checkpoint - Ensure core services pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 12. Voice pipeline (AI agent)
  - [ ] 12.1 Implement voice-agent service with LiveKit Agents SDK
    - Create LiveKit agent that connects to SIP call sessions
    - Integrate Parakeet STT for speech-to-text streaming
    - Integrate Qwen3-TTS for text-to-speech output
    - Target < 1500ms voice response latency (p95)
    - Identify self by persona name and state call purpose within 10s
    - Disclose AI-powered call and recording within first 15s
    - _Requirements: 5.1, 5.4, 5.5, 5.8, 17.8, 17.9_

  - [ ] 12.2 Implement smolagents conversation loop with RAG
    - Create smolagents-based conversation agent
    - Connect to pgvector (PostgreSQL) for project-specific KB retrieval (tenant-isolated via RLS)
    - Implement qualification conversation flow (budget, timeline, location, urgency probing)
    - Handle knowledge gaps gracefully (acknowledge, offer salesperson follow-up)
    - Support English + one configured Indian language per project
    - _Requirements: 5.3, 5.5, 6.1, 6.2, 6.7, 6.9_

  - [ ] 12.3 Implement call outcome processing and summary generation
    - Generate structured `CallSummary` on call end (qualification outcome, topics, next action)
    - Publish `call.completed` event to NATS with outcome
    - Handle opt-out during call: acknowledge, end politely, revoke consent within 5s
    - Capture site visit intent and preferred date from conversation
    - _Requirements: 5.11, 9.1, 9.2, 17.4_

  - [ ] 12.4 Implement warm transfer and callback
    - Detect human assistance request during call
    - If within Call_Window + warm transfer enabled: transfer to available salesperson within 30s
    - Provide context summary to receiving salesperson before connecting
    - If no salesperson available or outside hours: create priority Callback_Notification
    - _Requirements: 5.9, 5.10, 10.1, 10.2, 10.3, 10.4, 10.6, 10.7, 10.8, 11.8_

- [ ] 13. Knowledge service
  - [ ] 13.1 Implement knowledge-service for KB management
    - Create endpoints: `POST /knowledge/upload`, `GET /knowledge/documents`, `DELETE /knowledge/{id}`
    - Chunk uploaded documents (PDF, DOCX) and index in PostgreSQL via pgvector (tenant-isolated via RLS, zero extra service)
    - Make content available within 60 seconds of upload (no approval workflow)
    - Track `last_updated` per document, surface staleness warning (>7 days)
    - Track knowledge gaps (unanswered questions) for Tenant_Admin reporting
    - _Requirements: 6.1, 6.6, 6.8, 6.9, 20.5_

- [ ] 14. WhatsApp service
  - [ ] 14.1 Implement whatsapp-service
    - Integrate with WhatsApp Cloud API
    - Send template messages (warm-up, brochure, site visit confirmation) from project-configured number
    - Handle inbound webhooks: route simple replies to AI response, complex to human queue
    - Implement opt-out keyword detection on inbound messages
    - Retry failed deliveries (3x exponential backoff)
    - _Requirements: 4.10, 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 8.8_

- [ ] 15. Notification service
  - [ ] 15.1 Implement notification-service (multi-channel)
    - WebSocket push for real-time in-app notifications (hot lead alerts within 5s)
    - WhatsApp notification to salesperson (prospect name, project, score, summary, click-to-call)
    - Email daily digest for Sales_Managers (lead activity, call outcomes, SLA breaches)
    - Track delivery and read status for audit
    - Configure channels per role
    - _Requirements: 13.1, 13.2, 13.3, 13.4, 13.5, 13.6, 13.7, 6.5_

- [ ] 16. Checkpoint - Ensure integrations pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 17. Billing and usage tracking
  - [ ] 17.1 Implement billing-service
    - Track call duration (seconds) for every call, associated with tenant/project
    - Compute cost: `ceil(duration_seconds / 60) * rate_per_minute`
    - Super_Admin endpoint to set/update per-tenant billing rate
    - Usage summary view: per-tenant, per-project call minutes and costs
    - Retain usage records 12 months minimum
    - _Requirements: 15.1, 15.2, 15.3, 15.4, 15.5, 15.6_

  - [ ]* 17.2 Write property test for billing computation
    - **Property 15: Billing Cost Computation** — total cost = sum(ceil(duration/60) * rate) per record; connect rate = completed/total attempts
    - **Validates: Requirements 14.1, 14.3, 15.3**

- [ ] 18. Tenant and project management API
  - [ ] 18.1 Implement tenant and project CRUD endpoints
    - Super_Admin: create/update/deactivate tenants (halt outbound within 60s on deactivate)
    - Tenant_Admin: create/update projects (call window, language, assignment mode, handoff mode, voice persona, retry policy, score thresholds)
    - Apply project config changes to subsequent operations only (not in-progress calls)
    - _Requirements: 1.1, 1.3, 1.4, 1.5, 1.7_

  - [ ] 18.2 Implement white-label configuration
    - Store logo, color scheme, custom domain per tenant
    - Configure caller ID and WhatsApp number per project
    - Configure voice persona (name, language, tone) per project
    - Ensure no branding cross-contamination between tenants
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6_

- [ ] 19. Site visit tracking
  - [ ] 19.1 Implement site visit management
    - Create site visit record on intent capture (from AI call or manual)
    - Track status transitions: intent_captured → confirmed → completed | no_show
    - Notify assigned salesperson on booking
    - Sync status changes to CRM within 10s
    - No slot/availability management (capture intent and preferred date only)
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6_

- [ ] 20. Dashboard (Next.js)
  - [ ] 20.1 Implement dashboard authentication and tenant-branded layout
    - Custom JWT auth hook (no next-auth) — store/refresh tokens, decode tenant_id/role from JWT
    - Tenant-branded shell using `shadcn/ui` layout components (Sidebar, Sheet, Avatar)
    - Role-based navigation with `zustand` for sidebar/modal UI state
    - Use `@tanstack/react-query` v5 for all server state (auth status, user profile)
    - _Requirements: 2.1, 2.2, 16.1, 16.3_

  - [ ] 20.2 Implement lead management views
    - Lead list using `@tanstack/react-table` with sorting, filtering, pagination
    - Filter forms with `react-hook-form` + `zod` validation (stage, project, date range, classification)
    - Lead detail view using `shadcn/ui` Card, Badge, Tabs components
    - Data fetching via `@tanstack/react-query` v5 (useQuery, useMutation, optimistic updates)
    - Salesperson availability toggle (available/busy/offline)
    - _Requirements: 13.1, 10.7, 13.5_

  - [ ] 20.3 Implement real-time WebSocket integration
    - Native WebSocket hook (~30 lines, no socket.io) — connect, reconnect, parse JSON messages
    - Live updates via `@tanstack/react-query` cache invalidation on WS events
    - Hot lead toast notifications using `sonner` with click-to-action
    - _Requirements: 13.2, 13.5_

  - [ ] 20.4 Implement WhatsApp conversation view
    - Display WhatsApp threads per lead using `@tanstack/react-table` for message list
    - Clear AI-handled vs human-handled message indicators using `shadcn/ui` Badge
    - Human reply composer with `react-hook-form` + `zod` validation
    - `@tanstack/react-query` for message fetching with infinite scroll
    - _Requirements: 8.5, 8.6_

  - [ ] 20.5 Implement reporting and analytics views
    - `recharts` for all analytics charts (cost-per-lead, connect rate trends, project comparison)
    - Cost-per-lead and cost-per-site-visit metrics per project
    - Connect rate trends over configurable time periods
    - AI quality score display using `shadcn/ui` Card and Badge
    - Knowledge gaps report for Tenant_Admin
    - Data fetched via `@tanstack/react-query` with staleTime for expensive aggregation queries
    - _Requirements: 14.1, 14.2, 14.3, 14.4, 14.5, 6.8, 6.9_

  - [ ] 20.6 Implement Super_Admin views
    - Tenant management using `@tanstack/react-table` (create, deactivate, billing rate)
    - Settings forms with `react-hook-form` + `zod` for tenant/billing configuration
    - Per-tenant health view using `recharts` (active workflows, error rates, SLA compliance)
    - Usage summary (call minutes, costs per tenant/project) with `shadcn/ui` Table, Card, Skeleton
    - DLQ depth metrics
    - _Requirements: 1.1, 1.7, 15.4, 18.5_

- [ ] 21. Checkpoint - Ensure dashboard builds and renders
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 22. Observability and alerting
  - [ ] 22.1 Implement OpenTelemetry instrumentation across all services
    - Use `opentelemetry-instrumentation-fastapi` for automatic span creation on all routes
    - Use `sentry-sdk[fastapi]` for error tracking with FastAPI integration (auto-captures unhandled exceptions, request context)
    - Add correlation IDs to all external service interactions via OTel context propagation
    - Expose Prometheus metrics: active calls, queue depth, API latency, workflow failure rate, CRM sync lag, WhatsApp delivery rate
    - _Requirements: 18.1, 18.2, 18.4, 18.6_

  - [ ] 22.2 Implement alerting rules and capacity monitoring
    - Configure Grafana alerts: call failure rate >10%, CRM sync lag >60s, API p95 >2s
    - Capacity warning at 80% utilization
    - Per-tenant health aggregation for Super_Admin dashboard
    - _Requirements: 18.3, 18.5, 18.7_

  - [ ] 22.3 Configure Metabase for analytics
    - Connect Metabase to PostgreSQL read replica / direct SQL
    - Create base dashboards for cost-per-lead, connect rate, AI quality
    - _Requirements: 14.6, 14.7_

- [ ] 23. Data residency, encryption, and compliance finalization
  - [ ] 23.1 Implement encryption and data residency controls
    - Ensure TLS 1.2+ for all data in transit
    - Enable AES-256 encryption at rest (PostgreSQL, S3/MinIO, Valkey)
    - Configure data residency in India (storage region config)
    - Implement tenant data deletion workflow (remove all within 30 days, preserve anonymized billing)
    - Implement prospect data deletion (remove PII within 30 days, retain anonymized audit/billing)
    - _Requirements: 17.5, 17.6, 17.7, 20.7, 20.8_

  - [ ] 23.2 Implement NDNC registry compliance
    - Integrate with TRAI NDNC registry provider (API or bulk download)
    - Maintain local cache (PostgreSQL table) of NDNC numbers, refreshed daily (scheduled Temporal workflow)
    - Add pre-call NDNC check in dialer-service: block call if number is NDNC-registered
    - Mark Lead as `ndnc_blocked` if number found in registry
    - Display NDNC status in Dashboard (Tenant_Admin view)
    - Log all NDNC checks (pass/fail) in Audit_Log
    - Handle registry unavailability: queue call (don't skip check), alert Super_Admin
    - _Requirements: 21.1, 21.2, 21.3, 21.4, 21.5, 21.6_

- [ ] 24. Final checkpoint - Full integration validation
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional property-based test tasks and can be skipped for faster MVP
- Each task references specific requirement clauses for traceability
- Checkpoints at tasks 3, 7, 11, 16, 21, and 24 ensure incremental validation
- Property tests use Hypothesis (Python) with minimum 100 iterations per property, 200 for critical paths
- The design uses pointfree compositional style: pure functions at the core, side effects at edges via adapters
- Libraries: `fastapi-users` handles auth, `fastcrud` handles CRUD, `shadcn/ui` provides all UI components, `@tanstack/react-query` for server state, `@tanstack/react-table` for data tables
- Frontend folder renamed from `dashboard/` to `frontend/`
- All services run as modules within single FastAPI app (except `voice_agent` and `workers`)
- Docker Compose provides the full local dev stack; no cloud dependencies for development

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2", "1.3"] },
    { "id": 2, "tasks": ["1.4", "2.1"] },
    { "id": 3, "tasks": ["1.5", "2.2", "2.3", "2.4"] },
    { "id": 4, "tasks": ["4.1", "4.2"] },
    { "id": 5, "tasks": ["5.1", "5.3"] },
    { "id": 6, "tasks": ["5.2", "6.1", "6.2"] },
    { "id": 7, "tasks": ["6.3", "6.4", "6.5"] },
    { "id": 8, "tasks": ["6.6", "8.1"] },
    { "id": 9, "tasks": ["8.2", "8.3", "9.1", "9.2"] },
    { "id": 10, "tasks": ["9.3", "9.4", "10.1"] },
    { "id": 11, "tasks": ["10.2", "10.3", "12.1"] },
    { "id": 12, "tasks": ["12.2", "12.3"] },
    { "id": 13, "tasks": ["12.4", "13.1", "14.1"] },
    { "id": 14, "tasks": ["15.1", "17.1"] },
    { "id": 15, "tasks": ["17.2", "18.1", "18.2"] },
    { "id": 16, "tasks": ["19.1", "20.1"] },
    { "id": 17, "tasks": ["20.2", "20.3", "20.4"] },
    { "id": 18, "tasks": ["20.5", "20.6"] },
    { "id": 19, "tasks": ["22.1", "22.2", "22.3"] },
    { "id": 20, "tasks": ["23.1"] }
  ]
}
```
