from pydantic import BaseModel, Field
from ment_api.common.custom_object_id import CustomObjectId
from typing import Optional


class ChatMessage(BaseModel):
    id: CustomObjectId = Field(alias="_id", serialization_alias="id")
    author_id: str
    room_id: CustomObjectId
    recipient_id: str
    encrypted_content: Optional[str] = None
    nonce: Optional[str] = None
    message_state: str
    sent_date: Optional[str] = None
    # FE passes it sometimes
    temporary_id: Optional[str] = None
    # Plain text content for virtual users (AI characters) - no encryption
    plain_content: Optional[str] = None

    def __init__(self, **data):
        super().__init__(**data)
        if self.id and not self.sent_date:
            self.sent_date = str(self.id.generation_time)
