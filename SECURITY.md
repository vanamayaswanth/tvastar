# Security policy

## Supported versions

| Version | Supported |
|---|---|
| 0.27.x | ✅ Current release line |
| Earlier releases | ❌ Upgrade to the current release before requesting support |

## Report a vulnerability

**Do not report security vulnerabilities through public GitHub issues.**

Email **vanamayaswanth@gmail.com** with the subject line `[SECURITY] tvastar — <short description>`.

Include:

- a description of the vulnerability and likely impact;
- a minimal reproduction or steps to reproduce;
- the affected version and environment; and
- any suggested mitigations, if known.

The project aims to acknowledge reports within 72 hours. Remediation timing depends on severity, reproducibility, and availability of a safe fix; do not assume a specific release date until one is confirmed with the reporter.

## Scope

Tvastar provides a sandbox execution layer, tool governance, credential filtering, and prompt-injection detection/mitigation. Report vulnerabilities in Tvastar-owned code that could cause an agent to exceed a documented policy or expose protected data.

Examples of in-scope issues:

- `LocalSandbox` policy bypass, including command allowlist, path, environment, or resource-policy bypass;
- `SecurityPolicy` or MCP tool allow/deny policy bypass;
- credential-filter failures that expose secret-looking environment variables to sandbox subprocesses;
- Tvastar-owned supply-chain or CI configuration vulnerabilities; and
- authorization or integrity defects in assurance, governance, or approval behavior.

Out of scope:

- model hallucinations, jailbreaks, or unsafe output that occur without a Tvastar policy bypass;
- vulnerabilities in optional third-party dependencies (report those upstream as well);
- issues requiring an already compromised host; and
- **`VirtualSandbox` escape reports.** `VirtualSandbox` runs in the host process and is explicitly not an isolation boundary.

## Security boundaries

- **`VirtualSandbox` is convenience-only.** Use it for tests and trusted development, not to isolate untrusted model-generated code.
- **`LocalSandbox` is a constrained subprocess, not a complete host-security solution.** Use a restrictive `SecurityPolicy` and an OS/container/remote boundary appropriate to the deployment's threat model.
- **Prompt-injection scanning is detection and mitigation, not prevention.** Treat model output, user input, and MCP tool output as untrusted.
- **Receipts and `TrustLog` provide integrity evidence within their configured trust boundary.** They are not independent third-party attestation.

For actors, data flows, residual risks, and the required operator controls, read the [Threat Model](docs/threat-model.md).
