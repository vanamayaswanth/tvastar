"""Notification routes and WebSocket gateway."""
from fastapi import APIRouter

router = APIRouter(prefix="/notifications", tags=["notifications"])
