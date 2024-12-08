from typing import List
from pydantic import BaseModel

from ment_api.common.custom_object_id import CustomObjectId


class CheckRegisteredUsersRequest(BaseModel):
    phone_numbers: List[str]
