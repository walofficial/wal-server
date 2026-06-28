"""
Geofence event consumer: resolves AI character for location and sends push notification.

Subscribes to the geofence Pub/Sub topic. On enter event: finds active AI character
for the resolved feed, applies cooldown dedup, then sends a push with
geofence_character_available payload so the app can open character chat.
"""

import json
import logging
from typing import Any, Optional

from bson import ObjectId

from ment_api.models.geofence import GeofenceEnterEvent
from ment_api.services.ai_character_service import get_active_character_for_feed
from ment_api.services.notification_service import send_notification
from ment_api.services.redis_service import get_async_redis_client

logger = logging.getLogger(__name__)

# Cooldown: do not send the same user+character+feed notification more than once per TTL
GEOFENCE_COOLDOWN_TTL_SECONDS = 300


def geofence_cooldown_key(user_id: str, character_id: str, feed_id: str) -> str:
    """Redis key for geofence notification cooldown."""
    return f"geo_prompt_sent:{user_id}:{character_id}:{feed_id}"


async def process_geofence_event_callback(message: Any) -> None:
    """
    Pub/Sub callback for geofence enter events.

    Parses GeofenceEnterEvent, resolves active AI character for feed, checks
    cooldown, then sends push with type geofence_character_available and
    character/feed metadata for app navigation.
    """
    try:
        message_data = message.data
        if isinstance(message_data, bytes):
            message_data = message_data.decode("utf-8")

        payload = json.loads(message_data)
        event = GeofenceEnterEvent.model_validate(payload)

        if not event.feed_id:
            logger.info(
                "Geofence event skipped: no feed_id",
                extra={
                    "json_fields": {
                        "operation": "geofence_consumer_skip_no_feed",
                        "user_id": event.user_id,
                        "region_identifier": event.region_identifier,
                    },
                    "labels": {"component": "geofence_worker"},
                },
            )
            return

        feed_id_obj = ObjectId(event.feed_id)
        character = await get_active_character_for_feed(feed_id_obj)
        if not character:
            logger.info(
                "Geofence event skipped: no active character for feed",
                extra={
                    "json_fields": {
                        "operation": "geofence_consumer_skip_no_character",
                        "user_id": event.user_id,
                        "feed_id": event.feed_id,
                    },
                    "labels": {"component": "geofence_worker"},
                },
            )
            return

        character_id = str(character["_id"])
        character_user_id = character.get("user_id", "")
        character_name = character.get("name", "")

        redis = get_async_redis_client()
        cooldown_key = geofence_cooldown_key(event.user_id, character_id, event.feed_id)
        if await redis.get(cooldown_key):
            logger.info(
                "Geofence notification skipped: cooldown",
                extra={
                    "json_fields": {
                        "operation": "geofence_consumer_dedup",
                        "user_id": event.user_id,
                        "character_id": character_id,
                        "feed_id": event.feed_id,
                    },
                    "labels": {"component": "geofence_worker"},
                },
            )
            return

        prompt = f"{character_name} is available at {event.region_name}"
        notification_data = {
            "type": "geofence_character_available",
            "characterId": character_id,
            "characterUserId": character_user_id,
            "characterName": character_name,
            "feedId": event.feed_id,
            "regionIdentifier": event.region_identifier,
            "prompt": prompt,
        }

        sent = await send_notification(
            event.user_id,
            title=character_name,
            message=prompt,
            data=notification_data,
        )

        if sent:
            await redis.set(cooldown_key, "1", ex=GEOFENCE_COOLDOWN_TTL_SECONDS)
            logger.info(
                "Geofence push sent",
                extra={
                    "json_fields": {
                        "operation": "geofence_consumer_push_sent",
                        "user_id": event.user_id,
                        "character_id": character_id,
                        "feed_id": event.feed_id,
                    },
                    "labels": {"component": "geofence_worker"},
                },
            )
        else:
            logger.warning(
                "Geofence push not sent (no token or send failed)",
                extra={
                    "json_fields": {
                        "operation": "geofence_consumer_push_failed",
                        "user_id": event.user_id,
                    },
                    "labels": {"component": "geofence_worker"},
                },
            )

    except json.JSONDecodeError as e:
        logger.error(
            f"Geofence message invalid JSON: {e}",
            extra={
                "json_fields": {
                    "operation": "geofence_consumer_parse_error",
                    "error": str(e),
                },
                "labels": {"component": "geofence_worker", "severity": "high"},
            },
        )
        raise
    except Exception as e:
        logger.error(
            f"Geofence consumer error: {e}",
            extra={
                "json_fields": {
                    "operation": "geofence_consumer_error",
                    "error": str(e),
                },
                "labels": {"component": "geofence_worker", "severity": "high"},
            },
        )
        raise
