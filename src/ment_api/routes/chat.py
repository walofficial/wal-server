import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

import socketio
from bson import ObjectId
from exponent_server_sdk import PushClient
from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.params import Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from socketio import AsyncRedisManager

from ment_api.common.custom_object_id import CustomObjectId
from ment_api.configurations.config import settings
from ment_api.models.chat_message import ChatMessage
from ment_api.models.message_state import MessageState
from ment_api.models.update_message_request import UpdateMessageRequest
from ment_api.models.user import User
from ment_api.persistence import mongo, mongo_client
from ment_api.services.notification_service import send_notification
from ment_api.services.redis_service import get_async_redis_client
from ment_api.workers.message_state_worker import message_state_channel

# AI Message Buffer Configuration
AI_MESSAGE_DEBOUNCE_SECONDS = 4  # Wait time before AI responds
AI_MESSAGE_BUFFER_TTL = 120  # Safety TTL for Redis cleanup

logger = logging.getLogger(__name__)

redis_url = (
    f"redis://:{settings.redis_password}@{settings.redis_host}:{settings.redis_port}"
)
mgr = AsyncRedisManager(redis_url)
sio = socketio.AsyncServer(
    async_mode="asgi", cors_allowed_origins="*", client_manager=mgr
)
router = APIRouter(prefix="/chat", tags=["chat"])

push_client = PushClient()

notification_queue: Dict[str, asyncio.Task] = {}

# helper: atomic swap + return current sids for this user
DEVICE_SWAP_LUA = """
local prev = redis.call("GET", KEYS[1])
redis.call("SET", KEYS[1], ARGV[1])
local sids = redis.call("ZRANGE", KEYS[2], 0, -1)
return {prev, sids}
"""

# Presence is soft-state: it must expire if the instance/network dies.
# Client sends periodic `heartbeat` to refresh these TTLs.
PRESENCE_SID_TTL_SECONDS = 90
PRESENCE_ONLINE_WINDOW_SECONDS = 75


# Redis key helpers for scalable connection tracking
def user_sids_key(user_id: str) -> str:
    # ZSET: member=sid, score=last_seen_epoch_seconds
    return f"user_presence_sids:{user_id}"


def sid_user_key(sid: str) -> str:
    return f"sid_user:{sid}"


def sid_device_key(sid: str) -> str:
    return f"sid_device:{sid}"


def user_info_key(user_id: str) -> str:
    return f"user_info:{user_id}"


def _now_epoch_seconds() -> int:
    return int(datetime.now(tz=timezone.utc).timestamp())


async def _prune_and_count_online(redis, user_id: str) -> int:
    """Remove stale sids and return number of active sids within online window."""
    key = user_sids_key(user_id)
    now = _now_epoch_seconds()
    min_score = now - PRESENCE_ONLINE_WINDOW_SECONDS
    pipe = redis.pipeline()
    pipe.zremrangebyscore(key, 0, min_score - 1)
    pipe.zcount(key, min_score, "+inf")
    results = await pipe.execute()
    # results[0] is removed count, results[1] is active count
    return int(results[1] or 0)


async def _touch_presence(redis, user_id: str, sid: str) -> None:
    """Mark a sid as recently seen and keep the per-user ZSET bounded."""
    key = user_sids_key(user_id)
    now = _now_epoch_seconds()
    min_score = now - PRESENCE_ONLINE_WINDOW_SECONDS
    pipe = redis.pipeline()
    pipe.zadd(key, {sid: now})
    pipe.expire(key, PRESENCE_SID_TTL_SECONDS)
    pipe.zremrangebyscore(key, 0, min_score - 1)
    await pipe.execute()


# ============================================================================
# AI Message Buffer Functions (for scalable message batching)
# ============================================================================


def ai_buffer_key(room_id: str, user_id: str) -> str:
    """Redis key for buffered messages list."""
    return f"ai_buffer:{room_id}:{user_id}"


def ai_buffer_ts_key(room_id: str, user_id: str) -> str:
    """Redis key for last message timestamp."""
    return f"ai_buffer_ts:{room_id}:{user_id}"


def ai_buffer_lock_key(room_id: str, user_id: str) -> str:
    """Redis key for distributed processing lock."""
    return f"ai_buffer_lock:{room_id}:{user_id}"


async def buffer_ai_message(
    redis,
    room_id: str,
    sender_id: str,
    recipient_id: str,
    message: str,
) -> None:
    """
    Buffer an incoming message for batched AI response.
    
    This function:
    1. Adds message to the buffer list
    2. Updates last message timestamp
    3. Tracks active buffer for efficient scanning
    """
    buffer_key = ai_buffer_key(room_id, sender_id)
    ts_key = ai_buffer_ts_key(room_id, sender_id)
    
    # Store message with metadata
    message_data = json.dumps({
        "content": message,
        "timestamp": time.time(),
        "recipient_id": recipient_id,
    })
    
    pipe = redis.pipeline()
    # Add message to buffer list
    pipe.rpush(buffer_key, message_data)
    pipe.expire(buffer_key, AI_MESSAGE_BUFFER_TTL)
    # Update last message timestamp
    pipe.set(ts_key, str(time.time()), ex=AI_MESSAGE_BUFFER_TTL)
    # Track active buffer for efficient scanning
    pipe.sadd("ai_active_buffers", f"{room_id}:{sender_id}")
    await pipe.execute()


async def get_active_ai_buffers(redis) -> set:
    """Get all active AI message buffer keys."""
    members = await redis.smembers("ai_active_buffers")
    return members if members else set()


async def get_buffer_last_timestamp(redis, room_id: str, user_id: str) -> Optional[float]:
    """Get the timestamp of the last buffered message."""
    ts_key = ai_buffer_ts_key(room_id, user_id)
    ts = await redis.get(ts_key)
    return float(ts) if ts else None


async def acquire_buffer_lock(redis, room_id: str, user_id: str) -> bool:
    """Try to acquire distributed lock for buffer processing."""
    lock_key = ai_buffer_lock_key(room_id, user_id)
    # NX = only set if not exists, EX = expire after 10 seconds
    result = await redis.set(lock_key, "1", nx=True, ex=10)
    return result is not None


async def release_buffer_lock(redis, room_id: str, user_id: str) -> None:
    """Release the distributed lock."""
    lock_key = ai_buffer_lock_key(room_id, user_id)
    await redis.delete(lock_key)


async def get_and_clear_buffer(redis, room_id: str, user_id: str) -> List[str]:
    """
    Atomically get all buffered messages and clear the buffer.
    
    Returns list of message contents.
    """
    buffer_key = ai_buffer_key(room_id, user_id)
    ts_key = ai_buffer_ts_key(room_id, user_id)
    
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
    pipe.srem("ai_active_buffers", f"{room_id}:{user_id}")
    await pipe.execute()
    
    return messages


async def get_buffer_recipient_id(redis, room_id: str, user_id: str) -> Optional[str]:
    """Get the recipient (AI character) ID from buffer."""
    buffer_key = ai_buffer_key(room_id, user_id)
    # Get first message to extract recipient_id
    first_msg = await redis.lindex(buffer_key, 0)
    if first_msg:
        try:
            msg_data = json.loads(first_msg)
            return msg_data.get("recipient_id")
        except (json.JSONDecodeError, KeyError):
            pass
    return None


async def broadcast_user_public_key(user_id: str, public_key: str) -> None:
    """Notify all online participants across the user's chat rooms about the user's latest public key.

    Looks up all chat rooms that include the `user_id` in `participants` (stored as external_user_id strings),
    and for each other participant that is currently connected, emits a `user_public_key` event scoped to
    that chat room.
    """
    if not user_id or not public_key:
        return

    redis = get_async_redis_client()
    try:
        chat_rooms = await mongo.chat_rooms.find_all({"participants": user_id})
        for room in chat_rooms:
            participants = room.get("participants") or []
            room_id_str = str(room.get("_id"))
            for participant in participants:
                if participant == user_id:
                    continue
                try:
                    participant_sids = await redis.zrange(user_sids_key(participant), 0, -1)
                except Exception:
                    participant_sids = []
                if not participant_sids:
                    continue
                for participant_sid in list(participant_sids):
                    try:
                        await sio.emit(
                            "user_public_key",
                            {
                                "user_id": user_id,
                                "public_key": public_key,
                                "room_id": room_id_str,
                            },
                            to=participant_sid,
                        )
                    except Exception:
                        logger.debug(
                            "emit user_public_key failed",
                            extra={
                                "json_fields": {
                                    "user_id": user_id,
                                    "participant": participant,
                                    "room_id": room_id_str,
                                    "operation": "broadcast_user_public_key_emit",
                                },
                                "labels": {"component": "chat_keys"},
                            },
                        )
        logger.info(
            "Broadcasted user public key to participants",
            extra={
                "json_fields": {
                    "user_id": user_id,
                    "operation": "broadcast_user_public_key",
                },
                "labels": {"component": "chat_keys"},
            },
        )
    except Exception:
        logger.exception(
            "Failed to broadcast user public key",
            extra={
                "json_fields": {
                    "user_id": user_id,
                    "operation": "broadcast_user_public_key_error",
                },
                "labels": {"component": "chat_keys"},
            },
        )


async def delayed_send_chat_notification(
    user_id: str,
    message: str,
    room_id: str,
    message_title: str,
    sender_id: str | None = None,
    sender_avatar_url: str | None = None,
    encrypted_content: str = None,
    nonce: str = None,
):
    try:
        await asyncio.sleep(3)
        # iOS Communication Notifications (Messenger/iMessage style) are generated in the
        # Notification Service Extension from these structured fields.
        notification_data = {
            "type": "new_message",
            "roomId": room_id,
            "senderDisplayName": message_title,
        }
        if sender_id:
            notification_data["senderId"] = sender_id
        if sender_avatar_url:
            notification_data["senderAvatarUrl"] = sender_avatar_url
        if encrypted_content and nonce:
            # Consider not sending encrypted payload over push
            notification_data["encryptedContent"] = encrypted_content
            notification_data["nonce"] = nonce
        logger.info(
            "Sending notification",
            extra={
                "json_fields": {
                    "user_id": user_id,
                    "room_id": room_id,
                    "operation": "send_chat_notification",
                },
            },
        )
        await send_notification(user_id, message_title, message, data=notification_data)
        await mongo.notifications.insert_one(
            {"user_id": user_id, "timestamp": datetime.utcnow()}
        )
    except asyncio.CancelledError:
        logger.debug(
            "Notification task cancelled",
            extra={
                "json_fields": {
                    "user_id": user_id,
                    "room_id": room_id,
                    "operation": "delayed_send_chat_notification_cancelled",
                },
                "labels": {"component": "chat_notifications"},
            },
        )
        raise
    except Exception:
        logger.exception(
            "Error sending delayed notification",
            extra={
                "json_fields": {
                    "user_id": user_id,
                    "room_id": room_id,
                    "operation": "delayed_send_chat_notification_error",
                },
                "labels": {"component": "chat_notifications"},
            },
        )
    finally:
        notification_queue.pop(user_id, None)


async def send_chat_notification(
    user_id: str,
    message: str,
    room_id: str,
    message_title: str,
    sender_id: str | None = None,
    sender_avatar_url: str | None = None,
    encrypted_content: str = None,
    nonce: str = None,
):
    logger.info(
        "Queueing notification",
        extra={
            "json_fields": {
                "user_id": user_id,
                "room_id": room_id,
                "operation": "queue_chat_notification",
            },
            "labels": {"component": "chat_notifications"},
        },
    )

    old = notification_queue.get(user_id)
    if old:
        old.cancel()
        try:
            await asyncio.wait_for(old, timeout=1.0)
        except Exception:
            pass

    task = asyncio.create_task(
        delayed_send_chat_notification(
            user_id,
            message,
            room_id,
            message_title,
            sender_id,
            sender_avatar_url,
            encrypted_content,
            nonce,
        )
    )
    notification_queue[user_id] = task


@sio.event
async def connect(sid: str, environ: dict, auth) -> None:
    user_id = auth.get("userId")
    user_public_key = auth.get("publicKey")
    device_id = auth.get("deviceId")

    if not user_id or not device_id:
        await sio.emit("error", {"message": "Missing auth fields"}, room=sid)
        await sio.disconnect(sid)
        return

    redis = get_async_redis_client()

    # Join stable per-user and per-device rooms for cross-instance emits
    await sio.enter_room(sid, f"user:{user_id}")
    await sio.enter_room(sid, f"device:{device_id}")

    # Cache user info with TTL for fast metadata lookups
    user_info = await mongo.users.find_one({"external_user_id": user_id})
    if user_info:
        profile_pic = ""
        photos = user_info.get("photos") or []
        if photos:
            try:
                profile_pic = photos[0].get("image_url", [""])[0]
            except Exception:
                profile_pic = ""
        await redis.hset(
            user_info_key(user_id),
            mapping={
                "user_username": user_info.get("username") or "",
                "user_profile_picture": profile_pic,
            },
        )
        await redis.expire(user_info_key(user_id), 3600)

    # Atomically swap active device and fetch sids
    active_device_key = f"user_active_device:{user_id}"
    user_sids_key_name = user_sids_key(user_id)
    try:
        _prev_device, sids = await redis.eval(
            DEVICE_SWAP_LUA,
            keys=[active_device_key, user_sids_key_name],
            args=[device_id],
        )
    except Exception:
        # fallback to simple set
        await redis.set(active_device_key, device_id)
        sids = await redis.zrange(user_sids_key_name, 0, -1)
        _prev_device = None

    # Disconnect sids whose device != device_id
    # Batch get all device IDs at once instead of one by one
    if sids:
        device_keys = [sid_device_key(sid) for sid in list(sids)]
        device_values = await redis.mget(device_keys)

        # Build list of sids to disconnect
        sids_to_disconnect = []
        for existing_sid, existing_device in zip(list(sids), device_values):
            if existing_device and existing_device != device_id:
                sids_to_disconnect.append(existing_sid)

        # Disconnect and clean up in batch
        for existing_sid in sids_to_disconnect:
            try:
                await sio.emit(
                    "force_logout", {"reason": "new_device_login"}, to=existing_sid
                )
            except Exception:
                logger.debug(
                    "emit force_logout failed",
                    extra={
                        "json_fields": {
                            "sid": existing_sid,
                            "operation": "force_logout_emit",
                        },
                        "labels": {"component": "chat_connect"},
                    },
                )
            try:
                await sio.disconnect(existing_sid)
            except Exception:
                logger.debug(
                    "disconnect failed",
                    extra={
                        "json_fields": {
                            "sid": existing_sid,
                            "operation": "force_logout_disconnect",
                        },
                        "labels": {"component": "chat_connect"},
                    },
                )

        # Batch delete all Redis keys for disconnected sids
        if sids_to_disconnect:
            pipe = redis.pipeline()
            for existing_sid in sids_to_disconnect:
                pipe.zrem(user_sids_key_name, existing_sid)
                pipe.delete(sid_user_key(existing_sid))
                pipe.delete(sid_device_key(existing_sid))
            await pipe.execute()

    # Bookkeeping for this connection (with TTLs) - batch operations
    pipe = redis.pipeline()
    pipe.set(sid_user_key(sid), user_id, ex=PRESENCE_SID_TTL_SECONDS)
    pipe.set(sid_device_key(sid), device_id, ex=PRESENCE_SID_TTL_SECONDS)
    await pipe.execute()

    # Mark presence (ZSET) + TTL
    await _touch_presence(redis, user_id, sid)

    if user_public_key:
        await redis.set(f"user_public_key:{user_id}", user_public_key)
        # Proactively notify participants in the user's chat rooms of the updated key
        await broadcast_user_public_key(user_id, user_public_key)


@sio.event
async def disconnect(sid: str) -> None:
    redis = get_async_redis_client()
    # Resolve user for this sid and remove mappings - batch operations
    user_id = await redis.get(sid_user_key(sid))

    pipe = redis.pipeline()
    if user_id:
        pipe.zrem(user_sids_key(user_id), sid)
    pipe.delete(sid_user_key(sid))
    pipe.delete(sid_device_key(sid))
    await pipe.execute()

    logger.info(
        "Client disconnected",
        extra={
            "json_fields": {"sid": sid, "operation": "disconnect"},
            "labels": {"component": "chat_connect"},
        },
    )


@sio.event
async def heartbeat(sid: str) -> None:
    redis = get_async_redis_client()
    try:
        # Batch get user_id and device_id
        pipe = redis.pipeline()
        pipe.get(sid_user_key(sid))
        pipe.get(sid_device_key(sid))
        results = await pipe.execute()

        user_id = results[0]
        device_id = results[1]

        # Batch expire and update operations
        if user_id or device_id:
            pipe = redis.pipeline()
            if user_id:
                pipe.expire(sid_user_key(sid), PRESENCE_SID_TTL_SECONDS)
            if device_id:
                pipe.expire(sid_device_key(sid), PRESENCE_SID_TTL_SECONDS)
            await pipe.execute()
            if user_id:
                await _touch_presence(redis, user_id, sid)

        logger.info(
            "Heartbeat",
            extra={
                "json_fields": {
                    "sid": sid,
                    "user_id": user_id or "",
                    "operation": "heartbeat",
                },
                "labels": {"component": "chat_connect"},
            },
        )
    except Exception:
        logger.exception(
            "Error in heartbeat",
            extra={
                "json_fields": {"sid": sid, "operation": "heartbeat_error"},
                "labels": {"component": "chat_connect"},
            },
        )


@sio.event
async def private_message(sid: str, data: Dict[str, str]) -> None:
    recipient = data["recipient"]
    temporary_id = data["temporary_id"]
    room_id = data["room_id"]

    # Support both encrypted and plain content for virtual users
    encrypted_content = data.get("encrypted_content")
    nonce = data.get("nonce")
    plain_content = data.get("plain_content")

    logger.info(
        "Forwarding message",
        extra={
            "json_fields": {
                "sid": sid,
                "recipient": recipient,
                "room_id": room_id,
                "is_plain": plain_content is not None,
                "operation": "private_message_forward",
            },
            "labels": {"component": "chat_messages"},
        },
    )

    redis = get_async_redis_client()

    # Resolve sender + compute recipient presence via TTL'd heartbeat window
    sender = await redis.get(sid_user_key(sid))
    online_count = await _prune_and_count_online(redis, recipient)
    is_online = online_count > 0
    logger.info(
        "Recipient presence checked",
        extra={
            "json_fields": {
                "sender": sender or "",
                "recipient": recipient,
                "recipient_online": is_online,
                "active_sids": online_count,
                "operation": "presence_check_for_message",
            },
            "labels": {"component": "chat_presence"},
        },
    )

    # Check if recipient is a virtual user (AI character)
    recipient_user = await mongo.users.find_one({"external_user_id": recipient})
    is_virtual_recipient = recipient_user and recipient_user.get("is_virtual", False)

    # If messaging a virtual user, buffer the message for batched AI response
    if is_virtual_recipient and plain_content:
        from ment_api.services.ai_character_service import get_character_doc_by_user_id

        character = await get_character_doc_by_user_id(recipient)
        if character and character.get("chat_enabled", True):
            # Buffer the message instead of immediate response
            await buffer_ai_message(
                redis=redis,
                room_id=room_id,
                sender_id=sender,
                recipient_id=recipient,
                message=plain_content,
            )
            logger.info(
                "Buffered AI message",
                extra={
                    "json_fields": {
                        "room_id": room_id,
                        "sender_id": sender,
                        "recipient_id": recipient,
                        "operation": "buffer_ai_message",
                    },
                    "labels": {"component": "ai_chat_buffer"},
                },
            )

    # Emit to recipient if online
    if is_online and not is_virtual_recipient:
        # Get sender info from cache (fallback to empty strings)
        sender_info = await redis.hgetall(user_info_key(sender)) if sender else {}
        sender_username = sender_info.get("user_username", "")
        sender_profile_picture = sender_info.get("user_profile_picture", "")

        message_payload = {
            "sender": sender,
            "sender_username": sender_username,
            "sender_profile_picture": sender_profile_picture,
            "room_id": room_id,
            "temporary_id": temporary_id,
        }

        # Include either encrypted or plain content
        if plain_content is not None:
            message_payload["plain_content"] = plain_content
        else:
            message_payload["encrypted_content"] = encrypted_content
            message_payload["nonce"] = nonce

        await sio.emit(
            "private_message",
            message_payload,
            room=f"user:{recipient}",
        )
    elif not is_virtual_recipient:
        # Recipient is offline - send notification
        # Get sender username from cache first, fallback to DB
        sender_info = await redis.hgetall(user_info_key(sender)) if sender else {}
        message_title = sender_info.get("user_username")
        sender_profile_picture = sender_info.get("user_profile_picture", "")
        if not message_title:
            user_data = await mongo.users.find_one({"external_user_id": sender})
            message_title = (user_data.get("username") if user_data else None) or "Ment"

        await send_chat_notification(
            recipient,
            "ახალი შეტყობინება",
            room_id,
            message_title,
            sender,
            sender_profile_picture,
            encrypted_content,
            nonce,
        )

    # Store message asynchronously in background
    message_doc = {
        "author_id": sender,
        "recipient_id": recipient,
        "room_id": ObjectId(room_id),
        "message_state": MessageState.SENT,
    }

    # Store either encrypted or plain content
    if plain_content is not None:
        message_doc["plain_content"] = plain_content
    else:
        message_doc["encrypted_content"] = encrypted_content
        message_doc["nonce"] = nonce

    await mongo.chat_messages.insert_one(message_doc)


@sio.event
async def notify_single_message_seen(sid: str, data: Dict[str, str]) -> None:
    recipient = data["recipient"]
    temporary_id = data["temporary_id"]
    redis = get_async_redis_client()

    sender = await redis.get(sid_user_key(sid))
    is_online = (await _prune_and_count_online(redis, recipient)) > 0

    if is_online:
        await sio.emit(
            "notify_single_message_seen",
            {
                "sender": sender,
                "message_state": MessageState.READ,
                "temporary_id": temporary_id,
            },
            room=f"user:{recipient}",
        )


@sio.event
async def check_user_connection(sid: str, data: Dict[str, str]) -> None:
    is_that_connected_id = data.get("is_that_connected_id")
    redis = get_async_redis_client()
    is_connected = False
    if is_that_connected_id:
        try:
            is_connected = (await _prune_and_count_online(redis, is_that_connected_id)) > 0
        except Exception:
            is_connected = False
    await sio.emit("user_connection_status", {"is_connected": is_connected}, to=sid)


@router.post(
    "/update-messages",
    status_code=201,
    responses={500: {"description": "Generation error"}},
)
def update_message_state(update_request: UpdateMessageRequest) -> dict[str, bool]:
    messages = update_request.messages
    message_state_channel.put(messages)
    return {"ok": True}


class GetMessagesResponse(BaseModel):
    messages: List[ChatMessage]
    page: int
    page_size: int
    previous_cursor: Optional[int] = None
    next_cursor: Optional[int] = None


@router.get(
    "/messages",
    response_model=GetMessagesResponse,
    responses={500: {"description": "Generation error"}},
)
async def get_messages(
    request: Request,
    room_id: CustomObjectId = Query(),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1),
):
    external_user_id = request.state.supabase_user_id
    skip = (page - 1) * page_size

    redis = get_async_redis_client()
    # Get the user's public key timestamp
    timestamp = await redis.get(f"user_public_key_timestamp:{external_user_id}")

    if timestamp:
        timestamp = datetime.fromisoformat(timestamp)
        # Convert timestamp to ObjectId format
        timestamp_id = ObjectId.from_datetime(timestamp)
    else:
        timestamp = datetime(1970, 1, 1, tzinfo=timezone.utc)
        timestamp_id = ObjectId.from_datetime(timestamp)

    pipeline = [
        {"$match": {"_id": {"$gte": timestamp_id}, "room_id": room_id}},
        {"$sort": {"_id": -1}},
        {"$skip": skip},
        {"$limit": page_size},
    ]

    chat_messages = await mongo.chat_messages.aggregate(pipeline)
    messages_list = [ChatMessage(**message) for message in chat_messages]
    messages_list.reverse()
    # Calculate previous and next cursors
    previous_cursor = page - 1 if isinstance(page, int) and page > 1 else None
    next_cursor = (
        page + 1 if isinstance(page, int) and len(messages_list) == page_size else None
    )

    return GetMessagesResponse(
        messages=messages_list,
        page=page,
        page_size=page_size,
        previous_cursor=previous_cursor,
        next_cursor=next_cursor,
    )


class ChatRoom(BaseModel):
    id: str
    participants: List[User]
    created_at: str
    updated_at: str
    target_user_id: Optional[str] = None
    user_public_key: Optional[str] = None
    last_message: Optional[ChatMessage] = None
    is_friend: bool = False


class CreateChatRoomRequest(BaseModel):
    target_user_id: str
    user_public_key: str


class GetUserChatRoomsResponse(BaseModel):
    chat_rooms: List[ChatRoom]


@router.get(
    "/chat-rooms",
    response_model=GetUserChatRoomsResponse,
    operation_id="get_user_chat_rooms",
)
async def get_user_chat_rooms(request: Request):
    try:
        external_user_id = request.state.supabase_user_id
        redis = get_async_redis_client()
        # Get the user's public key timestamp
        timestamp_str = await redis.get(f"user_public_key_timestamp:{external_user_id}")
        if timestamp_str:
            datetime.fromisoformat(timestamp_str)
        else:
            datetime(1970, 1, 1, tzinfo=timezone.utc)
        pipeline = [
            {"$match": {"participants": external_user_id}},
            {
                "$lookup": {
                    "from": "chat_messages",
                    "let": {"room_id": "$_id"},
                    "pipeline": [
                        {
                            "$match": {
                                "$expr": {"$eq": ["$room_id", "$$room_id"]},
                            }
                        },
                        {"$sort": {"_id": -1}},
                        {"$limit": 1},
                    ],
                    "as": "last_message",
                }
            },
            {"$match": {"last_message": {"$ne": []}}},  # Only keep rooms with messages
            {
                "$lookup": {
                    "from": "users",
                    "localField": "participants",
                    "foreignField": "external_user_id",
                    "as": "participants_info",
                }
            },
            {
                "$project": {
                    "_id": 1,
                    "participants_info": 1,
                    "created_at": 1,
                    "updated_at": 1,
                    "last_message": {"$arrayElemAt": ["$last_message", 0]},
                }
            },
        ]

        chat_rooms = await mongo.chat_rooms.aggregate(pipeline)
        response_rooms = []

        # Get all target user IDs first
        target_user_ids = []
        rooms_list = []
        for room in chat_rooms:
            rooms_list.append(room)
            participants = room["participants_info"]
            target_user = next(
                (p for p in participants if p["external_user_id"] != external_user_id),
                None,
            )
            if target_user:
                target_user_ids.append(target_user["external_user_id"])

        # Get all target users' public keys from Redis in batch
        if target_user_ids:
            redis_keys = [f"user_public_key:{user_id}" for user_id in target_user_ids]
            public_keys = await redis.mget(redis_keys)
            public_key_map = dict(zip(target_user_ids, public_keys))
        else:
            public_key_map = {}

        for room in rooms_list:
            participants = room["participants_info"]
            participants_obj = [User(**p) for p in participants]

            target_user = next(
                (p for p in participants if p["external_user_id"] != external_user_id),
                None,
            )
            target_user_id = target_user["external_user_id"] if target_user else None
            target_public_key = (
                public_key_map.get(target_user_id) if target_user_id else None
            )

            room_obj = ChatRoom(
                id=str(room["_id"]),
                participants=participants_obj,
                created_at=room["created_at"],
                updated_at=room["updated_at"],
                target_user_id=target_user_id,
                user_public_key=target_public_key,
                last_message=(
                    ChatMessage(**room["last_message"])
                    if room.get("last_message")
                    else None
                ),
            )
            response_rooms.append(room_obj)

        return GetUserChatRoomsResponse(chat_rooms=response_rooms)

    except Exception as e:
        print("Error in get_user_chat_rooms:", e)
        return []


class CreateChatRoomResponse(BaseModel):
    success: bool
    chat_room_id: str
    target_public_key: Optional[str] = None


@router.post(
    "/create-chat-room",
    response_model=CreateChatRoomResponse,
    operation_id="create_chat_room",
)
async def create_chat_room(
    request: Request,
    create_request: CreateChatRoomRequest,
):
    external_user_id = request.state.supabase_user_id
    redis = get_async_redis_client()
    async with mongo_client.db.client.start_session() as session:
        async with await session.start_transaction():
            # Check if room already exists with these participants
            existing_room = await mongo.chat_rooms.find_one(
                {
                    "participants": {
                        "$all": [external_user_id, create_request.target_user_id]
                    }
                },
                session=session,
            )

            redis_key = f"user_public_key:{external_user_id}"
            await redis.set(redis_key, create_request.user_public_key)

            # Try to get target user's public key
            target_key = await redis.get(
                f"user_public_key:{create_request.target_user_id}"
            )
            target_public_key = target_key if target_key else None

            if existing_room:
                return {
                    "success": True,
                    "chat_room_id": str(existing_room["_id"]),
                    "target_public_key": target_public_key,
                }

            # Create a new chat room document if none exists
            now = datetime.utcnow().isoformat()
            chat_room = {
                "participants": [external_user_id, create_request.target_user_id],
                "created_at": now,
                "updated_at": now,
                "expiration_task_name": None,
            }

            result = await mongo.chat_rooms.insert_one(chat_room, session=session)
            chat_room_id = str(result.inserted_id)

            # Create expiration task
            task_name = f"expire-chat-room-{chat_room_id}"

            # Update chat room with task name
            await mongo.chat_rooms.update_one(
                {"_id": result.inserted_id},
                {"$set": {"expiration_task_name": task_name}},
                session=session,
            )

            return {
                "success": True,
                "chat_room_id": chat_room_id,
                "target_public_key": target_public_key,
            }


@router.post("/expire-chat-room")
async def expire_chat_room(request: dict = Body(...)):
    chat_room_id = request.get("chat_room_id")
    if not chat_room_id:
        raise HTTPException(status_code=400, detail="Chat room ID is required")

    chat_room = await mongo.chat_rooms.find_one({"_id": ObjectId(chat_room_id)})
    if not chat_room:
        return {"message": "Chat room not found or already expired"}

    # Delete the chat room
    await mongo.chat_rooms.delete_one({"_id": ObjectId(chat_room_id)})
    return {"message": "Chat room expired and removed successfully"}


class SendPublicKeyRequest(BaseModel):
    user_id: str
    public_key: str
    device_id: Optional[str] = None


@router.post("/send-public-key")
async def send_public_key(request: SendPublicKeyRequest):
    # Updated to use async Redis
    from ment_api.services.redis_service import get_async_redis_client
    # Save the last timestamp of when user generated the keys, so that we can exclude old chat messages which was generated with the old key

    redis = get_async_redis_client()

    # Batch all Redis writes using pipeline
    now_iso = datetime.utcnow().isoformat()
    pipe = redis.pipeline()
    if request.device_id:
        pipe.set(f"user_active_device:{request.user_id}", request.device_id)
    pipe.set(f"user_public_key:{request.user_id}", request.public_key)
    pipe.set(f"user_public_key_timestamp:{request.user_id}", now_iso)
    await pipe.execute()

    # If we know the device_id, proactively disconnect other device sessions for this user
    if request.device_id:
        try:
            sids = await redis.zrange(user_sids_key(request.user_id), 0, -1)
            if sids:
                # Batch get all device IDs at once
                device_keys = [sid_device_key(sid) for sid in list(sids)]
                device_values = await redis.mget(device_keys)

                # Build list of sids to disconnect
                sids_to_disconnect = []
                for existing_sid, existing_device in zip(list(sids), device_values):
                    if existing_device and existing_device != request.device_id:
                        sids_to_disconnect.append(existing_sid)

                # Disconnect sessions
                for existing_sid in sids_to_disconnect:
                    try:
                        await sio.emit(
                            "force_logout",
                            {"reason": "new_device_login"},
                            to=existing_sid,
                        )
                    except Exception:
                        pass
                    await sio.disconnect(existing_sid)

                # Batch cleanup Redis keys
                if sids_to_disconnect:
                    pipe = redis.pipeline()
                    for existing_sid in sids_to_disconnect:
                        pipe.zrem(user_sids_key(request.user_id), existing_sid)
                        pipe.delete(sid_user_key(existing_sid))
                        pipe.delete(sid_device_key(existing_sid))
                    await pipe.execute()
        except Exception:
            # Best-effort cleanup
            pass

    # Notify participants in all chat rooms for this user that the key was updated
    try:
        await broadcast_user_public_key(request.user_id, request.public_key)
    except Exception:
        # Do not fail the request on notification errors
        logger.debug(
            "broadcast_user_public_key failed after send_public_key",
            extra={
                "json_fields": {
                    "user_id": request.user_id,
                    "operation": "send_public_key_broadcast",
                },
                "labels": {"component": "chat_keys"},
            },
        )

    return {"success": True}


@router.get(
    "/message-chat-room",
    response_model=ChatRoom,
    operation_id="get_message_chat_room",
)
async def get_chat_room(
    request: Request,
    room_id: str,
):
    external_user_id = request.state.supabase_user_id

    redis = get_async_redis_client()
    chat_room = await mongo.chat_rooms.find_one({"_id": ObjectId(room_id)})

    if not chat_room:
        raise HTTPException(status_code=404, detail="Chat room not found")

    users = await mongo.users.find_all(
        {"external_user_id": {"$in": chat_room["participants"]}}
    )

    target_user = next(
        (user for user in users if user["external_user_id"] != external_user_id),
        None,
    )

    user_list = [User(**user) for user in users]
    target_key = await redis.get(f"user_public_key:{target_user['external_user_id']}")
    target_public_key = target_key if target_key else None

    is_friend = await mongo.friendships.find_one(
        {
            "user_id": external_user_id,
            "friend_id": target_user["external_user_id"],
        }
    )

    return ChatRoom(
        id=str(chat_room["_id"]),
        participants=user_list,
        created_at=chat_room["created_at"],
        updated_at=chat_room["updated_at"],
        target_user_id=target_user["external_user_id"],
        user_public_key=target_public_key,
        is_friend=is_friend is not None,
    )


# async def get_random_unseen_feed_item(
#     feed_id: str, user_id: str, redis: AsyncRedis
# ) -> Optional[FeedPost]:
#     viewer_key = f"task_viewers:{feed_id}"
#     user_seen_key = f"user_seen:{feed_id}:{user_id}"
#     user_seen_posts = await redis.smembers(user_seen_key)
#     user_seen_post_ids = (
#         [ObjectId(post_id) for post_id in user_seen_posts] if user_seen_posts else []
#     )

#     # Get timestamp from 1 minute ago
#     one_minute_ago = datetime.utcnow() - timedelta(minutes=1)

#     pipeline = [
#         {
#             "$match": {
#                 "feed_id": ObjectId(feed_id),
#                 "state": {
#                     "$in": [
#                         VerificationState.READY_FOR_USE,
#                         VerificationState.PROCESSING_MEDIA,
#                     ]
#                 },
#                 "is_public": True,
#                 "_id": {"$nin": user_seen_post_ids},
#                 "last_modified_date": {"$gte": one_minute_ago},
#             }
#         },
#         {
#             "$lookup": {
#                 "from": "users",
#                 "localField": "assignee_user_id",
#                 "foreignField": "external_user_id",
#                 "as": "assignee_user",
#             }
#         },
#         {"$unwind": "$assignee_user"},
#         {"$sort": {"last_modified_date": -1}},
#         {"$limit": 10},  # Get latest 10 posts
#         {"$sample": {"size": 1}},  # Randomly select 1
#     ]

#     results = await mongo.verifications.aggregate(pipeline)
#     posts = [FeedPost(**post) for post in results]

#     if posts:
#         # Add to global seen posts in Redis
#         await redis.sadd(viewer_key, str(posts[0].id))
#         # Add to user specific seen posts
#         await redis.sadd(user_seen_key, str(posts[0].id))
#         return posts[0]
#     return None


class PublicKeyEntry(BaseModel):
    user_id: str
    username: Optional[str] = None
    public_key: Optional[str]
    timestamp: Optional[str]
    active_device_id: Optional[str]
    is_connected: bool = False


class PublicKeysResponse(BaseModel):
    keys: List[PublicKeyEntry]


@router.get("/public-keys", response_model=PublicKeysResponse)
async def list_public_keys() -> PublicKeysResponse:
    redis = get_async_redis_client()

    # Get all user_public_key:* keys first
    key_names = await redis.keys("user_public_key:*")

    if not key_names:
        return PublicKeysResponse(keys=[])

    # Extract user_ids and build all keys we need to fetch
    user_ids = [key_name.split(":", 1)[1] for key_name in key_names]

    # Fetch usernames for these users from Mongo in a single query
    users = await mongo.users.find_all({"external_user_id": {"$in": user_ids}})
    user_map = {u["external_user_id"]: u for u in users}

    # Build list of all keys to fetch in one mget call
    all_keys = []
    for user_id in user_ids:
        all_keys.extend(
            [
                f"user_public_key:{user_id}",
                f"user_public_key_timestamp:{user_id}",
                f"user_active_device:{user_id}",
            ]
        )

    # Fetch all values in one Redis call
    all_values = await redis.mget(all_keys)

    # Check connection status for all users
    connection_status = {}
    for user_id in user_ids:
        try:
            is_connected = (await _prune_and_count_online(redis, user_id)) > 0
            connection_status[user_id] = is_connected
        except Exception:
            connection_status[user_id] = False

    # Parse results and build entries
    entries: List[PublicKeyEntry] = []
    for i, user_id in enumerate(user_ids):
        # Each user has 3 values: public_key, timestamp, active_device_id
        base_index = i * 3
        public_key = all_values[base_index]
        timestamp = all_values[base_index + 1]
        active_device_id = all_values[base_index + 2]
        username = (user_map.get(user_id) or {}).get("username")

        entries.append(
            PublicKeyEntry(
                user_id=user_id,
                username=username,
                public_key=public_key,
                timestamp=timestamp,
                active_device_id=active_device_id,
                is_connected=connection_status.get(user_id, False),
            )
        )

    # Sort entries by timestamp (most recent first)
    entries.sort(key=lambda entry: entry.timestamp or "", reverse=True)

    return PublicKeysResponse(keys=entries)


@router.get("/public-keys/ui", response_class=HTMLResponse)
async def public_keys_ui() -> HTMLResponse:
    data = await list_public_keys()

    # Sort entries by timestamp (most recent first)
    sorted_entries = sorted(
        data.keys, key=lambda entry: entry.timestamp or "", reverse=True
    )

    # Simple HTML table
    rows = "".join(
        f"<tr>"
        f"<td>{entry.user_id}</td>"
        f"<td>{entry.username or ''}</td>"
        f"<td><code style='font-size:12px'>{(entry.public_key or '')[:32]}..."
        f"</code></td>"
        f"<td>{entry.timestamp or ''}</td>"
        f"<td>{entry.active_device_id or ''}</td>"
        f"<td><span style='color: {'green' if entry.is_connected else 'red'}; font-weight: bold;'>{'🟢 Online' if entry.is_connected else '🔴 Offline'}</span></td>"
        f"</tr>"
        for entry in sorted_entries
    )
    html = f"""
    <html>
      <head>
        <title>Public Keys</title>
        <style>
          body {{ font-family: -apple-system, BlinkMacSystemFont, Segoe UI, Roboto, Helvetica, Arial, sans-serif; padding: 16px; }}
          table {{ border-collapse: collapse; width: 100%; }}
          th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
          th {{ background-color: #f2f2f2; }}
          code {{ word-break: break-all; }}
        </style>
      </head>
      <body>
        <h1>Public Keys</h1>
        <table>
          <thead>
            <tr>
              <th>User ID</th>
              <th>Username</th>
              <th>Public Key</th>
              <th>Updated At</th>
              <th>Active Device</th>
              <th>Connection Status</th>
            </tr>
          </thead>
          <tbody>
            {rows}
          </tbody>
        </table>
      </body>
    </html>
    """
    return HTMLResponse(content=html)


# ============================================================================
# AI Message Buffer Worker (Background Task)
# ============================================================================


async def process_ai_buffer(room_id: str, user_id: str) -> None:
    """
    Process a ready AI message buffer and generate response.
    
    This function:
    1. Acquires distributed lock
    2. Gets and clears all buffered messages
    3. Generates AI response to all messages
    4. Emits response via Socket.IO
    5. Stores response in database
    """
    redis = get_async_redis_client()
    
    # Try to acquire lock (only one instance processes)
    if not await acquire_buffer_lock(redis, room_id, user_id):
        return  # Another instance is handling it
    
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
                    "labels": {"component": "ai_chat_buffer"},
                },
            )
            return
        
        # Get all messages and clear buffer atomically
        messages = await get_and_clear_buffer(redis, room_id, user_id)
        
        if not messages:
            return
        
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
                "labels": {"component": "ai_chat_buffer"},
            },
        )
        
        # Get AI character
        from ment_api.services.ai_character_service import (
            generate_chat_response,
            get_character_doc_by_user_id,
        )
        
        character = await get_character_doc_by_user_id(recipient_id)
        if not character:
            logger.error(
                "Character not found for buffer processing",
                extra={
                    "json_fields": {
                        "room_id": room_id,
                        "recipient_id": recipient_id,
                        "operation": "process_ai_buffer_no_character",
                    },
                    "labels": {"component": "ai_chat_buffer"},
                },
            )
            return
        
        # Generate AI response to ALL messages
        ai_response = await generate_chat_response(
            character=character,
            user_id=user_id,
            user_messages=messages,  # Pass list of messages
            room_id=ObjectId(room_id),
        )
        
        # Get AI character info for notifications
        character_name = character.get("name", "")
        face_images = character.get("face_images", [])
        ai_profile_picture = face_images[0] if face_images else ""
        
        # Check if user is online
        online_count = await _prune_and_count_online(redis, user_id)
        is_user_online = online_count > 0
        
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
                # No encryption for AI messages
                encrypted_content=None,
                nonce=None,
            )
            logger.info(
                "Sent AI response notification to offline user",
                extra={
                    "json_fields": {
                        "room_id": room_id,
                        "user_id": user_id,
                        "character_name": character_name,
                        "operation": "ai_buffer_offline_notification",
                    },
                    "labels": {"component": "ai_chat_buffer"},
                },
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
                "labels": {"component": "ai_chat_buffer"},
            },
        )
        
    except Exception as e:
        logger.error(
            f"Error processing AI buffer: {e}",
            extra={
                "json_fields": {
                    "room_id": room_id,
                    "user_id": user_id,
                    "error": str(e),
                    "operation": "process_ai_buffer_error",
                },
                "labels": {"component": "ai_chat_buffer", "severity": "high"},
            },
        )
    finally:
        await release_buffer_lock(redis, room_id, user_id)


async def ai_buffer_worker() -> None:
    """
    Background worker that polls for ready AI message buffers.
    
    This runs on every instance and uses distributed locks to ensure
    only one instance processes each buffer.
    """
    logger.info(
        "AI buffer worker started",
        extra={
            "json_fields": {"operation": "ai_buffer_worker_start"},
            "labels": {"component": "ai_chat_buffer"},
        },
    )
    
    redis = get_async_redis_client()
    
    while True:
        try:
            await asyncio.sleep(1)  # Check every second
            
            # Get all active buffers
            active = await get_active_ai_buffers(redis)
            if not active:
                continue
            
            now = time.time()
            
            for key in active:
                try:
                    # Parse room_id:user_id from key
                    parts = key.split(":", 1)
                    if len(parts) != 2:
                        continue
                    room_id, user_id = parts
                    
                    # Check if debounce period has passed
                    last_ts = await get_buffer_last_timestamp(redis, room_id, user_id)
                    if last_ts is None:
                        # Buffer was already processed, clean up tracking
                        await redis.srem("ai_active_buffers", key)
                        continue
                    
                    if now - last_ts >= AI_MESSAGE_DEBOUNCE_SECONDS:
                        # Time to process this buffer
                        await process_ai_buffer(room_id, user_id)
                        
                except Exception as e:
                    logger.error(
                        f"Error checking buffer {key}: {e}",
                        extra={
                            "json_fields": {
                                "buffer_key": key,
                                "error": str(e),
                                "operation": "ai_buffer_worker_check_error",
                            },
                            "labels": {"component": "ai_chat_buffer"},
                        },
                    )
                    
        except Exception as e:
            logger.error(
                f"AI buffer worker error: {e}",
                extra={
                    "json_fields": {
                        "error": str(e),
                        "operation": "ai_buffer_worker_error",
                    },
                    "labels": {"component": "ai_chat_buffer", "severity": "high"},
                },
            )
            await asyncio.sleep(5)  # Wait before retrying on error


def init_ai_buffer_worker() -> asyncio.Task:
    """Initialize the AI buffer worker background task."""
    return asyncio.create_task(ai_buffer_worker())


async def cleanup_ai_buffer_worker(task: asyncio.Task) -> None:
    """Clean up the AI buffer worker task."""
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception:
        logger.error(
            "Error cleaning up AI buffer worker",
            extra={
                "json_fields": {"operation": "ai_buffer_worker_cleanup_error"},
                "labels": {"component": "ai_chat_buffer"},
            },
            exc_info=True,
        )
    logger.info(
        "AI buffer worker stopped",
        extra={
            "json_fields": {"operation": "ai_buffer_worker_stop"},
            "labels": {"component": "ai_chat_buffer"},
        },
    )
