from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field
from ment_api.common.custom_object_id import CustomObjectId
from ment_api.models.user import User


class NotificationType(str, Enum):
    POKE = "poke"
    MESSAGE = "message"
    VERIFICATION_LIKE = "verification_like"
    IMPRESSION = "impression"
    COMMENT_TAG = "comment_tag"
    COMMENT_REACTION = "comment_reaction"
    HAUS_JOIN_REQUEST = "haus_join_request"
    HAUS_PAYMENT_PROOF = "haus_payment_proof"
    HAUS_BOOKING_APPROVED = "haus_booking_approved"
    HAUS_BOOKING_REJECTED = "haus_booking_rejected"
    HAUS_CHECKED_IN = "haus_checked_in"


class Notification(BaseModel):
    id: CustomObjectId = Field(alias="_id", serialization_alias="id")
    from_user_id: str
    to_user_id: str
    type: NotificationType
    created_at: datetime
    read: bool = False
    verification_id: Optional[CustomObjectId] = None
    message: Optional[str] = None
    count: Optional[int] = None
    from_user: Optional[User] = None  # populated for enriched responses
    # Optional fields for reaction notifications
    comment_id: Optional[CustomObjectId] = None
    reaction_type: Optional[str] = None
    # Haus (optional)
    haus_booking_id: Optional[CustomObjectId] = None
    haus_event_id: Optional[CustomObjectId] = None
    haus_house_id: Optional[CustomObjectId] = None


class NotificationResponse(BaseModel):
    notification: Notification
    from_user: User
