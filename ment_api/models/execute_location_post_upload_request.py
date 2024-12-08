from pydantic import BaseModel

from ment_api.common.custom_object_id import CustomObjectId


class ExecuteLocationPostUploadRequest(BaseModel):
    task_id: CustomObjectId
    assignee_user_id: CustomObjectId
    content_type: str
    file_name: str
    file_extension: str
    verification_id: CustomObjectId
    should_transcode: bool
