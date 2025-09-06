import asyncio
import io
import logging
import time
from datetime import datetime, timezone
from typing import List, Optional

import aiohttp
from bs4 import BeautifulSoup
from bson import ObjectId
from google.genai.types import GenerateContentConfig, Part, ThinkingConfig
from langfuse import observe
from pydantic import BaseModel, Field
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    retry_if_result,
    stop_after_attempt,
    wait_random_exponential,
)

from ment_api.configurations.config import settings
from ment_api.events.social_media_scrape_event import SocialMediaScrapeEvent
from ment_api.models.location_feed_post import (
    FactCheckStatus,
    SocialMediaScrapeDetails,
    SocialMediaScrapeStatus,
)
from ment_api.persistence import mongo
from ment_api.services.external_clients.cloud_flare_client import upload_image
from ment_api.services.external_clients.gemini_client import gemini_client
from ment_api.services.external_clients.langfuse_client import (
    langfuse,
)
from ment_api.services.external_clients.scrape_do_client import get_scrape_do_client
from ment_api.services.news_service import publish_check_fact
from ment_api.services.notification_service import send_notification
from ment_api.services.pub_sub_service import publish_message

logger = logging.getLogger(__name__)

# Define patterns for social media URLs
SOCIAL_MEDIA_PATTERNS = {
    "facebook": r"(https?://(?:www\.)?facebook\.com/(?:share/p/[\w-]+/?|[\w.]+|[\w./?=&%-]+))",
    "linkedin": r"(https?://(?:www\.)?linkedin\.com/[\w./?=&%-]+)",
    # Add more platforms as needed
}

# Maximum file size in bytes (300KB)
MAX_FILE_SIZE = 100 * 1024


def compress_image(image_data: bytes, max_size: int = MAX_FILE_SIZE) -> bytes:
    """
    Compress an image to be under the specified maximum size

    Args:
        image_data: The original image data as bytes
        max_size: Maximum file size in bytes (default: 300KB)

    Returns:
        Compressed image data as bytes
    """
    try:
        from PIL import Image

        # Open the image
        image_stream = io.BytesIO(image_data)
        img = Image.open(image_stream)

        # Convert to RGB if necessary (for JPEG compression)
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGB")

        # Start with high quality and reduce until we're under the size limit
        quality = 95
        min_quality = 20

        while quality >= min_quality:
            output_stream = io.BytesIO()
            img.save(output_stream, format="JPEG", quality=quality, optimize=True)
            compressed_data = output_stream.getvalue()

            if len(compressed_data) <= max_size:
                logger.info(
                    f"Image compressed to {len(compressed_data)} bytes with quality {quality}"
                )
                return compressed_data

            quality -= 5

        # If still too large, try reducing dimensions
        original_width, original_height = img.size
        scale_factor = 0.9

        while scale_factor > 0.3:  # Don't scale down too much
            new_width = int(original_width * scale_factor)
            new_height = int(original_height * scale_factor)
            resized_img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)

            # Try with moderate quality
            output_stream = io.BytesIO()
            resized_img.save(output_stream, format="JPEG", quality=75, optimize=True)
            compressed_data = output_stream.getvalue()

            if len(compressed_data) <= max_size:
                logger.info(
                    f"Image resized to {new_width}x{new_height} and compressed to {len(compressed_data)} bytes"
                )
                return compressed_data

            scale_factor -= 0.1

        # Last resort: use the smallest version we created
        logger.warning(
            f"Could not compress image below {max_size} bytes, using best effort compression"
        )
        return compressed_data

    except ImportError:
        logger.warning("Pillow library is not installed. Cannot compress image.")
        return image_data
    except Exception as e:
        logger.error(f"Error compressing image: {e}")
        return image_data


class SocialMediaParsedContent(BaseModel):
    """Structured representation of parsed social media content"""

    is_broken_screenshot: bool = Field(
        False, description="Whether the screenshot is broken"
    )
    is_private_post: bool = Field(False, description="Whether the post is private")
    text_content: str = Field(
        description="The main text content of the post, should be just text, not html or something else. you can also remove emojis."
    )
    post_id: Optional[str] = Field(None, description="The ID of the post if available")
    platform: str = Field(description="The platform (e.g., 'facebook', 'linkedin')")
    author_name: Optional[str] = Field(
        None, description="The name of the post author if available"
    )
    author_profile_image: Optional[str] = Field(
        None, description="The profile image of the post author if available"
    )
    post_date: Optional[str] = Field(None, description="The posting date if available")
    has_images: bool = Field(False, description="Whether the post contains images")
    image_count: int = Field(0, description="Number of images in the post")
    image_urls: List[str] = Field(
        [], description="List of image URLs attached to the post"
    )


@observe(as_type="generation")
async def parse_social_media_content(
    markdown_content: str, platform: str, url: str, screenshot_data: bytes
) -> SocialMediaParsedContent:
    """
    Use Gemini to parse scraped social media content into a structured format.
    Now uses @observe decorator for automatic tracing.

    Args:
        markdown_content: The raw scraped content in markdown format
        platform: The social media platform
        url: The original URL
        screenshot_data: The screenshot of the facebook or other social network page
    Returns:
        Structured representation of the social media content
    """
    try:
        # Parse HTML content with BeautifulSoup
        soup = BeautifulSoup(markdown_content, "html.parser")

        # Find div with id containing mount_0
        mount_div = soup.find("div", id=lambda x: x and "mount_0" in x)

        # Extract content from mount div if found, otherwise use full content
        content_to_parse = str(mount_div) if mount_div else markdown_content

        system_instruction = """
        You are a social media content parser. Your task is to extract structured information from raw HTML/markdown
        content scraped from social media platforms. Extract as much information as possible including:
        - The main text content, do not return the HTML here or something, it should be pure facebook post content. If you can't find it return empty
        - Post ID (if present)
        - Author name
        - Post date
        - Whether images are present and how many
        - Image URLs attached to the post
        - Whether the post is private, might be from private group or page. Assume that it is not private if you see the post content even though there is some overlays around it.
        - Author profile image URL (if present)
        - If screenshot is valid and not broken. broken means that it has nothing on it like some logos and stuff and unrelated text for example.
        Return the information in a clean, structured JSON format without any HTML tags or markdown formatting.
        Focus on extracting factual information that would be useful for fact-checking.
        """

        prompt = f"""
        Parse the following {platform} content scraped from {url}:
        
        ```
        {content_to_parse}  # Limit content length to avoid token issues
        ```
        
        Extract the key information in a structured format. Include the main text content,
        post metadata, and any engagement metrics you can find.
        """

        langfuse.update_current_generation(
            input=[prompt],
            model="gemini-2.5-flash",
            metadata={
                "platform": platform,
                "content_length": len(markdown_content),
                "url": url,
            },
        )

        logger.info(f"Sending scraped {platform} content to Gemini for parsing")
        response = await gemini_client.aio.models.generate_content(
            model="gemini-2.5-flash",
            config=GenerateContentConfig(
                system_instruction=system_instruction,
                response_mime_type="application/json",
                response_schema=SocialMediaParsedContent.model_json_schema(),
                temperature=0.1,
                thinking_config=ThinkingConfig(
                    thinking_budget=2000,
                ),
            ),
            contents=[
                prompt,
                Part.from_bytes(
                    data=screenshot_data,
                    mime_type="image/jpeg",
                ),
            ],
        )

        langfuse.update_current_generation(
            usage_details={
                "input": response.usage_metadata.prompt_token_count,
                "output": response.usage_metadata.candidates_token_count,
                "cache_read_input_tokens": response.usage_metadata.cached_content_token_count,
                # "total": int,  # if not set, it is derived from input + cache_read_input_tokens + output
            },
        )

        parsed_content = SocialMediaParsedContent.model_validate_json(response.text)
        if "html" in parsed_content.text_content.lower():
            raise Exception(
                "HTML content found in the text content, should return empty text content"
            )

        logger.info(
            f"Successfully parsed {platform} content: {parsed_content.model_dump()}"
        )

        return parsed_content

    except Exception as e:
        logger.error(f"Error parsing {platform} content with Gemini: {e}")

        raise e


@observe()
async def scrape_social_media(
    verification_id: ObjectId, social_url: str, platform: str
) -> None:
    """
    Scrape social media content and update the verification document.
    Now uses @observe decorator for automatic tracing.
    """

    # Fetch verification to get user_id
    verification = await mongo.verifications.find_one_by_id(verification_id)
    user_id = verification.get("assignee_user_id") if verification else None

    # Set trace input using v3 pattern with safe context handling

    # Set session_id to verification_id and user_id for session grouping

    try:
        await mongo.verifications.update_one(
            {"_id": verification_id},
            {
                "$set": {
                    "social_media_scrape_status": SocialMediaScrapeStatus.PROCESSING,
                }
            },
        )

        def _should_retry_broken_screenshot(result: tuple) -> bool:
            try:
                parsed, shot, _ = result
                is_broken = getattr(parsed, "is_broken_screenshot", False)
                no_image = not bool(shot)
                return bool(is_broken or no_image)
            except Exception as pred_err:
                logger.warning(f"Retry predicate error: {pred_err}")
                return False

        @retry(
            retry=retry_if_exception_type()
            | retry_if_result(_should_retry_broken_screenshot),
            wait=wait_random_exponential(multiplier=1, max=3),
            stop=stop_after_attempt(3),
            before_sleep=before_sleep_log(logger, logging.WARNING),
        )
        async def _scrape_and_parse_once() -> tuple[
            SocialMediaParsedContent, Optional[bytes], str
        ]:
            scrape_start_time = time.time()

            with langfuse.start_as_current_span(
                name="scrape_with_screenshot"
            ) as scrape_span:
                scrape_span.update(
                    input={
                        "url": social_url,
                        "platform": platform,
                        "full_page": True,
                        "wait_until": "networkidle2",
                    },
                    user_id=user_id,
                )

                async with get_scrape_do_client() as client:
                    scrape_result = await client.scrape_with_screenshot(
                        social_url, full_page=True, wait_until="networkidle2"
                    )

                scrape_duration = (time.time() - scrape_start_time) * 1000

                if not scrape_result or not scrape_result.get("content"):
                    scrape_span.update(
                        output={"success": False, "error": "No content returned"},
                        metadata={"duration_ms": scrape_duration},
                    )
                    raise Exception("Failed to scrape social media content")

                scrape_span.update(
                    output={
                        "success": True,
                        "has_content": bool(scrape_result.get("content")),
                        "content_length": len(scrape_result.get("content", "")),
                        "has_screenshot": bool(scrape_result.get("screenshot_data")),
                        "screenshot_size_bytes": len(
                            scrape_result.get("screenshot_data", b"")
                        ),
                    },
                    metadata={"duration_ms": scrape_duration},
                )

            markdown = scrape_result["content"]
            shot_bytes = scrape_result.get("screenshot_data")
            parsed = await parse_social_media_content(
                markdown, platform, social_url, shot_bytes
            )
            return parsed, shot_bytes, markdown

        (
            parsed_content,
            screenshot_data,
            markdown_content,
        ) = await _scrape_and_parse_once()

        if parsed_content.is_broken_screenshot:
            raise Exception("Broken screenshot detected after all retry attempts")

        # Save the screenshot to Google Cloud Storage
        full_page_screenshot = None

        if screenshot_data:
            with langfuse.start_as_current_span(
                name="process_screenshot"
            ) as screenshot_span:
                screenshot_span.update(
                    input={
                        "screenshot_size_bytes": len(screenshot_data),
                        "verification_id": str(verification_id),
                    },
                    user_id=user_id,
                )

                # Generate a unique filename for the screenshot
                timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
                filename = f"screenshot_{verification_id}_{timestamp}.jpg"

                logger.info(
                    f"Uploading compressed screenshot to Cloud Storage with filename: {filename}"
                )

                full_page_screenshot = await upload_image(
                    file=compress_image(screenshot_data),
                    destination_file_name=filename,
                    content_type="image/jpeg",  # Changed to JPEG content type
                )
                logger.info(f"Screenshot uploaded to: {full_page_screenshot.url}")

                screenshot_span.update(
                    output={
                        "success": bool(full_page_screenshot.url),
                        "screenshot_url": full_page_screenshot.url,
                        "width": full_page_screenshot.width,
                        "height": full_page_screenshot.height,
                    }
                )

        if full_page_screenshot:
            logger.info(f"Screenshot URL: {full_page_screenshot.url}")

        # Helper to download an image URL to bytes
        async def _download_image_bytes(url: str) -> Optional[bytes]:
            try:
                timeout = aiohttp.ClientTimeout(total=20)
                headers = {
                    "User-Agent": (
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/126.0.0.0 Safari/537.36"
                    ),
                    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                    "Referer": "https://www.facebook.com/",
                }
                async with aiohttp.ClientSession(
                    timeout=timeout, headers=headers
                ) as session:
                    async with session.get(url, allow_redirects=True) as resp:
                        if resp.status != 200:
                            logger.warning(
                                f"Failed to download image {url}: {resp.status}"
                            )
                            return None
                        return await resp.read()
            except Exception as e:
                logger.warning(f"Error downloading image {url}: {e}")
                return None

        # Download and upload post images and author profile image in parallel
        # with a retry that re-scrapes the page if any image fails.
        async def _download_and_upload(idx: int, url: str, ts: str) -> Optional[str]:
            data = await _download_image_bytes(url)
            if not data:
                return None
            compressed = compress_image(data)
            try:
                uploaded = await upload_image(
                    file=compressed,
                    destination_file_name=f"post_image_{verification_id}_{ts}_{idx}.jpg",
                    content_type="image/jpeg",
                )
                return uploaded.url
            except Exception as e:
                logger.warning(f"Upload failed for image {url}: {e}")
                return None

        async def _download_and_upload_author(url: str, ts: str) -> Optional[str]:
            data = await _download_image_bytes(url)
            if not data:
                return None
            try:
                author_compressed = compress_image(data)
                author_uploaded = await upload_image(
                    file=author_compressed,
                    destination_file_name=f"author_profile_{verification_id}_{ts}.jpg",
                    content_type="image/jpeg",
                )
                return author_uploaded.url
            except Exception as e:
                logger.warning(f"Failed to upload author profile image: {e}")
                return None

        max_image_attempts = 2
        image_attempt = 1
        image_urls_uploaded: List[str] = []
        uploaded_author_profile_url: Optional[str] = None
        while image_attempt <= max_image_attempts:
            ts = datetime.now().strftime("%Y%m%d%H%M%S")

            post_tasks = [
                _download_and_upload(i, url, ts)
                for i, url in enumerate(parsed_content.image_urls)
            ]

            author_task = (
                _download_and_upload_author(parsed_content.author_profile_image, ts)
                if parsed_content.author_profile_image
                else None
            )

            all_tasks = post_tasks + ([author_task] if author_task else [])

            results: List[Optional[str]] = (
                await asyncio.gather(*all_tasks) if all_tasks else []
            )

            # Split results
            post_results = results[: len(post_tasks)] if post_tasks else []
            author_result = results[-1] if author_task is not None and results else None

            image_urls_uploaded = [u for u in post_results if u]
            uploaded_author_profile_url = author_result

            any_failures = False
            if post_tasks and len(image_urls_uploaded) != len(post_tasks):
                any_failures = True
            if author_task is not None and not uploaded_author_profile_url:
                any_failures = True

            if not any_failures:
                break

            logger.warning(
                f"Image download/upload failure detected (attempt {image_attempt}/{max_image_attempts}). Re-scraping page to retry."
            )

            # Re-scrape and re-parse to refresh potentially expiring image URLs
            try:
                async with get_scrape_do_client() as client:
                    scrape_result = await client.scrape_with_screenshot(
                        social_url, full_page=True, wait_until="networkidle2"
                    )
                if not scrape_result or not scrape_result.get("content"):
                    raise Exception("No content in re-scrape result")
                markdown_content = scrape_result["content"]
                parsed_content = await parse_social_media_content(
                    markdown_content, platform, social_url, full_page_screenshot.url
                )
            except Exception as re_err:
                logger.warning(f"Re-scrape attempt failed: {re_err}")

            image_attempt += 1

        if post_tasks and len(image_urls_uploaded) != len(post_tasks):
            raise Exception("Failed to process all post images after retries")
        if parsed_content.author_profile_image and not uploaded_author_profile_url:
            raise Exception("Failed to process author profile image after retries")

        # Extract relevant information from the parsed content
        details = SocialMediaScrapeDetails(
            platform=platform,
            url=social_url,
            content=parsed_content.text_content,  # Use the cleaned text content
            post_date=datetime.now(timezone.utc),  # Use parsed date if available
            image_urls=image_urls_uploaded,  # Include the screenshot URL in the image_urls
            author_name=parsed_content.author_name,
            author_profile_image=uploaded_author_profile_url,
            screenshot={
                "url": full_page_screenshot.url,
                "width": full_page_screenshot.width,
                "height": full_page_screenshot.height,
            },  # Store ScreenshotInfo object
        ).model_dump()

        await mongo.verifications.update_one(
            {"_id": verification_id},
            {
                "$set": {
                    "social_media_scrape_details": details,
                    "social_media_scrape_status": SocialMediaScrapeStatus.COMPLETED,
                }
            },
        )

        asyncio.create_task(get_enhanced_screenshot(str(verification_id)))

        if parsed_content.is_private_post:
            await mongo.verifications.update_one(
                {"_id": verification_id},
                {"$set": {"fact_check_status": FactCheckStatus.FAILED}},
            )
            verification = await mongo.verifications.find_one_by_id(verification_id)
            await send_notification(
                verification.get("assignee_user_id"),
                "ფოსტი ვერ გადამოწმდა",
                "ლინკი არ არის საჯარო",
                data={
                    "type": "fact_check_completed",
                    "verificationId": str(verification_id),
                },
            )
            await mongo.verifications.update_one(
                {"_id": verification_id},
                {"$set": {"is_public": False}},
            )
        else:
            await publish_check_fact([verification_id])

    except Exception as e:
        logger.error(f"Error scraping social media: {e}")

        # Update trace with error output

        # Update status to FAILED
        try:
            await mongo.verifications.update_one(
                {"_id": verification_id},
                {
                    "$set": {
                        "social_media_scrape_status": SocialMediaScrapeStatus.FAILED,
                        "social_media_scrape_error": str(e),
                        "fact_check_status": FactCheckStatus.FAILED,
                    }
                },
            )

        except Exception as update_error:
            raise update_error


async def publish_social_media_scrape_request(verification_id: ObjectId) -> None:
    """
    Publish a request to scrape social media for a verification
    """

    try:
        event = SocialMediaScrapeEvent(verification_id=str(verification_id))
        data = event.model_dump_json().encode()

        await publish_message(
            settings.gcp_project_id,
            settings.pub_sub_social_media_scrape_topic_id,
            data,
            retry_timeout=60.0,
        )

        logger.info(
            f"Published social media scrape request for verification {verification_id}"
        )
    except Exception as e:
        logger.error(
            f"Error publishing social media scrape request: {e}", exc_info=True
        )


async def get_enhanced_screenshot(verification_id: str) -> None:
    async with get_scrape_do_client() as client:
        # Capture screenshot using ScrapeDoClient
        # Get verification data for additional context

        # Build screenshot URL
        screenshot_url = f"https://wal.ge/status/{verification_id}/facebook-mock?static=true"

        # Capture screenshot using ScrapeDoClient
        result = await client.scrape_with_screenshot(
            scrape_url=screenshot_url,
            full_page=False,
            wait_until="load",
            width=1920,
            height=1080,
            particularScreenShot="#static-view",
        )

        screenshot_data = result.get("screenshot_data")
       
        image_screenshot = await upload_image(
            file=compress_image(screenshot_data),
            destination_file_name=f"screenshot_enhanced_{verification_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}.jpg",
            content_type="image/jpeg",
        )

        if not screenshot_data:
            raise Exception("No screenshot data received")

        logger.info(
            f"Screenshot generated successfully for verification {verification_id}"
        )

        await mongo.verifications.update_one(
            {"_id": ObjectId(verification_id)},
            {
                "$set": {
                    "image_gallery_with_dims": [image_screenshot.model_dump()],
                }
            },
        )
