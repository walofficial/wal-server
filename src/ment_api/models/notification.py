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


class NotificationResponse(BaseModel):
    notification: Notification
    from_user: User
