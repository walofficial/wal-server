import json
import logging

from bson import ObjectId
from google.pubsub_v1 import ReceivedMessage
from langfuse import observe

from ment_api.events.social_media_scrape_event import SocialMediaScrapeEvent
from ment_api.models.location_feed_post import SocialMediaScrapeStatus
from ment_api.persistence import mongo
from ment_api.services.external_clients.langfuse_client import (
    langfuse,
)
from ment_api.services.social_media_scraper_service import scrape_social_media

logger = logging.getLogger(__name__)


@observe()
async def process_social_media_callback(message: ReceivedMessage) -> None:
    """
    Process a social media scrape request from the pubsub subscription.
    Now uses @observe decorator for automatic tracing following v3 best practices.
    """
    message_id = message.message.message_id

    # Set trace input using v3 pattern with safe context handling

    print(f"Processing social media callback for message, id: {message_id}")
    if not message or not message.message.data:
        logger.error("Received empty message")

        return

    try:
        # Parse the message data
        event_data = json.loads(message.message.data.decode("utf-8"))
        event = SocialMediaScrapeEvent(**event_data)

        verification_id = ObjectId(event.verification_id)

        # Set session_id to verification_id for session grouping using v3 pattern

        # Get the verification document
        verification = await mongo.verifications.find_one_by_id(verification_id)

        if not verification:
            logger.error(f"Verification not found: {event.verification_id}")

            return

        # Check if the verification has social media details
        if (
            not verification.get("social_media_scrape_details")
            or verification.get("social_media_scrape_status")
            != SocialMediaScrapeStatus.PENDING
        ):
            logger.error(
                f"Verification {event.verification_id} does not have pending social media details"
            )

            return

        # Extract the social media URL and platform
        social_media_details = verification["social_media_scrape_details"]
        social_url = social_media_details.get("url")
        platform = social_media_details.get("platform")

        if not social_url or not platform:
            logger.error(
                f"Missing social media URL or platform for verification {event.verification_id}"
            )

            return

        # Update trace with detailed input

        # Process the social media scrape - this will create its own spans
        logger.info(f"Scraping social media for verification {event.verification_id}")
        await scrape_social_media(verification_id, social_url, platform)

    except Exception as e:
        logger.error(f"Error in social media callback: {e}", exc_info=True)
        raise

    finally:
        # Flush in short-lived environments (Pub/Sub workers)
        langfuse.flush()
