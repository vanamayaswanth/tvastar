from fastapi import APIRouter, Request

router = APIRouter(tags=["whatsapp"])


@router.post("/webhook")
async def whatsapp_webhook(request: Request):
    """Inbound WhatsApp webhook from Meta."""
    raise NotImplementedError


@router.get("/webhook")
async def verify_webhook(hub_verify_token: str = "", hub_challenge: str = ""):
    """Webhook verification challenge."""
    return hub_challenge
