from typing import List, Optional
from pydantic import BaseModel, Field
from datetime import datetime, timezone
from ment_api.common.custom_object_id import CustomObjectId
from ment_api.models.user import User
from ment_api.models.task import Task


class LocationFeedPost(BaseModel):
    id: CustomObjectId = Field(alias="_id", serialization_alias="id")
    assignee_user_id: CustomObjectId
    task_id: CustomObjectId
    state: str
    transcode_job_name: Optional[str] = None
    verified_media_playback: Optional[dict[str, str]] = None
    verified_image: Optional[str] = None
    text_content: Optional[str] = None
    is_public: bool = False
    assignee_user: Optional[User] = None
    last_modified_date: datetime
    task: Optional[Task] = None
