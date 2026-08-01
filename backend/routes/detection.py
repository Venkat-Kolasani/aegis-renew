"""Phase 0 placeholders for detection routes."""

from fastapi import APIRouter, status
from pydantic import BaseModel, Field

router = APIRouter(tags=["detection"])


class ScanRequest(BaseModel):
    """Request body for a domain scan."""

    domain: str = Field(min_length=1, max_length=253)


class PlaceholderResponse(BaseModel):
    """Standard response for a route not implemented in Phase 0."""

    detail: str


@router.get("/domains", response_model=PlaceholderResponse, status_code=status.HTTP_501_NOT_IMPLEMENTED)
def list_domains() -> PlaceholderResponse:
    """Return stored domains when detection persistence is implemented."""
    return PlaceholderResponse(detail="Domain listing is not implemented yet.")


@router.post("/scan", response_model=PlaceholderResponse, status_code=status.HTTP_501_NOT_IMPLEMENTED)
def scan_domain(_: ScanRequest) -> PlaceholderResponse:
    """Scan a domain when the detection engine is implemented."""
    return PlaceholderResponse(detail="Domain scanning is not implemented yet.")
