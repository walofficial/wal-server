from typing import List

from pydantic import BaseModel
from typing import Optional


class VerificationExampleMedia(BaseModel):
    id: str
    name: str
    media_type: str
    playback: Optional[dict[str, str]]
    thumbnail_url: Optional[str]
    image_media_url: Optional[str]
