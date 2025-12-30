"""
AI Character Memory Service for RAG (Retrieval Augmented Generation).

This service handles storage and retrieval of conversation memories
for AI characters using MongoDB Vector Search.
"""

import logging
from datetime import datetime, timezone
from typing import List, Optional

from bson import ObjectId

from ment_api.models.ai_character import MemoryCountResponse, MemoryDetail
from ment_api.persistence import mongo
from ment_api.services.gemini_embedding_service import (
    embed_for_retrieval_document,
    embed_for_retrieval_query,
)

logger = logging.getLogger(__name__)


async def store_memory(
    character_id: ObjectId,
    user_id: Optional[str],
    content: str,
    role: str,
    room_id: Optional[ObjectId] = None,
    is_global: bool = False,
    topic: Optional[str] = None,
) -> ObjectId:
    """
    Store a message with its embedding in the AI character memories collection.

    Args:
        character_id: The AI character's ObjectId
        user_id: The user's ID (None for global memories)
        content: The message text content
        role: Either "user" or "assistant"
        room_id: Optional chat room ID
        is_global: If True, this memory is shared across all users
        topic: Optional topic/category for the memory

    Returns:
        The ObjectId of the inserted memory document
    """
    try:
        # Generate embedding for the content
        embedding = await embed_for_retrieval_document(content)

        memory_doc = {
            "character_id": character_id,
            "user_id": user_id,
            "is_global": is_global,
            "content": content,
            "role": role,
            "embedding": embedding,
            "metadata": {
                "room_id": room_id,
                "timestamp": datetime.now(timezone.utc),
                "topic": topic,
            },
            "created_at": datetime.now(timezone.utc),
        }

        result = await mongo.ai_character_memories.insert_one(memory_doc)

        logger.info(
            "Stored memory for AI character",
            extra={
                "json_fields": {
                    "operation": "store_memory",
                    "character_id": str(character_id),
                    "user_id": user_id,
                    "role": role,
                    "is_global": is_global,
                    "content_length": len(content),
                },
                "labels": {"component": "ai_character_memory_service"},
            },
        )

        return result.inserted_id

    except Exception as e:
        logger.error(
            f"Failed to store memory: {e}",
            extra={
                "json_fields": {
                    "operation": "store_memory",
                    "character_id": str(character_id),
                    "error": str(e),
                },
                "labels": {
                    "component": "ai_character_memory_service",
                    "severity": "high",
                },
            },
        )
        raise


async def retrieve_context(
    character_id: ObjectId,
    user_id: str,
    query: str,
    limit: int = 10,
) -> List[MemoryDetail]:
    """
    Retrieve relevant memories using hybrid retrieval (per-user + global memories).

    Uses MongoDB Vector Search to find semantically similar memories
    that belong to the specific user OR are marked as global.

    Args:
        character_id: The AI character's ObjectId
        user_id: The current user's ID
        query: The query text to find relevant context for
        limit: Maximum number of memories to retrieve

    Returns:
        List of MemoryDetail with content, role, is_global, and similarity score
    """
    try:
        # Generate embedding for the query
        query_embedding = await embed_for_retrieval_query(query)

        # MongoDB Atlas Vector Search pipeline with hybrid filtering
        pipeline = [
            {
                "$vectorSearch": {
                    "index": "memory_vector_index",
                    "path": "embedding",
                    "queryVector": query_embedding,
                    "numCandidates": limit * 10,  # Consider more candidates for better results
                    "limit": limit,
                    "filter": {
                        "$and": [
                            {"character_id": character_id},
                            {
                                "$or": [
                                    {"user_id": user_id},
                                    {"is_global": True},
                                ]
                            },
                        ]
                    },
                }
            },
            {
                "$project": {
                    "_id": 1,
                    "character_id": 1,
                    "user_id": 1,
                    "content": 1,
                    "role": 1,
                    "is_global": 1,
                    "metadata": 1,
                    "created_at": 1,
                    "score": {"$meta": "vectorSearchScore"},
                }
            },
        ]

        docs = await mongo.ai_character_memories.aggregate(pipeline)
        memories = [MemoryDetail.from_mongo(doc) for doc in docs]

        logger.info(
            "Retrieved context for AI character",
            extra={
                "json_fields": {
                    "operation": "retrieve_context",
                    "character_id": str(character_id),
                    "user_id": user_id,
                    "query_length": len(query),
                    "results_count": len(memories),
                },
                "labels": {"component": "ai_character_memory_service"},
            },
        )

        return memories

    except Exception as e:
        logger.error(
            f"Failed to retrieve context: {e}",
            extra={
                "json_fields": {
                    "operation": "retrieve_context",
                    "character_id": str(character_id),
                    "error": str(e),
                },
                "labels": {
                    "component": "ai_character_memory_service",
                    "severity": "high",
                },
            },
        )
        # Return empty list on error to allow chat to continue without context
        return []


async def get_recent_memories(
    character_id: ObjectId,
    user_id: Optional[str] = None,
    limit: int = 20,
    include_global: bool = True,
) -> List[MemoryDetail]:
    """
    Get recent memories for a character, optionally filtered by user.

    Args:
        character_id: The AI character's ObjectId
        user_id: Optional user ID to filter by
        limit: Maximum number of memories to retrieve
        include_global: Whether to include global memories

    Returns:
        List of recent memory details
    """
    query: dict = {"character_id": character_id}

    if user_id:
        if include_global:
            query["$or"] = [{"user_id": user_id}, {"is_global": True}]
        else:
            query["user_id"] = user_id

    docs = await mongo.ai_character_memories.find_all(
        query, sort=[("created_at", -1)]
    )

    return [MemoryDetail.from_mongo(doc) for doc in docs[:limit]]


async def add_global_memory(
    character_id: ObjectId,
    content: str,
    topic: Optional[str] = None,
) -> ObjectId:
    """
    Add a global memory that will be shared across all users.

    Useful for adding backstory, facts about the character, or
    information that should be available in all conversations.

    Args:
        character_id: The AI character's ObjectId
        content: The memory content
        topic: Optional topic/category

    Returns:
        The ObjectId of the inserted memory
    """
    return await store_memory(
        character_id=character_id,
        user_id=None,
        content=content,
        role="assistant",  # Global memories are considered as character knowledge
        room_id=None,
        is_global=True,
        topic=topic,
    )


async def get_memory_count(
    character_id: ObjectId,
    user_id: Optional[str] = None,
) -> MemoryCountResponse:
    """
    Get memory statistics for a character.

    Args:
        character_id: The AI character's ObjectId
        user_id: Optional user ID to filter by

    Returns:
        MemoryCountResponse with total, global, and per-user counts
    """
    base_query: dict = {"character_id": character_id}

    total = await mongo.ai_character_memories.count_documents(base_query)
    global_count = await mongo.ai_character_memories.count_documents(
        {**base_query, "is_global": True}
    )

    user_count = 0
    if user_id:
        user_count = await mongo.ai_character_memories.count_documents(
            {**base_query, "user_id": user_id}
        )

    return MemoryCountResponse(
        total=total,
        global_count=global_count,
        user_count=user_count,
    )


async def delete_user_memories(
    character_id: ObjectId,
    user_id: str,
) -> int:
    """
    Delete all memories for a specific user (does not delete global memories).

    Args:
        character_id: The AI character's ObjectId
        user_id: The user's ID

    Returns:
        Number of deleted memories
    """
    result = await mongo.ai_character_memories.delete_all(
        {"character_id": character_id, "user_id": user_id, "is_global": False}
    )

    logger.info(
        "Deleted user memories for AI character",
        extra={
            "json_fields": {
                "operation": "delete_user_memories",
                "character_id": str(character_id),
                "user_id": user_id,
                "deleted_count": result.deleted_count,
            },
            "labels": {"component": "ai_character_memory_service"},
        },
    )

    return result.deleted_count

