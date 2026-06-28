"""
Geofence domain event models for Pub/Sub publishing and consumption.

Kept separate from the HTTP request/response models in routes/geofence.py.
"""

from typing import Optional

from pydantic import BaseModel, Field


class GeofenceEnterEvent(BaseModel):
    """Payload for geofence enter events published to GCP Pub/Sub."""

    user_id: str
    region_identifier: str
    region_name: str
    latitude: float
    longitude: float
    radius: float
    timestamp: str
    feed_id: Optional[str] = Field(default=None, description="Resolved feed_id from server-side geospatial check")
