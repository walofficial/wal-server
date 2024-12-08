from fastapi import APIRouter, Depends, HTTPException, Header, Query
from typing import List, Optional, Annotated
from bson import ObjectId
from datetime import datetime, timezone

from redis import Redis
from ment_api.persistence import mongo
from ment_api.common.custom_object_id import CustomObjectId
from ment_api.models.friend_request import FriendRequest, FriendRequestStatus
from ment_api.models.user import User
from ment_api.models.friend_request import FriendRequestSent
from ment_api.services.notification_service import (
    send_notification,
)  # Import the notification service

import asyncio

from ment_api.services.redis_service import get_redis_dependency

router = APIRouter(
    prefix="/friends",
    tags=["friends"],
    responses={404: {"description": "Not found"}},
)


@router.post("/request", status_code=201)
async def send_friend_request(
    request: FriendRequestSent,
    x_user_id: Annotated[CustomObjectId, Header()],
):
    # Check if request already exists
    existing_request = await mongo.friend_requests.find_one(
        {
            "$or": [
                {"sender_id": x_user_id, "receiver_id": request.target_user_id},
                {"sender_id": request.target_user_id, "receiver_id": x_user_id},
            ]
        }
    )

    if existing_request:
        return {
            "error_code": "ALREADY_SENT",
            "request_id": str(existing_request["_id"]),
        }

    # Create new friend request
    new_request = {
        "sender_id": x_user_id,
        "receiver_id": request.target_user_id,
        "status": FriendRequestStatus.PENDING,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }

    result = await mongo.friend_requests.insert_one(new_request)

    # Send notification to the receiver
    receiver = await mongo.users.find_one({"_id": request.target_user_id})
    sender = await mongo.users.find_one({"_id": x_user_id})

    if receiver and sender:
        notification_message = f"{sender['username']} გამოგიგზავნათ მეგობრობა"
        await send_notification(
            receiver["_id"],
            "მეგობრობის მოთხოვნა",
            notification_message,
            {"type": "friend_request_sent", "request_id": str(result.inserted_id)},
        )

    return {
        "message": "Friend request sent successfully",
        "request_id": str(result.inserted_id),
    }


@router.get("/requests")
async def get_friend_requests(x_user_id: Annotated[CustomObjectId, Header()]):
    requests = await mongo.friend_requests.find_all(
        {
            "$or": [{"sender_id": x_user_id}, {"receiver_id": x_user_id}],
            "status": FriendRequestStatus.PENDING,
        }
    )

    # Get unique user IDs from requests
    user_ids = set()
    for request in requests:
        user_ids.add(request["sender_id"])
        user_ids.add(request["receiver_id"])
    user_ids.discard(x_user_id)  # Remove the current user's ID

    # Fetch users in parallel
    users = await mongo.users.find_all({"_id": {"$in": list(user_ids)}})
    requests_future = [FriendRequest(**request) for request in requests]

    # Wait for both operations to complete

    # Create a dictionary of users for easy lookup
    users_dict = {str(user["_id"]): User(**user) for user in users}

    # Combine friend requests with user data
    result = []
    for request in requests_future:
        other_user_id = str(
            request.sender_id
            if request.receiver_id == x_user_id
            else request.receiver_id
        )
        result.append({"user": users_dict.get(other_user_id), "request": request})

    return result


@router.put("/request/{request_id}/accept")
async def accept_friend_request(
    request_id: CustomObjectId,
    x_user_id: Annotated[CustomObjectId, Header()],
):
    result = await mongo.friend_requests.update_one(
        {
            "_id": request_id,
            "receiver_id": x_user_id,
            "status": FriendRequestStatus.PENDING,
        },
        {
            "$set": {
                "status": FriendRequestStatus.ACCEPTED,
                "updated_at": datetime.now(timezone.utc),
            }
        },
    )

    if result.modified_count == 0:
        raise HTTPException(
            status_code=404, detail="Friend request not found or already processed"
        )

    # Get the friend request details
    friend_request = await mongo.friend_requests.find_one({"_id": request_id})

    # Add entries to the friendships collection
    await mongo.friendships.insert_many(
        [
            {
                "user_id": friend_request["sender_id"],
                "friend_id": friend_request["receiver_id"],
                "is_blocked": False,
            },
            {
                "user_id": friend_request["receiver_id"],
                "friend_id": friend_request["sender_id"],
                "is_blocked": False,
            },
        ]
    )
    # Send notification to the sender
    sender, receiver = await asyncio.gather(
        mongo.users.find_one({"_id": friend_request["sender_id"]}),
        mongo.users.find_one({"_id": friend_request["receiver_id"]}),
    )

    if sender and receiver:
        notification_message = f"{receiver['username']} დაგიდასტურათ მეგობრობა"
        await send_notification(
            sender["_id"],
            "მეგობრები ხართ",
            notification_message,
            {"type": "friend_request_accepted", "friend_id": str(receiver["_id"])},
        )

    return {"message": "Friend request accepted"}


@router.put("/request/{request_id}/reject")
async def reject_friend_request(
    request_id: CustomObjectId,
    x_user_id: Annotated[CustomObjectId, Header()],
):
    result = await mongo.friend_requests.delete_one(
        {
            "_id": request_id,
            "receiver_id": x_user_id,
            "status": FriendRequestStatus.PENDING,
        },
    )

    return {"message": "Friend request rejected"}


@router.get("/list", response_model=List[User])
async def get_friends_list(
    x_user_id: Annotated[CustomObjectId, Header()],
    page: Annotated[int, Query()] = 1,
    page_size: Annotated[int, Query()] = 10,
):
    skip = (page - 1) * page_size

    # Get friend IDs from friendships collection
    friendships = await mongo.friendships.find_all(
        {"user_id": x_user_id, "is_blocked": False}
    )
    friend_ids = [friendship["friend_id"] for friendship in friendships]

    pipeline = [
        {"$match": {"_id": {"$in": friend_ids}}},
        {"$sort": {"_id": -1}},
        {"$skip": skip},
        {"$limit": page_size},
    ]

    friends = await mongo.users.aggregate(pipeline)

    return [User(**friend) for friend in friends]


@router.delete("/remove/{friend_id}")
async def remove_friend(
    friend_id: CustomObjectId,
    x_user_id: Annotated[CustomObjectId, Header()],
):
    # Remove friendship entries
    result = await mongo.friendships.delete_all(
        {
            "$or": [
                {"user_id": x_user_id, "friend_id": friend_id},
                {"user_id": friend_id, "friend_id": x_user_id},
            ]
        }
    )

    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Friendship not found")

    # Update friend request status to REMOVED if it exists
    await mongo.friend_requests.delete_all(
        {
            "$or": [
                {"sender_id": x_user_id, "receiver_id": friend_id},
                {"sender_id": friend_id, "receiver_id": x_user_id},
            ],
            "status": FriendRequestStatus.ACCEPTED,
        },
    )

    return {"message": "Friend removed successfully"}


@router.get("/blocked")
async def blocked_friends(
    x_user_id: Annotated[CustomObjectId, Header()],
    redis: Redis = Depends(get_redis_dependency),
):
    blocked_friends = [ObjectId(id) for id in redis.smembers(str(x_user_id))]

    pipeline = [{"$match": {"_id": {"$in": blocked_friends}}}, {"$sort": {"_id": -1}}]

    friends = await mongo.users.aggregate(pipeline)

    return [User(**friend) for friend in friends]
