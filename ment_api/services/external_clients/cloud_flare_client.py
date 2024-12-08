import requests

from ment_api.config import settings
from typing import BinaryIO

import io
import uuid
from typing import BinaryIO, List, Tuple

from fastapi import UploadFile
from google.cloud import storage
from google.cloud.storage import transfer_manager

from ment_api.common.utils import get_file_name_and_extension
from ment_api.config import settings
from ment_api.models.uploaded_verification_example import UploadedVerificationExample
from ment_api.models.verification_example_media import VerificationExampleMedia

client = storage.Client()


def upload_image(file: bytes, destination_file_name: str, content_type: str) -> str:
    bucket = client.bucket(settings.storage_bucket_name)
    blob = bucket.blob(
        f"{settings.storage_video_verification_path}{destination_file_name}"
    )
    blob.upload_from_file(io.BytesIO(file), content_type=content_type)
    return blob.public_url
