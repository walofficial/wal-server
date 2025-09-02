from ment_api.configurations.config import settings
import asyncio
import io
import logging
from PIL import Image
from ment_api.models.image_with_dims import ImageWithDims
from ment_api.services.google_storage_service import client


def _upload_image_sync(
    file: bytes, destination_file_name: str, content_type: str
) -> ImageWithDims:
    """Synchronous upload function to run in thread pool"""
    try:
        # Log file size
        logging.info(
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
        logging.error(f"Error processing image {destination_file_name}: {e}")
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
    loop = asyncio.get_running_loop()

    # Run the blocking upload operation in thread pool
    return await loop.run_in_executor(
        None, _upload_image_sync, file, destination_file_name, content_type
    )


# Keep the synchronous version for backward compatibility if needed
def upload_image_sync(
    file: bytes, destination_file_name: str, content_type: str
) -> ImageWithDims:
    """Synchronous version - use upload_image() instead for async contexts"""
    return _upload_image_sync(file, destination_file_name, content_type)
