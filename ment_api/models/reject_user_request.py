from pydantic import BaseModel

from ment_api.common.custom_object_id import CustomObjectId


class RejectUserRequest(BaseModel):
    target_user_id: CustomObjectId
