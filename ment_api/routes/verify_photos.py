from pathlib import Path
from typing import BinaryIO, Annotated

from fastapi import APIRouter, UploadFile, Form, Header, HTTPException, File

from ment_api.common.custom_object_id import CustomObjectId
from ment_api.common.utils import get_file_name_and_extension
from ment_api.models.media_processing_result import MediaProcessingResult
from ment_api.models.verification_state import VerificationState
from ment_api.persistence import mongo
from ment_api.services.external_clients.cloud_flare_client import upload_image
from ment_api.services.verification_service import (
    execute_file_verification,
    process_image,
)
from typing import Optional
from datetime import datetime, timezone
from ment_api.models.locaiton_feed_post import LocationFeedPost
from ment_api.services.external_clients.openai_client import (
    get_completion_from_messages,
)
import random
from ment_api.services.image_service import generate_and_upload
import asyncio

router = APIRouter(
    prefix="/verify-photos",
    tags=["verify-photos"],
    responses={404: {"description": "Not found"}},
)

allowed_content_types = ["image/jpeg", "image/png", "image/webp"]


@router.post(
    "",
    responses={500: {"description": "Generation error"}},
)
async def verify_photos(
    x_user_id: Annotated[CustomObjectId, Header()],
    photo_file: Annotated[
        UploadFile, File(media_type="image/jpeg", description="verification image")
    ],
    match_id: Annotated[CustomObjectId, Form(...)],
):
    if photo_file.content_type not in allowed_content_types:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file type. Expected {allowed_content_types}.",
        )
    file_name, file_extension = get_file_name_and_extension(photo_file)

    file_bytes = photo_file.file.read()
    image_url = await upload_image(file_bytes)

    inserted = await mongo.verifications.insert_one(
        {
            "match_id": match_id,
            "assignee_user_id": x_user_id,
            "file_content_type": "image",
            "file_name": file_name,
            "verified_image": image_url,
        },
    )

    extra_data = {"image_url": image_url, "verification_id": inserted.inserted_id}

    photo_file.file.seek(0)

    result = await execute_file_verification(
        photo_file.file,
        file_name,
        file_extension,
        photo_file.content_type,
        match_id,
        x_user_id,
        process_image,
        extra_data,
    )

    return result


@router.post(
    "/upload-to-location",
    responses={500: {"description": "Generation error"}},
)
async def upload_photo_to_location(
    x_user_id: Annotated[CustomObjectId, Header()],
    photo_file: Annotated[
        UploadFile, File(media_type="image/jpeg", description="verification image")
    ],
    task_id: Annotated[CustomObjectId, Form(...)],
    text_content: Annotated[Optional[str], Form()] = None,
):
    if photo_file.content_type not in allowed_content_types:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file type. Expected {allowed_content_types}.",
        )
    file_name, file_extension = get_file_name_and_extension(photo_file)

    file_bytes = photo_file.file.read()
    dest_file_extension = ".jpeg"
    dest_file_full_name = f"{file_name}{dest_file_extension}"

    upload_url = upload_image(
        file=file_bytes,
        destination_file_name=dest_file_full_name,
        content_type=photo_file.content_type,
    )

    insert_doc = {
        "task_id": task_id,
        "assignee_user_id": x_user_id,
        "file_content_type": "image",
        "file_name": file_name,
        "verified_image": upload_url,
        "state": VerificationState.READY_FOR_USE,
        "last_modified_date": datetime.now(timezone.utc),
        "text_content": text_content,
    }

    verification_doc = await mongo.verifications.insert_one(insert_doc)

    extra_data = {
        "image_url": upload_url,
        "verification_id": verification_doc.inserted_id,
    }

    photo_file.file.seek(0)

    result = await execute_file_verification(
        photo_file.file,
        file_name,
        file_extension,
        photo_file.content_type,
        task_id,
        x_user_id,
        process_image,
        extra_data,
    )

    insert_doc["_id"] = verification_doc.inserted_id

    return {
        "uploaded_file_url": upload_url,
        "verification": LocationFeedPost(**insert_doc),
    }


@router.post(
    "/generate-random-post",
    responses={500: {"description": "Generation error"}},
)
async def generate_random_post(
    task_id: Annotated[CustomObjectId, Form(...)],
    theme: Annotated[str, Form(...)],
    num_times: Annotated[int, Form(...)] = 1,
):
    async def generate_single_post():
        # Get random user from users collection
        [random_user] = await mongo.users.aggregate([{"$sample": {"size": 1}}])

        # Generate post content using OpenAI
        content_prompt = f"""
        Generate a short social media post about {theme}. 
        The post should:
        1. Be written in first person
        2. Be casual and friendly in tone
        3. Be no longer than 2-3 sentences
        4. Be appropriate for a social platform
        5. Relate to the theme: {theme}
        6. Generate it in Georgian language
        7. Do not use hashtags
        8. very few emojis
        9. Text should be written in real time of what's happening in the situation, image post was made during what happened at that moment or today. It's real time post.

         Examples for theme like protest in Georgia:

         პროტესტი ამჯერად კიდევ მიმდინარეობს
         ოპოზიციონერები გამოვიდნენ პროტესტზე
            ძალადობას აქვს ადგილი პროტესტზე
        """

        text_content = await get_completion_from_messages(
            [
                {
                    "role": "system",
                    "content": "You are a helpful assistant who writes social media posts",
                },
                {"role": "user", "content": content_prompt},
            ]
        )

        # Generate image prompt using OpenAI
        image_prompt_request = f"""
        Generate a prompt for an AI image generator to create an image that:
        1. Shows a scene related to {theme}
        2. Should be in a casual iPhone camera photo
        3. Should look like it was taken with an iPhone camera app
        4. Should not include any text or words
        5. Should be from rear or front camera perspective
        6. Use example prompts like:
         People protesting in front of the old soviet building, captured with iPhone camera, the EU Flag
         Police clashing with people during protests
         Police clashing with people during protests, police using tear gas
         Mass protests in the country of Georgia, Tbilisi, drone view, thousands of people protesting visible in front of the building
         Protests in Tbilisi, people blocking the main roads of the city
        7. Do not start with Capture a photo of..., Create a photo of..., or anything similar.
        8. Do not include Russian flag, only Georgian, georgian flag is one with 4 red cross and one big cross in the centre
        """

        image_prompt = await get_completion_from_messages(
            [
                {
                    "role": "system",
                    "content": "You are a helpful assistant who creates image generation prompts",
                },
                {"role": "user", "content": image_prompt_request},
            ]
        )
        print(image_prompt)

        # Generate image using fal.ai
        image_result = await generate_and_upload(image_prompt)

        # Create verification/post document
        insert_doc = {
            "task_id": task_id,
            "assignee_user_id": random_user["_id"],
            "file_content_type": "image",
            "file_name": f"generated_post_{random.randint(1000, 9999)}",
            "verified_image": image_result["image_url"][0],
            "state": VerificationState.READY_FOR_USE,
            "last_modified_date": datetime.now(timezone.utc),
            "text_content": text_content,
            "is_public": True,
        }

        verification_doc = await mongo.verifications.insert_one(insert_doc)
        insert_doc["_id"] = verification_doc.inserted_id

        return {
            "uploaded_file_url": image_result["image_url"][0],
            "verification": LocationFeedPost(**insert_doc),
        }

    # Create tasks for parallel execution
    tasks = [generate_single_post() for _ in range(num_times)]

    # Execute all tasks in parallel and wait for results
    results = await asyncio.gather(*tasks)

    return results
