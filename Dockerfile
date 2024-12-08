# Build stage
FROM python:3.10-slim as builder

# Set build-time environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Create and activate virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install build dependencies
COPY requirements.txt .

# Install dependencies in virtual environment
RUN pip install --no-cache-dir -r requirements.txt

# Final stage
FROM python:3.10-slim

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV APP_HOME=/app
WORKDIR $APP_HOME

# Copy application code
COPY . ./

# Create non-root user for security
RUN adduser --disabled-password --gecos "" appuser && \
    chown -R appuser:appuser $APP_HOME
USER appuser

# Expose port (for documentation)
EXPOSE 8080

# Set the entry point
CMD exec gunicorn --bind :$PORT \
    --workers 1 \
    --worker-class uvicorn.workers.UvicornWorker \
    --threads 8 \
    --timeout 0 \
    main:app