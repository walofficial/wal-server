import asyncio
import base64

import requests
from bson import ObjectId

from ment_api.common.custom_object_id import CustomObjectId
from ment_api.config import settings
from ment_api.persistence import mongo

credentials = f"{settings.twilio_account_sid}:{settings.twilio_auth_token}"

encoded_credentials = base64.b64encode(credentials.encode("utf-8")).decode("utf-8")

vercelAppService = "https://ment.app"

if settings.env == "dev":
    vercelAppService = "https://local.ment.ge"


async def send_to_device(token, title, message, data=None):
    if data is None:
        data = {}
    payload = {
        "to": token,
        "title": title,
        "sound": "default",
        "body": message,
        "data": data,
    }
    print(payload)
    headers = {
        "Content-Type": "application/json",
    }

    response = requests.post(
        "https://exp.host/--/api/v2/push/send",
        json=payload,
        headers=headers,
    )
    print(response.status_code)
    print(response.json())
    return response.status_code == 200


async def send_notification(user_id, title, message, data=None):
    # Find the push token document for the user
    subscription = await mongo.push_notification_tokens.find_one(
        {"ownerId": ObjectId(user_id)}
    )

    if subscription is None or "expo_push_token" not in subscription:
        logger.debug(f"No push token found for user {user_id}")
        return False

    # Get the expo push token
    unique_token = subscription["expo_push_token"]

    # Send the notification
    success = await send_to_device(
        token=unique_token, title=title, message=message, data=data
    )

    return success


async def send_new_match_push(user: dict):
    return asyncio.gather(
        send_notification(
            str(user["_id"]),
            "You have a new match!",
            "Someone liked you! Go check it out!",
        ),
        # send_new_sms_notification(
        #     user,
        #     """თქვენ გყავთ მეწყვილე! შეგიძლიათ დაიწყოთ დავალების შესრულება \n\nhttps://ment.ge""",
        # ),
    )


async def send_expired_notification(user_id):
    return await send_notification(
        user_id,
        "Your task is expired!",
        "You have a task that is expired! Go update it",
    )


async def send_new_sms_notification(user: dict, body):
    if user.get("email") and "@" in user.get("email"):
        return True
    elif "phone_number" in user:

        url = f"https://api.twilio.com/2010-04-01/Accounts/{settings.twilio_account_sid}/Messages.json"
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Basic {encoded_credentials}",
        }
        data = {
            "From": "MENT",
            "Body": body,
            "To": user["phone_number"],
        }

        response = requests.post(url, headers=headers, data=data)
        if response.status_code == 201:
            return True
        return False


async def notify_companion_user(match, initiator_user_id: CustomObjectId):
    companion_user_id = next(
        participant
        for participant in match["participants"]
        if participant != initiator_user_id
    )

    # if companion_user_id in match.get("task_completer_user_ids", []):
    #     message = """თქვენმა მეწყვილემ შეასრულა დავალება. უფასო ლუდის აღება შეგიძლიათ ნატახტარის ჯიხურში \n იხილეთ: https://ment.ge """
    # else:
    #     message = """თქვენმა მეწყვილემ შეასრულა დავალება. \n იხილეთ: https://ment.ge """

    initiator_user = await mongo.users.find_one_by_id(initiator_user_id)

    await asyncio.gather(
        # send_new_sms_notification(companion_user, message),
        send_notification(
            companion_user_id,
            initiator_user["username"],
            "შევასრულე დავალება",
        ),
    )


from typing import List
import asyncio
from datetime import datetime, timezone
import logging
from exponent_server_sdk import (
    DeviceNotRegisteredError,
    PushClient,
    PushMessage,
    PushServerError,
    PushTicketError,
)
from requests.exceptions import ConnectionError, HTTPError
import math

from ment_api.persistence import mongo

logger = logging.getLogger(__name__)

# Constants for rate limiting
BATCH_SIZE = 100  # Number of notifications to send in each batch
RATE_LIMIT = 600  # Maximum notifications per second
BATCH_DELAY = 1.0  # Delay between batches in seconds


async def send_pinned_post_notifications(verification_id: str, task_id: str):
    try:
        # Get the pinned verification details
        verification = await mongo.verifications.find_one({"_id": verification_id})
        if not verification:
            logger.error(f"Verification {verification_id} not found")
            return

        # Get task details
        task = await mongo.daily_picks.find_one({"_id": task_id})
        if not task:
            logger.error(f"Task {task_id} not found")
            return

        # Get all active users with their push tokens in one query
        current_time = datetime.now(timezone.utc)
        pipeline = [
            {"$match": {"expiration_date": {"$gt": current_time}}},
            {
                "$lookup": {
                    "from": "push-notification-tokens",
                    "localField": "author_id",
                    "foreignField": "ownerId",
                    "as": "push_token",
                }
            },
            {"$unwind": {"path": "$push_token", "preserveNullAndEmptyArrays": False}},
            {"$match": {"push_token.expo_push_token": {"$exists": True}}},
        ]

        active_users = await mongo.live_users.aggregate(pipeline)
        active_users = list(active_users)
        if not active_users:
            logger.info("No active users found")
            return

        # Prepare notification data
        notification_title = task.get("display_name", "ყურადღება")
        verification_text = verification.get("text_content", "")
        if verification_text == "" or verification_text is None:
            verification_text = "კონტენტი ლოკაციიდან"
        notification_body = f"{verification_text}"
        notification_data = {
            "type": "pinned_post",
            "verificationId": str(verification_id),
            "taskId": str(task_id),
        }

        # Group users into batches
        user_batches = [
            active_users[i : i + BATCH_SIZE]
            for i in range(0, len(active_users), BATCH_SIZE)
        ]

        logger.info(
            f"Sending notifications to {len(active_users)} users in {len(user_batches)} batches"
        )

        # Process each batch
        for batch_index, user_batch in enumerate(user_batches):
            try:
                notification_tasks = []

                for user in user_batch:
                    notification_tasks.append(
                        send_to_device(
                            token=user["push_token"]["expo_push_token"],
                            title=notification_title,
                            message=notification_body,
                            data=notification_data,
                        )
                    )

                # Send notifications in parallel within the batch
                await asyncio.gather(*notification_tasks)

                # Calculate delay to stay within rate limit
                if batch_index < len(user_batches) - 1:  # Don't delay after last batch
                    await asyncio.sleep(BATCH_DELAY)

                logger.info(f"Processed batch {batch_index + 1}/{len(user_batches)}")

            except Exception as e:
                logger.error(f"Error processing batch {batch_index + 1}: {str(e)}")
                continue

        logger.info("Finished sending pinned post notifications")

    except Exception as e:
        logger.error(f"Error in send_pinned_post_notifications: {str(e)}")
        raise
