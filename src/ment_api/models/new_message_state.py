from pydantic import BaseModel

from ment_api.common.custom_object_id import CustomObjectId
from ment_api.models.message_state import MessageState


class NewMessageState(BaseModel):
    id: CustomObjectId
    state: MessageState
