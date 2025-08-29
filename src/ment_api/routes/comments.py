from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Body, HTTPException, Path, Query, Request
from pydantic import BaseModel
from typing_extensions import Annotated

from ment_api.common.custom_object_id import CustomObjectId
from ment_api.models.comment import (
    Comment,
    CommentAIAnalysis,
    CommentResponse,
    CommentTag,
    CurrentUserReaction,
    ReactionType,
)
from ment_api.models.notification import NotificationType
from ment_api.persistence import mongo
from ment_api.services.notification_service import send_notification

router = APIRouter(
    prefix="/comments",
    tags=["comments"],
    responses={404: {"description": "Not found"}},
)


class CreateCommentRequest(BaseModel):
    content: str
    verification_id: CustomObjectId
    parent_comment_id: Optional[CustomObjectId] = None
    tags: List[CommentTag] = []


async def analyze_comment_with_ai(content: str) -> CommentAIAnalysis:
    try:
        # Parse the response and create CommentAIAnalysis
        # This is a simplified version - you might want to make this more robust
        analysis_dict = {
            "sentiment": "neutral",  # Default values
            "labels": [],
            "toxicity_score": 0.0,
            "summary": None,
            "generated_at": datetime.utcnow(),
        }
        return CommentAIAnalysis(**analysis_dict)
    except Exception as e:
        print(f"Error analyzing comment: {e}")
        return None


async def get_user_reactions_for_comments(
    comment_ids: List[CustomObjectId], user_id: str
) -> dict:
    """Get current user's reactions for a list of comments using MongoDB pipeline."""
    if not comment_ids or not user_id:
        return {}

    pipeline = [
        {"$match": {"comment_id": {"$in": comment_ids}, "user_id": user_id}},
        {
            "$group": {
                "_id": "$comment_id",
                "reaction_type": {"$first": "$reaction_type"},
            }
        },
    ]

    reactions = await mongo.comment_reactions.aggregate(pipeline)
    return {reaction["_id"]: reaction["reaction_type"] for reaction in reactions}


@router.post("")
async def create_comment(
    request: Request, comment_request: Annotated[CreateCommentRequest, Body()]
):
    external_user_id = request.state.supabase_user_id

    # Verify verification exists
    verification = await mongo.verifications.find_one(
        {"_id": comment_request.verification_id}
    )
    if not verification:
        raise HTTPException(status_code=404, detail="Verification not found")

    # Get user info for enrichment
    user = await mongo.users.find_one({"external_user_id": external_user_id})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Analyze comment with AI
    ai_analysis = await analyze_comment_with_ai(comment_request.content)

    # Create comment document with initialized reactions_summary
    comment_doc = {
        "verification_id": comment_request.verification_id,
        "author_id": external_user_id,
        "content": comment_request.content,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
        "likes_count": 0,
        "tags": [tag.dict() for tag in comment_request.tags],
        "parent_comment_id": comment_request.parent_comment_id,
        "ai_analysis": ai_analysis.dict() if ai_analysis else None,
        "score": 0.0,  # Initial score
        "reactions_summary": {
            "like": {"count": 0},
            "love": {"count": 0},
            "laugh": {"count": 0},
            "angry": {"count": 0},
            "sad": {"count": 0},
            "wow": {"count": 0},
        },
    }

    result = await mongo.comments.insert_one(comment_doc)
    comment_doc["_id"] = result.inserted_id

    # Send notifications for tagged users
    for tag in comment_request.tags:
        await send_notification(
            from_user_id=external_user_id,
            to_user_id=tag.user_id,
            type=NotificationType.COMMENT_TAG,
            verification_id=comment_request.verification_id,
            message=f"{user.get('username', 'Someone')} tagged you in a comment",
        )

    comment = Comment(**comment_doc)
    comment.author = user

    return {
        "ok": True,
    }


class GetVerificationCommentsResponse(BaseModel):
    comments: List[CommentResponse]


@router.get(
    "/verification/{verification_id}",
    response_model=GetVerificationCommentsResponse,
    operation_id="get_verification_comments",
)
async def get_verification_comments(
    request: Request,
    verification_id: Annotated[CustomObjectId, Path()],
    sort_by: str = Query("recent", enum=["recent", "top"]),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
):
    external_user_id = request.state.supabase_user_id
    skip = (page - 1) * limit

    # Base pipeline using MongoDB aggregation for better performance
    pipeline = [
        {"$match": {"verification_id": verification_id}},
        {
            "$lookup": {
                "from": "users",
                "localField": "author_id",
                "foreignField": "external_user_id",
                "as": "author",
            }
        },
        {"$unwind": "$author"},
    ]

    # Add sorting based on parameter
    if sort_by == "top":
        pipeline.append({"$sort": {"score": -1, "created_at": -1}})
    else:  # recent
        pipeline.append({"$sort": {"created_at": -1}})

    pipeline.extend(
        [
            {"$skip": skip},
            {"$limit": limit},
        ]
    )

    # Get comments using aggregation
    comments = await mongo.comments.aggregate(pipeline)
    comments_list = []
    comment_ids = []

    # Extract comment IDs for user reactions lookup
    for comment in comments:
        comment_ids.append(comment["_id"])

    # Get user's reactions for all comments in one query (if user is authenticated)
    user_reactions = {}
    if external_user_id:
        user_reactions = await get_user_reactions_for_comments(
            comment_ids, external_user_id
        )

        # Get user's likes for these comments (backward compatibility)
        likes_pipeline = [
            {
                "$match": {
                    "comment_id": {"$in": comment_ids},
                    "user_id": external_user_id,
                }
            }
        ]
        user_likes = await mongo.comment_likes.aggregate(likes_pipeline)
        liked_comment_ids = {like["comment_id"] for like in user_likes}
    else:
        liked_comment_ids = set()

    # Process comments and add reaction data
    for comment in comments:
        comment_id = comment["_id"]

        # Set current user reaction if exists
        current_user_reaction = None
        if comment_id in user_reactions:
            current_user_reaction = CurrentUserReaction(
                type=ReactionType(user_reactions[comment_id])
            )

        comment["current_user_reaction"] = current_user_reaction

        # Check if user liked this comment (backward compatibility)
        is_liked_by_user = comment_id in liked_comment_ids

        comment_obj = Comment(**comment)
        comments_list.append(
            CommentResponse(comment=comment_obj, is_liked_by_user=is_liked_by_user)
        )

    return GetVerificationCommentsResponse(comments=comments_list)


class LikeCommentResponse(BaseModel):
    status: str


@router.post(
    "/{comment_id}/like",
    response_model=LikeCommentResponse,
    operation_id="like_comment",
)
async def like_comment(request: Request, comment_id: Annotated[CustomObjectId, Path()]):
    external_user_id = request.state.supabase_user_id

    # Check if comment exists
    comment = await mongo.comments.find_one({"_id": comment_id})
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")

    # Check if already liked
    existing_like = await mongo.comment_likes.find_one(
        {"user_id": external_user_id, "comment_id": comment_id}
    )

    if existing_like:
        raise HTTPException(status_code=400, detail="Comment already liked")

    # Create like
    like_doc = {
        "user_id": external_user_id,
        "comment_id": comment_id,
        "created_at": datetime.utcnow(),
    }
    await mongo.comment_likes.insert_one(like_doc)

    # Update comment likes count and score
    await mongo.comments.update_one(
        {"_id": comment_id},
        {"$inc": {"likes_count": 1, "score": 1}},  # Increment score by 1 for each like
    )

    return {"status": "success"}


@router.delete(
    "/{comment_id}/like",
    response_model=LikeCommentResponse,
    operation_id="unlike_comment",
)
async def unlike_comment(
    request: Request, comment_id: Annotated[CustomObjectId, Path()]
):
    external_user_id = request.state.supabase_user_id

    # Check if comment exists
    comment = await mongo.comments.find_one({"_id": comment_id})
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")

    # Remove like
    result = await mongo.comment_likes.delete_one(
        {"user_id": external_user_id, "comment_id": comment_id}
    )

    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Like not found")

    # Update comment likes count and score
    await mongo.comments.update_one(
        {"_id": comment_id},
        {"$inc": {"likes_count": -1, "score": -1}},  # Decrement score by 1 when unliked
    )

    return {"status": "success"}


class GetVerificationCommentsCountResponse(BaseModel):
    count: int


@router.get(
    "/verification/{verification_id}/count",
    response_model=GetVerificationCommentsCountResponse,
    operation_id="get_verification_comments_count",
)
async def get_verification_comments_count(
    verification_id: Annotated[CustomObjectId, Path()],
):
    count = await mongo.comments.count_documents({"verification_id": verification_id})
    return GetVerificationCommentsCountResponse(count=count)


@router.delete("/{comment_id}", operation_id="delete_comment")
async def delete_comment_endpoint(
    request: Request, comment_id: Annotated[CustomObjectId, Path()]
):
    external_user_id = request.state.supabase_user_id

    # Check if comment exists and user is the author
    comment = await mongo.comments.find_one({"_id": comment_id})
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")

    if comment["author_id"] != external_user_id:
        raise HTTPException(
            status_code=403, detail="You can only delete your own comments"
        )

    # Delete the comment
    result = await mongo.comments.delete_one({"_id": comment_id})

    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Comment not found")

    # Also delete all reactions for this comment
    await mongo.comment_reactions.delete_all({"comment_id": comment_id})

    return {"status": "success"}
