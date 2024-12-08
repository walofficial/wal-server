from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel, Field
from bson import ObjectId

from ment_api.common.custom_object_id import CustomObjectId


class Like(BaseModel):
    id: CustomObjectId = Field(alias="_id", serialization_alias="id")
    user_id: CustomObjectId
    verification_id: CustomObjectId
    created_at: datetime
    task_id: CustomObjectId
