import json
import logging
from datetime import datetime
from typing import Optional

import httpx
from langfuse import observe
from openai import AsyncOpenAI
from pydantic import ValidationError

from ment_api.configurations.config import settings
from ment_api.models.fact_checking_models import (
    FactCheckingResult,
    FactCheckRequest,
    JinaFactCheckResponse,
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
    has_defs = "$defs" in schema
    if has_defs:
        # Get the FactCheckingReference definition
        fact_checking_ref_def = schema["$defs"].get("FactCheckingReference", {})

        # Update the references property to use the inline definition
        if "properties" in schema and "references" in schema["properties"]:
            schema["properties"]["references"]["items"] = fact_checking_ref_def

        # Remove $defs
        del schema["$defs"]

    response_format = {
        "type": "object",
        "title": schema.get("title", "JinaFactCheckResponse"),
        "properties": schema.get("properties", {}),
        "required": schema.get("required", []),
    }

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
    You are an expert fact-checker tasked with thoroughly analyzing the following post details. Follow the step-by-step process below to ensure accuracy and completeness. 

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
Georgian clarity: Write for average readers but include precise factual details
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
Georgian User Summary
Concise fact-check summary in Georgian, formatted as raw markdown. Optimized for users with short attention spans:

Requirements:

Maximum 2-3 short sentences total (not paragraphs)
Lead with most important finding first
Use simple, direct language for quick scanning
Priority structure: a) False information: "მტკიცება მცდარია:" or "ინფორმაცია არასწორია:" + brief reason (max 15 words) b) Verified true information: "თუმცა, სწორია, რომ..." or "ინფორმაცია სწორია:" + reason (max 15 words)
c) Unverifiable claims: "ვერ გადამოწმდა..." or "გადაუმოწმებელია..." + specific claim (max 10 words)
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

        jina_request_params = {
            "model": "jina-deepsearch-v1",
            "messages": [
                {"role": "user", "content": fact_checking_prompt},
            ],
            "timeout": httpx.Timeout(60 * 10.0),
            "extra_body": {
                "budget_tokens": budget_tokens,
                "verification_id": str(request.verification_id),
            },
            "response_format": get_jina_response_format(),
        }
        # Make a single request to Jina DeepSearch using the module-level client
        logger.debug(
            "Sending request to Jina DeepSearch API",
            extra={
                "json_fields": {
                    "verification_id": str(request.verification_id),
                    "model": "jina-deepsearch-v1",
                    "timeout_seconds": 600,
                    "base_operation": "fact_check",
                    "operation": "jina_api_request_sent",
                },
                "labels": {"component": "jina_fact_checker", "phase": "api_call"},
            },
        )

        jina_response = await jina_client.chat.completions.create(**jina_request_params)
        response_text = json.loads(jina_response.choices[0].message.content)

        logger.info(
            "Jina DeepSearch API response received",
            extra={
                "json_fields": {
                    "verification_id": str(request.verification_id),
                    "response_length": len(jina_response.choices[0].message.content),
                    "has_response": bool(response_text),
                    "base_operation": "fact_check",
                    "operation": "jina_api_response_received",
                },
                "labels": {"component": "jina_fact_checker", "phase": "api_call"},
            },
        )

        try:
            jina_response_parsed: JinaFactCheckResponse = (
                JinaFactCheckResponse.model_validate(response_text)
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
                visited_urls=(
                    jina_response.model_extra.get("visitedURLs", [])
                    if hasattr(jina_response, "model_extra")
                    else []
                ),
                read_urls=(
                    jina_response.model_extra.get("readURLs", [])
                    if hasattr(jina_response, "model_extra")
                    else []
                ),
            )

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
                            jina_response.choices[0].message.content
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
                        "response_content": jina_response.choices[0].message.content[
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
