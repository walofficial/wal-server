from pydantic import BaseModel


class SocialMediaScrapeEvent(BaseModel):
    verification_id: str
