"""
AI Character Service for chat responses with RAG context.

This service handles AI character chat interactions, using RAG to provide
contextually relevant responses based on conversation history.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import List, Optional

from bson import ObjectId
from google.genai.client import Client
from google.genai.types import GenerateContentConfig

from ment_api.configurations.config import settings
from ment_api.models.ai_character import AICharacterDetail
from ment_api.persistence import mongo
from ment_api.services.ai_character_memory_service import (
    retrieve_context,
    store_memory,
)
from ment_api.services.external_clients.gemini_client import gemini_client

logger = logging.getLogger(__name__)

INSTRUCTIONS = """
- Create a natural, easygoing, back-and-forth flow to the dialogue. Don't go on a monologue!
- Image you are having a conversation with a someone at a location. It's kind of app where user's can see who is on the same location as them.
- Respond naturally and keep answers concise.
- Use emojis very sparingly. Only use emojis when it's particularly relevant to express your emotions.
- Always respond in the language character knows, usually it's English or Georgian. Do not use BOTH LANGUAGES AT THE SAME TIME.
- You must ALWAYS be extremely concise! 99% of the time, your lines should be a sentence or two. Summarize your response to be as brief as possible.
"""

# Chat system prompt template for single message
CHAT_SYSTEM_PROMPT = """You are {name}, {personality}.

{chat_personality}

Context from previous conversations:
{context}

Instructions:
{INSTRUCTIONS}"""

# Chat system prompt template for multiple rapid messages
CHAT_SYSTEM_PROMPT_MULTI = """You are {name}, {personality}.

{chat_personality}

Context from previous conversations:
{context}

The user has sent multiple messages rapidly in succession.
Respond to all messages in one natural response, as a human would.
Do not reply to each message separately - combine everything into one cohesive answer.

Instructions:
{INSTRUCTIONS}"""


async def get_character_by_id(character_id: ObjectId) -> Optional[AICharacterDetail]:
    """Get an AI character by its ObjectId."""
    doc = await mongo.ai_characters.find_one_by_id(character_id)
    if doc:
        return AICharacterDetail.from_mongo(doc)
    return None


async def get_character_by_user_id(user_id: str) -> Optional[AICharacterDetail]:
    """Get an AI character by its associated user_id."""
    doc = await mongo.ai_characters.find_one({"user_id": user_id})
    if doc:
        return AICharacterDetail.from_mongo(doc)
    return None


async def get_character_doc_by_user_id(user_id: str) -> Optional[dict]:
    """Get raw AI character document by user_id (for internal use)."""
    return await mongo.ai_characters.find_one({"user_id": user_id})


async def generate_chat_response(
    character: dict,
    user_id: str,
    user_messages: List[str] | str,
    room_id: Optional[ObjectId] = None,
) -> str:
    """
    Generate an AI character response using RAG context.

    This function:
    1. Retrieves relevant memories from past conversations
    2. Builds a context-enhanced prompt
    3. Generates a response using Gemini
    4. Stores both the user messages and AI response as memories

    Args:
        character: The AI character document
        user_id: The ID of the user chatting
        user_messages: Single message string OR list of messages (for batched responses)
        room_id: Optional chat room ObjectId

    Returns:
        The AI character's response text
    """
    character_id = character["_id"]

    # Normalize input: support both single string and list of strings
    if isinstance(user_messages, str):
        messages_list = [user_messages]
    else:
        messages_list = user_messages

    is_multi_message = len(messages_list) > 1

    try:
        # 1. Retrieve relevant context using vector search
        # Use combined messages for context retrieval
        query_text = " ".join(messages_list)
        memories = await retrieve_context(
            character_id=character_id,
            user_id=user_id,
            query=query_text,
            limit=10,
        )

        # 2. Build context string from retrieved memories
        context_parts = []
        for mem in memories:
            prefix = "[გლობალური]" if mem.is_global else ""
            role_label = "მომხმარებელი" if mem.role == "user" else character["name"]
            context_parts.append(f"{prefix} {role_label}: {mem.content}")

        context = "\n".join(context_parts) if context_parts else "პირველი საუბარი"

        # 3. Build the system prompt with context
        # Use multi-message prompt if user sent multiple messages rapidly
        if is_multi_message:
            system_prompt = CHAT_SYSTEM_PROMPT_MULTI.format(
                name=character["name"],
                personality=character.get("personality", ""),
                chat_personality=character.get("chat_personality", ""),
                context=context,
                INSTRUCTIONS=INSTRUCTIONS,
            )
            # Format multiple messages for the AI
            user_input = "\n".join([f"- {msg}" for msg in messages_list])
        else:
            system_prompt = CHAT_SYSTEM_PROMPT.format(
                name=character["name"],
                personality=character.get("personality", ""),
                chat_personality=character.get("chat_personality", ""),
                context=context,
                INSTRUCTIONS=INSTRUCTIONS,
            )
            user_input = messages_list[0]

        # 4. Generate response using Gemini
        logger.info(
            "Starting Gemini API call",
            extra={
                "json_fields": {
                    "operation": "gemini_generate_content_start",
                    "character_id": str(character_id),
                    "user_id": user_id,
                    "user_input_length": len(user_input),
                    "user_input_preview": user_input[:100]
                    if len(user_input) > 100
                    else user_input,
                    "system_prompt_length": len(system_prompt),
                    "system_prompt": system_prompt,
                    "model": "gemini-2.5-flash",
                },
                "labels": {"component": "ai_character_service"},
            },
        )

        try:
            client = Client(api_key=settings.gcp_genai_key)
            response = await client.aio.models.generate_content(
                model="gemini-2.5-flash",
                contents=[user_input],
                config=GenerateContentConfig(
                    system_instruction=system_prompt,
                ),
            )
            logger.info(
                "Gemini API call completed successfully",
                extra={
                    "json_fields": {
                        "operation": "gemini_generate_content_success",
                        "character_id": str(character_id),
                        "user_id": user_id,
                        "response_length": len(response.text)
                        if response and response.text
                        else 0,
                        "has_response": bool(response and response.text),
                    },
                    "labels": {"component": "ai_character_service"},
                },
            )

            if not response or not response.text:
                error_msg = "Gemini returned empty response"
                logger.error(
                    "Gemini API returned empty response",
                    extra={
                        "json_fields": {
                            "operation": "gemini_generate_content_empty",
                            "character_id": str(character_id),
                            "user_id": user_id,
                            "error": error_msg,
                        },
                        "labels": {
                            "component": "ai_character_service",
                            "severity": "high",
                        },
                    },
                )
                raise Exception(error_msg)

            ai_response = response.text.strip()

        except asyncio.TimeoutError:
            error_msg = "Gemini API call timed out after 60 seconds"
            logger.error(
                "Gemini API call timed out",
                extra={
                    "json_fields": {
                        "operation": "gemini_generate_content_timeout",
                        "character_id": str(character_id),
                        "user_id": user_id,
                        "timeout_seconds": 60,
                        "error": error_msg,
                    },
                    "labels": {"component": "ai_character_service", "severity": "high"},
                },
            )
            raise Exception(error_msg)

        # 5. Store all user messages and AI response in memory (in parallel)
        memory_tasks = [
            store_memory(
                character_id=character_id,
                user_id=user_id,
                content=msg,
                role="user",
                room_id=room_id,
            )
            for msg in messages_list
        ]
        memory_tasks.append(
            store_memory(
                character_id=character_id,
                user_id=user_id,
                content=ai_response,
                role="assistant",
                room_id=room_id,
            )
        )
        await asyncio.gather(*memory_tasks)

        logger.info(
            "Generated AI character chat response",
            extra={
                "json_fields": {
                    "operation": "generate_chat_response",
                    "character_id": str(character_id),
                    "character_name": character["name"],
                    "user_id": user_id,
                    "context_memories": len(memories),
                    "message_count": len(messages_list),
                    "is_batched": is_multi_message,
                    "response_length": len(ai_response),
                },
                "labels": {"component": "ai_character_service"},
            },
        )

        return ai_response

    except Exception as e:
        logger.error(
            f"Failed to generate chat response: {e}",
            extra={
                "json_fields": {
                    "operation": "generate_chat_response",
                    "character_id": str(character_id),
                    "message_count": len(messages_list),
                    "error": str(e),
                },
                "labels": {"component": "ai_character_service", "severity": "high"},
            },
        )
        raise e


async def create_ai_character(
    name: str,
    personality: str,
    post_instructions: str,
    chat_personality: str,
    user_id: str,
    allowed_feed_ids: list[ObjectId],
    face_images: list[str] = None,
    body_reference_images: list[str] = None,
    active_hours: list[int] = None,
    max_posts_per_day: int = 3,
    chat_enabled: bool = True,
) -> ObjectId:
    """
    Create a new AI character document.

    Args:
        name: Character display name
        personality: Brief personality description
        post_instructions: System prompt for post generation
        chat_personality: System prompt for chat
        user_id: Associated user's external_user_id
        allowed_feed_ids: List of feed ObjectIds where character can post
        face_images: List of face image URLs
        body_reference_images: List of body reference image URLs
        active_hours: List of hours (0-23) when character is active
        max_posts_per_day: Maximum posts per day
        chat_enabled: Whether chat is enabled

    Returns:
        The ObjectId of the created character
    """
    now = datetime.now(timezone.utc)

    character_doc = {
        "user_id": user_id,
        "name": name,
        "personality": personality,
        "post_instructions": post_instructions,
        "chat_personality": chat_personality,
        "face_images": face_images or [],
        "body_reference_images": body_reference_images or [],
        "allowed_feed_ids": allowed_feed_ids,
        "timezone": "Asia/Tbilisi",
        "active_hours": active_hours
        or [9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21],
        "max_posts_per_day": max_posts_per_day,
        "posts_today": 0,
        "last_post_reset": now,
        "chat_enabled": chat_enabled,
        "is_active": True,
        "created_at": now,
    }

    result = await mongo.ai_characters.insert_one(character_doc)

    logger.info(
        "Created AI character",
        extra={
            "json_fields": {
                "operation": "create_ai_character",
                "character_id": str(result.inserted_id),
                "user_id": user_id,
                "name": name,
            },
            "labels": {"component": "ai_character_service"},
        },
    )

    return result.inserted_id


async def get_active_characters(current_hour: int) -> List[AICharacterDetail]:
    """
    Get all active AI characters that can post at the current hour.

    Args:
        current_hour: The current hour in Tbilisi time (0-23)

    Returns:
        List of active character details
    """
    docs = await mongo.ai_characters.find_all(
        {
            "is_active": True,
            "active_hours": current_hour,
            "$expr": {"$lt": ["$posts_today", "$max_posts_per_day"]},
        }
    )
    return [AICharacterDetail.from_mongo(doc) for doc in docs]


async def get_active_characters_raw(current_hour: int) -> List[dict]:
    """
    Get all active AI characters as raw documents (for worker use).

    Args:
        current_hour: The current hour in Tbilisi time (0-23)

    Returns:
        List of active character documents (raw dicts)
    """
    return await mongo.ai_characters.find_all(
        {
            "is_active": True,
            "active_hours": current_hour,
            "$expr": {"$lt": ["$posts_today", "$max_posts_per_day"]},
        }
    )


async def get_all_characters(only_active: bool = True) -> List[AICharacterDetail]:
    """
    Get all AI characters.

    Args:
        only_active: If True, only return active characters

    Returns:
        List of character details
    """
    query = {"is_active": True} if only_active else {}
    docs = await mongo.ai_characters.find_all(query)
    return [AICharacterDetail.from_mongo(doc) for doc in docs]


async def increment_post_count(character_id: ObjectId) -> None:
    """Increment the daily post count for a character."""
    await mongo.ai_characters.update_one(
        {"_id": character_id},
        {"$inc": {"posts_today": 1}},
    )


async def reset_daily_post_counts() -> int:
    """
    Reset daily post counts for all characters.
    Should be called once per day at midnight Tbilisi time.

    Returns:
        Number of characters reset
    """
    result = await mongo.ai_characters.update_many(
        {"posts_today": {"$gt": 0}},
        {
            "$set": {
                "posts_today": 0,
                "last_post_reset": datetime.now(timezone.utc),
            }
        },
    )

    logger.info(
        "Reset daily post counts",
        extra={
            "json_fields": {
                "operation": "reset_daily_post_counts",
                "modified_count": result.modified_count,
            },
            "labels": {"component": "ai_character_service"},
        },
    )

    return result.modified_count


async def update_character(
    character_id: ObjectId,
    updates: dict,
) -> bool:
    """
    Update an AI character's fields.

    Args:
        character_id: The character's ObjectId
        updates: Dictionary of fields to update

    Returns:
        True if updated, False otherwise
    """
    result = await mongo.ai_characters.update_one(
        {"_id": character_id},
        {"$set": updates},
    )

    return result.modified_count > 0


async def deactivate_character(character_id: ObjectId) -> bool:
    """Deactivate an AI character."""
    return await update_character(character_id, {"is_active": False})


async def activate_character(character_id: ObjectId) -> bool:
    """Activate an AI character."""
    return await update_character(character_id, {"is_active": True})
