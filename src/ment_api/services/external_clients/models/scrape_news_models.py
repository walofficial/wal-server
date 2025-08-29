from datetime import datetime
from enum import StrEnum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, model_validator
from pydantic_xml import BaseXmlModel, attr, element


class NewsSource(StrEnum):
    IMEDI = "Imedi"
    PUBLIKA = "Publika"
    TV1 = "TV1"
    INTERPRESS = "InterPressNews"
    NETGAZETI = "Netgazeti"
    CIVIL = "Civil"


class NewsCategory(StrEnum):
    POLITICS = "Politics"


class NewsItem(BaseModel):
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


class NewsResponse(BaseModel):
    news_items: List[NewsItem]


class RawImediNewsItem(BaseModel):
    id: int = Field(alias="Id")
    title: str = Field(alias="Title")
    content: str = Field(alias="Content")
    details_url: str = Field(alias="Url")
    big_image_url: str = Field(alias="BigCoverPhotoUrl")
    medium_image_url: str = Field(alias="MediumCoverPhotoUrl")
    small_image_url: str = Field(alias="SmallCoverPhotoUrl")
    date: str = Field(alias="DateValue")


class RawImediNewsResponse(BaseModel):
    news_items: List[RawImediNewsItem] = Field(alias="List")


class RawPublikaNewsItem(BaseModel):
    id: int = Field(alias="ID")
    title: str
    details_url: str = Field(alias="url")
    date: int
    content: str = ""
    big_image_url: str = ""
    medium_image_url: str = ""
    small_image_url: str = ""

    @model_validator(mode="before")
    @classmethod
    def extract_nested_fields(cls, data: Dict[str, Any]) -> Dict[str, Any]:
        if "image" in data and "src" in data["image"]:
            image_src = data["image"]["src"]
            data["big_image_url"] = image_src.get("full", "")
            data["medium_image_url"] = image_src.get("large", "")
            data["small_image_url"] = image_src.get("medium", "")

        if "acf_meta" in data and "blocks" in data["acf_meta"]:
            for block in data["acf_meta"]["blocks"]:
                if "editor" in block:
                    data["content"] = block["editor"]
                    break

        return data


class RawPublikaNewsResponse(BaseModel):
    news_items: List[RawPublikaNewsItem] = Field(alias="posts")


class RawTV1NewsItem(BaseModel):
    id: int = Field(alias="ID")
    title: str = Field(alias="post_title")
    details_url: str = Field(alias="post_permalink")
    date: str = Field(alias="post_date")
    content: str = ""
    big_image_url: str = ""
    medium_image_url: str = ""
    small_image_url: str = ""

    @model_validator(mode="before")
    @classmethod
    def extract_nested_fields(cls, data: Dict[str, Any]) -> Dict[str, Any]:
        if "post_image" in data and "src" in data["post_image"]:
            image_src = data["post_image"]["src"]
            data["big_image_url"] = image_src.get("full", "")
            data["medium_image_url"] = image_src.get("large", "")
            data["small_image_url"] = image_src.get("medium", "")

        return data


class RawTV1NewsItemDetails(BaseModel):
    content: str = Field(alias="post_content")


class RawTV1NewsResponse(BaseModel):
    news_items: List[RawTV1NewsItem] = Field(alias="data")


class RawInterPressNewsItem(BaseModel):
    id: int
    title: str
    details_url: str = ""
    date: datetime = Field(alias="pub_dt")
    content: str = ""
    big_image_url: str = ""
    medium_image_url: str = ""
    small_image_url: str = ""

    @model_validator(mode="before")
    @classmethod
    def extract_nested_fields(cls, data: Dict[str, Any]) -> Dict[str, Any]:
        if "image" in data and "original" in data["image"]:
            image_src = data["image"]["original"]
            data["medium_image_url"] = image_src
        return data


class RawInterPressNewsItemDetails(BaseModel):
    content: str = Field(alias="fulltext")
    details_url_part: str = Field(alias="url")


class RawInterPressNewsResponse(BaseModel):
    news_items: List[RawInterPressNewsItem] = Field(alias="results")


# Generic RSS Models using Pydantic-XML


class AtomLink(BaseXmlModel, ns="atom"):
    """Model for the atom:link element to capture its attributes."""

    href: str = attr()
    rel: str = attr()
    type: str = attr()


class RawGenericRSSItem(
    BaseXmlModel,
    nsmap={
        "content": "http://purl.org/rss/1.0/modules/content/",
        "wfw": "http://wellformedweb.org/CommentAPI/",
        "dc": "http://purl.org/dc/elements/1.1/",
        "atom": "http://www.w3.org/2005/Atom",
        "sy": "http://purl.org/rss/1.0/modules/syndication/",
        "slash": "http://purl.org/rss/1.0/modules/slash/",
    },
):
    title: str = element()
    link: str = element()
    creator: Optional[str] = element(tag="creator", ns="dc", default="")
    pub_date: str = element(tag="pubDate")
    category: List[str] = element(tag="category", default_factory=list)
    guid: Optional[str] = element(default="")
    description: str = element()


class GenericRSSImage(BaseXmlModel):
    url: str = element()
    title: str = element()
    link: str = element()
    width: int = element()
    height: int = element()


# Updated Channel model with all missing fields
class Channel(
    BaseXmlModel,
    nsmap={
        "content": "http://purl.org/rss/1.0/modules/content/",
        "wfw": "http://wellformedweb.org/CommentAPI/",
        "dc": "http://purl.org/dc/elements/1.1/",
        "atom": "http://www.w3.org/2005/Atom",
        "sy": "http://purl.org/rss/1.0/modules/syndication/",
        "slash": "http://purl.org/rss/1.0/modules/slash/",
    },
):
    title: str = element()
    # Add the missing atom:link element
    atom_link: AtomLink = element(tag="link", ns="atom")
    link: str = element()
    description: str = element()
    last_build_date: Optional[str] = element(tag="lastBuildDate", default="")
    language: Optional[str] = element(default="")
    # Add the missing syndication elements
    update_period: Optional[str] = element(tag="updatePeriod", ns="sy", default=None)
    update_frequency: Optional[int] = element(
        tag="updateFrequency", ns="sy", default=None
    )
    # Add the missing generator element
    generator: Optional[str] = element(default=None)
    image: Optional[GenericRSSImage] = element(default=None)
    items: List[RawGenericRSSItem] = element(tag="item", default_factory=list)


class RawGenericRSSResponse(
    BaseXmlModel,
    tag="rss",
    nsmap={
        "content": "http://purl.org/rss/1.0/modules/content/",
        "wfw": "http://wellformedweb.org/CommentAPI/",
        "dc": "http://purl.org/dc/elements/1.1/",
        "atom": "http://www.w3.org/2005/Atom",
        "sy": "http://purl.org/rss/1.0/modules/syndication/",
        "slash": "http://purl.org/rss/1.0/modules/slash/",
    },
):
    channel: Channel = element()
