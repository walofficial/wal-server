from pydantic import BaseModel, Field
from bson import ObjectId
from typing import Literal
from ment_api.common.custom_object_id import CustomObjectId
from typing import Optional


class ChatMessage(BaseModel):
    id: CustomObjectId = Field(alias="_id", serialization_alias="id")
    author_id: str
    room_id: str
    message: Optional[str] = ""
    encrypted_content: Optional[str] = None
    nonce: Optional[str] = None
    message_state: str
