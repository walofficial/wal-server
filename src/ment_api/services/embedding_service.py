import logging
from typing import List

import numpy as np
from google.genai import types

from ment_api.services.external_clients.gemini_client import gemini_client

logger = logging.getLogger(__name__)


class EmbeddingService:
    """Service for generating title embeddings using Gemini text-embedding-001 model"""

    EMBEDDING_DIMENSIONS = 3072

    async def generate_embeddings(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings for multiple texts asynchronously"""
        if not texts:
            return []

        try:
            response = await gemini_client.aio.models.embed_content(
                model="gemini-embedding-001",
                contents=texts,
                config=types.EmbedContentConfig(
                    output_dimensionality=self.EMBEDDING_DIMENSIONS,
                    task_type="SEMANTIC_SIMILARITY",
                ),
            )

            logger.info(
                "Generated embeddings for texts",
                extra={
                    "json_fields": {
                        "texts_count": len(texts),
                        "operation": "generate_embeddings",
                    },
                    "labels": {"component": "embedding_service"},
                },
            )

            return [embedding.values for embedding in response.embeddings]

        except Exception as e:
            logger.error(
                "Failed to generate embeddings",
                extra={
                    "json_fields": {
                        "error": str(e),
                        "texts_count": len(texts),
                        "operation": "generate_embeddings",
                    },
                    "labels": {"component": "embedding_service", "severity": "high"},
                },
                exc_info=True,
            )
            # Return zero vectors as fallback
            return [[0.0] * self.EMBEDDING_DIMENSIONS for _ in texts]


embedding_service = EmbeddingService()
