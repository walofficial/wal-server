{
    "type": "object",
    "title": "JinaFactCheckResponse",
    "properties": {
        "factuality": {
            "description": "FACTUALITY SCORE REQUIREMENTS:\n\nOutput a precise numerical score between 0.0 and 1.0 representing the factuality of the post details. This score will be displayed to users as a factuality indicator.\n\nSCORING SCALE:\n• 0.9-1.0: Highly factual - well-supported by multiple reliable sources\n• 0.7-0.9: Mostly factual - minor uncertainties present  \n• 0.4-0.7: Partially factual - significant uncertainties present\n• 0.0-0.4: Mostly false or unverifiable claims\n\nSCORING METHODOLOGY:\n1. Weight evidence based on source authority and verification\n2. Evaluate source credibility and reliability systematically\n3. Assess strength of evidence for each claim\n4. Consider multiple perspectives and contrasting viewpoints\n5. Cross-reference claims across reliable sources",
            "title": "Factuality",
            "type": "number",
        },
        "reason": {
            "description": 'GEORGIAN STRUCTURED ANALYSIS - CRITICAL FORMATTING REQUIREMENTS:\n\nYour response MUST contain actual line break characters (newlines) between each element. Format as a multi-line string with proper line breaks, NOT as a single continuous line.\n\nMANDATORY STRUCTURE - Include ONLY sections with actual bullet points:\n\n## სიმართლე\n- [bullet point for verified true claim]\n- [bullet point for verified true claim]\n\n[Evidence paragraph: Lead with strongest supporting evidence, then provide 2-3 additional concrete details with specific dates, numbers, official statements. Include natural source references. Expand with NEW information beyond bullet points. Show how multiple sources corroborate.]\n\n## ტყუილი  \n- [bullet point for verified false claim]\n- [bullet point for verified false claim]\n\n[Evidence paragraph: Begin with most definitive contradicting evidence, then provide specific facts that disprove claims. Include precise contradictory data, official denials, timeline discrepancies. Focus on new contradictory evidence. Show how authoritative sources consistently refute these claims.]\n\n## გადაუმოწმებელი\n- [bullet point for unverifiable claim]\n\n[Evidence paragraph: Explain specific reasons verification is impossible - lack of official records, conflicting reports, insufficient documentation. Provide concrete examples of missing information. Reference consulted sources. Focus on factual gaps and source limitations.]\n\nCRITICAL FORMATTING RULES:\n• Use actual newline characters (\\n) in response\n• Each bullet point on separate line starting with "- "\n• Add blank lines between bullet points and evidence paragraphs\n• Add blank lines between sections\n• Use "---" separator only between sections (not after last section)\n• Keep bullet points concise and specific\n• Write evidence paragraphs in clear Georgian for average readers\n• DO NOT include empty sections without bullet points\n\nVERIFICATION CHECKLIST:\n□ Actual line breaks between bullet points\n□ Actual line breaks between sections  \n□ Multi-line string format (not single line)\n□ Only sections with actual content\n□ Proper spacing with blank lines',
            "title": "Reason",
            "type": "string",
        },
        "score_justification": {
            "description": "COMPREHENSIVE ENGLISH ANALYTICAL JUSTIFICATION:\n\nProvide extremely detailed analysis explaining the factuality score reasoning. This should be more thorough than the Georgian reason field.\n\nREQUIRED CONTENT:\n1. METHODOLOGY: Explain the evaluation approach used\n2. EVIDENCE WEIGHTING: Detail how different types of evidence were assessed and weighted\n3. SOURCE CREDIBILITY: Analyze reliability and authority of sources consulted  \n4. LOGICAL REASONING: Step-by-step reasoning process behind conclusions\n5. SCORE JUSTIFICATION: Complete explanation for the specific numerical score assigned\n\nQUALITY STANDARDS:\n• Be extremely detailed and analytical\n• Value strong arguments over source authority alone\n• Consider new technologies and contrarian perspectives\n• Use high levels of analysis appropriate for experienced analysts\n• Accuracy is critical - mistakes erode trust\n• Be highly organized in response structure\n• Include specific evidence assessment and cross-referencing methodology",
            "title": "Score Justification",
            "type": "string",
        },
        "reason_summary": {
            "description": 'GEORGIAN USER SUMMARY - OPTIMIZED FOR SHORT ATTENTION SPANS:\n\nConcise fact-check summary in Georgian, formatted as raw markdown (no code blocks). Must be optimized for users who scan quickly.\n\nREQUIREMENTS:\n• Maximum 2-3 short sentences total (NOT paragraphs)\n• Lead with most important finding first\n• Use simple, direct language for 3-second comprehension  \n• Write for immediate understanding\n\nPRIORITY STRUCTURE (use in order of impact):\n1. FALSE INFORMATION (if present): Start with "მტკიცება მცდარია:" or "ინფორმაცია არასწორია:" + brief reason (max 15 words)\n2. VERIFIED TRUE INFORMATION (if present): Use "თუმცა, სწორია, რომ..." after false claim, or lead with "ინფორმაცია სწორია:" if main point (max 15 words)  \n3. UNVERIFIABLE CLAIMS (if significant): End with "ვერ გადამოწმდა..." or "გადაუმოწმებელია..." + specific claim (max 10 words)\n\nWRITING STYLE:\n• Skip categories with no significant findings\n• Use active voice and specific terms\n• Avoid technical jargon or complex sentences\n• No academic language - write for average readers\n• Each sentence should add new value',
            "title": "Reason Summary",
            "type": "string",
        },
        "references": {
            "default": [],
            "description": "REFERENCE REQUIREMENTS:\n\nProvide comprehensive reference objects supporting the fact check analysis.\n\nEACH REFERENCE MUST INCLUDE:\n• url: Direct URL to the source\n• source_title: Full title of the reference source  \n• key_quote: Exact quote from source in original language that supports the analysis\n• is_supportive: Boolean indicating whether reference supports or contradicts the post details\n\nQUALITY STANDARDS:\n• Include URLs to authoritative sources\n• Provide key quotes that directly relate to claims being verified\n• Clear indication of support vs contradiction for each source\n• Prioritize official sources, established media, and verified documentation\n• Cross-reference multiple sources when possible",
            "items": {
                "properties": {
                    "url": {
                        "description": "Direct URL to the source",
                        "title": "Url",
                        "type": "string",
                    },
                    "source_title": {
                        "description": "Full title of the reference source",
                        "title": "Source Title",
                        "type": "string",
                    },
                    "key_quote": {
                        "description": "Exact quote from source in original language that supports the analysis",
                        "title": "Key Quote",
                        "type": "string",
                    },
                    "is_supportive": {
                        "description": "Boolean indicating whether reference supports or contradicts the post details",
                        "title": "Is Supportive",
                        "type": "boolean",
                    },
                },
                "required": ["url", "source_title", "key_quote", "is_supportive"],
                "title": "FactCheckingReference",
                "type": "object",
            },
            "title": "References",
            "type": "array",
        },
    },
    "required": ["factuality", "reason", "score_justification", "reason_summary"],
}
