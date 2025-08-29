from pydantic import BaseModel


class RejectUserRequest(BaseModel):
    target_user_id: str
