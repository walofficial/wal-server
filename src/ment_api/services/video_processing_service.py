import json
import logging
from typing import Any, Dict, Optional
from datetime import datetime

from google.genai.types import GenerateContentConfig, Part, ThinkingConfig
from langfuse import observe
from ment_api.services.external_clients.langfuse_client import (
    langfuse,
)

from ment_api.models.location_feed_post import AIVideoSummary
from ment_api.services.external_clients.gemini_client import gemini_client_vertex_ai
from ment_api.services.video_processing_schemas import (
    COMMON_VIDEO_SUMMARY_PROMPT,
    VIDEO_SUMMARY_PROMPT,
    TRANSCRIPT_GENERATION_PROMPT,
)

# Initialize logger
logger = logging.getLogger(__name__)

# Model identifier (can potentially get this from GeminiClient instance if needed)
model_id = "gemini-2.5-flash"


@observe(as_type="generation")
async def generate_audio_transcript(
    audio_gcs_uri: str, chunk_start_time: int = 0
) -> Optional[Dict[str, Any]]:
    """
    Generate a transcript from an audio file stored in GCS using Gemini.

    Args:
        audio_gcs_uri: GCS URI of the audio file (e.g., gs://bucket-name/audio.mp3)
        chunk_start_time: If this is a chunk of a larger video, the start time in seconds of this chunk

    Returns:
        Dictionary containing transcript text and detected language or None if generation fails
    """
    try:
        logger.info(f"Generating transcript from audio: {audio_gcs_uri}")
        msg1_audio1 = Part.from_uri(
            file_uri=audio_gcs_uri,
            mime_type="audio/webm",
        )

        # Create a prompt that includes information about the chunk's position if relevant
        prompt_text = TRANSCRIPT_GENERATION_PROMPT
        logger.info(f"Prompt text: {prompt_text}")

        langfuse.update_current_generation(
            input=[prompt_text, msg1_audio1],
            model=model_id,
            metadata={
                "audio_gcs_uri": audio_gcs_uri,
            },
        )
        # Use a model that supports audio input
        response = await gemini_client_vertex_ai.aio.models.generate_content(
            model=model_id,  # Use the audio-compatible model
            contents=[
                prompt_text,
                msg1_audio1,
            ],
            config=GenerateContentConfig(
                response_mime_type="text/plain",  # Not needed for audio transcription with timestamps
                max_output_tokens=65500,  # Keep existing token limit for now
                thinking_config=ThinkingConfig(
                    include_thoughts=False,
                    thinking_budget=0,
                ),
            ),
        )

        langfuse.update_current_generation(
            usage_details={
                "input": response.usage_metadata.prompt_token_count,
                "output": response.usage_metadata.candidates_token_count,
                "cache_read_input_tokens": response.usage_metadata.cached_content_token_count,
            },
        )

        if not response or not response.text:
            logger.error("Empty transcript response from Gemini")
            return None

        transcript = response.text

        return {"transcript": transcript}

    except Exception as e:
        logger.error(f"Error generating transcript with Speech API: {e}")
        return None


@observe(as_type="generation")
async def generate_current_events(
    today: str,
) -> Optional[str]:
    contents = [
        "Search for current events in the country of Georgia from the past month and return it in 5 bullet points, and provide a list of events. Also describe a current political landscape in Georgia in 5 bullet points"
        + "Current date: "
        + today,
    ]

    langfuse.update_current_generation(
        input=contents,
        model="gemini-2.5-flash",
        metadata={
            "today": today,
        },
    )
    response_current_events = await gemini_client_vertex_ai.aio.models.generate_content(
        model="gemini-2.5-flash",
        contents=contents,
        config=GenerateContentConfig(
            response_mime_type="text/plain",
            max_output_tokens=8000,
            thinking_config=ThinkingConfig(
                include_thoughts=False,
                thinking_budget=0,
            ),
        ),
    )

    langfuse.update_current_generation(
        usage_details={
            "input": response_current_events.usage_metadata.prompt_token_count,
            "output": response_current_events.usage_metadata.candidates_token_count,
            "cache_read_input_tokens": response_current_events.usage_metadata.cached_content_token_count,
        },
    )

    current_events = response_current_events.text
    return current_events


@observe(as_type="generation")
async def generate_summary_from_transcript(
    transcript: str, video_title: Optional[str] = None
) -> Optional[AIVideoSummary]:
    """
    Generate a summary from a transcript using Gemini with structured output.

    Args:
        transcript: Video transcript text
        video_title: Optional title of the video to help with summary generation

    Returns:
        AIVideoSummary object or None if processing fails
    """

    try:
        today = datetime.now().strftime("%Y-%m-%d")
        current_events = await generate_current_events(today)

        # Select appropriate prompt based on transcript language
        system_instruction = COMMON_VIDEO_SUMMARY_PROMPT

        # Add video title to prompt if available
        title_context = f"\nVideo title: {video_title}" if video_title else ""

        formatted_prompt = (
            VIDEO_SUMMARY_PROMPT.format(
                transcript=transcript, today=today, current_events=current_events
            )
            + title_context
        )

        langfuse.update_current_generation(
            input=[formatted_prompt],
            model="gemini-2.5-pro",
            metadata={
                "current_events": current_events,
            },
        )
        # Generate summary from transcript with structured output
        response = await gemini_client_vertex_ai.aio.models.generate_content(
            model="gemini-2.5-pro",
            contents=[formatted_prompt],
            config=GenerateContentConfig(
                system_instruction=system_instruction,
                response_mime_type="application/json",
                response_schema=AIVideoSummary.model_json_schema(),
                temperature=1,
                max_output_tokens=64500,
                top_p=0.95,
                thinking_config=ThinkingConfig(
                    include_thoughts=False,
                    thinking_budget=10000,
                ),
            ),
        )

        langfuse.update_current_generation(
            usage_details={
                "input": response.usage_metadata.prompt_token_count,
                "output": response.usage_metadata.candidates_token_count,
                "cache_read_input_tokens": response.usage_metadata.cached_content_token_count,
            },
        )

        if not response or not response.text:
            print("Empty summary response from Gemini")
            return None

        # Parse the JSON response directly
        summary_data = json.loads(response.text)
        summary = AIVideoSummary(**summary_data)

        return summary

    except Exception as e:
        print(f"Error generating summary from transcript: {e}")
        return None
