from fastapi import APIRouter

router = APIRouter(tags=["calls"])


@router.get("/")
async def list_calls():
    return []


@router.get("/{call_id}")
async def get_call(call_id: str):
    raise NotImplementedError
