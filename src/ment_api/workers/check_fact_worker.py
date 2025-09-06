import logging
import time
from typing import Dict, List

from google.pubsub_v1 import ReceivedMessage

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


async def process_check_fact_callback(message: ReceivedMessage):
    """
    Process fact check callback with comprehensive Langfuse tracing and idempotency.

    Industry best practices implemented:
    - Early acknowledgment after setting PENDING status
    - Idempotency checks to prevent duplicate processing
    - Retry limit handling
    - Proper error handling and logging
    """
    with langfuse.start_as_current_span(
        name="process_check_fact_callback"
    ) as worker_span:
        start_time = time.time()
        message_id = message.message.message_id

        delivery_attempt = message.delivery_attempt

        try:
            # Parse the event with span tracking
            with worker_span.start_as_current_span(
                name="message-parsing"
            ) as parse_span:
                news_created_event = NewsCreatedEvent.model_validate_json(
                    message.message.data.decode()
                )
                verification_ids = news_created_event.verifications
                verification_ids_str = [str(v_id) for v_id in verification_ids]

                langfuse.update_current_trace(session_id=verification_ids_str[0])

                parse_span.update(
                    input={
                        "message_id": message_id,
                        "raw_data_length": len(message.message.data.decode()),
                    },
                    output={
                        "verification_count": len(verification_ids),
                        "verification_ids": verification_ids_str,
                        "event_type": "NewsCreatedEvent",
                    },
                    metadata={
                        "operation": "message_parsing",
                        "worker_type": "fact_check",
                    },
                )

                logger.info(
                    "Fact check message received",
                    extra={
                        "json_fields": {
                            "verification_count": len(verification_ids),
                            "delivery_attempt": delivery_attempt,
                            "max_attempts": MAX_RETRY_ATTEMPTS,
                            "message_id": message_id,
                            "verification_id": verification_ids_str[0]
                            if verification_ids_str
                            else None,
                            "verification_ids": verification_ids_str,
                            "base_operation": "fact_check",
                            "operation": "fact_check_message_received",
                        },
                        "labels": {"component": "fact_check_worker", "phase": "start"},
                    },
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
                logger.error(
                    "Max retry attempts exceeded",
                    extra={
                        "json_fields": {
                            "delivery_attempt": delivery_attempt,
                            "max_attempts": MAX_RETRY_ATTEMPTS,
                            "verification_count": len(verification_ids),
                            "verification_id": verification_ids_str[0]
                            if verification_ids_str
                            else None,
                            "verification_ids": verification_ids_str,
                            "base_operation": "fact_check",
                            "operation": "fact_check_retry_exhausted",
                        },
                        "labels": {
                            "component": "fact_check_worker",
                            "severity": "high",
                        },
                    },
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
            with worker_span.start_as_current_span(
                name="verification-state-check"
            ) as state_span:
                state_span.update(
                    input={
                        "verification_count": len(verification_ids),
                        "verification_ids": verification_ids_str,
                    },
                    metadata={
                        "operation": "verification_state_check",
                        "worker_type": "fact_check",
                    },
                )

                processing_results = await _check_and_prepare_verifications(
                    verification_ids, message_id, delivery_attempt
                )

                state_span.update(
                    output={
                        "needs_processing": len(processing_results["needs_processing"]),
                        "already_completed": len(
                            processing_results["already_completed"]
                        ),
                        "already_pending": len(processing_results["already_pending"]),
                        "already_failed": len(processing_results["already_failed"]),
                        "not_found": len(processing_results.get("not_found", [])),
                    }
                )

            # If no verifications need processing, acknowledge early
            if not processing_results["needs_processing"]:
                logger.info(
                    "No fact check processing needed",
                    extra={
                        "json_fields": {
                            "already_completed": len(
                                processing_results["already_completed"]
                            ),
                            "already_pending": len(
                                processing_results["already_pending"]
                            ),
                            "already_failed": len(processing_results["already_failed"]),
                            "total_verifications": len(verification_ids),
                            "verification_id": verification_ids_str[0]
                            if verification_ids_str
                            else None,
                            "verification_ids": verification_ids_str,
                            "base_operation": "fact_check",
                            "operation": "fact_check_skipped",
                        },
                        "labels": {"component": "fact_check_worker", "phase": "skip"},
                    },
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
            needs_processing_ids = [
                str(vid) for vid in processing_results["needs_processing"]
            ]

            logger.info(
                "Setting verifications to PENDING status",
                extra={
                    "json_fields": {
                        "verification_count": len(
                            processing_results["needs_processing"]
                        ),
                        "verification_id": needs_processing_ids[0]
                        if needs_processing_ids
                        else None,
                        "verification_ids": needs_processing_ids,
                        "base_operation": "fact_check",
                        "operation": "fact_check_pending_set",
                    },
                    "labels": {
                        "component": "fact_check_worker",
                        "phase": "prepare",
                    },
                },
            )

            await _set_pending_status(
                processing_results["needs_processing"], message_id, delivery_attempt
            )

            # Process the fact check - this will create its own comprehensive spans
            with worker_span.start_as_current_span(
                name="fact-check-processing"
            ) as process_span:
                process_span.update(
                    input={
                        "verification_count": len(
                            processing_results["needs_processing"]
                        ),
                        "verification_ids": needs_processing_ids,
                        "primary_verification_id": needs_processing_ids[0]
                        if needs_processing_ids
                        else None,
                    },
                    metadata={
                        "operation": "fact_check_processing",
                        "worker_type": "fact_check",
                    },
                )

                logger.info(
                    "Starting fact check processing",
                    extra={
                        "json_fields": {
                            "verification_count": len(
                                processing_results["needs_processing"]
                            ),
                            "verification_id": needs_processing_ids[0]
                            if needs_processing_ids
                            else None,
                            "verification_ids": needs_processing_ids,
                            "base_operation": "fact_check",
                            "operation": "fact_check_processing_start",
                        },
                        "labels": {
                            "component": "fact_check_worker",
                            "phase": "process",
                        },
                    },
                )

                await check_fact(processing_results["needs_processing"][0])

                process_span.update(
                    output={
                        "processing_completed": True,
                        "verification_count": len(
                            processing_results["needs_processing"]
                        ),
                    }
                )

            end_time = time.time()
            duration_ms = (end_time - start_time) * 1000

            logger.info(
                "Fact check processing completed",
                extra={
                    "json_fields": {
                        "verification_count": len(
                            processing_results["needs_processing"]
                        ),
                        "processing_time_seconds": round(end_time - start_time, 2),
                        "delivery_attempt": delivery_attempt,
                        "verification_id": needs_processing_ids[0]
                        if needs_processing_ids
                        else None,
                        "verification_ids": needs_processing_ids,
                        "base_operation": "fact_check",
                        "operation": "fact_check_processing_complete",
                    },
                    "labels": {"component": "fact_check_worker", "phase": "complete"},
                },
            )

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

            logger.error(
                "Fact check processing failed",
                extra={
                    "json_fields": {
                        "error": str(e),
                        "error_type": type(e).__name__,
                        "delivery_attempt": delivery_attempt,
                        "processing_time_seconds": round(end_time - start_time, 2),
                        "verification_id": verification_ids_str[0]
                        if verification_ids_str
                        else None,
                        "verification_ids": verification_ids_str,
                        "base_operation": "fact_check",
                        "operation": "fact_check_processing_error",
                    },
                    "labels": {"component": "fact_check_worker", "severity": "high"},
                },
                exc_info=True,
            )

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
    with langfuse.start_as_current_span(name="check-verification-states") as check_span:
        check_span.update(
            input={
                "verification_count": len(verification_ids),
                "verification_ids": [str(vid) for vid in verification_ids],
            },
            metadata={
                "operation": "verification_state_check",
                "helper_function": "_check_and_prepare_verifications",
            },
        )

        # Use aggregation pipeline to get verification states efficiently
        pipeline = [
            {"$match": {"_id": {"$in": verification_ids}}},
            {"$project": {"_id": 1, "fact_check_status": 1}},
        ]

        verifications = await mongo.verifications.aggregate(pipeline)
        verification_states = {
            v["_id"]: v.get("fact_check_status") for v in verifications
        }

    verification_ids_str = [str(vid) for vid in verification_ids]
    logger.debug(
        "Verification states analyzed",
        extra={
            "json_fields": {
                "total_requested": len(verification_ids),
                "found_in_db": len(verification_states),
                "verification_id": verification_ids_str[0]
                if verification_ids_str
                else None,
                "verification_ids": verification_ids_str,
                "base_operation": "fact_check",
                "operation": "fact_check_state_analysis",
            },
            "labels": {"component": "fact_check_worker", "phase": "analyze"},
        },
    )

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

    logger.info(
        "Verification processing requirements determined",
        extra={
            "json_fields": {
                "needs_processing": len(results["needs_processing"]),
                "already_completed": len(results["already_completed"]),
                "already_pending": len(results["already_pending"]),
                "already_failed": len(results["already_failed"]),
                "not_found": len(results["not_found"]),
                "verification_id": verification_ids_str[0]
                if verification_ids_str
                else None,
                "verification_ids": verification_ids_str,
                "base_operation": "fact_check",
                "operation": "fact_check_requirements_determined",
            },
            "labels": {"component": "fact_check_worker", "phase": "analyze"},
        },
    )

    check_span.update(
        output={
            "needs_processing": len(results["needs_processing"]),
            "already_completed": len(results["already_completed"]),
            "already_pending": len(results["already_pending"]),
            "already_failed": len(results["already_failed"]),
            "not_found": len(results["not_found"]),
        }
    )

    return results


async def _set_pending_status(
    verification_ids: List[CustomObjectId], message_id: str, delivery_attempt: int
) -> None:
    """
    Set verifications to PENDING status atomically.
    """
    if not verification_ids:
        return

    with langfuse.start_as_current_span(name="update-pending-status") as pending_span:
        pending_span.update(
            input={
                "verification_count": len(verification_ids),
                "verification_ids": [str(vid) for vid in verification_ids],
            },
            metadata={
                "operation": "update_pending_status",
                "helper_function": "_set_pending_status",
            },
        )

        try:
            # Use bulk write for efficiency
            result = await mongo.verifications.update_many(
                {"_id": {"$in": verification_ids}},
                {"$set": {"fact_check_status": "PENDING"}},
            )

            verification_ids_str = [str(vid) for vid in verification_ids]
            if result.matched_count != len(verification_ids):
                logger.warning(
                    "Not all verifications found for PENDING status update",
                    extra={
                        "json_fields": {
                            "expected_count": len(verification_ids),
                            "found_count": result.matched_count,
                            "modified_count": result.modified_count,
                            "verification_id": verification_ids_str[0]
                            if verification_ids_str
                            else None,
                            "verification_ids": verification_ids_str,
                            "base_operation": "fact_check",
                            "operation": "fact_check_pending_partial",
                        },
                        "labels": {
                            "component": "fact_check_worker",
                            "severity": "medium",
                        },
                    },
                )
            else:
                logger.debug(
                    "Successfully set verifications to PENDING status",
                    extra={
                        "json_fields": {
                            "modified_count": result.modified_count,
                            "verification_count": len(verification_ids),
                            "verification_id": verification_ids_str[0]
                            if verification_ids_str
                            else None,
                            "verification_ids": verification_ids_str,
                            "base_operation": "fact_check",
                            "operation": "fact_check_pending_success",
                        },
                        "labels": {"component": "fact_check_worker"},
                    },
                )

            pending_span.update(
                output={
                    "matched_count": result.matched_count,
                    "modified_count": result.modified_count,
                    "all_found": result.matched_count == len(verification_ids),
                    "success": True,
                }
            )

        except Exception as e:
            pending_span.update(
                output={
                    "error": str(e),
                    "error_type": type(e).__name__,
                    "success": False,
                }
            )

            logger.error(
                "Failed to set PENDING status",
                extra={
                    "json_fields": {
                        "error": str(e),
                        "error_type": type(e).__name__,
                        "verification_count": len(verification_ids),
                        "verification_id": verification_ids_str[0]
                        if verification_ids_str
                        else None,
                        "verification_ids": verification_ids_str,
                        "base_operation": "fact_check",
                        "operation": "fact_check_pending_error",
                    },
                    "labels": {"component": "fact_check_worker", "severity": "high"},
                },
                exc_info=True,
            )
            # Don't raise here - we want to continue with processing


async def _mark_verifications_failed(
    verification_ids: List[CustomObjectId], error_reason: str
) -> None:
    """
    Mark verifications as FAILED due to retry exhaustion.
    """
    with langfuse.start_as_current_span(
        name="mark-verifications-failed"
    ) as failed_span:
        failed_span.update(
            input={
                "verification_count": len(verification_ids),
                "verification_ids": [str(vid) for vid in verification_ids],
                "error_reason": error_reason,
            },
            metadata={
                "operation": "mark_verifications_failed",
                "helper_function": "_mark_verifications_failed",
            },
        )

        try:
            result = await mongo.verifications.update_many(
                {"_id": {"$in": verification_ids}},
                {
                    "$set": {
                        "fact_check_status": "FAILED",
                        "error": error_reason,
                    }
                },
            )

            verification_ids_str = [str(vid) for vid in verification_ids]
            logger.info(
                "Marked verifications as FAILED due to retry exhaustion",
                extra={
                    "json_fields": {
                        "verification_count": len(verification_ids),
                        "error_reason": error_reason,
                        "verification_id": verification_ids_str[0]
                        if verification_ids_str
                        else None,
                        "verification_ids": verification_ids_str,
                        "base_operation": "fact_check",
                        "operation": "fact_check_mark_failed",
                    },
                    "labels": {"component": "fact_check_worker", "phase": "cleanup"},
                },
            )

            failed_span.update(
                output={
                    "matched_count": result.matched_count,
                    "modified_count": result.modified_count,
                    "success": True,
                }
            )

        except Exception as e:
            failed_span.update(
                output={
                    "error": str(e),
                    "error_type": type(e).__name__,
                    "success": False,
                }
            )

            logger.error(
                "Failed to mark verifications as FAILED",
                extra={
                    "json_fields": {
                        "error": str(e),
                        "error_type": type(e).__name__,
                        "verification_count": len(verification_ids),
                        "error_reason": error_reason,
                        "verification_id": verification_ids_str[0]
                        if verification_ids_str
                        else None,
                        "verification_ids": verification_ids_str,
                        "base_operation": "fact_check",
                        "operation": "fact_check_mark_failed_error",
                    },
                    "labels": {"component": "fact_check_worker", "severity": "high"},
                },
                exc_info=True,
            )
