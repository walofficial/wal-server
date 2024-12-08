from typing import List, Optional
from pydantic import BaseModel, Field
from ment_api.common.custom_object_id import CustomObjectId
from ment_api.models.user import User
from ment_api.models.task import Task


class PublicVerification(BaseModel):
    id: CustomObjectId = Field(alias="_id", serialization_alias="id")
    match_id: Optional[CustomObjectId]
    assignee_user_id: CustomObjectId
    task_id: CustomObjectId
    type: str
    state: str
    transcode_job_name: Optional[str] = None
    verified_media_playback: Optional[dict[str, str]] = None
    verified_image: Optional[str] = None
    is_public: bool
    assignee_user: User
    task: Task
