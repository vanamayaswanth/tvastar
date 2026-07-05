from fastapi import APIRouter

router = APIRouter(tags=["leads"])


@router.get("/")
async def list_leads():
    return []


@router.post("/")
async def create_lead():
    raise NotImplementedError


@router.get("/{lead_id}")
async def get_lead(lead_id: str):
    raise NotImplementedError
