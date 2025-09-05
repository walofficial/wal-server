import logging
import time
import uuid
from typing import Any, Dict, List, Optional

from langfuse import observe

from ment_api.common.custom_object_id import CustomObjectId
from ment_api.configurations.config import settings
from ment_api.models.fact_checking_models import (
    FactCheckRequest,
)
from ment_api.models.location_feed_post import (
    LocationFeedPost,
)
from ment_api.persistence import mongo, mongo_client
from ment_api.persistence.mongo import create_translation_projection
from ment_api.services.external_clients.cloud_vision_client import (
    get_cloud_vision_client,
)
from ment_api.services.external_clients.gemini_client import (
    GeminiClient,
    gemini_client,
)
from ment_api.services.external_clients.langfuse_client import langfuse
from ment_api.services.external_clients.models.gemini_models import (
    FactCheckInputRequest,
    FactCheckInputResponse,
)
from ment_api.services.external_clients.models.vision_models import (
    OCRResponse,
    TextExtractRequest,
)
from ment_api.services.fact_checking_service import check_fact as jina_check_fact
from ment_api.services.notification_service import send_notification
from ment_api.services.score_generator_service import generate_score
from ment_api.services.score_validity_service import calculate_valid_until_logarithmic
from ment_api.services.translation_service import publish_translation_request

logger = logging.getLogger(__name__)


async def update_verification_status(
    verification_id: CustomObjectId, status: str, additional_data: Optional[Dict] = None
) -> bool:
    """
    Update the verification document with the given status and additional data.
    Now uses @observe decorator for automatic tracing.

    Args:
        verification_id: The ID of the verification to update
        status: The status to set (PENDING, COMPLETED, FAILED)
        additional_data: Optional additional data to set in the document

    Returns:
        bool: True if the update was successful, False otherwise
    """

    update_dict = {"fact_check_status": status}
    if additional_data:
        update_dict.update(additional_data)

    try:
        update_result = await mongo.verifications.update_one(
            {"_id": verification_id}, {"$set": update_dict}
        )

        if update_result.modified_count == 0:
            logger.warning(
                "Verification status update had no effect",
                extra={
                    "json_fields": {
                        "verification_id": str(verification_id),
                        "status": status,
                        "update_data": update_dict,
                        "base_operation": "fact_check",
                        "operation": "fact_check_status_update_no_effect",
                    },
                    "labels": {"component": "fact_check_service", "severity": "medium"},
                },
            )

            return False

        return True

    except Exception:
        raise


@observe()
async def check_fact(
    verification_ids: List[CustomObjectId],
) -> Optional[Dict[CustomObjectId, Any]]:
    """
    Main fact checking function that processes multiple verifications.
    Now uses @observe decorator for automatic trace creation and context propagation.
    The decorator automatically captures function inputs/outputs and creates comprehensive traces.

    Enhanced with smart notification handling:
    - Only sends initial processing notification once (not on retries)
    - Maintains completion/failure notifications as before
    """

    translatable_fields = ["text_content", "title"]
    translation_projections = create_translation_projection(translatable_fields, "en")

    pipeline = [
        {"$match": {"_id": verification_ids[0]}},
        {
            "$project": {
                # Core fields
                "_id": 1,
                "assignee_user_id": 1,
                "feed_id": 1,
                "is_generated_news": 1,
                "fact_check_status": 1,
                # Media and content fields
                "ai_video_summary": 1,
                "social_media_scrape_status": 1,
                "image_gallery_with_dims": 1,
                "social_media_scrape_details": 1,
                "youtube_id": 1,
                "sources": 1,
                "preview_data": 1,
                "text_content_in_english": 1,
                # Translated fields with fallback logic
                **translation_projections,
            }
        },
    ]

    results = await mongo_client.db["verifications"].aggregate(pipeline)
    verification_list = await results.to_list(length=1)
    verification = verification_list[0] if verification_list else None

    verification_ids_str = [str(vid) for vid in verification_ids]
    logger.info(
        "Fact check service started",
        extra={
            "json_fields": {
                "verification_id": verification_ids_str[0]
                if verification_ids_str
                else None,
                "verification_ids": verification_ids_str,
                "verification_found": bool(verification),
                "base_operation": "fact_check",
                "operation": "fact_check_service_start",
            },
            "labels": {"component": "fact_check_service", "phase": "start"},
        },
    )
    await mongo.verifications.update_one(
        {"_id": verification_ids[0]},
        {
            # We only set statuses to completed so that UI can always poll until fact check is completed and times when message migth not be acknowledged in time and UI stops polling
            "$set": {
                "metadata_status": "COMPLETED",
                "ai_video_summary_status": "COMPLETED",
            }
        },
    )
    if verification is None:
        logger.error(
            "Verification not found for fact check",
            extra={
                "json_fields": {
                    "verification_id": verification_ids_str[0]
                    if verification_ids_str
                    else None,
                    "verification_ids": verification_ids_str,
                    "base_operation": "fact_check",
                    "operation": "fact_check_verification_not_found",
                },
                "labels": {"component": "fact_check_service", "severity": "high"},
            },
        )

        return None

    verification_id = verification.get("_id")

    # Create a new span for each verification processing - keep manual span for granular control
    with langfuse.start_as_current_span(
        name="process_single_verification"
    ) as verification_span:
        verification_start_time = time.time()
        user_id = verification.get("assignee_user_id")

        verification_span.update(
            input={
                "verification_id": str(verification_id),
                "user_id": user_id,
                "feed_id": str(verification.get("feed_id", "")),
                "is_generated_news": verification.get("is_generated_news", False),
                "has_video": bool(verification.get("ai_video_summary")),
                "social_media_status": verification.get("social_media_scrape_status"),
                "current_fact_check_status": verification.get("fact_check_status"),
            },
            user_id=user_id,
        )

        # Check if this verification was already in PENDING status
        # If so, this is a retry and we shouldn't send the initial notification again
        was_already_pending = verification.get("fact_check_status") == "PENDING"

        # Only update to PENDING if not already set (this handles the case where worker set it)
        if not was_already_pending:
            await update_verification_status(verification_id, "PENDING")
            logger.info(
                "Verification status updated to PENDING",
                extra={
                    "json_fields": {
                        "verification_id": str(verification_id),
                        "was_retry": False,
                        "base_operation": "fact_check",
                        "operation": "fact_check_status_pending",
                    },
                    "labels": {"component": "fact_check_service", "phase": "prepare"},
                },
            )
        else:
            logger.info(
                "Verification already in PENDING status (retry)",
                extra={
                    "json_fields": {
                        "verification_id": str(verification_id),
                        "was_retry": True,
                        "base_operation": "fact_check",
                        "operation": "fact_check_status_already_pending",
                    },
                    "labels": {"component": "fact_check_service", "phase": "prepare"},
                },
            )

        # Extract statement with logging - this function now uses @observe decorator
        statement = await extract_statement(verification)

        logger.info(
            "Statement extraction completed",
            extra={
                "json_fields": {
                    "verification_id": str(verification_id),
                    "has_statement": bool(statement),
                    "statement_length": len(statement) if statement else 0,
                    "base_operation": "fact_check",
                    "operation": "fact_check_statement_extracted",
                },
                "labels": {"component": "fact_check_service", "phase": "extract"},
            },
        )

        # Get image URLs if present
        image_urls = [
            image.get("url")
            for image in verification.get("image_gallery_with_dims", [])
        ]
        if verification.get("social_media_scrape_details"):
            social_media_image_urls = verification.get(
                "social_media_scrape_details", {}
            ).get("image_urls", [])
            post_screenshot_url = (
                verification.get("social_media_scrape_details", {})
                .get("screenshot", {})
                .get("url", None)
            )
            if post_screenshot_url:
                image_urls.append(post_screenshot_url)
            # Append social media images
            # to existing image gallery
            image_urls.extend(social_media_image_urls)

        # Step 1: Extract text from images using OCR if images are present
        extracted_image_text = None
        if image_urls:
            logger.info(
                "Starting OCR text extraction from images",
                extra={
                    "json_fields": {
                        "verification_id": str(verification_id),
                        "image_count": len(image_urls),
                        "base_operation": "fact_check",
                        "operation": "fact_check_ocr_start",
                    },
                    "labels": {"component": "fact_check_service", "phase": "ocr"},
                },
            )
            # Keep manual span for fine-grained OCR tracing
            with langfuse.start_as_current_span(name="ocr_extraction") as ocr_span:
                ocr_start_time = time.time()

                ocr_span.update(
                    input={
                        "image_count": len(image_urls),
                        "image_urls": image_urls,
                    },
                    user_id=user_id,
                )

                try:
                    async with get_cloud_vision_client() as vision_client:
                        ocr_request = TextExtractRequest(
                            image_urls=image_urls, max_concurrent=5
                        )
                        ocr_results = await vision_client.extract_text_from_images(
                            ocr_request
                        )
                        ocr_response = OCRResponse.from_results(ocr_results)

                        extracted_image_text = ocr_response.combined_text

                    ocr_duration = (time.time() - ocr_start_time) * 1000

                    ocr_span.update(
                        output={
                            "successful_extractions": ocr_response.successful_extractions,
                            "total_images": ocr_response.total_images,
                            "has_text": bool(extracted_image_text),
                            "text_length": (
                                len(extracted_image_text) if extracted_image_text else 0
                            ),
                        },
                        metadata={
                            "success_rate": (
                                ocr_response.successful_extractions / len(image_urls)
                                if image_urls
                                else 0
                            )
                        },
                    )

                    logger.info(
                        "OCR text extraction completed",
                        extra={
                            "json_fields": {
                                "verification_id": str(verification_id),
                                "successful_extractions": ocr_response.successful_extractions,
                                "total_images": len(image_urls),
                                "has_extracted_text": bool(extracted_image_text),
                                "text_length": len(extracted_image_text)
                                if extracted_image_text
                                else 0,
                                "success_rate": ocr_response.successful_extractions
                                / len(image_urls),
                                "base_operation": "fact_check",
                                "operation": "fact_check_ocr_complete",
                            },
                            "labels": {
                                "component": "fact_check_service",
                                "phase": "ocr",
                            },
                        },
                    )

                except Exception as e:
                    ocr_duration = (time.time() - ocr_start_time) * 1000

                    ocr_span.update(
                        output=None,
                        metadata={"error": str(e), "duration_ms": ocr_duration},
                    )

                    logger.error(
                        "OCR text extraction failed",
                        extra={
                            "json_fields": {
                                "verification_id": str(verification_id),
                                "error": str(e),
                                "error_type": type(e).__name__,
                                "image_count": len(image_urls),
                                "base_operation": "fact_check",
                                "operation": "fact_check_ocr_error",
                            },
                            "labels": {
                                "component": "fact_check_service",
                                "severity": "medium",
                            },
                        },
                    )
                    # Continue without image text - don't fail the entire fact check
                    extracted_image_text = None

        # Combine original statement with extracted image text
        combined_statement = statement
        if extracted_image_text:
            if statement:
                combined_statement = f"{statement}\n\nText extracted from images:\n{extracted_image_text}"
            else:
                combined_statement = (
                    f"Text extracted from images:\n{extracted_image_text}"
                )

        # Step 2: Use Gemini to analyze statement and images
        enhanced_input: FactCheckInputResponse = None
        if combined_statement or len(image_urls) > 0:
            if not combined_statement:
                combined_statement = "Post or screenshot to be fact checked, if it's not fact checkable content ignore it."

            # Send processing notification ONLY if this is the first time processing
            # (not a retry where status was already PENDING)
            if not verification.get("is_generated_news") and not was_already_pending:
                await send_notification(
                    verification.get("assignee_user_id"),
                    "ვამოწმებთ თქვენს ფოსტს",
                    "რამოდენიმე წუთში დასრულდება...",
                    data={
                        "type": "fact_check_started",
                        "verificationId": str(verification_id),
                    },
                )

            # Gemini analysis happens within its own span in the gemini_client
            logger.info(
                "Starting Gemini analysis for fact check validation",
                extra={
                    "json_fields": {
                        "verification_id": str(verification_id),
                        "statement_length": len(combined_statement),
                        "image_count": len(image_urls),
                        "is_social_media": verification.get(
                            "social_media_scrape_status"
                        )
                        == "COMPLETED",
                        "base_operation": "fact_check",
                        "operation": "fact_check_gemini_start",
                    },
                    "labels": {"component": "fact_check_service", "phase": "gemini"},
                },
            )
            gemini_request = FactCheckInputRequest(
                statement=combined_statement,
                image_urls=image_urls,
                is_social_media=verification.get("social_media_scrape_status")
                == "COMPLETED",
            )

            gemini_client_instance = GeminiClient()
            enhanced_input = await gemini_client_instance.get_fact_check_input(
                gemini_request
            )

            if not enhanced_input.is_valid_for_fact_check:
                logger.warning(
                    "Gemini analysis determined content not valid for fact check",
                    extra={
                        "json_fields": {
                            "verification_id": str(verification_id),
                            "error_reason": enhanced_input.error_reason,
                            "base_operation": "fact_check",
                            "operation": "fact_check_gemini_invalid",
                        },
                        "labels": {
                            "component": "fact_check_service",
                            "phase": "gemini",
                        },
                    },
                )
                await update_verification_status(
                    verification_id,
                    "FAILED",
                    {
                        "error": enhanced_input.error_reason
                        or "Not valid for fact check"
                    },
                )
                if not verification.get("is_generated_news"):
                    await send_notification(
                        verification.get("assignee_user_id"),
                        "ფოსტი ვერ გადამოწმდა",
                        enhanced_input.error_reason
                        or "არ შეიცავს გადასამოწმებელ მასალას",
                        data={
                            "type": "fact_check_failed",
                            "verificationId": str(verification_id),
                        },
                    )
                    await mongo.verifications.update_one(
                        {"_id": verification_id},
                        {"$set": {"is_public": False}},
                    )
                verification_span.update(
                    output={
                        "status": "FAILED",
                        "reason": "Not valid for fact check",
                    },
                    metadata={"error_reason": enhanced_input.error_reason},
                )
                return None
            enhanced_statement = enhanced_input.enhanced_statement
            logger.info(
                "Gemini analysis completed successfully",
                extra={
                    "json_fields": {
                        "verification_id": str(verification_id),
                        "enhanced_statement_length": len(enhanced_statement),
                        "has_preview_data": bool(enhanced_input.preview_data),
                        "base_operation": "fact_check",
                        "operation": "fact_check_gemini_success",
                    },
                    "labels": {"component": "fact_check_service", "phase": "gemini"},
                },
            )
        else:
            logger.warning(
                "No content available for fact checking",
                extra={
                    "json_fields": {
                        "verification_id": str(verification_id),
                        "has_statement": bool(statement),
                        "image_count": len(image_urls),
                        "base_operation": "fact_check",
                        "operation": "fact_check_no_content",
                    },
                    "labels": {"component": "fact_check_service", "phase": "validate"},
                },
            )
            await update_verification_status(
                verification_id, "FAILED", {"error": "No statement to check"}
            )
            if not verification.get("is_generated_news"):
                await send_notification(
                    verification.get("assignee_user_id"),
                    "ფოსტი ვერ გადამოწმდა",
                    "არ შეიცავს გადასამოწმებელ მასალას",
                    data={
                        "type": "fact_check_failed",
                        "verificationId": str(verification_id),
                    },
                )
            verification_span.update(
                output={"status": "FAILED", "reason": "No statement to check"}
            )
            return None

        # is_youtube_video = verification.get("youtube_id") is not None

        budget_tokens = 30000
        if settings.env == "dev":
            budget_tokens = 30000

        sources_length = len(verification.get("sources", []))
        if sources_length == 1:
            budget_tokens = 400000

        # Step 3: Use Jina to perform the fact check with the enhanced statement
        # This creates its own spans within the jina fact checking service

        jina_request = FactCheckRequest(
            details=enhanced_statement,
            budget_tokens=budget_tokens,
            verification_id=verification_id,
        )

        logger.info(
            "Starting Jina fact check analysis",
            extra={
                "json_fields": {
                    "verification_id": str(verification_id),
                    "budget_tokens": budget_tokens,
                    "enhanced_statement_length": len(enhanced_statement),
                    "sources_count": len(verification.get("sources", [])),
                    "base_operation": "fact_check",
                    "operation": "fact_check_jina_start",
                },
                "labels": {"component": "fact_check_service", "phase": "jina"},
            },
        )
        try:
            fact_check_data = await jina_check_fact(jina_request)
            if fact_check_data:
                logger.info(
                    "Jina fact check completed successfully",
                    extra={
                        "json_fields": {
                            "verification_id": str(verification_id),
                            "factuality_score": fact_check_data.factuality,
                            "references_count": len(fact_check_data.references)
                            if hasattr(fact_check_data, "references")
                            else 0,
                            "base_operation": "fact_check",
                            "operation": "fact_check_jina_success",
                        },
                        "labels": {"component": "fact_check_service", "phase": "jina"},
                    },
                )
            else:
                logger.error(
                    "Jina fact check returned no data",
                    extra={
                        "json_fields": {
                            "verification_id": str(verification_id),
                            "base_operation": "fact_check",
                            "operation": "fact_check_jina_no_data",
                        },
                        "labels": {
                            "component": "fact_check_service",
                            "severity": "high",
                        },
                    },
                )

        except Exception as e:
            logger.error(
                "Jina fact check failed",
                extra={
                    "json_fields": {
                        "verification_id": str(verification_id),
                        "error": str(e),
                        "error_type": type(e).__name__,
                        "base_operation": "fact_check",
                        "operation": "fact_check_jina_error",
                    },
                    "labels": {"component": "fact_check_service", "severity": "high"},
                },
            )
            # Don't re-raise, let the function handle None result
            fact_check_data = None

        if fact_check_data is None:
            await update_verification_status(
                verification_id, "FAILED", {"error": "Failed to check fact"}
            )
            logger.error(
                "Fact check process failed - no data returned",
                extra={
                    "json_fields": {
                        "verification_id": str(verification_id),
                        "base_operation": "fact_check",
                        "operation": "fact_check_failed",
                    },
                    "labels": {"component": "fact_check_service", "severity": "high"},
                },
            )
            if not verification.get("is_generated_news"):
                await send_notification(
                    verification.get("assignee_user_id"),
                    "ფოსტი ვერ გადამოწმდა",
                    "სცადეთ ხელახლა",
                    data={
                        "type": "fact_check_failed",
                        "verificationId": str(verification_id),
                    },
                )
                await mongo.verifications.update_one(
                    {"_id": verification_id},
                    {"$set": {"is_public": False}},
                )
            verification_span.update(
                output={"status": "FAILED", "reason": "Jina fact check failed"}
            )
            return None
        external_trace_id = "custom-" + str(uuid.uuid4())
        scoring_trace_id = langfuse.create_trace_id(
            seed=external_trace_id
        )  # 32 hexchar lowercase string, deterministic with seed

        # Prepare update data with fact check results
        update_data = {
            "fact_check_status": "COMPLETED",
            "fact_check_data": {
                **fact_check_data.model_dump(),
                "enhanced_statement": enhanced_statement,
                "extracted_image_text": extracted_image_text,
                "combined_statement": combined_statement,
            },
            "langfuse_trace_id": scoring_trace_id,
        }

        # Add preview data if it was generated
        if enhanced_input.preview_data is not None and not verification.get(
            "is_generated_news"
        ):
            if not verification.get("preview_data"):
                update_data["title"] = enhanced_input.preview_data.title
                update_data["text_content"] = enhanced_input.preview_data.description
            else:
                update_data["preview_data"] = {
                    **verification.get("preview_data"),
                    "title": enhanced_input.preview_data.title,
                    "description": enhanced_input.preview_data.description,
                }
                update_data["title"] = enhanced_input.preview_data.title
                update_data["text_content"] = enhanced_input.preview_data.description

        # Store the fact check data immediately in the verification object
        await mongo.verifications.update_one(
            {"_id": verification_id},
            {"$set": update_data},
        )

        logger.info(
            "Fact check data stored in database",
            extra={
                "json_fields": {
                    "verification_id": str(verification_id),
                    "has_preview_data": bool(enhanced_input.preview_data),
                    "factuality_score": fact_check_data.factuality,
                    "base_operation": "fact_check",
                    "operation": "fact_check_data_stored",
                },
                "labels": {"component": "fact_check_service", "phase": "store"},
            },
        )

        # Trigger translation after successful fact check completion
        try:
            await publish_translation_request(verification_id)
        except Exception as e:
            logger.error(
                "Failed to publish translation request",
                extra={
                    "json_fields": {
                        "verification_id": str(verification_id),
                        "error": str(e),
                        "error_type": type(e).__name__,
                        "base_operation": "fact_check",
                        "operation": "fact_check_translation_error",
                    },
                    "labels": {"component": "fact_check_service", "severity": "medium"},
                },
            )

        # Send notification that fact checking is completed
        if not verification.get("is_generated_news"):
            await send_notification(
                verification.get("assignee_user_id"),
                "ფაქტ ჩეკი დასრულდა",
                f"{fact_check_data.reason_summary}",
                data={
                    "type": "fact_check_completed",
                    "verificationId": str(verification_id),
                },
            )

        # Step 4: Generate score after fact check is completed
        logger.info(
            "Starting score generation",
            extra={
                "json_fields": {
                    "verification_id": str(verification_id),
                    "factuality_score": fact_check_data.factuality,
                    "base_operation": "fact_check",
                    "operation": "fact_check_score_start",
                },
                "labels": {"component": "fact_check_service", "phase": "score"},
            },
        )
        # Keep manual span for score generation fine-grained control

        score_response = await generate_score(
            gemini_client, enhanced_statement, fact_check_data.reason
        )

        logger.info(
            "Score generation completed",
            extra={
                "json_fields": {
                    "verification_id": str(verification_id),
                    "final_score": score_response.score,
                    "factuality_score": fact_check_data.factuality,
                    "base_operation": "fact_check",
                    "operation": "fact_check_score_complete",
                },
                "labels": {"component": "fact_check_service", "phase": "score"},
            },
        )

        logger.debug(
            "Score generation details",
            extra={
                "json_fields": {
                    "verification_id": str(verification_id),
                    "score_reasoning": score_response.reasoning,
                    "score_justification": score_response.justification,
                    "base_operation": "fact_check",
                    "operation": "fact_check_score_details",
                },
                "labels": {"component": "fact_check_service", "phase": "score"},
            },
        )

        # Update with the score data after it's generated
        await mongo.verifications.update_one(
            {"_id": verification_id},
            {
                "$set": {
                    "score_data": score_response.model_dump(),
                    "score": score_response.score,
                    "valid_until": calculate_valid_until_logarithmic(
                        score_response.score
                    ),
                }
            },
        )

        verification_duration = (time.time() - verification_start_time) * 1000

        verification_span.update(
            output={
                "status": "SUCCESS",
                "factuality_score": fact_check_data.factuality,
                "final_score": score_response.score,
                "references_count": len(fact_check_data.references),
            },
            metadata={
                "processing_duration_ms": verification_duration,
                "has_preview_data": bool(enhanced_input.preview_data),
            },
        )

    logger.info(
        "Fact check service completed successfully",
        extra={
            "json_fields": {
                "verification_id": str(verification_id),
                "final_score": score_response.score,
                "factuality_score": fact_check_data.factuality,
                "processing_duration_seconds": round(
                    (time.time() - verification_start_time), 2
                ),
                "base_operation": "fact_check",
                "operation": "fact_check_complete",
            },
            "labels": {"component": "fact_check_service", "phase": "complete"},
        },
    )
    return fact_check_data


translation_sys_instruct = (
    "You are a translation assistant which translates the English if there is any into Georgian language. "
    "preserving meaning, context, nuance, and idiomatic expressions. "
    "Ensure that cultural references and idiomatic language are appropriately adapted during translation. if there is English domain or references leave as it is."
)


@observe()
async def extract_statement(verification: LocationFeedPost) -> str:
    """
    Extract the statement from the verification object.
    Now uses @observe decorator for automatic tracing following v3 best practices.

    Args:
        verification (LocationFeedPost): The verification object.

    Returns:
        str: The extracted statement.
    """
    verification_id = verification.get("_id")
    user_id = verification.get("assignee_user_id")

    # Set trace input and metadata using v3 pattern
    langfuse.update_current_trace(
        input={
            "verification_id": str(verification_id),
            "has_text_content_english": bool(
                verification.get("text_content_in_english")
            ),
            "has_video_summary": bool(verification.get("ai_video_summary")),
            "has_text_content": bool(verification.get("text_content")),
        },
        user_id=user_id,
        metadata={
            "operation": "extract_statement",
            "verification_id": str(verification_id),
        },
    )

    if verification.get("text_content_in_english"):
        result = verification.get("text_content_in_english")

        # Update trace output using v3 pattern
        langfuse.update_current_trace(
            output={
                "statement_length": len(result),
                "source": "text_content_in_english",
            }
        )
        return result

    video_summary = verification.get("ai_video_summary")
    if video_summary is not None:
        components = []

        # First try to use the statements field which contains factual claims
        if (
            video_summary.get("statements") is not None
            and len(video_summary.get("statements")) > 0
        ):
            logger.debug(
                "Using video summary statements for extraction",
                extra={
                    "json_fields": {
                        "verification_id": str(verification_id),
                        "statements_count": len(video_summary.get("statements")),
                        "source": "video_statements",
                        "base_operation": "fact_check",
                        "operation": "statement_extract_video_statements",
                    },
                    "labels": {"component": "fact_check_service", "phase": "extract"},
                },
            )

            components.append(" ".join(video_summary.get("statements")))

        # If statements not available, try to use relevant_statements which contain timestamped content
        elif (
            video_summary.get("relevant_statements") is not None
            and len(video_summary.get("relevant_statements")) > 0
        ):
            logger.debug(
                "Using video relevant statements for extraction",
                extra={
                    "json_fields": {
                        "verification_id": str(verification_id),
                        "relevant_statements_count": len(
                            video_summary.get("relevant_statements")
                        ),
                        "source": "video_relevant_statements",
                        "base_operation": "fact_check",
                        "operation": "statement_extract_video_relevant",
                    },
                    "labels": {"component": "fact_check_service", "phase": "extract"},
                },
            )
            statements = [
                item.get("text")
                for item in video_summary.get("relevant_statements")
                if item.get("text")
            ]
            components.append("Video Relevant Statements: " + " ".join(statements))

        # If neither available, try to use short_summary
        elif video_summary.get("short_summary"):
            logger.debug(
                "Using video short summary for extraction",
                extra={
                    "json_fields": {
                        "verification_id": str(verification_id),
                        "summary_length": len(video_summary.get("short_summary")),
                        "source": "video_short_summary",
                        "base_operation": "fact_check",
                        "operation": "statement_extract_video_summary",
                    },
                    "labels": {"component": "fact_check_service", "phase": "extract"},
                },
            )

            components.append("Video Summary: " + video_summary.get("short_summary"))

        if components:
            result = " ".join(components)

            # Update trace output using v3 pattern
            langfuse.update_current_trace(
                output={
                    "statement_length": len(result),
                    "source": "video_summary",
                    "component_count": len(components),
                }
            )
            return result

    # If nothing else is available, use the original text content
    if verification.get("text_content"):
        result = verification.get("text_content")
        logger.debug(
            "Using original text content for extraction",
            extra={
                "json_fields": {
                    "verification_id": str(verification_id),
                    "text_length": len(result),
                    "source": "text_content",
                    "base_operation": "fact_check",
                    "operation": "statement_extract_text_content",
                },
                "labels": {"component": "fact_check_service", "phase": "extract"},
            },
        )

        # Update trace output using v3 pattern
        langfuse.update_current_trace(
            output={"statement_length": len(result), "source": "text_content"}
        )
        return result

    # Update trace output using v3 pattern
    langfuse.update_current_trace(
        output={"statement_length": 0, "source": "none"},
        metadata={"warning": "No statement extracted"},
    )
    return None
