# Google Cloud Pub/Sub Migration Guide

## Overview

This guide explains the improvements made to the Pub/Sub implementation to fix timeout and connection issues.

## Problems Fixed

1. **Timeout Errors (504 Deadline Exceeded)**: Caused by creating new publisher clients for each operation
2. **Connection Errors (503 failed to connect)**: Due to connection exhaustion and improper client lifecycle management
3. **Manual Deadline Extension**: Complex and error-prone manual deadline extension logic
4. **No Flow Control**: Risk of overwhelming the system with too many concurrent messages

## Key Improvements

### 1. Singleton Client Management

**Before:**

```python
async def publish_check_fact(verifications: List[ObjectId]) -> None:
    async with PublisherAsyncClient() as publisher:  # Creates new client each time!
        # ... publish logic
```

**After:**

```python
async def publish_check_fact(verifications: List[ObjectId]) -> None:
    await publish_message(
        project_id,
        topic_id,
        data,
        retry_timeout=60.0,
    )
```

### 2. Built-in Flow Control

The new implementation uses Google Cloud Pub/Sub's built-in flow control:

```python
flow_control = FlowControl(
    max_messages=100,  # Limit concurrent messages
    max_bytes=100 * 1024 * 1024,  # 100MB
    max_lease_duration=600,  # 10 minutes
)
```

### 3. Proper Retry Logic

Exponential backoff retry with configurable timeout:

```python
custom_retry = retry.AsyncRetry(
    initial=0.1,  # Start with 100ms
    maximum=10.0,  # Max 10 seconds between retries
    multiplier=2.0,  # Double the delay each retry
    timeout=retry_timeout,
)
```

### 4. Streaming Pull Instead of Manual Pull

**Before:** Manual pull with complex deadline extension
**After:** Streaming pull with automatic deadline management

## Architecture Changes

### PubSubManager Singleton

```python
class PubSubManager:
    """Singleton manager for Pub/Sub clients to ensure proper connection reuse."""

    - Manages single publisher client instance
    - Manages subscriber clients with flow control
    - Handles graceful shutdown
    - Prevents connection exhaustion
```

## Migration Steps

### 1. Update Publisher Code

Replace all instances of:

```python
async with PublisherAsyncClient() as publisher:
    topic_path = publisher.topic_path(project_id, topic_id)
    await publisher.publish(topic=topic_path, messages=[PubsubMessage(data=data)])
```

With:

```python
from ment_api.services.pub_sub_service import publish_message

await publish_message(project_id, topic_id, data, retry_timeout=60.0)
```

### 2. Update Subscriber Initialization

The `initialize_subscriber` now returns an `asyncio.Task` instead of a client:

```python
# Before
subscriber = await initialize_subscriber(...)
# Later: await close_subscriber(subscriber)

# After
task = await initialize_subscriber(...)
# Later: await close_subscriber(task)
```

### 3. Message Interface Compatibility

Worker callbacks continue to receive messages with the same interface:

- `message.message.data`
- `message.message.message_id`
- `message.ack_id`

No changes needed to worker implementations!

## Best Practices

### 1. Always Use the Singleton Publisher

```python
from ment_api.services.pub_sub_service import publish_message

# Good
await publish_message(project_id, topic_id, data)

# Bad - creates new client
async with PublisherAsyncClient() as publisher:
    # ...
```

### 2. Configure Appropriate Timeouts

```python
# For critical messages
await publish_message(project_id, topic_id, data, retry_timeout=120.0)

# For less critical messages
await publish_message(project_id, topic_id, data, retry_timeout=30.0)
```

### 3. Handle Publish Errors

```python
try:
    await publish_message(project_id, topic_id, data)
    logger.info("Message published successfully")
except Exception as e:
    logger.error(f"Failed to publish message: {e}")
    # Handle error appropriately
```

### 4. Monitor Subscriber Health

The new implementation logs subscriber status:

- "Starting subscriber for {subscription_path}"
- "Streaming pull started for {subscription_path}"
- "Successfully processed and acked message {message_id}"

## Performance Benefits

1. **Connection Reuse**: Single publisher client for all publish operations
2. **Reduced Latency**: No client creation overhead per operation
3. **Better Throughput**: Flow control prevents overwhelming the system
4. **Automatic Retries**: Built-in exponential backoff
5. **Graceful Degradation**: Continues operating even with transient failures

## Monitoring and Debugging

### Check for Timeout Errors

Look for these error patterns:

- "Timeout of 60.0s exceeded"
- "504 Deadline Exceeded"
- "503 failed to connect to all addresses"

These should be significantly reduced with the new implementation.

### Monitor Client Lifecycle

```
INFO - Created new PublisherAsyncClient
INFO - Created new sync SubscriberClient with flow control
INFO - Closed PublisherAsyncClient
```

### Track Message Processing

```
DEBUG - Successfully processed and acked message {message_id}
ERROR - Error processing message {message_id}: {error}
```

## Testing

Use the provided `test_pubsub_implementation.py` to verify:

1. Publisher singleton behavior
2. Connection resilience
3. Concurrent publish performance
4. Subscriber flow control

```bash
python test_pubsub_implementation.py
```

## Rollback Plan

If issues arise, you can temporarily revert to creating clients per operation:

1. Restore original `publish_check_fact` implementation
2. Restore original `publish_social_media_scrape_request` implementation
3. Restore original `publish_video_processor_request` implementation

However, this should only be a temporary measure as it will bring back the timeout issues.

## Future Improvements

1. **Batch Publishing**: Accumulate messages and publish in batches
2. **Dead Letter Queues**: Handle permanently failed messages
3. **Metrics Collection**: Track publish/subscribe performance
4. **Circuit Breaker**: Temporarily disable publishing during outages
