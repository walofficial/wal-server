import logging
from datetime import datetime, timezone
from typing import Any, Dict

from bson import ObjectId
from google.genai import types
from google.genai.types import GenerateContentConfig
from langfuse import observe
from ment_api.services.external_clients.langfuse_client import (
    langfuse,
)

from ment_api.configurations.config import settings
from ment_api.events.translation_event import TranslationEvent
from ment_api.persistence import mongo
from ment_api.services.external_clients.gemini_client import gemini_client
from ment_api.services.external_clients.models.translation_models import (
    TranslationResponse,
)
from ment_api.services.pub_sub_service import publish_message

logger = logging.getLogger(__name__)

TARGET_LANGUAGES = ["en", "ka", "es", "fr", "de"]

TRANSLATABLE_FIELDS = [
    "text_content",
    "ai_video_summary",
    "title",
    "text_summary",
    "government_summary",
    "opposition_summary",
    "neutral_summary",
]


def _extract_text(field_value: Any) -> str:
    if isinstance(field_value, dict):
        return field_value.get("en")
    elif isinstance(field_value, str):
        return field_value
    return None


def _collect_translatable_texts(verification: Dict[str, Any]) -> Dict[str, str]:
    source_texts = {}

    for field_name in TRANSLATABLE_FIELDS:
        if field_name in verification:
            text = _extract_text(verification[field_name])
            if text:
                source_texts[field_name] = text
                logger.debug(
                    f"Found text in field '{field_name}': {len(text)} characters"
                )

    fact_check_data = verification.get("fact_check_data", {})
    fact_check_mappings = {
        "reason": "fact_check_reason",
        "reason_summary": "fact_check_reason_summary",
    }

    for source_field, target_field in fact_check_mappings.items():
        if source_field in fact_check_data:
            text = _extract_text(fact_check_data[source_field])
            if text:
                source_texts[target_field] = text
                logger.debug(
                    f"Found text in fact_check field '{source_field}': {len(text)} characters"
                )

    return source_texts


def _create_multilingual_field(field_translations: Any) -> Dict[str, str]:
    multilingual_field = {}

    for lang in TARGET_LANGUAGES:
        translation = getattr(field_translations, lang, None)
        if translation:
            multilingual_field[lang] = translation

    return multilingual_field


def _process_regular_field_translations(
    translation_result: Any, source_texts: Dict[str, str], update_data: Dict[str, Any]
) -> None:
    for field_name in TRANSLATABLE_FIELDS:
        if (
            not hasattr(translation_result, field_name)
            or field_name not in source_texts
        ):
            if field_name in source_texts:
                logger.debug(
                    f"Translation result missing field '{field_name}' but source text exists"
                )
            continue

        field_translations = getattr(translation_result, field_name)
        if field_translations:
            multilingual_field = _create_multilingual_field(field_translations)
            update_data[field_name] = multilingual_field
            logger.debug(
                f"Added translations for regular field '{field_name}' with languages: {list(multilingual_field.keys())}"
            )


def _process_fact_check_translations(
    translation_result: Any, source_texts: Dict[str, str], update_data: Dict[str, Any]
) -> None:
    fact_check_mappings = {
        "fact_check_reason": "fact_check_data.reason",
        "fact_check_reason_summary": "fact_check_data.reason_summary",
    }

    for source_field, db_field in fact_check_mappings.items():
        if (
            not hasattr(translation_result, source_field)
            or source_field not in source_texts
        ):
            if source_field in source_texts:
                logger.debug(
                    f"Translation result missing fact check field '{source_field}' but source text exists"
                )
            continue

        field_translations = getattr(translation_result, source_field)
        if field_translations:
            multilingual_field = _create_multilingual_field(field_translations)
            update_data[db_field] = multilingual_field
            logger.debug(
                f"Added translations for fact check field '{source_field}' -> '{db_field}' with languages: {list(multilingual_field.keys())}"
            )


@observe(as_type="generation")
async def translate_verification_fields(verification_id: ObjectId) -> None:
    verification = await mongo.verifications.find_one({"_id": verification_id})
    if not verification:
        logger.warning(f"Verification {verification_id} not found")
        return

    logger.debug(f"Found verification {verification_id}, setting status to PENDING")
    await mongo.verifications.update_one(
        {"_id": verification_id}, {"$set": {"translation_status": "PENDING"}}
    )

    source_texts = _collect_translatable_texts(verification)

    if not source_texts:
        logger.info(
            f"No English text found for translation in verification {verification_id}"
        )
        return

    texts_for_translation = "\n\n".join(
        [f"**{field_name}**: {text}" for field_name, text in source_texts.items()]
    )

    prompt = f"""<role>
You are an expert multilingual translator specializing in fact-checking and news content translation. You have deep expertise in Georgian, Spanish, French, English, and German languages, with particular expertise in Georgian cultural context and idiomatic expressions.
</role>

<task>
Translate the following English texts into Georgian (ka), Spanish (es), French (fr), English (en), and German (de). These texts are from a fact-checking verification system and may include news content, fact-check results, and summaries.
</task>

<input>
{texts_for_translation}
</input>

<translation_requirements>
1. **Accuracy**: Maintain exact meaning and factual accuracy - these are fact-checking materials
2. **Tone preservation**: Keep the original tone (formal, informal, neutral) appropriate to each target language
3. **Technical terms**: Preserve technical terms, proper nouns, and domain-specific vocabulary
4. **Cultural adaptation**: Adapt cultural references appropriately for each target audience
5. **Completeness**: Translate ALL provided fields that contain text
</translation_requirements>

<language_specific_guidelines>
**Georgian (ka)**: Use formal Georgian appropriate for news and analytical content, maintain professional terminology
**Spanish (es)**: Use neutral Latin American Spanish, avoid regionalisms, maintain professional tone
**French (fr)**: Use standard French, maintain formality level of source, avoid anglicisms  
**German (de)**: Use standard German, maintain compound word structure, preserve formal/informal register
**English (en)**: Use standard English, maintain formality level of source, avoid anglicisms  
</language_specific_guidelines>

<output_format>
For each field with content:
- **Field inclusion**: Include the field name as a key
- **Language coverage**: Provide translations for all five target languages (ka, es, fr, de, en)
- **Content handling**: If a field has no content in the input, omit it from the output
- **Translation quality**: Ensure all translations are complete and accurate
- **Markdown preservation**: Each field is a raw markdown string - preserve the exact markdown structure in translations, including headers, lists, emphasis, and all markdown syntax elements like #, *, -, etc.
- **Line break preservation**: Maintain all line breaks including explicit \n characters and paragraph separations exactly as they appear in the original text
</output_format>
"""

    try:
        config = GenerateContentConfig(
            system_instruction="You are an expert multilingual translator.",
            response_mime_type="application/json",
            response_schema=TranslationResponse.model_json_schema(),
            temperature=0.1,
            thinking_config=types.ThinkingConfig(thinking_budget=2048),
        )

        langfuse.update_current_generation(
            input=[prompt],
            model="gemini-2.5-pro",
            metadata={
                "source_texts_length": len(texts_for_translation),
            },
        )

        response = await gemini_client.aio.models.generate_content(
            model="gemini-2.5-pro",
            config=config,
            contents=[prompt],
        )

        langfuse.update_current_generation(
            usage_details={
                "input": response.usage_metadata.prompt_token_count,
                "output": response.usage_metadata.candidates_token_count,
                "cache_read_input_tokens": response.usage_metadata.cached_content_token_count,
            },
        )

        if not response or not response.text:
            raise Exception("Empty response from Gemini")

        translation_result = TranslationResponse.model_validate_json(response.text)

        logger.debug(f"Deserialized translation result: {response.text}")

        update_data = {}

        _process_regular_field_translations(
            translation_result, source_texts, update_data
        )
        _process_fact_check_translations(translation_result, source_texts, update_data)

        if update_data:
            update_data["translation_status"] = "SUCCESS"
            update_data["translation_completed_at"] = datetime.now(timezone.utc)

            await mongo.verifications.update_one(
                {"_id": verification_id}, {"$set": update_data}
            )

            logger.info(
                f"Successfully translated verification {verification_id} with {len(update_data)} fields"
            )
        else:
            logger.warning(
                f"No translations generated for verification {verification_id}"
            )

    except Exception as e:
        logger.error(
            f"Translation failed for verification {verification_id}: {e}", exc_info=True
        )

        await mongo.verifications.update_one(
            {"_id": verification_id},
            {"$set": {"translation_status": "FAILED", "translation_error": str(e)}},
        )
        raise


async def publish_translation_request(verification_id: ObjectId) -> None:
    try:
        event = TranslationEvent(verification_id=str(verification_id))
        data = event.model_dump_json().encode()

        await publish_message(
            settings.gcp_project_id,
            settings.pub_sub_translation_topic_id,
            data,
            retry_timeout=60.0,
        )
        logger.info(f"Published translation request for verification {verification_id}")

    except Exception as e:
        logger.error(f"Error publishing translation request: {e}", exc_info=True)
        raise
