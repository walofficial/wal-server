import json
import logging
from typing import Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel

from ment_api.configurations.config import settings
from ment_api.models.geofence import GeofenceEnterEvent
from ment_api.services.location_service import find_feed_id_at_point
from ment_api.services.pub_sub_service import publish_message

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/geofence",
    tags=["geofence"],
    responses={404: {"description": "Not found"}},
)


class GeofenceEventRequest(BaseModel):
    event_type: str  # 'enter' or 'exit'
    region_identifier: str
    region_name: str
    latitude: float
    longitude: float
    radius: float
    timestamp: str


class GeofenceEventResponse(BaseModel):
    success: bool
    message: str
    user_id: Optional[str] = None
    region_name: Optional[str] = None


@router.post(
    "/event",
    response_model=GeofenceEventResponse,
    operation_id="geofence_event",
    responses={500: {"description": "Internal server error"}},
)
async def handle_geofence_event(
    request: Request,
    event: GeofenceEventRequest,
) -> GeofenceEventResponse:
    """
    Handle geofence enter/exit events from mobile clients.

    On enter: resolves feed from coordinates, publishes to GCP Pub/Sub for
    async processing (AI character notification). Response is returned
    immediately without waiting for notification delivery.
    """
    user_id = request.state.supabase_user_id

    logger.info(
        "Geofence event received",
        extra={
            "json_fields": {
                "operation": "geofence_event",
                "user_id": user_id,
                "event_type": event.event_type,
                "region_identifier": event.region_identifier,
                "region_name": event.region_name,
                "latitude": event.latitude,
                "longitude": event.longitude,
                "radius": event.radius,
                "timestamp": event.timestamp,
            },
            "labels": {"component": "geofence"},
        },
    )

    if event.event_type != "enter":
        action = "exited"
        message = f"User {action} region: {event.region_name}"
        return GeofenceEventResponse(
            success=True,
            message=message,
            user_id=user_id,
            region_name=event.region_name,
        )

    feed_id = None
    try:
        feed_id_obj = await find_feed_id_at_point(event.latitude, event.longitude)
        feed_id = str(feed_id_obj) if feed_id_obj else None
    except Exception as e:
        logger.warning(
            "Failed to resolve feed at point",
            extra={
                "json_fields": {
                    "operation": "geofence_resolve_feed",
                    "user_id": user_id,
                    "latitude": event.latitude,
                    "longitude": event.longitude,
                    "error": str(e),
                },
                "labels": {"component": "geofence"},
            },
        )

    enter_event = GeofenceEnterEvent(
        user_id=user_id,
        region_identifier=event.region_identifier,
        region_name=event.region_name,
        latitude=event.latitude,
        longitude=event.longitude,
        radius=event.radius,
        timestamp=event.timestamp,
        feed_id=feed_id,
    )
    payload_bytes = enter_event.model_dump_json().encode("utf-8")

    try:
        message_id = await publish_message(
            project_id=settings.gcp_project_id,
            topic_id=settings.pub_sub_geofence_topic_id,
            data=payload_bytes,
        )
        logger.info(
            "Geofence enter event published",
            extra={
                "json_fields": {
                    "operation": "geofence_publish",
                    "user_id": user_id,
                    "message_id": message_id,
                    "feed_id": feed_id,
                    "region_identifier": event.region_identifier,
                },
                "labels": {"component": "geofence"},
            },
        )
    except Exception as e:
        logger.error(
            "Failed to publish geofence enter event",
            extra={
                "json_fields": {
                    "operation": "geofence_publish_error",
                    "user_id": user_id,
                    "error": str(e),
                },
                "labels": {"component": "geofence", "severity": "high"},
            },
        )
        raise

    message = f"User entered region: {event.region_name}"
    return GeofenceEventResponse(
        success=True,
        message=message,
        user_id=user_id,
        region_name=event.region_name,
    )

