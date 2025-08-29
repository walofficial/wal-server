import asyncio
import logging
from io import BytesIO
from typing import List, Optional, Tuple

import aiohttp
import PIL.Image
from google.genai.types import GenerateContentConfig
from langfuse import observe
from ment_api.services.external_clients.langfuse_client import (
    langfuse,
)
from ment_api.services.external_clients.gemini_client import gemini_client

logger = logging.getLogger(__name__)


async def _download_single_image(
    session: aiohttp.ClientSession, url: str
) -> Optional[PIL.Image.Image]:
    """Helper function to download a single image."""
    try:
        async with session.get(url, timeout=10) as response:
            if response.status != 200:
                logger.error(f"Failed to download image from {url}: {response.status}")
                return None
            image_data = await response.read()
            try:
                return PIL.Image.open(BytesIO(image_data))
            except Exception as e:
                logger.error(f"Failed to open image data from {url}: {e}")
                return None
    except Exception as e:
        logger.error(f"Error downloading image from {url}: {e}")
        return None


@observe(as_type="generation")
async def download_and_analyze_images(
    image_urls: List[str],
) -> List[Tuple[str, Optional[str]]]:
    """
    Download and analyze multiple images from URLs.

    Args:
        image_urls: List of image URLs to download and analyze

    Returns:
        List of tuples containing (image_url, description) pairs.
        If an image couldn't be downloaded or analyzed, the description will be None.
    """
    results = []

    if not image_urls:
        return results

    async with aiohttp.ClientSession() as session:
        # Download images concurrently
        download_tasks = [_download_single_image(session, url) for url in image_urls]
        downloaded_images = await asyncio.gather(
            *download_tasks, return_exceptions=True
        )

    # Prepare for analysis
    system_instruction = """
    You are a helpful assistant that analyzes images and provides a detailed description of each one.
    """
    prompt = """
    Analyze the following images and provide a detailed description of each one.
    """

    config = GenerateContentConfig(
        system_instruction=system_instruction,
        response_mime_type="text/plain",
        temperature=0.1,
    )

    langfuse.update_current_generation(
        input=[prompt, *downloaded_images],
        model="gemini-2.5-flash",
        metadata={
            "image_count": len(downloaded_images),
        },
    )

    response = await gemini_client.aio.models.generate_content(
        model="gemini-2.5-flash",
        config=config,
        contents=[prompt, *downloaded_images],
    )

    langfuse.update_current_generation(
        usage_details={
            "input": response.usage_metadata.prompt_token_count,
            "output": response.usage_metadata.candidates_token_count,
            "cache_read_input_tokens": response.usage_metadata.cached_content_token_count,
        },
    )

    return response.text


async def get_image_descriptions_for_fact_checking(image_urls: List[str]) -> List[str]:
    """
    Create a list of image descriptions for fact checking.

    Args:
        image_urls: List of image URLs to analyze

    Returns:
        A list of image descriptions to include in a fact-checking request
    """
    if not image_urls:
        return []

    # Use the improved method for multi-image analysis
    analyzed_images = await download_and_analyze_images(image_urls)

    return analyzed_images
