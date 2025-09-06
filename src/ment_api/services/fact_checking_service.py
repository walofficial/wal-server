import json
import logging
from datetime import datetime
from typing import Optional

import httpx
from langfuse import observe
from langfuse.api.resources.ingestion.types import usage_details
from openai import AsyncOpenAI
from pydantic import ValidationError
from ment_api.services.external_clients.langfuse_client import (
    langfuse,
)
from google.genai.types import GenerateContentConfig, Part, ThinkingConfig
from ment_api.configurations.config import settings
from ment_api.models.fact_checking_models import (
    FactCheckingResult,
    FactCheckRequest,
    JinaFactCheckResponse,
)
from ment_api.services.external_clients.gemini_client import gemini_client
from groq import AsyncGroq

client = AsyncGroq(
    default_headers={"Groq-Model-Version": "latest"}, api_key=settings.groq_key
)

logger = logging.getLogger(__name__)


# Initialize Jina AI client once at module level following OpenAI best practices
try:
    jina_client = AsyncOpenAI(
        api_key=settings.jina_api_key,
        base_url=settings.jina_base_url,
        timeout=httpx.Timeout(
            connect=10.0,  # 10 seconds to establish connection
            read=720.0,  # 12 minutes to read response (10 min + 2 min buffer)
            write=30.0,  # 30 seconds to send request
            pool=10.0,  # 10 seconds to get connection from pool
        ),
    )

    logger.info(
        "Jina AI client initialized successfully",
        extra={
            "json_fields": {
                "base_url": settings.jina_base_url,
                "connect_timeout": 10.0,
                "read_timeout": 720.0,
                "write_timeout": 30.0,
                "pool_timeout": 10.0,
                "base_operation": "fact_check",
                "operation": "jina_client_initialized",
            },
            "labels": {"component": "jina_fact_checker", "phase": "init"},
        },
    )
except Exception as e:
    logger.error(
        "Failed to initialize Jina AI client",
        extra={
            "json_fields": {
                "error": str(e),
                "error_type": type(e).__name__,
                "base_url": settings.jina_base_url,
                "base_operation": "fact_check",
                "operation": "jina_client_init_error",
            },
            "labels": {"component": "jina_fact_checker", "severity": "critical"},
        },
        exc_info=True,
    )
    raise  # Re-raise to prevent module from loading with broken client


def get_jina_response_format():
    """
    Generate response format from JinaFactCheckResponse pydantic model
    to ensure single source of truth without $defs
    """
    logger.debug(
        "Generating Jina response format schema",
        extra={
            "json_fields": {
                "base_operation": "fact_check",
                "operation": "jina_schema_generation_start",
            },
            "labels": {"component": "jina_fact_checker", "phase": "schema"},
        },
    )

    schema = JinaFactCheckResponse.model_json_schema()

    # Remove $defs and inline the FactCheckingReference definition
    return """{
    "type": "object",
    "properties": {
      "factuality": {
        "type": "number",
        "description": "Output a numerical score between 0.0 and 1.0 representing the factuality of the news article. This score will be shown to users to indicate how factual the article is. Adhere to the following interpretation: 0.9-1.0 signifies highly factual (well-supported by multiple reliable sources); 0.7-0.9 signifies mostly factual (with minor uncertainties); 0.4-0.7 signifies partially factual (with significant uncertainties); and 0.0-0.4 signifies mostly false or unverifiable."
      },
      "reason": {
        "type": "string",
        "description": "Structured fact-check explanation formatted with specific sections. Use this exact format with proper line breaks:\n\n## სიმართლე\n- [bullet point for true claim]\n- [bullet point for true claim]\n\n[One paragraph explaining evidence why these claims are true]\n\n## ტყუილი\n- [bullet point for false claim]\n- [bullet point for false claim]\n\n[One paragraph explaining evidence why these claims are false]\n\n## გადაუმოწმებელი\n- [bullet point for unverifiable claim]\n\n[One paragraph explaining why these cannot be verified]\n\nOnly include sections that have content. Use '-' for bullet points. Keep bullet points short and digestible."
      },
      "score_justification": {
        "type": "string",
        "description": "Comprehensive English analysis providing detailed reasoning behind the factuality score. This should be even more thorough than the reason field, explaining: the methodology used for evaluation, specific evidence weighting, source credibility assessment, logical reasoning process, and complete justification for the numerical score assigned. Be extremely detailed and analytical."
      },
      "reason_summary": {
        "type": "string",
        "description": "A concise fact-check summary, formatted as raw markdown (no code blocks). This summary appears directly under articles alongside the factuality score and must be optimized for users with short attention spans. Requirements: Maximum 2-3 short sentences total (not paragraphs); Lead with the most important finding first; Use simple, direct language that an average reader can scan quickly; Structure: Present findings in order of impact using this priority: a) If false information is present, start with it; b) Then include unverifiable claims if applicable; c) End with any validated claims if present."
      },
      "references": {
        "type": "array",
        "description": "List of reference objects supporting the fact check, where each object has a url, source_title, key_quote, and is_supportive",
        "items": {
          "type": "object",
          "properties": {
            "url": {
              "type": "string",
              "description": "URL of the reference source"
            },
            "source_title": {
              "type": "string",
              "description": "Title of the reference source"
            },
            "key_quote": {
              "type": "string",
              "description": "Key quote from the source supporting the fact check"
            },
            "is_supportive": {
              "type": "boolean",
              "description": "Whether the reference supports or refutes the statement"
            }
          },
          "required": [
            "url",
            "key_quote",
            "is_supportive"
          ]
        }
      }
    },
    "required": [
      "factuality",
      "reason",
      "score_justification",
      "reason_summary"
      "references"
    ]
  }"""

    logger.debug(
        "Jina response format schema generated",
        extra={
            "json_fields": {
                "had_defs": has_defs,
                "properties_count": len(response_format["properties"]),
                "required_fields_count": len(response_format["required"]),
                "base_operation": "fact_check",
                "operation": "jina_schema_generation_complete",
            },
            "labels": {"component": "jina_fact_checker", "phase": "schema"},
        },
    )

    return response_format


# Create fact checking prompt
def create_fact_checking_prompt(details: str) -> str:
    current_date = datetime.now().strftime("%Y-%m-%d")

    logger.debug(
        "Creating fact checking prompt",
        extra={
            "json_fields": {
                "details_length": len(details),
                "current_date": current_date,
                "base_operation": "fact_check",
                "operation": "jina_prompt_creation",
            },
            "labels": {"component": "jina_fact_checker", "phase": "prompt"},
        },
    )
    prompt = f"""
    The current date is: {current_date} 

    Before you start the fact checking process, make sure to gather all the real time information you need for the persons, places, events, etc. that are mentioned in the post details. For example someone might have become a president or something today or someone made a statement maybe make sure to gather information from search instead of use training data

    <details>
{details}
</details>
Think step by step and then write your response.

RESEARCH PHASE: Research these post details thoroughly using your search capabilities
Consider multiple perspectives and contrasting viewpoints
Find the most authoritative and relevant information
Cross-reference claims across multiple reliable sources
You may research subjects after your knowledge cutoff - assume the user is correct when presented with current news
ANALYSIS PHASE: Analyze all gathered information systematically
Evaluate source credibility and reliability
Assess the strength of evidence for each claim
Identify any logical inconsistencies or gaps
Weight evidence based on source authority and verification
SCORING PHASE: Assign a precise numerical factuality score
Use the scale: 0.9-1.0 (highly factual - well-supported by multiple reliable sources)
0.7-0.9 (mostly factual - minor uncertainties)
0.4-0.7 (partially factual - significant uncertainties)
0.0-0.4 

Lead with strongest evidence: Start each paragraph with your most compelling fact or source
Include concrete details: Use specific dates, numbers, percentages, official titles, exact quotes
Natural source integration: Weave source references into the narrative flow, not as separate citations
Avoid bullet point repetition: Expand with NEW information that supports/contradicts/questions the claims
Progressive information: Each sentence should add new value, not repeat previous points
Multiple source corroboration: Show how different sources align or conflict
Logical flow: Evidence should build from strongest to supporting details
Write for average readers but include precise factual details
CRITICAL REQUIREMENTS:

If a section has NO bullet points, DO NOT include that section AT ALL
Keep bullet points concise and specific
English Analytical Justification
Provide comprehensive English analysis with detailed reasoning behind the factuality score. Include:

Methodology used for evaluation
Specific evidence weighting and assessment
Source credibility analysis
Logical reasoning process step-by-step
Complete justification for the numerical score assigned
Be extremely detailed and analytical
Concise fact-check summary, formatted as raw markdown. Optimized for users with short attention spans:

Requirements:

Maximum 2-3 short sentences total (not paragraphs)
Lead with most important finding first
Use simple, direct language for quick scanning
Skip categories with no significant findings
Use active voice and specific terms
Write for 3-second comprehension
Avoid technical jargon or complex sentences

References
Provide references with:
URLs to sources
Source titles
Key quotes in original language
Clear indication of whether each source supports or contradicts the post details

Quality standards:
You are working with a highly experienced analyst - be detailed and thorough
Accuracy is critical - mistakes erode trust
Value strong arguments over source authority alone
Consider new technologies and contrarian ideas, not just conventional wisdom
Use high levels of speculation or prediction when appropriate, but clearly flag it
Be highly organized in your response structure

You might need sometimes to search Georgian keywords in query as most of the news are in Georgian language

Try to provide more than 6 references and increase it as you find more information.
"""

    logger.debug(
        "Fact checking prompt created",
        extra={
            "json_fields": {
                "prompt_length": len(prompt),
                "details_length": len(details),
                "base_operation": "fact_check",
                "operation": "jina_prompt_created",
            },
            "labels": {"component": "jina_fact_checker", "phase": "prompt"},
        },
    )

    return prompt


@observe(as_type="generation")
async def check_fact(request: FactCheckRequest) -> Optional[FactCheckingResult]:
    """
    Check a statement for factual accuracy using Jina AI Deep Search.

    Args:
        request: The fact check request containing the statement to verify

    Returns:
        FactCheckingResult with the verification data or None if the check failed
    """
    try:
        logger.info(
            "Starting Jina DeepSearch fact check",
            extra={
                "json_fields": {
                    "verification_id": str(request.verification_id),
                    "statement_length": len(request.details),
                    "base_operation": "fact_check",
                    "operation": "jina_fact_check_start",
                },
                "labels": {"component": "jina_fact_checker", "phase": "start"},
            },
        )

        # Create prompts
        fact_checking_prompt = create_fact_checking_prompt(request.details)

        # Prepare request parameters
        budget_tokens = getattr(request, "budget_tokens") or settings.jina_token_limit

        logger.info(
            "Jina request parameters prepared",
            extra={
                "json_fields": {
                    "verification_id": str(request.verification_id),
                    "budget_tokens": budget_tokens,
                    "prompt_length": len(fact_checking_prompt),
                    "base_operation": "fact_check",
                    "operation": "jina_request_prepared",
                },
                "labels": {"component": "jina_fact_checker", "phase": "prepare"},
            },
        )

        completion = await client.chat.completions.create(
            model="groq/compound",
            messages=[
                {
                    "role": "system",
                    "content": " You are an expert fact-checker tasked with thoroughly analyzing the following post details. Follow the step-by-step process below to ensure accuracy and completeness. \n",
                },
                {"role": "user", "content": fact_checking_prompt},
            ],
            temperature=1,
            max_completion_tokens=6000,
            top_p=1,
            stream=False,
            stop=None,
            compound_custom={
                "tools": {
                    "enabled_tools": ["web_search", "browser_automation","visit_website"]
                }
            },
            search_settings={
                "country": "georgia",
            }
        )
        
        response_text = completion.choices[0].message.content

        response_json = await generate_json_from_fact_check_response(response_text)


        try:
            jina_response_parsed: JinaFactCheckResponse = (
                JinaFactCheckResponse.model_validate(response_json)
            )

            logger.info(
                "Jina response parsed successfully",
                extra={
                    "json_fields": {
                        "verification_id": str(request.verification_id),
                        "factuality_score": jina_response_parsed.factuality,
                        "references_count": len(jina_response_parsed.references),
                        "has_reason": bool(jina_response_parsed.reason),
                        "has_summary": bool(jina_response_parsed.reason_summary),
                        "base_operation": "fact_check",
                        "operation": "jina_response_parsed",
                    },
                    "labels": {"component": "jina_fact_checker", "phase": "parse"},
                },
            )

            fact_check_result = FactCheckingResult(
                factuality=jina_response_parsed.factuality,
                reason=jina_response_parsed.reason,
                score_justification=jina_response_parsed.score_justification,
                reason_summary=jina_response_parsed.reason_summary,
                references=jina_response_parsed.references,
                visited_urls=([]),
                read_urls=([]),
            )
            return fact_check_result
        except ValidationError as e:
            error_msg = f"Failed to deserialize Jina response: {str(e)}. Original content: {completion.choices[0].message.content}"

            logger.info(
                "Jina fact check completed successfully",
                extra={
                    "json_fields": {
                        "verification_id": str(request.verification_id),
                        "factuality_score": fact_check_result.factuality,
                        "references_count": len(fact_check_result.references),
                        "visited_urls_count": len(fact_check_result.visited_urls),
                        "read_urls_count": len(fact_check_result.read_urls),
                        "base_operation": "fact_check",
                        "operation": "jina_fact_check_success",
                    },
                    "labels": {"component": "jina_fact_checker", "phase": "complete"},
                },
            )

            return fact_check_result
        except ValidationError as e:
            logger.error(
                "Failed to parse Jina response",
                extra={
                    "json_fields": {
                        "verification_id": str(request.verification_id),
                        "error": str(e),
                        "error_type": "ValidationError",
                        "response_content_length": len(
                            completion.choices[0].message.content
                        ),
                        "base_operation": "fact_check",
                        "operation": "jina_response_parse_error",
                    },
                    "labels": {"component": "jina_fact_checker", "severity": "high"},
                },
                exc_info=True,
            )

            logger.debug(
                "Jina response content that failed parsing",
                extra={
                    "json_fields": {
                        "verification_id": str(request.verification_id),
                        "response_content": completion.choices[0].message.content[
                            :500
                        ],  # First 500 chars for debugging
                        "base_operation": "fact_check",
                        "operation": "jina_response_parse_error_debug",
                    },
                    "labels": {"component": "jina_fact_checker"},
                },
            )
            return None

    except Exception as e:
        logger.error(
            "Jina fact check failed with unexpected error",
            extra={
                "json_fields": {
                    "verification_id": str(request.verification_id),
                    "error": str(e),
                    "error_type": type(e).__name__,
                    "budget_tokens": budget_tokens
                    if "budget_tokens" in locals()
                    else None,
                    "base_operation": "fact_check",
                    "operation": "jina_fact_check_error",
                },
                "labels": {"component": "jina_fact_checker", "severity": "high"},
            },
            exc_info=True,
        )
        return None


@observe(as_type="generation")
async def generate_json_from_fact_check_response(
    response_text: str,
) -> Optional[str]:
    contents = [
        "Return JSON from this markdown response:" + response_text
    ]

    langfuse.update_current_generation(
        input=contents,
        model="gemini-2.5-flash",
        metadata={
            "response_text_length": len(response_text),
        },
    )
    response_json = await gemini_client.aio.models.generate_content(
        model="gemini-2.5-flash",
        contents=contents,
        config=GenerateContentConfig(
            response_mime_type="application/json",
            max_output_tokens=8000,
            thinking_config=ThinkingConfig(
                thinking_budget=1000,
            ),
            system_instruction="You are an expert JSON generator tasked with generating a JSON from the provided markdown response.",
            response_schema=JinaFactCheckResponse.model_json_schema(),
        ),
    )

    langfuse.update_current_generation(
        usage_details={
            "input": response_json.usage_metadata.prompt_token_count,
            "output": response_json.usage_metadata.candidates_token_count,
            "cache_read_input_tokens": response_json.usage_metadata.cached_content_token_count,
        },
    )

    return response_json.parsed
