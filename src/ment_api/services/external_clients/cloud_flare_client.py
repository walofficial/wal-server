import asyncio
import io
import logging

from PIL import Image

from ment_api.configurations.config import settings
from ment_api.models.image_with_dims import ImageWithDims
from ment_api.services.external_clients.langfuse_client import langfuse
from ment_api.services.google_storage_service import client

logger = logging.getLogger(__name__)


def _upload_image_sync(
    file: bytes, destination_file_name: str, content_type: str
) -> ImageWithDims:
    """Synchronous upload function to run in thread pool"""
    try:
        # Log file size
        logger.info(
            f"Processing image {destination_file_name} with size {len(file)} bytes"
        )

        # Get image dimensions
        image_stream = io.BytesIO(file)
        img = Image.open(image_stream)
        width, height = img.size

        # Upload to storage
        bucket = client.bucket(settings.storage_bucket_name)
        blob = bucket.blob(
            f"{settings.storage_video_verification_path}{destination_file_name}"
        )
        blob.upload_from_file(io.BytesIO(file), content_type=content_type)

        return ImageWithDims(
            url=blob.public_url,
            width=width,
            height=height,
            aspectRatio={"width": width, "height": height},
        )

    except Exception as e:
        logger.error(f"Error processing image {destination_file_name}: {e}")
        # Upload file even if dimension extraction fails
        bucket = client.bucket(settings.storage_bucket_name)
        blob = bucket.blob(
            f"{settings.storage_video_verification_path}{destination_file_name}"
        )
        blob.upload_from_file(io.BytesIO(file), content_type=content_type)

        return ImageWithDims(
            url=blob.public_url,
            width=1920,
            height=1080,
            aspectRatio={"width": 1920, "height": 1080},
        )


async def upload_image(
    file: bytes, destination_file_name: str, content_type: str
) -> ImageWithDims:
    """
    Async function to upload image to cloud storage.
    Runs blocking operations in thread pool to maintain asyncio compatibility.
    """
    with langfuse.start_as_current_span(name="cloud-storage-upload") as upload_span:
        upload_span.update(
            input={
                "destination_file_name": destination_file_name,
                "content_type": content_type,
                "file_size_bytes": len(file),
            },
            metadata={
                "operation": "cloud_storage_upload",
                "storage_provider": "google_cloud_storage",
            },
        )

        try:
            loop = asyncio.get_running_loop()

            # Run the blocking upload operation in thread pool
            result = await loop.run_in_executor(
                None, _upload_image_sync, file, destination_file_name, content_type
            )

            upload_span.update(
                output={
                    "upload_url": result.url,
                    "image_width": result.width,
                    "image_height": result.height,
                    "success": True,
                }
            )

            logger.info(
                "Image uploaded successfully to cloud storage",
                extra={
                    "json_fields": {
                        "destination_file_name": destination_file_name,
                        "file_size_bytes": len(file),
                        "image_width": result.width,
                        "image_height": result.height,
                        "upload_url": result.url,
                        "operation": "cloud_storage_upload_success",
                    },
                    "labels": {"component": "cloud_storage", "operation": "upload"},
                },
            )

            return result

        except Exception as e:
            upload_span.update(
                output={
                    "error": str(e),
                    "error_type": type(e).__name__,
                    "success": False,
                }
            )

            logger.error(
                "Failed to upload image to cloud storage",
                extra={
                    "json_fields": {
                        "destination_file_name": destination_file_name,
                        "file_size_bytes": len(file),
                        "content_type": content_type,
                        "error_message": str(e),
                        "operation": "cloud_storage_upload_error",
                    },
                    "labels": {
                        "component": "cloud_storage",
                        "operation": "upload",
                        "severity": "high",
                    },
                },
                exc_info=True,
            )
            raise


# Keep the synchronous version for backward compatibility if needed
def upload_image_sync(
    file: bytes, destination_file_name: str, content_type: str
) -> ImageWithDims:
    """Synchronous version - use upload_image() instead for async contexts"""
    return _upload_image_sync(file, destination_file_name, content_type)
