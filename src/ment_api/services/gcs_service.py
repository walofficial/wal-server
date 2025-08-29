import asyncio
import logging
from google.cloud import storage

logger = logging.getLogger(__name__)


async def upload_file_to_gcs(
    source_file_name: str, destination_blob_name: str, bucket_name: str
) -> str:
    """Uploads a file to the GCS bucket."""
    storage_client = storage.Client()
    try:
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(destination_blob_name)

        # Run upload in a separate thread
        def upload_sync():
            blob.upload_from_filename(source_file_name)

        await asyncio.to_thread(upload_sync)

        gcs_uri = f"gs://{bucket_name}/{destination_blob_name}"
        logger.info(f"File {source_file_name} uploaded to {gcs_uri}.")
        return gcs_uri
    except Exception as e:
        logger.error(f"Failed to upload {source_file_name} to GCS: {e}")
        return None


def build_audio_blob_path(youtube_id: str) -> str:
    """Build the GCS blob path for audio files."""
    return f"audio/youtube_{youtube_id}.webm"
