from fastapi import APIRouter, Depends, HTTPException, Query, Request
from typing import List, Annotated
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
from pydantic import BaseModel

router = APIRouter(
    prefix="/friends",
    tags=["friends"],
    responses={404: {"description": "Not found"}},
)


class FriendRequestSentResponse(BaseModel):
    error_code: str
    request_id: str


@router.post(
    "/request",
    status_code=201,
    response_model=FriendRequestSentResponse,
    operation_id="send_friend_request",
)
async def send_friend_request(
    request: Request,
    friend_request: FriendRequestSent,
):
    external_user_id = request.state.supabase_user_id
    # Check if request already exists
    existing_request = await mongo.friend_requests.find_one(
        {
            "$or": [
                {
                    "sender_id": external_user_id,
                    "receiver_id": friend_request.target_user_id,
                },
                {
                    "sender_id": friend_request.target_user_id,
                    "receiver_id": external_user_id,
                },
            ]
        }
    )

    if existing_request:
        return FriendRequestSentResponse(
            error_code="ALREADY_SENT",
            request_id=str(existing_request["_id"]),
        )

    # Create new friend request
    new_request = {
        "sender_id": external_user_id,
        "receiver_id": friend_request.target_user_id,
        "status": FriendRequestStatus.PENDING,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }

    result = await mongo.friend_requests.insert_one(new_request)

    # Send notification to the receiver
    receiver = await mongo.users.find_one(
        {"external_user_id": friend_request.target_user_id}
    )
    sender = await mongo.users.find_one({"external_user_id": external_user_id})

    if receiver and sender:
        notification_message = f"{sender['username']} გამოგიგზავნათ მეგობრობა"
        await send_notification(
            receiver["_id"],
            "მეგობრობის მოთხოვნა",
            notification_message,
            {"type": "friend_request_sent", "request_id": str(result.inserted_id)},
        )

    return FriendRequestSentResponse(
        error_code="SUCCESS",
        request_id=str(result.inserted_id),
    )


class FriendRequestResponse(BaseModel):
    user: User
    request: FriendRequest


@router.get(
    "/requests",
    response_model=List[FriendRequestResponse],
    operation_id="get_friend_requests",
)
async def get_friend_requests(request: Request):
    external_user_id = request.state.supabase_user_id
    requests = await mongo.friend_requests.find_all(
        {
            "$or": [{"sender_id": external_user_id}, {"receiver_id": external_user_id}],
            "status": FriendRequestStatus.PENDING,
        }
    )

    # Get unique user IDs from requests
    user_ids = set()
    for request in requests:
        user_ids.add(request["sender_id"])
        user_ids.add(request["receiver_id"])
    user_ids.discard(external_user_id)  # Remove the current user's ID

    # Fetch users in parallel
    users = await mongo.users.find_all({"external_user_id": {"$in": list(user_ids)}})
    requests_future = [FriendRequest(**request) for request in requests]
    # Wait for both operations to complete

    # Create a dictionary of users for easy lookup
    users_dict = {str(user["external_user_id"]): User(**user) for user in users}

    # Combine friend requests with user data
    result = []
    for request in requests_future:
        other_user_id = str(
            request.sender_id
            if request.receiver_id == external_user_id
            else request.receiver_id
        )
        # Check if the user exists in the users_dict because it might not be if current user
        if other_user_id in users_dict:
            result.append({"user": users_dict.get(other_user_id), "request": request})

    return result


@router.put("/request/{request_id}/accept", operation_id="accept_friend_request")
async def accept_friend_request(
    request: Request,
    request_id: CustomObjectId,
):
    external_user_id = request.state.supabase_user_id
    result = await mongo.friend_requests.update_one(
        {
            "_id": request_id,
            "receiver_id": external_user_id,
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
        mongo.users.find_one({"external_user_id": friend_request["sender_id"]}),
        mongo.users.find_one({"external_user_id": friend_request["receiver_id"]}),
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


@router.put("/request/{request_id}/reject", operation_id="reject_friend_request")
async def reject_friend_request(
    request: Request,
    request_id: CustomObjectId,
):
    external_user_id = request.state.supabase_user_id
    await mongo.friend_requests.delete_one(
        {
            "_id": request_id,
            "receiver_id": external_user_id,
            "status": FriendRequestStatus.PENDING,
        },
    )

    return {"message": "Friend request rejected"}


@router.get("/list", response_model=List[User], operation_id="get_friends_list")
async def get_friends_list(
    request: Request,
    page: Annotated[int, Query()] = 1,
    page_size: Annotated[int, Query()] = 10,
):
    external_user_id = request.state.supabase_user_id
    skip = (page - 1) * page_size
    # Get friend IDs from friendships collection
    friendships = await mongo.friendships.find_all(
        {"user_id": external_user_id, "is_blocked": False}
    )
    friend_ids = [friendship["friend_id"] for friendship in friendships]

    pipeline = [
        {"$match": {"external_user_id": {"$in": friend_ids}}},
        {"$sort": {"username": -1}},
        {"$skip": skip},
        {"$limit": page_size},
    ]

    friends = await mongo.users.aggregate(pipeline)

    return [User(**friend) for friend in friends]


@router.delete("/remove/{friend_id}", operation_id="remove_friend")
async def remove_friend(
    request: Request,
    friend_id: str,
):
    external_user_id = request.state.supabase_user_id
    # Remove friendship entries
    result = await mongo.friendships.delete_all(
        {
            "$or": [
                {"user_id": external_user_id, "friend_id": friend_id},
                {"user_id": friend_id, "friend_id": external_user_id},
            ]
        }
    )

    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Friendship not found")

    # Update friend request status to REMOVED if it exists
    await mongo.friend_requests.delete_all(
        {
            "$or": [
                {"sender_id": external_user_id, "receiver_id": friend_id},
                {"sender_id": friend_id, "receiver_id": external_user_id},
            ],
            "status": FriendRequestStatus.ACCEPTED,
        },
    )

    return {"message": "Friend removed successfully"}


@router.get("/blocked", response_model=List[User], operation_id="get_blocked_friends")
async def blocked_friends(
    request: Request,
    redis: Redis = Depends(get_redis_dependency),
):
    external_user_id = request.state.supabase_user_id
    blocked_friends = [id for id in redis.smembers(str(external_user_id))]

    pipeline = [{"$match": {"_id": {"$in": blocked_friends}}}, {"$sort": {"_id": -1}}]

    friends = await mongo.users.aggregate(pipeline)

    return [User(**friend) for friend in friends]
