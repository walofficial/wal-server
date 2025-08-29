"""Schemas for the Gemini client."""

# Response schema for structured output
FACT_CHECK_RESPONSE_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "type": "object",
        "properties": {
            "factuality": {
                "type": "number",
                "description": "Factuality score from 0-1, where 0 is completely false and 1 is completely true",
            },
            "result": {
                "type": "boolean",
                "description": "Overall result of the fact check: true or false",
            },
            "reason": {
                "type": "string",
                "description": "3-6 sentence detailed explanation of the fact check, in Georgian language. "
                + """
                ✅ Writing Style:
                - Use everyday spoken Georgian, be professional and keep it short and simple.
                - Keep sentences short and simple.
                - Break thoughts into paragraphs for easier reading.
                - Use common Georgian idioms and phrases.

                ✅ Language Choices:
                - Use common, widely understood Georgian words.
                - Avoid academic jargon or complex terminology.
                - Use active voice instead of passive.
                - Avoid English loanwords unless necessary.

                ✅ Formatting:
                - No Emojis
                - Use proper punctuation to show tone.
                - No bullet points or numbering, or bold text, avoid such things.

                ✅ Tone:
                - Keep a friendly and relatable tone.
                - Reference Georgian places, events or trends.

                ✅ Message:
                - Get to the point quickly.
                - Have a clear takeaway in each section.
                - Make sure information is easy to understand at first glance.
                """,
            },
            "reason_summary": {
                "type": "string",
                "description": "1-2 sentence summary of the reasoning in Georgian language, should be short and concise. Translated to Georgian language. "
                + """
                ✅ Writing Style:
                - Use everyday spoken Georgian, be professional and keep it short and simple.
                - Keep sentences short and simple.
                - Break thoughts into paragraphs for easier reading.
                - Use common Georgian idioms and phrases.

                ✅ Language Choices:
                - Use common, widely understood Georgian words.
                - Avoid academic jargon or complex terminology.
                - Use active voice instead of passive.
                - Avoid English loanwords unless necessary.

                ✅ Formatting:
                - No Emojis
                - Use proper punctuation to show tone.
                - No bullet points or numbering, or bold text, avoid such things.

                ✅ Tone:
                - Keep a friendly and relatable tone.
                - Reference Georgian places, events or trends.

                ✅ Message:
                - Get to the point quickly.
                - Have a clear takeaway in each section.
                - Make sure information is easy to understand at first glance.
                """,
            },
            "references": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "source_title": {
                            "type": "string",
                            "description": "title of the reference source, it will be a hostname or domain name",
                        },
                        "url": {
                            "type": "string",
                            "description": "Full URL of the reference source",
                        },
                        "key_quote": {
                            "type": "string",
                            "description": "Key quote from the source supporting the fact check, don't translate it.",
                        },
                        "is_supportive": {
                            "type": "boolean",
                            "description": "Whether the reference supports or refutes the statement",
                        },
                    },
                    "required": [
                        "source_title",
                        "url",
                        "key_quote",
                        "is_supportive",
                    ],
                },
                "description": "List of references supporting the fact check",
            },
        },
        "required": ["factuality", "result", "reason", "references"],
    },
}

# Research query schema for generating search queries
RESEARCH_QUERY_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "researchGoal": {"type": "string"},
        },
        "required": ["query", "researchGoal"],
    },
}

# System prompts for different operations
SYSTEM_PROMPT_TEMPLATE = """You are an expert researcher. Today is {today}. Follow these instructions when responding:
- You may be asked to research subjects that is after your knowledge cutoff, assume the user is right when presented with news.
- The user is a highly experienced analyst, no need to simplify it, be as detailed as possible and make sure your response is correct.
- Be highly organized.
- Suggest solutions that I didn't think about.
- Be proactive and anticipate my needs.
- Treat me as an expert in all subject matter.
- Mistakes erode my trust, so be accurate and thorough.
- Provide detailed explanations, I'm comfortable with lots of detail.
- Value good arguments over authorities, the source is irrelevant.
- Consider new technologies and contrarian ideas, not just the conventional wisdom.
- You may use high levels of speculation or prediction, just flag it for me."""
