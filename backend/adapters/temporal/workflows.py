"""Temporal workflow definitions."""
from uuid import UUID


class LeadWorkflow:
    """Orchestrates lead from creation through qualification to site visit."""

    async def run(self, lead_id: UUID, tenant_id: UUID, project_id: UUID) -> dict:
        raise NotImplementedError


class RNRRetryWorkflow:
    """Manages retry scheduling with cooling-off and call window enforcement."""

    async def run(self, lead_id: UUID, retry_policy: dict) -> dict:
        raise NotImplementedError


class CRMSyncWorkflow:
    """Syncs outcomes back to CRM with exponential backoff on failure."""

    async def run(self, sync_payload: dict) -> dict:
        raise NotImplementedError
