from typing import List, Literal, Optional

from pydantic import BaseModel, Field

from ment_api.common.custom_object_id import CustomObjectId


class Feed(BaseModel):
    id: CustomObjectId = Field(alias="_id", serialization_alias="id")
    feed_title: str
    feed_category_id: Optional[CustomObjectId] = None
    display_name: str
    feed_location: Optional[dict] = None
    feed_locations: Optional[List[dict]] = Field(default=[])
    hidden: Optional[bool] = False
    no_restrictions: Optional[bool] = False
    feed_country_code: Optional[str] = "ka"
    # Means user should be near this location to be returned
    nearby_feed: Optional[bool] = False
    feed_type: Optional[Literal["news", "fact_check", "location"]] = "news"
