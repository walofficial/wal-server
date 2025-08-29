# Ment API Structured Logging Guide

This document explains the comprehensive structured logging system implemented in the Ment API to enable detailed debugging and monitoring of fact-checking, social media scraping, PubSub operations, and more.

## Overview

The structured logging system provides:

- **Rich metadata** for easy filtering and debugging
- **Consistent log format** across all services
- **Service and component identification** for easy navigation
- **Performance metrics** (duration tracking)
- **Error details** with full context
- **PubSub message tracking** for debugging message flows
- **External service call monitoring** (Jina, Gemini, etc.)

## Log Structure

Every log entry contains:

```json
{
  "timestamp": "2024-01-15T10:30:45.123Z",
  "service": "ment-api",
  "component": "fact-checker",
  "message": "Starting fact check for 1 verifications",
  "level": "INFO",
  "verification_id": "507f1f77bcf86cd799439011",
  "user_id": "user_123",
  "operation": "check_fact_batch",
  "status": "STARTED",
  "duration_ms": 1250.5,
  "metadata": {
    "verification_count": 1,
    "verification_ids": ["507f1f77bcf86cd799439011"]
  }
}
```

## Services and Components

### Available Loggers

| Service    | Component         | Purpose                  |
| ---------- | ----------------- | ------------------------ |
| `ment-api` | `fact-checker`    | Fact checking operations |
| `ment-api` | `social-scraper`  | Social media scraping    |
| `ment-api` | `task-service`    | Task and post operations |
| `ment-api` | `pubsub`          | PubSub publish/receive   |
| `ment-api` | `media-extractor` | URL/media extraction     |

## Google Cloud Log Explorer Filters

### Basic Filtering

#### Filter by Service and Component

```
resource.type="gae_app"
jsonPayload.service="ment-api"
jsonPayload.component="fact-checker"
```

#### Filter by Verification ID

```
jsonPayload.verification_id="507f1f77bcf86cd799439011"
```

#### Filter by User ID

```
jsonPayload.user_id="user_123"
```

#### Filter by Operation

```
jsonPayload.operation="check_fact_batch"
```

#### Filter by Status

```
jsonPayload.status="ERROR"
```

### Advanced Filtering

#### Track Complete Fact Check Flow

```
jsonPayload.verification_id="507f1f77bcf86cd799439011"
AND (
  jsonPayload.operation="check_fact_batch" OR
  jsonPayload.operation="extract_statement" OR
  jsonPayload.operation="gemini_analysis" OR
  jsonPayload.operation="jina_fact_check"
)
```

#### Monitor PubSub Message Flow

```
jsonPayload.operation="pubsub_publish" OR jsonPayload.operation="pubsub_receive"
AND jsonPayload.metadata.topic="check-fact-topic"
```

#### Track Social Media Scraping

```
jsonPayload.component="social-scraper"
AND jsonPayload.verification_id="507f1f77bcf86cd799439011"
```

#### Find Slow Operations (>5 seconds)

```
jsonPayload.duration_ms>5000
```

#### Monitor External Service Calls

```
jsonPayload.operation=~"external_service_.*"
```

#### Track Media Extraction

```
jsonPayload.component="media-extractor"
AND jsonPayload.operation="extract_social_media_url"
```

#### Find Failed Operations

```
jsonPayload.status="ERROR" OR jsonPayload.status="FAILED"
```

### Debugging Specific Scenarios

#### Debug Fact Check Failures

```
jsonPayload.component="fact-checker"
AND jsonPayload.status="ERROR"
AND timestamp>="2024-01-15T00:00:00Z"
```

#### Debug PubSub Issues

```
jsonPayload.component="pubsub"
AND (jsonPayload.status="ERROR" OR jsonPayload.error EXISTS)
```

#### Monitor API Performance

```
jsonPayload.operation="api_request"
AND jsonPayload.metadata.endpoint="/publish-post"
AND jsonPayload.duration_ms>1000
```

#### Track User Journey

```
jsonPayload.user_id="user_123"
AND timestamp>="2024-01-15T10:00:00Z"
AND timestamp<="2024-01-15T11:00:00Z"
ORDER BY timestamp DESC
```

## Common Operations and Their Logs

### Fact Checking Flow

1. **Batch Start**: `operation="check_fact_batch", status="STARTED"`
2. **Statement Extraction**: `operation="fact_check_extract_statement"`
3. **Gemini Analysis**: `operation="external_service_gemini_analysis"`
4. **Jina Fact Check**: `operation="external_service_jina_fact_check"`
5. **Score Generation**: `operation="generate_score"`
6. **Batch Complete**: `operation="check_fact_batch", status="SUCCESS"`

### Social Media Scraping Flow

1. **Scrape Start**: `operation="scrape_social_media", status="STARTED"`
2. **Scrape Content**: `operation="external_service_scrape_with_screenshot"`
3. **Parse Content**: `operation="parse_content"`
4. **Update Verification**: `operation="update_verification"`
5. **Trigger Fact Check**: `operation="trigger_fact_check"`

### PubSub Message Flow

1. **Publish**: `operation="pubsub_publish", status="STARTED"`
2. **Published**: `operation="pubsub_publish", status="SUCCESS"`
3. **Receive**: `operation="pubsub_receive", status="RECEIVED"`
4. **Process**: `operation="process_check_fact_callback"`

## Performance Monitoring

### Track Duration Metrics

```
jsonPayload.duration_ms EXISTS
ORDER BY jsonPayload.duration_ms DESC
```

### Monitor Service Health

```
jsonPayload.status="ERROR"
AND timestamp>="2024-01-15T00:00:00Z"
GROUP BY jsonPayload.component, jsonPayload.operation
```

### API Endpoint Performance

```
jsonPayload.operation="api_request"
AND jsonPayload.metadata.endpoint="/publish-post"
ORDER BY jsonPayload.duration_ms DESC
```

## Error Analysis

### Get Error Details

```
jsonPayload.error EXISTS
AND jsonPayload.verification_id="507f1f77bcf86cd799439011"
```

### Monitor External Service Failures

```
jsonPayload.operation=~"external_service_.*"
AND jsonPayload.status="ERROR"
```

### PubSub Error Tracking

```
jsonPayload.component="pubsub"
AND jsonPayload.error EXISTS
```

## Log Retention and Cleanup

Logs are automatically retained according to Google Cloud Logging retention policies:

- **Default retention**: 30 days
- **Archive longer logs** to Cloud Storage if needed for compliance
- **Set up alerts** for critical errors using Cloud Monitoring

## Best Practices

1. **Use verification_id** as the primary correlation key
2. **Filter by time range** to improve query performance
3. **Use operation names** to track specific workflows
4. **Monitor duration_ms** for performance analysis
5. **Check error fields** for debugging failures
6. **Use component filters** to focus on specific services

## Setting Up Alerts

Create alerts for critical issues:

```
jsonPayload.component="fact-checker"
AND jsonPayload.status="ERROR"
AND jsonPayload.operation="jina_fact_check"
```

This enables proactive monitoring of fact-checking service health.

## Log Examples

### Successful Fact Check

```json
{
  "timestamp": "2024-01-15T10:30:45.123Z",
  "service": "ment-api",
  "component": "fact-checker",
  "message": "Completed fact check processing",
  "verification_id": "507f1f77bcf86cd799439011",
  "user_id": "user_123",
  "operation": "check_fact_batch",
  "status": "SUCCESS",
  "duration_ms": 15420.5,
  "metadata": {
    "total_verifications": 1,
    "successful_checks": 1,
    "average_duration_per_verification": 15420.5
  }
}
```

### PubSub Message

```json
{
  "timestamp": "2024-01-15T10:30:30.123Z",
  "service": "ment-api",
  "component": "pubsub",
  "message": "PubSub publish to check-fact-topic",
  "verification_id": "507f1f77bcf86cd799439011",
  "operation": "pubsub_publish",
  "status": "SUCCESS",
  "metadata": {
    "topic": "check-fact-topic",
    "message_id": "1234567890",
    "message_data": "{\"verifications\":[\"507f1f77bcf86cd799439011\"]}"
  }
}
```

This structured logging system provides comprehensive visibility into the application's behavior and enables rapid debugging and performance optimization.
