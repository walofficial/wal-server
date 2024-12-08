from typing import List, Optional
from pydantic import BaseModel, Field

from ment_api.common.custom_object_id import CustomObjectId
from ment_api.models.task import Task


class Affiliation(BaseModel):
    name: str
    icon_url: str


class User(BaseModel):
    id: CustomObjectId = Field(alias="_id", serialization_alias="id")
    city: Optional[str] = Field(default=None)
    date_of_birth: Optional[str] = Field(default=None)
    email: str
    phone_number: str
    username: Optional[str] = Field(default=None)
    gender: Optional[str] = Field(default=None)
    external_user_id: str
    interests: Optional[List[str]] = Field(default=None)
    location: Optional[dict] = Field(default=None)
    photos: List[dict]
    selected_task: Optional[Task] = None
    is_photos_hidden: Optional[bool] = Field(default=None)
    is_in_waitlist: Optional[bool] = False
    affiliated: Optional[Affiliation] = Field(default=None)
