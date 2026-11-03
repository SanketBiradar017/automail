from typing import List

from fastapi import APIRouter
from fastapi.responses import Response
from pydantic import BaseModel, Field

from backend import avatar_service

router = APIRouter(prefix="/api/avatars", tags=["Avatars"])


class ResolveIn(BaseModel):
    emails: List[str] = Field(default_factory=list, max_length=60)


@router.post("/resolve")
def resolve(body: ResolveIn):
    """Which of these addresses have a photo? {email: bool}. Never errors on
    missing photos or Google API failures - those mean "use initials"."""
    return {"photos": avatar_service.resolve(body.emails)}


@router.get("/photo")
def photo(email: str):
    """Streams the cached photo; 404 (cacheable) when there is none."""
    result = avatar_service.photo_bytes(email)
    if not result:
        return Response(status_code=404, headers={"Cache-Control": "private, max-age=900"})
    data, ctype = result
    return Response(content=data, media_type=ctype, headers={"Cache-Control": "private, max-age=86400"})
