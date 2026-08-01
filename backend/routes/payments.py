"""Phase 0 placeholders for payment routes."""

from fastapi import APIRouter, status
from pydantic import BaseModel, Field, HttpUrl

router = APIRouter(tags=["payments"])


class MandateRequest(BaseModel):
    """Request body for setting up a renewal mandate."""

    domain_id: int = Field(gt=0)
    merchant_name: str = Field(min_length=1)
    merchant_url: HttpUrl
    merchant_country: str = Field(min_length=2, max_length=2)
    cap_amount: float = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)
    frequency: str = "yearly"


class ExecuteRequest(BaseModel):
    """Request body for a server-derived renewal payment."""

    domain_id: int = Field(gt=0)


class PlaceholderResponse(BaseModel):
    """Standard response for a route not implemented in Phase 0."""

    detail: str


@router.post("/payments/mandate", response_model=PlaceholderResponse, status_code=status.HTTP_501_NOT_IMPLEMENTED)
def create_mandate(_: MandateRequest) -> PlaceholderResponse:
    """Create a mandate when the Prava integration is implemented."""
    return PlaceholderResponse(detail="Mandate setup is not implemented yet.")


@router.post("/payments/execute", response_model=PlaceholderResponse, status_code=status.HTTP_501_NOT_IMPLEMENTED)
def execute_payment(_: ExecuteRequest) -> PlaceholderResponse:
    """Execute a covered renewal when the payment service is implemented."""
    return PlaceholderResponse(detail="Payment execution is not implemented yet.")
