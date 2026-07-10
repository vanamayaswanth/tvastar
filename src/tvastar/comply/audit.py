"""Core audit function for the tvastar.comply package.

Pure function wrapping ComplianceVerifier.verify() with framework routing,
PII verification, and structured result packaging.

Fault isolation: catches all exceptions and reports them as NON_COMPLIANT
with remediation text — never raises into the calling agent loop.
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from .models import AuditResult

if TYPE_CHECKING:
    from .frameworks import FrameworkRegistry


def _get_loop_name(loop: Any) -> str:
    """Extract a loop name safely, falling back to type name."""
    if loop is None:
        return "<None>"
    name = getattr(loop, "name", None)
    if name:
        return str(name)
    config = getattr(loop, "_config", None)
    if config:
        cfg_name = getattr(config, "name", None)
        if cfg_name:
            return str(cfg_name)
    return f"<{type(loop).__name__}>"


def audit_compliance(
    loop: Any,
    *,
    framework: str | None = None,
    registry: "FrameworkRegistry | None" = None,
) -> AuditResult:
    """Execute all compliance checks for a Loop. Pure function.

    Wraps ComplianceVerifier.verify() and adds framework routing,
    PII verification, and structured result packaging.

    Fault isolation: catches all exceptions from checks and reports
    them as NON_COMPLIANT with remediation text — never raises into
    the calling agent loop.
    """
    resolved_framework = framework if framework is not None else "EU_AI_Act"

    try:
        # Validate loop
        from tvastar.loop import Loop

        if loop is None:
            return AuditResult(
                loop_name="<None>",
                status="NON_COMPLIANT",
                framework=resolved_framework,
                checks=[],
                pii_verification=None,
                remediation=["Invalid loop: expected a Loop instance, got None"],
            )

        if not isinstance(loop, Loop):
            return AuditResult(
                loop_name=_get_loop_name(loop),
                status="NON_COMPLIANT",
                framework=resolved_framework,
                checks=[],
                pii_verification=None,
                remediation=[f"Invalid loop: expected a Loop instance, got {type(loop).__name__}"],
            )

        loop_name = _get_loop_name(loop)

        # Set up registry
        if registry is None:
            from .frameworks import FrameworkRegistry as _FR

            registry = _FR()

        # Get checks for the requested framework
        checks_callables = registry.get_checks(framework)

        # Execute each check against the loop
        from tvastar.compliance import ArticleCheck

        article_checks: list[ArticleCheck] = []
        for check in checks_callables:
            try:
                result = check(loop)
                if isinstance(result, ArticleCheck):
                    article_checks.append(result)
                else:
                    # If check returns something else, wrap it
                    article_checks.append(
                        ArticleCheck(
                            article=getattr(check, "article", "Unknown"),
                            feature=getattr(check, "feature", "Unknown"),
                            passed=bool(getattr(result, "passed", False)),
                            remediation=getattr(result, "remediation", ""),
                        )
                    )
            except Exception as exc:
                article_checks.append(
                    ArticleCheck(
                        article=getattr(check, "article", "Unknown"),
                        feature=getattr(check, "feature", "Unknown"),
                        passed=False,
                        remediation=f"Check failed with error: {exc}",
                    )
                )

        # Determine status from check results
        all_pass = all(c.passed for c in article_checks)
        status = "COMPLIANT" if all_pass else "NON_COMPLIANT"

        # Collect remediation for failed checks
        remediation = [c.remediation for c in article_checks if not c.passed and c.remediation]

        # PII verification: run if assurance policy has vault configured
        pii_verification = None
        spec = getattr(loop, "_base_spec", None)
        policy = getattr(spec, "assurance", None) if spec else None
        vault = getattr(policy, "vault", None) if policy else None

        if vault is not None:
            log = getattr(policy, "log", None)
            if log is not None:
                try:
                    from .vault_verify import verify_pii_protection

                    # Get the most recent receipt from the trust log
                    entries = getattr(log, "_entries", [])
                    if entries:
                        latest_receipt = entries[-1]
                        pii_verification = verify_pii_protection(
                            latest_receipt, vault_configured=True
                        )
                except Exception:
                    pass  # PII verification is best-effort

        return AuditResult(
            loop_name=loop_name,
            status=status,
            framework=resolved_framework,
            checks=article_checks,
            pii_verification=pii_verification,
            remediation=remediation,
        )

    except Exception as exc:
        # Fault isolation: never raise into the calling agent loop
        return AuditResult(
            loop_name=_get_loop_name(loop),
            status="NON_COMPLIANT",
            framework=resolved_framework,
            checks=[],
            pii_verification=None,
            remediation=[f"Audit failed with internal error: {exc}"],
        )
