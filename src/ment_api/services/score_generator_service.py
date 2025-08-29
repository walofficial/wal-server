import logging

from google.genai import Client
from google.genai.types import GenerateContentConfig, ThinkingConfig
from langfuse import observe
from ment_api.services.external_clients.langfuse_client import (
    langfuse,
)
from tenacity import (
    before_sleep_log,
    retry,
    wait_random_exponential,
    stop_after_attempt,
)

from ment_api.services.models.score_generator_service.generate_score_response import (
    GenerateScoreResponse,
)

logger = logging.getLogger(__name__)

system_prompt = """You are an AI analyst specializing in contextual significance assessment.
Your core task is to evaluate the importance of information presented in an article excerpt, strictly moderated by its accompanying fact-check verification."""

user_prompt_template = """You are tasked with generating an importance score between 0 and 100 for an article based on its statements and associated fact check data. A score of 100 indicates the highest importance, while 0 indicates the least importance. Your goal is to analyze the provided information and determine how significant or impactful the article's content is.

First, carefully read the article statements:

<article_statements>
{article_statements}
</article_statements>

Now, consider the fact check reason provided:

<fact_check_reason>
{fact_check_reason}
</fact_check_reason>

To determine the importance score, carefully analyze the following factors:

1. Core Subject Matter & Domain: What fundamental area does the article address (e.g., politics, public health, national security, human rights, economy, science, environment)? Is this domain generally considered critical to society or public welfare?

2. Scale & Scope of Potential Impact:
   - Reach: How many people or what geographic area (local, regional, national, international) could be affected by the information or the events described?
   - Severity: How significant are the potential consequences? Do they relate to fundamental issues like safety, health, rights, governance, economic stability, or social order?

3. Prominence & Authority of Entities Involved: Are the key individuals, groups, or institutions mentioned in the article (those making statements or being discussed) highly influential, authoritative, or representative (e.g., government leaders, major international bodies, large corporations, recognized experts vs. minor figures or niche groups)?

4. Timeliness & Relevance: How closely does the information relate to current, pressing events, ongoing crises, or active public/political discourse? Is there an element of urgency or immediate relevance?

5. Verified Accuracy & Its Significance: 
   - What is the overall accuracy level indicated by the fact check (e.g., accurate, mostly accurate, mixed, misleading, false)?
   - How does this accuracy level interact with the potential impact? (e.g., A highly impactful and accurate statement is very important. A highly impactful but false/misleading statement can also be highly important as potentially harmful misinformation, depending on the context provided by the fact check reason).

Conduct your analysis inside <reasoning> tags. For each factor:
1. Quote relevant parts of the article and fact check.
2. Consider arguments for both higher and lower importance scores.
3. Provide a preliminary score for that factor (1-20, with 20 being highest importance).

After your analysis, provide your justification for the overall score in <justification> tags, followed by the numerical score in <score> tags.

Consult the Granular Score Range Descriptions below during your final assessment. Ensure your numerical <score> accurately reflects the specific level of verified significance detailed in your <reasoning> and summarized in your <justification>.

Granular Score Range Descriptions:

- 91-100 (Exceptional / Critical Importance): Top-tier critical issues (imminent major crisis, national security emergency, groundbreaking scientific discovery with immediate global impact, fundamental democratic process integrity). Extremely severe and widespread impact. Involves the highest levels of authority. Breaking news of utmost urgency. All central, highly impactful claims are fully verified as accurate and reliable by credible evidence.
  
- 81-90 (Very High Importance): Major national or international issues (significant policy shifts, major economic events, widespread public health crises, significant geopolitical developments). Severe and broad impact. Involves high-level authorities or very prominent institutions. Highly timely and relevant to pressing issues. The core message and most impactful claims are verified as accurate.

- 71-80 (High Importance): Significant issues affecting large populations or regions (important legislation, notable social trends, regional environmental concerns, major corporate actions). Substantial impact. Involves prominent figures or organizations. Timely and directly relevant to ongoing public discourse. Key claims are verified as largely accurate.

- 61-70 (Moderately High Importance): Matters of considerable public interest (significant political debates, notable economic indicators, community-wide issues). Moderate to substantial impact. Involves well-known entities or officials. Relevant to current events, though perhaps not breaking news. Information is mostly accurate and verified, but perhaps less impactful claims are central, or verification is solid but not exhaustive.

- 51-60 (Medium Importance): Standard news topics (routine government actions, ongoing social issues without major escalation, relevant industry news). Moderate, possibly localized impact. Involves recognizable but not top-tier entities. Generally relevant and timely. Information is generally accurate but may have noticeable caveats.

- 41-50 (Lower Medium Importance): Less critical topics or updates on known situations (follow-up reports, incremental developments, specific local issues). Impact is moderate but perhaps more limited in scope. Entities may be less prominent. Relevant but not necessarily urgent. Information is mixed, with some verified points but also significant unverified or partially verified claims relevant to the core message.

- 31-40 (Low Importance): Minor news, niche topics, updates confirming the status quo of less critical situations. Limited impact or scope. Entities may not be widely known. Lower relevance or timeliness. Key impactful claims are largely unverified or lean towards inaccurate/misleading.

- 21-30 (Very Low Importance): Topics of marginal public interest, highly localized non-critical events, background information. Negligible broad impact. Minor or unknown entities. Low relevance. Substantial portions are inaccurate, misleading, or unverified.

- 11-20 (Minimal / Near-Negligible Importance): Trivial subjects, potentially outdated information presented as current, purely speculative content. Virtually no impact. Obscure entities. Irrelevant. The core claims are demonstrably false or based on gross misrepresentation.

- 0-10 (Negligible / Misinformation): May attempt to seem important but is fundamentally baseless or nonsensical. The entire basis of the article is proven false, fabricated, or nonsensical. Represents pure misinformation or disinformation with no grounding in reality according to the check.

Remember to base your assessment solely on the provided article statements and fact check reason. Do not introduce external information or make assumptions beyond what is given."""


examples_prompt = """
<examples>
  <example>
    <article_statements>Amid the ongoing political crisis in Georgia, the **US Helsinki Commission** issued a statement supporting the Georgian people in their struggle against an "illegitimate regime", calling for *new, free, and fair elections* and the release of all political prisoners on the 150th day of protests. Concurrently, high-ranking officials from "Georgian Dream", including Parliament Speaker Shalva Papuashvili, continue to criticize foreign partners. Papuashvili and other MPs, Nino Tsilosani and Tornike Cheishvili, specifically accused the **French Embassy** of non-transparent funding of radical opposition groups and election interference, suspicions they claim are intensified by the embassy's silence.</article_statements>
    <fact_check_reason>The text is accurate based on the available information. The US Helsinki Commission's statement is fully verified, aligning with the claim of support on the 150th day of protests and calls for new elections and prisoner release. For the criticisms by Georgian Dream officials, Shalva Papuashvili's actions are confirmed, and given the context, it is reasonable to include Nino Tsilosani and Tornike Cheishvili as part of the group, though direct evidence for their specific statements is lacking. This uncertainty does not negate the overall accuracy, as the text's general claim holds, and their involvement is consistent with their roles.</fact_check_reason>
    <ideal_output>
      <reasoning>1. Core Subject Matter & Domain:
The article covers a significant political crisis, international relations (US/France involvement), democratic processes (elections, protests), and alleged foreign interference within Georgia.
Quote: "ongoing political crisis in Georgia", "US Helsinki Commission issued a statement supporting...", "calling for new, free, and fair elections", "Georgian Dream... criticize foreign partners", "accused the French Embassy of... election interference".
Fact check confirms: The core statement from the US Helsinki Commission is "fully verified". Papuashvili's criticism is "confirmed".
Arguments for higher importance: Deals with high-stakes political stability, national sovereignty, democratic integrity, and international diplomacy. These are inherently significant domains.
Arguments for lower importance: (None apparent based on the subject matter).
Preliminary score: 19/20 - Political crises involving major international actors and democratic fundamentals are highly important.

2. Scale & Scope of Potential Impact:
The events described have potentially large-scale consequences for the entire population of Georgia, regional stability, and diplomatic relations between Georgia, the US, and France.
Quote: "struggle against an 'illegitimate regime'", "new, free, and fair elections", "release of all political prisoners", "criticize foreign partners", "accused the French Embassy".
Fact check confirms: The key claims driving the potential impact (Helsinki Commission statement, Georgian Dream criticism) are largely verified or deemed reasonable.
Arguments for higher importance: Calls for new elections and prisoner releases directly impact governance and citizens' rights. Accusations against a foreign embassy can severely strain international relations. The outcome affects millions and regional geopolitics.
Arguments for lower importance: The immediate, tangible impact is not yet fully realized (e.g., elections haven't happened yet).
Preliminary score: 19/20 - The potential impact on a nation's governance, population, and international standing is substantial.

3. Prominence & Authority of Entities Involved:
The entities involved are significant international and national bodies/officials.
Quote: "US Helsinki Commission", "high-ranking officials from 'Georgian Dream'", "Parliament Speaker Shalva Papuashvili", "French Embassy".
Fact check confirms: Verifies the statement from the "US Helsinki Commission" (a reputable body) and actions of Papuashvili (high-ranking official).
Arguments for higher importance: The US Helsinki Commission is an influential international human rights and security body. The Speaker of Parliament is a top government official. Embassies represent sovereign nations. Statements/actions from these entities carry significant weight.
Arguments for lower importance: While prominent, the influence might be more focused regionally than globally (compared to, say, a UN Security Council resolution).
Preliminary score: 18/20 - Involves high-level national officials and a significant international commission.

4. Timeliness & Relevance:
The article describes an "ongoing political crisis" and actions happening "concurrently" on the "150th day of protests," indicating high timeliness and relevance to current events.
Quote: "ongoing political crisis", "150th day of protests", "continue to criticize".
Fact check confirms: The timeline aspect ("150th day") and the verified statements align with current events.
Arguments for higher importance: Addresses a live, unfolding situation with immediate relevance to international relations and Georgian politics. Political instability in strategically important regions is globally relevant.
Arguments for lower importance: (None apparent, the issue is clearly current).
Preliminary score: 19/20 - Directly relates to a current, significant political situation.

5. Verified Accuracy & Its Significance:
The fact check confirms the accuracy of the most impactful claims and deems the overall text accurate.
Fact check states: "The text is accurate based on the available information", "US Helsinki Commission's statement is fully verified", Papuashvili's actions "confirmed", inclusion of other MPs "reasonable".
Arguments for higher importance: The core, high-impact statement from the US Helsinki Commission is verified. The criticisms from the ruling party leadership are confirmed or reasonably inferred. The overall accuracy supports the significance of the reported events.
Arguments for lower importance: Minor lack of direct evidence for two specific MPs' statements, although considered contextually reasonable by the fact-checker.
Preliminary score: 18/20 - The most critical and impactful information is verified, reinforcing the article's significance.
</reasoning>
      <justification>The article warrants a high importance score (95/100) due to its focus on a critical, ongoing political crisis in Georgia with significant international dimensions. The involvement and verified statements of the US Helsinki Commission, a reputable international body, calling for fundamental changes like new elections and prisoner releases, dramatically elevates the importance. Similarly, verified criticisms and accusations involving high-ranking Georgian officials and the French Embassy underscore the severity and diplomatic stakes. The potential impact on the Georgian population, the country's democratic trajectory, regional stability, and international relations is substantial. The fact check confirms the core accuracy of these high-impact elements, solidifying the article's significance as reporting on a major geopolitical and democratic issue. The high timeliness and relevance further contribute to the score.</justification>
      <score>95</score>
    </ideal_output>
  </example>
  <example>
    <article_statements>Amid the ongoing political crisis in Georgia, the US Helsinki Commission issued a statement supporting the Georgian people in their struggle against an "illegitimate regime", calling for new, free, and fair elections and the release of all political prisoners on the 150th day of protests. Concurrently, high-ranking officials from "Georgian Dream", including Parliament Speaker Shalva Papuashvili, continue to criticize foreign partners. Papuashvili and other MPs, Nino Tsilosani and Tornike Cheishvili, specifically accused the French Embassy of non-transparent funding of radical opposition groups and election interference, suspicions they claim are intensified by the embassy's silence.</article_statements>
    <fact_check_reason>The text is largely accurate, with three claims confirmed and one partially confirmed. The protests' 150th day is fully verified, aligning with the timeline and official statements. The rally on April 26, 2025, is confirmed, but specific details about students and demands are plausible but not explicitly verified. The claim about Vashadze's comment remains unverified due to lack of evidence, though it is consistent with his opposition stance.</fact_check_reason>
    <ideal_output>
      <reasoning>1. Core Subject Matter & Domain:
The article covers politics, international relations, and democratic processes in Georgia.
Quote: "ongoing political crisis in Georgia", "US Helsinki Commission issued a statement...", "Georgian Dream... criticize foreign partners...".
Fact check confirms: *This fact check* primarily verifies the timeline ("150th day") and a specific rally date ("April 26, 2025"). It does *not* explicitly confirm the substance of the Helsinki Commission or Georgian Dream statements mentioned in the article text within *this reason*.
Arguments for higher importance: The underlying subject matter (political crisis, international relations) is inherently significant.
Arguments for lower importance: The *specific aspects verified by this fact check* (timeline marker, rally date) are less substantive than the core political statements mentioned in the article.
Preliminary score: 14/20 - The domain is important, but the verified information is procedural.

2. Scale & Scope of Potential Impact:
The *underlying situation* described (political crisis, international tensions) has high potential impact on Georgia and its relations.
Quote: "struggle against an 'illegitimate regime'", "calling for new, free, and fair elections", "accused the French Embassy".
Fact check confirms: *This fact check* verifies the "150th day" and a rally date. It only "partially confirms" details about the rally and leaves a specific comment "unverified". It does *not* verify the claims with the highest potential impact (Helsinki statement substance, French Embassy accusations) in this instance.
Arguments for higher importance: The broader context involves high stakes.
Arguments for lower importance: The *information verified by this specific fact check* (duration marker, rally confirmation) carries low immediate impact compared to the unverified or unaddressed major political claims.
Preliminary score: 7/20 - High potential impact of the overall situation, but low impact of the *verified information* here.

3. Prominence & Authority of Entities Involved:
The article mentions high-profile entities.
Quote: "US Helsinki Commission", "Georgian Dream", "Parliament Speaker Shalva Papuashvili", "French Embassy".
Fact check confirms: The fact check acknowledges these entities implicitly but focuses verification on timeline/event logistics rather than the substance of their statements or actions in this case.
Arguments for higher importance: The actors involved (US Helsinki Commission, Georgian gov) are significant.
Arguments for lower importance: The *verified information provided by this specific fact check* does not center on the actions or confirmed statements of these prominent entities. Their prominence isn't leveraged by the verified facts here.
Preliminary score: 10/20 - Actors are important, but their relevance isn't confirmed by the verified facts in *this* check.

4. Timeliness & Relevance:
The article addresses an "ongoing political crisis."
Quote: "ongoing political crisis", "150th day of protests".
Fact check confirms: Verifies the "150th day" and a specific recent/future rally date ("April 26, 2025"), confirming the situation is current.
Arguments for higher importance: The general topic remains relevant and timely.
Arguments for lower importance: The *verified information* (reaching day 150, confirming a rally) mainly serves as an update confirming the *continuation* of a known situation, rather than presenting a major *new* verified development. Low novelty based on verified facts.
Preliminary score: 8/20 - Topic is timely, but verified info lacks significant novelty.

5. Verified Accuracy & Its Significance:
The fact check labels the text "largely accurate" but specifies what *is* verified, partially verified, and unverified.
Fact check states: "150th day is fully verified", rally date "confirmed", rally details "plausible but not explicitly verified", Vashadze comment "unverified". Crucially, *this specific fact check does not verify the content of the Helsinki Commission statement or the French Embassy accusations*.
Arguments for higher importance: Confirms the timeline and occurrence of related events (rally).
Arguments for lower importance: The most impactful claims mentioned in the article text (Helsinki demands, GD accusations) are *not* confirmed by *this* fact check reason. The verified points are secondary/procedural. The "largely accurate" assessment seems based on these less critical points being confirmed.
Preliminary score: 6/20 - Accuracy is confirmed only for low-impact details; high-impact claims are not addressed or verified here.
</reasoning>
      <justification>This article receives a relatively low importance score (35/100) primarily because the *specific information verified by the provided fact check* is of limited significance. While the article text discusses potentially high-impact events involving major actors (US Helsinki Commission, Georgian government, French Embassy), this particular fact check only confirms procedural or secondary details like the 150th day of protests and a rally date. It explicitly notes partial verification or lack of verification for other details and, crucially, does *not* confirm the substance of the most impactful political statements mentioned (the Helsinki Commission's demands or the accusations against the French Embassy). Therefore, despite the importance of the overall context, the informational value added by *this specific verification* is low, focusing on continuity rather than confirming critical developments. The score reflects the low significance of the *verified* facts in this instance.</justification>
      <score>35</score>
    </ideal_output>
  </example>
  <example>
    <article_statements>A shocking new report from the "Citizens for Clean Air" initiative claims that recent municipal data, suppressed by city officials, shows levels of airborne pollutant Particle Z exceeding federal safety limits by over 500% in downtown areas following the opening of the new factory complex. The report links this directly to a documented 30% spike in emergency room visits for respiratory distress in the last quarter, urging immediate closure of the factory.</article_statements>
    <fact_check_reason>This report contains significant inaccuracies and misleading interpretations. While Particle Z levels did show a minor, temporary increase near the factory opening (within 10% of limits, not 500%), official, publicly available city data confirms levels have remained well below federal safety limits consistently. The claimed 30% spike in ER visits is unsubstantiated; hospital records show no statistically significant change in respiratory admissions for the period. The "Citizens for Clean Air" initiative has previously published unverified claims, and there is no evidence of data suppression by city officials. The core premises of the report are demonstrably false based on verified data.</fact_check_reason>
    <ideal_output>
      <reasoning>1. Core Subject Matter & Domain:
The article addresses public health, environmental pollution, and local governance (potential data suppression).
Quote: "levels of airborne pollutant Particle Z exceeding federal safety limits by over 500%", "30% spike in emergency room visits for respiratory distress", "data, suppressed by city officials".
Fact check confirms: The claims regarding pollution levels, ER visits, and data suppression are "demonstrably false".
Arguments for higher importance: Public health and environmental safety are generally critical domains. Allegations of government suppression are serious.
Arguments for lower importance: The specific claims made within these domains are found to be inaccurate/false, undermining the importance of *this specific report*.
Preliminary score: 5/20 - Domain is important, but the specific information presented is false.

2. Scale & Scope of Potential Impact:
The *claims* in the article suggest a very high impact: severe health risks (500% over limit, 30% ER spike) affecting potentially many people ("downtown areas").
Quote: "exceeding federal safety limits by over 500% in downtown areas", "30% spike in emergency room visits".
Fact check confirms: These high-impact claims are "inaccurate" and "unsubstantiated". The actual increase was minor and temporary ("within 10% of limits"), and ER visits showed "no statistically significant change".
Arguments for higher importance: If the claims *were* true, the impact would be significant.
Arguments for lower importance: The fact check decisively refutes the high-impact claims, meaning the *actual* verified impact related to this report is negligible or non-existent. The described danger is false.
Preliminary score: 2/20 - Claimed impact is high, but verified reality shows claims are false.

3. Prominence & Authority of Entities Involved:
Entities mentioned are "Citizens for Clean Air" (source), city officials, and a new factory complex. Counter-evidence comes from official city data and hospital records.
Quote: "'Citizens for Clean Air' initiative", "city officials".
Fact check confirms: The source ("Citizens for Clean Air") "has previously published unverified claims", undermining its authority. Official data and hospital records contradict the source. No evidence supports the claim against "city officials".
Arguments for higher importance: Involves local government ("city officials").
Arguments for lower importance: The primary source of the claims is explicitly flagged as unreliable by the fact check. Authoritative sources (official data, hospital records) contradict the claims.
Preliminary score: 3/20 - Involves governance, but the primary source is unreliable and its claims against officials are unsubstantiated.

4. Timeliness & Relevance:
The claims relate to "recent municipal data", "following the opening of the new factory complex", suggesting timeliness.
Quote: "recent municipal data", "new factory complex", "last quarter".
Fact check confirms: While the *timing* might be recent, the *substance* of the claims made about this period is false.
Arguments for higher importance: Air quality and public health impacts of industry are relevant topics.
Arguments for lower importance: The specific event described (a massive pollution spike causing health crises) is shown to be inaccurate, making this specific report irrelevant as factual information, though potentially relevant as misinformation.
Preliminary score: 4/20 - Topic area is relevant, but the specific claims are false, reducing relevance.

5. Verified Accuracy & Its Significance:
The fact check thoroughly debunks the core claims of the article.
Fact check states: "significant inaccuracies", "misleading interpretations", "500% [claim]... levels have remained well below federal safety limits", "30% spike... unsubstantiated;... no statistically significant change", "no evidence of data suppression", "core premises... demonstrably false".
Arguments for higher importance: (None - the significance lies in its *inaccuracy*).
Arguments for lower importance: The article's central claims are verified as false based on official data. It presents misinformation, not important factual information.
Preliminary score: 1/20 - The article is demonstrably inaccurate on its key points.
</reasoning>
      <justification>The importance score is very low (15/100) primarily because the fact check decisively refutes the core claims made in the article, demonstrating them to be inaccurate and misleading. While the subject areas (public health, environment, governance) are intrinsically important, the specific assertions presented here – extreme pollution levels (500% over limits), a consequent health crisis (30% ER spike), and data suppression by officials – are shown to be "demonstrably false" based on verified, official data. Furthermore, the source ("Citizens for Clean Air") is identified as having a history of unverified claims, undermining its credibility. Since the foundational claims lack accuracy, the article's value as important, actionable information is minimal. Its main significance is as an example of misinformation that has been debunked.</justification>
      <score>15</score>
    </ideal_output>
  </example>
</examples>
"""


@observe(as_type="generation")
@retry(
    wait=wait_random_exponential(multiplier=1, max=3),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
    stop=stop_after_attempt(4),
)
async def generate_score(
    client: Client,
    article_statements: str,
    fact_check_reason: str,
) -> GenerateScoreResponse:
    generate_score_user_prompt = user_prompt_template.format(
        article_statements=article_statements,
        fact_check_reason=fact_check_reason,
    )

    langfuse.update_current_generation(
        input=[generate_score_user_prompt, examples_prompt],
        model="gemini-2.5-flash-lite",
        metadata={
            "article_statements_length": len(article_statements),
            "fact_check_reason_length": len(fact_check_reason),
        },
    )

    response = await client.aio.models.generate_content(
        model="gemini-2.5-flash-lite",
        config=GenerateContentConfig(
            system_instruction=system_prompt,
            response_mime_type="application/json",
            response_schema=GenerateScoreResponse.model_json_schema(),
            thinking_config=ThinkingConfig(
                thinking_budget=5000,
            ),
        ),
        contents=[generate_score_user_prompt, examples_prompt],
    )

    langfuse.update_current_generation(
        usage_details={
            "input": response.usage_metadata.prompt_token_count,
            "output": response.usage_metadata.candidates_token_count,
            "cache_read_input_tokens": response.usage_metadata.cached_content_token_count,
        },
    )

    return GenerateScoreResponse.model_validate_json(response.text)
