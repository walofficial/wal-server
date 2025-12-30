"""
AI Character Models for the virtual character system.

These models define the data structures for AI characters, their memories,
location assets, and batch job tracking.
"""

from datetime import datetime
from enum import StrEnum
from typing import List, Optional

from bson import ObjectId
from pydantic import BaseModel, Field

from ment_api.common.custom_object_id import CustomObjectId


class AICharacterStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    SUSPENDED = "suspended"


class BatchJobStatus(StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class MemoryRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


# ============================================================================
# AI Character Model
# ============================================================================


class AICharacter(BaseModel):
    """AI Character profile and configuration."""

    id: CustomObjectId = Field(alias="_id", serialization_alias="id")
    user_id: str  # Links to users.external_user_id

    # Identity
    name: str

    # Personality
    personality: str  # Brief personality description
    post_instructions: str  # System prompt for post generation
    chat_personality: str  # System prompt for chat responses

    # Visual Assets (Cloud Storage URLs)
    face_images: List[str] = []
    body_reference_images: List[str] = []

    # Location Assignments
    allowed_feed_ids: List[CustomObjectId] = []

    # Schedule (GMT+4 Tbilisi)
    timezone: str = "Asia/Tbilisi"
    active_hours: List[int] = [9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21]

    # Posting Limits
    max_posts_per_day: int = 3
    posts_today: int = 0
    last_post_reset: Optional[datetime] = None

    # Chat Settings
    chat_enabled: bool = True

    # Status
    is_active: bool = True
    created_at: datetime

    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True


class CreateAICharacterRequest(BaseModel):
    """Request model for creating an AI character."""

    name: str
    personality: str
    post_instructions: str
    chat_personality: str
    allowed_feed_ids: List[str]  # Feed IDs as strings
    active_hours: List[int] = [9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21]
    max_posts_per_day: int = 3
    chat_enabled: bool = True


class AICharacterResponse(BaseModel):
    """Response model for AI character creation."""

    character_id: str
    user_id: str
    name: str


class AICharacterDetail(BaseModel):
    """Detailed AI character response for GET endpoints."""

    id: str
    user_id: str
    name: str
    personality: str
    post_instructions: str
    chat_personality: str
    face_images: List[str] = []
    body_reference_images: List[str] = []
    allowed_feed_ids: List[str] = []
    timezone: str = "Asia/Tbilisi"
    active_hours: List[int] = []
    max_posts_per_day: int = 3
    posts_today: int = 0
    last_post_reset: Optional[datetime] = None
    chat_enabled: bool = True
    is_active: bool = True
    created_at: Optional[datetime] = None

    @classmethod
    def from_mongo(cls, doc: dict) -> "AICharacterDetail":
        """Convert MongoDB document to AICharacterDetail."""
        return cls(
            id=str(doc["_id"]),
            user_id=doc.get("user_id", ""),
            name=doc.get("name", ""),
            personality=doc.get("personality", ""),
            post_instructions=doc.get("post_instructions", ""),
            chat_personality=doc.get("chat_personality", ""),
            face_images=doc.get("face_images", []),
            body_reference_images=doc.get("body_reference_images", []),
            allowed_feed_ids=[str(fid) for fid in doc.get("allowed_feed_ids", [])],
            timezone=doc.get("timezone", "Asia/Tbilisi"),
            active_hours=doc.get("active_hours", []),
            max_posts_per_day=doc.get("max_posts_per_day", 3),
            posts_today=doc.get("posts_today", 0),
            last_post_reset=doc.get("last_post_reset"),
            chat_enabled=doc.get("chat_enabled", True),
            is_active=doc.get("is_active", True),
            created_at=doc.get("created_at"),
        )


class AICharacterListResponse(BaseModel):
    """Response for listing AI characters."""

    characters: List[AICharacterDetail]
    total: int


# ============================================================================
# AI Character Memory Model (for RAG)
# ============================================================================


class MemoryMetadata(BaseModel):
    """Metadata for a memory entry."""

    room_id: Optional[CustomObjectId] = None
    timestamp: datetime
    topic: Optional[str] = None


class AICharacterMemory(BaseModel):
    """Memory entry for AI character RAG system."""

    id: CustomObjectId = Field(alias="_id", serialization_alias="id")
    character_id: CustomObjectId  # Reference to ai_characters collection
    user_id: Optional[str] = None  # None for global memories
    is_global: bool = False  # True = shared across all users
    content: str  # Original message text
    role: str  # "user" or "assistant"
    embedding: List[float]  # 768-dim Gemini embedding
    metadata: MemoryMetadata
    created_at: datetime

    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True


class AddMemoryRequest(BaseModel):
    """Request model for manually adding a memory."""

    content: str
    is_global: bool = False
    topic: Optional[str] = None


class MemoryDetail(BaseModel):
    """Detail response for a single memory."""

    id: str
    character_id: str
    user_id: Optional[str] = None
    is_global: bool = False
    content: str
    role: str
    metadata: Optional[dict] = None
    created_at: Optional[datetime] = None
    score: Optional[float] = None  # Vector search score

    @classmethod
    def from_mongo(cls, doc: dict) -> "MemoryDetail":
        """Convert MongoDB document to MemoryDetail."""
        metadata = doc.get("metadata", {})
        if metadata and metadata.get("room_id"):
            metadata["room_id"] = str(metadata["room_id"])

        return cls(
            id=str(doc["_id"]),
            character_id=str(doc.get("character_id", "")),
            user_id=doc.get("user_id"),
            is_global=doc.get("is_global", False),
            content=doc.get("content", ""),
            role=doc.get("role", ""),
            metadata=metadata,
            created_at=doc.get("created_at"),
            score=doc.get("score"),
        )


class MemoryListResponse(BaseModel):
    """Response model for listing memories."""

    memories: List[MemoryDetail]
    total: int


class MemoryCountResponse(BaseModel):
    """Response for memory statistics."""

    total: int
    global_count: int
    user_count: int


# ============================================================================
# Location Assets Model
# ============================================================================


class ImageWithDims(BaseModel):
    """Image with dimensions metadata."""

    url: str
    width: Optional[int] = None
    height: Optional[int] = None
    blur_hash: Optional[str] = None


class LocationAsset(BaseModel):
    """Location reference images and prompts for AI generation."""

    id: CustomObjectId = Field(alias="_id", serialization_alias="id")
    feed_id: CustomObjectId
    feed_name: str
    images: List[ImageWithDims] = []
    description: str  # For text generation context
    created_at: datetime
    updated_at: datetime

    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True


class CreateLocationAssetRequest(BaseModel):
    """Request model for creating/updating location assets."""

    feed_id: str
    feed_name: str
    description: str


class LocationAssetDetail(BaseModel):
    """Detail response for location asset."""

    id: str
    feed_id: str
    feed_name: str
    images: List[ImageWithDims] = []
    description: str
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @classmethod
    def from_mongo(cls, doc: dict) -> "LocationAssetDetail":
        """Convert MongoDB document to LocationAssetDetail."""
        images = []
        for img in doc.get("images", []):
            images.append(
                ImageWithDims(
                    url=img.get("url", ""),
                    width=img.get("width"),
                    height=img.get("height"),
                    blur_hash=img.get("blur_hash"),
                )
            )

        return cls(
            id=str(doc["_id"]),
            feed_id=str(doc.get("feed_id", "")),
            feed_name=doc.get("feed_name", ""),
            images=images,
            description=doc.get("description", ""),
            created_at=doc.get("created_at"),
            updated_at=doc.get("updated_at"),
        )


class LocationAssetListResponse(BaseModel):
    """Response for listing location assets."""

    assets: List[LocationAssetDetail]
    total: int


class LocationAssetUploadResponse(BaseModel):
    """Response for location asset upload."""

    status: str
    asset_id: str
    images_count: int


class LocationAssetDeleteResponse(BaseModel):
    """Response for location asset deletion."""

    status: str
    feed_id: str


class LocationAssetAddImagesResponse(BaseModel):
    """Response for adding images to location asset."""

    status: str
    new_count: int
    total_count: int


# ============================================================================
# AI Batch Jobs Model
# ============================================================================


class BatchPostMetadata(BaseModel):
    """Metadata for a single post in a batch job."""

    character_id: str
    character_user_id: str
    character_name: str
    feed_id: str
    text_content: str
    scheduled_delay: int  # Random delay in seconds (0-3600)


class AIBatchJob(BaseModel):
    """Tracking for Gemini Batch API jobs."""

    id: CustomObjectId = Field(alias="_id", serialization_alias="id")
    batch_job_name: str  # Gemini Batch API job name
    status: str = BatchJobStatus.PENDING
    posts: List[BatchPostMetadata] = []
    created_at: datetime
    completed_at: Optional[datetime] = None

    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True


# ============================================================================
# Chat Integration Models
# ============================================================================


class AIChatRequest(BaseModel):
    """Request model for chatting with an AI character."""

    message: str
    room_id: Optional[str] = None


class AIChatResponse(BaseModel):
    """Response model from AI character chat."""

    response: str
    character_id: str
    character_name: str


# ============================================================================
# Batch Complete Webhook Models
# ============================================================================


class BatchCompleteRequest(BaseModel):
    """Request model for batch completion webhook."""

    batch_job_name: str
    results: List[dict]  # List of generated image results


class ExecutePostRequest(BaseModel):
    """Request model for executing a scheduled post."""

    character_user_id: str
    feed_id: str
    text_content: str
    image_url: str
    image_dims: Optional[dict] = None


# ============================================================================
# Additional Response Models
# ============================================================================


class AddMemoryResponse(BaseModel):
    """Response for adding a memory."""

    memory_id: str
    status: str


class BatchCompleteResponse(BaseModel):
    """Response for batch completion."""

    status: str
    posts_scheduled: Optional[int] = None


class ExecutePostResponse(BaseModel):
    """Response for post execution."""

    status: str
    verification_id: str


class PollBatchJobsResponse(BaseModel):
    """Response for batch job polling."""

    status: str
    jobs_checked: Optional[int] = None
    processed: Optional[int] = None
    failed: Optional[int] = None


class TriggerPostsResponse(BaseModel):
    """Response for triggering post generation."""

    status: str
    batch_job_name: Optional[str] = None
    posts_count: Optional[int] = None
    hour: Optional[int] = None

