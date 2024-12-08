from openai import AsyncOpenAI
from ment_api.config import settings
import os

aclient = AsyncOpenAI(api_key=settings.openai_api_key)


async def get_completion_from_messages(messages, model="gpt-4o", temperature=0.7):
    response = await aclient.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
    )
    return response.choices[0].message.content
