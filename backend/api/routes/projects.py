from fastapi import APIRouter

router = APIRouter(tags=["projects"])


@router.get("/")
async def list_projects():
    return []


@router.post("/")
async def create_project():
    raise NotImplementedError
