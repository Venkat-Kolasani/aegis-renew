"""Phase 0 placeholders for agent routes."""

from fastapi import APIRouter, status
from pydantic import BaseModel, Field

router = APIRouter(tags=["agent"])


class RankRequest(BaseModel):
    """Request body for ranking scanned domains."""

    domain_ids: list[int] = Field(min_length=1)


class PlaceholderResponse(BaseModel):
    """Standard response for a route not implemented in Phase 0."""

    detail: str


@router.post("/agent/rank", response_model=PlaceholderResponse, status_code=status.HTTP_501_NOT_IMPLEMENTED)
def rank_domains(_: RankRequest) -> PlaceholderResponse:
    """Rank scanned domains when the agent service is implemented."""
    return PlaceholderResponse(detail="Domain ranking is not implemented yet.")
