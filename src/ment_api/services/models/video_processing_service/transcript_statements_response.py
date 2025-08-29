from typing import List

from pydantic import BaseModel, Field


class TranscriptStatementsResponse(BaseModel):
    verifiable_claims: List[str] = Field(
        description="List of verifiable claims extracted from the transcript."
    )
