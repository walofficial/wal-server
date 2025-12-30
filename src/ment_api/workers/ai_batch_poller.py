"""
AI Batch Job Poller Worker.

This worker polls for pending Gemini Batch API jobs and triggers
the completion handler when jobs succeed or fail.

Triggered by Cloud Scheduler every 5 minutes.
"""

import base64
import logging
import uuid
from datetime import datetime, timedelta, timezone

from ment_api.configurations.config import settings
from ment_api.persistence import mongo
from ment_api.services.external_clients.cloud_flare_client import upload_image
from ment_api.services.external_clients.gemini_client import gemini_client
from ment_api.services.google_tasks_service import create_http_task

logger = logging.getLogger(__name__)


async def poll_pending_batch_jobs() -> dict:
    """
    Poll for pending Gemini Batch API jobs and process completed ones.

    Returns:
        Dictionary with poll results
    """
    try:
        # Get all pending or processing batch jobs
        pending_jobs = await mongo.ai_batch_jobs.find_all(
            {"status": {"$in": ["PENDING", "PROCESSING"]}}
        )

        if not pending_jobs:
            logger.info(
                "No pending batch jobs to poll",
                extra={
                    "json_fields": {"operation": "poll_batch_jobs"},
                    "labels": {"component": "ai_batch_poller"},
                },
            )
            return {"status": "no_pending_jobs", "processed": 0}

        processed = 0
        failed = 0

        for job in pending_jobs:
            try:
                result = await check_and_process_job(job)
                if result.get("completed"):
                    processed += 1
                elif result.get("failed"):
                    failed += 1
            except Exception as e:
                logger.error(
                    f"Error processing batch job {job['batch_job_name']}: {e}",
                    extra={
                        "json_fields": {
                            "operation": "poll_batch_jobs",
                            "batch_job_name": job["batch_job_name"],
                            "error": str(e),
                        },
                        "labels": {"component": "ai_batch_poller", "severity": "high"},
                    },
                )
                continue

        logger.info(
            "Batch poll completed",
            extra={
                "json_fields": {
                    "operation": "poll_batch_jobs",
                    "jobs_checked": len(pending_jobs),
                    "processed": processed,
                    "failed": failed,
                },
                "labels": {"component": "ai_batch_poller"},
            },
        )

        return {
            "status": "polled",
            "jobs_checked": len(pending_jobs),
            "processed": processed,
            "failed": failed,
        }

    except Exception as e:
        logger.error(
            f"Batch poller failed: {e}",
            extra={
                "json_fields": {"operation": "poll_batch_jobs", "error": str(e)},
                "labels": {"component": "ai_batch_poller", "severity": "high"},
            },
        )
        raise


async def check_and_process_job(job: dict) -> dict:
    """
    Check the status of a single batch job and process if completed.

    Args:
        job: The batch job document from MongoDB

    Returns:
        Dictionary with processing result
    """
    batch_job_name = job["batch_job_name"]

    try:
        # Get batch job status from Gemini API
        batch_status = await gemini_client.aio.batches.get(name=batch_job_name)

        job_state = batch_status.state

        if job_state == "JOB_STATE_SUCCEEDED":
            # Job completed successfully - process results
            logger.info(
                f"Batch job {batch_job_name} succeeded, processing results",
                extra={
                    "json_fields": {
                        "operation": "process_completed_job",
                        "batch_job_name": batch_job_name,
                    },
                    "labels": {"component": "ai_batch_poller"},
                },
            )

            # Get results from batch job
            results = []
            async for result in batch_status.results():
                if result.response and result.response.candidates:
                    for candidate in result.response.candidates:
                        if candidate.content and candidate.content.parts:
                            for part in candidate.content.parts:
                                if hasattr(part, "inline_data") and part.inline_data:
                                    results.append(
                                        {
                                            "image_data": part.inline_data.data,
                                            "mime_type": getattr(
                                                part.inline_data, "mime_type", "image/png"
                                            ),
                                        }
                                    )

            # Process each result with its corresponding metadata
            posts_scheduled = 0
            for idx, meta in enumerate(job["posts"]):
                if idx >= len(results):
                    logger.warning(
                        f"Missing result for post index {idx} in batch {batch_job_name}"
                    )
                    continue

                result_data = results[idx]

                try:
                    # Decode and upload image
                    image_bytes = base64.b64decode(result_data["image_data"])
                    filename = f"ai_posts/{meta['character_user_id']}/{uuid.uuid4().hex}.jpg"

                    uploaded = await upload_image(
                        image_bytes, filename, "image/jpeg"
                    )

                    # Schedule post with Cloud Task (staggered by scheduled_delay)
                    scheduled_time = datetime.now(timezone.utc) + timedelta(
                        seconds=meta["scheduled_delay"]
                    )

                    create_http_task(
                        url=f"{settings.api_url}/ai-characters/execute-post",
                        json_payload={
                            "character_user_id": meta["character_user_id"],
                            "feed_id": meta["feed_id"],
                            "text_content": meta["text_content"],
                            "image_url": uploaded.url,
                            "image_dims": {
                                "url": uploaded.url,
                                "width": uploaded.width,
                                "height": uploaded.height,
                            },
                        },
                        schedule_time=scheduled_time,
                    )

                    posts_scheduled += 1

                except Exception as e:
                    logger.error(
                        f"Failed to process batch result {idx}: {e}",
                        extra={
                            "json_fields": {
                                "operation": "process_batch_result",
                                "batch_job_name": batch_job_name,
                                "index": idx,
                                "error": str(e),
                            },
                            "labels": {"component": "ai_batch_poller"},
                        },
                    )
                    continue

            # Update job status to completed
            await mongo.ai_batch_jobs.update_one(
                {"_id": job["_id"]},
                {
                    "$set": {
                        "status": "COMPLETED",
                        "completed_at": datetime.now(timezone.utc),
                        "posts_scheduled": posts_scheduled,
                    }
                },
            )

            return {"completed": True, "posts_scheduled": posts_scheduled}

        elif job_state == "JOB_STATE_FAILED":
            # Job failed
            logger.error(
                f"Batch job {batch_job_name} failed",
                extra={
                    "json_fields": {
                        "operation": "check_job_status",
                        "batch_job_name": batch_job_name,
                        "state": job_state,
                    },
                    "labels": {"component": "ai_batch_poller", "severity": "high"},
                },
            )

            await mongo.ai_batch_jobs.update_one(
                {"_id": job["_id"]},
                {
                    "$set": {
                        "status": "FAILED",
                        "completed_at": datetime.now(timezone.utc),
                    }
                },
            )

            return {"failed": True}

        elif job_state in ["JOB_STATE_RUNNING", "JOB_STATE_PENDING"]:
            # Job still running - update status
            new_status = "PROCESSING" if job_state == "JOB_STATE_RUNNING" else "PENDING"

            if job["status"] != new_status:
                await mongo.ai_batch_jobs.update_one(
                    {"_id": job["_id"]},
                    {"$set": {"status": new_status}},
                )

            return {"still_running": True, "state": job_state}

        else:
            # Unknown state
            logger.warning(
                f"Unknown batch job state: {job_state}",
                extra={
                    "json_fields": {
                        "operation": "check_job_status",
                        "batch_job_name": batch_job_name,
                        "state": job_state,
                    },
                    "labels": {"component": "ai_batch_poller"},
                },
            )

            return {"unknown_state": True, "state": job_state}

    except Exception as e:
        logger.error(
            f"Failed to check batch job status: {e}",
            extra={
                "json_fields": {
                    "operation": "check_job_status",
                    "batch_job_name": batch_job_name,
                    "error": str(e),
                },
                "labels": {"component": "ai_batch_poller", "severity": "high"},
            },
        )
        raise


async def cleanup_old_batch_jobs(days_old: int = 7) -> int:
    """
    Clean up old completed/failed batch jobs.

    Args:
        days_old: Delete jobs older than this many days

    Returns:
        Number of jobs deleted
    """
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=days_old)

    result = await mongo.ai_batch_jobs.delete_all(
        {
            "status": {"$in": ["COMPLETED", "FAILED"]},
            "created_at": {"$lt": cutoff_date},
        }
    )

    if result.deleted_count > 0:
        logger.info(
            "Cleaned up old batch jobs",
            extra={
                "json_fields": {
                    "operation": "cleanup_batch_jobs",
                    "deleted_count": result.deleted_count,
                    "cutoff_days": days_old,
                },
                "labels": {"component": "ai_batch_poller"},
            },
        )

    return result.deleted_count


# Entry point for Cloud Scheduler trigger
async def scheduler_handler() -> dict:
    """Handle Cloud Scheduler trigger for batch polling."""
    # Poll pending jobs
    result = await poll_pending_batch_jobs()

    # Optionally cleanup old jobs once a day (can be triggered at specific hour)
    # await cleanup_old_batch_jobs(days_old=7)

    return result

