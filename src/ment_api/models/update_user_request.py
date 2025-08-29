from typing import Optional, List

from pydantic import BaseModel


class UpdateUserRequest(BaseModel):
    username: Optional[str] = None
    date_of_birth: Optional[str] = None
    # We set gender later on register phase, thata why it is optional here
    gender: Optional[str] = None
    # We set names later on register phase, thata why it is optional here
    photos: Optional[List[dict]] = None
    preferred_content_language: Optional[str] = None
    preferred_news_feed_id: Optional[str] = None
    preferred_fact_check_feed_id: Optional[str] = None
