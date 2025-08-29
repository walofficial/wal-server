from datetime import datetime
from pydantic import BaseModel, Field

from ment_api.common.custom_object_id import CustomObjectId


class Like(BaseModel):
    id: CustomObjectId = Field(alias="_id", serialization_alias="id")
    user_id: str
    verification_id: str
    created_at: datetime
    feed_id: CustomObjectId
