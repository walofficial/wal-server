from pydantic import BaseModel
from ment_api.common.custom_object_id import CustomObjectId


class UpdateVerificationVisibilityRequest(BaseModel):
    verification_id: CustomObjectId
    is_public: bool
