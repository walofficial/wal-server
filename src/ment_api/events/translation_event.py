from pydantic import BaseModel


class TranslationEvent(BaseModel):
    """Event for translating verification attributes using Gemini API"""

    verification_id: str
