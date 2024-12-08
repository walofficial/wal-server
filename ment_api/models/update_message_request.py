from typing import List

from pydantic import BaseModel

from ment_api.models.new_message_state import NewMessageState


class UpdateMessageRequest(BaseModel):
    messages: List[NewMessageState]
