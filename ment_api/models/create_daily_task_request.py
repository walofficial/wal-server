from typing import Optional, List

from pydantic import BaseModel, Field

from ment_api.common.custom_object_id import CustomObjectId


class CreateDailyTaskRequest(BaseModel):
    task_title: str
    display_name: str
    task_location: dict
    task_verification_media_type: str
    task_description: str
    task_category_id: CustomObjectId
    task_verification_requirements: List[dict]
