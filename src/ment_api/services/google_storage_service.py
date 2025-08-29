import io
from typing import BinaryIO

from google.cloud import storage

from ment_api.configurations.config import settings

client = storage.Client(project=settings.gcp_project_id)


def upload_video_verification(
    file: BinaryIO, destination_file_name: str, content_type: str
) -> str:
    bucket = client.bucket(settings.storage_bucket_name)
    blob = bucket.blob(
        f"{settings.storage_video_verification_path}{destination_file_name}"
    )
    blob.upload_from_file(file, content_type=content_type)
    return blob.public_url


def download_video_verification(file_name: str) -> BinaryIO:
    bucket = client.bucket(settings.storage_bucket_name)
    blob = bucket.blob(f"{settings.storage_video_verification_path}{file_name}")
    file = io.BytesIO()
    blob.download_to_file(file)
    file.seek(0)
    return file


def build_raw_video_path(file_name: str) -> str:
    return f"gs://{settings.storage_bucket_name}/{settings.storage_video_verification_path}{file_name}"


def build_raw_video_mp4_path() -> str:
    return f"gs://{settings.storage_bucket_name}/{settings.storage_video_verification_path}"


def build_transcoded_video_path(file_name: str) -> str:
    return f"gs://{settings.storage_bucket_name}/{settings.storage_video_verification_transcoded_path}{file_name}/"


def build_public_transcoded_video_path(file_name: str) -> str:
    return f"https://storage.cloud.google.com/{settings.storage_bucket_name}/{settings.storage_video_verification_transcoded_path}{file_name}/"


def build_public_video_mp4_path(file_name: str) -> str:
    return f"https://storage.cloud.google.com/{settings.storage_bucket_name}/{settings.storage_video_verification_path}{file_name}"


def check_blob_exists(blob_path: str, bucket_name: str = None) -> bool:
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

    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_path)
    return blob.exists()


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
