# Security Policy

## Supported versions

| Version | Supported |
|---------|-----------|
| 0.11.x  | ✅ Current |
| 0.10.x  | ✅ Security fixes only |
| < 0.10  | ❌ No longer supported |

## Reporting a vulnerability

**Please do not report security vulnerabilities through public GitHub issues.**

Email **vanamayaswanth@gmail.com** with the subject line `[SECURITY] tvastar — <short description>`.

Include:
- A description of the vulnerability and its potential impact
- Steps to reproduce (minimal reproduction using `MockModel` + `VirtualSandbox` if possible)
- Any suggested mitigations

You will receive a response within **72 hours**. If confirmed, a patched release will
be published within 14 days for critical issues and 30 days for others.

## Scope

Tvastar includes a **sandbox execution layer** (`LocalSandbox`, `VirtualSandbox`) and a
**credential filter** (`CredentialFilter`). Security issues in these components are
treated as highest priority.

In-scope:
- Sandbox escape or bypass in `LocalSandbox` / `VirtualSandbox`
- `CredentialFilter` patterns that fail to strip secrets from subprocess environments
- `SecurityPolicy` allowlist bypass
- Prompt-injection vulnerabilities in the harness layer
- Supply-chain issues (malicious dependencies, CI poisoning)

Out of scope:
- Model behaviour (hallucinations, jailbreaks against the LLM itself)
- Issues in third-party optional dependencies (`anthropic`, `openai`, `fastapi`)
- Issues requiring an already-compromised host

## Security design notes

- **`VirtualSandbox`** runs code in-process in a restricted namespace. It is not a
  true isolation boundary — it is a convenience sandbox for trusted code. For
  untrusted model-generated code, use `LocalSandbox` with a tight `SecurityPolicy`
  or a container-based adapter.
- **`CredentialFilter`** removes env vars matching secret-looking patterns before any
  subprocess is spawned. Default patterns cover `*_KEY`, `*_TOKEN`, `*_SECRET`,
  `*_PASSWORD`, `*_PASS`, `*_CREDENTIAL`, `*_CREDENTIALS`.
- The **`prompt_injection` detector** surfaces suspicious tool output as a `WARNING`
  finding. It is a detection / mitigation layer, not a prevention layer.
