from pydantic import BaseModel
from ment_api.models.user import User
from typing import List as TypeList


class AnonListEntry(BaseModel):
    user: User
    is_friend: bool
