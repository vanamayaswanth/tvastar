from fastapi import APIRouter

router = APIRouter(tags=["knowledge"])


@router.post("/upload")
async def upload_document():
    raise NotImplementedError


@router.get("/")
async def list_documents():
    return []


@router.get("/search")
async def search_knowledge(q: str = ""):
    return []
