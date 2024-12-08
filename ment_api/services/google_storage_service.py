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


def upload_video_verification(
    file: BinaryIO, destination_file_name: str, content_type: str
) -> str:
    bucket = client.bucket(settings.storage_bucket_name)
    blob = bucket.blob(
        f"{settings.storage_video_verification_path}{destination_file_name}"
    )
    blob.upload_from_file(file, content_type=content_type)
    return blob.public_url


def upload_verification_example_media(
    task_id: str, examples: List[UploadFile]
) -> List[Tuple[UploadedVerificationExample, bytes]]:
    bucket = client.bucket(settings.storage_bucket_name)
    upload_pairs = []
    uploaded_files = []
    for file in examples:
        _, file_extension = get_file_name_and_extension(file)
        example_id = str(uuid.uuid4())
        upload_file_name = f"{example_id}{file_extension}"
        blob = bucket.blob(
            f"{settings.storage_verification_example_media_path}"
            f"{task_id}/{example_id}/{upload_file_name}"
        )
        file_content = file.file.read()
        if len(file_content) == 0:
            raise ValueError(f"File {file.filename} is empty")
        upload_pairs.append((io.BytesIO(file_content), blob))
        uploaded_files.append(
            (
                UploadedVerificationExample(
                    id=example_id, name=upload_file_name, media_type=file.content_type
                ),
                file_content,
            )
        )
        file.file.seek(0)  # Reset file pointer for potential future use

    from google.cloud.storage.transfer_manager import THREAD

    transfer_manager.upload_many(upload_pairs, worker_type=THREAD)
    return uploaded_files


def download_video_verification(file_name: str) -> BinaryIO:
    bucket = client.bucket(settings.storage_bucket_name)
    blob = bucket.blob(f"{settings.storage_video_verification_path}{file_name}")
    file = io.BytesIO()
    blob.download_to_file(file)
    file.seek(0)
    return file


def download_verification_example_media(
    task_id: str, verification_examples: List[VerificationExampleMedia]
) -> List[Tuple[VerificationExampleMedia, BinaryIO]]:
    bucket = client.bucket(settings.storage_bucket_name)
    blob_file_pairs = []
    result = []
    for verification_example in verification_examples:
        blob = bucket.blob(
            f"{settings.storage_verification_example_media_path}"
            f"{task_id}/{verification_example.id}/{verification_example.name}"
        )
        file = io.BytesIO()
        blob_file_pairs.append((blob, file))
        result.append((verification_example, file))
    from google.cloud.storage.transfer_manager import THREAD

    transfer_manager.download_many(blob_file_pairs=blob_file_pairs, worker_type=THREAD)
    for _, file in blob_file_pairs:
        file.seek(0)
    return result


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


def build_verification_example_media_folder_path(
    task_id: str, example_folder_name: str
) -> str:
    return (
        f"{settings.storage_bucket_name}/{settings.storage_verification_example_media_path}"
        f"{task_id}/{example_folder_name}/"
    )


def delete_verification_example_media(folder_path: str) -> None:
    bucket = client.bucket(settings.storage_bucket_name)
    blobs = bucket.list_blobs(prefix=folder_path)
    bucket.delete_blobs(blobs=list(blobs))
    # for blob in blobs:
    #     blob = bucket.blob(blob.name)
    #     blob.delete()
