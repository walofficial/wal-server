# Response schema for video summary
VIDEO_SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {
            "type": "string",
            "description": "A concise title that captures the main topic of the video",
        },
        "relevant_statements": {
            "type": "array",
            "description": "1-15 statements mentioned in the audio that are relevant to the current Georgian political environment",
            "items": {
                "type": "object",
                "properties": {
                    "timestamp": {
                        "type": "string",
                        "description": "Timestamp in MM:SS format",
                    },
                    "text": {
                        "type": "string",
                        "description": "Description of the key moment",
                    },
                },
                "required": ["timestamp", "text"],
            },
        },
        "interesting_facts": {
            "type": "array",
            "description": "1-3 interesting facts about the video/video",
            "items": {"type": "string"},
        },
        "did_you_know": {
            "type": "array",
            "description": "1-3 interesting facts about the video",
            "items": {"type": "string"},
        },
        "short_summary": {
            "type": "string",
            "description": "A short summary of the video in Georgian language, no more than 5 sentences",
        },
    },
    "required": [
        "title",
        "relevant_statements",
        "interesting_facts",
        "did_you_know",
        "short_summary",
    ],
}

# Prompts for video processing
TRANSCRIPT_GENERATION_PROMPT = """
Generate a complete transcript of this video. Follow these instructions:
1. If the video is in Georgian language, provide the transcript in Georgian.
2. If the video is in English or any other language, provide the transcript in English.
3. Include as much detail as possible, capturing all spoken content and important visual elements.
4. Include approximate timestamps in MM:SS format. 
5. NEVER generate word based timestamps. Do only sentence and normal dialogue based timestamps.

Generate entire transcript!

"""

COMMON_VIDEO_SUMMARY_PROMPT = """
You are an expert in the Georgian language and generating summaries from audio transcripts so that people don't have to watch the whole video or listen to the audio or video. 

Before you generate the summaries should you use the grounding tool to get the current events and political landscape in Georgia.

"""

TRANSLATION_STYLE_RULES = """
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
"""

CUSTOM_INSTRUCTIONS_FOR_VIDEO_SUMMARY = """
Also keep in mind following instructions and think step by step:

1. There shouldn't be any links in the summary, relevant statemnts  or in the title.
2. Make the results more interesting by referencing current events in Georgia or political landscape if only appropriate.
3. Make sure to not omit any important statement or relevant information frmo trhe transcript 
4. Make sure to include solid "interesting facts"  in your foundation knowledge which might be interesting for the reader and relevasnt to the conversation or transcript. It should be short and concise easy to understand.
5. Also did you know" section would be nice to have like some facts which might be useful for the reader.
6. Write the sentences and even facts and stuff in friendly Georgian note

"""

VIDEO_SUMMARY_PROMPT = (
    """
    Transcript:
{transcript}

Based on the provided English or Georgian transcript, create an engaging, clear, and comprehensive summary entirely in Georgian language, designed specifically for Georgian viewers. Follow this exact structure:

სათაური (Title)
A concise, engaging Georgian title that immediately captures attention and reflects the video's core topic clearly.

მთავარი მომენტები (Relevant Statements)
List 3-6 key or most interesting statements, each with timestamps in (MM:SS) format and accompanying YouTube links directly to those timestamps for easy navigation. This can be statements mentioned or the facts by the speakers relevant to the current events

მოკლე შინაარსი (Short Summary)
Provide a concise summary in Georgian (maximum 6 sentences). Feel free to add a touch of humor if appropriate to make it more entertaining. Translate key information from English to Georgian.

ინტერესონალური ფაქტები (Interesting Facts)
1-3 რელევანტური საინტერო ფაქტი

იცოდი ?  (Did you know?)
1-4 რელევანტური did you know ფაქტი

Statements (Statements)
1-10 statements from the transcript that can be and useful to be fact checked. This should be in English, if Georgian transcript is provided translate those statements in English

Your summary should focus on accuracy, readability, and entertainment value, ensuring the Georgian-speaking audience easily understands the key messages and finds the content relatable and interesting. Ensure all text content is entirely in Georgian, with only timestamps in MM:SS format. Translate all content from English to Georgian.


Current date: {today}

Search for current events in the country of Georgia from the past months month before you start generating the summary.
"""
    + TRANSLATION_STYLE_RULES
    + CUSTOM_INSTRUCTIONS_FOR_VIDEO_SUMMARY
)
