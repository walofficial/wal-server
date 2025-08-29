from datetime import datetime
from enum import StrEnum
from typing import NotRequired, TypedDict

from bson import ObjectId


class NewsSource(StrEnum):
    IMEDI = "Imedi"
    PUBLIKA = "Publika"
    TV1 = "TV1"
    INTERPRESS = "InterPressNews"
    NETGAZETI = "Netgazeti"
    CIVIL = "Civil"


class NewsCategory(StrEnum):
    POLITICS = "Politics"


class ExternalArticle(TypedDict):
    _id: NotRequired[ObjectId]
    external_id: str
    title: str
    content: str
    details_url: str
    big_image_url: str
    medium_image_url: str
    small_image_url: str
    created_at: datetime
    category: NewsCategory
    source: NewsSource
    used_by_post_generator: NotRequired[bool]
