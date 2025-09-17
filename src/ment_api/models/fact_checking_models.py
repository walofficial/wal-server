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
        description="""FACTUALITY SCORE — GENERIC REPORT-ONLY INFERENCE:

Assume the report is the sole source of truth. Produce a score in [0, 1]
purely from what the report states (no external knowledge).

STEPS:
1) Extract factual signals (from the report only):
   • Supporting signals: statements indicating correctness, confirmations,
     corroborations, official acknowledgments
   • Refuting signals: statements indicating incorrectness, contradictions,
     official denials, disprovals
   • Uncertain signals: statements indicating lack of data, conflicts, or
     unverifiability
2) Score each signal’s salience from 0.0–1.0 using ONLY textual cues present in
   the report (e.g., “official statement” > “media report” > “opinion”). Use a
   small discrete set: {1.0 (high), 0.6 (medium), 0.3 (low)}. Do not invent
   criteria beyond what the report states.
3) Aggregate:
   S = sum(salience of supporting signals)
   R = sum(salience of refuting signals)
   U = sum(salience of uncertain signals)
4) Base score:
   raw = (S - R) / max(1e-6, S + R + U)
   s = 0.5 + 0.5 * raw
5) Uncertainty dampening:
   u = U / max(1e-6, S + R + U)
   s = 0.5 + (s - 0.5) * (1 - min(0.7, u))
6) Conclusion anchoring (only if explicitly present in the report):
   • Clear final affirmation → s = max(s, 0.75)
   • Clear final refutation → s = min(s, 0.25)
   • Explicit “unverifiable/insufficient” → s = 0.50

OUTPUT:
• Clip to [0.0, 1.0]; round to 2 decimals
• Do not resolve contradictions; let S/R/U reflect them
• Use only the report; no new evidence or knowledge

TRACEABILITY:
• In `score_justification`, quote the key lines for top signals, list their
  salience and polarity, and report (S, R, U) and final s"""
    )

    reason: str = Field(
        description="""GEORGIAN ANALYSIS – EXTRACTION/DERIVATION FROM REPORT ONLY:

Extract the Georgian structured analysis from the final report. Do NOT verify
claims or add new evidence. Preserve the report’s original structure and
wording as much as possible.

MARKDOWN STRUCTURE:
• Use H2 headings exactly as: "## სიმართლე", "## ტყუილი", "## გადაუმოწმებელი"
• Include only sections that have bullet content; omit sections with zero bullets
• Under each included heading:
  - Bulleted list of claims (one claim per bullet; maximum 4 bullets)
  - After the bullets, one evidence paragraph (plain text) that stays within
    the report’s wording

BULLETS:
• Start each bullet with "- " (hyphen + space); do not use numbers or checkboxes
• Keep each bullet to one concise claim; no nested lists; maximum 4 bullets
• If more than 4 candidate bullets exist in the report, select the 4 most
  important strictly based on cues in the report (e.g., explicitness, source
  authority as stated, centrality to the conclusion); preserve wording
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
        description="""ENGLISH JUSTIFICATION — REPORT-ONLY:

Summarize, using only the report, how the score was derived. Cite key
supporting/refuting/uncertain signals and the report’s own rationale. Include
S, R, U totals and the final s. Quote brief phrases or reference sections for
traceability. Do not add new reasoning or external knowledge."""
    )

    reason_summary: str = Field(
        description="""GEORGIAN SUMMARY – FIXED, ULTRA-BRIEF, REPORT-ONLY:

Purpose: One familiar, scannable mini-summary derived only from `reason`.
Audience: Short attention span; immediate comprehension.

STRUCTURE (up to 3 sentences; include a sentence only if the category has
bullets in `reason`):
1) False (only if `reason` has false bullets):
   "ტყუილია: <the single most impactful false claim in 10–14 words>."
2) True (only if `reason` has true bullets):
   "სწორია: <the single most impactful true claim in 10–14 words>."
3) Unverifiable (only if `reason` has unverifiable bullets):
   "გადაუმოწმებელია: <the single most impactful unclear point in 8–12 words>."

RULES:
• Extraction-only from `reason`; no new claims or reworded interpretations
• Keep each sentence standalone; do not chain with conjunctions
• Omit a category entirely if `reason` has zero bullets for it
• Per category, include exactly one snippet: the most impactful claim/point
• Impact criteria (from report cues only): explicit official statements >
  multi-source corroborations > central assertions > peripheral notes
• Max total 3 sentences; avoid punctuation clutter; keep simple Georgian
• Use actual newline characters (\n) only if rendering as list; otherwise a single paragraph
• Prefer exact phrases from `reason` (minimally trimmed) for claim snippets"""
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
