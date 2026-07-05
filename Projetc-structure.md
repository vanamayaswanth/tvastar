backend/
├── pyproject.toml                    # uv workspace root
├── alembic/                          # DB migrations (forward-only)
│   └── versions/
├── core/                             # 🟢 PURE DOMAIN (zero dependencies, zero I/O)
│   ├── __init__.py
│   ├── types.py                      # Algebraic types: LeadStage, ConsentStatus, CallDisposition, etc.
│   ├── models.py                     # Frozen dataclasses: Lead, Call, Tenant, Project, etc.
│   ├── events.py                     # Domain event types (NATS payloads)
│   ├── errors.py                     # Result[T, E] types, domain errors
│   ├── lead/
│   │   ├── state_machine.py          # lead_stage_transition(current, event) → Result
│   │   ├── scoring.py                # score_lead(signals) → LeadScore (pure function)
│   │   └── assignment.py             # round_robin(available, pointer) → Salesperson (pure)
│   ├── call/
│   │   ├── window.py                 # is_within_call_window(ts, config) → bool
│   │   ├── cooling_off.py            # check_cooling_off(phone, last_call_ts) → CoolingOffResult
│   │   └── priority.py              # priority_sort(calls) → sorted list (pure)
│   ├── consent/
│   │   ├── gate.py                   # consent_gate(status) → Allowed | Blocked (pure)
│   │   └── keywords.py              # detect_opt_out(text, lang) → bool
│   ├── billing/
│   │   └── compute.py               # compute_cost(duration_s, rate) → Decimal (pure)
│   └── retry/
│       └── backoff.py                # exponential_backoff(attempt, base, max) → timedelta
│
├── ports/                            # 🟡 INTERFACES (Protocol classes — what the system NEEDS)
│   ├── __init__.py
│   ├── crm.py                        # CRMAdapter protocol (transform_inbound, sync_outcome)
│   ├── whatsapp.py                   # WhatsAppPort protocol (send_template, handle_inbound)
│   ├── telephony.py                  # TelephonyPort protocol (initiate_call, transfer)
│   ├── knowledge.py                  # KnowledgePort protocol (index, search)
│   ├── storage.py                    # StoragePort protocol (upload, get_url, delete)
│   ├── notifications.py             # NotificationPort protocol (push, whatsapp_alert, email)
│   └── cache.py                      # CachePort protocol (get, set, increment, lock)
│
├── adapters/                         # 🔴 IMPLEMENTATIONS (I/O lives HERE, nowhere else)
│   ├── __init__.py
│   ├── postgres/
│   │   ├── models.py                 # SQLAlchemy ORM models
│   │   ├── repos.py                  # LeadRepo, CallRepo, etc. (thin CRUD)
│   │   ├── session.py                # Tenant-scoped session factory (sets RLS context)
│   │   └── migrations.py            # Alembic helpers
│   ├── valkey/
│   │   ├── cache.py                  # CachePort impl (namespaced keys)
│   │   ├── rate_limiter.py          # Sliding window on sorted sets
│   │   └── locks.py                  # Distributed lock impl
│   ├── nats/
│   │   ├── publisher.py              # Tenant-scoped NATS publish
│   │   └── consumers.py             # Event consumers (subscriptions)
│   ├── temporal/
│   │   ├── workflows.py             # LeadWorkflow, RNRRetryWorkflow, CRMSyncWorkflow
│   │   └── activities.py            # Activity functions (boundary calls)
│   ├── livekit/
│   │   ├── dialer.py                 # TelephonyPort impl (SIP calls)
│   │   └── transfer.py              # Warm transfer logic
│   ├── qdrant/
│   │   ├── indexer.py                # KnowledgePort impl (chunk + embed + upsert)
│   │   └── search.py                # Vector search with tenant collection
│   ├── whatsapp/
│   │   ├── cloud_api.py             # WhatsAppPort impl (Cloud API HTTP client)
│   │   └── webhooks.py              # Inbound webhook processing
│   ├── crm/
│   │   ├── salesforce.py            # CRMAdapter impl for Salesforce
│   │   ├── hubspot.py               # CRMAdapter impl for HubSpot
│   │   └── generic_webhook.py       # CRMAdapter impl for generic webhook
│   ├── s3/
│   │   └── storage.py               # StoragePort impl (MinIO/S3)
│   └── email/
│       └── smtp.py                   # Email sending for digests
│
├── api/                              # 🔵 FASTAPI ROUTES (thin — validate, call service, format)
│   ├── __init__.py
│   ├── app.py                        # FastAPI app factory, middleware registration
│   ├── deps.py                       # Dependency injection (get_db, get_tenant_ctx, etc.)
│   ├── middleware/
│   │   ├── tenant.py                 # Extract tenant from JWT, set context
│   │   ├── auth.py                   # JWT validation, RBAC check
│   │   ├── rate_limit.py            # Per-tenant rate limiting
│   │   └── audit.py                  # Auto-audit decorator
│   └── routes/
│       ├── leads.py                  # POST/GET/PATCH leads
│       ├── calls.py                  # Call scheduling, recording access
│       ├── tenants.py                # Tenant CRUD (Super_Admin)
│       ├── projects.py              # Project CRUD (Tenant_Admin)
│       ├── users.py                  # User management
│       ├── auth.py                   # Login, token refresh
│       ├── knowledge.py             # KB upload/list/delete
│       ├── whatsapp.py              # WhatsApp webhook receiver
│       ├── crm_webhook.py           # CRM inbound webhook
│       ├── site_visits.py           # Site visit management
│       ├── notifications.py         # WebSocket gateway
│       ├── billing.py               # Usage, rates
│       └── health.py                # Healthcheck endpoint
│
├── voice_agent/                      # 🎤 SEPARATE PROCESS (LiveKit agent runtime)
│   ├── __init__.py
│   ├── agent.py                      # smolagents conversation loop
│   ├── rag.py                        # Qdrant retrieval during call
│   ├── scoring.py                    # Post-call signal extraction → core/lead/scoring.py
│   ├── persona.py                    # Voice persona configuration
│   └── handlers.py                   # Call start/end, transfer request, opt-out detection
│
├── workers/                          # ⚙️ TEMPORAL WORKERS (run as separate process)
│   ├── __init__.py
│   └── worker.py                     # Register workflows + activities, start worker
│
├── tests/
│   ├── properties/                   # Hypothesis property tests
│   │   ├── test_state_machine.py
│   │   ├── test_cooling_off.py
│   │   ├── test_consent_gate.py
│   │   ├── test_round_robin.py
│   │   ├── test_billing.py
│   │   └── ...
│   ├── integration/                  # Real DB/NATS/Valkey via testcontainers
│   │   ├── test_tenant_isolation.py
│   │   ├── test_lead_workflow.py
│   │   └── ...
│   └── conftest.py                   # Shared fixtures, factories
│
├── Dockerfile
└── docker-compose.yml                # Full local stack








Frontend/
├── package.json
├── next.config.ts
├── tailwind.config.ts
├── tsconfig.json
│
├── src/
│   ├── app/                          # 🔵 NEXT.JS APP ROUTER (routes only)
│   │   ├── layout.tsx                # Root layout (providers, fonts)
│   │   ├── (auth)/                   # Route group: login
│   │   │   └── login/page.tsx
│   │   ├── (dashboard)/              # Route group: authenticated pages
│   │   │   ├── layout.tsx            # Dashboard shell (sidebar, header, tenant branding)
│   │   │   ├── leads/
│   │   │   │   ├── page.tsx          # Lead list
│   │   │   │   └── [id]/page.tsx     # Lead detail
│   │   │   ├── calls/page.tsx
│   │   │   ├── whatsapp/page.tsx
│   │   │   ├── site-visits/page.tsx
│   │   │   ├── analytics/page.tsx
│   │   │   ├── knowledge/page.tsx
│   │   │   ├── settings/
│   │   │   │   ├── projects/page.tsx
│   │   │   │   ├── users/page.tsx
│   │   │   │   └── billing/page.tsx
│   │   │   └── admin/                # Super_Admin only
│   │   │       ├── tenants/page.tsx
│   │   │       └── health/page.tsx
│   │   └── api/                      # Next.js API routes (if needed for BFF)
│   │
│   ├── features/                     # 🟢 FEATURE SLICES (business logic per domain)
│   │   ├── leads/
│   │   │   ├── api.ts                # Lead API calls (SWR hooks)
│   │   │   ├── types.ts              # Lead TypeScript interfaces
│   │   │   ├── components/           # Lead-specific components
│   │   │   │   ├── LeadCard.tsx
│   │   │   │   ├── LeadList.tsx
│   │   │   │   ├── LeadDetail.tsx
│   │   │   │   └── LeadFilters.tsx
│   │   │   └── hooks/
│   │   │       └── useLeadUpdates.ts # WebSocket hook for lead real-time
│   │   ├── calls/
│   │   │   ├── api.ts
│   │   │   ├── types.ts
│   │   │   └── components/
│   │   ├── whatsapp/
│   │   │   ├── api.ts
│   │   │   ├── types.ts
│   │   │   └── components/
│   │   │       ├── ChatThread.tsx
│   │   │       └── MessageComposer.tsx
│   │   ├── notifications/
│   │   │   ├── api.ts
│   │   │   ├── types.ts
│   │   │   ├── hooks/
│   │   │   │   └── useNotifications.ts  # WebSocket notification hook
│   │   │   └── components/
│   │   │       └── NotificationToast.tsx
│   │   ├── analytics/
│   │   │   ├── api.ts
│   │   │   ├── types.ts
│   │   │   └── components/
│   │   ├── auth/
│   │   │   ├── api.ts
│   │   │   ├── types.ts
│   │   │   └── hooks/
│   │   │       └── useAuth.ts
│   │   └── admin/
│   │       ├── api.ts
│   │       ├── types.ts
│   │       └── components/
│   │
│   ├── shared/                       # 🟡 SHARED (cross-feature utilities)
│   │   ├── ui/                       # Atomic UI primitives (shadcn-based)
│   │   │   ├── Button.tsx
│   │   │   ├── Card.tsx
│   │   │   ├── Table.tsx
│   │   │   ├── Badge.tsx
│   │   │   ├── Skeleton.tsx
│   │   │   └── ...
│   │   ├── hooks/
│   │   │   ├── useWebSocket.ts       # Base WebSocket connection hook
│   │   │   └── useTenant.ts          # Tenant context hook
│   │   ├── lib/
│   │   │   ├── api-client.ts         # HTTP client with auth headers
│   │   │   ├── ws-client.ts          # WebSocket client factory
│   │   │   └── format.ts             # Date, currency, phone formatters
│   │   ├── providers/
│   │   │   ├── AuthProvider.tsx
│   │   │   ├── TenantProvider.tsx
│   │   │   └── WebSocketProvider.tsx
│   │   └── types/
│   │       └── common.ts             # Pagination, ApiResponse, etc.
│   │
│   └── config/
│       └── env.ts                    # Environment variables (typed)
│
├── public/
│   └── ...
└── Dockerfile
