from typing import List, Optional
from datetime import datetime, timezone
from pydantic import BaseModel, Field
from ment_api.models.user import User

from ment_api.common.custom_object_id import CustomObjectId


class Verification(BaseModel):
    id: CustomObjectId = Field(alias="_id", serialization_alias="id")
    assignee_user_id: CustomObjectId
    task_id: CustomObjectId
    file_name: Optional[str] = None
    file_extension: Optional[str] = None
    file_content_type: Optional[str] = None
    verified_media_playback: Optional[dict[str, str]] = None
    verified_image: Optional[str] = None
    verification_trials: List[dict] = []
    type: str = None
    state: str
    is_public: Optional[bool] = False
    last_modified_date: datetime
    assignee_user: User = None
    text_content: Optional[str] = None
