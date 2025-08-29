from pydantic import BaseModel, Field


class GenerateScoreResponse(BaseModel):
    score: float = Field(
        description="The numerical score between 0 and 100", ge=0, le=100
    )
    justification: str = Field(
        description="Your explanation for the importance score",
    )
    reasoning: str = Field(
        description="Your thought process and analysis of the factors",
    )
