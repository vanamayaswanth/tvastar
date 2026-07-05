from fastapi import APIRouter

router = APIRouter(tags=["tenants"])


@router.get("/")
async def list_tenants():
    return []


@router.post("/")
async def create_tenant():
    raise NotImplementedError
