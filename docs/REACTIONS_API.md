# Comment Reactions API Documentation

## Overview

This document describes the comment reactions functionality that allows users to react to comments with different types of reactions (like, love, laugh, angry, sad, wow). The implementation follows the research-based architecture for optimal performance and scalability.

## Features

- **Multiple Reaction Types**: 6 types of reactions (like, love, laugh, angry, sad, wow)
- **One Reaction Per User**: Each user can have only one reaction per comment
- **Optimized Reads**: Denormalized reaction counts in comments for fast display
- **Real-time Updates**: Immediate UI feedback with optimistic updates (client-side)
- **Notifications**: Users get notified when someone reacts to their comments
- **MongoDB Aggregation**: Efficient data fetching using MongoDB pipelines

## Architecture

### Collections

#### 1. Comments Collection (Updated)

```json
{
  "_id": "ObjectId",
  "verification_id": "ObjectId",
  "author_id": "string",
  "content": "string",
  "created_at": "Date",
  "updated_at": "Date",
  "reactions_summary": {
    "like": { "count": 0 },
    "love": { "count": 0 },
    "laugh": { "count": 0 },
    "angry": { "count": 0 },
    "sad": { "count": 0 },
    "wow": { "count": 0 }
  }
}
```

#### 2. Comment Reactions Collection (New)

```json
{
  "_id": "ObjectId",
  "comment_id": "ObjectId",
  "user_id": "string",
  "reaction_type": "string", // "like", "love", "laugh", "angry", "sad", "wow"
  "created_at": "Date"
}
```

### Indexes

The following indexes are automatically created for optimal performance:

```javascript
// comment_reactions collection
{ "comment_id": 1, "user_id": 1 } // unique - one reaction per user per comment
{ "comment_id": 1, "reaction_type": 1 } // for aggregating reactions by type
{ "user_id": 1 } // for user's reaction history
{ "created_at": -1 } // for recent reactions
```

## API Endpoints

### 1. Add or Update Reaction

**POST** `/comments/{comment_id}/reactions`

Add a new reaction or update an existing reaction for a comment.

**Request Body:**

```json
{
  "reaction_type": "love"
}
```

**Response (200/201):**

```json
{
  "success": true,
  "reaction_type": "love",
  "updated_summary": {
    "like": { "count": 5 },
    "love": { "count": 3 },
    "laugh": { "count": 1 },
    "angry": { "count": 0 },
    "sad": { "count": 0 },
    "wow": { "count": 0 }
  }
}
```

### 2. Remove Reaction

**DELETE** `/comments/{comment_id}/reactions`

Remove the authenticated user's reaction from a comment.

**Response (200):**

```json
{
  "success": true,
  "reaction_type": null,
  "updated_summary": {
    "like": { "count": 5 },
    "love": { "count": 2 },
    "laugh": { "count": 1 },
    "angry": { "count": 0 },
    "sad": { "count": 0 },
    "wow": { "count": 0 }
  }
}
```

### 3. Get Comment Reactions

**GET** `/comments/{comment_id}/reactions`

Get all reactions for a comment, including the current user's reaction.

**Response (200):**

```json
{
  "comment_id": "648f1234567890abcdef1234",
  "reactions_summary": {
    "like": { "count": 5 },
    "love": { "count": 2 },
    "laugh": { "count": 1 },
    "angry": { "count": 0 },
    "sad": { "count": 0 },
    "wow": { "count": 0 }
  },
  "current_user_reaction": {
    "type": "love"
  }
}
```

### 4. Updated Comments Endpoint

**GET** `/comments/verification/{verification_id}`

The existing comments endpoint now includes reaction data:

**Response (200):**

```json
[
  {
    "comment": {
      "id": "648f1234567890abcdef1234",
      "verification_id": "648f1234567890abcdef5678",
      "author_id": "user123",
      "content": "Great post!",
      "created_at": "2024-01-01T10:00:00Z",
      "reactions_summary": {
        "like": { "count": 5 },
        "love": { "count": 2 },
        "laugh": { "count": 1 },
        "angry": { "count": 0 },
        "sad": { "count": 0 },
        "wow": { "count": 0 }
      },
      "current_user_reaction": {
        "type": "love"
      },
      "author": {
        "username": "john_doe",
        "external_user_id": "user123"
      }
    },
    "is_liked_by_user": false
  }
]
```

## React Native Client Implementation (TanStack Query)

### 1. Types and Models

```typescript
enum ReactionType {
  LIKE = "like",
  LOVE = "love",
  LAUGH = "laugh",
  ANGRY = "angry",
  SAD = "sad",
  WOW = "wow",
}

interface ReactionCount {
  count: number;
}

interface ReactionsSummary {
  like: ReactionCount;
  love: ReactionCount;
  laugh: ReactionCount;
  angry: ReactionCount;
  sad: ReactionCount;
  wow: ReactionCount;
}

interface CurrentUserReaction {
  type: ReactionType;
}

interface Comment {
  id: string;
  verification_id: string;
  author_id: string;
  content: string;
  created_at: string;
  reactions_summary: ReactionsSummary;
  current_user_reaction?: CurrentUserReaction;
  author: User;
}
```

### 2. API Functions

```typescript
// api/reactions.ts
const API_BASE_URL = "YOUR_API_URL";

export const addReactionToComment = async ({
  commentId,
  reactionType,
}: {
  commentId: string;
  reactionType: ReactionType;
}) => {
  const response = await fetch(
    `${API_BASE_URL}/comments/${commentId}/reactions`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`, // Your auth token
      },
      body: JSON.stringify({ reaction_type: reactionType }),
    }
  );

  if (!response.ok) throw new Error("Failed to add reaction");
  return response.json();
};

export const removeReactionFromComment = async ({
  commentId,
}: {
  commentId: string;
}) => {
  const response = await fetch(
    `${API_BASE_URL}/comments/${commentId}/reactions`,
    {
      method: "DELETE",
      headers: {
        Authorization: `Bearer ${token}`,
      },
    }
  );

  if (!response.ok) throw new Error("Failed to remove reaction");
  return response.json();
};

export const fetchCommentsByVerificationId = async (verificationId: string) => {
  const response = await fetch(
    `${API_BASE_URL}/comments/verification/${verificationId}`,
    {
      headers: {
        Authorization: `Bearer ${token}`,
      },
    }
  );

  if (!response.ok) throw new Error("Failed to fetch comments");
  return response.json();
};
```

### 3. TanStack Query Hooks

```typescript
// hooks/useReactions.ts
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

export const useComments = (verificationId: string) => {
  return useQuery({
    queryKey: ["comments", verificationId],
    queryFn: () => fetchCommentsByVerificationId(verificationId),
  });
};

export const useAddReaction = (verificationId: string) => {
  const queryClient = useQueryClient();
  const commentsQueryKey = ["comments", verificationId];

  return useMutation({
    mutationFn: addReactionToComment,
    onMutate: async (variables) => {
      // Cancel outgoing refetches
      await queryClient.cancelQueries({ queryKey: commentsQueryKey });

      // Snapshot the previous value
      const previousComments = queryClient.getQueryData(commentsQueryKey);

      // Optimistically update
      queryClient.setQueryData(commentsQueryKey, (oldData: any) => {
        if (!oldData) return oldData;

        return oldData.map((commentResponse: any) => {
          if (commentResponse.comment.id === variables.commentId) {
            const comment = commentResponse.comment;
            const oldReactionType = comment.current_user_reaction?.type;
            const newReactionType = variables.reactionType;

            // Update reactions summary
            const newReactionsSummary = { ...comment.reactions_summary };

            // Decrement old reaction
            if (oldReactionType && newReactionsSummary[oldReactionType]) {
              newReactionsSummary[oldReactionType] = {
                count: Math.max(
                  0,
                  newReactionsSummary[oldReactionType].count - 1
                ),
              };
            }

            // Increment new reaction
            if (newReactionsSummary[newReactionType]) {
              newReactionsSummary[newReactionType] = {
                count: newReactionsSummary[newReactionType].count + 1,
              };
            }

            return {
              ...commentResponse,
              comment: {
                ...comment,
                reactions_summary: newReactionsSummary,
                current_user_reaction: { type: newReactionType },
              },
            };
          }
          return commentResponse;
        });
      });

      return { previousComments };
    },
    onError: (error, variables, context) => {
      // Rollback on error
      if (context?.previousComments) {
        queryClient.setQueryData(commentsQueryKey, context.previousComments);
      }
    },
    onSettled: () => {
      // Refetch to ensure consistency
      queryClient.invalidateQueries({ queryKey: commentsQueryKey });
    },
  });
};

export const useRemoveReaction = (verificationId: string) => {
  const queryClient = useQueryClient();
  const commentsQueryKey = ["comments", verificationId];

  return useMutation({
    mutationFn: removeReactionFromComment,
    onMutate: async (variables) => {
      await queryClient.cancelQueries({ queryKey: commentsQueryKey });
      const previousComments = queryClient.getQueryData(commentsQueryKey);

      queryClient.setQueryData(commentsQueryKey, (oldData: any) => {
        if (!oldData) return oldData;

        return oldData.map((commentResponse: any) => {
          if (commentResponse.comment.id === variables.commentId) {
            const comment = commentResponse.comment;
            const oldReactionType = comment.current_user_reaction?.type;

            // Update reactions summary
            const newReactionsSummary = { ...comment.reactions_summary };

            // Decrement the removed reaction
            if (oldReactionType && newReactionsSummary[oldReactionType]) {
              newReactionsSummary[oldReactionType] = {
                count: Math.max(
                  0,
                  newReactionsSummary[oldReactionType].count - 1
                ),
              };
            }

            return {
              ...commentResponse,
              comment: {
                ...comment,
                reactions_summary: newReactionsSummary,
                current_user_reaction: null,
              },
            };
          }
          return commentResponse;
        });
      });

      return { previousComments };
    },
    onError: (error, variables, context) => {
      if (context?.previousComments) {
        queryClient.setQueryData(commentsQueryKey, context.previousComments);
      }
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: commentsQueryKey });
    },
  });
};
```

### 4. React Component Example

```typescript
// components/ReactionButton.tsx
import React from "react";
import { TouchableOpacity, Text, View } from "react-native";
import { useAddReaction, useRemoveReaction } from "../hooks/useReactions";

interface ReactionButtonProps {
  comment: Comment;
  reactionType: ReactionType;
  verificationId: string;
}

const ReactionButton: React.FC<ReactionButtonProps> = ({
  comment,
  reactionType,
  verificationId,
}) => {
  const addReactionMutation = useAddReaction(verificationId);
  const removeReactionMutation = useRemoveReaction(verificationId);

  const isSelected = comment.current_user_reaction?.type === reactionType;
  const count = comment.reactions_summary[reactionType]?.count || 0;

  const handlePress = () => {
    if (isSelected) {
      // Remove reaction
      removeReactionMutation.mutate({ commentId: comment.id });
    } else {
      // Add/change reaction
      addReactionMutation.mutate({
        commentId: comment.id,
        reactionType,
      });
    }
  };

  const isLoading =
    addReactionMutation.isPending || removeReactionMutation.isPending;

  return (
    <TouchableOpacity
      onPress={handlePress}
      disabled={isLoading}
      style={{
        flexDirection: "row",
        alignItems: "center",
        padding: 8,
        borderRadius: 16,
        backgroundColor: isSelected ? "#007AFF" : "#F0F0F0",
      }}
    >
      <Text style={{ fontSize: 16 }}>{getReactionEmoji(reactionType)}</Text>
      {count > 0 && (
        <Text
          style={{
            marginLeft: 4,
            color: isSelected ? "white" : "black",
          }}
        >
          {count}
        </Text>
      )}
    </TouchableOpacity>
  );
};

const getReactionEmoji = (reactionType: ReactionType): string => {
  const emojiMap = {
    [ReactionType.LIKE]: "👍",
    [ReactionType.LOVE]: "❤️",
    [ReactionType.LAUGH]: "😂",
    [ReactionType.ANGRY]: "😠",
    [ReactionType.SAD]: "😢",
    [ReactionType.WOW]: "😮",
  };
  return emojiMap[reactionType];
};
```

## Database Migration

After deploying the reactions functionality, run the migration script to add the `reactions_summary` field to existing comments:

```bash
python src/ment_api/migrations/add_reactions_summary_to_comments.py
```

## Performance Considerations

### For 20+ Concurrent Users

1. **Efficient Indexing**: All necessary indexes are created automatically
2. **Denormalized Counts**: Reaction counts are stored directly in comments for fast reads
3. **MongoDB Aggregation**: User reactions are fetched efficiently using pipelines
4. **Optimistic Updates**: Client-side optimistic updates provide immediate feedback
5. **Atomic Operations**: MongoDB `$inc` operations ensure data consistency

### Scaling Further

If you need to scale beyond 20 concurrent users, consider:

1. **Redis Caching**: Cache frequently accessed comment reaction data
2. **Connection Pooling**: Ensure MongoDB connection pooling is optimized
3. **Read Replicas**: Use MongoDB read replicas for read operations
4. **Horizontal Scaling**: Scale your API servers horizontally

## Error Handling

The API handles various error cases:

- **404**: Comment not found
- **400**: Invalid reaction type
- **401**: Unauthorized user
- **500**: Internal server error

All endpoints return consistent error responses:

```json
{
  "detail": "Error message"
}
```

## Notifications

When a user reacts to a comment, the comment author receives:

1. **Database Notification**: Stored in the notifications collection
2. **Push Notification**: Real-time push notification to their device

Notification payload includes:

- Reaction type
- Comment ID
- Verification ID
- User who reacted

## Testing

Test the reactions functionality by:

1. Creating comments on a verification
2. Adding different types of reactions
3. Changing reaction types
4. Removing reactions
5. Verifying counts update correctly
6. Testing with multiple users

## Conclusion

This reactions system provides a robust, scalable solution for comment reactions that follows best practices for both backend performance and frontend user experience. The denormalized approach optimizes for read performance while maintaining data consistency through careful write operations.
