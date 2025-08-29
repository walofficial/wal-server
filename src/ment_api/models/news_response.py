from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field

from ment_api.models.location_feed_post import Source


class News(BaseModel):
    id: str = Field(description="Unique ID in English based on topic")
    title: str = Field(description="Title in Georgian language")
    summary: str = Field(
        description="Concise summary in Georgian language (1-2 sentences)"
    )
    government_summary: str = Field(
        description="Only the bullet points from the '🏛️ მთავრობის პოზიცია' section in Georgian language. Should contain ONLY the bullet points with markdown formatting, WITHOUT the section header/title. If no government sources are present, leave empty."
    )
    opposition_summary: str = Field(
        description="Only the bullet points from the '🗣️ ოპოზიციის მოსაზრება' section in Georgian language. Should contain ONLY the bullet points with markdown formatting, WITHOUT the section header/title. If no opposition sources are present, leave empty."
    )
    neutral_summary: str = Field(
        description="Only the bullet points from the '⚖️ ნეიტრალური მოსაზრება' section in Georgian language. Should contain ONLY the bullet points with markdown formatting, WITHOUT the section header/title. If no neutral sources are present, leave empty."
    )
    content: str = Field(
        title="Content (Georgian)",
        description="Detailed content in Georgian language. Must be raw markdown text (not in code blocks) using paragraphs, line breaks, **bold text**, and lists. Should include main bullet points and perspective sections (🏛️, 🗣️, ⚖️) when sources of those types are present.",
    )
    content_in_english: str = Field(
        title="Content (English)",
        description="Detailed content in English language. Must be raw markdown text (not in code blocks) using paragraphs, line breaks, **bold text**, and lists. Should include main bullet points and perspective sections (🏛️, 🗣️, ⚖️) when sources of those types are present.",
    )
    sources: List[Source]
    search_source: str = Field(description="Search method used to find articles")
    found_image_urls: List[str] = Field(
        description="List of image URLs that describe the news item content (maximum 2 images)",
        default=[],
    )
    event_date: Optional[datetime] = Field(
        description="Infer the date and time (ideally ISO 8601 format in UTC) of the actual event being reported by reading and understanding the content of the provided 'sources'.",
        default=None,
    )
    image_url: Optional[str] = Field(
        description="Appropriate image URL for the news item, shouldn't be image from 1tv.ge",
        default=None,
    )


class NewsResponse(BaseModel):
    news: List[News]
