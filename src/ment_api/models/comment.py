from typing import List, Optional
from datetime import datetime
from enum import Enum
from pydantic import BaseModel, Field
from ment_api.common.custom_object_id import CustomObjectId
from ment_api.models.user import User


class ReactionType(str, Enum):
    LIKE = "like"
    LOVE = "love"
    LAUGH = "laugh"
    ANGRY = "angry"
    SAD = "sad"
    WOW = "wow"
    DISLIKE = "dislike"


class ReactionCount(BaseModel):
    count: int = 0


class ReactionsSummary(BaseModel):
    like: ReactionCount = Field(default_factory=ReactionCount)
    love: ReactionCount = Field(default_factory=ReactionCount)
    laugh: ReactionCount = Field(default_factory=ReactionCount)
    angry: ReactionCount = Field(default_factory=ReactionCount)
    sad: ReactionCount = Field(default_factory=ReactionCount)
    wow: ReactionCount = Field(default_factory=ReactionCount)
    dislike: ReactionCount = Field(default_factory=ReactionCount)


class CurrentUserReaction(BaseModel):
    type: ReactionType


class CommentTag(BaseModel):
    user_id: str
    username: str
    start_index: int
    end_index: int


class CommentAIAnalysis(BaseModel):
    sentiment: str  # positive, negative, neutral
    labels: List[str]  # list of AI-generated labels
    toxicity_score: float
    summary: Optional[str] = None
    generated_at: datetime


class Comment(BaseModel):
    id: CustomObjectId = Field(alias="_id", serialization_alias="id")
    verification_id: CustomObjectId
    author_id: str
    content: str
    likes_count: int = 0  # Keep for backward compatibility
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    tags: List[CommentTag] = []
    parent_comment_id: Optional[CustomObjectId] = None  # For reply functionality
    ai_analysis: Optional[CommentAIAnalysis] = None
    score: float = 0.0  # Base score for ranking
    author: Optional[User] = None  # For response enrichment

    # New reaction fields
    reactions_summary: ReactionsSummary = Field(default_factory=ReactionsSummary)
    current_user_reaction: Optional[CurrentUserReaction] = None


class CommentResponse(BaseModel):
    comment: Comment
    is_liked_by_user: bool = False  # Keep for backward compatibility


class Reaction(BaseModel):
    id: CustomObjectId = Field(alias="_id", serialization_alias="id")
    comment_id: CustomObjectId
    user_id: str
    reaction_type: ReactionType
    created_at: datetime = Field(default_factory=datetime.utcnow)


class CreateReactionRequest(BaseModel):
    reaction_type: ReactionType
