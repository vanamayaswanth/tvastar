"""Billing and usage tracking routes (Super_Admin)."""
from fastapi import APIRouter

router = APIRouter(prefix="/billing", tags=["billing"])
