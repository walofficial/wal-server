from typing import Optional

from pydantic import BaseModel


class VerificationResult(BaseModel):
    is_verification_success: bool
    rejection_description: Optional[str] = None
