"""
AI Chat Buffer Worker.

This worker processes buffered AI chat messages using Pub/Sub for event-driven triggering.
Instead of polling, it uses Cloud Tasks for debounce scheduling.

Flow:
1. User sends message to AI character
2. Message is buffered in Redis
3. Pub/Sub message is published with room_id and user_id
4. Worker receives message, schedules Cloud Task for debounce delay
5. Cloud Task triggers endpoint after debounce period
6. Endpoint checks for new messages, processes if debounce complete
"""

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from bson import ObjectId
from google.cloud.pubsub_v1.subscriber.message import Message

from ment_api.services.chat_utils import _prune_and_count_online

from ment_api.configurations.config import settings
from ment_api.models.message_state import MessageState
from ment_api.persistence import mongo
from ment_api.services.google_tasks_service import create_http_task
from ment_api.services.redis_service import get_async_redis_client

logger = logging.getLogger(__name__)

# Buffer configuration
AI_MESSAGE_DEBOUNCE_SECONDS = 4
AI_MESSAGE_BUFFER_TTL = 120


def ai_buffer_key(room_id: str, user_id: str) -> str:
    """Redis key for buffered messages list."""
    return f"ai_buffer:{room_id}:{user_id}"


def ai_buffer_ts_key(room_id: str, user_id: str) -> str:
    """Redis key for last message timestamp."""
    return f"ai_buffer_ts:{room_id}:{user_id}"


def ai_buffer_task_key(room_id: str, user_id: str) -> str:
    """Redis key to track if a task is already scheduled."""
    return f"ai_buffer_task:{room_id}:{user_id}"


def ai_buffer_lock_key(room_id: str, user_id: str) -> str:
    """Redis key for distributed processing lock."""
    return f"ai_buffer_lock:{room_id}:{user_id}"


async def get_buffer_last_timestamp(
    redis, room_id: str, user_id: str
) -> Optional[float]:
    """Get the timestamp of the last buffered message."""
    ts_key = ai_buffer_ts_key(room_id, user_id)
    ts = await redis.get(ts_key)
    return float(ts) if ts else None


async def acquire_buffer_lock(redis, room_id: str, user_id: str) -> bool:
    """Try to acquire distributed lock for buffer processing."""
    lock_key = ai_buffer_lock_key(room_id, user_id)
    result = await redis.set(lock_key, "1", nx=True, ex=30)
    return result is not None


async def release_buffer_lock(redis, room_id: str, user_id: str) -> None:
    """Release the distributed lock."""
    lock_key = ai_buffer_lock_key(room_id, user_id)
    await redis.delete(lock_key)


async def get_and_clear_buffer(redis, room_id: str, user_id: str) -> list[str]:
    """
    Atomically get all buffered messages and clear the buffer.

    Returns list of message contents.
    """
    buffer_key = ai_buffer_key(room_id, user_id)
    ts_key = ai_buffer_ts_key(room_id, user_id)
    task_key = ai_buffer_task_key(room_id, user_id)

    # Get all messages
    messages_raw = await redis.lrange(buffer_key, 0, -1)

    if not messages_raw:
        return []

    # Parse messages
    messages = []
    for msg_raw in messages_raw:
        try:
            msg_data = json.loads(msg_raw)
            messages.append(msg_data["content"])
        except (json.JSONDecodeError, KeyError):
            continue

    # Clear buffer atomically
    pipe = redis.pipeline()
    pipe.delete(buffer_key)
    pipe.delete(ts_key)
    pipe.delete(task_key)
    await pipe.execute()

    return messages


async def get_buffer_recipient_id(
    redis, room_id: str, user_id: str
) -> Optional[str]:
    """Get the recipient (AI character) ID from buffer."""
    buffer_key = ai_buffer_key(room_id, user_id)
    first_msg = await redis.lindex(buffer_key, 0)
    if first_msg:
        try:
            msg_data = json.loads(first_msg)
            return msg_data.get("recipient_id")
        except (json.JSONDecodeError, KeyError):
            pass
    return None


async def schedule_ai_buffer_processing(room_id: str, user_id: str) -> None:
    """
    Schedule a Cloud Task to process the AI buffer after debounce delay.

    Uses Redis to ensure only one task is scheduled per buffer.
    """
    redis = get_async_redis_client()
    task_key = ai_buffer_task_key(room_id, user_id)

    # Check if task is already scheduled (NX = only set if not exists)
    already_scheduled = await redis.set(
        task_key, "1", nx=True, ex=AI_MESSAGE_BUFFER_TTL
    )

    if not already_scheduled:
        # Task already scheduled, it will handle this message
        logger.debug(
            "AI buffer task already scheduled",
            extra={
                "json_fields": {
                    "room_id": room_id,
                    "user_id": user_id,
                    "operation": "schedule_ai_buffer_skip",
                },
                "labels": {"component": "ai_buffer_worker"},
            },
        )
        return

    # Schedule Cloud Task with debounce delay
    schedule_time = datetime.now(timezone.utc) + timedelta(
        seconds=AI_MESSAGE_DEBOUNCE_SECONDS
    )

    try:
        create_http_task(
            url=f"{settings.api_url}/chat/process-ai-buffer",
            json_payload={
                "room_id": room_id,
                "user_id": user_id,
            },
            schedule_time=schedule_time,
        )

        logger.info(
            "Scheduled AI buffer processing task",
            extra={
                "json_fields": {
                    "room_id": room_id,
                    "user_id": user_id,
                    "delay_seconds": AI_MESSAGE_DEBOUNCE_SECONDS,
                    "operation": "schedule_ai_buffer_task",
                },
                "labels": {"component": "ai_buffer_worker"},
            },
        )

    except Exception as e:
        # Clear the task key so next message can reschedule
        await redis.delete(task_key)
        logger.error(
            f"Failed to schedule AI buffer task: {e}",
            extra={
                "json_fields": {
                    "room_id": room_id,
                    "user_id": user_id,
                    "error": str(e),
                    "operation": "schedule_ai_buffer_task_error",
                },
                "labels": {"component": "ai_buffer_worker", "severity": "high"},
            },
        )
        raise


async def process_ai_buffer(room_id: str, user_id: str) -> dict:
    """
    Process a ready AI message buffer and generate response.

    This function is called by Cloud Tasks after debounce period.

    Returns dict with processing result for API response.
    """
    from ment_api.routes.chat import sio, send_chat_notification

    redis = get_async_redis_client()

    # Check if debounce period has truly passed (new messages might have arrived)
    last_ts = await get_buffer_last_timestamp(redis, room_id, user_id)
    if last_ts is None:
        # Buffer was already processed or empty
        return {"status": "already_processed"}

    time_since_last = time.time() - last_ts
    if time_since_last < AI_MESSAGE_DEBOUNCE_SECONDS:
        # New messages arrived, reschedule
        # Clear task key so we can reschedule
        task_key = ai_buffer_task_key(room_id, user_id)
        await redis.delete(task_key)
        await schedule_ai_buffer_processing(room_id, user_id)
        return {"status": "rescheduled", "time_remaining": AI_MESSAGE_DEBOUNCE_SECONDS - time_since_last}

    # Try to acquire lock (only one instance processes)
    if not await acquire_buffer_lock(redis, room_id, user_id):
        return {"status": "lock_held"}

    try:
        # Get recipient (AI character) ID before clearing buffer
        recipient_id = await get_buffer_recipient_id(redis, room_id, user_id)
        if not recipient_id:
            logger.warning(
                "No recipient ID in buffer",
                extra={
                    "json_fields": {
                        "room_id": room_id,
                        "user_id": user_id,
                        "operation": "process_ai_buffer_no_recipient",
                    },
                    "labels": {"component": "ai_buffer_worker"},
                },
            )
            return {"status": "no_recipient"}

        # Get all messages and clear buffer atomically
        messages = await get_and_clear_buffer(redis, room_id, user_id)

        if not messages:
            return {"status": "empty_buffer"}

        logger.info(
            "Processing AI buffer",
            extra={
                "json_fields": {
                    "room_id": room_id,
                    "user_id": user_id,
                    "recipient_id": recipient_id,
                    "message_count": len(messages),
                    "operation": "process_ai_buffer",
                },
                "labels": {"component": "ai_buffer_worker"},
            },
        )

        # Get AI character
        from ment_api.services.ai_character_service import (
            generate_chat_response,
            get_character_doc_by_user_id,
        )

        character = await get_character_doc_by_user_id(recipient_id)
        logger.info(
            "Character found for buffer processing",
            extra={
                "json_fields": {
                    "has_character": bool(character),
                },
            },
        )
        if not character:
            logger.error(
                "Character not found for buffer processing",
                extra={
                    "json_fields": {
                        "room_id": room_id,
                        "recipient_id": recipient_id,
                        "operation": "process_ai_buffer_no_character",
                    },
                    "labels": {"component": "ai_buffer_worker"},
                },
            )
            return {"status": "character_not_found"}

        # Generate AI response to ALL messages
        ai_response = await generate_chat_response(
            character=character,
            user_id=user_id,
            user_messages=messages,
            room_id=ObjectId(room_id),
        )

        # Get AI character info for notifications
        character_name = character.get("name", "")
        face_images = character.get("face_images", [])
        ai_profile_picture = face_images[0] if face_images else ""

        # Check if user is online

        online_count = await _prune_and_count_online(redis, user_id)
        is_user_online = online_count > 0
        logger.info(
            "User online status",
            extra={
                "json_fields": {
                    "is_user_online": is_user_online,
                },
            },
        )
        if is_user_online:
            # User is online - emit via Socket.IO
            await sio.emit(
                "private_message",
                {
                    "sender": recipient_id,
                    "sender_username": character_name,
                    "sender_profile_picture": ai_profile_picture,
                    "plain_content": ai_response,
                    "room_id": room_id,
                    "temporary_id": f"ai_batch_{int(time.time() * 1000)}",
                },
                room=f"user:{user_id}",
            )
        else:
            # User is offline - send push notification
            await send_chat_notification(
                user_id=user_id,
                message=ai_response,
                room_id=room_id,
                message_title=character_name,
                sender_id=recipient_id,
                sender_avatar_url=ai_profile_picture,
                encrypted_content=None,
                nonce=None,
            )

        # Store AI response message
        await mongo.chat_messages.insert_one(
            {
                "author_id": recipient_id,
                "recipient_id": user_id,
                "room_id": ObjectId(room_id),
                "plain_content": ai_response,
                "message_state": MessageState.SENT,
            }
        )

        logger.info(
            "AI buffer processed successfully",
            extra={
                "json_fields": {
                    "room_id": room_id,
                    "user_id": user_id,
                    "message_count": len(messages),
                    "response_length": len(ai_response),
                    "user_online": is_user_online,
                    "operation": "process_ai_buffer_success",
                },
                "labels": {"component": "ai_buffer_worker"},
            },
        )

        return {
            "status": "success",
            "messages_processed": len(messages),
            "response_length": len(ai_response),
        }

    except Exception as e:
        logger.info(
            "Error processing AI buffer",
            extra={
                "json_fields": {
                    "error": str(e),
                },
            },
        )
        logger.error(
            f"Error processing AI buffer: {e}",
            extra={
                "json_fields": {
                    "room_id": room_id,
                    "user_id": user_id,
                    "error": str(e),
                    "operation": "process_ai_buffer_error",
                },
                "labels": {"component": "ai_buffer_worker", "severity": "high"},
            },
        )
        return {"status": "error", "error": str(e)}

    finally:
        await release_buffer_lock(redis, room_id, user_id)


async def process_ai_buffer_pubsub_callback(message: Message) -> None:
    """
    PubSub callback handler for AI buffer messages.

    When a message is received, it schedules a Cloud Task to process
    the buffer after the debounce period.
    """
    try:
        # Parse the message data
        message_data = message.data
        if isinstance(message_data, bytes):
            message_data = message_data.decode("utf-8")

        payload = json.loads(message_data)
        room_id = payload.get("room_id")
        user_id = payload.get("user_id")

        if not room_id or not user_id:
            logger.warning(
                "Invalid AI buffer message payload",
                extra={
                    "json_fields": {
                        "payload": payload,
                        "operation": "ai_buffer_pubsub_invalid",
                    },
                    "labels": {"component": "ai_buffer_worker"},
                },
            )
            return

        logger.debug(
            "Received AI buffer trigger",
            extra={
                "json_fields": {
                    "room_id": room_id,
                    "user_id": user_id,
                    "message_id": message.message_id,
                    "operation": "ai_buffer_pubsub_received",
                },
                "labels": {"component": "ai_buffer_worker"},
            },
        )

        # Schedule processing task (handles deduplication internally)
        await schedule_ai_buffer_processing(room_id, user_id)

    except json.JSONDecodeError as e:
        logger.error(
            f"Failed to parse AI buffer message: {e}",
            extra={
                "json_fields": {
                    "error": str(e),
                    "operation": "ai_buffer_pubsub_parse_error",
                },
                "labels": {"component": "ai_buffer_worker"},
            },
        )
    except Exception as e:
        logger.error(
            f"AI buffer pubsub callback error: {e}",
            extra={
                "json_fields": {
                    "error": str(e),
                    "operation": "ai_buffer_pubsub_error",
                },
                "labels": {"component": "ai_buffer_worker", "severity": "high"},
            },
        )
        raise

