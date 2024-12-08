from typing import Optional, List

from pydantic import BaseModel, Field


class UpdateUserRequest(BaseModel):
    username: Optional[str] = None
    date_of_birth: Optional[str] = None
    # We set gender later on register phase, thata why it is optional here
    gender: Optional[str] = None
    # We set names later on register phase, thata why it is optional here
    photos: Optional[List[dict]] = Field(
        default=[
            {
                "image_url": [
                    "https://storage.googleapis.com/ment-verification/video-verifications/raw/png-clipart-user-profile-computer-icons-login-user-avatars-monochrome-black-thumbnail.png"
                ]
            }
        ]
    )
