import asyncio
import logging
from io import BytesIO
from typing import List, Optional, TypeVar

import aiohttp
import PIL.Image
from google.genai import Client
from google.genai.types import BlockedReason, GenerateContentConfig, ThinkingConfig
from langfuse import observe
from tenacity import (
    before_sleep_log,
    retry,
    stop_after_attempt,
    wait_random_exponential,
)

from ment_api.configurations.config import settings
from ment_api.services.external_clients.langfuse_client import langfuse
from ment_api.services.external_clients.models.gemini_models import (
    FactCheckInputRequest,
    FactCheckInputResponse,
    NotificationGenerationRequest,
    NotificationGenerationResponse,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")
# Initialize Gemini client
gemini_client = Client(api_key=settings.gcp_genai_key)
gemini_client_vertex_ai = Client(
    vertexai=True, location="global", project=settings.gcp_project_id
)


class GeminiClient:
    def __init__(self):
        self.model_id = "gemini-2.5-flash"
        self.langfuse = langfuse

    @observe()
    async def download_image(self, url: str) -> Optional[PIL.Image.Image]:
        """
        Download an image from a URL and return it as a PIL Image object.
        Enhanced with comprehensive Langfuse observability.

        Args:
            url: URL of the image to download

        Returns:
            PIL Image object or None if download fails
        """
        # Set trace input using v3 pattern with enhanced metadata
        self.langfuse.update_current_trace(
            input={"url": url},
            metadata={
                "operation": "download_image",
                "url_domain": url.split("/")[2] if "://" in url else "unknown",
            },
        )

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=10) as response:
                    if response.status != 200:
                        error_msg = (
                            f"Failed to download image from {url}: {response.status}"
                        )
                        logger.error(
                            "Image download failed - HTTP error",
                            extra={
                                "json_fields": {
                                    "url": url,
                                    "status_code": response.status,
                                    "error": error_msg,
                                    "operation": "gemini_image_download_http_error",
                                },
                                "labels": {
                                    "component": "gemini_client",
                                    "severity": "medium",
                                },
                            },
                        )
                        self.langfuse.update_current_trace(
                            output=None,
                            metadata={
                                "error": error_msg,
                                "status_code": response.status,
                                "failure_reason": "http_error",
                            },
                        )
                        return None

                    image_data = await response.read()
                    try:
                        image = PIL.Image.open(BytesIO(image_data))
                        self.langfuse.update_current_trace(
                            output={
                                "success": True,
                                "image_size": image.size,
                                "data_size_bytes": len(image_data),
                            },
                            metadata={
                                "image_format": image.format,
                                "image_mode": image.mode,
                                "width": image.size[0],
                                "height": image.size[1],
                                "data_size_kb": round(len(image_data) / 1024, 2),
                            },
                        )
                        logger.debug(
                            "Image downloaded successfully",
                            extra={
                                "json_fields": {
                                    "url": url,
                                    "image_size": image.size,
                                    "format": image.format,
                                    "data_size_kb": round(len(image_data) / 1024, 2),
                                    "operation": "gemini_image_download_success",
                                },
                                "labels": {"component": "gemini_client"},
                            },
                        )
                        return image
                    except Exception as e:
                        error_msg = f"Failed to open image data: {e}"
                        logger.error(
                            "Image download failed - image processing error",
                            extra={
                                "json_fields": {
                                    "url": url,
                                    "error": str(e),
                                    "error_type": type(e).__name__,
                                    "data_size_bytes": len(image_data),
                                    "operation": "gemini_image_download_processing_error",
                                },
                                "labels": {
                                    "component": "gemini_client",
                                    "severity": "medium",
                                },
                            },
                        )
                        self.langfuse.update_current_trace(
                            output=None,
                            metadata={
                                "error": error_msg,
                                "failure_reason": "image_processing_error",
                                "data_size_bytes": len(image_data),
                            },
                        )
                        return None
        except Exception as e:
            error_msg = f"Error downloading image from {url}: {e}"
            logger.error(
                "Image download failed - network error",
                extra={
                    "json_fields": {
                        "url": url,
                        "error": str(e),
                        "error_type": type(e).__name__,
                        "operation": "gemini_image_download_network_error",
                    },
                    "labels": {"component": "gemini_client", "severity": "medium"},
                },
            )
            self.langfuse.update_current_trace(
                output=None,
                metadata={
                    "error": error_msg,
                    "failure_reason": "network_error",
                },
            )
            return None

    @observe()
    async def download_images(
        self, urls: List[str]
    ) -> List[tuple[str, Optional[PIL.Image.Image]]]:
        """
        Download multiple images from URLs in parallel.
        Now uses @observe decorator for automatic tracing.

        Args:
            urls: List of image URLs to download

        Returns:
            List of tuples containing (url, image) pairs
            If an image couldn't be downloaded, its value will be None
        """
        # Set trace input using v3 pattern
        self.langfuse.update_current_trace(
            input={"url_count": len(urls), "urls": urls},
            metadata={"operation": "download_images"},
        )

        if not urls:
            self.langfuse.update_current_trace(output=[])
            return []

        # Create download tasks for all URLs
        async def download_single(url):
            image = await self.download_image(url)
            return (url, image)

        tasks = [download_single(url) for url in urls]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Filter out exceptions and return valid results
        valid_results = []
        successful_downloads = 0
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"Error downloading image from {urls[i]}: {result}")
                valid_results.append((urls[i], None))
            else:
                valid_results.append(result)
                if result[1] is not None:
                    successful_downloads += 1

        self.langfuse.update_current_trace(
            output={
                "total_urls": len(urls),
                "successful_downloads": successful_downloads,
            },
            metadata={"success_rate": successful_downloads / len(urls) if urls else 0},
        )
        return valid_results

    @observe()
    @retry(
        wait=wait_random_exponential(multiplier=1, max=3),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
        stop=stop_after_attempt(4),
    )
    async def analyze_images(
        self, images: List[PIL.Image.Image], analysis_prompt: str = None
    ) -> Optional[str]:
        """
        Analyze multiple images in a single call using Gemini.
        Now uses @observe decorator for automatic tracing with enhanced generation observability.

        Args:
            images: List of PIL Image objects to analyze
            analysis_prompt: Optional prompt to guide the image analysis

        Returns:
            String with analysis of all images or None if analysis failed
        """
        if not images:
            error_msg = "Cannot analyze images: no images provided"
            logger.error(
                "Gemini image analysis failed - no images provided",
                extra={
                    "json_fields": {
                        "error": error_msg,
                        "operation": "gemini_analyze_images",
                        "model": self.model_id,
                    },
                    "labels": {"component": "gemini_client", "severity": "medium"},
                },
            )
            self.langfuse.update_current_trace(
                input={"image_count": 0, "prompt": analysis_prompt},
                output=None,
                metadata={"error": error_msg},
            )
            return None

        if not analysis_prompt:
            analysis_prompt = "Describe these images concisely. For each image, focus on key visible elements that might be relevant for fact-checking."

        # Set trace input using v3 pattern
        self.langfuse.update_current_trace(
            input={
                "image_count": len(images),
                "prompt": analysis_prompt,
                "model": self.model_id,
            },
            metadata={"operation": "analyze_images"},
        )

        try:
            # Create generation span using v3 pattern with enhanced observability
            with self.langfuse.start_as_current_generation(
                name="gemini_analyze_images", model=self.model_id
            ) as gen:
                # Build content list with all images followed by the prompt
                contents = images + [analysis_prompt]

                # Enhanced input tracking for generation
                gen.update(
                    input={
                        "prompt": analysis_prompt,
                        "image_count": len(images),
                        "model_config": {
                            "model": self.model_id,
                            "temperature": None,  # Default temperature
                            "max_tokens": None,  # Default max tokens
                        },
                    },
                    metadata={
                        "image_count": len(images),
                        "prompt_length": len(analysis_prompt),
                        "operation_type": "image_analysis",
                        "fact_check_context": True,
                    },
                )

                logger.info(
                    "Starting Gemini image analysis generation",
                    extra={
                        "json_fields": {
                            "image_count": len(images),
                            "prompt_length": len(analysis_prompt),
                            "model": self.model_id,
                            "operation": "gemini_analyze_images_start",
                        },
                        "labels": {"component": "gemini_client", "phase": "generation"},
                    },
                )

                response = await gemini_client.aio.models.generate_content(
                    model=self.model_id, contents=contents
                )

                if response and response.text:
                    result = response.text.strip()

                    # Enhanced output tracking with comprehensive usage details
                    gen.update(
                        output={
                            "analysis": result,
                            "response_length": len(result),
                            "success": True,
                        },
                        usage={
                            "input": response.usage_metadata.prompt_token_count,
                            "output": response.usage_metadata.candidates_token_count,
                            "total": response.usage_metadata.total_token_count,
                        },
                        metadata={
                            "response_length": len(result),
                            "completion_reason": "success",
                            "model_version": self.model_id,
                            "cache_read_input_tokens": response.usage_metadata.cached_content_token_count,
                        },
                    )

                    logger.info(
                        "Gemini image analysis generation completed successfully",
                        extra={
                            "json_fields": {
                                "response_length": len(result),
                                "input_tokens": response.usage_metadata.prompt_token_count,
                                "output_tokens": response.usage_metadata.candidates_token_count,
                                "total_tokens": response.usage_metadata.total_token_count,
                                "operation": "gemini_analyze_images_success",
                            },
                            "labels": {
                                "component": "gemini_client",
                                "phase": "generation",
                            },
                        },
                    )

                    return result
                else:
                    error_msg = "No response from Gemini"
                    logger.error(
                        "Gemini image analysis failed - empty response",
                        extra={
                            "json_fields": {
                                "error": error_msg,
                                "model": self.model_id,
                                "operation": "gemini_analyze_images_empty_response",
                            },
                            "labels": {
                                "component": "gemini_client",
                                "severity": "high",
                            },
                        },
                    )
                    gen.update(
                        output=None,
                        metadata={
                            "error": error_msg,
                            "completion_reason": "empty_response",
                        },
                    )
                    return None

        except Exception as e:
            error_msg = f"Error analyzing images with Gemini: {e}"
            logger.error(
                "Gemini image analysis generation failed with exception",
                extra={
                    "json_fields": {
                        "error": str(e),
                        "error_type": type(e).__name__,
                        "model": self.model_id,
                        "operation": "gemini_analyze_images_exception",
                    },
                    "labels": {"component": "gemini_client", "severity": "high"},
                },
            )
            raise

    async def download_and_analyze_images(
        self, image_urls: List[str], analysis_prompt: str = None
    ) -> List[Optional[str]]:
        """
        Download and analyze multiple images from URLs.

        Args:
            image_urls: List of image URLs to download and analyze
            analysis_prompt: Optional prompt to guide the image analysis

        Returns:
            List of descriptions corresponding to the input image URLs.
            If an image couldn't be downloaded or analyzed, its corresponding
            description in the list will be None.
        """
        with self.langfuse.start_as_current_span(
            name="download_and_analyze_images"
        ) as span:
            span.update(
                input={
                    "image_url_count": len(image_urls),
                    "analysis_prompt": analysis_prompt,
                    "urls": image_urls,
                }
            )

            if not image_urls:
                span.update(output=[])
                return []

            # First, download all images
            downloaded_images = await self.download_images(image_urls)

            # Collect successfully downloaded images
            valid_images = []
            url_to_index = {}  # Maps URL to its index in the valid_images list

            for i, (url, image) in enumerate(downloaded_images):
                if image:
                    valid_images.append(image)
                    # Store the index *within the valid_images list* for later mapping
                    url_to_index[url] = len(valid_images) - 1

            if not valid_images:
                logger.warning("No valid images were downloaded for analysis")
                result = [None] * len(image_urls)
                span.update(
                    output={"analyzed_count": 0, "total_requested": len(image_urls)},
                    metadata={"warning": "No valid images downloaded"},
                )
                return result

            # Analyze all valid images in a single call
            if not analysis_prompt:
                analysis_prompt = "Describe each image separately in a numbered list. For each image, focus on key visible elements that might be relevant for fact-checking. Format the response as:\n1. [First image description]\n2. [Second image description]..."

            analysis_result = await self.analyze_images(valid_images, analysis_prompt)

            # Process the analysis result
            if not analysis_result:
                # Return a list of None if analysis failed
                result = [None] * len(image_urls)
                span.update(
                    output={"analyzed_count": 0, "total_requested": len(image_urls)},
                    metadata={"error": "Analysis failed"},
                )
                return result

            # Try to parse numbered list responses
            image_descriptions = []
            try:
                # Split by lines and look for numbered items
                lines = analysis_result.split("\n")
                current_description = ""
                current_number = 1
                processed_indices = set()  # Keep track of which numbered items we found

                for line in lines:
                    stripped = line.strip()
                    # Match pattern like "1.", "01.", " 1. ", etc.
                    import re

                    match = re.match(r"^(\d+)\.\s*(.*)", stripped)
                    if match:
                        number = int(match.group(1))
                        description_part = match.group(2)

                        # If this is the start of a new numbered item
                        if number == current_number and number not in processed_indices:
                            if current_number > 1:
                                # Add the previously accumulated description
                                image_descriptions.append(current_description.strip())
                            # Start the new description
                            current_description = description_part
                            processed_indices.add(current_number)
                            current_number += 1
                        # If it continues the current description (might be indented)
                        elif number == current_number - 1 and current_number > 1:
                            current_description += " " + stripped
                        # Handle cases where numbering might be off but content belongs to the current item
                        elif current_number > 1:
                            current_description += " " + stripped

                    # If it's not a numbered line, append to the current description
                    elif current_description:
                        current_description += " " + stripped
                    # Handle case where the first line is not numbered
                    elif not image_descriptions and not processed_indices:
                        current_description += " " + stripped

                # Add the last description if any
                if current_description:
                    image_descriptions.append(current_description.strip())

                # If parsing failed or description count mismatch, log warning.
                # We'll still try to map what we got.
                if not image_descriptions or len(image_descriptions) != len(
                    valid_images
                ):
                    logger.warning(
                        f"Parsed {len(image_descriptions)} descriptions, but expected {len(valid_images)}. "
                        f"Response format might be unexpected: '{analysis_result[:100]}...'"
                    )
                    # Fallback: if only one description was parsed, assume it applies to all
                    if len(image_descriptions) == 1 and len(valid_images) > 1:
                        logger.warning(
                            "Applying the single parsed description to all valid images."
                        )
                        image_descriptions = [image_descriptions[0]] * len(valid_images)
                    # Otherwise, mapping might be incomplete

            except Exception as e:
                logger.error(
                    f"Error parsing image descriptions: {e}. Raw result: '{analysis_result[:100]}...'"
                )
                # Fallback: Use the raw analysis result for all valid images if parsing fails badly
                image_descriptions = [analysis_result] * len(valid_images)

            # Map the descriptions back to the original order of URLs
            final_descriptions = [None] * len(image_urls)
            successful_analyses = 0

            for i, (url, image) in enumerate(downloaded_images):
                # Check if the image was successfully downloaded AND exists in our url_to_index map
                if image and url in url_to_index:
                    desc_index = url_to_index[
                        url
                    ]  # Index within valid_images/image_descriptions
                    # Check if we have a description for this index
                    if desc_index < len(image_descriptions):
                        final_descriptions[i] = image_descriptions[desc_index]
                        successful_analyses += 1
                    else:
                        # This case might happen if parsing yielded fewer descriptions than valid images
                        logger.warning(
                            f"No parsed description found for image {i + 1} ({url}) at description index {desc_index}."
                        )
                        final_descriptions[i] = None  # Explicitly set to None
                else:
                    # Image failed to download, description remains None
                    final_descriptions[i] = None

            span.update(
                output={
                    "analyzed_count": successful_analyses,
                    "total_requested": len(image_urls),
                    "downloaded_count": len(
                        [img for _, img in downloaded_images if img]
                    ),
                },
                metadata={
                    "analysis_success_rate": (
                        successful_analyses / len(image_urls) if image_urls else 0
                    )
                },
            )
            return final_descriptions

    @observe()
    @retry(
        wait=wait_random_exponential(multiplier=1, max=4),
        before_sleep=before_sleep_log(logger, logging.ERROR),
        reraise=True,
        stop=stop_after_attempt(4),
    )
    async def get_fact_check_input(
        self, request: FactCheckInputRequest
    ) -> Optional[FactCheckInputResponse]:
        """
        Analyze a statement and any images to create an enhanced statement for fact-checking.
        Enhanced with comprehensive Langfuse observability and nested span tracking.

        This method analyzes the statement and images with Gemini and generates a
        comprehensive statement that can be passed to the Jina fact check service.

        Args:
            request: The FactCheckRequest containing the statement and images to analyze

        Returns:
            FactCheckInputResponse object that incorporates both text and image analysis
            or None if the analysis failed
        """
        # Create nested span for the entire fact check input analysis
        with self.langfuse.start_as_current_span(
            name="gemini_fact_check_input_analysis"
        ) as analysis_span:
            analysis_span.update(
                input={
                    "statement": request.statement,
                    "image_urls": request.image_urls or [],
                    "is_social_media": request.is_social_media,
                    "model": "gemini-2.5-flash",
                },
                metadata={
                    "operation": "fact_check_input_analysis",
                    "statement_length": len(request.statement),
                    "image_count": len(request.image_urls) if request.image_urls else 0,
                },
            )

            logger.info(
                "Starting Gemini fact check input analysis",
                extra={
                    "json_fields": {
                        "statement_length": len(request.statement),
                        "image_count": len(request.image_urls)
                        if request.image_urls
                        else 0,
                        "is_social_media": request.is_social_media,
                        "operation": "gemini_fact_check_input_start",
                    },
                    "labels": {
                        "component": "gemini_client",
                        "phase": "fact_check_input",
                    },
                },
            )

            # Create the system prompt
            system_prompt = """You are a fact-checking assistant. Your task is to analyze the provided statement
and images (if any) and create a comprehensive text description that captures all claims 
that need to be fact-checked. Focus on extracting key factual claims and summarizing 
any visual evidence from images in a way that can be verified by a text-only fact-checking system."""

            # Process images if they are included in the request - nested span for image processing
            image_objects = []
            if request.image_urls:
                with self.langfuse.start_as_current_span(
                    name="image_download_processing"
                ) as img_span:
                    img_span.update(
                        input={
                            "image_urls": request.image_urls,
                            "image_count": len(request.image_urls),
                        },
                        metadata={"operation": "image_download_processing"},
                    )

                    logger.info(
                        "Processing images for fact checking",
                        extra={
                            "json_fields": {
                                "image_count": len(request.image_urls),
                                "operation": "gemini_image_download_start",
                            },
                            "labels": {
                                "component": "gemini_client",
                                "phase": "image_processing",
                            },
                        },
                    )

                    successful_downloads = 0
                    for url in request.image_urls:
                        image = await self.download_image(url)
                        if image:
                            image_objects.append(image)
                            successful_downloads += 1
                            logger.debug(
                                "Successfully downloaded image",
                                extra={
                                    "json_fields": {
                                        "image_url": url,
                                        "operation": "gemini_image_download_success",
                                    },
                                    "labels": {"component": "gemini_client"},
                                },
                            )
                        else:
                            logger.warning(
                                "Failed to download image",
                                extra={
                                    "json_fields": {
                                        "image_url": url,
                                        "operation": "gemini_image_download_failed",
                                    },
                                    "labels": {
                                        "component": "gemini_client",
                                        "severity": "medium",
                                    },
                                },
                            )

                    img_span.update(
                        output={
                            "successful_downloads": successful_downloads,
                            "total_requested": len(request.image_urls),
                            "success_rate": successful_downloads
                            / len(request.image_urls),
                        },
                        metadata={
                            "download_success_rate": successful_downloads
                            / len(request.image_urls),
                        },
                    )

            # Create the prompt for generating the enhanced statement

            statement_text = f"Statement: {request.statement}"
            if request.is_social_media:
                statement_text = f"Statement extracted from scraped social media, Check the images for the statements too: {request.statement} \n"
                statement_text += "Sometimes the scraped image provided here might not contain the statements it might just be failed scraped image screenshot, like facebook logo or some unecessary HTML screenshot just ignore it if it's useless."

            analysis_prompt = f"""
Analyze the following statement and any accompanying images. Generate a comprehensive 
text representation that captures all verifiable claims from both the text and images.
Also generate preview data (title and description) based on the content.

{statement_text}

Guidelines for enhanced statement:
1. Extract all factual claims from the statement and images, if the images are appropriate.
2. For images, describe visible entities, text, contexts, and any implied claims, if image is valid screenshot.
3. Include all relevant details that would need verification
4. Format your response as a single cohesive text that a fact-checking system can verify
5. Maintain the original meaning and intent of the content
6. If it is a social media post, make sure to extract the most appropriate fact checkable and interesting statements based on the post, author text and images. Do not extract boring factual statements.

Guidelines for preview data:
1. Generate a concise, informative title that summarizes the main topic or claim, in Georgian language
2. Create a brief description that captures the essence of the content, in Georgian language, just a few sentences, do not mention Post Contains, ტექსტი შეიცავს or  ფოსტი შეიცავს just a 1-2 sentences .
3. If there's no URL (image-only submission), still generate title and description based on image content
4. Keep title under 100 characters and description under 200 characters, in Georgian language
5. Make title and description suitable for social media sharing, in Georgian language, do not mention Post Contains, ტექსტი შეიცავს or ფოსტი შეიცავს just a 1-2 sentences.

Your response should include both the enhanced statement and preview data.
"""

            # Build the content list, including images if available
            contents = []

            # Add images to the content if available
            if image_objects:
                contents.extend(image_objects)

            # Add the analysis prompt
            contents.append(analysis_prompt)

            # Create generation span for the actual Gemini API call - nested within analysis span
            with self.langfuse.start_as_current_generation(
                name="gemini_fact_check_generation", model="gemini-2.5-flash"
            ) as gen:
                gen.update(
                    input={
                        "prompt": analysis_prompt,
                        "statement": request.statement,
                        "image_count": len(image_objects),
                        "is_social_media": request.is_social_media,
                        "model_config": {
                            "model": "gemini-2.5-flash",
                            "temperature": 0.2,
                            "response_format": "json",
                            "thinking_budget": 3000,
                        },
                    },
                    metadata={
                        "statement_length": len(request.statement),
                        "image_url_count": len(request.image_urls)
                        if request.image_urls
                        else 0,
                        "successful_image_downloads": len(image_objects),
                        "is_social_media": request.is_social_media,
                        "operation_type": "fact_check_input_generation",
                        "response_format": "structured_json",
                    },
                )

                logger.info(
                    "Starting Gemini fact check input generation",
                    extra={
                        "json_fields": {
                            "statement_length": len(request.statement),
                            "image_count": len(image_objects),
                            "model": "gemini-2.5-flash",
                            "temperature": 0.2,
                            "operation": "gemini_fact_check_generation_start",
                        },
                        "labels": {"component": "gemini_client", "phase": "generation"},
                    },
                )

                # Generate the enhanced statement using the new response schema
                config = GenerateContentConfig(
                    system_instruction=system_prompt,
                    response_mime_type="application/json",
                    response_schema=FactCheckInputResponse.model_json_schema(),
                    temperature=0.2,
                    thinking_config=ThinkingConfig(
                        thinking_budget=3000,
                    ),
                )

                try:
                    response = await gemini_client.aio.models.generate_content(
                        model="gemini-2.5-flash",
                        config=config,
                        contents=contents,
                    )
                    print(response)

                    if not response or not response.text:
                        if (
                            response.prompt_feedback.block_reason
                            == BlockedReason.PROHIBITED_CONTENT
                        ):
                            return FactCheckInputResponse(
                                enhanced_statement="",
                                is_valid_for_fact_check=False,
                                error_reason="Prohibited content",
                            )
                        error_msg = "Empty response when generating enhanced statement"
                        logger.error(error_msg)
                        gen.update(output=None, metadata={"error": error_msg})
                        raise Exception(error_msg)

                    try:
                        result = FactCheckInputResponse.model_validate_json(
                            response.text
                        )

                        # Enhanced output tracking with comprehensive details
                        gen.update(
                            output={
                                "enhanced_statement": result.enhanced_statement,
                                "is_valid_for_fact_check": result.is_valid_for_fact_check,
                                "has_preview_data": result.preview_data is not None,
                                "success": True,
                            },
                            usage={
                                "input": response.usage_metadata.prompt_token_count,
                                "output": response.usage_metadata.candidates_token_count,
                                "total": response.usage_metadata.total_token_count,
                            },
                            metadata={
                                "enhanced_statement_length": len(
                                    result.enhanced_statement
                                ),
                                "is_valid_for_fact_check": result.is_valid_for_fact_check,
                                "has_preview_data": result.preview_data is not None,
                                "completion_reason": "success",
                                "model_version": "gemini-2.5-flash",
                                "cache_read_input_tokens": response.usage_metadata.cached_content_token_count,
                            },
                        )

                        logger.info(
                            "Gemini fact check input generation completed successfully",
                            extra={
                                "json_fields": {
                                    "enhanced_statement_length": len(
                                        result.enhanced_statement
                                    ),
                                    "is_valid_for_fact_check": result.is_valid_for_fact_check,
                                    "has_preview_data": result.preview_data is not None,
                                    "input_tokens": response.usage_metadata.prompt_token_count,
                                    "output_tokens": response.usage_metadata.candidates_token_count,
                                    "total_tokens": response.usage_metadata.total_token_count,
                                    "operation": "gemini_fact_check_generation_success",
                                },
                                "labels": {
                                    "component": "gemini_client",
                                    "phase": "generation",
                                },
                            },
                        )

                        # Update the analysis span with final results
                        analysis_span.update(
                            output={
                                "enhanced_statement": result.enhanced_statement,
                                "is_valid_for_fact_check": result.is_valid_for_fact_check,
                                "has_preview_data": result.preview_data is not None,
                                "image_processing_success": len(image_objects) > 0
                                if request.image_urls
                                else True,
                            },
                            metadata={
                                "total_tokens_used": response.usage_metadata.total_token_count,
                                "processing_success": True,
                            },
                        )

                        return result

                    except Exception as e:
                        error_msg = f"Failed to parse FactCheckInputResponse: {e}"
                        logger.error(
                            "Gemini fact check input generation failed - parsing error",
                            extra={
                                "json_fields": {
                                    "error": str(e),
                                    "error_type": type(e).__name__,
                                    "raw_response_preview": response.text[:200]
                                    if response.text
                                    else None,
                                    "operation": "gemini_fact_check_generation_parse_error",
                                },
                                "labels": {
                                    "component": "gemini_client",
                                    "severity": "high",
                                },
                            },
                        )
                        gen.update(
                            output=None,
                            metadata={
                                "error": error_msg,
                                "raw_response": response.text[:500]
                                if response.text
                                else None,
                                "completion_reason": "parsing_failed",
                            },
                        )
                        analysis_span.update(
                            output=None,
                            metadata={
                                "error": error_msg,
                                "processing_success": False,
                            },
                        )
                        raise e

                except Exception as e:
                    error_msg = f"Gemini API call failed: {e}"
                    logger.error(
                        "Gemini fact check input generation failed with exception",
                        extra={
                            "json_fields": {
                                "error": str(e),
                                "error_type": type(e).__name__,
                                "operation": "gemini_fact_check_generation_exception",
                            },
                            "labels": {
                                "component": "gemini_client",
                                "severity": "high",
                            },
                        },
                    )
                    gen.update(
                        output=None,
                        metadata={
                            "error": str(e),
                            "completion_reason": "api_exception",
                        },
                    )
                    analysis_span.update(
                        output=None,
                        metadata={
                            "error": error_msg,
                            "processing_success": False,
                        },
                    )
                    raise e

    @observe()
    @retry(
        wait=wait_random_exponential(multiplier=1, max=3),
        before_sleep=before_sleep_log(logger, logging.ERROR),
        reraise=True,
        stop=stop_after_attempt(4),
    )
    async def generate_notification(
        self, request: NotificationGenerationRequest
    ) -> Optional[NotificationGenerationResponse]:
        """
        Generate an engaging notification title and description based on news content.
        Enhanced with comprehensive Langfuse observability.

        Args:
            request: The NotificationGenerationRequest containing news items and notification type

        Returns:
            NotificationGenerationResponse with title, description, and relevance assessment
        """
        # Create generation span with enhanced observability
        with self.langfuse.start_as_current_generation(
            name="gemini_notification_generation", model="gemini-2.5-flash"
        ) as gen:
            logger.info(
                "Starting Gemini notification generation",
                extra={
                    "json_fields": {
                        "news_items_count": len(request.news_items),
                        "notification_type": request.notification_type,
                        "tab": request.tab,
                        "operation": "gemini_notification_generation_start",
                    },
                    "labels": {"component": "gemini_client", "phase": "notification"},
                },
            )

            # Enhanced input tracking for notification generation
            gen.update(
                input={
                    "news_items": [
                        {
                            "id": item.id,
                            "content_length": len(item.content),
                        }
                        for item in request.news_items
                    ],
                    "news_item_count": len(request.news_items),
                    "notification_type": request.notification_type,
                    "tab": request.tab,
                    "has_fact_check_data": request.fact_check_data is not None,
                    "model_config": {
                        "model": "gemini-2.5-flash",
                        "temperature": 0.7,
                        "response_format": "json",
                        "thinking_budget": 3000,
                    },
                },
                metadata={
                    "news_item_count": len(request.news_items),
                    "notification_type": request.notification_type,
                    "tab": request.tab,
                    "operation_type": "notification_generation",
                    "has_fact_check_data": request.fact_check_data is not None,
                },
            )

            # Check if this is for social media content generation
            is_social_media = request.notification_type == "social_media_content"

            if is_social_media:
                # Create the system prompt for social media content generation
                system_prompt = """You are an expert content creator for a Georgian news application specializing in social media content. Your goal is to create engaging social media posts that can be shared across platforms.

<persona>
You are creative, informative, and understand the Georgian cultural context. You create content that is shareable, engaging, and informative without being clickbait. Your writing is always in the Georgian language.
</persona>

<rules>
- All output must be in the Georgian language.
- The tone should be professional yet engaging.
- Focus on the single most impactful, surprising, or newsworthy item from the provided list.
- Create content appropriate for social media sharing.
- The social_media_card_title should be optimized for card display (10-35 words).
</rules>
"""

                # Get tab perspective or default to neutral
                tab_perspective = request.tab or "neutral"

                # Check if fact check data is available
                fact_check_section = ""
                if request.fact_check_data:
                    factuality_score = request.fact_check_data.get("factuality", 0.0)
                    reason_summary = request.fact_check_data.get("reason_summary", "")

                    factuality_percentage = int(factuality_score * 100)

                    fact_check_section = f"""

<fact_check_data>
Factuality Score: {factuality_percentage}% factual
Reason Summary: {reason_summary}
</fact_check_data>

<fact_check_task>
7.  **Generate Fact Check Summary**: Create an engaging, direct fact check summary (15-40 words) that immediately states what was true or false. Start with the most important finding without any prefixes or colons. Be direct and engaging for social media users.
   - For high factuality (70%+): Start by confirming what's true directly
   - For medium factuality (30-69%): Start with what's accurate, then mention what's questionable
   - For low factuality (below 30%): Start directly with what's false or misleading
   - Always write in Georgian language
   - Avoid prefixes like "ფაქტებრივად სწორია", "მტკიცება ზუსტია", "მტკიცება მცდარია", or any similar formal prefixes
   - Make it conversational and engaging

Examples:
- High factuality: "ლიეტუვამ ნამდვილად 10 ქართველს სანქციები დაუწესა და ყველა დასახელებული პირი დადასტურებულია"
- Medium factuality: "ლიეტუვას სანქციები დადასტურებულია, მაგრამ ესტონეთისა და სხვა ქვეყნების შესახებ ცნობები არასწორია"
- Low factuality: "ძირითადი ცნობები არ დადასტურდა და რამდენიმე მნიშვნელოვანი ფაქტი მცდარია"
</fact_check_task>"""

                analysis_prompt = f"""
<task_definition>
Your task is to analyze news items and generate social media content from a {tab_perspective} perspective. You will create a title, description, social media card title{", and fact check summary" if request.fact_check_data else ""}.
</task_definition>

<thinking_process>
1.  **Analyze Content**: Review the news items and select the most relevant one for social media sharing.
2.  **Apply Perspective**: Consider the {tab_perspective} perspective when framing the content:
   - neutral: Balanced, factual presentation
   - government: Slightly favorable to government actions/policies  
   - opposition: More critical stance on government actions
3.  **Craft Title**: Create an engaging title appropriate for the {tab_perspective} perspective (max 100 characters).
4.  **Write Description**: Write a compelling description that elaborates on the title (max 200 characters).
5.  **Create Social Media Card Title**: Generate a concise, punchy title perfect for social media cards (10-35 words, optimized for visual appeal and shareability).
6.  **Select Verification ID**: Include the ID of the news item your content is based on.{fact_check_section}
</thinking_process>

<news_items>
{"\n".join([f"- ID: {item.id} | Content: {item.content}" for item in request.news_items])}
</news_items>

<perspective_examples>
<neutral_examples>
- Title: "ახალი კანონპროექტი პარლამენტში განიხილება"
- Social Media Card Title: "პარლამენტი ახალ კანონს განიხილავს - რას ნიშნავს ეს ქვეყნისთვის"
</neutral_examples>
<government_examples>
- Title: "მთავრობის ახალი ინიციატივა განვითარებისთვის"
- Social Media Card Title: "მთავრობის ახალი ინიციატივა: პოზიტიური ცვლილებები ქვეყანაში"
</government_examples>
<opposition_examples>
- Title: "კითხვები ახალი პოლიტიკის შესახებ"
- Social Media Card Title: "ახალი პოლიტიკა: რა შედეგები მოსალოდნელია მოქალაქეებისთვის"
</opposition_examples>
</perspective_examples>

Please generate the content according to the specified JSON schema, ensuring the social_media_card_title field is included.
"""
            else:
                # Original system prompt for regular notifications
                system_prompt = """You are an expert notification copywriter for a Georgian news application. Your goal is to craft compelling push notifications that maximize user engagement (click-through rate).

<persona>
You are creative, witty, and understand the Georgian cultural context. You know how to create a sense of urgency and curiosity without resorting to clickbait. Your writing is always in the Georgian language.
</persona>

<rules>
- All output must be in the Georgian language.
- The tone should be engaging and intriguing, not just a dry summary.
- Avoid generic, overused phrases such as "თანამედროვე სიახლეები" or "დღის ღონისძიებები".
- Focus on the single most impactful, surprising, or newsworthy item from the provided list.
- If no news items are significant enough to warrant a notification, you must indicate that.
</rules>
"""

                # Prepare the news content summary with IDs
                news_summary = "\n".join(
                    [
                        f"- ID: {item.id} | Content: {item.content}"
                        for item in request.news_items
                    ]
                )

                analysis_prompt = f"""
<task_definition>
Your task is to analyze a list of news items and generate a single, professional news notification in the style of major news outlets like CNN or BBC. You will also determine if the news is significant enough for a push notification.
</task_definition>

<thinking_process>
1.  **Analyze Relevance**: First, review all the news items provided. Determine if any of them are truly newsworthy and important enough for a push notification to the general public in Georgia. If not, conclude that the news is not relevant.
2.  **Select Key Story**: If the news is relevant, identify the single most compelling story by its ID. This could be the most surprising, urgent, or impactful news item. Remember the ID of this selected news item.
3.  **Craft Title**: Based on the selected key story, create a professional, newsworthy title in Georgian (max 50 characters). Use clear, factual language similar to major news outlets. The title must be concise and informative.
4.  **Write Description**: Write a single, factual sentence in Georgian (max 120 characters) that provides essential context and key details about the story. Focus on the most important facts, similar to how CNN or BBC would present breaking news. Avoid any calls-to-action or phrases that encourage app usage.
5.  **Select Verification ID**: Include the ID of the news item that your notification is based on in the selected_verification_id field.
6.  **Final Review**: Read your title and description. Are they clear, factual, and professional? Do they provide essential information without promotional language? Do they adhere to all guidelines?
</thinking_process>

<news_items>
{news_summary}
</news_items>

<examples>
<good_titles>
- "პარლამენტმა ახალი კანონი მიიღო"
- "მთავრობა ახალ რეფორმას აცხადებს"
- "ეკონომიკური ზრდა 5%-ით გაიზარდა"
</good_titles>
<good_descriptions>
- "კანონი იანვრიდან ძალაში შევა და ყველა მოქალაქეს შეეხება."
- "რეფორმა განათლების სისტემაში ცვლილებებს გულისხმობს."
- "სტატისტიკის უწყების ბოლო მონაცემების თანახმად."
</good_descriptions>
</examples>

Please follow the thinking process and generate the notification according to the specified JSON schema.
"""

            # Generate the notification using structured response
            config = GenerateContentConfig(
                system_instruction=system_prompt,
                response_mime_type="application/json",
                response_schema=NotificationGenerationResponse.model_json_schema(),
                temperature=0.7,  # Slightly higher temperature for creativity
                thinking_config=ThinkingConfig(
                    thinking_budget=3000,
                ),
            )

            try:
                logger.info(
                    "Executing Gemini notification generation",
                    extra={
                        "json_fields": {
                            "prompt_length": len(analysis_prompt),
                            "model": "gemini-2.5-flash",
                            "temperature": 0.7,
                            "operation": "gemini_notification_api_call",
                        },
                        "labels": {"component": "gemini_client", "phase": "generation"},
                    },
                )

                response = await gemini_client.aio.models.generate_content(
                    model="gemini-2.5-flash",
                    config=config,
                    contents=[analysis_prompt],
                )

                # Enhanced usage tracking
                gen.update(
                    usage={
                        "input": response.usage_metadata.prompt_token_count,
                        "output": response.usage_metadata.candidates_token_count,
                        "total": response.usage_metadata.total_token_count,
                    },
                    metadata={
                        "model_version": "gemini-2.5-flash",
                        "completion_reason": "success"
                        if response and response.text
                        else "empty_response",
                        "cache_read_input_tokens": response.usage_metadata.cached_content_token_count,
                    },
                )

                if not response or not response.text:
                    error_msg = "Empty response when generating notification"
                    logger.error(error_msg)
                    gen.update(output=None, metadata={"error": error_msg})
                    raise Exception(error_msg)

                try:
                    result = NotificationGenerationResponse.model_validate_json(
                        response.text
                    )

                    # Prepare comprehensive output logging
                    output_data = {
                        "title": result.title,
                        "description": result.description,
                        "is_relevant": result.is_relevant,
                        "title_length": len(result.title),
                        "description_length": len(result.description),
                        "success": True,
                    }

                    # Add social media card title to output if present
                    if result.social_media_card_title:
                        output_data["social_media_card_title"] = (
                            result.social_media_card_title
                        )
                        output_data["social_media_card_title_length"] = len(
                            result.social_media_card_title
                        )

                    # Add fact check summary to output if present
                    if result.fact_check_summary:
                        output_data["fact_check_summary"] = result.fact_check_summary
                        output_data["fact_check_summary_length"] = len(
                            result.fact_check_summary
                        )

                    # Update generation with comprehensive output data
                    gen.update(
                        output=output_data,
                        metadata={
                            "is_relevant": result.is_relevant,
                            "has_social_media_card_title": bool(
                                result.social_media_card_title
                            ),
                            "has_fact_check_summary": bool(result.fact_check_summary),
                            "completion_reason": "success",
                        },
                    )

                    logger.info(
                        "Gemini notification generation completed successfully",
                        extra={
                            "json_fields": {
                                "is_relevant": result.is_relevant,
                                "title_length": len(result.title),
                                "description_length": len(result.description),
                                "has_social_media_card_title": bool(
                                    result.social_media_card_title
                                ),
                                "has_fact_check_summary": bool(
                                    result.fact_check_summary
                                ),
                                "input_tokens": response.usage_metadata.prompt_token_count,
                                "output_tokens": response.usage_metadata.candidates_token_count,
                                "total_tokens": response.usage_metadata.total_token_count,
                                "operation": "gemini_notification_generation_success",
                            },
                            "labels": {
                                "component": "gemini_client",
                                "phase": "notification",
                            },
                        },
                    )

                    return result

                except Exception as e:
                    error_msg = f"Failed to parse NotificationGenerationResponse: {e}"
                    logger.error(
                        "Gemini notification generation failed - parsing error",
                        extra={
                            "json_fields": {
                                "error": str(e),
                                "error_type": type(e).__name__,
                                "raw_response_preview": response.text[:200]
                                if response.text
                                else None,
                                "operation": "gemini_notification_generation_parse_error",
                            },
                            "labels": {
                                "component": "gemini_client",
                                "severity": "high",
                            },
                        },
                    )
                    gen.update(
                        output=None,
                        metadata={
                            "error": error_msg,
                            "raw_response": response.text[:500]
                            if response.text
                            else None,
                            "completion_reason": "parsing_failed",
                        },
                    )
                    raise e

            except Exception as e:
                error_msg = f"Gemini notification generation failed: {e}"
                logger.error(
                    "Gemini notification generation failed with exception",
                    extra={
                        "json_fields": {
                            "error": str(e),
                            "error_type": type(e).__name__,
                            "operation": "gemini_notification_generation_exception",
                        },
                        "labels": {"component": "gemini_client", "severity": "high"},
                    },
                )
                gen.update(
                    output=None,
                    metadata={
                        "error": str(e),
                        "completion_reason": "api_exception",
                    },
                )
                raise e
