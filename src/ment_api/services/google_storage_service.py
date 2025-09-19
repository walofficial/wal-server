import io
import logging
from typing import BinaryIO, Tuple

from gcloud.aio.storage import Storage
from PIL import Image

from ment_api.configurations.config import settings
from ment_api.models.image_with_dims import ImageWithDims


def calculate_instagram_aspect_ratio(width: int, height: int) -> Tuple[int, int]:
    """
    Calculate Instagram-style aspect ratio clamping.
    
    Instagram rules:
    - Minimum aspect ratio: 1.91:1 (landscape)  
    - Maximum aspect ratio: 4:5 (portrait, 0.8:1)
    
    Args:
        width: Original image width
        height: Original image height
        
    Returns:
        Tuple of (clamped_width, clamped_height) for aspect ratio calculation
    """
    original_aspect_ratio = width / height
    
    # Instagram limits
    min_aspect_ratio = 1.91  # Landscape limit (1.91:1)
    max_aspect_ratio = 0.8   # Portrait limit (4:5 or 0.8:1)
    
    # If image is too wide (landscape), clamp to 1.91:1
    if original_aspect_ratio > min_aspect_ratio:
        # Keep width, reduce height
        clamped_height = int(width / min_aspect_ratio)
        return width, clamped_height
    
    # If image is too tall (portrait), clamp to 4:5 (0.8:1) 
    elif original_aspect_ratio < max_aspect_ratio:
        # Keep width, reduce height to max allowed
        clamped_height = int(width / max_aspect_ratio)
        return width, clamped_height
    
    # Image is within Instagram's aspect ratio limits
    return width, height


async def upload_video_verification(
    file: BinaryIO, destination_file_name: str, content_type: str
) -> str:
    object_name = f"{settings.storage_video_verification_path}{destination_file_name}"

    logging.info(
        "Starting video verification upload",
        extra={
            "json_fields": {
                "operation": "upload_video_verification",
                "destination_file_name": destination_file_name,
                "content_type": content_type,
                "bucket": settings.storage_bucket_name,
            },
            "labels": {"component": "google_storage_service"},
        },
    )

    try:
        async with Storage() as client:
            file_data = file.read()

            await client.upload(
                bucket=settings.storage_bucket_name,
                object_name=object_name,
                file_data=file_data,
                content_type=content_type,
            )

            public_url = f"https://storage.googleapis.com/{settings.storage_bucket_name}/{object_name}"

            logging.info(
                "Video verification upload completed",
                extra={
                    "json_fields": {
                        "operation": "upload_video_verification",
                        "destination_file_name": destination_file_name,
                        "public_url": public_url,
                        "file_size_bytes": len(file_data),
                    },
                    "labels": {"component": "google_storage_service"},
                },
            )

            return public_url

    except Exception as e:
        logging.error(
            "Video verification upload failed",
            extra={
                "json_fields": {
                    "operation": "upload_video_verification",
                    "destination_file_name": destination_file_name,
                    "error": str(e),
                },
                "labels": {"component": "google_storage_service", "severity": "high"},
            },
        )
        raise


async def upload_image_verification(
    file: bytes, destination_file_name: str, content_type: str, limit_aspect_ratio: bool = False
) -> ImageWithDims:
    """
    Upload image to Google Cloud Storage and extract dimensions.

    Args:
        file: Image file bytes
        destination_file_name: Name of the destination file
        content_type: MIME type of the image

    Returns:
        ImageWithDims: Object containing URL, dimensions, and aspect ratio
    """
    object_name = f"{settings.storage_video_verification_path}{destination_file_name}"

    logging.info(
        "Starting image verification upload",
        extra={
            "json_fields": {
                "operation": "upload_image_verification",
                "destination_file_name": destination_file_name,
                "content_type": content_type,
                "bucket": settings.storage_bucket_name,
                "file_size_bytes": len(file),
            },
            "labels": {"component": "google_storage_service"},
        },
    )

    try:
        # Extract image dimensions
        image_stream = io.BytesIO(file)
        img = Image.open(image_stream)
        width, height = img.size

        # Apply Instagram-style aspect ratio clamping if enabled
        if limit_aspect_ratio:
            clamped_width, clamped_height = calculate_instagram_aspect_ratio(width, height)
            aspect_ratio_dict = {"width": clamped_width, "height": clamped_height}
        else:
            aspect_ratio_dict = {"width": width, "height": height}

        # Upload to storage
        async with Storage() as client:
            await client.upload(
                bucket=settings.storage_bucket_name,
                object_name=object_name,
                file_data=file,
                content_type=content_type,
            )

            public_url = f"https://storage.googleapis.com/{settings.storage_bucket_name}/{object_name}"

            result = ImageWithDims(
                url=public_url,
                width=width,
                height=height,
                aspectRatio=aspect_ratio_dict,
            )

            logging.info(
                "Image verification upload completed",
                extra={
                    "json_fields": {
                        "operation": "upload_image_verification",
                        "destination_file_name": destination_file_name,
                        "public_url": public_url,
                        "file_size_bytes": len(file),
                        "image_width": width,
                        "image_height": height,
                    },
                    "labels": {"component": "google_storage_service"},
                },
            )

            return result

    except Exception as e:
        # If dimension extraction fails, try upload with default dimensions
        try:
            async with Storage() as client:
                await client.upload(
                    bucket=settings.storage_bucket_name,
                    object_name=object_name,
                    file_data=file,
                    content_type=content_type,
                )

                public_url = f"https://storage.googleapis.com/{settings.storage_bucket_name}/{object_name}"

                result = ImageWithDims(
                    url=public_url,
                    width=1920,
                    height=1080,
                    aspectRatio={"width": 1920, "height": 1080},
                )

                logging.warning(
                    "Image verification upload completed with default dimensions",
                    extra={
                        "json_fields": {
                            "operation": "upload_image_verification",
                            "destination_file_name": destination_file_name,
                            "public_url": public_url,
                            "file_size_bytes": len(file),
                            "dimension_extraction_error": str(e),
                            "used_default_dimensions": True,
                        },
                        "labels": {"component": "google_storage_service"},
                    },
                )

                return result

        except Exception as upload_error:
            logging.error(
                "Image verification upload failed",
                extra={
                    "json_fields": {
                        "operation": "upload_image_verification",
                        "destination_file_name": destination_file_name,
                        "error": str(upload_error),
                        "original_dimension_error": str(e),
                    },
                    "labels": {
                        "component": "google_storage_service",
                        "severity": "high",
                    },
                },
            )
            raise upload_error


async def download_video_verification(file_name: str) -> BinaryIO:
    object_name = f"{settings.storage_video_verification_path}{file_name}"

    logging.info(
        "Starting video verification download",
        extra={
            "json_fields": {
                "operation": "download_video_verification",
                "file_name": file_name,
                "bucket": settings.storage_bucket_name,
            },
            "labels": {"component": "google_storage_service"},
        },
    )

    try:
        async with Storage() as client:
            data = await client.download(
                bucket=settings.storage_bucket_name, object_name=object_name
            )

            file = io.BytesIO(data)
            file.seek(0)

            logging.info(
                "Video verification download completed",
                extra={
                    "json_fields": {
                        "operation": "download_video_verification",
                        "file_name": file_name,
                        "file_size_bytes": len(data),
                    },
                    "labels": {"component": "google_storage_service"},
                },
            )

            return file

    except Exception as e:
        logging.error(
            "Video verification download failed",
            extra={
                "json_fields": {
                    "operation": "download_video_verification",
                    "file_name": file_name,
                    "error": str(e),
                },
                "labels": {"component": "google_storage_service", "severity": "high"},
            },
        )
        raise


def build_raw_video_path(file_name: str) -> str:
    return f"gs://{settings.storage_bucket_name}/{settings.storage_video_verification_path}{file_name}"


def build_raw_video_mp4_path() -> str:
    return f"gs://{settings.storage_bucket_name}/{settings.storage_video_verification_path}"


def build_transcoded_video_path(file_name: str) -> str:
    return f"gs://{settings.storage_bucket_name}/{settings.storage_video_verification_transcoded_path}{file_name}/"


def build_public_transcoded_video_path(file_name: str) -> str:
    return f"https://storage.googleapis.com/{settings.storage_bucket_name}/{settings.storage_video_verification_transcoded_path}{file_name}/"


def build_public_video_mp4_path(file_name: str) -> str:
    return f"https://storage.googleapis.com/{settings.storage_bucket_name}/{settings.storage_video_verification_path}{file_name}"


async def check_blob_exists(blob_path: str, bucket_name: str = None) -> bool:
    """
    Checks if a blob exists in the specified bucket.

    Args:
        blob_path: The full path of the blob to check
        bucket_name: The name of the bucket (defaults to settings.storage_bucket_name)

    Returns:
        bool: True if the blob exists, False otherwise
    """
    if bucket_name is None:
        bucket_name = settings.storage_bucket_name

    logging.info(
        "Checking blob existence",
        extra={
            "json_fields": {
                "operation": "check_blob_exists",
                "blob_path": blob_path,
                "bucket_name": bucket_name,
            },
            "labels": {"component": "google_storage_service"},
        },
    )

    try:
        async with Storage() as client:
            bucket = client.get_bucket(bucket_name)
            exists = await bucket.blob_exists(blob_path)

            logging.info(
                "Blob existence check completed",
                extra={
                    "json_fields": {
                        "operation": "check_blob_exists",
                        "blob_path": blob_path,
                        "bucket_name": bucket_name,
                        "exists": exists,
                    },
                    "labels": {"component": "google_storage_service"},
                },
            )

            return exists

    except Exception as e:
        logging.error(
            "Blob existence check failed",
            extra={
                "json_fields": {
                    "operation": "check_blob_exists",
                    "blob_path": blob_path,
                    "bucket_name": bucket_name,
                    "error": str(e),
                },
                "labels": {"component": "google_storage_service", "severity": "high"},
            },
        )
        raise


async def upload_file_from_path(
    source_file_path: str, destination_blob_name: str, bucket_name: str = None
) -> str:
    """
    Upload a file from local filesystem to GCS bucket.

    Args:
        source_file_path: Path to the local file to upload
        destination_blob_name: Name of the destination blob in GCS
        bucket_name: Name of the bucket (defaults to settings.storage_bucket_name)

    Returns:
        str: GCS URI of the uploaded file
    """
    if bucket_name is None:
        bucket_name = settings.storage_bucket_name

    logging.info(
        "Starting file upload from path",
        extra={
            "json_fields": {
                "operation": "upload_file_from_path",
                "source_file_path": source_file_path,
                "destination_blob_name": destination_blob_name,
                "bucket_name": bucket_name,
            },
            "labels": {"component": "google_storage_service"},
        },
    )

    try:
        # Read file data
        with open(source_file_path, "rb") as file:
            file_data = file.read()

        async with Storage() as client:
            await client.upload(
                bucket=bucket_name,
                object_name=destination_blob_name,
                file_data=file_data,
            )

            gcs_uri = f"gs://{bucket_name}/{destination_blob_name}"

            logging.info(
                "File upload from path completed",
                extra={
                    "json_fields": {
                        "operation": "upload_file_from_path",
                        "source_file_path": source_file_path,
                        "destination_blob_name": destination_blob_name,
                        "bucket_name": bucket_name,
                        "gcs_uri": gcs_uri,
                        "file_size_bytes": len(file_data),
                    },
                    "labels": {"component": "google_storage_service"},
                },
            )

            return gcs_uri

    except Exception as e:
        logging.error(
            "File upload from path failed",
            extra={
                "json_fields": {
                    "operation": "upload_file_from_path",
                    "source_file_path": source_file_path,
                    "destination_blob_name": destination_blob_name,
                    "bucket_name": bucket_name,
                    "error": str(e),
                },
                "labels": {"component": "google_storage_service", "severity": "high"},
            },
        )
        raise


def build_gcs_uri(blob_path: str, bucket_name: str = None) -> str:
    """
    Builds a GCS URI for a blob.

    Args:
        blob_path: The full path of the blob
        bucket_name: The name of the bucket (defaults to settings.storage_bucket_name)

    Returns:
        str: The GCS URI for the blob
    """
    if bucket_name is None:
        bucket_name = settings.storage_bucket_name

    return f"gs://{bucket_name}/{blob_path}"
