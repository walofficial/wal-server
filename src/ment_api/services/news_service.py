import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import List, Optional

import httpx
from bson import ObjectId
from google.genai.types import GenerateContentConfig, ThinkingConfig
from pydantic import TypeAdapter
from pymongo.errors import BulkWriteError
from tenacity import (
    before_sleep_log,
    retry,
    stop_after_attempt,
    wait_random_exponential,
)

from langfuse import observe
from ment_api.configurations.config import settings
from ment_api.events.news_created_event import NewsCreatedEvent
from ment_api.models.location_feed_post import NewsFeedPost
from ment_api.models.news_response import NewsResponse
from ment_api.models.verification_state import VerificationState
from ment_api.persistence import mongo
from ment_api.persistence.models.external_article import (
    ExternalArticle,
    NewsCategory,
    NewsSource,
)
from ment_api.persistence.mongo import create_translation_projection
from ment_api.services.external_clients.cloud_flare_client import upload_image
from ment_api.services.external_clients.gemini_client import gemini_client
from ment_api.services.external_clients.langfuse_client import (
    langfuse,
)
from ment_api.services.external_clients.models.scrape_news_models import NewsItem
from ment_api.services.external_clients.scrape_news_1tv_client import (
    get_scrape_1tv_news_client,
)
from ment_api.services.external_clients.scrape_news_civil_client import (
    get_scrape_civil_news_client,
)
from ment_api.services.external_clients.scrape_news_imedi_client import (
    get_scrape_imedi_news_client,
)
from ment_api.services.external_clients.scrape_news_interpress_client import (
    get_scrape_interpress_news_client,
)
from ment_api.services.external_clients.scrape_news_netgazeti_client import (
    get_scrape_netgazeti_news_client,
)
from ment_api.services.external_clients.scrape_news_publika_client import (
    get_scrape_publika_news_client,
)
from ment_api.services.pub_sub_service import publish_message

logger = logging.getLogger(__name__)

Website = str
ScrapedMarkdown = str


class NewsSourceType(StrEnum):
    GOVERNMENT = "Government"
    OPPOSITION = "Opposition"
    NEUTRAL = "Neutral"


# Source classification mapping
SOURCE_CLASSIFICATIONS = {
    "Imedi": NewsSourceType.GOVERNMENT,
    "Publika": NewsSourceType.OPPOSITION,
    "TV1": NewsSourceType.NEUTRAL,
    "InterPressNews": NewsSourceType.NEUTRAL,
    "Netgazeti": NewsSourceType.NEUTRAL,
    "Civil": NewsSourceType.OPPOSITION,
}


def get_source_classifications():
    government_sources = [
        source
        for source, type in SOURCE_CLASSIFICATIONS.items()
        if type == NewsSourceType.GOVERNMENT
    ]
    opposition_sources = [
        source
        for source, type in SOURCE_CLASSIFICATIONS.items()
        if type == NewsSourceType.OPPOSITION
    ]
    neutral_sources = [
        source
        for source, type in SOURCE_CLASSIFICATIONS.items()
        if type == NewsSourceType.NEUTRAL
    ]

    return {
        "government_sources": government_sources,
        "opposition_sources": opposition_sources,
        "neutral_sources": neutral_sources,
    }


async def generate_news(assignee_user_id: str, feed_id: ObjectId):
    inserted_ids = await generate_news_from_site_apis(assignee_user_id, feed_id)
    if len(inserted_ids) == 0:
        return

    # Publish check fact messages separately for each verification
    # NOTE: This is done because of the fact that the fact check service is not able to handle a large number of verifications at and acknoledgement in message which contains many verifications to process.
    await asyncio.gather(
        *[publish_check_fact([verification_id]) for verification_id in inserted_ids]
    )


async def generate_news_from_site_apis(
    assignee_user_id: str, feed_id: ObjectId
) -> List[ObjectId]:
    """
    This function generates news from site APIs.
    It scrapes news from the 1TV, Imedi, and Publika websites and saves them to the database.
    """
    async with (
        get_scrape_1tv_news_client() as tv1_client,
        get_scrape_imedi_news_client() as imedi_client,
        get_scrape_publika_news_client() as publika_client,
        get_scrape_netgazeti_news_client() as netgazeti_client,
        get_scrape_interpress_news_client() as interpress_client,
        get_scrape_civil_news_client() as civil_client,
    ):
        # Use asyncio to concurrently fetch news from all three sources
        try:
            (
                tv1_news,
                imedi_news,
                publika_news,
                interpress_news,
                netgazeti_news,
                civil_news,
            ) = await asyncio.gather(
                tv1_client.scrape_news(),
                imedi_client.scrape_news(),
                publika_client.scrape_news(),
                interpress_client.scrape_news(),
                netgazeti_client.scrape_news(),
                civil_client.scrape_news(),
            )

            combined_news = (
                tv1_news.news_items
                + imedi_news.news_items
                + publika_news.news_items
                + interpress_news.news_items
                + netgazeti_news.news_items
                + civil_news.news_items
            )

            logger.info("combined news size: %s", len(combined_news))

            # Only insert if there are news items to insert
            if combined_news:
                try:
                    await mongo.external_articles.insert_many(
                        [
                            ExternalArticle(
                                external_id=news.external_id,
                                title=news.title,
                                content=news.content,
                                details_url=news.details_url,
                                small_image_url=news.small_image_url,
                                medium_image_url=news.medium_image_url,
                                big_image_url=news.big_image_url,
                                created_at=news.created_at,
                                category=NewsCategory[news.category.name],
                                source=NewsSource[news.source.name],
                            )
                            for news in combined_news
                        ],
                        ordered=False,
                    )
                except BulkWriteError as error:
                    # Silently handle duplicates, only report inserted count
                    inserted_count = error.details.get("nInserted", 0)
                    logger.info(
                        f"Inserted {inserted_count} documents (duplicates ignored)"
                    )
            else:
                logger.info("No news items to insert into external_articles")

            # Get titles of already generated news to avoid duplicates
            already_generated_news_titles = await get_todays_news_titles()
            logger.info(
                f"Already generated news titles: {already_generated_news_titles}"
            )

            # Process the scraped content into structured news items
            news_response = await structurize_scraped_news(
                combined_news, already_generated_news_titles
            )

            # Save the news items and return their IDs
            return await save_news(news_response, assignee_user_id, feed_id)

        except Exception as e:
            logger.error(f"Error generating news from site APIs: {e}", exc_info=True)
            return []


# Maximum number of news items to generate per execution (2-8 range)
max_news_items_count = 1

structurize_system_prompt = """
You are an expert news aggregator specializing in Georgian politics with advanced analytical capabilities.

**Primary Role**: Analyze articles from multiple news websites, identify distinct events related to Georgian politics, and generate high-quality structured news items by synthesizing information from ALL sources covering each event.

**Core Competencies**:
1. **Event Recognition**: Distinguish between truly separate events vs. different coverage of the same event
2. **Article Synthesis**: Combine ALL information from multiple sources covering the same event into comprehensive, aggregated news items
3. **Source Integration**: Synthesize perspectives from multiple sources into coherent narratives that preserve all important information
4. **Perspective Analysis**: Identify and appropriately handle conflicting viewpoints or emphasis across sources
5. **Quality Assessment**: Prioritize newsworthy events and maintain journalistic standards
6. **Structured Output**: Generate consistent, well-formatted bilingual content

**Quality Standards**:
- Factual accuracy and neutrality are paramount
- Each news item must represent a genuinely distinct event
- MANDATORY: Multiple sources covering the same event must be synthesized into ONE comprehensive news item
- All important information from every source must be preserved in the synthesis
- Multiple source perspectives should be woven together naturally
- Content must be accessible to average readers while maintaining depth
- Strict adherence to formatting requirements ensures consistency
"""

structurize_news_prompt = """
<task_overview>
Analyze the provided articles and generate up to {max_news_items_count} distinct, high-quality news items focusing on Georgian politics.
</task_overview>

<analysis_process>
1. **Event Identification**: Read all articles and identify core events/topics with key actors, timeframe, location, and policy implications
2. **Event Clustering**: Group articles covering the same event (even with different angles/headlines) - ensure each cluster represents a truly distinct event
3. **Article Synthesis**: For each event cluster, combine information from ALL sources covering that event. Merge facts, quotes, details, and context from multiple articles into a comprehensive understanding of the event
4. **Key Points Extraction**: Extract 3-5 most important points from the synthesized information, ensuring no critical details from any source are lost
5. **Source Classification**: Classify sources as government, opposition, or neutral based on the mapping provided
6. **Perspective Synthesis**: Extract and summarize perspectives from each source type separately while maintaining the complete narrative
</analysis_process>

<source_classification>
Government sources: {government_sources}
Opposition sources: {opposition_sources}
Neutral sources: {neutral_sources}
</source_classification>

<requirements>
- Maximum {max_news_items_count} news items (or fewer if insufficient distinct events)
- Both Georgian and English versions required for each item
- 3-5 bullet points highlighting key information synthesized from ALL articles covering the same event
- Separate sections for government, opposition, and neutral perspectives (only include sections when sources of that type are present)
- Raw markdown output (NOT in code blocks)
- No overlap with events in <excluded_titles>
- Use exact details_url values from input for source references
- MANDATORY: Group articles discussing the same event into single news items - never create separate news items for the same event
- Each news item must represent a complete synthesis of all available sources covering that event
</requirements>

<synthesis_instructions>
**Critical Synthesis Process:**
- When multiple articles cover the same event, you MUST create ONE aggregated news item, not separate items
- Combine ALL factual information, quotes, details, and context from every source covering the event
- Ensure no important information from any source is lost in the synthesis
- The main bullet points should reflect the most comprehensive understanding possible by merging all sources
- Handle conflicting information by noting different perspectives in the appropriate government/opposition/neutral sections
- Create a coherent narrative that incorporates the full scope of coverage from all sources
- Each synthesized news item should be more informative than any individual source article
</synthesis_instructions>

<content_template>
Each news item must follow this structure:

**Title**: Brief, clear title in Georgian
**Summary**: 1-2 sentence summary in Georgian
**Government Summary**: ONLY the bullet points from the '🏛️ მთავრობის პოზიცია' section (NO header/title, just bullet points)
**Opposition Summary**: ONLY the bullet points from the '🗣️ ოპოზიციის მოსაზრება' section (NO header/title, just bullet points)
**Neutral Summary**: ONLY the bullet points from the '⚖️ ნეიტრალური მოსაზრება' section (NO header/title, just bullet points)
**Content (Georgian)**: Raw markdown with exact structure below
**Content (English)**: Raw markdown with exact structure below  
**Sources**: All articles used with exact title and details_url

**CRITICAL MARKDOWN FORMATTING:**
- Include proper line endings (\\n) between all sections
- Each bullet point must be on its own line
- Headers (##) must have blank lines before and after them
- Ensure proper spacing for correct markdown rendering

**PERSPECTIVE SUMMARY EXTRACTION:**
- For each perspective (government/opposition/neutral), extract ONLY the bullet points from their respective sections
- Government Summary should contain ONLY the bullet points from "🏛️ მთავრობის პოზიცია" section (NO header/title/emojis)
- Opposition Summary should contain ONLY the bullet points from "🗣️ ოპოზიციის მოსაზრება" section (NO header/title/emojis)
- Neutral Summary should contain ONLY the bullet points from "⚖️ ნეიტრალური მოსაზრება" section (NO header/title/emojis)
- **CRITICAL**: Include proper line breaks (\\n) after each bullet point in summary fields
- If no sources of that type are present, leave the summary empty

**MAIN CONTENT STRUCTURE:**
- Use 3-5 main bullet points (not exactly 5)
- Generate only as many bullet points as needed to cover the key information
- Minimum 3 bullet points, maximum 5 bullet points
- Quality over quantity - ensure each bullet point adds significant value

### Georgian Content Structure:
```
- [Synthesized from ALL sources - concise description with key details, dates, people, context - 15-25 words]
- [Synthesized from ALL sources - concise description with key details, dates, people, context - 15-25 words]
- [Synthesized from ALL sources - concise description with key details, dates, people, context - 15-25 words]
- [OPTIONAL: Additional bullet points if needed, up to 5 total - 15-25 words each]
- [OPTIONAL: Additional bullet points if needed, up to 5 total - 15-25 words each]

## 🏛️ მთავრობის პოზიცია:

- [Government perspective/statement extracted from government sources - 10-20 words]
- [OPTIONAL: Additional government perspectives, up to 4 total - 10-20 words each]
- [OPTIONAL: Additional government perspectives, up to 4 total - 10-20 words each]
- [OPTIONAL: Additional government perspectives, up to 4 total - 10-20 words each]

## 🗣️ ოპოზიციის მოსაზრება:

- [Opposition perspective/statement extracted from opposition sources - 10-20 words]
- [OPTIONAL: Additional opposition perspectives, up to 4 total - 10-20 words each]
- [OPTIONAL: Additional opposition perspectives, up to 4 total - 10-20 words each]
- [OPTIONAL: Additional opposition perspectives, up to 4 total - 10-20 words each]

## ⚖️ ნეიტრალური მოსაზრება:

- [Neutral perspective/statement extracted from neutral sources - 10-20 words]
- [OPTIONAL: Additional neutral perspectives, up to 4 total - 10-20 words each]
- [OPTIONAL: Additional neutral perspectives, up to 4 total - 10-20 words each]
- [OPTIONAL: Additional neutral perspectives, up to 4 total - 10-20 words each]
```

### English Content Structure:
```
- [Synthesized from ALL sources - concise description with key details, dates, people, context - 15-25 words]
- [Synthesized from ALL sources - concise description with key details, dates, people, context - 15-25 words]
- [Synthesized from ALL sources - concise description with key details, dates, people, context - 15-25 words]
- [OPTIONAL: Additional bullet points if needed, up to 5 total - 15-25 words each]
- [OPTIONAL: Additional bullet points if needed, up to 5 total - 15-25 words each]

## 🏛️ Government Position:

- [Government perspective/statement extracted from government sources - 10-20 words]
- [OPTIONAL: Additional government perspectives, up to 4 total - 10-20 words each]
- [OPTIONAL: Additional government perspectives, up to 4 total - 10-20 words each]
- [OPTIONAL: Additional government perspectives, up to 4 total - 10-20 words each]

## 🗣️ Opposition View:

- [Opposition perspective/statement extracted from opposition sources - 10-20 words]
- [OPTIONAL: Additional opposition perspectives, up to 4 total - 10-20 words each]
- [OPTIONAL: Additional opposition perspectives, up to 4 total - 10-20 words each]
- [OPTIONAL: Additional opposition perspectives, up to 4 total - 10-20 words each]

## ⚖️ Neutral Perspective:

- [Neutral perspective/statement extracted from neutral sources - 10-20 words]
- [OPTIONAL: Additional neutral perspectives, up to 4 total - 10-20 words each]
- [OPTIONAL: Additional neutral perspectives, up to 4 total - 10-20 words each]
- [OPTIONAL: Additional neutral perspectives, up to 4 total - 10-20 words each]
```

### Perspective Summary Fields:
For the separate summary fields, extract ONLY the bullet points from each perspective section:

**Government Summary**: Copy ONLY the bullet points from "🏛️ მთავრობის პოზიცია" section (NO header, NO emojis, just the bullet points)
**Opposition Summary**: Copy ONLY the bullet points from "🗣️ ოპოზიციის მოსაზრება" section (NO header, NO emojis, just the bullet points)  
**Neutral Summary**: Copy ONLY the bullet points from "⚖️ ნეიტრალური მოსაზრება" section (NO header, NO emojis, just the bullet points)
</content_template>

<markdown_output_requirements>
**CRITICAL FOR PROPER RENDERING:**
When generating the actual content, you MUST include proper line endings for markdown rendering:
- After each bullet point, include a line break (\\n)
- Before each header (##), include a blank line (\\n\\n)
- After each header (##), include a blank line (\\n\\n)
- Between different sections, include proper spacing
- Each bullet point must be on its own line
- **CRITICAL**: Perspective summary fields must also include proper line breaks (\\n) after each bullet point
- The output must render correctly as markdown in a browser
</markdown_output_requirements>

<formatting_rules>
- Use bullet points (-) for ALL lists, never numbered lists
- Follow exact emoji and heading sequence as shown in template
- Georgian and English content must be equivalent
- Each main bullet point should be 15-25 words and contain concrete information synthesized from ALL available sources
- Perspective bullet points should be 10-20 words focusing on the key stance/statement
- Use minimum 3, maximum 5 bullet points for the main content sections
- Use up to 4 bullet points maximum for each perspective section (government/opposition/neutral)
- Main bullet points must represent comprehensive synthesis, not individual source information
- **CRITICAL**: Extract perspective sections as separate summary fields containing the exact markdown content
- Government/Opposition/Neutral summary fields should contain the respective section's bullet points with formatting
- If no sources of a particular type (government/opposition/neutral) are present, leave that summary empty
</formatting_rules>

<quality_validation>
Before finalizing, verify each news item:
- [ ] Represents a distinct event not covered by others
- [ ] Synthesizes ALL available sources covering the same event into one comprehensive news item
- [ ] No important information from any source is missing from the synthesis
- [ ] 3-5 main bullet points with key information that represents the complete picture from all sources
- [ ] Only includes perspective sections when sources of that type are present
- [ ] Georgian and English content are equivalent
- [ ] Uses bullet points (-) for all lists
- [ ] **Bold formatting** used for: people, organizations, dates, numbers, locations, key terms
- [ ] Word counts within specified limits (15-25 words for main points, 10-20 words for perspectives)
- [ ] Main bullet points: 3-5 total, perspective sections: up to 4 bullet points each maximum
- [ ] Source references include ALL sources used, with exact details_url from input
- [ ] No overlap with <excluded_titles>
- [ ] Content is raw markdown (no code blocks)
- [ ] Each news item is more comprehensive than any individual source article
- [ ] **CRITICAL**: Government/Opposition/Neutral summary fields contain ONLY the bullet points from their respective sections
- [ ] **CRITICAL**: Perspective summary fields do NOT include section headers/titles/emojis, only bullet points
- [ ] Empty perspective summary fields when no sources of that type are present
- [ ] CRITICAL: Proper line endings (\\n) included between all sections and elements for correct markdown rendering
- [ ] Each bullet point is on its own line with proper line breaks
- [ ] Headers (##) have blank lines before and after them
</quality_validation>

<focus_priorities>
1. **Georgian Domestic Politics** (highest priority)
2. **International Politics affecting Georgia** 
3. **Significant social, economic, cultural events in Georgia**

Generate {max_news_items_count} items, prioritizing quality and distinctiveness over quantity.
</focus_priorities>

<excluded_titles>
{excluded_titles}
</excluded_titles>

<scraped_content>
{formatted_scraped_results}
</scraped_content>"""


@observe(as_type="generation")
@retry(
    wait=wait_random_exponential(multiplier=1, max=3),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
    stop=stop_after_attempt(4),
)
async def structurize_scraped_news(
    news_items: List[NewsItem], excluded_titles: List[str]
) -> Optional[NewsResponse]:
    news_items_obj = TypeAdapter(List[NewsItem]).dump_python(
        news_items,
        include={
            "__all__": {
                "title",
                "content",
                "details_url",
                "big_image_url",
                "source",
                "created_at",
            }
        },
    )
    news_items_json = json.dumps(news_items_obj, ensure_ascii=False, default=str)
    classifications = get_source_classifications()
    news_prompt = structurize_news_prompt.format(
        excluded_titles=excluded_titles,
        formatted_scraped_results=news_items_json,
        max_news_items_count=max_news_items_count,
        **classifications,
    )
    logger.info(f"News prompt length: {len(news_prompt)}")

    langfuse.update_current_generation(
        input=[news_prompt],
        model="gemini-2.5-flash",
        metadata={
            "news_items_count": len(news_items),
        },
    )

    response = await gemini_client.aio.models.generate_content(
        model="gemini-2.5-flash",
        config=GenerateContentConfig(
            system_instruction=structurize_system_prompt,
            response_mime_type="application/json",
            response_schema=NewsResponse,
            max_output_tokens=64000,
            temperature=0.1,
            thinking_config=ThinkingConfig(
                thinking_budget=5000,
            ),
        ),
        contents=[news_prompt],
    )

    langfuse.update_current_generation(
        usage_details={
            "input": response.usage_metadata.prompt_token_count,
            "output": response.usage_metadata.candidates_token_count,
            "cache_read_input_tokens": response.usage_metadata.cached_content_token_count,
        },
    )

    # Add comprehensive validation
    if not response:
        logger.error("Gemini returned None response")
        return None

    if not hasattr(response, "candidates") or not response.candidates:
        logger.error("Gemini response has no candidates")
        return None

    if not response.text:
        logger.error(
            f"Gemini response.text is None or empty. Response: {response.text[:100]}"
        )
        return None

    if not response.parsed:
        logger.error(
            f"Gemini response.parsed is None. Response text: {response.text[:100]}"
        )
        return None

    if response.usage_metadata:
        logger.info(
            f"Gemini - Output token count: {response.usage_metadata.candidates_token_count}"
        )

    logger.info(f"Generated news response: {response.text[:100]}")
    news_response: NewsResponse = response.parsed
    return news_response


async def get_todays_news_titles() -> List[str]:
    today = datetime.now(timezone.utc).date()
    start_of_day = datetime(
        today.year, today.month, today.day, tzinfo=timezone.utc
    ) - timedelta(hours=48)
    end_of_day = start_of_day + timedelta(days=1)

    # Create translation projection for title field only
    translation_projections = create_translation_projection(["title"], "ka")

    pipeline = [
        {
            "$match": {
                "news_date": {"$gte": start_of_day, "$lt": end_of_day},
                "is_generated_news": True,
            }
        },
        {
            "$project": {
                # Only project the title field with translation fallback
                **translation_projections
            }
        },
    ]

    news_items = await mongo.verifications.aggregate(pipeline)
    return [
        news_item.get("title") for news_item in news_items if news_item.get("title")
    ]


async def save_news(
    response: NewsResponse, assignee_user_id: str, feed_id: ObjectId
) -> List[ObjectId]:
    logger.info(f"Saving {len(response.news)} news items")

    async def fetch_and_upload_image(
        client: httpx.AsyncClient, url: str, fallback_name: str
    ):
        try:
            r = await client.get(url, timeout=20.0, follow_redirects=True)
            r.raise_for_status()
            # Determine content type and extension
            content_type = r.headers.get("content-type", "image/jpeg").split(";")[0]
            # Simple extension inference
            ext = ".jpg"
            if "png" in content_type:
                ext = ".png"
            elif "webp" in content_type:
                ext = ".webp"
            elif "jpeg" in content_type or "jpg" in content_type:
                ext = ".jpg"
            elif "gif" in content_type:
                ext = ".gif"
            destination_file_name = f"{fallback_name}{ext}"
            image_with_dims = await upload_image(
                r.content, destination_file_name, content_type
            )
            return image_with_dims.model_dump()
        except Exception as e:
            logger.warning(f"Failed to fetch/upload image from {url}: {e}")
            return None

    # Process images for all news concurrently
    async with httpx.AsyncClient() as client:
        upload_tasks = []
        for item in response.news:
            primary_url = item.image_url or (
                item.found_image_urls[0] if item.found_image_urls else None
            )
            print(primary_url)
            if primary_url:
                unique_name = f"{item.id}_{uuid.uuid4().hex}"
                upload_tasks.append(
                    fetch_and_upload_image(client, primary_url, unique_name)
                )
            else:
                upload_tasks.append(asyncio.sleep(0, result=None))
        uploaded_images = await asyncio.gather(*upload_tasks)
    operations = []
    for idx, news_item in enumerate(response.news):
        image_gallery = []
        if uploaded_images[idx] is not None:
            image_gallery = [uploaded_images[idx]]

        operations.append(
            NewsFeedPost(
                news_id=news_item.id,
                title=news_item.title,
                text_content=news_item.content,  # This will be the Georgian content for FeedPost compatibility
                text_summary=news_item.summary,
                text_content_in_english=news_item.content_in_english,
                sources=[s.model_dump() for s in news_item.sources],
                news_date=datetime.now(timezone.utc),
                assignee_user_id=assignee_user_id,
                feed_id=feed_id,
                is_public=True,
                last_modified_date=datetime.now(timezone.utc),
                state=VerificationState.READY_FOR_USE,
                is_generated_news=True,
                government_summary=news_item.government_summary,  # This will be the Georgian content for FeedPost compatibility
                opposition_summary=news_item.opposition_summary,  # This will be the Georgian content for FeedPost compatibility
                neutral_summary=news_item.neutral_summary,  # This will be the Georgian content for FeedPost compatibility
                image_gallery_with_dims=image_gallery,
            )
        )
    if len(operations) == 0:
        logger.info("No news items to insert")
        return []
    # Convert Pydantic models to plain dicts for MongoDB
    documents = []
    for operation in operations:
        doc = operation.model_dump(by_alias=True, exclude_none=True)
        # Ensure ObjectId types are preserved for MongoDB where needed
        if "feed_id" in doc and isinstance(doc["feed_id"], str):
            try:
                doc["feed_id"] = ObjectId(doc["feed_id"])  # type: ignore
            except Exception:
                pass

        documents.append(doc)

    insert_result = await mongo.verifications.insert_many(documents)
    if len(insert_result.inserted_ids) != len(response.news):
        logger.error("Failed to insert all news items into verifications")
    else:
        logger.info(
            f"Successfully inserted {len(insert_result.inserted_ids)} news items into verifications"
        )
    return insert_result.inserted_ids


async def publish_check_fact(verifications: List[ObjectId]) -> None:
    try:
        event = NewsCreatedEvent(verifications=verifications)
        data = event.model_dump_json().encode()

        await publish_message(
            settings.gcp_project_id,
            settings.pub_sub_check_fact_topic_id,
            data,
            retry_timeout=60.0,
        )

        logger.info(
            f"Successfully published check fact message for {len(verifications)} verifications"
        )
    except Exception:
        logger.error("Error publishing check fact messages", exc_info=True)
