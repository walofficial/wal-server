from typing import Optional

from pydantic import Field

from ment_api.services.external_clients.models.gemini_models import GeminiBaseModel


class FieldTranslations(GeminiBaseModel):
    """Translations for a single field across multiple languages"""

    en: Optional[str] = Field(default=None, description="English translation")
    ka: Optional[str] = Field(default=None, description="Georgian translation")
    es: Optional[str] = Field(default=None, description="Spanish translation")
    fr: Optional[str] = Field(default=None, description="French translation")
    de: Optional[str] = Field(default=None, description="German translation")


class TranslationResponse(GeminiBaseModel):
    """Complete translation response for all verification fields"""

    text_content: Optional[FieldTranslations] = None
    ai_video_summary: Optional[FieldTranslations] = None
    title: Optional[FieldTranslations] = None
    text_summary: Optional[FieldTranslations] = None
    government_summary: Optional[FieldTranslations] = None
    opposition_summary: Optional[FieldTranslations] = None
    neutral_summary: Optional[FieldTranslations] = None
    fact_check_reason: Optional[FieldTranslations] = None
    fact_check_reason_summary: Optional[FieldTranslations] = None
