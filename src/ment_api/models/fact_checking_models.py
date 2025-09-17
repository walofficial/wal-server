from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from ment_api.common.custom_object_id import CustomObjectId


class FactCheckingReference(BaseModel):
    """
    Reference supporting or refuting a fact check
    """

    url: str = Field(description="URL of the reference source")
    source_title: Optional[str] = Field(
        default="", description="Title of the reference source"
    )
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
    """
    Extraction-only mapping from a finalized fact-check report.

    This model is used to parse a pre-generated, finalized report.
    Do NOT perform verification, re-scoring, reasoning, or external research.
    Only extract and, when truly necessary, derive strictly from the report’s
    own content, rubrics, and labels.
    """

    factuality: float = Field(
        description="""EXTRACTION-ONLY FACTUALITY SCORE:

Extract the factuality score exactly as reported in the final report. If the
final report does not provide an explicit numeric score, derive it ONLY from
the report’s own explicit rubric, scale, labels, or conclusion statements.
Do NOT use external knowledge or new reasoning. Do NOT verify or re-evaluate.

NORMALIZATION RULES (only if the report uses a different scale):
• 0–100 or percentages → divide by 100 (e.g., 80% → 0.8, 80/100 → 0.8)
• 0–10 → divide by 10 (e.g., 8/10 → 0.8)
• If already 0.0–1.0 → keep as is

FORMATTING:
• Clip to [0.0, 1.0]
• Round to 2 decimal places
• If multiple scores appear, choose the one explicitly labeled “final”, “overall”,
  or appearing in the summary/conclusion section

DERIVATION GUIDELINES (when no numeric score is present):
• If the report maps labels (e.g., True/Mostly True/Partially True/False) to a
  numeric scale, use that mapping exactly as stated
• If the report provides a rubric (e.g., weightings, thresholds), apply it as
  written to compute a score using only report-provided inputs
• If only qualitative descriptors are given, use the report’s own descriptor-to-
  number guidance; if none is provided, return 0.0 for clearly false, 1.0 for
  clearly true, or 0.5 when the report states it is mixed/uncertain—only if such
  language is explicitly present in the report
• Document the derivation in `score_justification` by quoting the exact parts of
  the report that guided the mapping"""
    )

    reason: str = Field(
        description="""GEORGIAN ANALYSIS – EXTRACTION/DERIVATION FROM REPORT ONLY:

Extract the Georgian structured analysis from the final report. Do NOT verify
claims or add new evidence. Preserve the report’s original structure and
wording as much as possible.

MARKDOWN STRUCTURE:
• Use H2 headings exactly as: "## სიმართლე", "## ტყუილი", "## გადაუმოწმებელი"
• Include only sections that have content in the report; omit empty sections
• Under each included heading:
  - Bulleted list of claims (one claim per bullet)
  - After the bullets, one evidence paragraph (plain text) that stays within
    the report’s wording

BULLETS:
• Start each bullet with "- " (hyphen + space); do not use numbers or checkboxes
• Keep each bullet to one concise claim; no nested lists
• Use Georgian wording from the report (verbatim or minimally trimmed)

EVIDENCE PARAGRAPH:
• Write as a single paragraph per section using the report’s own sentences
• May include inline links that exist in the report: [label](url)
• For quotes present in the report, you may use "> " lines or embed in the
  paragraph with quotation marks

    FORMATTING RULES:
    • Newlines: use actual newline characters (\\n)
      - After each H2 heading: add a blank line (\\n\\n)
      - Between the last bullet and the evidence paragraph: add a blank line (\\n\\n)
      - Between sections: add a blank line (\\n\\n)
      - Each bullet is on its own line and ends with \\n
• No code blocks, tables, images, emojis, or autogenerated labels
• Inline emphasis (e.g., **bold**) only if present in the report; do not invent
• Language must remain Georgian; keep original names/dates as written
• Do NOT create new claims or reinterpretation; extract only from the report

DERIVATION WHEN NOT EXPLICIT:
• If findings are scattered, compile them under the three headings using verbatim
  sentences or minimal trims only
• Rely solely on statements present in the report; do NOT add reasoning
• If a section has no explicit content in the report, omit that section"""
    )

    score_justification: str = Field(
        description="""ENGLISH JUSTIFICATION – EXTRACTION/DERIVATION FROM REPORT ONLY:

Extract the report’s existing justification/methodology/explanation for the
score. Do NOT generate new reasoning or re-evaluate evidence.

GUIDELINES:
• Prefer verbatim extraction of the justification section
• If no dedicated section exists, extract the sentences that explicitly explain
  how the score was determined (quoted as-is)
• Keep original ordering and terminology from the report

DERIVATION WHEN NOT EXPLICIT:
• If rationale is distributed across the report, compile only the sentences that
  state the rubric, weights, thresholds, or conclusion logic (verbatim)
• Quote phrases and cite section names when available; avoid paraphrasing unless
  trimming for brevity without changing meaning
• Do NOT add analysis beyond what the report explicitly states"""
    )

    reason_summary: str = Field(
        description="""GEORGIAN SUMMARY – EXTRACTION/DERIVATION FROM REPORT ONLY:

Extract the report’s user-facing Georgian summary. Do NOT add new content or
rephrase beyond minimal trimming.

GUIDELINES:
• If a summary exists, copy it verbatim
• If absent, produce a 2–3 sentence compression using only explicit conclusions
  stated in the report (no new claims)
• Prefer quoting key phrases; do not change their meaning
• Keep raw markdown if present in the source"""
    )

    references: List[FactCheckingReference] = Field(
        description="""REFERENCES – EXTRACTION/DERIVATION FROM REPORT ONLY:

Extract references exactly as listed in the final report. Do NOT introduce new
sources. Populate fields from the report only.

EACH REFERENCE SHOULD INCLUDE:
• url: The cited URL from the report (leave empty if truly not provided)
• source_title: Title as written in the report (leave empty if not provided)
• key_quote: A direct quote associated with that source, copied from the report
• is_supportive: Use the report’s own stance/labeling (supporting/contradicting)

DERIVATION WHEN NOT EXPLICITLY LISTED:
• If the report has inline citations/URLs but no reference list, extract those
• Determine is_supportive only from explicit context in the report (e.g., placed
  in a “supporting evidence” vs “contradicting evidence” section); do NOT infer
  beyond the report’s own wording
• Do NOT add sources that are not present in the report""",
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
