import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import timedelta
from functools import partial
from typing import Awaitable, Callable, Dict, Optional

from google.api_core.exceptions import AlreadyExists, GoogleAPICallError, NotFound
from google.cloud.pubsub_v1 import PublisherClient, SubscriberClient
from google.cloud.pubsub_v1.subscriber.futures import StreamingPullFuture
from google.cloud.pubsub_v1.subscriber.message import Message
from google.cloud.pubsub_v1.subscriber.scheduler import ThreadScheduler
from google.cloud.pubsub_v1.types import FlowControl
from google.pubsub_v1 import PublisherAsyncClient, SubscriberAsyncClient
from google.pubsub_v1.types import (
    DeadLetterPolicy,
    ExpirationPolicy,
    RetryPolicy,
    Subscription,
)

# Callback contract: async function that receives the native high-level Message.
# Acknowledgement is owned by the supervisor, NOT the callback.
AsyncCallable = Callable[[Message], Awaitable[None]]

logger = logging.getLogger(__name__)

# Retry policy applied to created/updated subscriptions (server-side redelivery).
_MIN_BACKOFF = timedelta(seconds=10)
_MAX_BACKOFF = timedelta(seconds=600)

# Per-subscription publish/flow-control byte budget.
_MAX_BYTES = 50 * 1024 * 1024  # 50 MB


class _PubSubClients:
    """Owns the shared Pub/Sub clients. Created lazily, closed exactly once.

    - ``publisher`` (high-level, batching) is used for publishing.
    - ``subscriber`` (high-level, threaded streaming pull) is used for ``subscribe``.
    - ``async_subscriber`` / ``async_publisher`` (gapic async) are used only for
      awaitable admin calls (get/create/update subscription, get/create topic) so
      startup never blocks a thread.
    """

    def __init__(self) -> None:
        self._publisher: Optional[PublisherClient] = None
        self._subscriber: Optional[SubscriberClient] = None
        self._async_subscriber: Optional[SubscriberAsyncClient] = None
        self._async_publisher: Optional[PublisherAsyncClient] = None

    @property
    def publisher(self) -> PublisherClient:
        if self._publisher is None:
            self._publisher = PublisherClient()
            logger.info(
                "Created high-level PublisherClient",
                extra={
                    "json_fields": {"operation": "pubsub_client_init"},
                    "labels": {"component": "pubsub"},
                },
            )
        return self._publisher

    @property
    def subscriber(self) -> SubscriberClient:
        if self._subscriber is None:
            self._subscriber = SubscriberClient()
            logger.info(
                "Created high-level SubscriberClient",
                extra={
                    "json_fields": {"operation": "pubsub_client_init"},
                    "labels": {"component": "pubsub"},
                },
            )
        return self._subscriber

    @property
    def async_subscriber(self) -> SubscriberAsyncClient:
        if self._async_subscriber is None:
            self._async_subscriber = SubscriberAsyncClient()
        return self._async_subscriber

    @property
    def async_publisher(self) -> PublisherAsyncClient:
        if self._async_publisher is None:
            self._async_publisher = PublisherAsyncClient()
        return self._async_publisher

    async def close(self) -> None:
        """Best-effort close of every client. Never raises."""
        if self._publisher is not None:
            try:
                self._publisher.stop()
            except Exception:
                logger.warning(
                    "Failed to stop PublisherClient",
                    extra={
                        "json_fields": {"operation": "pubsub_client_close"},
                        "labels": {"component": "pubsub"},
                    },
                    exc_info=True,
                )
            self._publisher = None

        if self._subscriber is not None:
            try:
                self._subscriber.close()
            except Exception:
                logger.warning(
                    "Failed to close SubscriberClient",
                    extra={
                        "json_fields": {"operation": "pubsub_client_close"},
                        "labels": {"component": "pubsub"},
                    },
                    exc_info=True,
                )
            self._subscriber = None

        for client in (self._async_subscriber, self._async_publisher):
            if client is None:
                continue
            try:
                await client.transport.close()
            except Exception:
                logger.warning(
                    "Failed to close async admin client",
                    extra={
                        "json_fields": {"operation": "pubsub_client_close"},
                        "labels": {"component": "pubsub"},
                    },
                    exc_info=True,
                )
        self._async_subscriber = None
        self._async_publisher = None


_clients = _PubSubClients()


async def publish_message(
    project_id: str,
    topic_id: str,
    data: bytes,
    retry_timeout: float = 60.0,
    *,
    ordering_key: str = "",
    attributes: Optional[Dict[str, str]] = None,
) -> str:
    """Publish a single message and return its message id.

    Non-blocking: the high-level publisher commits the batch on its own
    background thread; we bridge its ``concurrent.futures.Future`` to asyncio
    via ``asyncio.wrap_future`` without occupying an event-loop executor thread.
    """
    publisher = _clients.publisher
    topic_path = publisher.topic_path(project_id, topic_id)

    future = publisher.publish(
        topic_path,
        data,
        ordering_key=ordering_key,
        timeout=retry_timeout,
        **(attributes or {}),
    )

    try:
        message_id = await asyncio.wrap_future(future)
        logger.debug(
            "Published message",
            extra={
                "json_fields": {
                    "operation": "pubsub_publish",
                    "topic_id": topic_id,
                    "message_id": message_id,
                },
                "labels": {"component": "pubsub"},
            },
        )
        return message_id
    except Exception as e:
        logger.error(
            "Failed to publish message",
            extra={
                "json_fields": {
                    "operation": "pubsub_publish_error",
                    "topic_id": topic_id,
                    "error": str(e),
                    "error_type": type(e).__name__,
                },
                "labels": {"component": "pubsub", "severity": "high"},
            },
            exc_info=True,
        )
        raise


async def _ensure_topic_exists(project_id: str, topic_id: str) -> str:
    """Idempotently ensure a topic exists (async admin). Returns the topic path."""
    publisher = _clients.async_publisher
    topic_path = publisher.topic_path(project_id, topic_id)
    try:
        await publisher.get_topic(topic=topic_path)
        return topic_path
    except NotFound:
        try:
            await publisher.create_topic(name=topic_path)
            logger.info(
                "Created Pub/Sub topic",
                extra={
                    "json_fields": {
                        "operation": "pubsub_topic_create",
                        "topic_id": topic_id,
                    },
                    "labels": {"component": "pubsub"},
                },
            )
        except AlreadyExists:
            pass
    return topic_path


async def _apply_subscription_config(
    spec: "SubscriberSpec",
    project_id: str,
) -> None:
    """Ensure the subscription exists with tuned ack deadline, retry policy and
    (optionally) a dead-letter policy. Idempotent and safe to call on every start.

    Best-effort updates on existing subscriptions are guarded: an IAM/permission
    failure while attaching a dead-letter policy will not block startup.
    """
    subscriber = _clients.async_subscriber
    subscription_path = subscriber.subscription_path(project_id, spec.subscription_id)
    topic_path = _clients.async_publisher.topic_path(project_id, spec.topic_id)

    retry_policy = RetryPolicy(minimum_backoff=_MIN_BACKOFF, maximum_backoff=_MAX_BACKOFF)

    dead_letter_policy: Optional[DeadLetterPolicy] = None
    if spec.dlq_topic_id:
        dlq_topic_path = await _ensure_topic_exists(project_id, spec.dlq_topic_id)
        dead_letter_policy = DeadLetterPolicy(
            dead_letter_topic=dlq_topic_path,
            max_delivery_attempts=spec.max_delivery_attempts,
        )

    try:
        await subscriber.get_subscription(subscription=subscription_path)
        exists = True
    except NotFound:
        exists = False

    if not exists:
        subscription = Subscription(
            name=subscription_path,
            topic=topic_path,
            ack_deadline_seconds=spec.ack_deadline_seconds,
            retry_policy=retry_policy,
            enable_exactly_once_delivery=False,
            # Empty ttl => never expire, so an idle subscriber never loses its
            # config/backlog (default would auto-delete after 31 days).
            expiration_policy=ExpirationPolicy(),
        )
        if dead_letter_policy is not None:
            subscription.dead_letter_policy = dead_letter_policy
        try:
            await subscriber.create_subscription(request=subscription)
            logger.info(
                "Created subscription",
                extra={
                    "json_fields": {
                        "operation": "pubsub_subscription_create",
                        "subscription_id": spec.subscription_id,
                        "ack_deadline_seconds": spec.ack_deadline_seconds,
                        "dlq_enabled": dead_letter_policy is not None,
                    },
                    "labels": {"component": "pubsub"},
                },
            )
        except AlreadyExists:
            pass
        return

    # Existing subscription: bring ack deadline, retry policy and expiration in
    # line (safe fields). Empty ttl => never expire.
    await _update_subscription(
        subscription_path,
        Subscription(
            name=subscription_path,
            ack_deadline_seconds=spec.ack_deadline_seconds,
            retry_policy=retry_policy,
            expiration_policy=ExpirationPolicy(),
        ),
        ["ack_deadline_seconds", "retry_policy", "expiration_policy"],
        spec,
    )

    # Attach the dead-letter policy separately so an IAM/topic error here does not
    # prevent the ack-deadline / retry tuning above from taking effect.
    if dead_letter_policy is not None:
        await _update_subscription(
            subscription_path,
            Subscription(name=subscription_path, dead_letter_policy=dead_letter_policy),
            ["dead_letter_policy"],
            spec,
        )


async def _update_subscription(
    subscription_path: str,
    subscription: Subscription,
    paths: list[str],
    spec: "SubscriberSpec",
) -> None:
    subscriber = _clients.async_subscriber
    try:
        await subscriber.update_subscription(
            request={"subscription": subscription, "update_mask": {"paths": paths}}
        )
    except GoogleAPICallError as e:
        logger.warning(
            "Failed to update subscription config (non-fatal). If this is a "
            "dead-letter policy update, grant the Pub/Sub service agent "
            "roles/pubsub.publisher on the DLQ topic and roles/pubsub.subscriber "
            "on the subscription.",
            extra={
                "json_fields": {
                    "operation": "pubsub_subscription_update_error",
                    "subscription_id": spec.subscription_id,
                    "update_paths": paths,
                    "error": str(e),
                    "error_type": type(e).__name__,
                },
                "labels": {"component": "pubsub", "severity": "medium"},
            },
        )


@dataclass(frozen=True)
class SubscriberSpec:
    """Declarative description of a single subscription.

    Intentionally a frozen dataclass (not Pydantic): this is a static in-code
    registry entry that holds a live coroutine callback, not validated wire data.
    """

    name: str
    topic_id: str
    subscription_id: str
    callback: AsyncCallable
    max_concurrency: int = 4
    ack_deadline_seconds: int = 60
    dlq_topic_id: Optional[str] = None
    max_delivery_attempts: int = 5
    enabled: bool = True


class SubscriberSupervisor:
    """Owns the lifecycle of all streaming-pull subscriptions.

    Responsibilities:
    - start each subscription with a dedicated, bounded ``ThreadScheduler`` so
      concurrency is capped per subscription and never touches asyncio's shared
      default executor (the previous design's hang);
    - bridge the library's sync callback thread to the asyncio loop and own
      ack/nack based on the coroutine outcome;
    - auto-heal: re-subscribe with backoff if a stream dies unexpectedly;
    - cancel + drain every stream exactly once and close clients on shutdown.
    """

    _MAX_RESTART_BACKOFF = 60

    def __init__(self, project_id: str, loop: asyncio.AbstractEventLoop) -> None:
        self._project_id = project_id
        self._loop = loop
        self._specs: Dict[str, SubscriberSpec] = {}
        self._futures: Dict[str, StreamingPullFuture] = {}
        self._schedulers: Dict[str, ThreadScheduler] = {}
        self._restart_attempts: Dict[str, int] = {}
        self._shutting_down = False

    async def start(self, spec: SubscriberSpec) -> None:
        self._specs[spec.name] = spec
        await self._subscribe(spec)

    async def _subscribe(self, spec: SubscriberSpec) -> None:
        await _apply_subscription_config(spec, self._project_id)

        subscriber = _clients.subscriber
        subscription_path = subscriber.subscription_path(
            self._project_id, spec.subscription_id
        )

        scheduler = ThreadScheduler(
            ThreadPoolExecutor(
                max_workers=spec.max_concurrency,
                thread_name_prefix=f"pubsub-{spec.name}",
            )
        )
        flow_control = FlowControl(
            max_messages=spec.max_concurrency,
            max_bytes=_MAX_BYTES,
            max_lease_duration=max(spec.ack_deadline_seconds, 600),
        )

        future = subscriber.subscribe(
            subscription_path,
            callback=self._make_callback(spec),
            flow_control=flow_control,
            scheduler=scheduler,
            await_callbacks_on_shutdown=True,
        )
        future.add_done_callback(partial(self._on_future_done, spec.name))

        self._futures[spec.name] = future
        self._schedulers[spec.name] = scheduler

        logger.info(
            "Subscriber started",
            extra={
                "json_fields": {
                    "operation": "pubsub_subscriber_start",
                    "subscriber": spec.name,
                    "subscription_id": spec.subscription_id,
                    "max_concurrency": spec.max_concurrency,
                    "ack_deadline_seconds": spec.ack_deadline_seconds,
                    "dlq_enabled": spec.dlq_topic_id is not None,
                },
                "labels": {"component": "pubsub"},
            },
        )

    def _make_callback(self, spec: SubscriberSpec) -> Callable[[Message], None]:
        loop = self._loop
        async_callback = spec.callback
        name = spec.name

        def _callback(message: Message) -> None:
            # Runs on the library's own per-subscription scheduler thread.
            # Blocking on ``.result()`` here (not the loop, not the shared
            # executor) is what provides real backpressure: the message stays
            # outstanding until processing finishes, bounded by max_concurrency.
            future = asyncio.run_coroutine_threadsafe(async_callback(message), loop)
            try:
                future.result()
                message.ack()
            except Exception as e:
                logger.error(
                    "Message processing failed; nacking for redelivery",
                    extra={
                        "json_fields": {
                            "operation": "pubsub_message_nack",
                            "subscriber": name,
                            "message_id": message.message_id,
                            "delivery_attempt": message.delivery_attempt,
                            "error": str(e),
                            "error_type": type(e).__name__,
                        },
                        "labels": {"component": "pubsub", "severity": "high"},
                    },
                    exc_info=True,
                )
                message.nack()

        return _callback

    def _on_future_done(self, name: str, future: StreamingPullFuture) -> None:
        # Invoked (from a library thread) when the stream terminates.
        if self._shutting_down:
            return

        error: Optional[BaseException] = None
        try:
            error = future.exception()
        except Exception:
            error = None

        logger.error(
            "Subscriber stream ended unexpectedly; scheduling restart",
            extra={
                "json_fields": {
                    "operation": "pubsub_stream_ended",
                    "subscriber": name,
                    "error": str(error) if error else None,
                    "error_type": type(error).__name__ if error else None,
                },
                "labels": {"component": "pubsub", "severity": "high"},
            },
        )
        self._loop.call_soon_threadsafe(
            lambda: asyncio.ensure_future(self._restart(name))
        )

    async def _restart(self, name: str) -> None:
        if self._shutting_down:
            return
        spec = self._specs.get(name)
        if spec is None:
            return

        attempt = self._restart_attempts.get(name, 0) + 1
        self._restart_attempts[name] = attempt
        backoff = min(self._MAX_RESTART_BACKOFF, 2**attempt)

        await asyncio.sleep(backoff)
        if self._shutting_down:
            return

        try:
            await self._subscribe(spec)
            self._restart_attempts[name] = 0
        except Exception:
            logger.error(
                "Failed to restart subscriber; will retry",
                extra={
                    "json_fields": {
                        "operation": "pubsub_restart_error",
                        "subscriber": name,
                        "attempt": attempt,
                    },
                    "labels": {"component": "pubsub", "severity": "high"},
                },
                exc_info=True,
            )
            self._loop.call_soon_threadsafe(
                lambda: asyncio.ensure_future(self._restart(name))
            )

    async def stop(self) -> None:
        self._shutting_down = True

        futures = list[StreamingPullFuture](self._futures.values())
        for future in futures:
            try:
                future.cancel()  # non-blocking; triggers graceful shutdown
            except Exception:
                logger.warning(
                    "Error cancelling subscriber future",
                    extra={
                        "json_fields": {"operation": "pubsub_shutdown"},
                        "labels": {"component": "pubsub"},
                    },
                    exc_info=True,
                )

        if futures:
            # Await native termination (drains in-flight callbacks) without a thread.
            await asyncio.gather(
                *(asyncio.wrap_future(future) for future in futures),
                return_exceptions=True,
            )

        self._futures.clear()
        self._schedulers.clear()

        await _clients.close()

        logger.info(
            "All subscribers stopped",
            extra={
                "json_fields": {"operation": "pubsub_shutdown_complete"},
                "labels": {"component": "pubsub"},
            },
        )


def get_topic_path(project_id: str, topic_id: str) -> str:
    """Return the fully-qualified topic path (used by the transcoder job config)."""
    return PublisherClient.topic_path(project_id, topic_id)
