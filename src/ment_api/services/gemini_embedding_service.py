"""
Gemini Embedding Service using gemini-embedding-001 model.

This service provides text embedding capabilities for RAG (Retrieval Augmented Generation)
using Google's Gemini embedding model with 768-dimensional output.
"""

import logging
from typing import List

from google.genai import types as genai_types
from tenacity import (
    before_sleep_log,
    retry,
    stop_after_attempt,
    wait_random_exponential,
)

from ment_api.services.external_clients.gemini_client import gemini_client

logger = logging.getLogger(__name__)

# Model configuration
EMBEDDING_MODEL = "gemini-embedding-001"
EMBEDDING_DIMENSIONS = 768


@retry(
    wait=wait_random_exponential(min=1, max=20),
    stop=stop_after_attempt(3),
    before_sleep=before_sleep_log(logger, logging.WARNING),
)
async def embed_text(
    text: str,
    task_type: str = "RETRIEVAL_DOCUMENT",
) -> List[float]:
    """
    Generate a 768-dimensional embedding for a single text using gemini-embedding-001.

    Args:
        text: The text to embed
        task_type: The type of embedding task. Options:
            - "RETRIEVAL_DOCUMENT": For documents to be retrieved
            - "RETRIEVAL_QUERY": For search queries
            - "SEMANTIC_SIMILARITY": For comparing text similarity
            - "CLASSIFICATION": For text classification
            - "CLUSTERING": For clustering texts

    Returns:
        List of 768 float values representing the embedding
    """
    try:
        response = await gemini_client.aio.models.embed_content(
            model=EMBEDDING_MODEL,
            contents=text,
            config=genai_types.EmbedContentConfig(
                task_type=task_type,
                output_dimensionality=EMBEDDING_DIMENSIONS,
            ),
        )

        if response and response.embeddings:
            embedding = response.embeddings[0].values
            logger.debug(
                "Generated embedding",
                extra={
                    "json_fields": {
                        "operation": "embed_text",
                        "text_length": len(text),
                        "embedding_dims": len(embedding),
                        "task_type": task_type,
                    },
                    "labels": {"component": "gemini_embedding_service"},
                },
            )
            return list(embedding)

        logger.error(
            "Failed to generate embedding - empty response",
            extra={
                "json_fields": {"operation": "embed_text", "text_length": len(text)},
                "labels": {"component": "gemini_embedding_service"},
            },
        )
        raise ValueError("Empty embedding response from Gemini")

    except Exception as e:
        logger.error(
            f"Error generating embedding: {e}",
            extra={
                "json_fields": {
                    "operation": "embed_text",
                    "error": str(e),
                    "text_length": len(text),
                },
                "labels": {"component": "gemini_embedding_service", "severity": "high"},
            },
        )
        raise


@retry(
    wait=wait_random_exponential(min=1, max=20),
    stop=stop_after_attempt(3),
    before_sleep=before_sleep_log(logger, logging.WARNING),
)
async def embed_texts_batch(
    texts: List[str],
    task_type: str = "RETRIEVAL_DOCUMENT",
) -> List[List[float]]:
    """
    Generate embeddings for multiple texts in a single API call.

    Args:
        texts: List of texts to embed
        task_type: The type of embedding task

    Returns:
        List of embeddings, each being a list of 768 float values
    """
    if not texts:
        return []

    try:
        response = await gemini_client.aio.models.embed_content(
            model=EMBEDDING_MODEL,
            contents=texts,
            config=genai_types.EmbedContentConfig(
                task_type=task_type,
                output_dimensionality=EMBEDDING_DIMENSIONS,
            ),
        )

        if response and response.embeddings:
            embeddings = [list(e.values) for e in response.embeddings]
            logger.info(
                "Generated batch embeddings",
                extra={
                    "json_fields": {
                        "operation": "embed_texts_batch",
                        "num_texts": len(texts),
                        "task_type": task_type,
                    },
                    "labels": {"component": "gemini_embedding_service"},
                },
            )
            return embeddings

        logger.error(
            "Failed to generate batch embeddings - empty response",
            extra={
                "json_fields": {
                    "operation": "embed_texts_batch",
                    "num_texts": len(texts),
                },
                "labels": {"component": "gemini_embedding_service"},
            },
        )
        raise ValueError("Empty embedding response from Gemini")

    except Exception as e:
        logger.error(
            f"Error generating batch embeddings: {e}",
            extra={
                "json_fields": {
                    "operation": "embed_texts_batch",
                    "error": str(e),
                    "num_texts": len(texts),
                },
                "labels": {"component": "gemini_embedding_service", "severity": "high"},
            },
        )
        raise


async def embed_for_retrieval_document(text: str) -> List[float]:
    """Embed text optimized for document storage/retrieval."""
    return await embed_text(text, task_type="RETRIEVAL_DOCUMENT")


async def embed_for_retrieval_query(query: str) -> List[float]:
    """Embed a search query optimized for retrieval."""
    return await embed_text(query, task_type="RETRIEVAL_QUERY")


async def embed_for_semantic_similarity(text: str) -> List[float]:
    """Embed text optimized for semantic similarity comparisons."""
    return await embed_text(text, task_type="SEMANTIC_SIMILARITY")

