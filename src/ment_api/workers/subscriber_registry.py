from typing import List, Optional

from ment_api.configurations.config import settings
from ment_api.services.pub_sub_service import AsyncCallable, SubscriberSpec
from ment_api.services.verification_service import video_transcode_callback
from ment_api.workers.ai_buffer_worker import process_ai_buffer_pubsub_callback
from ment_api.workers.ai_character_worker import process_ai_character_callback
from ment_api.workers.check_fact_worker import process_check_fact_callback
from ment_api.workers.media_post_generator_worker import (
    process_media_post_generator_callback,
)
from ment_api.workers.news_worker import process_news_callback
from ment_api.workers.social_media_worker import process_social_media_callback
from ment_api.workers.translation_worker import process_translation_callback
from ment_api.workers.video_processor_worker import process_video_callback


def _dlq_topic_id(topic_id: str) -> Optional[str]:
    """Derive the dead-letter topic id for a given source topic, or None if DLQ
    is disabled globally."""
    if not settings.pub_sub_enable_dlq:
        return None
    return f"{topic_id}{settings.pub_sub_dlq_topic_suffix}"


def _spec(
    *,
    name: str,
    topic_id: str,
    subscription_id: str,
    callback: AsyncCallable,
    max_concurrency: int,
    ack_deadline_seconds: int,
    enabled: bool = True,
) -> SubscriberSpec:
    """Build a SubscriberSpec, filling the DLQ topic and delivery-attempt policy
    from settings so each registry entry only declares its identity and tuning."""
    return SubscriberSpec(
        name=name,
        topic_id=topic_id,
        subscription_id=subscription_id,
        callback=callback,
        max_concurrency=max_concurrency,
        ack_deadline_seconds=ack_deadline_seconds,
        dlq_topic_id=_dlq_topic_id(topic_id),
        max_delivery_attempts=settings.pub_sub_max_delivery_attempts,
        enabled=enabled,
    )


def build_subscriber_specs() -> List[SubscriberSpec]:
    """Declarative registry of every Pub/Sub subscription.

    Concurrency is bounded per subscription so all subscribers can run
    simultaneously without contending for a shared thread pool. Long-running
    workers get low concurrency + a long ack deadline; fast workers get more.
    Toggle a subscriber with ``enabled`` instead of commenting code.
    """
    return [
        _spec(
            name="transcoder",
            topic_id=settings.pub_sub_transcoder_topic_id,
            subscription_id=settings.pub_sub_transcoder_subscription_id,
            callback=video_transcode_callback,
            max_concurrency=4,
            ack_deadline_seconds=60,
        ),
        _spec(
            name="news",
            topic_id=settings.pub_sub_news_topic_id,
            subscription_id=settings.pub_sub_news_subscription_id,
            callback=process_news_callback,
            max_concurrency=1,
            ack_deadline_seconds=600,
        ),
        _spec(
            name="check_fact",
            topic_id=settings.pub_sub_check_fact_topic_id,
            subscription_id=settings.pub_sub_check_fact_subscription_id,
            callback=process_check_fact_callback,
            max_concurrency=2,
            ack_deadline_seconds=600,
        ),
        _spec(
            name="social_media",
            topic_id=settings.pub_sub_social_media_scrape_topic_id,
            subscription_id=settings.pub_sub_social_media_scrape_subscription_id,
            callback=process_social_media_callback,
            max_concurrency=4,
            ack_deadline_seconds=120,
        ),
        _spec(
            name="video_processor",
            topic_id=settings.pub_sub_video_processor_topic_id,
            subscription_id=settings.pub_sub_video_processor_subscription_id,
            callback=process_video_callback,
            max_concurrency=2,
            ack_deadline_seconds=600,
        ),
        _spec(
            name="translation",
            topic_id=settings.pub_sub_translation_topic_id,
            subscription_id=settings.pub_sub_translation_subscription_id,
            callback=process_translation_callback,
            max_concurrency=8,
            ack_deadline_seconds=60,
        ),
        _spec(
            name="media_post_generator",
            topic_id=settings.pub_sub_media_post_generator_topic_id,
            subscription_id=settings.pub_sub_media_post_generator_subscription_id,
            callback=process_media_post_generator_callback,
            max_concurrency=1,
            ack_deadline_seconds=600,
        ),
        _spec(
            name="ai_character",
            topic_id=settings.pub_sub_ai_character_topic_id,
            subscription_id=settings.pub_sub_ai_character_subscription_id,
            callback=process_ai_character_callback,
            max_concurrency=1,
            ack_deadline_seconds=600,
        ),
        _spec(
            name="ai_buffer",
            topic_id=settings.pub_sub_ai_buffer_topic_id,
            subscription_id=settings.pub_sub_ai_buffer_subscription_id,
            callback=process_ai_buffer_pubsub_callback,
            max_concurrency=4,
            ack_deadline_seconds=60,
        ),
    ]
