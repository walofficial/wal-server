from typing import Optional, List

from pydantic import BaseModel, Field


class CreateUserRequest(BaseModel):
    city: Optional[str] = Field(default=None)
    date_of_birth: Optional[str]
    email: str
    # We set gender later on register phase, thata why it is optional here
    gender: Optional[str]
    external_user_id: str
    interests: Optional[List[str]] = Field(default=None)
    # We set names later on register phase, thata why it is optional here
    username: Optional[str]
    photos: Optional[List[dict]] = Field(
        default=[
            {
                "image_url": [
                    "https://storage.googleapis.com/ment-verification/video-verifications/raw/png-clipart-user-profile-computer-icons-login-user-avatars-monochrome-black-thumbnail.png"
                ]
            }
        ]
    )
    # We set gender preference later on register phase, thata why it is optional here
    profile_image: Optional[str] = Field(default=None)
    phone_number: str
    is_photos_hidden: Optional[bool] = Field(default=None)
