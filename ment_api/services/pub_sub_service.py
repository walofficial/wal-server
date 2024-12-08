import asyncio
import logging
from typing import Callable, Awaitable

from google import pubsub_v1
from google.api_core.exceptions import NotFound, AlreadyExists, DeadlineExceeded
from google.pubsub_v1 import ReceivedMessage, SubscriberAsyncClient, SubscriberClient

AsyncCallable = Callable[[ReceivedMessage], Awaitable[None]]

logger = logging.getLogger(__name__)


async def initialize_subscriber(project_id: str, topic_id: str, subscription_id: str, callback: AsyncCallable) \
        -> SubscriberAsyncClient:
    subscriber = pubsub_v1.SubscriberAsyncClient()
    subscription_path = subscriber.subscription_path(project_id, subscription_id)
    topic_path = subscriber.topic_path(project_id, topic_id)
    await ensure_subscription_exists(subscriber, subscription_path, topic_path)
    _ = asyncio.create_task(subscribe(subscriber, subscription_path, callback))
    return subscriber


async def subscribe(subscriber: SubscriberAsyncClient, subscription_path: str, callback: AsyncCallable):
    while True:
        try:
            response = await subscriber.pull(
                subscription=subscription_path,
                max_messages=10,
                return_immediately=False,
                timeout=10
            )
            handler_tasks = [callback(message) for message in response.received_messages]
            await asyncio.gather(*handler_tasks)

            ack_ids = [message.ack_id for message in response.received_messages]
            await subscriber.acknowledge(
                subscription=subscription_path,
                ack_ids=ack_ids
            )
        except DeadlineExceeded:
            logger.info("No messages to be consumed. Continue trial...")
        except Exception as e:
            logger.error("Error pulling messages", exc_info=True)
            await asyncio.sleep(5)
        await asyncio.sleep(1)


async def ensure_subscription_exists(subscriber: SubscriberAsyncClient, subscription_path: str, topic_path: str):
    try:
        await subscriber.get_subscription(subscription=subscription_path)
        logger.info(f"Subscription {subscription_path} already exists.")
    except NotFound:
        logger.info(f"Subscription {subscription_path} does not exist. Creating it.")
        try:
            await subscriber.create_subscription(name=subscription_path, topic=topic_path)
            logger.info(f"Subscription {subscription_path} created.")
        except AlreadyExists:
            logger.info(f"Subscription {subscription_path} already exists after creation attempt.")
        except Exception:
            logger.error(f"Error creating subscription.", exc_info=True)


def get_topic_path(project_id: str, topic_id: str) -> str:
    return SubscriberClient.topic_path(project_id, topic_id)


async def close_subscriber(subscriber: SubscriberAsyncClient) -> None:
    await subscriber.transport.close()
