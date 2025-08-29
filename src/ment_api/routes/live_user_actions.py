from fastapi import (
    APIRouter,
    HTTPException,
    Body,
    Path,
    Depends,
    Request,
)
from typing import Annotated
from datetime import datetime, timedelta
from pydantic import BaseModel
from redis import Redis
import logging

from ment_api.common.custom_object_id import CustomObjectId
from ment_api.models.notification import NotificationType
from ment_api.persistence import mongo
from ment_api.services.notification_service import send_notification
from ment_api.services.redis_service import get_redis_dependency
from ment_api.services.external_clients.langfuse_client import langfuse

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/live-actions",
    tags=["live-actions"],
    responses={404: {"description": "Not found"}},
)


@router.post("/poke/{target_user_id}")
async def poke_user(
    request: Request,
    target_user_id: str,
):
    external_user_id = request.state.supabase_user_id
    # Check if user has already poked the target user for this task within 30 minutes
    thirty_minutes_ago = datetime.utcnow() - timedelta(minutes=30)

    recent_poke = await mongo.notifications.find_one(
        {
            "from_user_id": external_user_id,
            "to_user_id": target_user_id,
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
        "from_user_id": external_user_id,
        "to_user_id": target_user_id,
        "type": NotificationType.POKE,
        "created_at": datetime.utcnow(),
        "read": False,
    }

    await mongo.notifications.insert_one(notification)

    # Get the sender's name for the notification
    sender = await mongo.users.find_one({"external_user_id": external_user_id})

    # Send push notification
    await send_notification(
        str(target_user_id),
        f"{sender['username']} გიჯიკათ!",
        "👋",
        {"type": "poke", "userId": str(external_user_id)},
    )

    return {
        "success": True,
    }


class MessageUserResponse(BaseModel):
    success: bool


@router.post("/message/{target_user_id}")
async def message_user(
    request: Request,
    target_user_id: str,
    message: Annotated[str, Body(embed=True)],
):
    external_user_id = request.state.supabase_user_id

    sender = await mongo.users.find_one({"external_user_id": external_user_id})

    await send_notification(
        str(target_user_id),
        f"{sender['username']}",
        message[:50] + "..." if len(message) > 50 else message,
        {"type": "message", "userId": str(external_user_id)},
    )

    return MessageUserResponse(success=True)


class LikeVerificationResponse(BaseModel):
    success: bool


@router.post(
    "/like-verification/{verification_id}",
    operation_id="like_verification",
    response_model=LikeVerificationResponse,
)
async def like_verification(
    request: Request,
    verification_id: CustomObjectId,
    redis: Redis = Depends(get_redis_dependency),
):
    external_user_id = request.state.supabase_user_id
    verification = await mongo.verifications.find_one_by_id(verification_id)
    if not verification:
        raise HTTPException(status_code=404, detail="Verification not found")

    # Create like document
    like = {
        "user_id": external_user_id,
        "verification_id": verification_id,
        "feed_id": verification["feed_id"],
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
    if verification["assignee_user_id"] != external_user_id:
        notification = {
            "from_user_id": external_user_id,
            "to_user_id": verification["assignee_user_id"],
            "type": NotificationType.VERIFICATION_LIKE,
            "created_at": datetime.utcnow(),
            "read": False,
            "verification_id": verification_id,
        }

        await mongo.notifications.insert_one(notification)

        # Check if user has recently liked/unliked this verification
        redis_key = f"like_notification:{external_user_id}:{verification_id}"
        if not redis.exists(redis_key):
            sender = await mongo.users.find_one({"external_user_id": external_user_id})
            await send_notification(
                str(verification["assignee_user_id"]),
                f"{sender['username']} მოიწონა თქვენი ფოსტი",
                "❤️",
                {
                    "type": "verification_like",
                    "userId": str(external_user_id),
                    "verificationId": str(verification_id),
                },
            )
            # Set a cooldown period of 5 minutes
            redis.setex(redis_key, 300, "1")

    return LikeVerificationResponse(success=True)


class UnlikeVerificationResponse(BaseModel):
    success: bool


@router.delete(
    "/unlike-verification/{verification_id}",
    operation_id="unlike_verification",
    response_model=UnlikeVerificationResponse,
)
async def unlike_verification(
    request: Request,
    verification_id: CustomObjectId,
):
    external_user_id = request.state.supabase_user_id
    result = await mongo.likes.find_one_and_delete(
        {"user_id": external_user_id, "verification_id": verification_id}
    )

    if not result:
        raise HTTPException(status_code=404, detail="Like not found")

    return UnlikeVerificationResponse(success=True)


class GetVerificationLikesCountResponse(BaseModel):
    likes_count: int
    has_liked: bool


@router.get(
    "/verification-likes/{verification_id}",
    operation_id="get_verification_likes_count",
    response_model=GetVerificationLikesCountResponse,
)
async def get_verification_likes_count(
    request: Request,
    verification_id: Annotated[CustomObjectId, Path()],
):
    likes_count = await mongo.likes.count_documents(
        {"verification_id": verification_id}
    )
    if request.state.is_guest:
        return GetVerificationLikesCountResponse(
            likes_count=likes_count, has_liked=False
        )
    else:
        external_user_id = request.state.supabase_user_id
        has_liked = (
            await mongo.likes.find_one(
                {"verification_id": verification_id, "user_id": external_user_id}
            )
            is not None
        )

        return GetVerificationLikesCountResponse(
            likes_count=likes_count, has_liked=has_liked
        )


class TrackImpressionsResponse(BaseModel):
    success: bool


@router.post(
    "/track-impressions/{verification_id}",
    operation_id="track_impressions",
    response_model=TrackImpressionsResponse,
)
async def track_impressions(
    request: Request,
    verification_id: CustomObjectId,
    redis: Redis = Depends(get_redis_dependency),
):
    external_user_id = request.state.supabase_user_id
    redis_key = f"impressions:{verification_id}"
    redis.incr(redis_key)

    # Save who viewed it
    viewer_key = f"viewers:{verification_id}"
    redis.sadd(viewer_key, str(external_user_id))

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
                    "from_user_id": external_user_id,
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
                "from_user_id": external_user_id,
                "to_user_id": assignee_user_id,
                "type": NotificationType.IMPRESSION,
                "count": impression_count,
                "created_at": datetime.utcnow(),
                "read": False,
                "verificationId": verification_id,
            }

            await mongo.notifications.insert_one(notification)

            sender = await mongo.users.find_one({"external_user_id": external_user_id})
            await send_notification(
                str(assignee_user_id),
                f"თქვენს ფოსტმა დააგროვა {notify_threshold} ნახვა",
                f"{sender['username']} და სხვებმა ნახეს თქვენი ფოტო",
                {
                    "type": "impression",
                    "verificationId": str(verification_id),
                    "count": notify_threshold,
                },
            )
            # Set a cooldown period of 1 hour
            redis.setex(f"impression_notification:{verification_id}", 3600, "1")

    return TrackImpressionsResponse(success=True)


class GetImpressionsCountResponse(BaseModel):
    impressions_count: int
    unique_viewers: int


@router.get(
    "/get-impressions/{verification_id}",
    operation_id="get_impressions_count",
    response_model=GetImpressionsCountResponse,
)
async def get_impressions_count(
    verification_id: CustomObjectId,
    redis: Redis = Depends(get_redis_dependency),
):
    redis_key = f"impressions:{verification_id}"
    viewer_key = f"viewers:{verification_id}"

    impression_count = int(redis.get(redis_key) or 0)
    viewers = redis.smembers(viewer_key)
    unique_viewers = len(viewers) if viewers else 0

    return GetImpressionsCountResponse(
        impressions_count=impression_count, unique_viewers=unique_viewers
    )


class RateFactCheckResponse(BaseModel):
    success: bool


@router.post(
    "/rate-fact-check/{verification_id}",
    operation_id="rate_fact_check",
    response_model=RateFactCheckResponse,
)
async def rate_fact_check(
    request: Request,
    verification_id: CustomObjectId,
):
    external_user_id = request.state.supabase_user_id

    # Create rating document
    rating = {
        "user_id": external_user_id,
        "verification_id": verification_id,
        "created_at": datetime.utcnow(),
    }

    try:
        await mongo.fact_check_ratings.insert_one(rating)
    except Exception as e:
        if "duplicate key error" in str(e):
            raise HTTPException(
                status_code=400, detail="You have already rated this fact check"
            )
        raise e

    # Create Langfuse score for thumbs up (positive rating)
    try:
        # Get verification to retrieve the langfuse_trace_id
        verification = await mongo.verifications.find_one_by_id(verification_id)
        if verification and verification.get("langfuse_trace_id"):
            # Score the fact-check result with thumbs up
            langfuse.create_score(
                name="user_fact_check_rating",
                value=True,  # True for thumbs up
                data_type="BOOLEAN",
                trace_id=verification["langfuse_trace_id"],
                comment=f"User {external_user_id} gave thumbs up to fact-check result",
                config_id="fact_check_user_feedback",  # Optional: for grouping similar scores
            )

            # Flush to ensure the score is sent to Langfuse
            langfuse.flush()

            logger.info(
                f"Created Langfuse thumbs up score for verification {verification_id} by user {external_user_id}"
            )
        else:
            logger.warning(
                f"No Langfuse trace_id found for verification {verification_id}, skipping score creation"
            )
    except Exception as e:
        # Log error but don't fail the rating - Langfuse scoring is optional
        logger.error(
            f"Failed to create Langfuse score for fact-check thumbs up: {e}",
            exc_info=True,
        )

    return RateFactCheckResponse(success=True)


class UnrateFactCheckResponse(BaseModel):
    success: bool


@router.delete(
    "/unrate-fact-check/{verification_id}",
    operation_id="unrate_fact_check",
    response_model=UnrateFactCheckResponse,
)
async def unrate_fact_check(
    request: Request,
    verification_id: CustomObjectId,
):
    external_user_id = request.state.supabase_user_id
    result = await mongo.fact_check_ratings.find_one_and_delete(
        {"user_id": external_user_id, "verification_id": verification_id}
    )

    if not result:
        raise HTTPException(status_code=404, detail="Rating not found")

    # Create Langfuse score for thumbs down (negative rating)
    try:
        # Get verification to retrieve the langfuse_trace_id
        verification = await mongo.verifications.find_one_by_id(verification_id)
        if verification and verification.get("langfuse_trace_id"):
            # Score the fact-check result with thumbs down
            langfuse.create_score(
                name="user_fact_check_rating",
                value=False,  # False for thumbs down
                data_type="BOOLEAN",
                trace_id=verification["langfuse_trace_id"],
                comment=f"User {external_user_id} gave thumbs down to fact-check result",
                config_id="fact_check_user_feedback",  # Optional: for grouping similar scores
            )

            # Flush to ensure the score is sent to Langfuse
            langfuse.flush()

            logger.info(
                f"Created Langfuse thumbs down score for verification {verification_id} by user {external_user_id}"
            )
        else:
            logger.warning(
                f"No Langfuse trace_id found for verification {verification_id}, skipping score creation"
            )
    except Exception as e:
        # Log error but don't fail the unrating - Langfuse scoring is optional
        logger.error(
            f"Failed to create Langfuse score for fact-check thumbs down: {e}",
            exc_info=True,
        )

    return UnrateFactCheckResponse(success=True)


class GetFactCheckRatingsCountResponse(BaseModel):
    ratings_count: int
    has_rated: bool


@router.get(
    "/fact-check-ratings/{verification_id}",
    operation_id="get_fact_check_ratings_count",
    response_model=GetFactCheckRatingsCountResponse,
)
async def get_fact_check_ratings_count(
    request: Request,
    verification_id: Annotated[CustomObjectId, Path()],
):
    ratings_count = await mongo.fact_check_ratings.count_documents(
        {"verification_id": verification_id}
    )

    if request.state.is_guest:
        return GetFactCheckRatingsCountResponse(
            ratings_count=ratings_count, has_rated=False
        )
    else:
        external_user_id = request.state.supabase_user_id
        has_rated = (
            await mongo.fact_check_ratings.find_one(
                {"verification_id": verification_id, "user_id": external_user_id}
            )
            is not None
        )

        return GetFactCheckRatingsCountResponse(
            ratings_count=ratings_count, has_rated=has_rated
        )


class GetFactCheckResponse(BaseModel):
    has_rated: bool


@router.get(
    "/fact-check/{verification_id}",
    operation_id="get_fact_check",
    response_model=GetFactCheckResponse,
)
async def get_fact_check(
    request: Request,
    verification_id: Annotated[CustomObjectId, Path()],
):
    external_user_id = request.state.supabase_user_id
    fact_check = await mongo.fact_check_ratings.find_one(
        {"verification_id": verification_id, "user_id": external_user_id}
    )
    if not fact_check:
        raise HTTPException(status_code=404, detail="Fact check not found")

    return GetFactCheckResponse(has_rated=True)
