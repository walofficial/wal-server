import logging
import os
from enum import Enum
from typing import Annotated, List, Optional

from bson import ObjectId
from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Path,
    Query,
    Request,
)
from redis import Redis

from ment_api.common.custom_object_id import CustomObjectId
from ment_api.models.location_feed_post import FeedPost
from ment_api.persistence import mongo
from ment_api.persistence.mongo import create_translation_projection
from ment_api.configurations.config import settings
from ment_api.services.redis_service import get_redis_dependency
from ment_api.utils.language_utils import normalize_language_code

router = APIRouter(
    prefix="/user/feed",
    tags=["user-feed"],
    responses={404: {"description": "Not found"}},
)


class ContentTypeFilter(str, Enum):
    ALL = "last24h"
    YOUTUBE_ONLY = "youtube_only"
    SOCIAL_MEDIA_ONLY = "social_media_only"


async def get_mixed_feed_pipeline(
    feed_id: CustomObjectId,
    skip: int,
    page_size: int,
    accept_language: str,
    blocked_user_ids: List[ObjectId] = None,
    content_type_filter: ContentTypeFilter = ContentTypeFilter.ALL,
    search_term: Optional[str] = None,
    is_guest: bool = False,
):
    """Pipeline for getting a mixed feed of news, YouTube summaries, and social media posts."""
    match_condition = {
        "is_public": True,
        "feed_id": feed_id,
        # "$or" will be set based on content_type_filter
    }
    if content_type_filter == ContentTypeFilter.YOUTUBE_ONLY:
        match_condition["$or"] = [
            # {"ai_video_summary_status": {"$exists": False}},
            {"ai_video_summary_status": {"$ne": "FAILED"}},
            {"ai_video_summary": {"$exists": True}},
        ]
        match_condition["$and"] = [
            {"external_video.platform": "youtube"},
            {"metadata_status": {"$ne": "FAILED"}},
            {"social_media_scrape_status": {"$ne": "FAILED"}},
            {"fact_check_status": {"$ne": "FAILED"}},
        ]

    elif content_type_filter == ContentTypeFilter.SOCIAL_MEDIA_ONLY:
        # Fix: Properly structure the $or condition for social media posts
        match_condition["$or"] = [
            {"fact_check_status": {"$exists": False}},
            {"fact_check_status": {"$ne": "FAILED"}},
        ]
        match_condition["$and"] = [
            {"metadata_status": {"$ne": "FAILED"}},
            {"external_video.platform": "facebook"},
        ]
    elif content_type_filter == ContentTypeFilter.ALL:
        match_condition["$and"] = [
            {"ai_video_summary_status": {"$ne": "FAILED"}},
            {"social_media_scrape_status": {"$ne": "FAILED"}},
            {"metadata_status": {"$ne": "FAILED"}},
            {"social_media_scrape_status": {"$ne": "FAILED"}},
            {"fact_check_status": {"$ne": "FAILED"}},
        ]

    if blocked_user_ids:
        match_condition["assignee_user_id"] = {"$nin": blocked_user_ids}

    search_pipeline = []
    if search_term:
        # Add condition to exclude generated news when searching
        search_pipeline = [
            {
                "$search": {
                    "index": ("default" if settings.env == "dev" else "default"),
                    "text": {
                        "query": search_term,
                        "path": {
                            "wildcard": "*",
                        },
                    },
                },
            },
        ]

    # Add match stage after search
    search_pipeline.append({"$match": match_condition})

    user_pipeline = []

    if not is_guest:
        user_pipeline = [
            {
                "$lookup": {
                    "from": "users",
                    "localField": "assignee_user_id",
                    "foreignField": "external_user_id",
                    "as": "assignee_user",
                }
            },
            {
                "$unwind": "$assignee_user"
            },  # Consider preserveNullAndEmptyArrays if user can be missing
        ]

    pipeline = (
        search_pipeline
        + [
            {"$sort": {"last_modified_date": -1}},  # Sort by most recent
            {"$skip": skip},
            {"$limit": page_size},
        ]
        + user_pipeline
    )

    # Create translation projections for multilingual fields
    translatable_fields = [
        "text_content",
        "title",
        "text_summary",
        "government_summary",
        "opposition_summary",
        "neutral_summary",
    ]
    fact_check_fields = ["fact_check_data.reason", "fact_check_data.reason_summary"]

    translation_projections = create_translation_projection(
        translatable_fields, accept_language
    )
    fact_check_projections = create_translation_projection(
        fact_check_fields, accept_language
    )

    # Add projection stage
    pipeline.append(
        {
            "$project": {
                "_id": 1,
                "assignee_user_id": 1,
                "feed_id": 1,
                "state": 1,
                "transcode_job_name": 1,
                "verified_media_playback": 1,
                "assignee_user": 1,
                "is_live": 1,
                "live_ended_at": 1,
                "is_space": 1,
                "space_state": 1,
                "image_gallery_with_dims": 1,
                "scheduled_at": 1,
                "has_recording": 1,
                "livekit_room_name": 1,
                "last_modified_date": 1,
                "sources": 1,
                "is_factchecked": 1,
                "is_generated_news": 1,
                "is_public": 1,
                "external_video": 1,
                "ai_video_summary": 1,
                "ai_video_summary_status": 1,
                "metadata_status": 1,
                "fact_check_status": 1,
                "preview_data": 1,
                "fact_check_data": {
                    "factuality": "$fact_check_data.factuality",
                    "reason": fact_check_projections["fact_check_data.reason"],
                    "reason_summary": fact_check_projections[
                        "fact_check_data.reason_summary"
                    ],
                    "references": "$fact_check_data.references",
                },
                "social_media_scrape_details": 1,
                "social_media_scrape_status": 1,
                "news_id": 1,
                # Fields from LocationFeedPost that might be relevant for all types
                "ai_video_summary_error": 1,
                "social_media_scrape_error": 1,
                "visited_urls": 1,  # If relevant for these post types
                "read_urls": 1,  # If relevant for these post types
                # Translated fields with fallback logic
                **translation_projections,
            }
        }
    )

    return pipeline


@router.get(
    "/location-feed/{feed_id}",
    response_model=List[FeedPost],
    responses={500: {"description": "Internal server error"}},
    operation_id="get_location_feed_paginated",
)
async def get_location_feed_paginated(
    request: Request,
    feed_id: Annotated[CustomObjectId, Path()],
    accept_language: Annotated[str, Header()] = "ka",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 10,
    content_type_filter: Annotated[ContentTypeFilter, Query()] = ContentTypeFilter.ALL,
    search_term: Optional[str] = None,
    redis: Redis = Depends(get_redis_dependency),
):
    # Normalize language code
    accept_language = normalize_language_code(accept_language)

    is_guest = request.state.is_guest
    try:
        external_user_id = request.state.supabase_user_id
        blocked_set = redis.smembers(str(external_user_id))
        blocked_user_ids = [ObjectId(id) for id in blocked_set]
        skip = (page - 1) * page_size

        pipeline = await get_mixed_feed_pipeline(
            feed_id,
            skip,
            page_size,
            accept_language,
            blocked_user_ids,
            content_type_filter,
            search_term,
            is_guest,
        )
        verifications = await mongo.verifications.aggregate(pipeline)
        feed_posts = [FeedPost(**verification) for verification in verifications]
        return feed_posts

    except Exception as e:
        logging.error(e)
        raise HTTPException(status_code=500, detail="Internal server error")
