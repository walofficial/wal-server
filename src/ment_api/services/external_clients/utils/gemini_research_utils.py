"""Utility functions for Gemini research operations."""

import logging
from datetime import datetime
from typing import Any, Dict, List

from google.genai.types import GenerateContentConfig, GoogleSearchRetrieval, Tool

from ment_api.services.external_clients.schemas.gemini_schemas import (
    FACT_CHECK_RESPONSE_SCHEMA,
    RESEARCH_QUERY_SCHEMA,
    SYSTEM_PROMPT_TEMPLATE,
)
from ment_api.services.video_processing_schemas import TRANSLATION_STYLE_RULES

logger = logging.getLogger(__name__)


def get_query_generation_config(system_prompt: str) -> GenerateContentConfig:
    """
    Create the configuration for query generation with Gemini.

    Args:
        system_prompt: The system prompt to use

    Returns:
        Configuration for query generation
    """
    return GenerateContentConfig(
        system_instruction=system_prompt,
        response_mime_type="application/json",
        response_schema=RESEARCH_QUERY_SCHEMA,
        temperature=0.7,
        top_p=0.95,
        top_k=40,
        max_output_tokens=8192,
    )


def get_research_config(system_prompt: str) -> GenerateContentConfig:
    """
    Create the configuration for research operations with Gemini.

    Args:
        system_prompt: The system prompt to use

    Returns:
        Configuration for research operations with Google Search tools
    """
    return GenerateContentConfig(
        system_instruction=system_prompt,
        temperature=0.2,
        top_p=0.95,
        top_k=40,
        max_output_tokens=8192,
        tools=[Tool(google_search=GoogleSearchRetrieval)],
    )


def get_synthesis_config(system_prompt: str) -> GenerateContentConfig:
    """
    Create the configuration for fact checking synthesis with Gemini.

    Args:
        system_prompt: The system prompt to use

    Returns:
        Configuration for synthesis operations
    """
    return GenerateContentConfig(
        system_instruction=system_prompt,
        response_mime_type="application/json",
        response_schema=FACT_CHECK_RESPONSE_SCHEMA,
        temperature=0.1,
        max_output_tokens=60000,
    )


def create_system_prompt() -> str:
    """
    Create a system prompt with the current date.

    Returns:
        Formatted system prompt
    """
    today = datetime.now().strftime("%Y-%m-%d")
    return SYSTEM_PROMPT_TEMPLATE.format(today=today)


def format_research_results(research_results: List[Dict[str, Any]]) -> str:
    """
    Format research results for inclusion in the synthesis prompt.

    Args:
        research_results: List of research result dictionaries

    Returns:
        Formatted research results as a string
    """
    formatted_results = []

    for i, result in enumerate(research_results):
        formatted_results.append(f"Query {i + 1}: {result.get('query')}")
        formatted_results.append(f"Research Goal: {result.get('researchGoal')}")
        formatted_results.append(f"Findings:\n{result.get('findings')}")
        formatted_results.append("---")

    return "\n\n".join(formatted_results)


def create_query_generation_prompt(statement: str) -> str:
    """
    Create a prompt for generating research queries.

    Args:
        statement: The statement to research

    Returns:
        Formatted prompt for query generation
    """
    return f"""Given the following statement to fact-check:
<statement>{statement}</statement>

If there are any images provided with this request, analyze them carefully for:
- Visual content and objects in the images
- Text appearing in the images
- People, places, or events depicted
- Any visual characteristics that might help verify or refute the statement

Based on this statement and any provided images, generate a list of search queries to further research the topic. 
Make sure each query is unique and not similar to each other.

You MUST respond in JSON matching this schema:
```json
{{
  "type": "array",
  "items": {{
    "type": "object",
    "properties": {{
      "query": {{
        "type": "string",
        "description": "The search query."
      }},
      "researchGoal": {{
        "type": "string",
        "description": "First talk about the goal of the research that this query is meant to accomplish, then go deeper into how to advance the research once the results are found, mention additional research directions. Be as specific as possible, especially for additional research directions."
      }}
    }},
    "required": ["query", "researchGoal"]
  }},
  "description": "List of search queries."
}}
```

Expected output:
```json
[{{"query": "This is a sample query.", "researchGoal": "This is the reason for the query."}}]

IMPORTANT: If the statement is invalid, inappropriate, or doesn't contain factual claims that can be researched, OR if the provided images are inappropriate, unrelated to the statement, or cannot be analyzed, return an empty array [].

```"""


def create_research_prompt(query: str, research_goal: str, statement: str) -> str:
    """
    Create a prompt for researching a specific query.

    Args:
        query: The search query
        research_goal: The goal of the research
        statement: The statement being fact-checked

    Returns:
        Formatted research prompt
    """
    return f"""Research the following topic thoroughly:
                
Query: {query}
Research Goal: {research_goal}

This is related to fact-checking this statement: "{statement}"

If images were provided with this request:
- Use information from the images to inform your search strategy
- Look for matching or similar images
- Verify any claims about specific visual content
- Research any text, locations, or individuals visible in the images

Find the most authoritative and relevant information. Consider multiple perspectives.
Provide specific data points, sources, and evidence.

You must include the source title, URL, key quote, and whether it supports the statement.
"""


def create_synthesis_prompt(
    statement: str, research_results: List[Dict[str, Any]]
) -> str:
    """
    Create a prompt for synthesizing research findings.

    Args:
        statement: The statement being fact-checked
        research_results: List of research results

    Returns:
        Formatted synthesis prompt
    """
    formatted_results = format_research_results(research_results)

    return f"""Based on the following research findings about this statement:
            
Statement to fact-check: "{statement}"

Research Findings:
{formatted_results}

Synthesize these findings into a comprehensive fact check. Consider all evidence carefully.

If images were provided with this request:
- Analyze the images carefully for visual evidence related to the statement
- Compare the image content with the research findings
- Consider whether the images support or refute the statement
- Include specific observations from the images in your analysis

You must include:
1. A factuality score from 0-1
2. A boolean result (true or false)
3. A detailed reason explaining your verdict in Georgian
4. References with URLs, source titles, key quotes, and whether they support the statement

Your thinking process should be in English, but the final JSON response must be entirely in Georgian, including the 'reason' field and any other text fields.

For the 'reason' field, use simple and everyday Georgian language that makes it easy to understand at a glance and engaging to encourage readers to read it.

Format your response as JSON according to the required schema.

TRANSLATION_STYLE_RULES: {TRANSLATION_STYLE_RULES}
"""
