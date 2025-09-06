from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from ment_api.common.custom_object_id import CustomObjectId
from ment_api.models.image_with_dims import ImageWithDims
from ment_api.models.feed import Feed
from ment_api.models.user import User
from ment_api.models.link_preview_data import LinkPreviewData
from ment_api.models.fact_checking_models import FactCheckingResult


class FactCheckStatus(str, Enum):
    IDLE = "IDLE"
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class AIVideoSummaryStatus(str, Enum):
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    FAILED_PREPROCESSING = "FAILED_PREPROCESSING"
    SKIPPED_DURATION = "SKIPPED_DURATION"
    METADATA_INCOMPLETE = "METADATA_INCOMPLETE"
    METADATA_FETCH_FAILED = "METADATA_FETCH_FAILED"
    PROCESSING_ERROR = "PROCESSING_ERROR"
    NOT_ELIGIBLE = "NOT_ELIGIBLE"


class MetadataStatus(str, Enum):
    IDLE = "IDLE"
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class SocialMediaScrapeStatus(str, Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class Source(BaseModel):
    title: str
    uri: str
    domain: Optional[str] = None


class AISummaryPoint(BaseModel):
    text: str
    timestamp: Optional[str] = None
    link_to_timestamp: Optional[str] = None


class AIVideoSummary(BaseModel):
    title: str = Field(description="Title of the video")
    relevant_statements: List[AISummaryPoint] = Field(
        description="List of relevant statements from the audio transcript"
    )
    interesting_facts: List[str] = Field(
        description="List of interesting facts from the audio transcript"
    )
    did_you_know: List[str] = Field(
        description="List of did you know facts from the audio transcript"
    )
    short_summary: str = Field(description="Short summary of the audio transcript")
    statements: Optional[List[str]] = Field(
        description="List of statements from the audio transcript which can be fact checked in English"
    )


class ScreenshotInfo(BaseModel):
    url: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None


class SocialMediaScrapeDetails(BaseModel):
    platform: str
    url: str
    content: Optional[str] = None
    author_name: Optional[str] = None
    post_date: Optional[datetime] = None
    image_urls: Optional[List[str]] = Field(default_factory=list)
    screenshot: Optional[ScreenshotInfo] = None
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)
    author_profile_image: Optional[str] = None


class ExternalVideo(BaseModel):
    url: str
    platform: str = "youtube"

    @property
    def video_id(self) -> str:
        """Extract video ID from YouTube URL."""
        if "youtube.com" in self.url or "youtu.be" in self.url:
            # Handle both youtube.com and youtu.be URLs
            if "youtu.be" in self.url:
                return self.url.split("/")[-1].split("?")[0]
            video_id = self.url.split("v=")[-1].split("&")[0]
            return video_id
        return ""


class PlaybackMedia(BaseModel):
    hls: Optional[str] = None
    dash: Optional[str] = None
    mp4: Optional[str] = None
    thumbnail: str = ""


class LocationFeedPost(BaseModel):
    id: CustomObjectId = Field(alias="_id", serialization_alias="id")
    assignee_user_id: str = ""
    feed_id: CustomObjectId = None
    state: str = "READY_FOR_USE"
    transcode_job_name: Optional[str] = None
    verified_media_playback: Optional[PlaybackMedia] = None
    news_id: Optional[str] = None
    text_summary: Optional[str] = None
    text_content: Optional[str] = None
    visited_urls: Optional[List[str]] = []
    read_urls: Optional[List[str]] = []
    is_public: bool = False
    is_live: bool = False
    live_ended_at: Optional[datetime] = None
    is_space: bool = False
    has_recording: bool = False
    livekit_room_name: Optional[str] = ""
    space_state: Optional[str] = None
    scheduled_at: Optional[datetime] = None
    assignee_user: Optional[User] = None
    last_modified_date: datetime
    image_gallery_with_dims: List[ImageWithDims] = []
    feed: Optional[Feed] = None
    is_factchecked: bool = False
    title: Optional[str] = ""
    text_content_in_english: Optional[str] = None
    news_date: Optional[datetime] = None
    sources: List[Source] = []
    fact_check_id: Optional[CustomObjectId] = None
    fact_check_data: Optional[FactCheckingResult] = None
    fact_check_status: Optional[FactCheckStatus] = None
    is_generated_news: bool = False
    external_video: Optional[ExternalVideo] = None
    ai_video_summary: Optional[AIVideoSummary] = None
    ai_video_summary_status: Optional[AIVideoSummaryStatus] = None
    metadata_status: Optional[MetadataStatus] = None
    ai_video_summary_error: Optional[str] = None
    social_media_scrape_details: Optional[SocialMediaScrapeDetails] = None
    social_media_scrape_status: Optional[SocialMediaScrapeStatus] = None
    social_media_scrape_error: Optional[str] = None
    preview_data: Optional[LinkPreviewData] = None
    government_summary: Optional[str] = None
    opposition_summary: Optional[str] = None
    neutral_summary: Optional[str] = None


class FeedPost(LocationFeedPost):
    assignee_user: Optional[User] = None


class NewsFeedPost(LocationFeedPost):
    id: Optional[CustomObjectId] = Field(
        default=None, alias="_id", serialization_alias="id"
    )
    news_date: Optional[datetime] = None
    text_content_in_english: Optional[str] = None
    sources: Optional[List[Source]] = None
