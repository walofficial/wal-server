from typing import List, Annotated

from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Query

from ment_api.common.custom_object_id import CustomObjectId
from ment_api.models.verification_example_media import VerificationExampleMedia
from ment_api.persistence import mongo
from ment_api.services.google_storage_service import (
    upload_verification_example_media,
    build_verification_example_media_folder_path,
    build_public_video_mp4_path,
    delete_verification_example_media,
)
from ment_api.services.transcoder_service import (
    create_transcode_jobs,
    wait_for_job_completion,
)

router = APIRouter(
    prefix="/verification-example-media",
    tags=["verification-example-media"],
    responses={404: {"description": "Not found"}},
)


@router.post("")
async def submit_media_example(
    example_media: Annotated[
        list[UploadFile], File(description="Multiple verification example files")
    ],
    task_id: Annotated[CustomObjectId, Form()],
) -> List:
    task_id_str = str(task_id)
    uploaded_files = upload_verification_example_media(task_id_str, example_media)
    input_output_uris = []
    task_verification_example_sources = []
    for file_info, blob in uploaded_files:
        transcoded_files = []
        image_source = ""

        needs_transcode = file_info.media_type.startswith("video")
        folder_path = build_verification_example_media_folder_path(
            task_id_str, file_info.id
        )
        playback = None

        if needs_transcode:
            output_path = f"gs://{folder_path}"
            input_path = f"{output_path}{file_info.name}"
            input_output_uris.append((input_path, output_path))
            public_manifest_path = f"https://storage.cloud.google.com/{folder_path}"
            public_video_mp4_path = build_public_video_mp4_path(file_info.name)
            transcoded_files = [
                f"{public_manifest_path}manifest.m3u8",
                f"{public_manifest_path}manifest.mpd",
            ]
            playback = {
                "hls": transcoded_files[0],
                "dash": transcoded_files[1],
                "mp4": public_video_mp4_path,
            }
        else:
            image_source = (
                f"https://storage.cloud.google.com/{folder_path}{file_info.name}"
            )

        task_verification_example_sources.append(
            VerificationExampleMedia(
                id=file_info.id,
                name=file_info.name,
                media_type=file_info.media_type,
                playback=playback,
                thumbnail_url="",
                image_media_url=image_source,
            )
        )

    job_names = create_transcode_jobs(input_output_uris)
    for job_name in job_names:
        await wait_for_job_completion(job_name)
    await mongo.daily_picks.update_one(
        {
            "_id": task_id,
        },
        {
            "$push": {
                "task_verification_example_sources": {
                    "$each": [
                        item.model_dump() for item in task_verification_example_sources
                    ]
                }
            }
        },
    )
    return job_names


@router.delete("")
async def delete_verification_example(
    task_id: Annotated[CustomObjectId, Query()], task_example_id: str = Query()
) -> None:
    folder_path = build_verification_example_media_folder_path(
        str(task_id), task_example_id
    )
    delete_verification_example_media(folder_path.replace("ment-verification/", ""))

    result = await mongo.daily_picks.update_one(
        {"_id": task_id},
        {"$pull": {"task_verification_example_sources": {"id": task_example_id}}},
    )
    if result.modified_count == 0:
        raise HTTPException(404, "Could not delete verification example.")
