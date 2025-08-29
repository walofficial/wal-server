import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import DefaultDict, Dict, List, Optional, Set

import socketio
from bson import ObjectId
from exponent_server_sdk import PushClient
from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.params import Query
from pydantic import BaseModel
from redis import Redis

from ment_api.models.chat_message import ChatMessage
from ment_api.models.message_state import MessageState
from ment_api.models.update_message_request import UpdateMessageRequest
from ment_api.models.user import User
from ment_api.models.verification_state import VerificationState
from ment_api.persistence import mongo, mongo_client
from ment_api.services.notification_service import send_notification
from ment_api.models.location_feed_post import FeedPost
from ment_api.services.redis_service import get_redis_client
from ment_api.workers.message_state_worker import message_state_channel

logger = logging.getLogger(__name__)

sio = socketio.AsyncServer(async_mode="asgi", cors_allowed_origins="*")
router = APIRouter(prefix="/chat", tags=["chat"])

user_connections: Dict[str, Set[str]] = {}
feed_connections: DefaultDict[str, Set[str]] = defaultdict(set)
push_client = PushClient()

notification_queue: Dict[str, asyncio.Task] = {}


async def delayed_send_chat_notification(
    user_id: str, message: str, room_id: str, message_title: str
):
    await asyncio.sleep(3)  # 3-second delay
    await send_notification(
        user_id,
        message_title,
        message,
        data={"type": "new_message", "roomId": room_id},
    )
    # Update the last notification timestamp
    await mongo.notifications.insert_one(
        {"user_id": user_id, "timestamp": datetime.utcnow()}
    )
    if user_id in notification_queue:
        del notification_queue[user_id]


async def send_chat_notification(
    user_id: str, message: str, room_id: str, message_title: str
):
    logger.info(f"Queueing notification for {user_id}")

    if user_id in notification_queue:
        notification_queue[user_id].cancel()

    notification_queue[user_id] = asyncio.create_task(
        delayed_send_chat_notification(user_id, message, room_id, message_title)
    )


@sio.event
async def connect(sid: str, environ: dict, auth) -> None:
    user_id = auth.get("userId")
    user_public_key = auth.get("publicKey")  # Get public key from auth

    if not user_id:
        await sio.emit("error", {"message": "Missing userId in auth"}, room=sid)
        await sio.disconnect(sid)
        return

    if user_id not in user_connections:
        user_connections[user_id] = {sid}
    else:
        user_connections[user_id].add(sid)

    # Save task connection and public key in Redis
    redis = get_redis_client()
    if user_public_key:
        user_key = f"user_public_key:{user_id}"
        redis.set(user_key, user_public_key)

        # Find all chat rooms for this user
        chat_rooms = await mongo.chat_rooms.find_all({"participants": user_id})
        for room in chat_rooms:
            for participant in room["participants"]:
                if (
                    str(participant) != str(user_id)
                    and str(participant) in user_connections
                ):
                    for participant_sid in user_connections[str(participant)]:
                        await sio.emit(
                            "user_public_key",
                            {
                                "user_id": str(user_id),
                                "public_key": user_public_key,
                                "room_id": str(room["_id"]),
                            },
                            to=participant_sid,
                        )


@sio.event
async def disconnect(sid: str) -> None:
    for user, connections in list(user_connections.items()):
        if sid in connections:
            connections.remove(sid)
            if not connections:
                del user_connections[user]

    redis = get_redis_client()
    for feed_id, connections in list(feed_connections.items()):
        if sid in connections:
            connections.remove(sid)
            if not connections:
                del feed_connections[feed_id]
                # Remove from Redis
                redis_key = f"feed_connections:{feed_id}"
                redis.srem(redis_key, sid)

    print(f"Client disconnected: {sid}")


@sio.event
async def private_message(sid: str, data: Dict[str, str]) -> None:
    recipient = data["recipient"]
    encrypted_content = data["encrypted_content"]
    nonce = data["nonce"]
    temporary_id = data["temporary_id"]
    room_id = data["room_id"]
    sender = None

    logger.info(f"Forwarding encrypted message from {sid} to {recipient}")

    for user, user_sids in user_connections.items():
        if sid in user_sids:
            sender = user
            break
    if recipient in user_connections:
        # Recipient is online - forward the encrypted message
        recipient_sids = user_connections[recipient]
        for recipient_sid in recipient_sids:
            await sio.emit(
                "private_message",
                {
                    "sender": sender,
                    "encrypted_content": encrypted_content,
                    "nonce": nonce,
                    "temporary_id": temporary_id,
                },
                to=recipient_sid,
            )
    else:
        # Recipient is offline - just send a notification that there's a new message
        message_title = (await mongo.users.find_one({"external_user_id": sender}))[
            "username"
        ] or "Ment"
        await send_chat_notification(recipient, "მესიჯი", room_id, message_title)

    await mongo.chat_messages.insert_one(
        {
            "author_id": sender,
            "recipient_id": recipient,
            "room_id": room_id,
            "encrypted_content": encrypted_content,
            "nonce": nonce,
            "message_state": MessageState.SENT,
        }
    )


@sio.event
async def notify_single_message_seen(sid: str, data: Dict[str, str]) -> None:
    recipient = data["recipient"]
    temporary_id = data["temporary_id"]
    sender = None
    for user, user_sids in user_connections.items():
        if sid in user_sids:
            sender = user
            break

    if recipient in user_connections:
        recipient_sids = user_connections[recipient]
        for recipient_sid in recipient_sids:
            await sio.emit(
                "notify_single_message_seen",
                {
                    "sender": sender,
                    "message_state": MessageState.READ,
                    "temporary_id": temporary_id,
                },
                to=recipient_sid,
            )


@sio.event
async def check_user_connection(sid: str, data: Dict[str, str]) -> None:
    is_that_connected_id = data.get("is_that_connected_id")
    if is_that_connected_id in user_connections:
        await sio.emit("user_connection_status", {"is_connected": True}, to=sid)
    else:
        await sio.emit("user_connection_status", {"is_connected": False}, to=sid)


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
    room_id: str = Query(),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1),
):
    external_user_id = request.state.supabase_user_id
    skip = (page - 1) * page_size

    redis = get_redis_client()
    # Get the user's public key timestamp
    redis.get(f"user_public_key_timestamp:{external_user_id}")
    pipeline = [
        {"$match": {"room_id": room_id}},
        {"$sort": {"_id": -1}},
        {"$skip": skip},
        {"$limit": page_size},
    ]

    chat_messages = await mongo.chat_messages.aggregate(pipeline)
    messages_list = [ChatMessage(**message) for message in chat_messages]
    messages_list.reverse()  # Reverse the array of messages

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
    target_user_id: str = None
    user_public_key: str = None
    last_message: Optional[ChatMessage] = None


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
        redis = get_redis_client()
        # Get the user's public key timestamp
        timestamp_str = redis.get(f"user_public_key_timestamp:{external_user_id}")
        if timestamp_str:
            datetime.fromisoformat(timestamp_str)
        else:
            datetime(1970, 1, 1, tzinfo=timezone.utc)
        pipeline = [
            {"$match": {"participants": external_user_id}},
            {"$addFields": {"room_id_str": {"$toString": "$_id"}}},
            {
                "$lookup": {
                    "from": "chat_messages",
                    "let": {"room_id": "$room_id_str"},
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
            public_keys = redis.mget(redis_keys)
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
    target_public_key: str


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
    redis = get_redis_client()
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

            # Store the requesting user's public key in Redis
            redis_key = f"user_public_key:{external_user_id}"
            redis.set(redis_key, create_request.user_public_key)

            # Try to get target user's public key
            target_key = redis.get(f"user_public_key:{create_request.target_user_id}")
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


@router.post("/send-public-key")
async def send_public_key(request: SendPublicKeyRequest):
    redis = get_redis_client()
    redis_key = f"user_public_key:{request.user_id}"
    redis.set(redis_key, request.public_key)

    # Save the last timestamp of when user generated the keys, so that we can exclude old chat messages which was generated with the old key
    current_time = datetime.utcnow()
    redis.set(f"user_public_key_timestamp:{request.user_id}", current_time.isoformat())

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

    redis = get_redis_client()
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
    target_key = redis.get(f"user_public_key:{target_user['external_user_id']}")
    target_public_key = target_key if target_key else None

    return ChatRoom(
        id=str(chat_room["_id"]),
        participants=user_list,
        created_at=chat_room["created_at"],
        updated_at=chat_room["updated_at"],
        target_user_id=target_user["external_user_id"],
        user_public_key=target_public_key,
    )


async def get_random_unseen_feed_item(
    feed_id: str, user_id: str, redis: Redis
) -> Optional[FeedPost]:
    viewer_key = f"task_viewers:{feed_id}"
    user_seen_key = f"user_seen:{feed_id}:{user_id}"
    user_seen_posts = redis.smembers(user_seen_key)
    user_seen_post_ids = (
        [ObjectId(post_id) for post_id in user_seen_posts] if user_seen_posts else []
    )

    # Get timestamp from 1 minute ago
    one_minute_ago = datetime.utcnow() - timedelta(minutes=1)

    pipeline = [
        {
            "$match": {
                "feed_id": ObjectId(feed_id),
                "state": {
                    "$in": [
                        VerificationState.READY_FOR_USE,
                        VerificationState.PROCESSING_MEDIA,
                    ]
                },
                "is_public": True,
                "_id": {"$nin": user_seen_post_ids},
                "last_modified_date": {"$gte": one_minute_ago},
            }
        },
        {
            "$lookup": {
                "from": "users",
                "localField": "assignee_user_id",
                "foreignField": "external_user_id",
                "as": "assignee_user",
            }
        },
        {"$unwind": "$assignee_user"},
        {"$sort": {"last_modified_date": -1}},
        {"$limit": 10},  # Get latest 10 posts
        {"$sample": {"size": 1}},  # Randomly select 1
    ]

    results = await mongo.verifications.aggregate(pipeline)
    posts = [FeedPost(**post) for post in results]

    if posts:
        # Add to global seen posts in Redis
        redis.sadd(viewer_key, str(posts[0].id))
        # Add to user specific seen posts
        redis.sadd(user_seen_key, str(posts[0].id))
        return posts[0]
    return None


async def broadcast_feed_items():
    redis = get_redis_client()
    while True:
        await asyncio.sleep(5)  # Wait 5 seconds between broadcasts

        # Get all feed connections from Redis
        all_feeds = redis.keys("feed_connections:*")
        for feed_key in all_feeds:
            feed_id = feed_key.split(":")[1]
            sids = redis.smembers(feed_key)

            for sid in sids:
                # Find the user_id for this sid
                user_id = None
                for uid, user_sids in user_connections.items():
                    if sid in user_sids:
                        user_id = uid
                        break
                if not user_id:
                    continue

                try:
                    feed_item = await get_random_unseen_feed_item(
                        feed_id, user_id, redis
                    )

                    # DO NOT broadcast to the user that created the feed item
                    if feed_item and str(feed_item.assignee_user_id) != user_id:
                        feed_item.last_modified_date = (
                            feed_item.last_modified_date.isoformat()
                        )
                        await sio.emit(
                            "new_feed_item", feed_item.model_dump(), room=sid
                        )
                        print(f"Broadcasted feed item to {sid}")
                except Exception as e:
                    print(f"Error broadcasting feed item: {e}")
