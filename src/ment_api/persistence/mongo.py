import logging
from typing import Any, Dict, List

from bson import ObjectId
from pymongo import ASCENDING, DESCENDING, IndexModel, UpdateOne

from ment_api.persistence import mongo_client

logger = logging.getLogger(__name__)


def create_translation_projection(
    field_names: List[str], language: str = "en"
) -> Dict[str, Any]:
    projection = {}
    fallback_language = "ka" if language == "en" else "en"

    for field_name in field_names:
        projection[field_name] = {
            "$ifNull": [
                f"${field_name}.{language}",
                {
                    "$ifNull": [
                        f"${field_name}.{fallback_language}",
                        f"${field_name}",
                    ]
                },
            ]
        }

    return projection


class BaseMongo:
    def __init__(self, collection):
        self.collection = collection

    async def aggregate(self, pipeline):
        cursor = await mongo_client.db[self.collection].aggregate(pipeline)
        return await cursor.to_list(length=None)

    async def find_one(self, query, sort=None, session=None):
        return await mongo_client.db[self.collection].find_one(
            query, sort=sort, session=session
        )

    async def find_one_by_id(self, obj_id: ObjectId, session=None):
        return await mongo_client.db[self.collection].find_one(
            {"_id": obj_id}, session=session
        )

    async def insert_many(self, documents, ordered=True, session=None):
        return await mongo_client.db[self.collection].insert_many(
            documents, ordered=ordered, session=session
        )

    async def delete_one(self, query, session=None):
        return await mongo_client.db[self.collection].delete_one(query, session=session)

    async def insert_one(self, document, session=None):
        return await mongo_client.db[self.collection].insert_one(
            document, session=session
        )

    async def delete_all(self, filter, session=None):
        return await mongo_client.db[self.collection].delete_many(
            filter, session=session
        )

    async def update_one(self, query, new_state, upsert=False, session=None):
        return await mongo_client.db[self.collection].update_one(
            query, new_state, upsert=upsert, session=session
        )

    async def update_many(self, query, new_state, upsert=False, session=None):
        return await mongo_client.db[self.collection].update_many(
            query, new_state, upsert=upsert, session=session
        )

    async def find_all(self, query, sort=None):
        cursor = mongo_client.db[self.collection].find(query, sort=sort)
        return await cursor.to_list(length=None)

    async def bulk_update(self, operations: list[UpdateOne]):
        return await mongo_client.db[self.collection].bulk_write(operations)

    async def count_documents(self, filter):
        return await mongo_client.db[self.collection].count_documents(filter)

    async def find_one_and_delete(self, query, session=None):
        return await mongo_client.db[self.collection].find_one_and_delete(
            query, session=session
        )


users = BaseMongo("users")
likes = BaseMongo("likes")
news = BaseMongo("news")
feeds = BaseMongo("feeds")
gcloud_tasks = BaseMongo("gcloud_tasks")
verifications = BaseMongo("verifications")
push_notification_tokens = BaseMongo("push-notification-tokens")
chat_messages = BaseMongo("chat_messages")
notifications = BaseMongo("notifications")
friend_requests = BaseMongo("friend_requests")
friendships = BaseMongo("friendships")
chat_rooms = BaseMongo("chat_rooms")
feed_location_mappings = BaseMongo("feed_location_mappings")
live_users = BaseMongo("live_users")
subscribed_space_users = BaseMongo("subscribed_space_users")
comments = BaseMongo("comments")
comment_likes = BaseMongo("comment_likes")
comment_reactions = BaseMongo("comment_reactions")
fact_checks = BaseMongo("fact_checks")
fact_check_ratings = BaseMongo("fact_check_ratings")
external_articles = BaseMongo("external_articles")


async def initialize_db() -> None:
    logger.info(f"Initializing database: {mongo_client.db}")
    await mongo_client.db["feed_location_mappings"].create_index(
        [("feed_ids", DESCENDING)], unique=True
    )
    await mongo_client.db["notifications"].create_indexes(
        [
            IndexModel([("to_user_id", ASCENDING), ("created_at", DESCENDING)]),
            IndexModel(
                [
                    ("from_user_id", ASCENDING),
                    ("to_user_id", ASCENDING),
                    ("type", ASCENDING),
                    ("feed_id", ASCENDING),
                    ("created_at", ASCENDING),
                ]
            ),
        ]
    )
    await mongo_client.db["likes"].create_index(
        [("user_id", ASCENDING), ("verification_id", ASCENDING)], unique=True
    )

    await mongo_client.db["live_users"].create_indexes(
        [
            IndexModel([("author_id", ASCENDING), ("feed_id", ASCENDING)]),
            IndexModel([("expiration_date", ASCENDING)]),
            IndexModel([("feed_id", ASCENDING), ("expiration_date", ASCENDING)]),
        ]
    )
    await mongo_client.db["subscribed_space_users"].create_index(
        [("user_id", ASCENDING), ("livekit_room_name", ASCENDING)], unique=True
    )
    await mongo_client.db["news"].create_index(
        [("created_at", DESCENDING), ("id", DESCENDING)]
    )
    await mongo_client.db["users"].create_indexes(
        [
            IndexModel([("external_user_id", ASCENDING)], unique=True),
            IndexModel([("username", ASCENDING)]),
            IndexModel([("phone_number", ASCENDING)]),
            IndexModel([("username", "text")], default_language="none"),
        ]
    )
    await mongo_client.db["comments"].create_indexes(
        [
            IndexModel([("verification_id", ASCENDING), ("created_at", DESCENDING)]),
            IndexModel([("verification_id", ASCENDING), ("score", DESCENDING)]),
            IndexModel([("author_id", ASCENDING)]),
        ]
    )
    await mongo_client.db["comment_likes"].create_index(
        [("user_id", ASCENDING), ("comment_id", ASCENDING)], unique=True
    )
    await mongo_client.db["comment_reactions"].create_indexes(
        [
            IndexModel(
                [("comment_id", ASCENDING), ("user_id", ASCENDING)], unique=True
            ),
            IndexModel([("comment_id", ASCENDING), ("reaction_type", ASCENDING)]),
            IndexModel([("user_id", ASCENDING)]),
            IndexModel([("created_at", DESCENDING)]),
        ]
    )
    await mongo_client.db["verifications"].create_indexes(
        [
            IndexModel(
                [("assignee_user.user_id", ASCENDING), ("created_at", DESCENDING)]
            ),
            IndexModel([("assignee_user_id", ASCENDING), ("is_public", ASCENDING)]),
            IndexModel([("livekit_room_name", ASCENDING)]),
            IndexModel([("valid_until", DESCENDING)]),
            IndexModel([("score", DESCENDING)]),
        ]
    )
    await mongo_client.db["friendships"].create_index(
        [("user_id", ASCENDING), ("friend_id", ASCENDING)], unique=True
    )
    await mongo_client.db["friend_requests"].create_index(
        [("sender_id", ASCENDING), ("receiver_id", ASCENDING)], unique=True
    )
    await mongo_client.db["push-notification-tokens"].create_indexes(
        [
            IndexModel([("ownerId", ASCENDING)]),
            IndexModel([("expo_push_token", ASCENDING)]),
        ]
    )
    await mongo_client.db["fact_checks"].create_indexes(
        [
            IndexModel([("verification_id", ASCENDING)], unique=True),
            IndexModel([("created_at", DESCENDING)]),
        ]
    )

    await mongo_client.db["fact_check_ratings"].create_index(
        [("user_id", ASCENDING), ("verification_id", ASCENDING)], unique=True
    )

    await mongo_client.db["chat_messages"].create_index(
        [("room_id", ASCENDING), ("created_at", DESCENDING)]
    )

    await mongo_client.db["external_articles"].create_index(
        [("external_id", ASCENDING)], unique=True
    )
    # Ensure Atlas Search index on verifications collection
    try:
        search_indexes_cursor = await mongo_client.db[
            "verifications"
        ].list_search_indexes()
        existing_search_indexes = await search_indexes_cursor.to_list(length=None)
        existing_search_index_names = [
            idx.get("name") for idx in existing_search_indexes
        ]

        if "default" not in existing_search_index_names:
            await mongo_client.db["verifications"].create_search_index(
                {
                    "definition": {
                        "mappings": {
                            "dynamic": True,
                        }
                    },
                    "name": "default",
                }
            )
            logger.info(
                "Created Atlas Search index 'default' on 'verifications' collection"
            )
        else:
            logger.info(
                "Atlas Search index 'default' already exists on 'verifications' collection"
            )
    except Exception as e:
        logger.warning(f"Failed to ensure Atlas Search index on 'verifications': {e}")

