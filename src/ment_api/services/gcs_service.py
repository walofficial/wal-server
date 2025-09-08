import logging

from ment_api.services.google_storage_service import upload_file_from_path

logger = logging.getLogger(__name__)


async def upload_file_to_gcs(
    source_file_name: str, destination_blob_name: str, bucket_name: str
) -> str:
    """Uploads a file to the GCS bucket."""
    try:
        gcs_uri = await upload_file_from_path(
            source_file_name, destination_blob_name, bucket_name
        )

        logger.info(
            "File uploaded to GCS successfully",
            extra={
                "json_fields": {
                    "operation": "upload_file_to_gcs",
                    "source_file_name": source_file_name,
                    "destination_blob_name": destination_blob_name,
                    "bucket_name": bucket_name,
                    "gcs_uri": gcs_uri,
                },
                "labels": {"component": "gcs_service"},
            },
        )

        return gcs_uri
    except Exception as e:
        logger.error(
            "Failed to upload file to GCS",
            extra={
                "json_fields": {
                    "operation": "upload_file_to_gcs",
                    "source_file_name": source_file_name,
                    "destination_blob_name": destination_blob_name,
                    "bucket_name": bucket_name,
                    "error": str(e),
                },
                "labels": {"component": "gcs_service", "severity": "high"},
            },
        )
        return None


def build_audio_blob_path(youtube_id: str) -> str:
    """Build the GCS blob path for audio files."""
    return f"audio/youtube_{youtube_id}.webm"
