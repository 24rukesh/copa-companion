"""Request schemas — validation happens here, at the trust boundary."""

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=500)
    section: int | None = Field(default=None, ge=1, le=999)
    # distance/ETA computed client-side from the fan's position; raw
    # coordinates deliberately never reach the server (privacy)
    distance_km: float | None = Field(default=None, ge=0, le=1000)
    eta_min: int | None = Field(default=None, ge=0, le=6000)


class TicketRequest(BaseModel):
    text: str = Field(min_length=1, max_length=300)
