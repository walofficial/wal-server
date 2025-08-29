from typing import Dict

LANGUAGE_NAME_TO_ISO: Dict[str, str] = {
    "georgian": "ka",
    "english": "en",
    "french": "fr",
    "ka": "ka",
    "en": "en",
    "fr": "fr",
}


def normalize_language_code(language: str) -> str:
    normalized = language.lower().strip()
    return LANGUAGE_NAME_TO_ISO.get(normalized, "ka")
