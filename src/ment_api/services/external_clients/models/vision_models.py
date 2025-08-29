from typing import List, Optional

from pydantic import BaseModel, Field


class TextExtractRequest(BaseModel):
    """Request model for extracting text from images using OCR from Google Cloud Storage."""

    image_urls: List[str] = Field(
        description="List of GCS image URLs to extract text from (gs:// or GCS HTTP URLs)"
    )
    max_concurrent: int = Field(
        default=5, description="Maximum number of concurrent OCR requests", ge=1, le=10
    )


class OCRResult(BaseModel):
    """Result model for text extraction from a single image."""

    image_url: str = Field(description="URL of the processed image")
    extracted_text: Optional[str] = Field(
        default=None, description="Text extracted from the image, None if no text found"
    )
    success: bool = Field(description="Whether text extraction was successful")
    error_message: Optional[str] = Field(
        default=None, description="Error message if extraction failed"
    )
    image_index: int = Field(description="Index of the image in the original request")

    @property
    def has_text(self) -> bool:
        """Check if the result contains extracted text."""
        return (
            self.success
            and self.extracted_text is not None
            and len(self.extracted_text.strip()) > 0
        )


class OCRResponse(BaseModel):
    """Response model for batch text extraction from multiple images."""

    results: List[OCRResult] = Field(description="List of OCR results for each image")
    total_images: int = Field(description="Total number of images processed")
    successful_extractions: int = Field(
        description="Number of images with successfully extracted text"
    )
    combined_text: Optional[str] = Field(
        default=None, description="All extracted text combined into a single string"
    )

    @classmethod
    def from_results(cls, results: List[OCRResult]) -> "OCRResponse":
        """Create OCRResponse from a list of OCRResult objects."""
        successful_results = [r for r in results if r.has_text]

        combined_text = None
        if successful_results:
            text_parts = []
            for result in successful_results:
                if result.extracted_text:
                    text_parts.append(
                        f"Image {result.image_index + 1}: {result.extracted_text}"
                    )

            if text_parts:
                combined_text = "\n\n".join(text_parts)

        return cls(
            results=results,
            total_images=len(results),
            successful_extractions=len(successful_results),
            combined_text=combined_text,
        )
