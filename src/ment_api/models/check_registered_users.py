from typing import List
from pydantic import BaseModel


class CheckRegisteredUsersRequest(BaseModel):
    phone_numbers: List[str]
