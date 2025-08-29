from typing import List, Optional
from pydantic import BaseModel, Field


class Affiliation(BaseModel):
    name: str
    icon_url: str


class UserPhoto(BaseModel):
    image_url: List[str]
    # Some old user's doesn't have those values, maybe we should do migration for that.
    image_id: Optional[str] = None
    blur_hash: Optional[str] = None


class User(BaseModel):
    id: str = Field(alias="external_user_id", serialization_alias="id")
    city: Optional[str] = ""
    date_of_birth: Optional[str]
    email: str
    phone_number: str
    username: Optional[str]
    gender: Optional[str]
    external_user_id: str
    interests: Optional[List[str]]
    photos: List[UserPhoto]
    is_in_waitlist: Optional[bool] = False
    can_summarify: Optional[bool] = False
    # Preferred feeds and content language (derived from country detection), shouldn't be optional
    # Default values exist for non migrated users
    preferred_news_feed_id: str = "687960db5051460a7afd6e63"
    preferred_fact_check_feed_id: str = "67bb256786841cb3e7074bcd"
    preferred_content_language: str = "georgian"
