from typing import Optional, List

from pydantic import BaseModel


# New model for storing link preview data
class LinkPreviewData(BaseModel):
    url: str = ""
    title: Optional[str]
    description: Optional[str]
    images: Optional[List[str]] = []
    site_name: Optional[str] = "unknown"
    platform: Optional[str] = "unknown"