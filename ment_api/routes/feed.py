import asyncio
from datetime import datetime, timezone, timedelta, timezone
import random
from typing import Annotated, List, Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, Body
from redis import Redis

from ment_api.models.task import Task
from ment_api.models.user import User
from ment_api.models.locaiton_feed_post import LocationFeedPost
from ment_api.models.public_verification import PublicVerification
from ment_api.persistence import mongo
from ment_api.common.custom_object_id import CustomObjectId

from ment_api.services.redis_service import get_redis_dependency
from ment_api.models.task_rating import TaskRating, RateRequest


router = APIRouter(
    prefix="/user/feed",
    tags=["user-feed"],
    responses={404: {"description": "Not found"}},
)


@router.get(
    "/task-stories/{task_id}", responses={500: {"description": "Internal server error"}}
)
async def get_task_stories_paginated(
    x_user_id: Annotated[CustomObjectId, Header()],
    task_id: Annotated[CustomObjectId, Path()],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 10,
    redis: Redis = Depends(get_redis_dependency),
):
    try:
        blocked_set = redis.smembers(str(x_user_id))
        blocked_user_ids = [ObjectId(id) for id in blocked_set]
        skip = (page - 1) * page_size
        pipeline = [
            {
                "$match": {
                    "state": "READY_FOR_USE",
                    "is_public": True,
                    "task_id": task_id,
                    "assignee_user_id": {"$nin": blocked_user_ids},
                }
            },
            {"$sort": {"_id": -1}},
            {"$group": {"_id": "$assignee_user_id", "doc": {"$first": "$$ROOT"}}},
            {"$replaceRoot": {"newRoot": "$doc"}},
            {"$skip": skip},
            {"$limit": page_size},
            {
                "$lookup": {
                    "from": "users",
                    "localField": "assignee_user_id",
                    "foreignField": "_id",
                    "as": "assignee_user",
                }
            },
            {"$unwind": "$assignee_user"},
            {
                "$lookup": {
                    "from": "daily_picks",
                    "localField": "task_id",
                    "foreignField": "_id",
                    "as": "task",
                }
            },
            {"$unwind": "$task"},
            {
                "$project": {
                    "_id": 1,
                    "match_id": 1,
                    "assignee_user_id": 1,
                    "task_id": 1,
                    "type": 1,
                    "state": 1,
                    "transcode_job_name": 1,
                    "verified_media_playback": 1,
                    "verified_image": 1,
                    "is_public": 1,
                    "assignee_user": 1,
                    "task": 1,
                }
            },
        ]

        verifications = await mongo.verifications.aggregate(pipeline)

        return [PublicVerification(**verification) for verification in verifications]
    except Exception as e:
        print(e)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get(
    "/location-feed/{task_id}",
    responses={500: {"description": "Internal server error"}},
)
async def get_location_feed_paginated(
    x_user_id: Annotated[CustomObjectId, Header()],
    task_id: Annotated[CustomObjectId, Path()],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 10,
    redis: Redis = Depends(get_redis_dependency),
):
    try:
        blocked_set = redis.smembers(str(x_user_id))
        blocked_user_ids = [ObjectId(id) for id in blocked_set]
        skip = (page - 1) * page_size

        # Get pinned verification if it exists
        pinned_post = None
        document = await mongo.pinned_verifications.find_one({"task_id": task_id})
        if document and page == 1:
            pinned_pipeline = [
                {
                    "$match": {
                        "_id": document["verification_id"],
                        "assignee_user_id": {"$nin": blocked_user_ids},
                        "is_public": True,
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
                {
                    "$project": {
                        "_id": 1,
                        "assignee_user_id": 1,
                        "task_id": 1,
                        "state": 1,
                        "transcode_job_name": 1,
                        "verified_media_playback": 1,
                        "verified_image": 1,
                        "assignee_user": 1,
                        "last_modified_date": 1,
                        "text_content": 1,
                    }
                },
            ]
            pinned_results = await mongo.verifications.aggregate(pinned_pipeline)
            pinned_posts = [LocationFeedPost(**post) for post in pinned_results]
            if pinned_posts:
                pinned_post = pinned_posts[0]

        # Get regular feed items
        pipeline = [
            {
                "$match": {
                    "is_public": True,
                    "task_id": task_id,
                    "assignee_user_id": {"$nin": blocked_user_ids},
                    "_id": {"$ne": document["verification_id"] if document else None},
                }
            },
            {"$sort": {"_id": -1}},
            {"$skip": skip},
            {"$limit": page_size - (1 if pinned_post and page == 1 else 0)},
            {
                "$lookup": {
                    "from": "users",
                    "localField": "assignee_user_id",
                    "foreignField": "_id",
                    "as": "assignee_user",
                }
            },
            {"$unwind": "$assignee_user"},
            {
                "$project": {
                    "_id": 1,
                    "assignee_user_id": 1,
                    "task_id": 1,
                    "state": 1,
                    "transcode_job_name": 1,
                    "verified_media_playback": 1,
                    "verified_image": 1,
                    "assignee_user": 1,
                    "last_modified_date": 1,
                    "text_content": 1,
                }
            },
        ]
        verifications = await mongo.verifications.aggregate(pipeline)
        feed_posts = [
            LocationFeedPost(**verification) for verification in verifications
        ]

        # Insert pinned post at the beginning for page 1
        if pinned_post and page == 1:
            feed_posts.insert(0, pinned_post)

        return feed_posts

    except Exception as e:
        print(e)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post(
    "/rate-task/{task_id}",
    response_model=TaskRating,
    responses={
        400: {"description": "Bad request"},
        500: {"description": "Internal server error"},
    },
)
async def rate_task(
    x_user_id: Annotated[CustomObjectId, Header()],
    task_id: Annotated[CustomObjectId, Path()],
    request: Annotated[RateRequest, Body()],
):
    try:
        # Check if task exists
        task = await mongo.daily_picks.find_one({"_id": task_id})
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        # Create or update rating
        rating_doc = {
            "user_id": x_user_id,
            "task_id": task_id,
            "rate_type": request.rate_type,
            "created_at": datetime.utcnow(),
        }

        result = await mongo.task_ratings.update_one(
            {"user_id": x_user_id, "task_id": task_id},
            {"$set": rating_doc},
            upsert=True,
        )

        if result.upserted_id:
            rating_doc["_id"] = result.upserted_id
        else:
            existing_rating = await mongo.task_ratings.find_one(
                {"user_id": x_user_id, "task_id": task_id}
            )
            rating_doc["_id"] = existing_rating["_id"]

        return TaskRating(**rating_doc)

    except Exception as e:
        print(e)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get(
    "/check-task-rating/{task_id}",
    response_model=Optional[TaskRating],
    responses={
        404: {"description": "Not found"},
        500: {"description": "Internal server error"},
    },
)
async def check_task_rating(
    x_user_id: Annotated[CustomObjectId, Header()],
    task_id: Annotated[CustomObjectId, Path()],
):
    try:
        # Calculate the datetime 24 hours ago
        twenty_four_hours_ago = datetime.utcnow() - timedelta(hours=12)

        # Find the rating
        rating = await mongo.task_ratings.find_one(
            {
                "user_id": x_user_id,
                "task_id": task_id,
                "created_at": {"$gte": twenty_four_hours_ago},
            }
        )

        if not rating:
            return None

        return TaskRating(**rating)

    except Exception as e:
        print(e)
        raise HTTPException(status_code=500, detail="Internal server error")
