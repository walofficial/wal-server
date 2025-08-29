from typing import List, Optional

from pydantic import BaseModel, Field

from ment_api.common.custom_object_id import CustomObjectId


class Feed(BaseModel):
    id: CustomObjectId = Field(alias="_id", serialization_alias="id")
    feed_title: str
    feed_category_id: Optional[CustomObjectId] = None
    display_name: str
    feed_location: Optional[dict] = None
    feed_locations: Optional[List[dict]] = Field(default=[])
    feed_description: Optional[str] = ""
    hidden: Optional[bool] = False
    live_user_count: Optional[int] = 0
    verification_count: Optional[int] = 0
    no_restrictions: Optional[bool] = False
    feed_language_code: Optional[str] = "ka"
