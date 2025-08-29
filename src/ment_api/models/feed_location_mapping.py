from typing import List, Set, Tuple

from pydantic import BaseModel, Field

from ment_api.common.custom_object_id import CustomObjectId

Lat = float
Lng = float


class Location(BaseModel):
    name: str
    address: str
    location: Tuple[Lat, Lng]


class FeedLocationMapping(BaseModel):
    id: CustomObjectId = Field(alias="_id", serialization_alias="id")
    feed_ids: Set[CustomObjectId] = set()
    locations: List[Location] = []
    radius: float = 300
