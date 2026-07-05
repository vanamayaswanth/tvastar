"""CRM inbound webhook receiver (per-tenant adapter routing)."""
from fastapi import APIRouter

router = APIRouter(prefix="/webhooks/crm", tags=["crm"])
