import logging
from typing import Dict, List, Optional, Tuple

from ment_api.common.custom_object_id import CustomObjectId
from ment_api.models.feed_location_mapping import Lat, Lng, Location
from ment_api.persistence import mongo

logger = logging.getLogger(__name__)


async def is_on_feed_location(
    feed_id: CustomObjectId, current_location: Tuple[Lat, Lng]
) -> Tuple[bool, Optional[Location]]:
    """Fast geospatial check using 2dsphere index.

    Returns (is_inside_radius, nearest_location)
    """
    try:
        near_point = {
            "type": "Point",
            "coordinates": [current_location[0],current_location[1], ],
        }

        pipeline = [
            {
                "$geoNear": {
                    "near": near_point,
                    "distanceField": "distance",
                    "spherical": True,
                    "key": "location",
                    "query": {"feed_id": feed_id},
                    "limit": 1,
                }
            },
            {
                "$project": {
                    "name": 1,
                    "address": 1,
                    "radius": 1,
                    "distance": 1,
                    "lat": {"$arrayElemAt": ["$location.coordinates", 1]},
                    "lng": {"$arrayElemAt": ["$location.coordinates", 0]},
                }
            },
        ]
        print(pipeline)

        results = await mongo.feed_locations.aggregate(pipeline)
        if not results:
            return False, None

        doc = results[0]
        inside = bool(doc.get("distance", float("inf")) <= doc.get("radius", 300))
        nearest_location = Location(
            name=doc.get("name", ""),
            address=doc.get("address", ""),
            location=(doc.get("lat", 0.0), doc.get("lng", 0.0)),
        )

        return inside, nearest_location

    except Exception as e:
        logger.error(
            "Location check failed",
            extra={
                "json_fields": {
                    "feed_id": str(feed_id),
                    "operation": "is_on_feed_location",
                    "error_message": str(e),
                },
                "labels": {"component": "location_service", "severity": "high"},
            },
            exc_info=True,
        )
        return False, None


async def get_nearest_locations_for_feeds(
    feed_ids: List[CustomObjectId], current_location: Tuple[Lat, Lng]
) -> Dict[CustomObjectId, Tuple[bool, Optional[Location]]]:
    """Batch nearest lookup per feed using a single $geoNear + $group.

    Returns mapping of feed_id -> (is_inside_radius, nearest_location)
    """
    if not feed_ids:
        return {}

    near_point = {
        "type": "Point",
        "coordinates": [current_location[0], current_location[1] ],
    }

    pipeline = [
        {
            "$geoNear": {
                "near": near_point,
                "distanceField": "distance",
                "spherical": True,
                "key": "location",
                "query": {"feed_id": {"$in": feed_ids}},
            }
        },
        {"$sort": {"distance": 1}},
        {
            "$group": {
                "_id": "$feed_id",
                "nearest": {
                    "$first": {
                        "name": "$name",
                        "address": "$address",
                        "radius": "$radius",
                        "distance": "$distance",
                        "lat": {"$arrayElemAt": ["$location.coordinates", 0]},
                        "lng": {"$arrayElemAt": ["$location.coordinates", 1]},
                    }
                },
                "inside": {
                    "$max": {
                        "$cond": [
                            {"$lte": ["$distance", "$radius"]},
                            1,
                            0,
                        ]
                    }
                },
            }
        },
        {
            "$project": {
                "feed_id": "$_id",
                "inside": 1,
                "nearest": 1,
                "_id": 0,
            }
        },
    ]
    results = await mongo.feed_locations.aggregate(pipeline)
    mapping: Dict[CustomObjectId, Tuple[bool, Optional[Location]]] = {}

    for doc in results:
        feed_id = doc["feed_id"]
        nearest = doc.get("nearest") or {}
        location_obj = None
        if nearest:
            location_obj = Location(
                name=nearest.get("name", ""),
                address=nearest.get("address", ""),
                location=(nearest.get("lat", 0.0), nearest.get("lng", 0.0)),
            )
        mapping[feed_id] = (bool(doc.get("inside", 0)), location_obj)
    return mapping