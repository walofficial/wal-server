"""
AI Characters API Routes.

Endpoints for creating AI characters, chatting with them,
managing memories, and handling batch job completion.
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request, UploadFile, File, Form
from pydantic import BaseModel

from ment_api.configurations.config import settings
from ment_api.models.ai_character import (
    AddMemoryRequest,
    AddMemoryResponse,
    AIChatRequest,
    AIChatResponse,
    AICharacterDetail,
    AICharacterListResponse,
    AICharacterResponse,
    BatchCompleteRequest,
    BatchCompleteResponse,
    CreateAICharacterRequest,
    CreateLocationAssetRequest,
    ExecutePostRequest,
    ExecutePostResponse,
    GoLiveRequest,
    GoLiveResponse,
    MemoryDetail,
    MemoryListResponse,
    PollBatchJobsResponse,
    UpdateAICharacterRequest,
    UpdateAICharacterResponse,
)
from ment_api.persistence import mongo
from ment_api.services.ai_character_memory_service import (
    add_global_memory,
    get_memory_count,
    get_recent_memories,
)
from ment_api.services.ai_character_service import (
    create_ai_character,
    generate_chat_response,
    get_all_characters,
    get_character_by_id,
    get_character_by_user_id,
    get_character_doc_by_user_id,
)
from ment_api.services.external_clients.cloud_flare_client import upload_image
from ment_api.services.google_tasks_service import create_http_task
from ment_api.services.profile_placeholder_generator import set_placeholder_avatar

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/ai-characters",
    tags=["ai-characters"],
    responses={404: {"description": "Not found"}},
)


# ============================================================================
# Character Management Endpoints
# ============================================================================


@router.post(
    "/create",
    response_model=AICharacterResponse,
    operation_id="create_ai_character",
)
async def create_character_endpoint(
    request: Request,
    name: str = Form(...),
    personality: str = Form(...),
    post_instructions: str = Form(...),
    chat_personality: str = Form(...),
    allowed_feed_ids: str = Form(...),  # Comma-separated feed IDs
    active_hours: str = Form(default="9,10,11,12,13,14,15,16,17,18,19,20,21"),
    max_posts_per_day: int = Form(default=3),
    chat_enabled: bool = Form(default=True),
    face_images: List[UploadFile] = File(default=[]),
):
    """
    Create a new AI character with associated virtual user.

    This endpoint:
    1. Creates a virtual user in the users collection
    2. Uploads face images to cloud storage
    3. Creates the AI character document
    4. Sets up live user presence at allowed feeds
    """
    try:
        # Parse inputs
        feed_ids = [
            ObjectId(fid.strip()) for fid in allowed_feed_ids.split(",") if fid.strip()
        ]
        hours = [int(h.strip()) for h in active_hours.split(",") if h.strip()]

        # Generate unique user ID for the virtual user
        virtual_user_id = f"ai_{uuid.uuid4().hex[:16]}"

        # Upload face images if provided
        uploaded_face_images = []
        for idx, image_file in enumerate(face_images):
            content = await image_file.read()
            content_type = image_file.content_type or "image/jpeg"
            filename = f"ai_characters/{virtual_user_id}/face_{idx}_{uuid.uuid4().hex[:8]}.jpg"

            result = await upload_image(content, filename, content_type)
            uploaded_face_images.append(result.url)

        # Create virtual user document
        await mongo.users.insert_one(
            {
                "external_user_id": virtual_user_id,
                "username": name,
                "email": f"{virtual_user_id}@virtual.wal.app",
                "phone_number": f"+0{uuid.uuid4().int % 1000000000}",
                "photos": [{"image_url": uploaded_face_images}]
                if uploaded_face_images
                else [],
                "is_in_waitlist": False,
                "is_virtual": True,  # Mark as virtual user
                "date_of_birth": "01/02/2000",
                "interests": [],
                "gender": "female",
                "bio": personality
            }
        )

        # Set placeholder avatar if no face images
        if not uploaded_face_images:
            await set_placeholder_avatar(virtual_user_id, name, 256)

        # Create AI character document
        character_id = await create_ai_character(
            name=name,
            personality=personality,
            post_instructions=post_instructions,
            chat_personality=chat_personality,
            user_id=virtual_user_id,
            allowed_feed_ids=feed_ids,
            face_images=uploaded_face_images,
            active_hours=hours,
            max_posts_per_day=max_posts_per_day,
            chat_enabled=chat_enabled,
        )

        # Set up live user presence at all allowed feeds (far future expiration)
        far_future = datetime.now(timezone.utc) + timedelta(days=365 * 100)  # 100 years
        for feed_id in feed_ids:
            await mongo.live_users.insert_one(
                {
                    "author_id": virtual_user_id,
                    "feed_id": feed_id,
                    "expiration_date": far_future,
                    "created_at": datetime.now(timezone.utc),
                }
            )

        logger.info(
            "Created AI character",
            extra={
                "json_fields": {
                    "operation": "create_ai_character",
                    "character_id": str(character_id),
                    "user_id": virtual_user_id,
                    "name": name,
                    "feed_count": len(feed_ids),
                },
                "labels": {"component": "ai_characters_route"},
            },
        )

        return AICharacterResponse(
            character_id=str(character_id),
            user_id=virtual_user_id,
            name=name,
        )

    except Exception as e:
        logger.error(
            f"Failed to create AI character: {e}",
            extra={
                "json_fields": {"operation": "create_ai_character", "error": str(e)},
                "labels": {"component": "ai_characters_route", "severity": "high"},
            },
        )
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/{character_id}",
    response_model=AICharacterDetail,
    operation_id="get_ai_character",
)
async def get_character_endpoint(character_id: str) -> AICharacterDetail:
    """Get an AI character by ID."""
    try:
        character = await get_character_by_id(ObjectId(character_id))
        if not character:
            raise HTTPException(status_code=404, detail="Character not found")
        return character
    except HTTPException:
        raise
    except Exception as e:
        if "not found" in str(e).lower():
            raise HTTPException(status_code=404, detail="Character not found")
        raise HTTPException(status_code=500, detail=str(e))


@router.put(
    "/{character_id}",
    response_model=UpdateAICharacterResponse,
    operation_id="update_ai_character",
)
async def update_character_endpoint(
    character_id: str,
    update_request: UpdateAICharacterRequest,
) -> UpdateAICharacterResponse:
    """
    Update an existing AI character.

    Only provided fields will be updated (partial update).
    """
    try:
        character_obj_id = ObjectId(character_id)
        
        # Verify character exists
        existing = await mongo.ai_characters.find_one_by_id(character_obj_id)
        if not existing:
            raise HTTPException(status_code=404, detail="Character not found")

        # Build update document with only provided fields
        update_data: dict = {}
        
        if update_request.name is not None:
            update_data["name"] = update_request.name
            # Also update the linked user's username
            await mongo.users.update_one(
                {"external_user_id": existing["user_id"]},
                {"$set": {"username": update_request.name}},
            )
        
        if update_request.personality is not None:
            update_data["personality"] = update_request.personality
            # Also update user bio
            await mongo.users.update_one(
                {"external_user_id": existing["user_id"]},
                {"$set": {"bio": update_request.personality[:200]}},
            )
        
        if update_request.post_instructions is not None:
            update_data["post_instructions"] = update_request.post_instructions
        
        if update_request.chat_personality is not None:
            update_data["chat_personality"] = update_request.chat_personality
        
        if update_request.allowed_feed_ids is not None:
            new_feed_ids = [ObjectId(fid) for fid in update_request.allowed_feed_ids]
            update_data["allowed_feed_ids"] = new_feed_ids
            
            # Update live_users presence
            old_feed_ids = set(str(fid) for fid in existing.get("allowed_feed_ids", []))
            new_feed_ids_str = set(update_request.allowed_feed_ids)
            
            # Remove from feeds no longer allowed
            feeds_to_remove = old_feed_ids - new_feed_ids_str
            if feeds_to_remove:
                await mongo.live_users.delete_many({
                    "author_id": existing["user_id"],
                    "feed_id": {"$in": [ObjectId(fid) for fid in feeds_to_remove]},
                })
            
            # Add to new feeds
            feeds_to_add = new_feed_ids_str - old_feed_ids
            far_future = datetime.now(timezone.utc) + timedelta(days=365 * 100)
            for feed_id in feeds_to_add:
                await mongo.live_users.insert_one({
                    "author_id": existing["user_id"],
                    "feed_id": ObjectId(feed_id),
                    "expiration_date": far_future,
                    "created_at": datetime.now(timezone.utc),
                })
        
        if update_request.active_hours is not None:
            update_data["active_hours"] = update_request.active_hours
        
        if update_request.max_posts_per_day is not None:
            update_data["max_posts_per_day"] = update_request.max_posts_per_day
        
        if update_request.chat_enabled is not None:
            update_data["chat_enabled"] = update_request.chat_enabled
        
        if update_request.is_active is not None:
            update_data["is_active"] = update_request.is_active

        if not update_data:
            return UpdateAICharacterResponse(
                status="no_changes",
                character_id=character_id,
            )

        # Apply update
        await mongo.ai_characters.update_one(
            {"_id": character_obj_id},
            {"$set": update_data},
        )

        logger.info(
            "Updated AI character",
            extra={
                "json_fields": {
                    "operation": "update_ai_character",
                    "character_id": character_id,
                    "updated_fields": list(update_data.keys()),
                },
                "labels": {"component": "ai_characters_route"},
            },
        )

        return UpdateAICharacterResponse(
            status="updated",
            character_id=character_id,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Failed to update AI character: {e}",
            extra={
                "json_fields": {
                    "operation": "update_ai_character",
                    "character_id": character_id,
                    "error": str(e),
                },
                "labels": {"component": "ai_characters_route", "severity": "high"},
            },
        )
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/{character_id}/go-live",
    response_model=GoLiveResponse,
    operation_id="ai_character_go_live",
)
async def go_live_endpoint(
    character_id: str,
    request: GoLiveRequest,
) -> GoLiveResponse:
    """
    Make an AI character go live at a specific feed.

    This endpoint:
    1. Verifies the character exists
    2. Verifies the feed exists
    3. Checks if the character is already live at the feed
    4. Creates a live_users entry with far future expiration
    5. Optionally adds the feed to allowed_feed_ids if not present
    """
    try:
        character_obj_id = ObjectId(character_id)
        feed_obj_id = ObjectId(request.feed_id)

        # Verify character exists
        character = await mongo.ai_characters.find_one_by_id(character_obj_id)
        if not character:
            raise HTTPException(status_code=404, detail="Character not found")

        # Verify feed exists
        feed = await mongo.feeds.find_one({"_id": feed_obj_id})
        if not feed:
            raise HTTPException(status_code=404, detail="Feed not found")

        user_id = character["user_id"]

        # Check if already live at this feed
        existing_live = await mongo.live_users.find_one({
            "author_id": user_id,
            "feed_id": feed_obj_id,
        })

        if existing_live:
            return GoLiveResponse(
                status="already_live",
                character_id=character_id,
                feed_id=request.feed_id,
                message=f"Character is already live at feed '{feed.get('feed_title', request.feed_id)}'",
            )

        # Create live_users entry with far future expiration
        far_future = datetime.now(timezone.utc) + timedelta(days=365 * 100)  # 100 years
        await mongo.live_users.insert_one({
            "author_id": user_id,
            "feed_id": feed_obj_id,
            "expiration_date": far_future,
            "created_at": datetime.now(timezone.utc),
        })

        # Add feed to allowed_feed_ids if not present
        current_feed_ids = character.get("allowed_feed_ids", [])
        if feed_obj_id not in current_feed_ids:
            await mongo.ai_characters.update_one(
                {"_id": character_obj_id},
                {"$addToSet": {"allowed_feed_ids": feed_obj_id}},
            )

        logger.info(
            "AI character went live at feed",
            extra={
                "json_fields": {
                    "operation": "go_live",
                    "character_id": character_id,
                    "feed_id": request.feed_id,
                    "user_id": user_id,
                },
                "labels": {"component": "ai_characters_route"},
            },
        )

        return GoLiveResponse(
            status="live",
            character_id=character_id,
            feed_id=request.feed_id,
            message=f"Character is now live at feed '{feed.get('feed_title', request.feed_id)}'",
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Failed to go live: {e}",
            extra={
                "json_fields": {
                    "operation": "go_live",
                    "character_id": character_id,
                    "feed_id": request.feed_id,
                    "error": str(e),
                },
                "labels": {"component": "ai_characters_route", "severity": "high"},
            },
        )
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# Chat Endpoints
# ============================================================================


@router.post(
    "/{character_id}/chat",
    response_model=AIChatResponse,
    operation_id="chat_with_ai_character",
)
async def chat_with_character(
    request: Request,
    character_id: str,
    chat_request: AIChatRequest,
) -> AIChatResponse:
    """
    Send a message to an AI character and get a RAG-enhanced response.

    The response is generated using context from previous conversations
    with this user and global character knowledge.
    """
    user_id = request.state.supabase_user_id
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")


    room_id = ObjectId(chat_request.room_id) if chat_request.room_id else None

    # Get raw doc for generate_chat_response (needs dict)
    response = await generate_chat_response(
        character_id=ObjectId(character_id),
        user_id=user_id,
        user_message=chat_request.message,
        room_id=room_id,
    )

    return AIChatResponse(
        response=response,
        character_id=character_id,
    )


# ============================================================================
# Memory Management Endpoints
# ============================================================================


@router.post(
    "/{character_id}/add-memory",
    response_model=AddMemoryResponse,
    operation_id="add_ai_character_memory",
)
async def add_memory_endpoint(
    character_id: str,
    memory_request: AddMemoryRequest,
) -> AddMemoryResponse:
    """
    Manually add a global memory to an AI character.

    This is useful for adding backstory, facts, or information
    that should be available in all conversations.
    """
    character = await get_character_by_id(ObjectId(character_id))
    if not character:
        raise HTTPException(status_code=404, detail="Character not found")

    memory_id = await add_global_memory(
        character_id=ObjectId(character_id),
        content=memory_request.content,
        topic=memory_request.topic,
    )

    return AddMemoryResponse(memory_id=str(memory_id), status="created")


@router.get(
    "/{character_id}/memories",
    response_model=MemoryListResponse,
    operation_id="list_ai_character_memories",
)
async def list_memories_endpoint(
    character_id: str,
    user_id: Optional[str] = None,
    limit: int = 50,
    include_global: bool = True,
) -> MemoryListResponse:
    """
    List memories for an AI character (admin endpoint).

    Can filter by user_id to see per-user memories,
    or set include_global=True to include shared memories.
    """
    character = await get_character_by_id(ObjectId(character_id))
    if not character:
        raise HTTPException(status_code=404, detail="Character not found")

    memories = await get_recent_memories(
        character_id=ObjectId(character_id),
        user_id=user_id,
        limit=limit,
        include_global=include_global,
    )

    stats = await get_memory_count(ObjectId(character_id), user_id)

    return MemoryListResponse(memories=memories, total=stats.total)


# ============================================================================
# Batch Job & Post Execution Endpoints
# ============================================================================


@router.post(
    "/batch-complete",
    response_model=BatchCompleteResponse,
    operation_id="ai_character_batch_complete",
)
async def batch_complete_endpoint(request: BatchCompleteRequest) -> BatchCompleteResponse:
    """
    Handle completion of a Gemini Batch API job.

    This endpoint:
    1. Retrieves the batch job metadata
    2. Processes each generated image result
    3. Schedules Cloud Tasks for staggered post insertion
    """
    batch_job = await mongo.ai_batch_jobs.find_one(
        {"batch_job_name": request.batch_job_name}
    )
    if not batch_job:
        raise HTTPException(status_code=404, detail="Batch job not found")

    if batch_job["status"] == "COMPLETED":
        return BatchCompleteResponse(status="already_processed")

    try:
        for idx, (result, meta) in enumerate(zip(request.results, batch_job["posts"])):
            # Upload generated image to cloud storage
            if result.get("image_data"):
                import base64

                image_bytes = base64.b64decode(result["image_data"])
                filename = f"ai_posts/{meta['character_user_id']}/{uuid.uuid4().hex}.jpg"
                uploaded = await upload_image(image_bytes, filename, "image/jpeg")
                image_url = uploaded.url
                image_dims = {
                    "url": uploaded.url,
                    "width": uploaded.width,
                    "height": uploaded.height,
                }
            else:
                # Skip if no image generated
                logger.warning(f"No image data for batch result {idx}")
                continue

            # Schedule post with Cloud Task (staggered by scheduled_delay)
            scheduled_time = datetime.now(timezone.utc) + timedelta(
                seconds=meta["scheduled_delay"]
            )

            create_http_task(
                url=f"{settings.api_url}/ai-characters/execute-post",
                json_payload={
                    "character_user_id": meta["character_user_id"],
                    "feed_id": meta["feed_id"],
                    "text_content": meta["text_content"],
                    "image_url": image_url,
                    "image_dims": image_dims,
                },
                schedule_time=scheduled_time,
            )

        # Update batch job status
        await mongo.ai_batch_jobs.update_one(
            {"_id": batch_job["_id"]},
            {
                "$set": {
                    "status": "COMPLETED",
                    "completed_at": datetime.now(timezone.utc),
                }
            },
        )

        logger.info(
            "Processed batch completion",
            extra={
                "json_fields": {
                    "operation": "batch_complete",
                    "batch_job_name": request.batch_job_name,
                    "posts_scheduled": len(batch_job["posts"]),
                },
                "labels": {"component": "ai_characters_route"},
            },
        )

        return BatchCompleteResponse(
            status="processed",
            posts_scheduled=len(batch_job["posts"]),
        )

    except Exception as e:
        await mongo.ai_batch_jobs.update_one(
            {"_id": batch_job["_id"]},
            {"$set": {"status": "FAILED"}},
        )
        logger.error(f"Batch completion failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/execute-post",
    response_model=ExecutePostResponse,
    operation_id="execute_ai_character_post",
)
async def execute_post_endpoint(request: ExecutePostRequest) -> ExecutePostResponse:
    """
    Execute a scheduled AI character post.

    Called by Cloud Tasks to insert the verification document
    at the scheduled time.
    """
    try:
        # Get the feed to ensure it exists
        feed = await mongo.feeds.find_one({"_id": ObjectId(request.feed_id)})
        if not feed:
            raise HTTPException(status_code=404, detail="Feed not found")

        # Build image gallery
        image_gallery = []
        if request.image_url:
            image_gallery.append(
                {
                    "url": request.image_url,
                    "width": request.image_dims.get("width") if request.image_dims else None,
                    "height": request.image_dims.get("height") if request.image_dims else None,
                }
            )

        # Insert verification document (post)
        verification_doc = {
            "feed_id": ObjectId(request.feed_id),
            "assignee_user_id": request.character_user_id,
            "text_content": request.text_content,
            "state": "READY_FOR_USE",
            "is_public": True,
            "image_gallery_with_dims": image_gallery,
            "created_at": datetime.now(timezone.utc),
            "is_ai_generated": True,
        }

        result = await mongo.verifications.insert_one(verification_doc)

        logger.info(
            "Executed AI character post",
            extra={
                "json_fields": {
                    "operation": "execute_post",
                    "verification_id": str(result.inserted_id),
                    "character_user_id": request.character_user_id,
                    "feed_id": request.feed_id,
                },
                "labels": {"component": "ai_characters_route"},
            },
        )

        return ExecutePostResponse(
            status="posted",
            verification_id=str(result.inserted_id),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to execute post: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# Batch Job Polling Endpoint
# ============================================================================


@router.post(
    "/poll-batch-jobs",
    response_model=PollBatchJobsResponse,
    operation_id="poll_ai_batch_jobs",
)
async def poll_batch_jobs_endpoint() -> PollBatchJobsResponse:
    """
    Poll for pending Gemini Batch API jobs and trigger completion handlers.

    Called by Cloud Scheduler every 5 minutes to check job status.
    """
    from ment_api.services.external_clients.gemini_client import gemini_client

    pending_jobs = await mongo.ai_batch_jobs.find_all(
        {"status": {"$in": ["PENDING", "PROCESSING"]}}
    )

    processed = 0
    failed = 0
    for job in pending_jobs:
        try:
            # Check job status with Gemini API
            batch_status = await gemini_client.aio.batches.get(
                name=job["batch_job_name"]
            )

            if batch_status.state.name == "JOB_STATE_SUCCEEDED":
                # Get results from inline responses
                results: List[dict] = []
                
                if batch_status.dest and batch_status.dest.inlined_responses:
                    # Results are inline (for generate content requests)
                    for inline_response in batch_status.dest.inlined_responses:
                        if inline_response.response and inline_response.response.candidates:
                            for candidate in inline_response.response.candidates:
                                for part in candidate.content.parts:
                                    if hasattr(part, "inline_data") and part.inline_data:
                                        results.append(
                                            {"image_data": part.inline_data.data}
                                        )
                        elif inline_response.error:
                            logger.error(
                                f"Batch response error: {inline_response.error}",
                                extra={
                                    "json_fields": {
                                        "operation": "poll_batch_jobs",
                                        "batch_job_name": job["batch_job_name"],
                                        "error": str(inline_response.error),
                                    },
                                    "labels": {"component": "ai_characters_route"},
                                },
                            )
                elif batch_status.dest and batch_status.dest.file_name:
                    # Results are in a file (shouldn't happen for our use case, but handle it)
                    logger.warning(
                        f"Batch results in file format not supported: {batch_status.dest.file_name}",
                        extra={
                            "json_fields": {
                                "operation": "poll_batch_jobs",
                                "batch_job_name": job["batch_job_name"],
                                "file_name": batch_status.dest.file_name,
                            },
                            "labels": {"component": "ai_characters_route"},
                        },
                    )
                    continue
                else:
                    logger.warning(
                        f"No results found for batch job",
                        extra={
                            "json_fields": {
                                "operation": "poll_batch_jobs",
                                "batch_job_name": job["batch_job_name"],
                            },
                            "labels": {"component": "ai_characters_route"},
                        },
                    )
                    continue

                # Call batch complete handler
                await batch_complete_endpoint(
                    BatchCompleteRequest(
                        batch_job_name=job["batch_job_name"],
                        results=results,
                    )
                )
                processed += 1

            elif batch_status.state.name == "JOB_STATE_FAILED":
                await mongo.ai_batch_jobs.update_one(
                    {"_id": job["_id"]},
                    {"$set": {"status": "FAILED"}},
                )
                logger.error(
                    f"Batch job failed",
                    extra={
                        "json_fields": {
                            "operation": "poll_batch_jobs",
                            "batch_job_name": job["batch_job_name"],
                            "error": str(batch_status.error) if batch_status.error else "Unknown",
                        },
                        "labels": {"component": "ai_characters_route"},
                    },
                )
                failed += 1

            elif batch_status.state.name == "JOB_STATE_RUNNING":
                await mongo.ai_batch_jobs.update_one(
                    {"_id": job["_id"]},
                    {"$set": {"status": "PROCESSING"}},
                )
            
            elif batch_status.state.name in ["JOB_STATE_CANCELLED", "JOB_STATE_EXPIRED"]:
                await mongo.ai_batch_jobs.update_one(
                    {"_id": job["_id"]},
                    {"$set": {"status": batch_status.state.name}},
                )
                logger.warning(
                    f"Batch job {batch_status.state.name}",
                    extra={
                        "json_fields": {
                            "operation": "poll_batch_jobs",
                            "batch_job_name": job["batch_job_name"],
                            "state": batch_status.state.name,
                        },
                        "labels": {"component": "ai_characters_route"},
                    },
                )
                failed += 1

        except Exception as e:
            logger.error(f"Failed to poll batch job {job['batch_job_name']}: {e}")
            failed += 1
            continue

    return PollBatchJobsResponse(
        status="polled",
        jobs_checked=len(pending_jobs),
        processed=processed,
        failed=failed,
    )


# ============================================================================
# List Characters Endpoint
# ============================================================================


@router.get(
    "/",
    response_model=AICharacterListResponse,
    operation_id="list_ai_characters",
)
async def list_characters_endpoint(
    is_active: Optional[bool] = None,
    limit: int = 50,
) -> AICharacterListResponse:
    """List all AI characters with optional active filter."""
    if is_active is None:
        characters = await get_all_characters(only_active=False)
    else:
        characters = await get_all_characters(only_active=is_active)

    return AICharacterListResponse(
        characters=characters[:limit],
        total=len(characters),
    )

