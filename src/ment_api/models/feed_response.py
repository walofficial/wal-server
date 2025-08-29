from typing import List, Optional
from pydantic import BaseModel
from ment_api.models.feed import Feed
from ment_api.models.feed_location_mapping import Location


class FeedWithLocation(BaseModel):
    feed: Feed
    nearest_location: Optional[Location] = None


class FeedsResponse(BaseModel):
    feeds_at_location: List[Feed]
    nearest_feeds: List[FeedWithLocation]
