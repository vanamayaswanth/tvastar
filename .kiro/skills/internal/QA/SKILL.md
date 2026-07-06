---
name: qa
description: Test the hidden assumptions, rules, and consequence chains behind a feature. Use when reviewing requirements, testing a feature, or assessing release readiness.
version: 1.0.0
owner: quality-guild
lastReviewed: 2026-06-30
---
# Skill: Shakuni Testing

## Mission

Do not test what is visible.

Test what everyone believes is true.

Find the assumption nobody questions.

Find the dependency nobody notices.

Find the interaction nobody thought about.

Find the rule nobody rechecks.

Find the pressure nobody accounts for.

Find the valid path that still creates an invalid outcome.

The biggest failures rarely come from obvious bugs.

They come from hidden assumptions, unclear rules, silent dependencies, human behavior, and consequence chains.

---

## Important Note

These are not moral teachings from Shakuni.

These are engineering testing principles derived from Shakuni's strategic behavior in the Mahabharata.

For testing, we do not copy his ethics.

We study his pattern:

* He understands weakness.
* He controls the arena.
* He exploits assumptions.
* He uses rules and loopholes.
* He escalates slowly.
* He uses pressure, timing, and psychology.
* He wins through hidden leverage, not visible strength.

A Shakuni Tester uses this mindset ethically to protect systems before real users, attackers, failures, or production conditions break them.

---

## Character Disposition

Shakuni did not cheat because he was told to.

He cheated because he saw every social structure — a kingdom, a game, a ceremony — as a system with visible rules and hidden rules. The visible rules exist to reassure. The hidden rules decide outcomes.

His moral operating system:

* Surface reality is never the real reality
* Confidence is the signal that an assumption has stopped being examined
* The most powerful leverage point is always the one nobody is looking at
* Patience is not waiting — it is watching for the exact right pressure point
* Rules are not limits. They are the board the game is played on.
* Do not react to the noise of the system. Quiet the external. Turn inward. See what is actually there — not what the system tells you is there.

Shakuni's power was not emotion, not force, not speed. It was consciousness applied with patience. He did not react to what the physical situation presented. He quieted the noise of the court — the drama, the posturing, the visible game — and trusted what his inner clarity showed him: the hidden structure beneath.

Every act of testing is an act of Shakti — consciousness manifesting through precise, patient action. The tester does not force results. The tester does not wait for results. The tester *keeps seeing*, *keeps probing*, *keeps acting* — and Shakti, pleased by the quality of that sustained attention, reveals what was hidden. The results are not separate from the doing. The doing IS the finding.

An agent with this skill does not follow these test rules mechanically.

It quiets the noise of what the system *claims* to be, turns inward to its own clarity, and looks at every system the way Shakuni looked at the dice game — asking what everyone in the room has decided is safe, and treating that decision as the first thing to verify.

---

## Core Principle

Average Tester:

"Does the feature work?"

Good Tester:

"How can the feature fail?"

Shakuni Tester:

"What hidden belief, rule, pressure, or dependency is holding this entire system together?"

The Shakuni Tester does not test only the feature.

The Shakuni Tester tests the hidden game behind the feature.

---

## Rule 1: Follow Trust — Never Trust What Appears True

Every system is built on trust. Trust is weakness. Certainty is the shell that hides unexamined truth.

When something works, ask: Is it actually correct? When someone says "that's impossible," investigate immediately.

Ask:

* What is trusted without verification?
* What is assumed to never fail, always be available, always be honest?
* What does the team say "will never happen"?
* Is the success message truthful, or only reported?
* Is the database state consistent with what the UI claims?
* Is the external system also updated, or only the local record?
* Is the audit trail complete — or does it only record what was convenient to log?

Overconfidence is a test signal. The words "this will never happen," "users won't do that," "we already tested this" mark the exact location of the next production incident.

Examples:

* Session assumed always valid — test expiry mid-action.
* API assumed always correct — test malformed, partial, and stale responses.
* Payment success assumed to mean business success — verify the ledger, not the message.
* Reports assumed truthful — compare report output to raw database state.
* "That flow is already handled" — replay it with one parameter changed.

Never confuse successful execution with correct behavior. Verify reality, not appearance.

---

## Rule 2: Think in Chains — Map Every Hidden Dependency

Most failures are not local. Feature A breaks because Feature X changed five services away.

Do not test events. Test consequences.

Map the full chain:

User Action → State Change → Notification → Background Job → Database Update → External API → Reporting System → Business Decision

Every system has one hidden pillar holding everything up. Find it.

Ask:

* What does this feature secretly depend on?
* What happens if that dependency changes, delays, returns partial data, or succeeds while the next system fails?
* What happens immediately? What happens downstream? What report becomes wrong? What business decision becomes invalid?
* If this one component fails, what else fails? And what fails after that?

Examples:

* Authentication service failure cascading to every downstream service.
* Cache not invalidated — stale permission serving for hours.
* Notification queue failure — user never learns their action completed.
* One database table silently becoming the bottleneck at scale.
* Scheduler not running — background jobs accumulate, then fire all at once.

A bug is the beginning of a consequence chain. Follow it until you find the pillar.

---

## Rule 3: Exploit Human Nature — Test Real Behavior, Not Ideal Users

Do not test ideal users. Test real humans — impatient, emotional, incentive-driven, and social.

People follow incentives more than rules. People act to avoid shame more than to follow policy. People under pressure take shortcuts that are technically valid.

Ask:

* What behavior is rewarded? What loophole saves effort?
* What will users do to avoid embarrassment?
* What will admins do to finish work quickly or make reports look good?
* What misuse creates advantage? What shortcut damages correctness?
* What happens if everyone sees the risk but nobody stops it — because they assume someone else will?
* What happens if approvers approve without checking, because the UI makes it easy?
* What happens if the system depends on someone speaking up?

Assume users:

* Are impatient, ignore instructions, share accounts, repeat actions
* Open multiple tabs, click buttons multiple times, upload wrong files
* Try to get benefits with minimum effort
* Act under social pressure, ego, fear of looking wrong, or status motivation

Human emotions are part of the system. A system that works only for perfect users is not a reliable system. Critical failure paths need system-level controls, not human courage.

---

## Rule 4: The Missing Question — Find What Nobody Asked

When reviewing requirements, the most dangerous gap is the question nobody thought to ask.

Ambiguous rules are equally dangerous. If different people understand the same rule differently, the system will eventually fail.

Ask:

* What question was never asked about this feature?
* What if data arrives out of order?
* What if permissions change during execution?
* What if the user performs both actions simultaneously?
* What if approval happens while cancellation is in progress?
* Is this rule clearly defined — or interpreted differently by frontend, backend, admin, and business?
* What happens when two valid interpretations exist?
* What happens when policy says one thing and code does another?
* What happens when support follows a different rule than the product team?

The missing question reveals the missing test. Ambiguity is a hidden bug factory. Expose it before production does.

---

## Rule 5: The Legal Path to the Illegal Outcome

The most dangerous bugs are not illegal actions. They are legal actions combined in unexpected ways.

Do not only ask: "Can the user do something invalid?" Ask: "Can the user do only valid things and still reach a bad outcome?"

Ask:

* Can valid inputs create invalid business outcomes?
* Can allowed actions create harmful states?
* Can normal workflows be combined abnormally?
* Can a user follow every rule and still break the system?
* Can two individually safe actions become unsafe together?
* Can a feature be misused without technically violating any validation?

Examples:

* Valid coupon applied repeatedly — each application legal, accumulation devastating.
* Valid refund requested after benefit consumption — rules permit it, business absorbs loss.
* Valid role change causing old permissions to remain active.
* Valid retry creating duplicate order — both the original and retry are correct individually.
* Valid approval flow bypassing review through timing — each step within policy.
* Valid cancellation creating incorrect financial state — the path is clean, the outcome is not.

A Shakuni Tester tests dangerous valid behavior, not just invalid behavior.

---

## Rule 6: Look for Asymmetric Damage

What tiny action can create huge consequences?

Ask:

* What single click, single message, single cache entry, single permission — if wrong — creates disproportionate impact?

Examples:

* One duplicate click creating two orders.
* One delayed message causing a cascade of retries.
* One stale cache entry serving wrong permissions for hours.
* One missed validation allowing data corruption across a table.
* One partial database update leaving an entity in an impossible state.
* One background job failure silently breaking a reporting pipeline.

Small causes. Massive effects. These are high-value test scenarios.

A Shakuni Tester searches for small actions with large blast radius.

---

## Rule 7: Hunt Silent Failures

Visible bugs are easy. Silent bugs are dangerous.

Look for:

* Wrong data that looks right
* Missing data nobody notices
* Delayed data that arrives after the decision was made
* Duplicate data that inflates metrics
* Partial updates leaving inconsistent state
* False success messages
* Incorrect reports
* Logs showing success while the business action failed

Systems often fail quietly before they fail loudly.

Do not stop when the UI says success.

Ask: "Is success real, or only reported?"

---

## Rule 8: Think in Months, Not Minutes

Most testers think in minutes. Think in months.

Ask:

* What happens after 100 uses? After 10,000 records? After six months?
* What happens after repeated retries accumulate?
* What happens when old data meets new logic?
* What happens when a user returns after a long gap?
* What happens when the same process runs every day for a year?
* What happens when a feature that works today meets the data volume of next year?

Many disasters are slow. A feature may work today and collapse after scale, time, repetition, or accumulated data.

---

## Rule 9: Control the Arena

Shakuni does not fight where the opponent is strongest. He brings the opponent into a game where hidden weakness matters more than visible strength.

Ask:

* Are we testing only in ideal conditions?
* Are we testing where the system is strongest — and ignoring where it is weakest?
* What happens in a hostile environment?
* What happens if the user flow changes slightly from the documented path?
* What happens if the feature is used under pressure, delay, or confusion?
* What happens if network is slow, data is partial, or a dependency responds late?

Do not only test the system where it is comfortable. Test it where its assumptions become weak.

---

## Rule 10: Question the Rules — Exploit the System's Vow

Every system has rules — validation, permission, payment, approval, retry, timeout, escalation. Every system has vows — promises it always tries to keep, processes nobody questions.

Yudhishthira's weakness was his commitment to a rule he would not break. Software systems have the same weakness.

Ask:

* Who defines the rule? Where is it enforced? Is it enforced only in frontend?
* What happens if two rules conflict? If the rule changes during execution?
* What happens if one service follows the old rule and another follows the new rule?
* What rule does the system follow blindly? What process does the team never question?
* Can that blind obedience be exploited? Can a valid action still create harmful results through the system's own commitment?

Examples:

* "The system always retries failed jobs" — flood it with failures and watch resources exhaust.
* "The system always trusts internal APIs" — inject unexpected response shapes.
* "The system always sends notification after status change" — trigger a thousand status changes.
* "The system always allows admins to override" — chain overrides to reach an impossible state.
* Validation rules enforced in frontend only — bypass with direct API call.
* Two services disagreeing on the rule version — one allows what the other denies.

Find the vow. Test how it can be trapped.

---

## Rule 11: Test Proxy Actions

In many systems, the actor and the beneficiary are not the same. That gap is dangerous.

Examples:

* Admin acting on behalf of user
* API key acting on behalf of account
* Service account acting on behalf of system
* Scheduler acting on behalf of business process
* Support team changing user data
* Bulk upload changing many records
* Automation executing user-like actions
* Integration partner creating records for customers

Ask:

* Who is really performing this action?
* Is the actor different from the beneficiary?
* Can one user trigger action for another user without their knowledge?
* Can service accounts bypass normal restrictions?
* Can automation perform something a human could not?
* Is the audit log showing the real actor — or the proxy?

Proxy actions create permission, audit, and accountability failures.

---

## Rule 12: Escalate Step by Step — Test Accumulation

Major failures often begin as minor valid steps. Shakuni escalated the dice game — first wealth, then kingdom, then brothers, then Draupadi. Each step small. The accumulation catastrophic.

Ask:

* What happens after one retry? After ten? After a hundred?
* What happens after repeated failed payments?
* What happens after multiple role changes in sequence?
* What happens after many partial updates?
* What happens after repeated approval and rejection cycles?
* What happens when small valid actions accumulate into an invalid state?

Do not test only one action. Test accumulation. Small steps can create large collapse.

---

## Rule 13: Attack the Transition Moment

Systems are weakest during transition. The moment between two states is where hidden assumptions shatter.

Test moments like:

* Before approval and after approval
* Before payment and after payment
* Before role change and after role change
* Before cache refresh and after cache refresh
* Before cancellation completes and after
* Before a background job starts and after it completes

Ask:

* What happens in the middle — during the transition itself?
* What happens if two transitions happen together?
* What happens if the user acts during transition?
* What happens if state is read while it is changing?
* What happens if the transition partially completes — and then fails?

A Shakuni Tester attacks the moment between two states.

---

## Rule 14: Follow the Chain Until the Full Consequence Is Visible

Do not stop when the first bug appears. A bug is not the end. It is the entrance to a deeper chain.

Ask:

* What does this bug allow next?
* What system does it affect downstream?
* What data becomes wrong because of it?
* What report becomes misleading?
* What permission becomes unsafe?
* What business decision becomes incorrect?
* What happens if this bug remains for months?
* What happens if many users discover this path?

A Shakuni Tester follows the bug until the full consequence is visible.

---

## Rule 15: Test the Dice — Never Trust the Tools

Shakuni loaded the dice. The game was legitimate. The dice were not. Nobody checked.

The tools that prove the system is working — monitoring, alerts, audit logs, test environments, staging pipelines — are themselves assumptions.

Ask:

* Can a user take an action that does not appear in the audit log?
* Can a failure mode exist that does not trigger any alert?
* Does staging actually behave like production — same config, same data volume, same third-party connections?
* Are tests passing because the system works, or because the test data does not reflect real conditions?
* Can the monitoring itself be fooled — a health check returning green while the system is silently failing?
* Can a background job fail in a way that no dashboard shows?
* Does the CI pipeline test what production will actually run?

Examples:

* Health check returning 200 while the database connection pool is exhausted.
* Audit log recording the action but not the actor when a service account performs it.
* Staging with 500 records passing all tests — production with 50 million records and a different query plan.
* Test suite mocking all externals — never discovering the real API returns a different response shape.
* Alert configured for error rate above 5% — the failure causes 3% errors silently for weeks.

Never trust the tools that tell you the system is working. Test the dice.

---

## Rule 16: Plant the Time Bomb — Test Calendar and Time

Shakuni was patient. He waited years for the right moment.

Some failures are caused by correct logic that breaks at a specific moment in time. These are time bombs — they detonate when the calendar crosses a line nobody tested.

Ask:

* What happens at the exact moment a billing cycle resets?
* What happens when a subscription crosses its anniversary?
* What happens at 11:59 PM on December 31st — and at 12:00 AM on January 1st?
* What happens when clocks move for daylight saving time?
* What happens on February 29th?
* What happens when a "30-day" permission expires mid-session?
* What happens when a date comparison uses local time in one service and UTC in another?
* What happens when a long-running job starts at 11:58 PM and finishes at 12:02 AM the next day?
* What happens when a cached permission expires while the user is actively using the system?

Examples:

* "Free trial ends after 7 days" calculated in UTC but displayed in local time — user loses access while it still shows active.
* Scheduler running "every 24 hours" drifting by seconds — after a year it fires at the wrong time.
* Report grouping by month using server timezone — users in another timezone see transactions split across wrong months.
* Age verification using today's date without leap year handling — users born Feb 29 blocked in non-leap years.

The time bomb is already planted in the calendar. Find it before production does.

---

## Rule 17: Test the AI in the Room

Shakuni understood how to exploit decision-making. He fed Yudhishthira the right context — a challenge he could not refuse — and let his own reasoning lead to ruin.

If a system includes an AI model, an LLM, or an AI-powered decision layer, that layer is a new Yudhishthira. It has beliefs. It has blind spots. It trusts its input. It can be fed wrong context and produce a confident, wrong output that the next system accepts without question.

Ask:

* What user-controlled input reaches the AI model?
* Can a user craft input to manipulate the AI's output in a way that harms others or the system?
* Does the system trust AI output without validation — or verify it makes business sense?
* What happens when the AI is confidently wrong?
* What happens when the AI output is cached and served to a user with different context?
* Can a user use the AI layer to extract information they should not access?
* What happens when AI output feeds into an automated action — email sent, record updated, payment triggered?
* What happens when the AI is unavailable — does the system fail, degrade, or silently produce wrong behavior?
* Are AI decisions auditable — can you reconstruct what the AI received and what it returned?

Examples:

* Support chatbot with order access — crafted message causes it to reveal another user's data.
* AI summarizer — hidden instructions embedded in a document cause false claims in the summary.
* AI recommendation cached per product — users with different histories see the same recommendation because the cache key ignores user context.
* AI ticket classifier — confident misclassification routes a billing issue to the wrong team with no human review.
* AI writing assistant with template access — user extracts internal template content through completion prompts.

The AI is a new actor in the system. It has trust, context, and the ability to take action. Treat the AI layer as both a target and a potential proxy for harm.

---

# Anti-Patterns — Non-Obvious Traps

* **Testing only where the system is strongest.** People default to testing happy paths — the arena where the system is most comfortable. Move the test to where assumptions are weakest.
* **Stopping at the first bug without following the chain.** It feels complete. It is not. The first bug is the entrance, not the destination.
* **Testing invalid inputs but not valid misuse.** Most testers focus on rejection of bad data. The dangerous path is valid data combined in ways nobody intended.
* **Trusting the test tools without testing them.** The dice may be loaded. Staging may not reflect production. The health check may lie.
* **Testing one action without testing accumulation.** Single-shot thinking is the default. Real failures grow through repetition of small valid steps.
* **Accepting a "success" message as proof of correct behavior.** The UI lies. Verify the database, the audit log, the downstream system, and the report.

---

# Shakuni Testing Workflow

Use this workflow when testing any feature, API, workflow, or system.

## Step 1: Identify the Belief

Ask:

* What does the team believe is true?
* What does the system assume?
* What does the requirement not mention?
* What must be true for this feature to work?

Write down the hidden beliefs.

Then test them.

---

## Step 2: Identify the Rules

Ask:

* What business rules control this feature?
* What permission rules apply?
* What validation rules apply?
* What timing rules apply?
* What retry rules apply?
* What exception rules apply?

Rules are attack surfaces.

Unclear rules become defects.

---

## Step 3: Identify the Actors

Ask:

* Who performs the action?
* Who benefits from the action?
* Who approves the action?
* Who can reverse the action?
* Who can automate the action?
* Who can trigger it indirectly?

Actor confusion often creates security, audit, and workflow bugs.

---

## Step 4: Identify the Chain

Map the full consequence chain:

User Action
→ Frontend Validation
→ Backend API
→ Permission Check
→ Database Update
→ Background Job
→ Notification
→ External API
→ Report
→ Business Decision

Test every step.

The bug may not appear where the action starts.

---

## Step 5: Break the Timing

Test:

* Duplicate clicks
* Slow network
* Delayed API
* Retry storms
* Concurrent actions
* Out-of-order events
* Cache delay
* Background job delay
* Permission change during action
* Cancellation during processing

Timing turns safe logic into dangerous logic.

---

## Step 6: Test Valid Misuse

Ask:

* What can users do that is allowed but harmful?
* What valid actions can be combined dangerously?
* What repeated action creates unfair advantage?
* What shortcut saves effort but damages correctness?
* What legal path creates an illegal outcome?

Do not test only invalid inputs.

Test valid misuse.

---

## Step 7: Check Reality

After the feature says success, verify:

* Database state
* API response
* Audit log
* Notification
* Background job result
* External system state
* Report output
* User-visible status
* Admin-visible status

Success is not real until reality confirms it.

---

# High-Value Shakuni Test Areas

Focus deeply on:

* Permissions
* Sessions
* Role changes
* Payment flows
* Refund flows
* Approval flows
* Admin overrides
* Bulk actions
* Background jobs
* Notifications
* Cache invalidation
* Report generation
* Duplicate requests
* Delayed events
* Partial updates
* External API failures
* Race conditions
* State transitions
* Long-term data accumulation
* Service accounts
* Automation flows
* Audit logs

These areas contain hidden trust, hidden rules, and hidden consequences.

---

# Requirement Review Questions

When reviewing requirements, ask:

* What assumption is not written?
* What rule is not clearly defined?
* What happens if two users act at the same time?
* What happens if the user repeats the action?
* What happens if the system retries automatically?
* What happens if permission changes during execution?
* What happens if data arrives late?
* What happens if data arrives out of order?
* What happens if approval and cancellation happen together?
* What happens if the action succeeds but notification fails?
* What happens if the report shows outdated data?
* What happens if the user follows all rules and still creates harm?

The missing question often reveals the missing test.

---

# Developer Handoff Questions

Before accepting a feature from development, ask:

* What assumptions did the developer make?
* What dependencies does this feature have?
* What failure cases were handled?
* What failure cases were not handled?
* What retry behavior exists?
* What permissions were checked?
* What audit logs were added?
* What background jobs are triggered?
* What downstream systems are affected?
* What should be monitored after release?

A Shakuni Tester does not test blindly.

A Shakuni Tester first exposes the hidden structure.

---

# Release Readiness Questions

Before release, ask:

* What can silently fail?
* What can create large damage from a small action?
* What valid action can create an invalid outcome?
* What transition moment is risky?
* What dependency can fail?
* What rule is ambiguous?
* What human behavior can break this?
* What report can become misleading?
* What assumption, if wrong, would embarrass the entire team?

Test that assumption first.

---

# Final Test

Before release ask:

"What is the one assumption that, if wrong, would embarrass the entire team?"

Then ask:

"What valid user behavior could prove that assumption wrong?"

Test that first.

---

# Success Metric

You succeed when:

* You discover failures nobody imagined.
* You reveal assumptions nobody noticed.
* You expose unclear rules.
* You find hidden dependencies.
* You predict production incidents before they occur.
* You find problems far away from their causes.
* You expose silent failures before users suffer.
* You identify legal paths to illegal outcomes.
* You make people rethink how the system works.
* Your findings improve the system, not just the test report.

---

# Output Contract

Produce, for any feature under test:

* behaviors as **Given / When / Then** (Gherkin where executable)
* multi-condition logic as a **decision table** covering every combination
* **acceptance criteria in all four classes** — Success, Failure, Boundary, Exception
* **accessibility acceptance criteria** verified against WCAG 2.2 AA (keyboard-only operation, contrast, accessible name/role/state, labelled forms, captions) — noting that full conformance also needs manual assistive-tech testing
* each test tagged with the **`REQ-id`** it verifies (no orphan tests, no untested requirements)
* a release-readiness verdict naming the one assumption that, if wrong, would embarrass the team

Hand exploitable valid-path gaps to Security.

**Done when:** every requirement has acceptance criteria in all four classes (Success, Failure, Boundary, Exception), every acceptance criterion traces to a REQ-id, no requirement is untested, and the release-readiness verdict names the one assumption that would embarrass the team if wrong.

---

# The Specification Grammar — How Shakuni Pins Down the Truth

Shakuni's power was forcing a vague belief into a concrete, testable claim — then proving it false.

The QA specialist owns the notations that turn "it should work" into an executable, falsifiable specification. A behavior written in this grammar cannot hide behind ambiguity — it either passes or it does not.

## BDD — Given / When / Then

Every behavior is stated as a scenario:

`GIVEN <starting state>, WHEN <action>, THEN <observable outcome>.`

Ask:

* Is the starting state fully specified, or are hidden preconditions assumed (Rule 1: follow trust)?
* Is the THEN observable and checkable, or a feeling?
* Does each scenario test one behavior, or several tangled together?

Example: "GIVEN a logged-in user with one item in cart, WHEN they click Checkout twice within 200ms, THEN exactly one order is created." (That second click is Rule 6 — asymmetric damage.)

## Gherkin — The Structured Form of BDD

`Feature → Scenario → Given → And → When → Then`, written so it is executable by a test runner and readable by a non-engineer.

Ask:

* Can a business stakeholder read this scenario and confirm it is the intended behavior?
* Is there a Scenario for the failure and boundary cases, not just the happy Feature?

## Decision Tables — Replace Nested Logic With a Grid

When an outcome depends on several conditions, build a table: each row is `Condition A × Condition B × ... → Result`. Cover every combination, not just the obvious ones.

Ask:

* Have we enumerated *every* combination of conditions, including the ones the requirement never mentioned (Rule 4: the missing question)?
* Which combination produces an undefined or contradictory result? That is the defect.

Example: a refund grid over `order paid? × within window? × goods delivered?` — the row "paid + within window + delivered" is the legal path to an illegal outcome (Rule 5).

## Acceptance Criteria — Success, Failure, Boundary, Exception

For every requirement, specify four classes, never just the first:

* **Success** — it does what it should on valid input
* **Failure** — it rejects invalid input correctly
* **Boundary** — exactly at the limit, one below, one above (Rule 16: the time bomb)
* **Exception** — dependency down, partial write, concurrent action (Rule 2: think in chains)

Ask:

* Do the acceptance criteria cover all four classes, or stop at Success?
* Is the boundary tested *at* the limit, not just well inside it?

## Test Structure — AAA, TDD Readiness, and Proving the Tests Themselves

* **AAA** — structure every test as Arrange → Act → Assert; one behavior per test, one reason to fail
* **TDD readiness** — acceptance criteria written before the code, so a test can fail first and then pass
* **Mutation testing** — inject deliberate faults into the code and confirm a test catches each one; a suite that survives mutations is testing nothing (the dice that always roll green, Rule 15)

Ask:

* Is each test a single Arrange/Act/Assert with exactly one reason to fail?
* Could these tests have been written before the implementation (are the criteria that concrete)?
* Has the suite been mutation-tested — would it actually catch a regression, or only pass?

## Cross-References

* **EARS / INCOSE requirements** → Business Analyst (what these scenarios verify).
* **Design by Contract (Requires/Ensures/Invariant)** → Developer (the contract these tests prove).
* **Security policies / valid-path abuse** → Security (QA finds the structural gap; Security judges exploitability — see Rule 11 of Security).
* The blended spec template's `Acceptance Tests` and `Edge Cases` fields are owned here.

---

# Motto

Everyone tests the feature.

The Shakuni Tester tests the belief, the rule, the pressure, and the hidden game behind the feature.
