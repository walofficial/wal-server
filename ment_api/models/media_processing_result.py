from typing import List, Optional

from pydantic import BaseModel


class MediaProcessingResult(BaseModel):
    file_urls: List[str]
    verification_state: str
    transcode_job_name: Optional[str] = None
