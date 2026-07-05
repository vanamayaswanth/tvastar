from fastapi import APIRouter

router = APIRouter(tags=["auth"])


@router.post("/login")
async def login():
    raise NotImplementedError


@router.post("/refresh")
async def refresh_token():
    raise NotImplementedError
