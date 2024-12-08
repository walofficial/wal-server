from typing import Awaitable
from pymongo import ASCENDING, DESCENDING, UpdateOne

from bson import ObjectId

from ment_api.persistence.mongo_client import db


class BaseMongo:
    def __init__(self, collection):
        self.collection = collection

    async def aggregate(self, pipeline):
        cursor = db[self.collection].aggregate(pipeline)
        return await cursor.to_list(length=None)

    async def find_one(self, query, sort=None, session=None):
        return await db[self.collection].find_one(query, sort=sort, session=session)

    async def find_one_by_id(self, obj_id: ObjectId, session=None):
        return await db[self.collection].find_one({"_id": obj_id}, session=session)

    async def insert_many(self, documents, session=None):
        return await db[self.collection].insert_many(documents, session=session)

    async def delete_one(self, query, session=None):
        return await db[self.collection].delete_one(query, session=session)

    async def insert_one(self, document, session=None):
        return await db[self.collection].insert_one(document, session=session)

    async def delete_all(self, filter, session=None):
        return await db[self.collection].delete_many(filter, session=session)

    async def update_one(self, query, new_state, upsert=False, session=None):
        return await db[self.collection].update_one(
            query, new_state, upsert=upsert, session=session
        )

    async def find_all(self, query, sort=None):
        cursor = db[self.collection].find(query, sort=sort)
        return await cursor.to_list(length=None)

    async def bulk_update(self, operations: list[UpdateOne]):
        return await db[self.collection].bulk_write(operations)

    async def count_documents(self, filter):
        return await db[self.collection].count_documents(filter)

    async def find_one_and_delete(self, query, session=None):
        return await db[self.collection].find_one_and_delete(query, session=session)


swipes = BaseMongo("swipes")
tasks = BaseMongo("tasks")
users = BaseMongo("users")
likes = BaseMongo("likes")
daily_news = BaseMongo("daily_news")
daily_picks = BaseMongo("daily_picks")
daily_picks_categories = BaseMongo("daily_picks_categories")
gcloud_tasks = BaseMongo("gcloud_tasks")
matches = BaseMongo("matches")
verifications = BaseMongo("task_verifications")
push_notification_tokens = BaseMongo("push-notification-tokens")
chat_messages = BaseMongo("chat_messages")
notifications = BaseMongo("notifications")
friend_requests = BaseMongo("friend_requests")
friendships = BaseMongo("friendships")
challenge_requests = BaseMongo("challenge_requests")
chat_rooms = BaseMongo("chat_rooms")
task_location_mappings = BaseMongo("task_location_mappings")
task_ratings = BaseMongo("task_ratings")
live_users = BaseMongo("live_users")
pinned_verifications = BaseMongo("pinned_verifications")


async def initialize_db() -> Awaitable[None]:
    await db["swipes"].create_index(
        [("initiator_user_id", ASCENDING), ("target_user_id", ASCENDING)], unique=True
    )
    await db["task_location_mappings"].create_index(
        [("task_ids", DESCENDING)], unique=True
    )
    await db["notifications"].create_index(
        [("to_user_id", ASCENDING), ("created_at", DESCENDING)]
    )
    await db["likes"].create_index(
        [("user_id", ASCENDING), ("verification_id", ASCENDING)], unique=True
    )
    await db["notifications"].create_index(
        [
            ("from_user_id", ASCENDING),
            ("to_user_id", ASCENDING),
            ("type", ASCENDING),
            ("task_id", ASCENDING),
            ("created_at", ASCENDING),
        ]
    )
    await db["task_ratings"].create_index(
        [("user_id", ASCENDING), ("task_id", ASCENDING)], unique=True
    )
    await db["task_ratings"].create_index([("created_at", ASCENDING)])
