from typing import Optional

from pydantic import BaseModel, Field


class MediaGeneratedPost(BaseModel):
    external_id: str = Field(description="External article id of the selected post")
    title: str = Field(description="Title for the fact-checkable media post (Georgian)")
    content: str = Field(
        description="Content for the post (Georgian), concise but with claims suitable for fact-check"
    )
    big_image_url: Optional[str] = Field(
        default=None, description="Big image URL to use for the post if available"
    )


class MediaGeneratedPostResponse(BaseModel):
    post: MediaGeneratedPost
