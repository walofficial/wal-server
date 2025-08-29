from pydantic import BaseModel


class VideoProcessorEvent(BaseModel):
    """Event for video processing tasks using Gemini API"""

    verification_id: str
    youtube_url: str
    external_user_id: str
    video_title: str = "TITLE NOT FOUND"
