import asyncio
import logging
from typing import Awaitable, Callable, Dict, Optional

from google.api_core import retry
from google.api_core.exceptions import AlreadyExists, NotFound
from google.cloud.pubsub_v1 import PublisherClient, SubscriberClient
from google.cloud.pubsub_v1.subscriber.message import Message
from google.cloud.pubsub_v1.types import FlowControl
from google.pubsub_v1 import PublisherAsyncClient, SubscriberAsyncClient

AsyncCallable = Callable[[Message], Awaitable[None]]

logger = logging.getLogger(__name__)


class PubSubManager:
    """Singleton manager for Pub/Sub clients to ensure proper connection reuse."""

    _instance = None
    _publisher_client: Optional[PublisherAsyncClient] = None
    _subscriber_clients: Dict[str, SubscriberAsyncClient] = {}
    _sync_publisher: Optional[PublisherClient] = None
    _sync_subscriber: Optional[SubscriberClient] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    async def get_publisher(self) -> PublisherAsyncClient:
        """Get or create a singleton publisher client."""
        if self._publisher_client is None:
            self._publisher_client = PublisherAsyncClient()
            logger.info("Created new PublisherAsyncClient")
        return self._publisher_client

    def get_sync_publisher(self) -> PublisherClient:
        """Get or create a singleton sync publisher client for flow control."""
        if self._sync_publisher is None:
            self._sync_publisher = PublisherClient()
            logger.info("Created new sync PublisherClient")
        return self._sync_publisher

    def get_sync_subscriber(self) -> SubscriberClient:
        """Get or create a singleton sync subscriber client."""
        if self._sync_subscriber is None:
            # Configure flow control optimized for long-running tasks
            flow_control = FlowControl(
                max_messages=50,  # Reduced from 100 to prevent overwhelming with long tasks
                max_bytes=50 * 1024 * 1024,  # 50MB (reduced from 100MB)
                max_lease_duration=600,  # 10 minutes - matches our ack deadline
            )
            self._sync_subscriber = SubscriberClient()
            self._sync_subscriber._flow_control = flow_control
            logger.info(
                "Created new sync SubscriberClient with optimized flow control for long-running tasks"
            )
        return self._sync_subscriber

    async def close_all(self):
        """Close all clients gracefully."""
        if self._publisher_client:
            await self._publisher_client.transport.close()
            self._publisher_client = None
            logger.info("Closed PublisherAsyncClient")

        for subscription_path, client in self._subscriber_clients.items():
            await client.transport.close()
            logger.info(f"Closed SubscriberAsyncClient for {subscription_path}")
        self._subscriber_clients.clear()

        if self._sync_publisher:
            self._sync_publisher.transport.close()
            self._sync_publisher = None

        if self._sync_subscriber:
            self._sync_subscriber.transport.close()
            self._sync_subscriber = None


# Global instance
_manager = PubSubManager()


async def publish_message(
    project_id: str,
    topic_id: str,
    data: bytes,
    retry_timeout: float = 60.0,
) -> str:
    """
    Publish a message to a Pub/Sub topic with retry logic.

    Args:
        project_id: GCP project ID
        topic_id: Pub/Sub topic ID
        data: Message data as bytes
        retry_timeout: Total timeout for retries (default 60s)

    Returns:
        Message ID of the published message
    """
    publisher = await _manager.get_publisher()
    topic_path = publisher.topic_path(project_id, topic_id)

    # Configure retry with exponential backoff
    custom_retry = retry.AsyncRetry(
        initial=0.1,  # Start with 100ms
        maximum=10.0,  # Max 10 seconds between retries
        multiplier=2.0,  # Double the delay each retry
        timeout=retry_timeout,
        predicate=retry.if_exception_type(
            Exception,
        ),
    )

    try:
        # Publish with retry
        result = await publisher.publish(
            topic=topic_path,
            messages=[{"data": data}],
            retry=custom_retry,
        )
        message_id = result.message_ids[0]
        logger.debug(f"Published message {message_id} to {topic_path}")
        return message_id
    except Exception as e:
        logger.error(f"Failed to publish message to {topic_path}: {e}", exc_info=True)
        raise


async def initialize_subscriber(
    project_id: str,
    topic_id: str,
    subscription_id: str,
    callback: AsyncCallable,
    sequential_processing: bool = False,
) -> asyncio.Task:
    """
    Initialize a subscriber with improved error handling and flow control.

    Returns the asyncio Task running the subscriber.
    """
    subscriber = _manager.get_sync_subscriber()
    subscription_path = subscriber.subscription_path(project_id, subscription_id)

    # Ensure topic and subscription exist before subscribing
    publisher = await _manager.get_publisher()
    topic_path = publisher.topic_path(project_id, topic_id)
    await ensure_topic_exists(publisher, topic_path)
    await ensure_subscription_exists(subscriber, subscription_path, topic_path)

    # Create and return the subscription task
    task = asyncio.create_task(
        subscribe_with_flow_control(
            subscriber, subscription_path, callback, sequential_processing
        ),
        name=f"subscriber_{subscription_id}",
    )

    return task


async def subscribe_with_flow_control(
    subscriber: SubscriberClient,
    subscription_path: str,
    callback: AsyncCallable,
    sequential_processing: bool = False,
):
    """
    Subscribe to messages using the high-level streaming pull with flow control.
    """
    logger.info(f"Starting subscriber for {subscription_path}")

    # Create a wrapper for the Message to make it compatible with ReceivedMessage
    class MessageWrapper:
        def __init__(self, msg: Message):
            self._msg = msg
            self.ack_id = msg.ack_id
            self.message = self

        @property
        def data(self):
            return self._msg.data

        @property
        def message_id(self):
            return self._msg.message_id

        @property
        def attributes(self):
            return self._msg.attributes

        @property
        def publish_time(self):
            return self._msg.publish_time

        @property
        def delivery_attempt(self):
            return self._msg.delivery_attempt

    async def process_message(message: Message):
        """Process a single message and handle acknowledgment."""
        delivery_attempt = message.delivery_attempt

        try:
            # Wrap the message to make it compatible with ReceivedMessage interface
            wrapped_message = MessageWrapper(message)

            # Convert to async processing
            await callback(wrapped_message)

            # Acknowledge the message (this happens for both successful processing and early-ack scenarios)
            message.ack()
            logger.debug(
                f"Successfully processed and acked message {message.message_id}, delivery attempt: {delivery_attempt}"
            )

        except Exception as e:
            logger.error(
                f"Error processing message {message.message_id} (attempt {delivery_attempt}): {e}",
                exc_info=True,
            )

            # Nack the message to retry later (unless it's a known non-retryable error)
            message.nack()
            logger.warning(
                f"Nacked message {message.message_id} for retry, delivery attempt: {delivery_attempt}"
            )

    # Get the main event loop to schedule tasks on
    loop = asyncio.get_event_loop()

    # Streaming pull callback
    def callback_wrapper(message: Message):
        """Wrapper to handle sync/async bridge."""
        # Schedule the coroutine on the main event loop from this thread
        asyncio.run_coroutine_threadsafe(process_message(message), loop)

    # Configure flow control optimized for long-running tasks
    flow_control = FlowControl(
        max_messages=50,  # Reduced to prevent overwhelming with long-running tasks
        max_bytes=50 * 1024 * 1024,  # 50MB
        max_lease_duration=600,  # 10 minutes - matches ack deadline
    )

    # Start streaming pull
    streaming_pull_future = subscriber.subscribe(
        subscription_path,
        callback=callback_wrapper,
        flow_control=flow_control,
        await_callbacks_on_shutdown=True,
    )

    logger.info(f"Streaming pull started for {subscription_path}")

    # Keep the coroutine running
    try:
        async with asyncio.timeout(None):  # No timeout
            await asyncio.get_event_loop().run_in_executor(
                None, streaming_pull_future.result
            )
    except Exception as e:
        logger.error(
            f"Streaming pull error for {subscription_path}: {e}", exc_info=True
        )
        streaming_pull_future.cancel()
        raise


async def ensure_topic_exists(publisher: PublisherAsyncClient, topic_path: str):
    """Ensure a topic exists, creating it if necessary."""
    try:
        await publisher.get_topic(topic=topic_path)
        logger.debug(f"Topic {topic_path} already exists.")
    except NotFound:
        logger.info(f"Topic {topic_path} does not exist. Creating it.")
        try:
            await publisher.create_topic(name=topic_path)
            logger.info(f"Topic {topic_path} created.")
        except AlreadyExists:
            logger.debug(f"Topic {topic_path} already exists (race condition).")
        except Exception as e:
            logger.error(f"Error creating topic {topic_path}: {e}", exc_info=True)
            raise


async def ensure_subscription_exists(
    subscriber: SubscriberClient, subscription_path: str, topic_path: str
):
    """Ensure a subscription exists, creating it if necessary."""
    try:
        subscriber.get_subscription(subscription=subscription_path)
        logger.debug(f"Subscription {subscription_path} already exists.")
    except NotFound:
        logger.info(f"Subscription {subscription_path} does not exist. Creating it.")
        try:
            # Create with a reasonable ack deadline
            subscriber.create_subscription(
                request={
                    "name": subscription_path,
                    "topic": topic_path,
                    "ack_deadline_seconds": 60,  # 1 minute default
                    "enable_exactly_once_delivery": False,
                    "retry_policy": {
                        "minimum_backoff": {"seconds": 10},
                        "maximum_backoff": {"seconds": 600},
                    },
                }
            )
            logger.info(f"Subscription {subscription_path} created.")
        except AlreadyExists:
            logger.debug(
                f"Subscription {subscription_path} already exists (race condition)."
            )
        except Exception as e:
            logger.error(
                f"Error creating subscription {subscription_path}: {e}", exc_info=True
            )
            raise


async def close_subscriber(task: asyncio.Task) -> None:
    """Close a subscriber task gracefully."""
    if task and not task.done():
        logger.info(f"Cancelling subscriber task {task.get_name()}")
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            logger.info(f"Subscriber task {task.get_name()} cancelled successfully")

    # Close all clients
    await _manager.close_all()


# Compatibility functions for minimal code changes
def get_topic_path(project_id: str, topic_id: str) -> str:
    """Get the topic path."""
    return SubscriberAsyncClient.topic_path(project_id, topic_id)
