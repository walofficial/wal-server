"""
AI Character Post Generation Worker.

This worker is triggered by Cloud Scheduler to generate posts for AI characters.
It generates text content using Gemini, builds image prompts, and submits
batch jobs for image generation.
"""

import json
import logging
import random
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from bson import ObjectId
from google.cloud.pubsub_v1.subscriber.message import Message

from ment_api.configurations.config import settings
from ment_api.persistence import mongo
from ment_api.services.ai_character_service import (
    get_active_characters,
    increment_post_count,
    reset_daily_post_counts,
)
from ment_api.services.external_clients.gemini_client import gemini_client

logger = logging.getLogger(__name__)

# Post text generation prompt template
POST_TEXT_PROMPT = """შენ ხარ {character_name}, {personality}.
ადგილი: {location_name} - {location_description}
დრო: {current_time} (თბილისი)

დაწერე მოკლე პოსტი (1-2 წინადადება) ქართულად.
ბუნებრივი ტონი, არა რობოტული. შეიძლება 1-2 ემოჯი.

უპასუხე JSON ფორმატში:
{{"text_content": "...", "mood": "happy/energetic/chill/thoughtful"}}"""


async def generate_post_text(
    character: dict,
    location: dict,
    current_time: str,
) -> Optional[dict]:
    """
    Generate post text using Gemini.

    Args:
        character: The AI character document
        location: The location asset document
        current_time: Formatted current time string

    Returns:
        Dictionary with text_content and mood, or None on failure
    """
    try:
        prompt = POST_TEXT_PROMPT.format(
            character_name=character["name"],
            personality=character.get("personality", ""),
            location_name=location.get("feed_name", ""),
            location_description=location.get("description", ""),
            current_time=current_time,
        )

        # Add post instructions if available
        if character.get("post_instructions"):
            prompt = f"{character['post_instructions']}\n\n{prompt}"

        response = await gemini_client.aio.models.generate_content(
            model="gemini-2.5-flash",
            contents=[{"role": "user", "parts": [{"text": prompt}]}],
        )

        # Parse JSON response
        text = response.text.strip()
        # Handle potential markdown code blocks
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        text = text.strip()

        result = json.loads(text)

        logger.info(
            "Generated post text",
            extra={
                "json_fields": {
                    "operation": "generate_post_text",
                    "character_id": str(character["_id"]),
                    "location": location.get("feed_name"),
                    "mood": result.get("mood"),
                },
                "labels": {"component": "ai_character_worker"},
            },
        )

        return result

    except Exception as e:
        logger.error(
            f"Failed to generate post text: {e}",
            extra={
                "json_fields": {
                    "operation": "generate_post_text",
                    "character_id": str(character["_id"]),
                    "error": str(e),
                },
                "labels": {"component": "ai_character_worker", "severity": "high"},
            },
        )
        return None


def build_image_prompt(
    character: dict,
    location: dict,
    mood: str,
) -> str:
    """
    Build a detailed image generation prompt for Nano Banana.

    Generates varied prompt styles (Instagram photo, Facebook photo, candid shot, etc.)
    using character and location information.

    Args:
        character: The AI character document
        location: The location asset document
        mood: The mood from text generation (happy/energetic/chill/thoughtful)

    Returns:
        Detailed image generation prompt string
    """
    # Map moods to visual descriptions
    mood_visuals = {
        "happy": "smiling, bright expression, positive energy",
        "energetic": "dynamic pose, active, vibrant energy",
        "chill": "relaxed, casual pose, comfortable atmosphere",
        "thoughtful": "contemplative, focused, serene expression",
    }

    mood_desc = mood_visuals.get(mood, mood_visuals["happy"])

    # Random photo style templates for variety
    photo_styles = [
        {
            "style": "Instagram photo",
            "details": "aesthetic composition, natural lighting, filtered, social media ready",
        },
        {
            "style": "Facebook casual photo",
            "details": "candid moment, everyday life, relatable, authentic feel",
        },
        {
            "style": "Selfie",
            "details": "front-facing camera angle, personal moment, direct eye contact or slight angle",
        },
        {
            "style": "Candid snapshot",
            "details": "unposed, natural moment captured, spontaneous, real-life scene",
        },
        {
            "style": "Portrait photo",
            "details": "focused on subject, good lighting, clear facial features, professional quality",
        },
        {
            "style": "Lifestyle photo",
            "details": "activity-based, showing personality, environmental context, storytelling",
        },
        {
            "style": "Story post",
            "details": "casual, in-the-moment, vertical format style, ephemeral feel",
        },
    ]

    # Select random photo style
    selected_style = random.choice(photo_styles)

    # Get location details
    location_name = location.get("feed_name", "")
    location_description = location.get("description", "")

    # Build comprehensive prompt
    prompt_parts = [
        f"{selected_style['style']} of {character['name']}",
        f"Character description: {character.get('personality', '')}",
        f"Mood and expression: {mood_desc}",
        f"Location: {location_name}",
        f"Location context: {location_description}",
        f"Photo style: {selected_style['details']}",
        "Realistic, high quality, natural colors",
        "Shot on smartphone camera",
    ]

    return ". ".join(prompt_parts)


async def process_ai_character_trigger(message: dict = None):
    """
    Main worker function triggered by Cloud Scheduler/PubSub.

    This function:
    1. Resets daily post counts if needed
    2. Gets active characters for current hour
    3. Generates text for each character/location pair
    4. Submits Gemini Batch API job for image generation
    5. Stores batch job metadata for polling

    Args:
        message: Optional PubSub message payload
    """
    try:
        # Get current time in Tbilisi (GMT+4)
        tbilisi_tz = timezone(timedelta(hours=4))
        now_tbilisi = datetime.now(tbilisi_tz)
        current_hour = now_tbilisi.hour
        current_time = now_tbilisi.strftime("%H:%M, %d %B")

        # Check if we should reset daily counts (at midnight)
        if current_hour == 0:
            await reset_daily_post_counts()

        # Get active characters
        characters = await get_active_characters(current_hour)

        if not characters:
            logger.info(
                "No active characters for current hour",
                extra={
                    "json_fields": {
                        "operation": "process_trigger",
                        "current_hour": current_hour,
                    },
                    "labels": {"component": "ai_character_worker"},
                },
            )
            return {"status": "no_active_characters", "hour": current_hour}

        batch_requests = []
        post_metadata = []

        for character in characters:
            # Select random feeds for this character (1-2 posts)
            feed_ids = character.get("allowed_feed_ids", [])
            if not feed_ids:
                continue

            selected_feeds = random.sample(
                feed_ids, min(random.randint(1, 2), len(feed_ids))
            )

            for feed_id in selected_feeds:
                # Get location assets
                location = await mongo.location_assets.find_one({"feed_id": feed_id})
                if not location:
                    logger.warning(f"No location assets for feed {feed_id}")
                    continue

                # Generate text content
                text_result = await generate_post_text(
                    character, location, current_time
                )
                if not text_result:
                    continue

                # Build image prompt
                image_prompt = build_image_prompt(
                    character, location, text_result.get("mood", "happy")
                )

                # Prepare batch request for image generation
                batch_requests.append(
                    {
                        "contents": [{"parts": [{"text": image_prompt}]}],
                        "config": {"response_modalities": ["IMAGE"]},
                    }
                )

                # Store metadata for later processing
                post_metadata.append(
                    {
                        "character_id": str(character["_id"]),
                        "character_user_id": character["user_id"],
                        "character_name": character["name"],
                        "feed_id": str(feed_id),
                        "text_content": text_result["text_content"],
                        "scheduled_delay": random.randint(0, 3600),  # 0-60 min delay
                    }
                )

                # Increment post count
                await increment_post_count(character["_id"])

        if not batch_requests:
            logger.info(
                "No posts to generate",
                extra={
                    "json_fields": {
                        "operation": "process_trigger",
                        "characters_checked": len(characters),
                    },
                    "labels": {"component": "ai_character_worker"},
                },
            )
            return {"status": "no_posts_generated"}

        # Submit Batch API job for image generation
        try:
            batch_job = await gemini_client.aio.batches.create(
                model="gemini-2.5-flash",
                src=batch_requests,
            )

            # Store batch job in database
            await mongo.ai_batch_jobs.insert_one(
                {
                    "batch_job_name": batch_job.name,
                    "status": "PENDING",
                    "posts": post_metadata,
                    "created_at": datetime.now(timezone.utc),
                }
            )

            logger.info(
                "Submitted batch job for image generation",
                extra={
                    "json_fields": {
                        "operation": "submit_batch_job",
                        "batch_job_name": batch_job.name,
                        "posts_count": len(post_metadata),
                    },
                    "labels": {"component": "ai_character_worker"},
                },
            )

            return {
                "status": "batch_submitted",
                "batch_job_name": batch_job.name,
                "posts_count": len(post_metadata),
            }

        except Exception as batch_error:
            logger.error(
                f"Failed to submit batch job: {batch_error}",
                extra={
                    "json_fields": {
                        "operation": "submit_batch_job",
                        "error": str(batch_error),
                    },
                    "labels": {"component": "ai_character_worker", "severity": "high"},
                },
            )
            # Fallback: Generate images one by one
            return await process_posts_individually(post_metadata, current_time)

    except Exception as e:
        logger.error(
            f"AI character worker failed: {e}",
            extra={
                "json_fields": {"operation": "process_trigger", "error": str(e)},
                "labels": {"component": "ai_character_worker", "severity": "high"},
            },
        )
        raise


async def process_posts_individually(
    post_metadata: List[dict],
    current_time: str,
) -> dict:
    """
    Fallback function to process posts one at a time if batch API fails.

    This uses regular Gemini image generation instead of batch.
    """
    from ment_api.services.external_clients.cloud_flare_client import upload_image
    from ment_api.services.google_tasks_service import create_http_task
    import uuid

    scheduled = 0

    for meta in post_metadata:
        try:
            # Get character and location for prompt building
            character = await mongo.ai_characters.find_one(
                {"_id": ObjectId(meta["character_id"])}
            )
            location = await mongo.location_assets.find_one(
                {"feed_id": ObjectId(meta["feed_id"])}
            )

            if not character or not location:
                continue

            # Build image prompt
            image_prompt = build_image_prompt(character, location, "happy")

            # Generate image using Gemini
            response = await gemini_client.aio.models.generate_content(
                model="gemini-2.5-flash",
                contents=[{"parts": [{"text": image_prompt}]}],
                config={"response_modalities": ["IMAGE"]},
            )

            # Extract image data
            image_data = None
            if response.candidates:
                for candidate in response.candidates:
                    if candidate.content and candidate.content.parts:
                        for part in candidate.content.parts:
                            if hasattr(part, "inline_data") and part.inline_data:
                                import base64

                                image_data = base64.b64decode(part.inline_data.data)
                                break

            if not image_data:
                logger.warning(f"No image generated for post {meta['character_name']}")
                continue

            # Upload image
            filename = f"ai_posts/{meta['character_user_id']}/{uuid.uuid4().hex}.jpg"
            uploaded = await upload_image(image_data, filename, "image/jpeg")

            # Schedule post with Cloud Task
            scheduled_time = datetime.now(timezone.utc) + timedelta(
                seconds=meta["scheduled_delay"]
            )

            create_http_task(
                url=f"{settings.api_url}/ai-characters/execute-post",
                json_payload={
                    "character_user_id": meta["character_user_id"],
                    "feed_id": meta["feed_id"],
                    "text_content": meta["text_content"],
                    "image_url": uploaded.url,
                    "image_dims": {
                        "url": uploaded.url,
                        "width": uploaded.width,
                        "height": uploaded.height,
                    },
                },
                schedule_time=scheduled_time,
            )

            scheduled += 1

        except Exception as e:
            logger.error(f"Failed to process individual post: {e}")
            continue

    return {"status": "individual_processing", "posts_scheduled": scheduled}


# Entry point for PubSub trigger
async def pubsub_handler(message: dict):
    """Handle PubSub message trigger."""
    return await process_ai_character_trigger(message)


async def process_ai_character_callback(message: Message) -> None:
    """
    PubSub callback handler for AI character post generation.

    This is the entry point called by the PubSub subscriber.
    Similar to other workers (news_worker, check_fact_worker, etc.)
    """
    import json

    try:
        # Parse the message data
        message_data = message.data
        if isinstance(message_data, bytes):
            message_data = message_data.decode("utf-8")

        payload = {}
        if message_data:
            try:
                payload = json.loads(message_data)
            except json.JSONDecodeError:
                payload = {"trigger": "pubsub"}

        logger.info(
            "Received AI character trigger message",
            extra={
                "json_fields": {
                    "operation": "process_ai_character_callback",
                    "message_id": message.message_id,
                    "payload": payload,
                },
                "labels": {"component": "ai_character_worker"},
            },
        )

        # Process the trigger
        result = await process_ai_character_trigger(payload)

        logger.info(
            "AI character trigger completed",
            extra={
                "json_fields": {
                    "operation": "process_ai_character_callback",
                    "message_id": message.message_id,
                    "result": result,
                },
                "labels": {"component": "ai_character_worker"},
            },
        )

    except Exception as e:
        logger.error(
            f"Failed to process AI character trigger: {e}",
            extra={
                "json_fields": {
                    "operation": "process_ai_character_callback",
                    "error": str(e),
                },
                "labels": {"component": "ai_character_worker", "severity": "high"},
            },
        )
        raise

