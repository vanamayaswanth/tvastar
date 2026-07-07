# Security Grammar — Policy Notation & Threat Mapping Reference

Load this file when producing the Output Contract artifacts (threat model, access rules, least-privilege assessment, audit plan).

---

## The Policy Grammar — Default Deny Is the Starting Position

Shakuni won because a system that did not explicitly forbid an action implicitly allowed it.

Security is written the opposite way: state what is permitted, deny everything else by default, and make the permission boundary explicit rather than assumed.

Security owns the **access-policy notation** and shares the safety patterns with Reliability.

### Security Policies — Permit, Deny, Allow-only, Default Deny

Every access rule is stated as one of:

* **Default deny** — the baseline; nothing is allowed unless a rule permits it
* **Permit** `<subject>` to `<action>` on `<resource>` `<under condition>`
* **Deny** `<subject>` ... — an explicit prohibition that overrides permits
* **Allow only** `<set>` — the closed set of who/what may do this

Ask:

* Is the baseline default-deny, or does the absence of a rule accidentally permit (Rule 1: think like Shakuni)?
* Is every Permit scoped to subject + action + resource + condition — or is it a blanket grant (Rule 9: least privilege)?
* Does a Deny correctly override a broad Permit, or can a permit slip through?

Example: "Default deny. Permit authenticated users to READ their own orders. Permit role=admin to EXPORT orders ONLY IF MFA present. Deny all access to suspended accounts (overrides all permits)."

---

### Safety Patterns (Shared With Reliability) — The Security Lines

The same `Never / Always / Only if / At most / At least` grammar, applied to trust and abuse:

Ask:

* What must NEVER happen across a trust boundary (never trust a frontend price, never log a secret)?
* What must ALWAYS happen (always re-check authorization at each sensitive step — Rule 8)?
* What is gated `Only if` (privileged action only if MFA + same-tenant)?
* What is bounded `At most` (at most N failed logins before lockout — the slow-accumulation defense, Rule 6)?

Example: "The system SHALL NEVER expose a password or token in a log or response. The system SHALL ALWAYS re-verify authorization on state-changing actions. Bulk export SHALL be permitted ONLY IF actor is admin AND MFA is present. Login SHALL be allowed AT MOST 5 times per account per 15 minutes."

---

### STRIDE + OWASP — Name the Threat, Map the Control

Walk every component through **STRIDE** and map each finding to a named control:

* **Spoofing** → authentication
* **Tampering** → integrity / signing (the loaded dice, Rule 3)
* **Repudiation** → audit logs with the real actor (Rule 7)
* **Information disclosure** → encryption + least privilege (Rule 9)
* **Denial of service** → rate limits / quotas / bulkheads
* **Elevation of privilege** → authorization (Rule 8)

Track every finding against the **OWASP Top 10** by name — Broken Access Control (the #1 risk, covered by Rule 8), Cryptographic Failures, Injection, Insecure Design, Security Misconfiguration, Vulnerable/Outdated Components, Identification & Authentication Failures, Software & Data Integrity Failures, Security Logging & Monitoring Failures, SSRF.

Ask:

* Has each component been walked through all six STRIDE categories?
* Does each finding map to an OWASP Top 10 category and a named, testable control?
* Is Broken Access Control — the most common breach — explicitly covered by the authorization checks in Rule 8?

---

### Cross-References

* **Temporal logic / Safety / FMEA / TLA+ invariants** → Reliability owns the availability framing; full safety grammar lives there.
* **Acceptance Criteria / valid-path abuse** → QA finds the structural gap; Security judges whether it is deliberately exploitable (Rule 5).
* **API Contract** → Architect; Security validates untrusted input against it (Rule 3, the Lac House).
* The blended spec template's `Security` NFR field is owned here.
