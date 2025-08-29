from pydantic import BaseModel, Field
from ment_api.common.custom_object_id import CustomObjectId
from typing import Optional


class ChatMessage(BaseModel):
    id: CustomObjectId = Field(alias="_id", serialization_alias="id")
    author_id: str
    room_id: str
    recipient_id: str
    message: Optional[str] = ""
    encrypted_content: Optional[str]
    nonce: Optional[str]
    message_state: str
    sent_date: Optional[str] = None
    # FE passes it sometimes
    temporary_id: Optional[str] = None

    def __init__(self, **data):
        super().__init__(**data)
        if self.id and not self.sent_date:
            self.sent_date = str(self.id.generation_time)
