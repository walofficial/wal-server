from pydantic import BaseModel, Field
from typing import Optional
from ment_api.common.custom_object_id import CustomObjectId


class FeedCategory(BaseModel):
    id: CustomObjectId = Field(alias="_id", serialization_alias="id")
    title: str
    display_name: str
    hidden: Optional[bool] = None
    order: Optional[int] = None
