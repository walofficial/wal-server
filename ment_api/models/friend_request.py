from pydantic import BaseModel, Field
from enum import Enum
from datetime import datetime, timezone
from ment_api.common.custom_object_id import CustomObjectId


class FriendRequestStatus(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class FriendRequest(BaseModel):
    id: CustomObjectId = Field(alias="_id", serialization_alias="id")
    sender_id: CustomObjectId
    receiver_id: CustomObjectId
    status: FriendRequestStatus
    created_at: datetime
    updated_at: datetime


class FriendRequestSent(BaseModel):
    target_user_id: CustomObjectId
