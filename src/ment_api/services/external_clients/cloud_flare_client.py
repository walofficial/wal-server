import logging
from typing import Optional

from ment_api.models.image_with_dims import ImageWithDims
from ment_api.services.external_clients.langfuse_client import langfuse
from ment_api.services.google_storage_service import upload_image_verification
import httpx

logger = logging.getLogger(__name__)


async def upload_image(
    file: bytes, destination_file_name: str, content_type: str
) -> ImageWithDims:
    """
    Async function to upload image to cloud storage using the google_storage_service.
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
            # Use the new async function from google_storage_service
            result = await upload_image_verification(
                file, destination_file_name, content_type
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
