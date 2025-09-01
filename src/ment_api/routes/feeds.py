import asyncio
import logging
import random
import re
import urllib.parse
import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated, Dict, List, Optional, Tuple

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Body,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Path,
    Query,
    Request,
    Response,
    UploadFile,
)
from pydantic import BaseModel

from ment_api.common.custom_object_id import CustomObjectId
from ment_api.models.feed import Feed
from ment_api.models.feed_location_mapping import (
    Location,
)
from ment_api.models.feed_response import FeedsResponse, FeedWithLocation

# Add this import at the top of the file
from ment_api.models.live_user_response import LiveUser
from ment_api.models.location_feed_post import (
    FeedPost,
    LocationFeedPost,
    MetadataStatus,
)
from ment_api.persistence import mongo
from ment_api.services.external_clients.cloud_flare_client import upload_image
from ment_api.services.external_clients.gemini_client import GeminiClient
from ment_api.services.external_clients.langfuse_client import (
    langfuse,
)
from ment_api.services.external_clients.models.gemini_models import (
    NewsItemWithId,
    NotificationGenerationRequest,
)
from ment_api.services.external_clients.scrape_do_client import (
    ScrapeDoClient,
    get_scrape_do_dependency,
)
from ment_api.services.location_service import is_on_feed_location
from ment_api.services.news_service import publish_check_fact
from ment_api.services.notification_service import (
    send_global_notifications,
    send_notification,
)
from ment_api.services.og_service import get_og_preview
from ment_api.services.video_processor_service import publish_video_processor_request
from ment_api.utils.bot_ids import bot_name_to_id
from ment_api.configurations.config import settings

# Initialize loggers
logger = logging.getLogger(__name__)
# Use global client for v3 patterns
# langfuse is imported directly from langfuse_client

# --- GCS Configuration (Should ideally come from settings) ---
GCS_BUCKET_NAME = "ment-verification"
# -----------------------------------------------------------


# Helper functions for background tasks
async def _fetch_and_update_og_preview(
    verification_id: CustomObjectId, media_url: str, media_platform: str
):
    try:
        # Set status to pending
        await mongo.verifications.update_one(
            {"_id": verification_id},
            {"$set": {"metadata_status": MetadataStatus.PENDING}},
        )

        # Fetch preview data
        preview_data_result = await get_og_preview(media_url, media_platform)

        # Update verification with results
        update = {}
        if preview_data_result:
            preview_data = preview_data_result.model_dump()
            # Facebook has bad titles when instant scraping, title is during the final stages anyway
            update["preview_data"] = preview_data

        await mongo.verifications.update_one({"_id": verification_id}, {"$set": update})

    except Exception as e:
        # Set failed status on error
        await mongo.verifications.update_one(
            {"_id": verification_id},
            {"$set": {"metadata_status": MetadataStatus.FAILED}},
        )
        logging.error(f"Error fetching OG preview: {e}", exc_info=True)


async def _fetch_youtube_metadata_and_process_in_background(
    verification_id: CustomObjectId,
    youtube_url: str,
    external_user_id: str,
):
    try:
        # Fetch preview data
        preview_data = await get_og_preview(youtube_url, "youtube")

        # Update preview data if successful
        if preview_data:
            await mongo.verifications.update_one(
                {"_id": verification_id},
                {"$set": {"preview_data": preview_data.model_dump()}},
            )

            # Check user eligibility and process video
            user = await mongo.users.find_one({"external_user_id": external_user_id})
            if user:
                await publish_video_processor_request(
                    verification_id,
                    youtube_url,
                    external_user_id,
                    preview_data.title or "TITLE NOT FOUND",
                )

            else:
                status = "USER_NOT_FOUND" if not user else "NOT_ELIGIBLE"
                await mongo.verifications.update_one(
                    {"_id": verification_id},
                    {"$set": {"ai_video_summary_status": status}},
                )

    except Exception as e:
        logging.error(f"YouTube processing error: {e}", exc_info=True)
        await mongo.verifications.update_one(
            {"_id": verification_id},
            {"$set": {"ai_video_summary_status": "PROCESSING_ERROR"}},
        )


# Add this helper function near the top of the file
async def get_location_feeds_pipeline(category_id: CustomObjectId):
    """Common pipeline for getting tasks with live user and verification counts"""
    return [
        {"$match": {"feed_category_id": category_id, "hidden": settings.env == "dev"}},
        {
            "$project": {
                "_id": 1,
                "feed_title": 1,
                "feed_category_id": 1,
                "display_name": 1,
                "feed_location": 1,
                "feed_locations": 1,
                "feed_description": 1,
                "hidden": 1,
            }
        },
    ]


router = APIRouter(
    prefix="/feeds",
    tags=["feeds"],
    responses={404: {"description": "Not found"}},
)


@router.get(
    "/locations",
    operation_id="get_location_feeds",
    response_model=FeedsResponse,
    responses={500: {"description": "Generation error"}},
)
async def get_location_feeds(
    category_id: Annotated[CustomObjectId, Query()],
    # antartica coordinates as default
    x_user_location_latitude: Annotated[float, Header(...)] = -77.85,
    x_user_location_longitude: Annotated[float, Header(...)] = 166.67,
    ignore_location_check: Annotated[bool, Query()] = False,
):
    user_location = (x_user_location_latitude, x_user_location_longitude)
    pipeline = await get_location_feeds_pipeline(category_id)

    feeds = await mongo.feeds.aggregate(pipeline)
    feeds = list(feeds)

    feeds_at_location = []
    nearest_feeds = []

    for feed in feeds:
        feed_obj = Feed(**feed)
        is_at_location, nearest_location = await is_on_feed_location(
            feed_obj.id, user_location
        )
        if settings.env == "dev":
            is_at_location = True
        if is_at_location:
            feeds_at_location.append(feed_obj)
        else:
            nearest_feeds.append(
                FeedWithLocation(feed=feed_obj, nearest_location=nearest_location)
            )

    return FeedsResponse(
        feeds_at_location=feeds_at_location, nearest_feeds=nearest_feeds
    )


@router.post("/notifications/push")
async def send_push_notification(notification_data: Annotated[Dict, Body(...)]):
    title = notification_data["title"]
    description = notification_data["description"]
    await send_global_notifications(title, description)


# Add this new endpoint
@router.get("/live-users", response_model=List[LiveUser], operation_id="get_live_users")
async def live_users(
    request: Request,
    feed_id: Annotated[CustomObjectId, Query(...)],
):
    external_user_id = request.state.supabase_user_id
    pipeline = [
        {
            "$match": {
                "feed_id": feed_id,
                "expiration_date": {"$gt": datetime.utcnow()},
                "author_id": {"$ne": external_user_id},
            }
        },
        {
            "$lookup": {
                "from": "users",
                "localField": "author_id",
                "foreignField": "external_user_id",
                "as": "author",
            }
        },
        {"$unwind": "$author"},
        {
            "$lookup": {
                "from": "friendships",
                "let": {"author_id": "$author_id"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$user_id", external_user_id]},
                                    {"$eq": ["$friend_id", "$$author_id"]},
                                ]
                            }
                        }
                    }
                ],
                "as": "friendship",
            }
        },
        {"$addFields": {"is_friend": {"$gt": [{"$size": "$friendship"}, 0]}}},
        {
            "$project": {
                "user": "$author",
                "is_friend": 1,
                "_id": 0,
            }
        },
        {"$sort": {"is_friend": -1}},  # Sort friends first
    ]
    results = await mongo.live_users.aggregate(pipeline)

    return [LiveUser(**result) for result in results]


class LiveUsersCount(BaseModel):
    count: int


@router.get(
    "/live-users/count", response_model=LiveUsersCount, operation_id="count_live_users"
)
async def count_live_users(
    feed_id: Annotated[CustomObjectId, Query(...)],
):
    # Updated to use async Redis - import the async client
    from ment_api.services.redis_service import get_async_redis_client

    redis = get_async_redis_client()
    # Try to get cached count from Redis
    cache_key = f"live_users_count:{feed_id}"
    cached_count = await redis.get(cache_key)

    if cached_count is not None:
        return LiveUsersCount(count=int(cached_count))

    # If not in cache, query MongoDB
    count = await mongo.live_users.count_documents(
        {
            "feed_id": feed_id,
            "expiration_date": {"$gt": datetime.utcnow()},
        }
    )

    # Cache the result in Redis with 10 second expiration
    await redis.set(cache_key, str(count), ex=10)

    return LiveUsersCount(count=count)


@router.get("/single/{feed_id}", response_model=Feed, operation_id="get_single_feed")
async def get_single_feed(
    feed_id: Annotated[CustomObjectId, Path(...)],
):
    feed = await mongo.feeds.find_one_by_id(feed_id)
    if not feed:
        raise HTTPException(status_code=404, detail="Feed not found")

    return Feed(**feed)


@router.get(
    "/check-location",
    response_model=Tuple[bool, Optional[Location]],
    operation_id="check_location",
)
async def check_location(
    feed_id: Annotated[CustomObjectId, Query()],
    latitude: Annotated[float, Query()],
    longitude: Annotated[float, Query()],
) -> Tuple[bool, Optional[Location]]:
    return await is_on_feed_location(feed_id, (latitude, longitude))


class GoLiveRequest(BaseModel):
    feed_id: CustomObjectId


@router.post("/go-live", response_model=dict, operation_id="go_live")
async def go_live(
    request: Request,
    go_live_request: GoLiveRequest,
):
    external_user_id = request.state.supabase_user_id
    feed_id = go_live_request.feed_id

    expiration_date = datetime.now(timezone.utc) + timedelta(hours=12)

    await mongo.live_users.update_one(
        {"author_id": external_user_id, "feed_id": feed_id},
        {
            "$set": {"expiration_date": expiration_date},
            "$setOnInsert": {"created_at": datetime.now(timezone.utc)},
        },
        upsert=True,
    )

    return {"success": True}


@router.post(
    "/publish-post", response_model=LocationFeedPost, operation_id="publish_post"
)
async def publish_post(
    request: Request,
    background_tasks: BackgroundTasks,
    feed_id: Annotated[CustomObjectId, Form(...)],
    content: Annotated[str, Form()] = "",
    files: Annotated[
        List[UploadFile],
        File(media_type="image/jpeg", description="verification images"),
    ] = [],
):
    with langfuse.start_as_current_span(name="publish_post_endpoint") as endpoint_span:
        external_user_id = request.state.supabase_user_id

        endpoint_span.update(
            input={
                "feed_id": str(feed_id),
                "user_id": external_user_id,
                "content_length": len(content),
                "file_count": len(files),
                "content_preview": content[:100],
            },
            metadata={
                "endpoint": "/publish-post",
                "method": "POST",
            },
            user_id=external_user_id,
        )

    try:
        # 1. Prepare image upload coroutines
        upload_coroutines = []
        for file in files:
            file_content = await file.read()
            upload_coroutines.append(
                upload_image(file_content, f"{file.filename}", file.content_type)
            )

        if upload_coroutines:
            images_with_dims = await asyncio.gather(*upload_coroutines)

        # 2. Process content and extract initial media info
        cleaned_content = content.strip()

        extracted_media = extract_social_media_url(cleaned_content)

        logging.info(f"Extracted media extraction result: {extracted_media}")

        youtube_url = None
        initial_ai_video_summary_status = None

        if extracted_media:
            media_url = extracted_media["url"]
            media_platform = extracted_media["platform"]

            if media_platform == "youtube":
                youtube_url = media_url
                logging.info(
                    f"Found YouTube video: {youtube_url}. Will process metadata in background."
                )
            else:
                logging.info(
                    f"Found social media ({media_platform}): {media_url}. Will fetch OG preview in background."
                )
                # For non-YouTube, social_media_info can be set if it's the primary identified link.
                # However, the original logic differentiated social_media_info for other scraping purposes.
                # Let's ensure clarity: extracted_media is the primary content link.
                # social_media_info is for additional scraping mentioned later.
                # So, if extracted_media is not YouTube, we schedule its OG fetch.

        # This part handles other specific social media info, potentially different from extracted_media
        # The original code's `social_media_info` variable was set if platform was not youtube.
        # Let's ensure this doesn't conflict.
        # The original 'social_media_info' seemed to be tied to 'extracted_media' if not YouTube.
        # Let's refine: if extracted_media is non-youtube, it might be what social_media_info was for.
        active_social_media_info_for_scrape = None
        if extracted_media and extracted_media["platform"] != "youtube":
            active_social_media_info_for_scrape = extracted_media
        # The original logic had `social_media_info = extracted_media` if not youtube.
        # Then it checked `if social_media_info:`
        # This seems to be the correct interpretation for the scraper part.

        # 5. Prepare verification document
        verification_doc = {
            "feed_id": feed_id,
            "assignee_user_id": external_user_id,
            "text_content": content,
            "state": "READY_FOR_USE",
            "last_modified_date": datetime.now(timezone.utc),
            "is_public": True,
            "image_gallery_with_dims": (
                [img.model_dump() for img in images_with_dims]
                if upload_coroutines
                else []
            ),
            "title": "",
            "external_video": extracted_media,  # Store original extracted info (URL, platform)
            "ai_video_summary_status": initial_ai_video_summary_status,
            # UI can immediately show pending status if there's media to process without refetching
            "metadata_status": MetadataStatus.PENDING,  # Set pending status if there's media to process
        }

        if (
            active_social_media_info_for_scrape
        ):  # This is for the social media scraper service
            from ment_api.models.location_feed_post import (
                SocialMediaScrapeDetails,
                SocialMediaScrapeStatus,
            )

            verification_doc["social_media_scrape_details"] = SocialMediaScrapeDetails(
                platform=active_social_media_info_for_scrape["platform"],
                url=active_social_media_info_for_scrape["url"],
                image_urls=[],
            ).model_dump()
            verification_doc["social_media_scrape_status"] = (
                SocialMediaScrapeStatus.PENDING
            )

        result = await mongo.verifications.insert_one(verification_doc)
        verification_id = result.inserted_id
        verification_doc["_id"] = verification_id

        # 7. Schedule background tasks that need verification_id
        if youtube_url:  # This means extracted_media was YouTube
            background_tasks.add_task(
                _fetch_youtube_metadata_and_process_in_background,
                verification_id,
                youtube_url,
                external_user_id,
            )
        elif extracted_media and extracted_media["platform"] != "youtube":
            # The OG preview task was added earlier, but it needs verification_id.
            # This is a problem. The task must be added *after* verification_id is known.
            # Let's re-add it here with verification_id.
            # To do this, we need to remove the previous one if it was added.
            # Simpler: only add tasks here.

            # Remove previous attempt to add _fetch_and_update_og_preview if any
            # This is not directly possible with BackgroundTasks API.
            # Instead, only add tasks here.

            # Reset logic for adding OG preview task:
            # The 'if' condition here is slightly redundant due to the elif but harmless.
            # This is the correct place to add the task.
            background_tasks.add_task(
                _fetch_and_update_og_preview,
                verification_id,  # Now we have it
                extracted_media["url"],
                extracted_media["platform"],
            )

        if active_social_media_info_for_scrape:  # If social media needs scraping
            from ment_api.services.social_media_scraper_service import (
                publish_social_media_scrape_request,
            )

            logging.info(
                f"Scheduling background task for social media scrape: {verification_id}"
            )
            # Assuming publish_social_media_scrape_request is non-blocking or handled by background_tasks correctly
            background_tasks.add_task(
                publish_social_media_scrape_request, verification_id
            )

        needs_general_fact_check = True
        if (
            verification_doc.get("social_media_scrape_status") == "PENDING"
        ):  # Handled by social scraper
            needs_general_fact_check = False

        if (
            needs_general_fact_check
            and verification_doc["state"] == "PENDING_FACTCHECK"
        ):
            # If it's marked for fact check and no specific media processor will handle it
            logging.info(f"Scheduling general fact check for {verification_id}")

            background_tasks.add_task(publish_check_fact, [verification_id])
        elif not extracted_media and not active_social_media_info_for_scrape:
            # Fallback for simple posts that only have `should_factcheck`
            logging.info(
                f"Scheduling general fact check (fallback) for {verification_id}"
            )

            background_tasks.add_task(publish_check_fact, [verification_id])

        endpoint_span.update(
            output={
                "verification_id": str(verification_id),
                "has_background_tasks": True,
                "processing_type": (
                    "youtube"
                    if youtube_url
                    else (
                        "social_media"
                        if active_social_media_info_for_scrape
                        else "fact_check"
                    )
                ),
                "success": True,
            },
            metadata={
                "youtube_url": youtube_url,
                "has_social_media_scrape": bool(active_social_media_info_for_scrape),
            },
        )

        return FeedPost(**verification_doc)

    except Exception:
        logging.exception("Error during publish_post:")  # Log the full traceback
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/get-country-feed", response_model=Feed, operation_id="get_country_feed")
async def get_country_feed():
    country_feed = await mongo.feeds.find_one({"feed_title": "რახდება"})
    if not country_feed:
        raise HTTPException(status_code=404, detail="Country feed task not found")
    return Feed(**country_feed)


@router.get("/screenshot", operation_id="get_screenshot")
async def get_screenshot(
    verification_id: Annotated[
        CustomObjectId, Query(description="Verification ID for screenshot")
    ],
    tab: Annotated[str, Query(description="Content perspective")] = "neutral",
    scrape_client: ScrapeDoClient = Depends(get_scrape_do_dependency),
):
    """
    Generate and cache a screenshot for a verification ID.
    Returns cached version if available, otherwise generates new screenshot.

    Args:
        verification_id: The verification ID to generate screenshot for
        tab: Content perspective (neutral, government, opposition)

    Returns:
        Raw bytes of the screenshot image as JPEG format
    """
    try:
        # Updated to use async Redis
        from ment_api.services.redis_service import get_async_redis_client

        redis = get_async_redis_client()
        cache_key = f"screenshot:{verification_id}:{tab}"

        # Check if screenshot is already cached
        cached_data = await redis.get(cache_key)
        if cached_data:
            logger.info(
                f"Returning cached screenshot for verification {verification_id}"
            )
            return Response(
                content=cached_data,
                media_type="image/jpeg",
                headers={
                    "Content-Disposition": f"inline; filename=screenshot_{verification_id}.jpg"
                },
            )

        # Get verification data for additional context
        verification = await mongo.verifications.find_one_by_id(verification_id)
        if not verification:
            raise HTTPException(status_code=404, detail="Verification not found")

        # Build screenshot URL
        screenshot_url = (
            f"https://wal.ge/status/{verification_id}?static=true&tab={tab}"
        )

        # Add title parameter if available
        social_media_card_title = verification.get("title", "")
        if social_media_card_title:
            encoded_title = urllib.parse.quote(social_media_card_title)
            screenshot_url += f"&title={encoded_title}"

        # Add fact check summary if available
        fact_check_summary = verification.get("fact_check_data", {}).get(
            "reason_summary", ""
        )
        if fact_check_summary:
            encoded_fact_check_summary = urllib.parse.quote(fact_check_summary)
            screenshot_url += f"&fact_check_summary={encoded_fact_check_summary}"

        # Capture screenshot using ScrapeDoClient
        result = await scrape_client.scrape_with_screenshot(
            scrape_url=screenshot_url,
            full_page=False,
            wait_until="load",
            width=1920,
            height=1080,
            particularScreenShot="#static-view",
        )

        screenshot_data = result.get("screenshot_data")
        if not screenshot_data:
            raise Exception("No screenshot data received")

        # Cache the screenshot bytes in Redis with 24 hour expiration
        await redis.set(
            cache_key, screenshot_data, ex=86400
        )  # 24 hours = 86400 seconds

        logger.info(
            f"Screenshot generated successfully for verification {verification_id}"
        )

        # Return the screenshot bytes as a Response with proper headers
        return Response(
            content=screenshot_data,
            media_type="image/jpeg",
            headers={
                "Content-Disposition": f"inline; filename=screenshot_{verification_id}.jpg"
            },
        )

    except HTTPException:
        # Re-raise HTTPExceptions as-is
        raise
    except Exception as e:
        logger.error(
            f"Error generating screenshot for verification {verification_id}: {str(e)}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail=f"Failed to generate screenshot: {str(e)}"
        )


@router.get("/generate-social-media-content")
async def generate_social_media_content(
    tab: Annotated[
        str, Query(description="Content perspective")
    ] = "neutral",  # neutral|government|opposition
    verification_id: Annotated[
        Optional[CustomObjectId], Query(description="Verification ID")
    ] = None,
    scrape_client: ScrapeDoClient = Depends(get_scrape_do_dependency),
):
    """
    Generate social media content by selecting an appropriate news article or fact check,
    generating title/description, and creating a screenshot.

    Args:
        tab: Content perspective (neutral, government, opposition)
        include_fact_checks: Whether to include fact-checked content (is_generated_news=False)

    Returns:
        Dictionary containing image_url, title, description, and verification_id
    """

    try:
        # Get content from the last 24 hours
        # twenty_four_hours_ago = datetime.now(timezone.utc) - timedelta(hours=200)

        # Build match conditions based on parameters
        match_conditions = {
            "text_content": {"$exists": True, "$ne": ""},
            "fact_check_data.factuality": {"$lt": 0.51, "$exists": True},
            "assignee_user_id": {"$in": list(bot_name_to_id().values())},
            "$or": [
                {"used_by_zapier": {"$exists": False}},
                {"used_by_zapier": False},
            ],
        }

        # Add content type filter
        # if include_fact_checks:

        # else:
        #     # Only generated news
        #     match_conditions["is_generated_news"] = True

        # Pipeline to get appropriate content
        content_pipeline = [
            {
                "$project": {
                    "_id": 1,
                    "text_content": "$text_content.ka",
                    "last_modified_date": 1,
                    "is_generated_news": 1,
                    "fact_check_data": {
                        "factuality": "$fact_check_data.factuality",
                        "reason": "$fact_check_data.reason.ka",
                        "reason_summary": "$fact_check_data.reason_summary.ka",
                        "references": "$fact_check_data.references",
                    },
                }
            },
        ]

        if verification_id:
            content_pipeline.insert(0, {"$match": {"_id": verification_id}})
        else:
            content_pipeline.insert(0, {"$match": match_conditions})
            content_pipeline.append({"$sort": {"last_modified_date": -1}})
            content_pipeline.append(
                {"$limit": 20}
            )  # Get more items to allow for random selection

        content_items = await mongo.verifications.aggregate(content_pipeline)
        content_items = list(content_items)

        if not content_items:
            logger.info("No content items found for social media generation")
            raise HTTPException(
                status_code=404,
                detail="No appropriate content found for social media generation",
            )

        # Select a random item from the results
        selected_item = random.choice(content_items)
        verification_id = selected_item["_id"]

        logger.info(
            f"Selected content item {verification_id} for social media generation"
        )

        # Prepare content for AI generation
        news_items_with_ids = [
            NewsItemWithId(
                id=str(selected_item["_id"]), content=selected_item["text_content"]
            )
        ]

        # Generate engaging title and description using Gemini AI
        gemini_client = GeminiClient()

        try:
            # Prepare fact check data if available
            fact_check_data = selected_item.get("fact_check_data")

            notification_request = NotificationGenerationRequest(
                news_items=news_items_with_ids,
                notification_type="social_media_content",
                tab=tab,
                fact_check_data=fact_check_data,
            )

            notification_response = await gemini_client.generate_notification(
                notification_request
            )

            if notification_response and notification_response.is_relevant:
                title = notification_response.title
                description = notification_response.description
                logger.info(
                    f"Generated AI content - Title: {title}, Description: {description}"
                )
            else:
                # Fallback to default if AI determines content is not relevant
                reason = (
                    notification_response.relevance_reason
                    if notification_response
                    else "AI generation failed"
                )
                logger.info(f"AI determined content not relevant: {reason}")

                if selected_item.get("is_generated_news", False):
                    title = "📰 მნიშვნელოვანი სიახლე"
                    description = "ახალი ინფორმაცია, რომელიც ყურადღებას იმსახურებს"
                else:
                    title = "🔍 ფაქტების შემოწმება"
                    description = "მნიშვნელოვანი ინფორმაციის ზუსტად შემოწმება"

        except Exception as e:
            # Fallback to default content if AI generation fails
            logger.error(f"Failed to generate AI content, using fallback: {str(e)}")

            if selected_item.get("is_generated_news", False):
                title = "📰 მნიშვნელოვანი სიახლე"
                description = "ახალი ინფორმაცია, რომელიც ყურადღებას იმსახურებს"
            else:
                title = "🔍 ფაქტების შემოწმება"
                description = "მნიშვნელოვანი ინფორმაციის ზუსტად შემოწმება"

        # Generate screenshot using the verification_id
        try:
            # Get social media card title from response
            social_media_card_title = ""
            if notification_response and notification_response.social_media_card_title:
                social_media_card_title = notification_response.social_media_card_title
                logger.info(f"Using social media card title: {social_media_card_title}")

            # Get fact check summary from response
            fact_check_summary = ""
            if notification_response and notification_response.fact_check_summary:
                fact_check_summary = notification_response.fact_check_summary
                logger.info(f"Generated fact check summary: {fact_check_summary}")

            # Construct URL for screenshot with title parameter
            screenshot_url = (
                f"https://wal.ge/status/{verification_id}?static=true&tab={tab}"
            )

            # Add title parameter if available
            if social_media_card_title:
                encoded_title = urllib.parse.quote(social_media_card_title)
                screenshot_url += f"&title={encoded_title}"
            if fact_check_summary:
                encoded_fact_check_summary = urllib.parse.quote(fact_check_summary)
                screenshot_url += f"&fact_check_summary={encoded_fact_check_summary}"
            logger.info(f"Capturing screenshot for URL: {screenshot_url}")

            # Capture screenshot using ScrapeDoClient
            result = await scrape_client.scrape_with_screenshot(
                scrape_url=screenshot_url,
                full_page=False,
                wait_until="load",
                width=1920,
                height=1080,
                particularScreenShot="#static-view",
            )

            screenshot_data = result.get("screenshot_data")
            if not screenshot_data:
                raise Exception("No screenshot data received")

            logger.info(
                f"Screenshot captured, data length: {len(screenshot_data)} bytes"
            )

            # Upload screenshot to CloudFlare
            image_screenshot = await upload_image(
                file=screenshot_data,
                destination_file_name=f"{uuid.uuid4()}_social_media.jpg",
                content_type="image/jpeg",
            )

            image_url = image_screenshot.url
            logger.info(f"Screenshot uploaded successfully: {image_url}")

        except Exception as e:
            logger.error(f"Error capturing screenshot: {str(e)}")
            raise HTTPException(
                status_code=500, detail=f"Error generating screenshot: {str(e)}"
            )

        # Mark the selected verification as used by Zapier so it's not reused
        await mongo.verifications.update_one(
            {"_id": verification_id}, {"$set": {"used_by_zapier": True}}
        )

        return {
            "image_url": image_url,
            "title": social_media_card_title,
            "description": fact_check_summary,
            "social_media_card_title": social_media_card_title,
            "fact_check_summary": fact_check_summary,
            "verification_id": str(verification_id),
            "content_type": (
                "generated_news"
                if selected_item.get("is_generated_news", False)
                else "fact_check"
            ),
            "factuality_score": selected_item.get("fact_check_data", {}).get(
                "factuality", 0.0
            ),
        }

    except HTTPException:
        # Re-raise HTTPExceptions as-is
        raise
    except Exception as e:
        logger.error(f"Error generating social media content: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500, detail="Failed to generate social media content"
        )


@router.post("/send-user-notification")
async def send_user_notification(
    user_id: Annotated[str, Body(embed=True)],
    title: Annotated[str, Body(embed=True)],
    description: Annotated[str, Body(embed=True)],
    notification_type: Annotated[str, Body(embed=True)] = "feed_digest",
    verification_id: Annotated[Optional[str], Body(embed=True)] = None,
):
    """
    Send notification to a specific user
    This endpoint is called by Google Cloud Tasks
    """
    try:
        # Prepare notification data
        notification_data = {
            "type": notification_type,
            "route": "/feed-digest",  # The route in your app to navigate to
        }

        # Include verification ID if provided (similar to fact_check_completed notifications)
        if verification_id:
            notification_data["verificationId"] = verification_id
            notification_data["type"] = "fact_check_completed"

        # Send the notification
        success = await send_notification(
            user_id=user_id, title=title, message=description, data=notification_data
        )

        if success:
            logger.info(
                f"Successfully sent {notification_type} notification to user {user_id}"
            )
            return {"success": True, "user_id": user_id}
        else:
            logger.warning(
                f"Failed to send notification to user {user_id} (no push token found)"
            )
            return {"success": False, "user_id": user_id, "reason": "no_push_token"}

    except Exception as e:
        logger.error(
            f"Error sending notification to user {user_id}: {str(e)}", exc_info=True
        )
        raise HTTPException(status_code=500, detail="Failed to send notification")


# Helper function to extract social media URLs
def extract_social_media_url(text):
    # Clean up the input text
    text = text.strip()

    # First handle the case where text might start with @
    if text.startswith("@"):
        text = text[1:]

    print(f"Extracting social media URL from: {text}")

    # **Prioritize YouTube extraction**
    youtube_pattern = (
        r"(https?://(?:www\.|m\.)?(?:youtube\.com/watch\?v=|youtu\.be/)[:\w\./?=&%+-]+)"
    )
    match = re.search(youtube_pattern, text)
    if match:
        full_url = match.group(1)
        # Convert m.youtube.com to www.youtube.com
        if "m.youtube.com" in full_url:
            full_url = full_url.replace("m.youtube.com", "www.youtube.com")
        # Simple clean-up for potential extra chars
        full_url = full_url.split()[0]

        print(f"Extracted YouTube URL: {full_url}")
        return {"url": full_url, "platform": "youtube"}

    # **Then check for Facebook**
    # Check for Facebook photo links of the form https://www.facebook.com/photo/?fbid=...&set=...
    fb_photo_pattern = (
        r"https?://(?:www\.)?facebook\.com/photo/\?fbid=([\w.-]+)&set=[^\s]+"
    )
    match = re.search(fb_photo_pattern, text)
    if match:
        fbid = match.group(1)
        full_url = f"https://www.facebook.com/{fbid}"
        print(f"Converted Facebook photo URL to direct format: {full_url}")
        return {"url": full_url, "platform": "facebook"}

    # Check for mobile Facebook URLs and convert to standard web format
    mobile_fb_pattern = (
        r"https?://m\.facebook\.com/story\.php\?story_fbid=([\w.-]+)&id=([\w.-]+)"
    )
    match = re.search(mobile_fb_pattern, text)
    if match:
        story_fbid = match.group(1)
        user_id = match.group(2)
        # Convert to standard web format
        full_url = f"https://www.facebook.com/{user_id}/posts/{story_fbid}"
        print(f"Converted mobile Facebook URL to standard web format: {full_url}")
        return {"url": full_url, "platform": "facebook"}

    # Updated Facebook patterns
    # General posts/videos/photos/reels patterns
    facebook_general_pattern = r"(https?://(?:www\.)?facebook\.com/(?:(?:[^/]+/posts/|video\.php\?v=|photo\.php\?fbid=|photos/|permalink\.php\?story_fbid=|reel/)[\w.-]+(?:\?.*)?))"
    # Updated share pattern to also match links without the p/v/r segment (e.g., /share/<id>)
    facebook_share_pattern = (
        r"(https?://(?:www\.)?facebook\.com/share/(?:p/|v/|r/)?[\w-]+/?(?:\?.*)?)"
    )
    # Watch links
    facebook_watch_pattern = (
        r"(https?://(?:www\.)?facebook\.com/watch(?:/)?\?(?:v=)?([\w.-]+)(?:&.*)?)"
    )

    fb_patterns = [
        facebook_general_pattern,
        facebook_share_pattern,
        facebook_watch_pattern,
        # facebook_fallback_pattern # Keep this commented unless absolutely necessary
    ]

    for i, pattern in enumerate(fb_patterns):
        match = re.search(pattern, text)
        if match:
            full_url = match.group(1)
            # If it's a watch link, reconstruct if needed
            if pattern == facebook_watch_pattern and match.group(2):
                full_url = f"https://www.facebook.com/watch/?v={match.group(2)}"
            # Clean up potential trailing characters
            full_url = full_url.split()[0]

            print(f"Extracted Facebook URL: {full_url}")
            return {"url": full_url, "platform": "facebook"}

    # **Finally, check for X/Twitter**
    # Handles x.com and twitter.com, including status links
    x_pattern = (
        r"(https?://(?:www\.)?(?:x\.com|twitter\.com)/[^/]+/status/[\w]+(?:\?.*)?)"
    )
    # Fallback using raw string
    x_fallback_pattern = (
        r"(https?://(?:www\.)?(?:x\.com|twitter\.com)/[^\s!@#$%^&*()_+={}|\\:;<>,.?/]+)"
    )

    match = re.search(x_pattern, text) or re.search(x_fallback_pattern, text)
    if match:
        full_url = match.group(1)
        full_url = full_url.split()[0]

        print(f"Extracted X/Twitter URL: {full_url}")
        return {"url": full_url, "platform": "x"}

    print("No social media URL found in text")
    return None
