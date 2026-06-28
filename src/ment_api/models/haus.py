"""Haus domain models and shared constants (booking state machine + push payload types)."""

from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class HausBookingStatus(str, Enum):
    REQUESTED = "requested"
    PAYMENT_PENDING = "payment_pending"
    PROOF_UPLOADED = "proof_uploaded"
    APPROVED = "approved"
    REJECTED = "rejected"
    WAITLISTED = "waitlisted"
    CHECKED_IN = "checked_in"


# Push / client `data.type` values (keep in sync with wal-react-native)
HAUS_PUSH_JOIN_REQUEST = "haus_join_request"
HAUS_PUSH_PAYMENT_PROOF = "haus_payment_proof"
HAUS_PUSH_BOOKING_APPROVED = "haus_booking_approved"
HAUS_PUSH_BOOKING_REJECTED = "haus_booking_rejected"
HAUS_PUSH_CHECKED_IN = "haus_checked_in"


class HausProfileUpsert(BaseModel):
    invite_code: str = Field(min_length=4, max_length=64)
    instagram_handle: str = Field(min_length=1, max_length=128)
    age_confirmed: bool = True


class HausProfileResponse(BaseModel):
    external_user_id: str
    invite_code: str
    instagram_handle: str
    age_confirmed: bool
    is_host: bool = False
    completed_at: datetime


class HausHouseResponse(BaseModel):
    id: str
    host_external_user_id: str
    title: str
    neighborhood: str
    vibe_tag: str
    capacity: int
    payment_instructions: str
    image_urls: List[str] = Field(default_factory=list)
    bathroom_note: Optional[str] = None
    created_at: datetime


class HausEventResponse(BaseModel):
    id: str
    house_id: str
    title: str
    starts_at: datetime
    ends_at: datetime
    price_gel: float
    spots_total: int
    spots_taken: int
    midnight_drop_percent: int = 40
    qr_activate_hours_before: float = 1.0
    created_at: datetime


class HausBookingResponse(BaseModel):
    id: str
    event_id: str
    guest_external_user_id: str
    status: HausBookingStatus
    booking_code: str
    payment_proof_url: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class HausBookingProofBody(BaseModel):
    proof_image_url: str = Field(min_length=8, max_length=2048)


class HausCheckInBody(BaseModel):
    token: str = Field(min_length=10)


class HausTicketResponse(BaseModel):
    token: Optional[str] = None
    active: bool
    valid_from: Optional[datetime] = None
    valid_until: Optional[datetime] = None
    message: Optional[str] = None


class HausEventDetailResponse(BaseModel):
    event: HausEventResponse
    house: HausHouseResponse
