from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from ment_api.common.custom_object_id import CustomObjectId


class RateRequest(BaseModel):
    rate_type: str


class TaskRating(BaseModel):
    id: CustomObjectId = Field(alias="_id", serialization_alias="id")
    user_id: CustomObjectId
    task_id: CustomObjectId
    rate_type: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
