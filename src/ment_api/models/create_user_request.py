from typing import Optional, List

from pydantic import BaseModel, Field


class CreateUserRequest(BaseModel):
    city: Optional[str]
    date_of_birth: Optional[str]
    email: str
    # We set gender later on register phase, thata why it is optional here
    gender: Optional[str]
    external_user_id: str
    interests: Optional[List[str]]
    # We set names later on register phase, thata why it is optional here
    username: Optional[str]
    photos: Optional[List[dict]] = Field(
        default=[
            {
                "image_url": [
                    "https://storage.googleapis.com/ment-verification/video-verifications/raw/placeholder1.png"
                ]
            }
        ]
    )
    phone_number: str
    preferred_content_language: Optional[str] = "georgian"
