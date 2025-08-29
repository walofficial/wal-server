import logging
from contextlib import asynccontextmanager
from typing import List, Optional

from google.cloud.vision_v1 import (
    AnnotateImageRequest,
    BatchAnnotateImagesRequest,
    Feature,
    Image,
    ImageAnnotatorAsyncClient,
    ImageSource,
)
from tenacity import (
    before_sleep_log,
    retry,
    stop_after_attempt,
    wait_random_exponential,
)

from ment_api.services.external_clients.models.vision_models import (
    OCRResult,
    TextExtractRequest,
)

logger = logging.getLogger(__name__)


class CloudVisionClient:
    """
    Client for Google Cloud Vision API to extract text from images using OCR.
    Works with images stored in Google Cloud Storage (GCS) for optimal performance.
    Uses async client and batch processing for better performance.
    """

    def __init__(self):
        # Use the async version of the client
        self.client = ImageAnnotatorAsyncClient()

    def _convert_to_gcs_uri(self, url: str) -> Optional[str]:
        """
        Convert URL to GCS URI format if it's a GCS URL.

        Args:
            url: Image URL (could be HTTP or GCS format)

        Returns:
            GCS URI (gs://) or None if not a valid GCS URL
        """
        try:
            # If already in gs:// format, return as-is
            if url.startswith("gs://"):
                return url

            # If it's a GCS HTTP URL, convert to gs:// format
            # Format: https://storage.googleapis.com/bucket-name/path/to/file
            # or: https://storage.cloud.google.com/bucket-name/path/to/file
            if "storage.googleapis.com" in url or "storage.cloud.google.com" in url:
                # Extract bucket and path from URL
                if "/storage.googleapis.com/" in url:
                    parts = url.split("/storage.googleapis.com/", 1)
                elif "/storage.cloud.google.com/" in url:
                    parts = url.split("/storage.cloud.google.com/", 1)
                else:
                    return None

                if len(parts) == 2:
                    path_part = parts[1]
                    # Split bucket and object path
                    path_components = path_part.split("/", 1)
                    if len(path_components) >= 1:
                        bucket = path_components[0]
                        object_path = (
                            path_components[1] if len(path_components) > 1 else ""
                        )
                        return f"gs://{bucket}/{object_path}"

            logger.warning(f"URL is not a valid GCS URL: {url}")
            return None
        except Exception as e:
            logger.error(f"Error converting URL to GCS URI: {e}")
            return None

    def _create_annotate_image_request(self, gcs_uri: str) -> AnnotateImageRequest:
        """
        Create an AnnotateImageRequest for a GCS URI.

        Args:
            gcs_uri: GCS URI of the image

        Returns:
            AnnotateImageRequest configured for text detection
        """
        # Create image object with GCS source
        image = Image(source=ImageSource(image_uri=gcs_uri))

        # Create feature for text detection
        feature = Feature(type_=Feature.Type.TEXT_DETECTION)

        # Create and return the request
        return AnnotateImageRequest(image=image, features=[feature])

    @retry(
        wait=wait_random_exponential(multiplier=1, max=3),
        stop=stop_after_attempt(3),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    async def _extract_text_from_batch(
        self, requests: List[AnnotateImageRequest]
    ) -> List[Optional[str]]:
        """
        Extract text from multiple images using batch processing.

        Args:
            requests: List of AnnotateImageRequest objects

        Returns:
            List of extracted texts or None for failed extractions
        """
        try:
            # Create batch request
            batch_request = BatchAnnotateImagesRequest(requests=requests)

            # Perform batch text detection
            response = await self.client.batch_annotate_images(request=batch_request)

            # Process responses
            results = []
            for annotation_response in response.responses:
                # Check for errors
                if annotation_response.error.message:
                    logger.error(
                        f"Vision API error: {annotation_response.error.message}"
                    )
                    results.append(None)
                    continue

                # Extract full text annotation
                texts = annotation_response.text_annotations
                if texts:
                    # The first text annotation contains the full detected text
                    full_text = texts[0].description
                    results.append(full_text.strip() if full_text else None)
                else:
                    logger.info("No text detected in image")
                    results.append(None)

            return results

        except Exception as e:
            logger.error(f"Error extracting text from batch: {e}")
            # Return list of None values matching the request count
            return [None] * len(requests)

    async def extract_text_from_image(self, image_url: str) -> Optional[str]:
        """
        Extract text from a single image URL (must be a GCS URL).

        Args:
            image_url: GCS URL of the image to process

        Returns:
            Extracted text or None if extraction fails
        """
        # Convert to GCS URI format
        gcs_uri = self._convert_to_gcs_uri(image_url)
        if not gcs_uri:
            logger.error(f"Invalid or non-GCS URL provided: {image_url}")
            return None

        # Create request and process as single-item batch
        request = self._create_annotate_image_request(gcs_uri)
        results = await self._extract_text_from_batch([request])

        return results[0] if results else None

    async def extract_text_from_images(
        self, request: TextExtractRequest
    ) -> List[OCRResult]:
        """
        Extract text from multiple images using batch processing.

        Args:
            request: TextExtractRequest containing GCS image URLs and options

        Returns:
            List of OCRResult objects with extracted text and metadata
        """
        if not request.image_urls:
            return []

        logger.info(f"Starting batch OCR for {len(request.image_urls)} images")

        # Convert URLs to GCS URIs and create requests
        batch_requests = []
        valid_indices = []

        for index, url in enumerate(request.image_urls):
            gcs_uri = self._convert_to_gcs_uri(url)
            if gcs_uri:
                batch_requests.append(self._create_annotate_image_request(gcs_uri))
                valid_indices.append(index)
            else:
                logger.warning(f"Skipping invalid GCS URL at index {index}: {url}")

        if not batch_requests:
            logger.error("No valid GCS URLs found for batch processing")
            # Return OCRResult with errors for all images
            return [
                OCRResult(
                    image_url=url,
                    extracted_text=None,
                    success=False,
                    error_message="Invalid or non-GCS URL",
                    image_index=index,
                )
                for index, url in enumerate(request.image_urls)
            ]

        # Process in smaller batches to avoid API limits
        # Google Cloud Vision allows up to 16 images per batch request
        max_batch_size = min(16, len(batch_requests))

        all_results = []

        # Process requests in batches
        for i in range(0, len(batch_requests), max_batch_size):
            batch_end = min(i + max_batch_size, len(batch_requests))
            current_batch = batch_requests[i:batch_end]
            current_indices = valid_indices[i:batch_end]

            logger.info(
                f"Processing batch {i // max_batch_size + 1} with {len(current_batch)} images"
            )

            try:
                batch_results = await self._extract_text_from_batch(current_batch)

                # Convert batch results to OCRResult objects
                for j, extracted_text in enumerate(batch_results):
                    original_index = current_indices[j]
                    original_url = request.image_urls[original_index]

                    all_results.append(
                        OCRResult(
                            image_url=original_url,
                            extracted_text=extracted_text,
                            success=extracted_text is not None,
                            error_message=(
                                None
                                if extracted_text is not None
                                else "No text detected"
                            ),
                            image_index=original_index,
                        )
                    )

            except Exception as e:
                logger.error(f"Error processing batch {i // max_batch_size + 1}: {e}")
                # Add error results for this batch
                for j in range(len(current_batch)):
                    original_index = current_indices[j]
                    original_url = request.image_urls[original_index]

                    all_results.append(
                        OCRResult(
                            image_url=original_url,
                            extracted_text=None,
                            success=False,
                            error_message=str(e),
                            image_index=original_index,
                        )
                    )

        # Fill in results for invalid URLs that were skipped
        final_results = [None] * len(request.image_urls)

        # Place the processed results in their correct positions
        for result in all_results:
            final_results[result.image_index] = result

        # Fill in any remaining None positions with error results
        for index, result in enumerate(final_results):
            if result is None:
                final_results[index] = OCRResult(
                    image_url=request.image_urls[index],
                    extracted_text=None,
                    success=False,
                    error_message="Invalid or non-GCS URL",
                    image_index=index,
                )

        successful_extractions = sum(1 for r in final_results if r.success)
        logger.info(
            f"Batch OCR completed: {successful_extractions}/{len(request.image_urls)} images processed successfully"
        )

        return final_results

    async def close(self):
        """
        Close the async client connection.
        """
        # The async client doesn't require explicit closing in current version
        # but this method is here for future compatibility
        pass


@asynccontextmanager
async def get_cloud_vision_client():
    """
    Async context manager for CloudVisionClient.
    """
    client = CloudVisionClient()
    try:
        yield client
    finally:
        await client.close()


async def get_cloud_vision_dependency():
    """
    FastAPI dependency for CloudVisionClient.
    """
    async with get_cloud_vision_client() as client:
        yield client
