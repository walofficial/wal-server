from pydantic import BaseModel

from ment_api.common.custom_object_id import CustomObjectId


class DeleteVerificationExample(BaseModel):
    task_id: CustomObjectId
    example_id: str
