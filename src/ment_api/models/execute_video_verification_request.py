from pydantic import BaseModel

from ment_api.common.custom_object_id import CustomObjectId


class ExecuteVideoVerificationRequest(BaseModel):
    match_id: CustomObjectId
    assignee_user_id: str
    content_type: str
    file_name: str
    file_extension: str
