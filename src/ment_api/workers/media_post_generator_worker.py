import logging
import time
from datetime import datetime, timedelta, timezone
from typing import List

import httpx
from google.pubsub_v1 import ReceivedMessage

from ment_api.models.location_feed_post import NewsFeedPost, Source
from ment_api.models.media_post_generator_response import (
    MediaGeneratedPostResponse,
)
from ment_api.models.verification_state import VerificationState
from ment_api.persistence import mongo
from ment_api.utils.bot_ids import bot_name_to_id
from ment_api.services.external_clients.cloud_flare_client import upload_image
from ment_api.services.external_clients.gemini_client import gemini_client
from ment_api.services.news_service import publish_check_fact

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """
You are a critical media analysis system. From up to 20 recent articles, identify ONE most fact-checkable claim set (with strong, verifiable statements) and produce a single concise Georgian post.

Output must be JSON matching the schema with external_id, title, content, big_image_url.
Focus on high-impact, checkable claims (numbers, quotes, concrete assertions) and write content suitable for fact checking.
"""


def _build_prompt(articles: List[dict]) -> str:
    return (
        "Select one most fact-checkable item from these articles and produce a single post.\n\n"
        + "\n".join(
            [
                f"[{i + 1}] external_id={a['external_id']} | source={a['source']} | title={a['title']}\ncontent={a['content'][:500]}\nimage={a.get('big_image_url', '')}"
                for i, a in enumerate(articles)
            ]
        )
    )


async def _fetch_and_upload_image(url: str, fallback_name: str):
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(url, timeout=20.0, follow_redirects=True)
            r.raise_for_status()
            content_type = r.headers.get("content-type", "image/jpeg").split(";")[0]
            ext = ".jpg"
            if "png" in content_type:
                ext = ".png"
            elif "webp" in content_type:
                ext = ".webp"
            elif "jpeg" in content_type or "jpg" in content_type:
                ext = ".jpg"
            elif "gif" in content_type:
                ext = ".gif"
            destination_file_name = f"{fallback_name}{ext}"
            image_with_dims = await upload_image(
                r.content, destination_file_name, content_type
            )
            return image_with_dims.model_dump()
    except Exception as e:
        logger.warning(f"Failed to fetch/upload image from {url}: {e}")
        return None


async def _query_recent_articles(limit: int = 20):
    twenty_four_hours_ago = datetime.now(timezone.utc) - timedelta(hours=24)
    pipeline = [
        {
            "$match": {
                "created_at": {"$gte": twenty_four_hours_ago},
                "source": {"$in": list(bot_name_to_id().keys())},
                "$or": [
                    {"used_by_post_generator": {"$exists": False}},
                    {"used_by_post_generator": False},
                ],
            }
        },
        {"$sort": {"created_at": -1}},
        {"$limit": limit},
        {
            "$project": {
                "_id": 0,
                "external_id": 1,
                "title": 1,
                "content": 1,
                "details_url": 1,
                "big_image_url": 1,
                "source": 1,
                "created_at": 1,
            }
        },
    ]

    results = await mongo.external_articles.aggregate(pipeline)
    return list(results)


async def _generate_post(articles: List[dict]) -> MediaGeneratedPostResponse | None:
    user_prompt = _build_prompt(articles)
    response = await gemini_client.aio.models.generate_content(
        model="gemini-2.5-flash-lite",
        config={
            "system_instruction": SYSTEM_PROMPT,
            "response_mime_type": "application/json",
            "response_schema": MediaGeneratedPostResponse,
            "temperature": 0.1,
            "max_output_tokens": 8000,
        },
        contents=[user_prompt],
    )
    if not response or not response.parsed:
        logger.error(
            "Gemini did not return a parsable response for media post generation"
        )
        return None
    return response.parsed


async def process_media_post_generator_callback(message: ReceivedMessage):
    logger.info(
        f"Processing media post generator callback for {message.message.message_id}"
    )
    start = time.time()

    try:
        articles = await _query_recent_articles(limit=20)
        if not articles:
            logger.info("No recent external articles found for media post generation")
            return

        gen = await _generate_post(articles)
        if not gen or not gen.post:
            logger.info("No post generated from Gemini")
            return

        selected = gen.post

        image_gallery = []
        if selected.big_image_url:
            uploaded = await _fetch_and_upload_image(
                selected.big_image_url, f"media_post_{int(time.time())}"
            )
            if uploaded:
                image_gallery = [uploaded]

        # Build sources from the matched external article
        matched_article = next(
            (a for a in articles if a.get("external_id") == selected.external_id),
            None,
        )
        sources: list[Source] = []
        if matched_article:
            sources.append(
                Source(
                    title=matched_article.get("title", selected.title),
                    uri=matched_article.get("details_url", ""),
                )
            )

        # Choose assignee based on source
        assignee_user_id = bot_name_to_id().get(
            matched_article.get("source", ""), "089d915f-d75c-4eed-a4e1-ab88a4e0f42f"
        )

        # Create verification as a post that can be fact checked
        verification_doc = NewsFeedPost(
            title=selected.title,
            text_content=selected.content,
            text_summary=selected.content,
            text_content_in_english=None,
            sources=sources,
            assignee_user_id=assignee_user_id,
            feed_id="67bb256786841cb3e7074bcd",
            is_public=True,
            last_modified_date=datetime.now(timezone.utc),
            state=VerificationState.READY_FOR_USE,
            is_generated_news=False,
            government_summary=None,
            opposition_summary=None,
            neutral_summary=None,
            image_gallery_with_dims=image_gallery,
        )

        # Insert into verifications
        doc = verification_doc.model_dump(by_alias=True, exclude_none=True)
        insert_result = await mongo.verifications.insert_one(doc)
        verification_id = insert_result.inserted_id
        logger.info(f"Created verification for generated media post: {verification_id}")

        # Publish fact-check for this verification
        await publish_check_fact([verification_id])

        # Invalidate used articles
        ext_ids = [a["external_id"] for a in articles]
        await mongo.external_articles.update_many(
            {"external_id": {"$in": ext_ids}},
            {"$set": {"used_by_post_generator": True}},
        )

        logger.info(
            f"Media post generation completed in {time.time() - start:.2f}s using {len(articles)} articles"
        )
    except Exception:
        logger.error("Error in media post generator worker", exc_info=True)
