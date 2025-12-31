# Langfuse Tracing Configuration

## Overview
Langfuse tracing is **DISABLED by default** in development environments to prevent authentication errors and unnecessary network calls.

## Current Configuration

### Development Environment (`.env.dev`)
```bash
LANGFUSE_TRACING_ENABLED=false  # Tracing disabled
LANGFUSE_HOST=http://localhost:3000
LANGFUSE_PUBLIC_KEY=disabled
LANGFUSE_SECRET_KEY=disabled
LANGFUSE_TRACING_ENVIRONMENT=development
```

## How It Works

The `langfuse_client.py` module checks the `LANGFUSE_TRACING_ENABLED` environment variable:

- **If `false` (default)**: Uses a no-op `DisabledLangfuse` class that accepts all method calls but doesn't export any traces
- **If `true`**: Uses the real Langfuse client with proper credentials

## Enabling Langfuse Tracing

### For Development
1. Sign up at [cloud.langfuse.com](https://cloud.langfuse.com)
2. Get your API keys from the dashboard
3. Update `config/.env.dev`:
```bash
LANGFUSE_TRACING_ENABLED=true
LANGFUSE_HOST=https://cloud.langfuse.com
LANGFUSE_PUBLIC_KEY=pk-lf-your-public-key
LANGFUSE_SECRET_KEY=sk-lf-your-secret-key
LANGFUSE_TRACING_ENVIRONMENT=development
```
4. Restart: `docker compose restart`

### For Production
Production environments should have `LANGFUSE_TRACING_ENABLED=true` with valid credentials set via environment variables or Google Secret Manager.

## Benefits of Langfuse

When enabled, Langfuse provides:
- LLM call tracking and monitoring
- Token usage and cost analysis
- Latency metrics
- Error tracking
- Trace visualization
- User feedback integration

## Troubleshooting

**401 Authentication Errors**
- Ensure `LANGFUSE_TRACING_ENABLED=false` in development
- Or provide valid credentials if you want to enable tracing

**No Traces Appearing**
- Check `LANGFUSE_TRACING_ENABLED=true`
- Verify credentials are correct
- Check Docker logs for "Langfuse tracing is ENABLED" message
