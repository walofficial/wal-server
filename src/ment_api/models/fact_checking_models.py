from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from ment_api.common.custom_object_id import CustomObjectId


class FactCheckingReference(BaseModel):
    """
    Reference supporting or refuting a fact check
    """

    url: str = Field(description="URL of the reference source")
    source_title: str = Field(default=None, description="Title of the reference source")
    key_quote: str = Field(
        description="Key quote from the source supporting the fact check",
    )
    is_supportive: bool = Field(
        description="Whether the reference supports or refutes the statement",
    )


class FactCheckingResult(BaseModel):
    """
    Result of a fact check operation
    """

    factuality: float = Field(
        default=None,
        description="Factuality score from 0-1, where 0 is completely false and 1 is completely true",
    )
    reason: str = Field(
        default=None,
        description="Structured Georgian explanation with sections სიმართლე/ტყუილი/გადაუმოწმებელი using bullet points and evidence paragraphs",
    )
    score_justification: str = Field(
        default=None,
        description="Comprehensive English analysis explaining the detailed reasoning behind the factuality score generation",
    )
    reason_summary: Optional[str] = Field(
        default=None,
        description="1-2 sentence summary of the fact check result which can be read easily",
    )
    fact_status: Optional[str] = Field(
        default=None,
        description="1 or 2 word fact status",
    )
    references: List[FactCheckingReference] = Field(
        default_factory=list, description="List of references supporting the fact check"
    )
    visited_urls: List[str] = Field(
        default_factory=list, description="URLs visited during fact checking"
    )
    read_urls: List[str] = Field(
        default_factory=list, description="URLs read during fact checking"
    )
    usage: Optional[Dict[str, Any]] = Field(
        default=None, description="Usage information about the fact check"
    )


class JinaFactCheckResponse(BaseModel):
    factuality: float = Field(
        description="""FACTUALITY SCORE REQUIREMENTS:
        
Output a precise numerical score between 0.0 and 1.0 representing the factuality of the post details. This score will be displayed to users as a factuality indicator.

SCORING SCALE:
• 0.9-1.0: Highly factual - well-supported by multiple reliable sources
• 0.7-0.9: Mostly factual - minor uncertainties present  
• 0.4-0.7: Partially factual - significant uncertainties present
• 0.0-0.4: Mostly false or unverifiable claims

SCORING METHODOLOGY:
1. Weight evidence based on source authority and verification
2. Evaluate source credibility and reliability systematically
3. Assess strength of evidence for each claim
4. Consider multiple perspectives and contrasting viewpoints
5. Cross-reference claims across reliable sources"""
    )

    reason: str = Field(
        description="""GEORGIAN STRUCTURED ANALYSIS - CRITICAL FORMATTING REQUIREMENTS:

Your response MUST contain actual line break characters (newlines) between each element. Format as a multi-line string with proper line breaks, NOT as a single continuous line.

MANDATORY STRUCTURE - Include ONLY sections with actual bullet points:

## სიმართლე
- [bullet point for verified true claim]
- [bullet point for verified true claim]

[Evidence paragraph: Lead with strongest supporting evidence, then provide 2-3 additional concrete details with specific dates, numbers, official statements. Include natural source references. Expand with NEW information beyond bullet points. Show how multiple sources corroborate.]

## ტყუილი  
- [bullet point for verified false claim]
- [bullet point for verified false claim]

[Evidence paragraph: Begin with most definitive contradicting evidence, then provide specific facts that disprove claims. Include precise contradictory data, official denials, timeline discrepancies. Focus on new contradictory evidence. Show how authoritative sources consistently refute these claims.]

## გადაუმოწმებელი
- [bullet point for unverifiable claim]

[Evidence paragraph: Explain specific reasons verification is impossible - lack of official records, conflicting reports, insufficient documentation. Provide concrete examples of missing information. Reference consulted sources. Focus on factual gaps and source limitations.]

CRITICAL FORMATTING RULES:
• Use actual newline characters (\\n) in response
• Each bullet point on separate line starting with "- "
• Add blank lines between bullet points and evidence paragraphs
• Add blank lines between sections
• Use "---" separator only between sections (not after last section)
• Keep bullet points concise and specific
• Write evidence paragraphs in clear Georgian for average readers
• DO NOT include empty sections without bullet points

VERIFICATION CHECKLIST:
□ Actual line breaks between bullet points
□ Actual line breaks between sections  
□ Multi-line string format (not single line)
□ Only sections with actual content
□ Proper spacing with blank lines"""
    )

    score_justification: str = Field(
        description="""COMPREHENSIVE ENGLISH ANALYTICAL JUSTIFICATION:

Provide extremely detailed analysis explaining the factuality score reasoning. This should be more thorough than the Georgian reason field.

REQUIRED CONTENT:
1. METHODOLOGY: Explain the evaluation approach used
2. EVIDENCE WEIGHTING: Detail how different types of evidence were assessed and weighted
3. SOURCE CREDIBILITY: Analyze reliability and authority of sources consulted  
4. LOGICAL REASONING: Step-by-step reasoning process behind conclusions
5. SCORE JUSTIFICATION: Complete explanation for the specific numerical score assigned

QUALITY STANDARDS:
• Be extremely detailed and analytical
• Value strong arguments over source authority alone
• Consider new technologies and contrarian perspectives
• Use high levels of analysis appropriate for experienced analysts
• Accuracy is critical - mistakes erode trust
• Be highly organized in response structure
• Include specific evidence assessment and cross-referencing methodology"""
    )

    reason_summary: str = Field(
        description="""GEORGIAN USER SUMMARY - OPTIMIZED FOR SHORT ATTENTION SPANS:

Concise fact-check summary in Georgian, formatted as raw markdown (no code blocks). Must be optimized for users who scan quickly.

REQUIREMENTS:
• Maximum 2-3 short sentences total (NOT paragraphs)
• Lead with most important finding first
• Use simple, direct language for 3-second comprehension  
• Write for immediate understanding

PRIORITY STRUCTURE (use in order of impact):
1. FALSE INFORMATION (if present): Start with "მტკიცება მცდარია:" or "ინფორმაცია არასწორია:" + brief reason (max 15 words)
2. VERIFIED TRUE INFORMATION (if present): Use "თუმცა, სწორია, რომ..." after false claim, or lead with "ინფორმაცია სწორია:" if main point (max 15 words)  
3. UNVERIFIABLE CLAIMS (if significant): End with "ვერ გადამოწმდა..." or "გადაუმოწმებელია..." + specific claim (max 10 words)

WRITING STYLE:
• Skip categories with no significant findings
• Use active voice and specific terms
• Avoid technical jargon or complex sentences
• No academic language - write for average readers
• Each sentence should add new value"""
    )

    references: List[FactCheckingReference] = Field(
        default=[],
        description="""REFERENCE REQUIREMENTS:

Provide comprehensive reference objects supporting the fact check analysis.

EACH REFERENCE MUST INCLUDE:
• url: Direct URL to the source
• source_title: Full title of the reference source  
• key_quote: Exact quote from source in original language that supports the analysis
• is_supportive: Boolean indicating whether reference supports or contradicts the post details

QUALITY STANDARDS:
• Include URLs to authoritative sources
• Provide key quotes that directly relate to claims being verified
• Clear indication of support vs contradiction for each source
• Prioritize official sources, established media, and verified documentation
• Cross-reference multiple sources when possible""",
    )


class FactCheckRequest(BaseModel):
    """
    Request for fact checking service
    """

    details: str = Field(description="Details of the post to check for factuality")
    budget_tokens: Optional[int] = None
    verification_id: Optional[CustomObjectId] = Field(
        default=None, description="Verification ID"
    )
