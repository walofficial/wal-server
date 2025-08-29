from pydantic import BaseModel
from ment_api.models.user import User


class LiveUser(BaseModel):
    user: User
    is_friend: bool
