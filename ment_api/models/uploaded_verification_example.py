from pydantic import BaseModel


class UploadedVerificationExample(BaseModel):
    id: str
    name: str
    media_type: str
