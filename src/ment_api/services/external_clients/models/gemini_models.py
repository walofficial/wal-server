from typing import List, Optional, Dict

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from ment_api.models.link_preview_data import LinkPreviewData


class GeminiBaseModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


class FactCheckingReference(GeminiBaseModel):
    source_title: str = Field(description="title of the reference source")
    url: str = Field(description="Full URL of the reference source")
    key_quote: str = Field(
        description="Key quote from the source supporting the fact check"
    )
    is_supportive: bool = Field(
        description="Whether the reference supports or refutes the statement"
    )


class FactCheckingResult(GeminiBaseModel):
    factuality: float = Field(
        description="Factuality score from 0-1, where 0 is completely false and 1 is completely true"
    )
    result: bool = Field(description="Overall result of the fact check: true or false")
    reason: str = Field(
        description="Detailed explanation of the fact check result with reasoning"
    )
    reason_summary: Optional[str] = Field(
        description="1-2 sentence summary of the reasoning in Georgian language",
    )
    references: List[FactCheckingReference] = Field(
        default_factory=list, description="List of references supporting the fact check"
    )


class FactCheckInputRequest(GeminiBaseModel):
    statement: str = Field(description="The statement to fact-check")
    image_urls: Optional[List[str]] = Field(
        default=None, description="URLs of images to include in the fact check"
    )
    is_social_media: bool = Field(
        default=False, description="Whether the statement is from social media"
    )


class FactCheckInputResponse(BaseModel):
    enhanced_statement: str = Field(
        description="The enhanced statement for fact checking, combining text and image analysis."
    )
    is_valid_for_fact_check: bool = Field(
        description="Whether the input is valid for fact checking."
    )
    error_reason: Optional[str] = Field(
        default=None, description="If not valid for fact checking, the reason why."
    )
    preview_data: Optional[LinkPreviewData] = Field(
        default=None,
        description="Generated preview data with title and description based on content. Title and description should be in Georgian language.",
    )


class NewsItemWithId(BaseModel):
    id: str = Field(description="Verification ID of the news item")
    content: str = Field(description="Text content of the news item")


class NotificationGenerationRequest(GeminiBaseModel):
    news_items: List[NewsItemWithId] = Field(
        description="List of news items with IDs and text content"
    )
    notification_type: str = Field(
        default="daily_news_digest", description="Type of notification being generated"
    )
    tab: Optional[str] = Field(
        default=None,
        description="Content perspective for social media (neutral, government, opposition)",
    )
    fact_check_data: Optional[Dict] = Field(
        default=None, description="Fact check data for generating fact check summary"
    )


class NotificationGenerationResponse(BaseModel):
    title: str = Field(
        description="Engaging notification title in Georgian language, max 100 characters"
    )
    description: str = Field(
        description="Compelling notification description in Georgian language, max 200 characters, should entice users to open the app"
    )
    is_relevant: bool = Field(
        description="Whether the news items are relevant enough to send a notification"
    )
    selected_verification_id: Optional[str] = Field(
        default=None,
        description="ID of the most relevant news item that the notification is based on",
    )
    relevance_reason: Optional[str] = Field(
        default=None,
        description="Reason why the content might not be relevant for notification",
    )
    social_media_card_title: Optional[str] = Field(
        default=None,
        description="Title for social media card (10-35 words), based on neutral/government/opposition summary but squashed for card display",
    )
    fact_check_summary: Optional[str] = Field(
        default=None,
        description="Concise fact check summary (15-40 words) for social media display, explaining factuality in simple terms",
    )
