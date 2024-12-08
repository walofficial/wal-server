from typing import Any, Dict, List, Set, DefaultDict
from fastapi import APIRouter, HTTPException, Header, Body
from fastapi.params import Query
import socketio
from exponent_server_sdk import PushClient, PushMessage
from ment_api.models.chat_message import ChatMessage
from ment_api.models.message_state import MessageState
from ment_api.models.update_message_request import UpdateMessageRequest
from ment_api.persistence import mongo
from ment_api.workers.message_state_worker import message_state_channel
from datetime import datetime, timezone, timedelta, timezone
import requests
import logging
from ment_api.services.notification_service import send_notification
import asyncio
from pydantic import BaseModel
from typing import Annotated, Optional
from ment_api.models.user import User
from ment_api.common.custom_object_id import CustomObjectId
from ment_api.services.external_clients.google_client import task_expire
from collections import defaultdict
import random
from ment_api.models.locaiton_feed_post import LocationFeedPost
from ment_api.services.redis_service import get_redis_dependency, get_redis_client
from redis import Redis
import json
from ment_api.models.verification_state import VerificationState

logger = logging.getLogger(__name__)
from ment_api.persistence.mongo_client import client, db

sio = socketio.AsyncServer(async_mode="asgi", cors_allowed_origins="*")
router = APIRouter(prefix="/chat")

user_connections: Dict[str, Set[str]] = {}
task_connections: DefaultDict[str, Set[str]] = defaultdict(set)
push_client = PushClient()
from bson import ObjectId

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
    async with get_redis_client() as redis:

        if user_public_key:
            user_key = f"user_public_key:{user_id}"
            redis.set(user_key, user_public_key)
            redis.expire(user_key, 86400)  # Expire after 24 hours

            # Find all chat rooms for this user
            chat_rooms = await mongo.chat_rooms.find_all(
                {"participants": ObjectId(user_id)}
            )
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

    print(f"User {user_id} {sid}")


@sio.event
async def disconnect(sid: str) -> None:
    for user, connections in list(user_connections.items()):
        if sid in connections:
            connections.remove(sid)
            if not connections:
                del user_connections[user]

    async with get_redis_client() as redis:
        for task_id, connections in list(task_connections.items()):
            if sid in connections:
                connections.remove(sid)
                if not connections:
                    del task_connections[task_id]
                # Remove from Redis
                redis_key = f"task_connections:{task_id}"
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
        message_title = (await mongo.users.find_one({"_id": ObjectId(sender)}))[
            "username"
        ] or "Ment"
        await send_chat_notification(recipient, "New message", room_id, message_title)

    inserted = await mongo.chat_messages.insert_one(
        {
            "author_id": sender,
            "room_id": room_id,
            "encrypted_content": encrypted_content,
            "nonce": nonce,
            "message_state": MessageState.SENT,
        }
    )
    print(inserted)
    print(room_id)


@sio.event
async def notify_single_message_seen(sid: str, data: Dict[str, str]) -> None:
    recipient = data["recipient"]
    temporary_id = data["temporary_id"]
    sender = None
    print(f"Seen notify from {sid} to {recipient}")
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
            print(f"Seen notify from {sender} to {recipient}")


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


@router.get("/messages", responses={500: {"description": "Generation error"}})
async def get_messages(
    room_id: str = Query(),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1),
):
    skip = (page - 1) * page_size
    pipeline = [
        {"$match": {"room_id": {"$eq": room_id}}},
        {"$sort": {"_id": -1}},
        {"$skip": skip},
        {"$limit": page_size},
    ]

    chat_messages = await mongo.chat_messages.aggregate(pipeline)

    return {
        "messages": [ChatMessage(**message) for message in chat_messages],
        "page": page,
        "page_size": page_size,
    }


class ChatRoom(BaseModel):
    id: str
    participants: List[User]
    created_at: str
    updated_at: str
    target_user_id: Optional[CustomObjectId] = None
    user_public_key: Optional[str] = None


class CreateChatRoomRequest(BaseModel):
    target_user_id: CustomObjectId
    user_public_key: str  # Add this field


@router.get("/chat-rooms")
async def get_user_chat_rooms(x_user_id: Annotated[CustomObjectId, Header(...)]):
    try:
        # Find all chat rooms where the user is a participant
        chat_rooms = await mongo.chat_rooms.find_all({"participants": x_user_id})

        response_rooms = []
        for room in chat_rooms:
            # Get all participants for each room
            participants = await mongo.users.find_all(
                {"_id": {"$in": room["participants"]}}
            )

            response_rooms.append(
                ChatRoom(
                    id=str(room["_id"]),
                    participants=[User(**user) for user in participants],
                    created_at=room["created_at"],
                    updated_at=room["updated_at"],
                )
            )

        return {"chat_rooms": response_rooms}
    except Exception as e:
        logger.error(f"Error getting chat rooms: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to get chat rooms")


@router.post("/create-chat-room")
async def create_chat_room(
    x_user_id: Annotated[CustomObjectId, Header(...)],
    request: CreateChatRoomRequest,
):
    async with await client.start_session() as session, get_redis_client() as redis:
        async with session.start_transaction():
            # Check if room already exists with these participants
            participants = sorted([x_user_id, request.target_user_id])

            existing_room = await mongo.chat_rooms.find_one(
                {"participants": {"$all": [x_user_id, request.target_user_id]}},
                session=session,
            )

            # Store the requesting user's public key in Redis
            redis_key = f"user_public_key:{x_user_id}"
            redis.set(redis_key, request.user_public_key)
            redis.expire(redis_key, 86400)  # Expire after 24 hours

            # Try to get target user's public key
            target_key = redis.get(f"user_public_key:{request.target_user_id}")
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
                "participants": [x_user_id, request.target_user_id],
                "created_at": now,
                "updated_at": now,
                "expiration_task_name": None,
            }

            result = await mongo.chat_rooms.insert_one(chat_room, session=session)
            chat_room_id = str(result.inserted_id)

            # Create expiration task
            task_name = f"expire-chat-room-{chat_room_id}"
            task_expire.create_task(
                in_seconds=timedelta(hours=24).total_seconds(),
                path="/chat/expire-chat-room",
                payload={"chat_room_id": chat_room_id},
                task_name=task_name,
            )

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
    user_id: CustomObjectId
    public_key: str


@router.post("/send-public-key")
async def send_public_key(request: SendPublicKeyRequest):
    async with get_redis_client() as redis:
        redis_key = f"user_public_key:{request.user_id}"
        redis.set(redis_key, request.public_key)
        redis.expire(redis_key, 86400)  # Expire after 24 hours

        return {"success": True}


@router.get("/message-chat-room", response_model=ChatRoom)
async def get_chat_room(
    room_id: str, x_user_id: Annotated[CustomObjectId, Header(...)]
):
    async with get_redis_client() as redis:
        chat_room = await mongo.chat_rooms.find_one({"_id": ObjectId(room_id)})

        if not chat_room:
            raise HTTPException(status_code=404, detail="Chat room not found")

        users = await mongo.users.find_all({"_id": {"$in": chat_room["participants"]}})

        target_user = next(
            (user for user in users if str(user["_id"]) != str(x_user_id)), None
        )

        user_list = [User(**user) for user in users]
        target_key = redis.get(f"user_public_key:{target_user['_id']}")
        target_public_key = target_key if target_key else None

        return ChatRoom(
            id=str(chat_room["_id"]),
            participants=user_list,
            created_at=chat_room["created_at"],
            updated_at=chat_room["updated_at"],
            target_user_id=target_user["_id"],
            user_public_key=target_public_key,
        )


async def get_random_unseen_feed_item(
    task_id: str, user_id: str, redis: Redis
) -> Optional[LocationFeedPost]:
    viewer_key = f"task_viewers:{task_id}"
    user_seen_key = f"user_seen:{task_id}:{user_id}"
    seen_posts = redis.smembers(viewer_key)
    user_seen_posts = redis.smembers(user_seen_key)
    seen_post_ids = [ObjectId(post_id) for post_id in seen_posts] if seen_posts else []
    user_seen_post_ids = (
        [ObjectId(post_id) for post_id in user_seen_posts] if user_seen_posts else []
    )

    # Get timestamp from 1 minute ago
    one_minute_ago = datetime.utcnow() - timedelta(minutes=1)

    pipeline = [
        {
            "$match": {
                "task_id": ObjectId(task_id),
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
                "foreignField": "_id",
                "as": "assignee_user",
            }
        },
        {"$unwind": "$assignee_user"},
        {"$sort": {"last_modified_date": -1}},
        {"$limit": 10},  # Get latest 10 posts
        {"$sample": {"size": 1}},  # Randomly select 1
    ]

    results = await mongo.verifications.aggregate(pipeline)
    posts = [LocationFeedPost(**post) for post in results]

    if posts:
        # Add to global seen posts in Redis
        redis.sadd(viewer_key, str(posts[0].id))
        # Add to user specific seen posts
        redis.sadd(user_seen_key, str(posts[0].id))
        return posts[0]
    return None


async def broadcast_feed_items():
    async with get_redis_client() as redis:
        while True:
            await asyncio.sleep(5)  # Wait 5 seconds between broadcasts

            # Get all task connections from Redis
            all_tasks = redis.keys("task_connections:*")
            for task_key in all_tasks:
                task_id = task_key.split(":")[1]
                sids = redis.smembers(task_key)

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
                            task_id, user_id, redis
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
