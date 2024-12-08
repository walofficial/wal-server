from typing import List

from pydantic import BaseModel


class GenerateInterestsRequest(BaseModel):
    chosen_interests: List[str]
