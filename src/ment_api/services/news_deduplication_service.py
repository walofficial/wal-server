import logging
from datetime import datetime, timedelta, timezone
from typing import List

import numpy as np
from numpy.typing import NDArray
from sklearn.metrics.pairwise import cosine_similarity

from ment_api.persistence import mongo
from ment_api.services.embedding_service import embedding_service
from ment_api.services.external_clients.models.scrape_news_models import NewsItem

logger = logging.getLogger(__name__)

COSINE_SIMILARITY_THRESHOLD = 0.85
DEFAULT_EMBEDDING = [0.0] * 3072


async def deduplicate_news_items(combined_news: List[NewsItem]) -> List[NewsItem]:
    """
    Filter out news items that are too similar to existing verifications
    using vector similarity search with cosine similarity threshold of 0.8
    """
    if not combined_news:
        return combined_news

    logger.info(
        "Starting news deduplication",
        extra={
            "json_fields": {
                "input_count": len(combined_news),
                "operation": "deduplicate_news_items",
            },
            "labels": {"component": "news_deduplication"},
        },
    )

    try:
        # Generate embeddings for all news titles
        news_titles = [news.title for news in combined_news]
        raw_combined_news_embeddings = await embedding_service.generate_embeddings(
            news_titles
        )

        if not raw_combined_news_embeddings or len(raw_combined_news_embeddings) == 0:
            logger.warning(
                "No embeddings generated for news titles",
                extra={
                    "json_fields": {
                        "titles_count": len(news_titles),
                        "operation": "deduplicate_news_items",
                    },
                    "labels": {"component": "news_deduplication"},
                },
            )
            return combined_news

        # Get existing verifications from last 24 hours
        today = datetime.now(timezone.utc).date()
        start_of_day = datetime(
            today.year, today.month, today.day, tzinfo=timezone.utc
        ) - timedelta(hours=24)

        pipeline = [
            {
                "$match": {
                    "news_date": {"$gte": start_of_day},
                    "title_embedding": {"$exists": True, "$ne": None},
                }
            },
            {"$project": {"title": 1, "title_embedding": 1, "news_date": 1}},
        ]

        results = await mongo.verifications.aggregate(pipeline)

        if not results:
            logger.info(
                "No existing verifications found for comparison",
                extra={
                    "json_fields": {
                        "operation": "deduplicate_news_items",
                        "lookback_hours": 24,
                    },
                    "labels": {"component": "news_deduplication"},
                },
            )
            return combined_news

        verifications_embeddings: List[NDArray[float]] = [
            np.array(result.get("title_embedding")) for result in results
        ]

        logger.info(
            "Comparing news items against existing verifications",
            extra={
                "json_fields": {
                    "news_count": len(raw_combined_news_embeddings),
                    "verifications_count": len(verifications_embeddings),
                    "operation": "deduplicate_news_items",
                },
                "labels": {"component": "news_deduplication"},
            },
        )

        # Create matrices for similarity comparison
        combined_news_embeddings = [
            np.array(embedding) for embedding in raw_combined_news_embeddings
        ]
        combined_news_embeddings_matrix = np.array(combined_news_embeddings)
        verifications_embeddings_matrix = np.array(verifications_embeddings)

        # Calculate similarity matrix: news_items x verifications
        similarity_matrix = cosine_similarity(
            combined_news_embeddings_matrix, verifications_embeddings_matrix
        )

        # Filter out news items that are too similar to existing verifications
        filtered_news = []
        duplicate_count = 0
        duplicate_details = []

        for i in range(len(combined_news_embeddings)):
            is_duplicate = False
            for j in range(len(verifications_embeddings)):
                similarity = similarity_matrix[i, j]
                if similarity >= COSINE_SIMILARITY_THRESHOLD:
                    is_duplicate = True
                    duplicate_details.append(
                        {
                            "news_title": combined_news[i].title,
                            "verification_title": (
                                results[j].get("title", {}).get("ka")
                                if isinstance(results[j].get("title"), dict)
                                else results[j].get("title")
                            )
                            or "N/A",
                            "similarity": float(similarity),
                        }
                    )
                    break
            if not is_duplicate:
                filtered_news.append(combined_news[i])
            else:
                duplicate_count += 1

        logger.info(
            "Completed news deduplication",
            extra={
                "json_fields": {
                    "input_count": len(combined_news),
                    "filtered_count": len(filtered_news),
                    "duplicates_removed": duplicate_count,
                    "duplicate_details": duplicate_details[
                        :15
                    ],  # Log first 5 duplicates for debugging
                    "operation": "deduplicate_news_items",
                },
                "labels": {"component": "news_deduplication"},
            },
        )

        return filtered_news

    except Exception as e:
        logger.error(
            "Error during news deduplication",
            extra={
                "json_fields": {
                    "error": str(e),
                    "input_count": len(combined_news),
                    "operation": "deduplicate_news_items",
                },
                "labels": {"component": "news_deduplication", "severity": "high"},
            },
            exc_info=True,
        )
        return combined_news
