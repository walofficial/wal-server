import logging
import time
from typing import Dict, List

from google.pubsub_v1 import ReceivedMessage

from langfuse import observe
from ment_api.common.custom_object_id import CustomObjectId
from ment_api.events.news_created_event import NewsCreatedEvent
from ment_api.persistence import mongo
from ment_api.services.check_fact_service import check_fact
from ment_api.services.external_clients.langfuse_client import (
    langfuse,
)

logger = logging.getLogger(__name__)

# Maximum retry attempts for fact checking
MAX_RETRY_ATTEMPTS = 2

# Initialize global Langfuse client
# langfuse is imported directly from langfuse_client


@observe()
async def process_check_fact_callback(message: ReceivedMessage):
    """
    Process fact check callback with comprehensive Langfuse tracing and idempotency.

    Industry best practices implemented:
    - Early acknowledgment after setting PENDING status
    - Idempotency checks to prevent duplicate processing
    - Retry limit handling
    - Proper error handling and logging
    """
    with langfuse.start_as_current_span(name="process_check_fact_callback"):
        start_time = time.time()
        message_id = message.message.message_id

        # Extract retry count from message attributes (Pub/Sub automatically adds delivery_attempt)
        delivery_attempt = int(
            message.message.attributes.get("googclient_deliveryattempt", "1")
        )

        # Set trace-level attributes using the global client
        langfuse.update_current_trace(
            input={
                "message_id": message_id,
                "delivery_attempt": delivery_attempt,
                "message_data_preview": message.message.data.decode()[:200],
            },
            metadata={
                "worker_type": "fact_check",
                "pubsub_subscription": "check_fact_subscription",
                "max_retry_attempts": MAX_RETRY_ATTEMPTS,
            },
        )

        try:
            # Parse the event
            news_created_event = NewsCreatedEvent.model_validate_json(
                message.message.data.decode()
            )
            verification_ids = news_created_event.verifications
            verification_ids_str = [str(v_id) for v_id in verification_ids]

            logger.info(
                f"Starting fact check processing for {len(verification_ids)} verifications (attempt {delivery_attempt}/{MAX_RETRY_ATTEMPTS})"
            )

            # Update trace with parsed event data
            langfuse.update_current_trace(
                input={
                    "message_id": message_id,
                    "delivery_attempt": delivery_attempt,
                    "verification_count": len(verification_ids),
                    "verification_ids": verification_ids_str,
                    "event_type": "NewsCreatedEvent",
                }
            )

            # Check if we've exceeded retry limit
            if delivery_attempt > MAX_RETRY_ATTEMPTS:
                logger.warning(
                    f"Max retry attempts exceeded ({delivery_attempt}/{MAX_RETRY_ATTEMPTS}) - marking {len(verification_ids)} verifications as FAILED"
                )

                # Mark all verifications as failed due to max retries
                await _mark_verifications_failed(
                    verification_ids,
                    f"Exceeded maximum retry attempts ({MAX_RETRY_ATTEMPTS})",
                )

                langfuse.update_current_trace(
                    output={
                        "status": "MAX_RETRIES_EXCEEDED",
                        "delivery_attempt": delivery_attempt,
                        "verification_count": len(verification_ids),
                    },
                    metadata={"reason": "Exceeded retry limit"},
                )

                # Acknowledge to prevent further retries
                return

            # Check verification states and filter what needs processing
            processing_results = await _check_and_prepare_verifications(
                verification_ids, message_id, delivery_attempt
            )

            # If no verifications need processing, acknowledge early
            if not processing_results["needs_processing"]:
                logger.info(
                    f"No processing needed - completed: {len(processing_results['already_completed'])}, "
                    f"pending: {len(processing_results['already_pending'])}, "
                    f"failed: {len(processing_results['already_failed'])}"
                )

                langfuse.update_current_trace(
                    output={
                        "status": "NO_PROCESSING_NEEDED",
                        "already_completed": len(
                            processing_results["already_completed"]
                        ),
                        "already_pending": len(processing_results["already_pending"]),
                        "already_failed": len(processing_results["already_failed"]),
                    },
                )
                return

            # Set verifications to PENDING status and acknowledge message early
            logger.info(
                f"Setting {len(processing_results['needs_processing'])} verifications to PENDING status"
            )
            await _set_pending_status(
                processing_results["needs_processing"], message_id, delivery_attempt
            )

            # Process the fact check - this will create its own comprehensive spans
            logger.info(
                f"Starting fact check processing for {len(processing_results['needs_processing'])} verifications"
            )
            await check_fact(processing_results["needs_processing"])

            end_time = time.time()
            duration_ms = (end_time - start_time) * 1000

            logger.info(f"Fact check completed in {end_time - start_time} seconds")

            # Set trace output
            langfuse.update_current_trace(
                output={
                    "status": "SUCCESS",
                    "delivery_attempt": delivery_attempt,
                    "processed_count": len(processing_results["needs_processing"]),
                    "processing_time_seconds": end_time - start_time,
                },
                metadata={
                    "average_time_per_verification": (
                        duration_ms / len(processing_results["needs_processing"])
                        if processing_results["needs_processing"]
                        else 0
                    ),
                },
            )

        except Exception as e:
            end_time = time.time()
            duration_ms = (end_time - start_time) * 1000

            logger.error(f"Error in fact check callback: {e}", exc_info=True)

            # Set trace output for error case
            langfuse.update_current_trace(
                output={
                    "status": "ERROR",
                    "error": str(e),
                    "delivery_attempt": delivery_attempt,
                    "processing_time_seconds": end_time - start_time,
                },
                metadata={"error_type": type(e).__name__, "duration_ms": duration_ms},
            )

            # Re-raise to trigger message retry/dead letter
            raise

        finally:
            langfuse.flush()


async def _check_and_prepare_verifications(
    verification_ids: List[CustomObjectId], message_id: str, delivery_attempt: int
) -> Dict[str, List[CustomObjectId]]:
    """
    Check the current state of verifications and determine what needs processing.

    Returns a dict with:
    - needs_processing: verifications that need fact checking
    - already_completed: verifications already completed
    - already_pending: verifications already being processed
    - already_failed: verifications already failed
    """
    # Use aggregation pipeline to get verification states efficiently
    pipeline = [
        {"$match": {"_id": {"$in": verification_ids}}},
        {"$project": {"_id": 1, "fact_check_status": 1}},
    ]

    verifications = await mongo.verifications.aggregate(pipeline)
    verification_states = {v["_id"]: v.get("fact_check_status") for v in verifications}

    results = {
        "needs_processing": [],
        "already_completed": [],
        "already_pending": [],
        "already_failed": [],
        "not_found": [],
    }

    for verification_id in verification_ids:
        status = verification_states.get(verification_id)

        if status == "COMPLETED":
            results["needs_processing"].append(verification_id)
        elif status == "PENDING":
            results["needs_processing"].append(verification_id)
        elif status == "FAILED":
            results["needs_processing"].append(verification_id)
        elif status is None:
            # Verification not found or has no fact_check_status
            results["needs_processing"].append(verification_id)
        else:
            # Any other status, treat as needs processing
            results["needs_processing"].append(verification_id)

    return results


async def _set_pending_status(
    verification_ids: List[CustomObjectId], message_id: str, delivery_attempt: int
) -> None:
    """
    Set verifications to PENDING status atomically.
    """
    if not verification_ids:
        return

    try:
        # Use bulk write for efficiency
        result = await mongo.verifications.update_many(
            {"_id": {"$in": verification_ids}},
            {"$set": {"fact_check_status": "PENDING"}},
        )

        if result.matched_count != len(verification_ids):
            logger.warning(
                f"Not all verifications were found when setting PENDING status. "
                f"Expected: {len(verification_ids)}, Found: {result.matched_count}"
            )
        else:
            logger.info(
                f"Successfully set {result.modified_count} verifications to PENDING status"
            )

    except Exception as e:
        logger.error(f"Error setting PENDING status: {e}", exc_info=True)
        # Don't raise here - we want to continue with processing


async def _mark_verifications_failed(
    verification_ids: List[CustomObjectId], error_reason: str
) -> None:
    """
    Mark verifications as FAILED due to retry exhaustion.
    """
    try:
        await mongo.verifications.update_many(
            {"_id": {"$in": verification_ids}},
            {
                "$set": {
                    "fact_check_status": "FAILED",
                    "error": error_reason,
                }
            },
        )

        logger.info(
            f"Marked {len(verification_ids)} verifications as FAILED due to: {error_reason}"
        )

    except Exception as e:
        logger.error(f"Error marking verifications as failed: {e}", exc_info=True)
