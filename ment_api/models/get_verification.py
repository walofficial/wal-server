from typing import Optional
from fastapi import Query
from pydantic import BaseModel, Field
from typing import Annotated
from ment_api.common.custom_object_id import CustomObjectId


class GetVerificationRequest(BaseModel):
    verification_id: Optional[Annotated[CustomObjectId, Query()]] = None
    user_id: Optional[Annotated[CustomObjectId, Query()]] = None
    match_id: Optional[Annotated[CustomObjectId, Query()]] = None
