import asyncio
import base64
import logging
from contextlib import asynccontextmanager

import httpx
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ment_api.configurations.config import settings
from ment_api.persistence import mongo

credentials = f"{settings.twilio_account_sid}:{settings.twilio_auth_token}"

encoded_credentials = base64.b64encode(credentials.encode("utf-8")).decode("utf-8")

vercelAppService = "https://ment.app"

if settings.env == "dev":
    vercelAppService = "https://local.wal.ge"

logger = logging.getLogger(__name__)

# HTTP retry decorator following the existing codebase pattern
http_retry = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=(
        retry_if_exception_type((httpx.RequestError,))
        | retry_if_exception(
            lambda e: isinstance(e, httpx.HTTPStatusError)
            and e.response.status_code in [429, 502, 503, 504]
        )
    ),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)


@asynccontextmanager
async def get_expo_push_client():
    """Context manager for Expo push notifications HTTP client"""
    client = None
    try:
        client_config = {
            "headers": {
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            "timeout": httpx.Timeout(
                connect=30.0,
                read=60.0,
                write=30.0,
                pool=90.0,
            ),
            "http2": False,
            "follow_redirects": True,
            "limits": httpx.Limits(
                max_keepalive_connections=5, max_connections=10, keepalive_expiry=30.0
            ),
        }

        client = httpx.AsyncClient(
            base_url="https://exp.host/--/api/v2/push/send", **client_config
        )
        yield client
    finally:
        if client:
            await client.aclose()


@asynccontextmanager
async def get_twilio_client():
    """Context manager for Twilio SMS HTTP client"""
    client = None
    try:
        client_config = {
            "headers": {
                "Content-Type": "application/x-www-form-urlencoded",
                "Authorization": f"Basic {encoded_credentials}",
            },
            "timeout": httpx.Timeout(
                connect=30.0,
                read=60.0,
                write=30.0,
                pool=90.0,
            ),
            "http2": False,
            "follow_redirects": True,
            "limits": httpx.Limits(
                max_keepalive_connections=5, max_connections=10, keepalive_expiry=30.0
            ),
        }

        client = httpx.AsyncClient(
            base_url=f"https://api.twilio.com/2010-04-01/Accounts/{settings.twilio_account_sid}/Messages.json",
            **client_config,
        )
        yield client
    finally:
        if client:
            await client.aclose()


@http_retry
async def send_to_device(token, title, message, data=None, client=None):
    if data is None:
        data = {}
    payload = {
        "to": token,
        "title": title,
        "sound": "default",
        "body": message,
        "data": data,
    }
    logger.debug(f"Sending push notification payload: {payload}")

    try:
        if client:
            # Use provided client
            response = await client.post("", json=payload)
            response.raise_for_status()
            return response.status_code == 200
        else:
            # Fallback to creating new client (for backward compatibility)
            async with get_expo_push_client() as new_client:
                response = await new_client.post("", json=payload)
                response.raise_for_status()
                return response.status_code == 200
    except httpx.RequestError as exc:
        logger.warning(f"Request error occurred while sending push notification: {exc}")
        raise
    except httpx.HTTPStatusError as exc:
        logger.error(
            f"HTTP error {exc.response.status_code} while sending push notification"
        )
        raise


async def send_notification(user_id, title, message, data=None):
    # Find the push token document for the user
    subscription = await mongo.push_notification_tokens.find_one({"ownerId": user_id})

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


@http_retry
async def send_new_sms_notification(user: dict, body, client=None):
    if user.get("email") and "@" in user.get("email"):
        return True
    elif "phone_number" in user:
        data = {
            "From": "MENT",
            "Body": body,
            "To": user["phone_number"],
        }

        try:
            if client:
                # Use provided client
                response = await client.post("", data=data)
                response.raise_for_status()
                return response.status_code == 201
            else:
                # Fallback to creating new client (for backward compatibility)
                async with get_twilio_client() as new_client:
                    response = await new_client.post("", data=data)
                    response.raise_for_status()
                    return response.status_code == 201
        except httpx.RequestError as exc:
            logger.warning(f"Request error occurred while sending SMS: {exc}")
            raise
        except httpx.HTTPStatusError as exc:
            logger.error(f"HTTP error {exc.response.status_code} while sending SMS")
            raise
    return False


# Constants for rate limiting
BATCH_SIZE = 100  # Number of notifications to send in each batch
RATE_LIMIT = 600  # Maximum notifications per second
BATCH_DELAY = 1.0  # Delay between batches in seconds


async def send_global_notifications(title: str, description: str):
    try:
        # Get all users with push tokens in one query
        pipeline = [
            {
                "$lookup": {
                    "from": "push-notification-tokens",
                    "localField": "_id",
                    "foreignField": "ownerId",
                    "as": "push_token",
                }
            },
            {"$unwind": {"path": "$push_token", "preserveNullAndEmptyArrays": False}},
            {"$match": {"push_token.expo_push_token": {"$exists": True}}},
        ]

        users_with_tokens = await mongo.users.aggregate(pipeline)
        users_with_tokens = list(users_with_tokens)

        if not users_with_tokens:
            logger.info("No users with push tokens found")
            return

        # Prepare notification data
        notification_data = {
            "type": "global_notification",
        }

        # Remove duplicate tokens
        seen_tokens = set()
        unique_users = []
        for user in users_with_tokens:
            token = user["push_token"]["expo_push_token"]
            if token not in seen_tokens:
                seen_tokens.add(token)
                unique_users.append(user)

        # Group users into batches
        user_batches = [
            unique_users[i : i + BATCH_SIZE]
            for i in range(0, len(unique_users), BATCH_SIZE)
        ]

        logger.info(
            f"Sending notifications to {len(unique_users)} users in {len(user_batches)} batches"
        )

        # Process each batch
        for batch_index, user_batch in enumerate(user_batches):
            try:
                # Create a single HTTP client for this batch
                async with get_expo_push_client() as batch_client:
                    notification_tasks = []

                    for user in user_batch:
                        notification_tasks.append(
                            send_to_device(
                                token=user["push_token"]["expo_push_token"],
                                title=title,
                                message=description,
                                data=notification_data,
                                client=batch_client,
                            )
                        )

                    # Send notifications in parallel within the batch using shared client
                    await asyncio.gather(*notification_tasks)

                    # Calculate delay to stay within rate limit
                    if (
                        batch_index < len(user_batches) - 1
                    ):  # Don't delay after last batch
                        await asyncio.sleep(BATCH_DELAY)

                    logger.info(
                        f"Processed batch {batch_index + 1}/{len(user_batches)}"
                    )

            except Exception as e:
                logger.error(f"Error processing batch {batch_index + 1}: {str(e)}")
                continue

        logger.info("Finished sending global notifications")

    except Exception as e:
        logger.error(f"Error in send_global_notifications: {str(e)}")
        raise
