import logging
import time

from google.pubsub_v1 import ReceivedMessage

from langfuse import observe
from ment_api.events.translation_event import TranslationEvent
from ment_api.services.external_clients.langfuse_client import langfuse
from ment_api.services.translation_service import translate_verification_fields
from bson import ObjectId

logger = logging.getLogger(__name__)

MAX_RETRY_ATTEMPTS = 5


@observe()
async def process_translation_callback(message: ReceivedMessage):
    with langfuse.start_as_current_span(name="process_translation_callback"):
        start_time = time.time()
        message_id = message.message.message_id

        langfuse.update_current_trace(
            input={
                "message_id": message_id,
                "delivery_attempt": message.delivery_attempt,
                "message_data_preview": message.message.data.decode()[:200],
            },
            metadata={
                "worker_type": "translation",
                "pubsub_subscription": "translation_subscription",
                "max_retry_attempts": MAX_RETRY_ATTEMPTS,
            },
        )

        try:
            translation_event = TranslationEvent.model_validate_json(
                message.message.data.decode()
            )

            verification_id = ObjectId(translation_event.verification_id)

            await translate_verification_fields(verification_id)

            end_time = time.time()
            duration_ms = (end_time - start_time) * 1000

            logger.info(
                f"Translation completed for verification {verification_id} in {end_time - start_time} seconds"
            )

            langfuse.update_current_trace(
                output={
                    "status": "SUCCESS",
                    "verification_id": str(verification_id),
                    "delivery_attempt": message.delivery_attempt,
                    "processing_time_seconds": end_time - start_time,
                },
                metadata={"duration_ms": duration_ms},
            )

        except Exception as e:
            end_time = time.time()
            duration_ms = (end_time - start_time) * 1000

            logger.error(
                f"Error processing translation for message {message_id}: {e}",
                exc_info=True,
            )

            langfuse.update_current_trace(
                output={
                    "status": "ERROR",
                    "error": str(e),
                    "delivery_attempt": message.delivery_attempt,
                    "processing_time_seconds": end_time - start_time,
                },
                metadata={"error_type": type(e).__name__, "duration_ms": duration_ms},
            )

            raise

        finally:
            langfuse.flush()
