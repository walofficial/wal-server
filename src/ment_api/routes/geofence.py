import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel

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
    
    This endpoint receives notifications when a user enters or exits
    a geofenced region.
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

    # Here you can add additional logic:
    # - Store the event in MongoDB
    # - Trigger notifications
    # - Update user's current location
    # - Track analytics
    # - etc.

    action = "entered" if event.event_type == "enter" else "exited"
    message = f"User {action} region: {event.region_name}"

    return GeofenceEventResponse(
        success=True,
        message=message,
        user_id=user_id,
        region_name=event.region_name,
    )

