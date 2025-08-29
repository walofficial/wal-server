from typing import Optional
from fastapi import APIRouter, HTTPException, Request, Path, Body
from typing_extensions import Annotated
from datetime import datetime
from ment_api.models.comment import (
    CreateReactionRequest,
    ReactionType,
    ReactionsSummary,
    CurrentUserReaction,
)
from ment_api.common.custom_object_id import CustomObjectId
from ment_api.persistence import mongo
from ment_api.services.notification_service import send_notification
from ment_api.models.notification import NotificationType
from pydantic import BaseModel

router = APIRouter(
    prefix="/comments",
    tags=["reactions"],
    responses={404: {"description": "Not found"}},
)


class ReactionResponse(BaseModel):
    success: bool
    reaction_type: Optional[ReactionType] = None
    updated_summary: ReactionsSummary


async def update_comment_reaction_summary(
    comment_id: CustomObjectId,
    old_reaction_type: Optional[ReactionType],
    new_reaction_type: Optional[ReactionType],
) -> None:
    """
    Update the reactions_summary field in the comment document.
    Handle incrementing new reaction type and decrementing old reaction type.
    """
    update_operations = {}

    # Decrement old reaction type if it exists
    if old_reaction_type:
        update_operations[f"reactions_summary.{old_reaction_type.value}.count"] = -1

    # Increment new reaction type if it exists
    if new_reaction_type:
        update_operations[f"reactions_summary.{new_reaction_type.value}.count"] = 1

    if update_operations:
        await mongo.comments.update_one(
            {"_id": comment_id}, {"$inc": update_operations}
        )


async def get_user_current_reaction(
    comment_id: CustomObjectId, user_id: str
) -> Optional[ReactionType]:
    """Get the current user's reaction for a specific comment."""
    reaction = await mongo.comment_reactions.find_one(
        {"comment_id": comment_id, "user_id": user_id}
    )
    return ReactionType(reaction["reaction_type"]) if reaction else None


async def get_comment_reactions_summary(comment_id: CustomObjectId) -> ReactionsSummary:
    """Get the current reactions summary for a comment from the database."""
    comment = await mongo.comments.find_one({"_id": comment_id})
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")

    # Return the reactions_summary or create a default one
    reactions_data = comment.get("reactions_summary", {})
    return ReactionsSummary(**reactions_data)


class AddOrUpdateReactionResponse(BaseModel):
    success: bool
    reaction_type: Optional[ReactionType] = None
    updated_summary: ReactionsSummary


@router.post("/{comment_id}/reactions")
async def add_or_update_reaction(
    request: Request,
    comment_id: Annotated[CustomObjectId, Path()],
    reaction_request: Annotated[CreateReactionRequest, Body()],
):
    """
    Add a new reaction or update an existing reaction for a comment.
    Implements upsert logic - if user already has a reaction, it updates it.
    """
    external_user_id = request.state.supabase_user_id

    # Verify comment exists
    comment = await mongo.comments.find_one({"_id": comment_id})
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")

    # Check for existing reaction
    existing_reaction = await mongo.comment_reactions.find_one(
        {"comment_id": comment_id, "user_id": external_user_id}
    )

    old_reaction_type = None
    if existing_reaction:
        old_reaction_type = ReactionType(existing_reaction["reaction_type"])

        # If it's the same reaction type, do nothing (or could implement toggle behavior)
        if old_reaction_type == reaction_request.reaction_type:
            updated_summary = await get_comment_reactions_summary(comment_id)
            return AddOrUpdateReactionResponse(
                success=True,
                reaction_type=reaction_request.reaction_type,
                updated_summary=updated_summary,
            )

    # Create or update reaction
    reaction_doc = {
        "comment_id": comment_id,
        "user_id": external_user_id,
        "reaction_type": reaction_request.reaction_type.value,
        "created_at": datetime.utcnow(),
    }

    if existing_reaction:
        # Update existing reaction
        await mongo.comment_reactions.update_one(
            {"_id": existing_reaction["_id"]},
            {
                "$set": {
                    "reaction_type": reaction_request.reaction_type.value,
                    "created_at": datetime.utcnow(),
                }
            },
        )
    else:
        # Insert new reaction
        await mongo.comment_reactions.insert_one(reaction_doc)

    # Update comment's reactions_summary
    await update_comment_reaction_summary(
        comment_id, old_reaction_type, reaction_request.reaction_type
    )

    # Send notification to comment author (if not self)
    if comment["author_id"] != external_user_id:
        user = await mongo.users.find_one({"external_user_id": external_user_id})
        username = user.get("username", "Someone") if user else "Someone"

        # Store notification in database
        notification_doc = {
            "from_user_id": external_user_id,
            "to_user_id": comment["author_id"],
            "type": NotificationType.COMMENT_REACTION.value,
            "created_at": datetime.utcnow(),
            "read": False,
            "verification_id": comment["verification_id"],
            "message": f"{username}  გამოხატა რეაქციას თქვენს კომენტარზე",
            "comment_id": comment_id,
            "reaction_type": reaction_request.reaction_type.value,
        }

        await mongo.notifications.insert_one(notification_doc)

        # Send push notification
        await send_notification(
            comment["author_id"],
            "ახალი რეაქცია",
            f"{username} გამოხატა რეაქცია თქვენს კომენტარზე",
            {
                "type": "comment_reaction",
                "commentId": str(comment_id),
                "verificationId": str(comment["verification_id"]),
                "reactionType": reaction_request.reaction_type.value,
            },
        )

    # Get updated summary
    updated_summary = await get_comment_reactions_summary(comment_id)

    return AddOrUpdateReactionResponse(
        success=True,
        reaction_type=reaction_request.reaction_type,
        updated_summary=updated_summary,
    )


class RemoveReactionResponse(BaseModel):
    success: bool
    reaction_type: Optional[ReactionType] = None
    updated_summary: ReactionsSummary


@router.delete(
    "/{comment_id}/reactions",
    operation_id="remove_reaction",
    response_model=RemoveReactionResponse,
)
async def remove_reaction(
    request: Request, comment_id: Annotated[CustomObjectId, Path()]
):
    """Remove the authenticated user's reaction from a comment."""
    external_user_id = request.state.supabase_user_id

    # Verify comment exists
    comment = await mongo.comments.find_one({"_id": comment_id})
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")

    # Find and remove the reaction
    existing_reaction = await mongo.comment_reactions.find_one(
        {"comment_id": comment_id, "user_id": external_user_id}
    )

    if not existing_reaction:
        # No reaction to remove - return success (idempotent)
        updated_summary = await get_comment_reactions_summary(comment_id)
        return RemoveReactionResponse(
            success=True, reaction_type=None, updated_summary=updated_summary
        )

    old_reaction_type = ReactionType(existing_reaction["reaction_type"])

    # Delete the reaction
    await mongo.comment_reactions.delete_one(
        {"comment_id": comment_id, "user_id": external_user_id}
    )

    # Update comment's reactions_summary (decrement the removed reaction)
    await update_comment_reaction_summary(comment_id, old_reaction_type, None)

    # Get updated summary
    updated_summary = await get_comment_reactions_summary(comment_id)

    return RemoveReactionResponse(
        success=True, reaction_type=None, updated_summary=updated_summary
    )


class GetCommentReactionsResponse(BaseModel):
    comment_id: CustomObjectId
    reactions_summary: ReactionsSummary
    current_user_reaction: Optional[CurrentUserReaction] = None


@router.get("/{comment_id}/reactions", response_model=GetCommentReactionsResponse)
async def get_comment_reactions(
    request: Request, comment_id: Annotated[CustomObjectId, Path()]
):
    """Get all reactions for a comment, including the current user's reaction."""
    external_user_id = request.state.supabase_user_id

    # Verify comment exists
    comment = await mongo.comments.find_one({"_id": comment_id})
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")

    # Get reactions summary
    reactions_summary = await get_comment_reactions_summary(comment_id)

    # Get current user's reaction if authenticated
    current_user_reaction = None
    if external_user_id:
        user_reaction_type = await get_user_current_reaction(
            comment_id, external_user_id
        )
        if user_reaction_type:
            current_user_reaction = CurrentUserReaction(type=user_reaction_type)

    return GetCommentReactionsResponse(
        comment_id=comment_id,
        reactions_summary=reactions_summary,
        current_user_reaction=current_user_reaction,
    )
