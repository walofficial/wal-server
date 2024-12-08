import asyncio
import queue
from typing import List

from pymongo import UpdateOne

from ment_api.models.message_state import MessageState
from ment_api.models.new_message_state import NewMessageState
from ment_api.persistence import mongo

message_state_channel = queue.Queue()


async def process_message_states():
    while True:
        new_message_states = await get_batch_from_queue(message_state_channel, 20)
        if new_message_states:
            operations = [
                UpdateOne(
                    {
                        "_id": new_state.id,
                        "message_state": {"$ne": MessageState.READ},
                    },
                    {"$set": {"message_state": new_state.state.value}},
                )
                for new_state in new_message_states
            ]
            bulk_write_result = await mongo.chat_messages.bulk_update(operations)
        await asyncio.sleep(1)  # Sleep for a short period to prevent tight loop


async def get_batch_from_queue(
    q: queue.Queue, batch_size: int
) -> List[NewMessageState]:
    items = []
    for _ in range(batch_size):
        try:
            item = q.get_nowait()  # Non-blocking
            items.extend(item)
        except queue.Empty:
            break  # Exit loop if the queue is empty
    return items


def init_message_state_worker() -> None:
    asyncio.create_task(process_message_states())
    return None
