"""User management routes (Tenant_Admin creates salespersons/managers)."""
from fastapi import APIRouter

router = APIRouter(prefix="/users", tags=["users"])

# ponytail: fastapi-users handles most user ops; these are custom overrides only
