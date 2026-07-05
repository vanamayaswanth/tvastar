"""Temporal worker process — registers workflows and activities."""


async def run_worker() -> None:
    """Connect to Temporal and register all workflows + activities."""
    # ponytail: Will use temporalio Worker with LeadWorkflow, RNRRetryWorkflow, CRMSyncWorkflow
    raise NotImplementedError


if __name__ == "__main__":
    import asyncio
    asyncio.run(run_worker())
