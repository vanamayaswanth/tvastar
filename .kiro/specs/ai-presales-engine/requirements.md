# Requirements Document

## Introduction

This document defines the requirements for an AI-powered real estate pre-sales execution engine, delivered as a multi-tenant white-label SaaS platform. The system receives leads from client CRMs, qualifies prospects via AI voice calling, manages follow-ups through WhatsApp, tracks leads through qualification to site visit booking, and provides dashboards and analytics. The platform serves multiple real estate developer clients with full tenant isolation, configurable branding, and per-minute billing.

## Glossary

- **Platform**: The AI Pre-Sales Execution Engine SaaS application in its entirety
- **Tenant**: A real estate developer client organization using the Platform
- **Project**: A specific real estate development (building, township, etc.) belonging to a Tenant
- **Prospect**: A potential property buyer whose lead data is received from a Tenant's CRM
- **Lead**: A record representing a Prospect's interest in a specific Project, deep-copied from CRM
- **AI_Voice_Agent**: The automated voice calling subsystem that conducts qualification calls with Prospects
- **Salesperson**: A Tenant's sales team member who handles qualified leads and site visits
- **Sales_Manager**: A Tenant's management user who oversees Salesperson performance and reviews reports
- **Tenant_Admin**: A Tenant's administrator who configures projects, knowledge bases, templates, and rules
- **Super_Admin**: A Platform team member who manages all Tenants, monitors operations, and sets billing rates
- **CRM_System**: An external Customer Relationship Management system operated by a Tenant
- **Integration_Layer**: The subsystem that connects to external CRM systems and performs deep copy of lead data
- **Knowledge_Base**: Project-specific information (brochures, pricing, amenities, location details) used by the AI for conversations
- **RNR**: Ring No Response — when a call is not answered by the Prospect
- **Warm_Transfer**: Live handoff of an active call from AI_Voice_Agent to a Salesperson
- **Callback_Notification**: Alert sent to a Salesperson to call back a qualified Prospect
- **Call_Window**: The permitted time range during which outbound calls may be placed
- **Lead_Score**: An AI-generated numerical assessment of a Prospect's purchase intent based on conversation signals
- **Hot_Lead**: A Lead with high purchase intent requiring immediate Salesperson engagement
- **WhatsApp_System**: The WhatsApp Business API (Cloud API or BSP) used for messaging
- **Site_Visit**: A Prospect's expressed intent to physically visit a Project location
- **Round_Robin**: A lead assignment algorithm that distributes leads evenly among Salespersons within a Project
- **SLA**: Service Level Agreement — time-bound expectations for response or action
- **RAG**: Retrieval-Augmented Generation — technique to ground AI responses in project-specific knowledge
- **Tenant_Isolation**: Row-level data separation ensuring one Tenant cannot access another Tenant's data
- **White_Label**: Customizable branding (logo, colors, domain, caller ID, WhatsApp number, voice persona) per Tenant
- **Dashboard**: The web-based user interface for managing and monitoring the Platform
- **Consent**: Explicit permission from a Prospect to receive communications
- **Audit_Log**: An immutable record of significant actions performed within the Platform
- **Engagement_Lock**: A state in which automated AI outbound actions (calls and messages) are suspended for a Lead that has been assigned to a Salesperson, preventing the AI from contacting a Prospect who is already in human follow-up
- **Cooling_Off_Period**: A minimum time interval that must elapse before the same phone number can be called again, preventing spam-like call patterns
- **NDNC**: National Do Not Call — India's telecom registry (maintained by TRAI) of phone numbers that have opted out of telemarketing calls. Calling NDNC-registered numbers without consent is a regulatory violation.
- **Warm_Up_Message**: A WhatsApp message sent to a Prospect before the first AI call, introducing the service and setting expectations

## Requirements

### Requirement 1: Tenant & Project Management

**User Story:** As a Super_Admin, I want to onboard new Tenants and configure their Projects, so that each real estate developer client can operate independently on the Platform.

#### Acceptance Criteria

1. WHEN a Super_Admin creates a new Tenant, THE Platform SHALL provision a Tenant record with name, contact details, billing rate, and white-label configuration
2. WHEN a Tenant is created, THE Platform SHALL enforce row-level isolation for all data belonging to that Tenant
3. WHEN a Tenant_Admin creates a new Project, THE Platform SHALL associate the Project with the Tenant and initialize project-specific configuration (call window, language, assignment rules, handoff mode)
4. THE Platform SHALL support configuring a Call_Window per Tenant and per Project, defaulting to 10:00–20:00 IST
5. WHEN a Tenant_Admin updates Project configuration, THE Platform SHALL apply changes to all subsequent operations without affecting in-progress calls
6. THE Platform SHALL enforce that each Tenant_Admin can only view and manage Projects belonging to their own Tenant
7. WHEN a Super_Admin deactivates a Tenant, THE Platform SHALL halt all outbound calls and messages for that Tenant within 60 seconds

### Requirement 2: White-Label Engine

**User Story:** As a Tenant_Admin, I want to customize the Platform's branding for my organization, so that my Prospects and team experience a branded interface and communications.

#### Acceptance Criteria

1. WHEN a Tenant is onboarded, THE Platform SHALL allow configuration of logo, color scheme, and custom domain for the Dashboard
2. THE Platform SHALL serve the Dashboard under the Tenant's custom domain with the Tenant's branding applied
3. WHEN a Tenant_Admin configures a caller ID for a Project, THE AI_Voice_Agent SHALL use that caller ID for all outbound calls to Prospects of that Project
4. WHEN a Tenant_Admin configures a WhatsApp number for a Project, THE WhatsApp_System SHALL send all messages from that configured number
5. WHEN a Tenant_Admin configures a voice persona for a Project, THE AI_Voice_Agent SHALL use that persona (name, language, tone) during calls for that Project
6. THE Platform SHALL ensure that no Tenant's branding elements are visible to users of another Tenant

### Requirement 3: CRM Integration Layer

**User Story:** As a Tenant_Admin, I want the Platform to receive leads from my CRM automatically, so that the AI can begin qualification without manual data entry.

#### Acceptance Criteria

1. WHEN the CRM_System sends a new lead via the Integration_Layer, THE Platform SHALL create a deep copy of the lead data in the Platform's database within 5 seconds
2. THE Integration_Layer SHALL support an adapter pattern allowing connection to multiple CRM platforms per Tenant
3. WHEN a lead is received, THE Integration_Layer SHALL validate that all required fields (name, phone number, project interest) are present before creating the Lead record
4. IF a lead is received with missing required fields, THEN THE Integration_Layer SHALL reject the lead and log the validation error with the field names that failed
5. WHEN call outcomes are generated, THE Platform SHALL publish the outcome event to NATS JetStream and sync the outcome back to the CRM_System within 10 seconds of call completion
6. IF the CRM sync fails, THEN THE Platform SHALL record the failure in a failed_sync table and retry using exponential backoff via Temporal workflows
7. THE Integration_Layer SHALL include the Tenant identifier in all lead records to maintain Tenant_Isolation
8. WHEN the Integration_Layer receives a lead, THE Platform SHALL NOT perform deduplication (deduplication is the CRM_System's responsibility)
9. THE Platform SHALL enforce per-Tenant rate limits on lead ingestion (configurable, default: 200 leads per minute). IF the rate limit is exceeded, THEN THE Platform SHALL queue excess leads and process them at the configured rate, returning a 429 status to the CRM_System for leads exceeding the burst buffer

### Requirement 4: Lead Workflow Engine

**User Story:** As a Tenant_Admin, I want leads to progress through a defined workflow automatically, so that each Prospect is engaged at the right time with the right action.

#### Acceptance Criteria

1. WHEN a new Lead is created, THE Platform SHALL initiate a Temporal workflow that progresses the Lead through stages: received → calling → qualified → assigned → callback_tracking → site_visit_booked
2. WHEN a new Lead is created, THE Platform SHALL publish a lead.created event to NATS JetStream, which triggers the Temporal workflow and any other subscribed consumers
3. WHEN a Lead is created, THE Platform SHALL schedule the first outbound call within 2 minutes, provided the current time is within the configured Call_Window
4. WHILE the current time is outside the configured Call_Window, THE Platform SHALL NOT place outbound calls and SHALL queue them for the next Call_Window opening
5. WHEN a Lead reaches the qualified stage, THE Platform SHALL trigger the lead assignment process
6. WHEN a Lead reaches the site_visit_booked stage, THE Platform SHALL update the CRM_System and cease automated outbound actions for that Lead
7. IF a Temporal workflow fails, THEN THE Platform SHALL retry the failed activity with exponential backoff up to 5 attempts before marking the Lead as requiring_manual_review
8. THE Platform SHALL track SLA timers for each workflow stage and generate alerts when SLA thresholds are breached
9. WHEN all concurrent call slots are occupied, THE Platform SHALL queue pending calls in priority order (Hot_Lead first, then by lead age) and execute them within the configured Call_Window as slots free up
10. BEFORE scheduling the first outbound call to a Lead, THE Platform SHALL send a WhatsApp warm-up message (e.g., 'Hi [Name], thanks for your interest in [Project]. Our team will call you shortly.') and wait a configurable delay (default 5 minutes) before initiating the AI call. WHERE the Lead has no WhatsApp-reachable number, THE Platform SHALL proceed to call directly

### Requirement 5: AI Voice Calling

**User Story:** As a Tenant_Admin, I want the AI to call Prospects and qualify their purchase intent, so that Salespersons only spend time on genuinely interested buyers.

#### Acceptance Criteria

1. WHEN a call is scheduled, THE AI_Voice_Agent SHALL initiate an outbound call via LiveKit SIP to the Prospect's phone number using the Project's configured caller ID
2. THE AI_Voice_Agent SHALL support up to 5 concurrent calls per Tenant (initial capacity), designed to scale to 20 or more per Tenant without major architecture changes
3. WHEN a call is connected, THE AI_Voice_Agent SHALL conduct a qualification conversation using RAG over the Project's Knowledge_Base
4. THE AI_Voice_Agent SHALL maintain voice response latency (measured from end of Prospect speech to start of AI speech output) below 1500 milliseconds at the 95th percentile under normal load (up to 5 concurrent calls per Tenant)
5. THE AI_Voice_Agent SHALL support conversations in English and one configured Indian language per Project
6. WHEN a call is completed, THE Platform SHALL generate a call recording and store it in S3-compatible storage
7. THE Platform SHALL retain call recordings for 90 days from the call date, then delete them automatically
8. WHEN a Prospect answers, THE AI_Voice_Agent SHALL identify itself by the configured persona name and state the purpose of the call within the first 10 seconds
9. WHEN a Prospect requests to speak to a human during business hours and warm transfer is configured, THE AI_Voice_Agent SHALL initiate a Warm_Transfer to an available Salesperson
10. WHEN a Prospect requests to speak to a human outside business hours or when warm transfer is not available, THE Platform SHALL create a Callback_Notification for the assigned Salesperson
11. WHEN a call ends, THE Platform SHALL generate a structured call summary including qualification outcome, key topics discussed, and next action
12. BEFORE initiating any outbound call, THE AI_Voice_Agent SHALL verify that the Lead's consent status is GRANTED and the phone number is not NDNC-blocked (cross-ref: Requirements 17.2, 17.3, 21.1, 21.2)

### Requirement 6: Conversation AI & Lead Scoring

**User Story:** As a Tenant_Admin, I want the AI to use project-specific knowledge during calls and score leads based on conversation signals, so that lead quality is assessed consistently and accurately.

#### Acceptance Criteria

1. WHEN a Tenant_Admin uploads Knowledge_Base content (brochures, pricing, amenities, FAQs), THE Platform SHALL index the content using pgvector in PostgreSQL for RAG retrieval within 60 seconds
2. WHEN the AI_Voice_Agent conducts a call, THE AI_Voice_Agent SHALL use smolagents with pgvector retrieval to access relevant Knowledge_Base content and answer Prospect questions accurately
3. WHEN a call is completed, THE Platform SHALL generate a Lead_Score based on conversation signals (budget mention, timeline, location preference, urgency indicators)
4. THE Platform SHALL classify Leads as Hot_Lead, warm, or cold based on Lead_Score thresholds. THE Platform SHALL provide platform-calibrated default thresholds that auto-adjust per Project after 100 completed calls, with Tenant_Admin override capability
5. WHEN a Lead is classified as Hot_Lead, THE Platform SHALL trigger immediate Salesperson notification within 30 seconds of call completion
6. WHEN a Tenant_Admin uploads new Knowledge_Base content, THE Platform SHALL make the content available to the AI_Voice_Agent without requiring approval workflow (admin uploads are live immediately)
7. IF the AI_Voice_Agent cannot answer a Prospect's question from the Knowledge_Base, THEN THE AI_Voice_Agent SHALL acknowledge the gap and offer to have a Salesperson follow up with the answer
8. THE Platform SHALL track the last-updated timestamp for each Knowledge_Base document. WHEN a document has not been updated for more than 7 days, THE Platform SHALL display a staleness warning to the Tenant_Admin in the Dashboard
9. THE Platform SHALL track occurrences where the AI_Voice_Agent cannot answer a Prospect question from the Knowledge_Base and surface these gaps to Tenant_Admin as a 'Knowledge Gaps' report

### Requirement 7: RNR Retry Automation

**User Story:** As a Tenant_Admin, I want the system to automatically retry calls to Prospects who did not answer, so that no lead is lost due to timing.

#### Acceptance Criteria

1. WHEN an outbound call results in RNR (Ring No Response), THE Platform SHALL schedule a retry call according to the configured retry policy
2. THE Platform SHALL support configurable retry policies per Project including: maximum retry count, delay between retries, and time-of-day preferences. THE Platform SHALL provide smart defaults (7 retries over 10 days with increasing intervals: 2hr, 4hr, 8hr, next-day, 2-days, 3-days, 4-days within Call_Window) that apply unless explicitly overridden by Tenant_Admin
3. WHEN a call reaches voicemail, THE Platform SHALL mark the call as RNR and NOT leave a voicemail message
4. WHEN the maximum retry count is exhausted without a successful connection, THE Platform SHALL mark the Lead as unreachable and notify the assigned Salesperson
5. WHILE a Lead has pending retries, THE Platform SHALL NOT schedule additional retries for the same Lead concurrently
6. WHEN a retry call connects successfully, THE Platform SHALL cancel all remaining scheduled retries for that Lead
7. THE Platform SHALL enforce a per-phone-number cooling-off period: the same phone number SHALL NOT be called more than once within a 4-hour window, regardless of how many Lead records reference that number across the same Project
8. IF a Lead's consent status is revoked while retries are pending, THEN THE Platform SHALL cancel all scheduled retries for that Lead immediately (cross-ref: Requirement 17.3)

### Requirement 8: WhatsApp Communication

**User Story:** As a Tenant_Admin, I want the system to send WhatsApp follow-ups to Prospects and handle their replies, so that engagement continues beyond phone calls.

#### Acceptance Criteria

1. WHEN a qualification call is completed successfully, THE WhatsApp_System SHALL send the Project brochure to the Prospect via WhatsApp within 60 seconds
2. WHEN a site visit is booked, THE WhatsApp_System SHALL send a confirmation message with location details to the Prospect
3. THE WhatsApp_System SHALL use the Project's configured WhatsApp number as the sender for all outbound messages
4. WHEN a Prospect sends a simple reply (acknowledgment, thank you, basic question from Knowledge_Base), THE Platform SHALL generate and send an AI-powered response via WhatsApp
5. WHEN a Prospect sends a complex reply (negotiation, complaint, detailed query not in Knowledge_Base), THE Platform SHALL route the message to the Dashboard for human response by the assigned Salesperson
6. THE Platform SHALL display all WhatsApp conversations in the Dashboard with clear indicators of AI-handled versus human-handled messages
7. WHEN a WhatsApp message fails to deliver, THE Platform SHALL retry delivery up to 3 times with exponential backoff and log the failure if all retries are exhausted
8. WHEN a Prospect sends a message containing opt-out keywords (STOP, unsubscribe, don't message, or equivalent in configured language), THE Platform SHALL immediately revoke consent for WhatsApp communication and update the Lead's consent status within 5 seconds

### Requirement 9: Site Visit Capture & Tracking

**User Story:** As a Salesperson, I want to capture a Prospect's intent to visit a project site with their preferred date, so that I can follow up and confirm the visit.

#### Acceptance Criteria

1. WHEN a Prospect expresses site visit interest during an AI call, THE AI_Voice_Agent SHALL capture the intent and preferred date from the conversation
2. WHEN site visit intent is captured, THE Platform SHALL create a site visit record associated with the Lead, including preferred date and any stated preferences
3. WHEN a site visit is booked, THE Platform SHALL notify the assigned Salesperson via the Dashboard and applicable notification channels
4. THE Platform SHALL track site visit status as: intent_captured → confirmed → completed → no_show
5. THE Platform SHALL NOT manage site visit time slots or availability (the system captures intent and preferred date only)
6. WHEN a site visit status changes, THE Platform SHALL sync the updated status to the CRM_System within 10 seconds

### Requirement 10: Human Handoff

**User Story:** As a Prospect, I want to speak to a real person when I need to, so that my complex questions or concerns are addressed by a human.

#### Acceptance Criteria

1. WHEN a Prospect requests human assistance during a call within the Call_Window and warm transfer is enabled for the Project, THE AI_Voice_Agent SHALL transfer the live call to an available Salesperson within 30 seconds
2. WHEN a Prospect requests human assistance outside the Call_Window, THE Platform SHALL inform the Prospect that a Salesperson will call back and SHALL create a Callback_Notification
3. WHEN a Warm_Transfer is initiated, THE AI_Voice_Agent SHALL provide the receiving Salesperson with a brief context summary (Prospect name, Project, key discussion points) before connecting
4. IF no Salesperson is available for Warm_Transfer within 30 seconds, THEN THE Platform SHALL inform the Prospect and create a priority Callback_Notification
5. WHEN a Callback_Notification is created, THE Platform SHALL track whether the callback was completed and alert the Sales_Manager if the callback SLA is breached
6. THE Platform SHALL allow Tenant_Admin to configure handoff mode (warm_transfer, callback_only, or both) per Project
7. THE Platform SHALL track Salesperson availability status (available, busy, offline) via a manual status toggle in the Dashboard. WHEN a Salesperson sets status to offline or busy, THE Platform SHALL exclude them from Warm_Transfer routing
8. IF no Salesperson has available status within the Project at the time of Warm_Transfer request, THE Platform SHALL fall back to Callback_Notification without waiting the full 30-second timeout

### Requirement 11: Lead Assignment & Routing

**User Story:** As a Tenant_Admin, I want qualified leads to be automatically assigned to Salespersons using fair distribution rules, so that workload is balanced and response times are minimized.

#### Acceptance Criteria

1. WHEN a Lead reaches the qualified stage, THE Platform SHALL assign the Lead to a Salesperson using the Round_Robin algorithm within the Lead's Project
2. THE Platform SHALL support project-based routing where each Salesperson is associated with one or more Projects
3. WHEN a Salesperson is unavailable (on leave, at capacity), THE Platform SHALL skip that Salesperson in the Round_Robin rotation
4. THE Platform SHALL allow Tenant_Admin to configure assignment rules per Project (Round_Robin is the default; additional algorithms may be added)
5. WHEN a Lead is assigned, THE Platform SHALL notify the Salesperson via the configured notification channels within 30 seconds
6. THE Platform SHALL track assignment timestamps and generate alerts when a Salesperson has not acknowledged an assigned Lead within the configured SLA
7. WHEN a Salesperson is removed from a Project, THE Platform SHALL redistribute their unacknowledged Leads to other active Salespersons in that Project
8. WHEN a Lead is assigned to a Salesperson, THE Platform SHALL cease all automated AI outbound calls and automated WhatsApp messages for that Lead (engagement lock), as required by the Warm_Transfer and Callback flow (cross-ref: Requirement 10). Automated actions SHALL only resume if the Salesperson explicitly releases the Lead back to the automated workflow

### Requirement 12: Inbound Call Handling

**User Story:** As a Sales_Manager, I want inbound calls from Prospects to be logged and matched to existing leads, so that no inbound interest is lost.

#### Acceptance Criteria

1. WHEN an inbound call is received on a Project's configured caller ID, THE Platform SHALL log the call with timestamp, duration, and caller phone number
2. WHEN an inbound call is received, THE Platform SHALL attempt to match the caller's phone number to an existing Lead record
3. WHEN a match is found, THE Platform SHALL create an opportunity note on the matched Lead indicating the inbound call details
4. WHEN no match is found, THE Platform SHALL create a new unmatched inbound call record and notify the Sales_Manager
5. THE Platform SHALL NOT initiate AI conversation on inbound calls (inbound calls are logged for human follow-up only)
6. WHEN an inbound call is logged, THE Platform SHALL notify the assigned Salesperson (if a Lead match exists) via the Dashboard

### Requirement 13: Dashboard & Real-time Notifications

**User Story:** As a Salesperson, I want a real-time dashboard showing my assigned leads and notifications, so that I can act on hot leads immediately.

#### Acceptance Criteria

1. THE Dashboard SHALL display leads, call outcomes, site visit statuses, and WhatsApp conversations relevant to the logged-in user's role and Project assignments
2. WHEN a Hot_Lead notification is generated, THE Platform SHALL deliver it to the assigned Salesperson via in-app WebSocket push within 5 seconds
3. WHEN a Hot_Lead is identified, THE Platform SHALL send a WhatsApp notification to the assigned Salesperson's registered phone number containing: Prospect name, Project name, lead score, 1-line conversation summary, and a click-to-call link to the Prospect's phone number
4. THE Platform SHALL send a daily email digest to Sales_Managers summarizing lead activity, call outcomes, and SLA breaches for their Projects
5. THE Dashboard SHALL update in real-time via WebSocket for: new lead assignments, call completions, WhatsApp messages, and site visit status changes
6. THE Platform SHALL support multi-channel notifications configurable per role: in-app (WebSocket), WhatsApp (for Salespersons on hot leads), and email digest (for Sales_Managers)
7. WHEN a notification is delivered, THE Platform SHALL track delivery status and read status for audit purposes

### Requirement 14: Reporting & Analytics

**User Story:** As a Sales_Manager, I want analytics on cost-per-lead, connect rates, and AI quality, so that I can optimize campaigns and measure ROI.

#### Acceptance Criteria

1. THE Platform SHALL compute and display cost-per-lead metrics based on per-minute billing rates and call durations per Project
2. THE Platform SHALL compute and display cost-per-site-visit metrics by aggregating costs of all calls and messages leading to a site visit booking
3. THE Platform SHALL track and display connect rate trends (successful connections vs total call attempts) over configurable time periods
4. THE Platform SHALL provide project comparison views allowing Sales_Managers to compare performance metrics across Projects within their Tenant
5. THE Platform SHALL compute an AI quality score per Project based on: call completion rate, successful qualification rate, and Prospect satisfaction signals
6. THE Platform SHALL expose analytics data to Metabase for custom dashboard creation by the Platform team
7. THE Platform SHALL NOT provide custom report builder functionality or report export (Metabase handles these natively)

### Requirement 15: Billing & Usage Tracking

**User Story:** As a Super_Admin, I want to track per-minute call usage per Tenant and set billing rates, so that I can generate accurate invoices.

#### Acceptance Criteria

1. THE Platform SHALL track call duration in seconds for every outbound and inbound call, associated with the Tenant and Project
2. WHEN a Super_Admin sets or updates a per-minute billing rate for a Tenant, THE Platform SHALL apply the new rate to all subsequent calls
3. THE Platform SHALL compute total usage cost per Tenant per billing period based on call durations and the configured per-minute rate
4. THE Platform SHALL provide Super_Admin with a usage summary view showing per-Tenant and per-Project call minutes and costs
5. THE Platform SHALL NOT generate invoices automatically (invoicing is a manual process performed by the Platform team)
6. THE Platform SHALL retain usage records for a minimum of 12 months for billing reconciliation

### Requirement 16: Authentication, RBAC & Audit

**User Story:** As a Super_Admin, I want role-based access control with full audit trails, so that the Platform is secure and all actions are traceable.

#### Acceptance Criteria

1. THE Platform SHALL enforce authentication for all Dashboard and API access using secure token-based authentication
2. THE Platform SHALL support the following roles with hierarchical permissions: Super_Admin > Tenant_Admin > Sales_Manager > Salesperson
3. THE Platform SHALL enforce that users can only access data belonging to their own Tenant (Tenant_Isolation)
4. WHEN a Super_Admin creates a user, THE Platform SHALL assign the user to a specific Tenant and role
5. WHEN a Tenant_Admin creates a user, THE Platform SHALL restrict the new user's role to Sales_Manager or Salesperson within the Tenant_Admin's Tenant
6. THE Platform SHALL log all significant actions (user login, configuration changes, lead updates, call initiations, data exports) in an immutable Audit_Log
7. THE Audit_Log SHALL record: timestamp, actor (user ID and role), action performed, affected resource, Tenant context, and IP address
8. THE Platform SHALL retain Audit_Log records for a minimum of 12 months
9. WHEN a user's role is changed or revoked, THE Platform SHALL invalidate active sessions for that user within 60 seconds

### Requirement 17: Consent & Compliance

**User Story:** As a Tenant_Admin, I want the system to handle consent and regulatory compliance, so that the Platform operates within legal boundaries for telemarketing and data privacy in India.

#### Acceptance Criteria

1. THE Platform SHALL store consent status for each Lead record indicating whether the Prospect has consented to receive calls and messages
2. WHEN a Lead is received without explicit consent data from the CRM_System, THE Platform SHALL treat the Lead as consent-pending and SHALL NOT place outbound calls or messages until consent is confirmed. THE Platform SHALL send a single opt-in WhatsApp message requesting consent (where WhatsApp number is available), and SHALL NOT proceed with AI calling until positive consent is received or the Tenant provides documented proof of prior consent
3. WHILE a Lead's consent status is revoked, THE Platform SHALL NOT place outbound calls or send messages to that Prospect
4. WHEN a Prospect requests to opt out during a call, THE AI_Voice_Agent SHALL acknowledge the request, end the call politely, and update the consent status to revoked within 5 seconds
5. THE Platform SHALL enforce the configured Call_Window (default 10:00–20:00 IST) to comply with TRAI regulations on telemarketing hours
6. THE Platform SHALL store all Prospect data with data residency in India
7. WHEN a Prospect requests data deletion, THE Platform SHALL remove personal data within 30 days while retaining anonymized records required for billing and audit
8. THE Platform SHALL prefix all AI calls with a disclosure that the call is AI-powered, as required by applicable regulations
9. THE AI_Voice_Agent SHALL inform the Prospect that the call is being recorded for quality and training purposes within the first 15 seconds of every connected call, immediately after the AI disclosure

### Requirement 18: Observability & Alerting

**User Story:** As a Super_Admin, I want comprehensive monitoring and alerting, so that I can detect and resolve issues before they impact Tenants.

#### Acceptance Criteria

1. THE Platform SHALL emit structured telemetry (traces, metrics, logs) via OpenTelemetry for all API requests, workflow executions, and external service calls
2. THE Platform SHALL expose Prometheus metrics for: active calls, call queue depth, API latency, workflow failure rate, CRM sync lag, and WhatsApp delivery rate
3. WHEN a critical metric breaches its threshold (e.g., call failure rate > 10%, CRM sync lag > 60 seconds, API p95 > 2 seconds), THE Platform SHALL generate an alert via the configured alerting channel (Grafana alerts)
4. THE Platform SHALL integrate with Sentry for error tracking and exception reporting across all services
5. THE Platform SHALL provide per-Tenant health views in the Super_Admin Dashboard showing active workflows, error rates, and SLA compliance
6. THE Platform SHALL log all external service interactions (telephony, WhatsApp, CRM) with correlation IDs for end-to-end tracing
7. WHEN the concurrent call capacity reaches 80% utilization, THE Platform SHALL generate a capacity warning alert to Super_Admin

### Requirement 19: Performance & Scalability

**User Story:** As a Super_Admin, I want the Platform to meet strict latency targets and scale to support growing Tenant demand, so that Prospects experience natural conversations and no leads are delayed.

#### Acceptance Criteria

1. WHEN a new Lead is created, THE Platform SHALL initiate the first outbound call within 2 minutes (provided the Call_Window is active)
2. WHEN a call is completed, THE Platform SHALL sync the outcome to the CRM_System within 10 seconds
3. WHEN a Hot_Lead is identified, THE Platform SHALL notify the assigned Salesperson within 30 seconds
4. THE AI_Voice_Agent SHALL maintain voice response latency suitable for natural conversation pace (turn-taking delay perceivable as natural)
5. THE Platform SHALL support 5 concurrent AI calls with architecture designed to scale to 20 or more concurrent calls without degradation
6. WHEN the Platform is under peak load (maximum concurrent calls), THE Platform SHALL maintain API response times below 500ms at the 95th percentile
7. THE Platform SHALL process lead ingestion from the CRM_System at a sustained rate of at least 100 leads per minute per Tenant without queuing delays exceeding 5 seconds. WHEN concurrent call capacity is exhausted, THE Platform SHALL queue outbound calls using a priority-based FIFO queue and execute them as capacity becomes available
8. THE Platform SHALL enforce per-Tenant API rate limits (configurable, default: 1000 requests per minute) to prevent abuse or misconfigured integrations from impacting other Tenants

### Requirement 20: Data Architecture & Isolation

**User Story:** As a Super_Admin, I want all Tenant data to be securely isolated in a shared database, so that the Platform is cost-effective while maintaining strict data boundaries.

#### Acceptance Criteria

1. THE Platform SHALL use a single PostgreSQL database with row-level security policies enforcing Tenant_Isolation
2. THE Platform SHALL include a tenant_id column on every table containing Tenant-specific data
3. WHEN any database query is executed, THE Platform SHALL apply Tenant_Isolation filters ensuring no cross-tenant data leakage
4. THE Platform SHALL use Valkey for caching and distributed locks with Tenant-namespaced keys, and NATS JetStream for event pub-sub with Tenant-scoped subjects, to prevent cross-tenant data access
5. THE Platform SHALL store vector embeddings for Knowledge_Base content in PostgreSQL using pgvector with tenant_id column for Tenant_Isolation
6. THE Platform SHALL store call recordings and documents in S3-compatible storage with Tenant-prefixed paths
7. THE Platform SHALL encrypt all data at rest using AES-256 and all data in transit using TLS 1.2 or higher
8. WHEN a Tenant is deleted, THE Platform SHALL remove all Tenant data (database records, files, cache entries) within 30 days while preserving anonymized billing records

### Requirement 21: NDNC Registry Compliance

**User Story:** As a Tenant_Admin, I want the system to scrub outbound call lists against India's National Do Not Call (NDNC) registry, so that the Platform never calls DNC-registered numbers and avoids regulatory penalties.

#### Acceptance Criteria

1. BEFORE initiating any outbound call, THE Platform SHALL check the Prospect's phone number against the NDNC registry (or a locally cached copy refreshed daily)
2. IF a phone number is found in the NDNC registry, THEN THE Platform SHALL block the outbound call, mark the Lead as ndnc_blocked, and notify the Tenant_Admin
3. THE Platform SHALL maintain a local cache of NDNC registry data, refreshed at minimum once every 24 hours from the official TRAI NDNC provider
4. WHEN a Tenant_Admin views a blocked Lead, THE Dashboard SHALL clearly indicate the NDNC status and the reason the call was blocked
5. THE Platform SHALL log all NDNC checks (pass/fail) in the Audit_Log for regulatory compliance evidence
6. IF the NDNC registry service is unavailable, THEN THE Platform SHALL queue the call for later (not skip the check) and alert Super_Admin of the registry outage
