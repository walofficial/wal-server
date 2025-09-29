import logging
from datetime import datetime

logger = logging.getLogger(__name__)


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

Before you start the fact checking process, make sure to gather all the real time information you need for the persons, places, events, etc. that are mentioned in the post details. For example someone might have become a president or something today or someone made a statement maybe make sure to gather information from search instead of using training data.
You should just use this real time information as a context to details below and not in a actual response markdown.
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


