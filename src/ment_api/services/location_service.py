import logging
import math
from typing import Awaitable, Optional, Tuple
from ment_api.common.custom_object_id import CustomObjectId
from ment_api.models.feed_location_mapping import (
    Lat,
    Lng,
    FeedLocationMapping,
    Location,
)
from ment_api.persistence import mongo

logger = logging.getLogger(__name__)


async def is_on_feed_location(
    feed_id: CustomObjectId, current_location: Tuple[Lat, Lng]
) -> Awaitable[Tuple[bool, Optional[Location]]]:
    mapping = await mongo.feed_location_mappings.find_one({"feed_ids": feed_id})

    if mapping is None:
        return False, None

    location_mapping = FeedLocationMapping(**mapping)
    closest_point = None
    min_distance = float("inf")
    radius = location_mapping.radius if hasattr(location_mapping, "radius") else 300

    for location_metadata in location_mapping.locations:
        distance = haversine_distance(current_location, location_metadata.location)
        if distance <= radius:
            return True, location_metadata
        if distance < min_distance:
            min_distance = distance
            closest_point = location_metadata

    return False, closest_point


def haversine_distance(point_one: Tuple[Lat, Lng], point_two: Tuple[Lat, Lng]) -> float:
    R = 6371000  # Radius of the Earth in meters
    lat1, lon1 = point_one
    lat2, lon2 = point_two
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    distance = R * c  # Distance in meters
    return distance
