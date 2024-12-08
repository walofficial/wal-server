from fastapi import APIRouter, Header, HTTPException, Query, Body, Path, Depends
from typing import Annotated
from datetime import datetime, timedelta
from bson import ObjectId
from redis import Redis

from ment_api.common.custom_object_id import CustomObjectId
from ment_api.models.notification import Notification, NotificationType
from ment_api.models.like import Like
from ment_api.persistence import mongo
from ment_api.services.notification_service import send_notification
from ment_api.services.redis_service import get_redis_dependency

router = APIRouter(
    prefix="/live-actions",
    tags=["live-actions"],
    responses={404: {"description": "Not found"}},
)


@router.post("/poke/{target_user_id}/{task_id}")
async def poke_user(
    target_user_id: CustomObjectId,
    task_id: CustomObjectId,
    x_user_id: Annotated[CustomObjectId, Header(...)],
):
    # Check if user has already poked the target user for this task within 30 minutes
    thirty_minutes_ago = datetime.utcnow() - timedelta(minutes=30)

    recent_poke = await mongo.notifications.find_one(
        {
            "from_user_id": x_user_id,
            "to_user_id": target_user_id,
            "task_id": task_id,
            "type": NotificationType.POKE,
            "created_at": {"$gt": thirty_minutes_ago},
        }
    )

    if recent_poke:
        return {
            "success": False,
            "error_code": "POKE_EVERY_30_MIN",
        }

    # Create notification document
    notification = {
        "from_user_id": x_user_id,
        "to_user_id": target_user_id,
        "task_id": task_id,
        "type": NotificationType.POKE,
        "created_at": datetime.utcnow(),
        "read": False,
    }

    result = await mongo.notifications.insert_one(notification)

    # Get the sender's name for the notification
    sender = await mongo.users.find_one_by_id(x_user_id)

    # Send push notification
    await send_notification(
        str(target_user_id),
        f"{sender['username']} poked you!",
        "Someone is trying to get your attention 👋",
        {"type": "poke", "userId": str(x_user_id)},
    )

    return {
        "success": True,
    }


@router.post("/message/{target_user_id}")
async def message_user(
    target_user_id: CustomObjectId,
    x_user_id: Annotated[CustomObjectId, Header(...)],
    message: Annotated[str, Body(embed=True)],
):
    notification = {
        "from_user_id": x_user_id,
        "to_user_id": target_user_id,
        "type": NotificationType.MESSAGE,
        "created_at": datetime.utcnow(),
        "read": False,
        "message": message,
    }

    result = await mongo.notifications.insert_one(notification)

    sender = await mongo.users.find_one_by_id(x_user_id)

    await send_notification(
        str(target_user_id),
        f"New message from {sender['username']}",
        message[:50] + "..." if len(message) > 50 else message,
        {"type": "message", "userId": str(x_user_id)},
    )

    return {
        "success": True,
    }


@router.post("/like-verification/{verification_id}")
async def like_verification(
    verification_id: CustomObjectId,
    x_user_id: Annotated[CustomObjectId, Header(...)],
    redis: Redis = Depends(get_redis_dependency),
):
    verification = await mongo.verifications.find_one_by_id(verification_id)
    if not verification:
        raise HTTPException(status_code=404, detail="Verification not found")

    # Create like document
    like = {
        "user_id": x_user_id,
        "verification_id": verification_id,
        "task_id": verification["task_id"],
        "created_at": datetime.utcnow(),
    }

    try:
        await mongo.likes.insert_one(like)
    except Exception as e:
        print(e)
        if "duplicate key error" in str(e):
            raise HTTPException(
                status_code=400, detail="You have already liked this verification"
            )
        raise e

    # Only create notification if not liking own verification
    if verification["assignee_user_id"] != x_user_id:
        notification = {
            "from_user_id": x_user_id,
            "to_user_id": verification["assignee_user_id"],
            "type": NotificationType.VERIFICATION_LIKE,
            "created_at": datetime.utcnow(),
            "read": False,
            "verification_id": verification_id,
        }

        await mongo.notifications.insert_one(notification)

        # Check if user has recently liked/unliked this verification
        redis_key = f"like_notification:{x_user_id}:{verification_id}"
        if not redis.exists(redis_key):
            sender = await mongo.users.find_one_by_id(x_user_id)
            await send_notification(
                str(verification["assignee_user_id"]),
                f"{sender['username']} მოიწონა თქვენი ფოსტი",
                "❤️",
                {
                    "type": "verification_like",
                    "userId": str(x_user_id),
                    "verificationId": str(verification_id),
                },
            )
            # Set a cooldown period of 5 minutes
            redis.setex(redis_key, 300, "1")

    return {"success": True}


@router.delete("/unlike-verification/{verification_id}")
async def unlike_verification(
    verification_id: CustomObjectId,
    x_user_id: Annotated[CustomObjectId, Header(...)],
):
    result = await mongo.likes.find_one_and_delete(
        {"user_id": x_user_id, "verification_id": verification_id}
    )

    if not result:
        raise HTTPException(status_code=404, detail="Like not found")

    return {"success": True}


@router.get("/verification-likes/{verification_id}")
async def get_verification_likes_count(
    verification_id: Annotated[CustomObjectId, Path()],
    x_user_id: Annotated[CustomObjectId, Header(...)] = None,
):
    likes_count = await mongo.likes.count_documents(
        {"verification_id": verification_id}
    )

    has_liked = (
        await mongo.likes.find_one(
            {"verification_id": verification_id, "user_id": x_user_id}
        )
        is not None
    )
    print(has_liked)
    print(verification_id)
    print(x_user_id)

    return {"likes_count": likes_count, "has_liked": has_liked}


@router.post("/track-impressions/{verification_id}")
async def track_impressions(
    verification_id: CustomObjectId,
    x_user_id: Annotated[CustomObjectId, Header(...)],
    redis: Redis = Depends(get_redis_dependency),
):
    redis_key = f"impressions:{verification_id}"
    redis.incr(redis_key)

    # Save who viewed it
    viewer_key = f"viewers:{verification_id}"
    redis.sadd(viewer_key, str(x_user_id))

    # Check if the user should be notified
    impression_count = int(redis.get(redis_key) or 0)
    notify_threshold = 100  # Example threshold for notification

    if impression_count == notify_threshold:
        verification = await mongo.verifications.find_one_by_id(verification_id)
        if verification:
            assignee_user_id = verification["assignee_user_id"]

            # Check for recent impression notification
            recent_impression = await mongo.notifications.find_one(
                {
                    "from_user_id": x_user_id,
                    "to_user_id": assignee_user_id,
                    "verification_id": verification_id,
                    "type": NotificationType.IMPRESSION,
                    "created_at": {"$gt": datetime.utcnow() - timedelta(hours=1)},
                }
            )

            if recent_impression:
                return {
                    "success": False,
                    "error_code": "IMPRESSION_NOTIFICATION_COOLDOWN",
                }

            # Create notification document
            notification = {
                "from_user_id": x_user_id,
                "to_user_id": assignee_user_id,
                "type": NotificationType.IMPRESSION,
                "count": impression_count,
                "created_at": datetime.utcnow(),
                "read": False,
                "verification_id": verification_id,
            }

            await mongo.notifications.insert_one(notification)

            sender = await mongo.users.find_one_by_id(x_user_id)
            await send_notification(
                str(assignee_user_id),
                f"თქვენს ფოსტმა დააგროვა {notify_threshold} ნახვა",
                f"{sender['username']} და სხვებმა ნახეს თქვენი ფოტო",
                {"type": "impression", "verificationId": str(verification_id)},
            )
            # Set a cooldown period of 1 hour
            redis.setex(f"impression_notification:{verification_id}", 3600, "1")

    return {"success": True}


@router.get("/get-impressions/{verification_id}")
async def get_impressions_count(
    verification_id: CustomObjectId,
    redis: Redis = Depends(get_redis_dependency),
):
    redis_key = f"impressions:{verification_id}"
    viewer_key = f"viewers:{verification_id}"

    impression_count = int(redis.get(redis_key) or 0)
    viewers = redis.smembers(viewer_key)
    unique_viewers = len(viewers) if viewers else 0

    return {"impressions_count": impression_count, "unique_viewers": unique_viewers}
