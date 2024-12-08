from typing import List, Optional

from pydantic import BaseModel, Field

from ment_api.common.custom_object_id import CustomObjectId
from ment_api.models.verification_example_media import VerificationExampleMedia


class Task(BaseModel):
    id: CustomObjectId = Field(alias="_id", serialization_alias="id")
    task_title: str
    task_category_id: Optional[CustomObjectId] = None
    task_verification_media_type: str
    display_name: str
    task_verification_requirements: List[dict]
    task_verification_example_sources: Optional[List[VerificationExampleMedia]] = Field(
        default=[]
    )
    task_location: Optional[dict] = None
    task_locations: Optional[List[dict]] = Field(default=[])
    task_description: Optional[str] = ""
    hidden: Optional[bool] = False
    live_user_count: Optional[int] = 0
    verification_count: Optional[int] = 0
    can_pin_user_ids: Optional[List[CustomObjectId]] = Field(default=[])
