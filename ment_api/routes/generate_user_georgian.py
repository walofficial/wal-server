import uuid
import random
from fastapi import APIRouter, Body, Query
from ment_api.persistence import mongo
from ment_api.services.user_service import generate_user
from ment_api.common.custom_object_id import CustomObjectId
from datetime import datetime, timezone, timedelta
from typing import Optional, Annotated
from ment_api.persistence.mongo_client import client, db
from ment_api.services.image_service import generates_images_and_upload_from_list

router = APIRouter(
    prefix="/generate-user-georgian",
    tags=["generate-user-georgian"],
    responses={404: {"description": "Not found"}},
)


# INSERT_YOUR_REWRITE_HERE
@router.post(
    "",
    responses={500: {"description": "Generation error"}},
)
async def generate():
    import asyncio

    async def generate_single_user():
        generated_user = await generate_user(is_georgian=True)
        id = str(uuid.uuid4())
        generated_user["external_user_id"] = id
        generated_user["phone_number"] = (
            f"+995{''.join([str(random.randint(0, 9)) for _ in range(9)])}"
        )

        inserted_user = await mongo.users.insert_one(generated_user)

        return {"ok": True}

    tasks = [generate_single_user() for _ in range(10)]
    results = await asyncio.gather(*tasks)

    return results


import random
import string


def generate_random_username(
    words, length=2, include_numbers=True, include_symbols=False
):
    """
    Generates a random username based on a list of words.

    Args:
        words (list): A list of words to use for generating the username.
        length (int): The number of words to combine. Default is 2.
        include_numbers (bool): Whether to append random numbers to the username. Default is True.
        include_symbols (bool): Whether to append random symbols to the username. Default is False.

    Returns:
        str: A randomly generated username.
    """
    if not words or length < 1:
        raise ValueError("Please provide a valid list of words and a positive length.")

    # Select random words
    selected_words = random.sample(words, min(len(words), length))
    username = "".join(selected_words)

    # Optionally append numbers
    if include_numbers:
        username += str(random.randint(10, 99))  # Add a two-digit random number

    # Optionally append symbols
    if include_symbols:
        username += random.choice(string.punctuation)

    return username


# Example usage
words_list = [
    "apple",
    "banana",
    "cat",
    "dog",
    "elephant",
    "flower",
    "guitar",
    "house",
    "island",
    "jacket",
    "key",
    "lamp",
    "moon",
    "notebook",
    "ocean",
    "pencil",
    "queen",
    "rainbow",
    "star",
    "tree",
    "umbrella",
    "violin",
    "window",
    "xylophone",
    "yacht",
    "zebra",
    "ana",
    "baia",
    "dali",
    "dato",
    "eka",
    "elene",
    "giorgi",
    "goga",
    "gvantsa",
    "irakli",
    "keti",
    "lali",
    "lasha",
    "levani",
    "mari",
    "mariam",
    "nana",
    "nika",
    "nino",
    "salome",
    "sandro",
    "tamari",
    "tato",
    "temo",
    "tornike",
    "vako",
    "zura",
]


@router.post(
    "/with-challenge",
    responses={500: {"description": "Generation error"}},
)
async def generate_with_challenge(task_id: Optional[Annotated[CustomObjectId, Body()]]):
    import asyncio

    async def generate_single_user_with_challenge():
        generated_user = await generate_user(is_georgian=True)
        id = str(uuid.uuid4())
        generated_user["external_user_id"] = id
        random_phone_number = (
            f"+995{''.join([str(random.randint(0, 9)) for _ in range(9)])}"
        )
        generated_user["phone_number"] = random_phone_number
        generated_user["email"] = random_phone_number
        generated_user["username"] = generate_random_username(
            words_list, length=3, include_numbers=True, include_symbols=True
        )
        generated_user["photos"] = await generates_images_and_upload_from_list(
            generated_user["photo_prompts"]
        )

        async with await client.start_session() as session:
            async with session.start_transaction():
                inserted_user = await mongo.users.insert_one(
                    generated_user, session=session
                )
                user_id = inserted_user.inserted_id

                expiration_date = datetime.now(timezone.utc) + timedelta(hours=3)

                challenge_request = {
                    "author_id": user_id,
                    "task_id": task_id,
                    "expiration_date": expiration_date,
                    "created_at": datetime.now(timezone.utc),
                }

                result = await mongo.live_users.insert_one(
                    challenge_request, session=session
                )
                live_user_id = result.inserted_id

                # Create expiration task
                from ment_api.services.external_clients.google_client import task_expire

                expiration_task_name = f"expire-live-{live_user_id}"
                expiration_task = task_expire.create_task(
                    in_seconds=timedelta(hours=3).total_seconds(),
                    path="/tasks/live/expire",
                    payload={"live_doc_id": str(live_user_id)},
                    task_name=expiration_task_name,
                )

                await mongo.live_users.update_one(
                    {"_id": live_user_id},
                    {"$set": {"task_expire_id": expiration_task_name}},
                    session=session,
                )

        return {"ok": True, "user_id": str(user_id), "challenge_id": str(live_user_id)}

    tasks = [generate_single_user_with_challenge() for _ in range(30)]
    results = await asyncio.gather(*tasks)

    return results
