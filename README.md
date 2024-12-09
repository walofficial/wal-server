# MENT Backend Service

A robust FastAPI-based backend service powering MENT, a social platform that combines task-based interactions with social networking features. The service handles user verification, real-time chat, task management, and media processing through a microservices architecture.

## Overview
The MENT backend is built using Python 3.10 and FastAPI, leveraging MongoDB for data persistence and Redis for caching. It implements a comprehensive API layer that supports both REST endpoints and WebSocket connections for real-time features. The service integrates with various Google Cloud Platform services for media processing, task management, and logging.

## Core Architecture
The application follows a modular architecture with clear separation of concerns:

- **API Layer**: FastAPI routes handling HTTP requests and WebSocket connections
- **Service Layer**: Business logic implementation and external service integration
- **Persistence Layer**: MongoDB data access and Redis caching
- **Worker Layer**: Background task processing and async operations

## Key Features
The backend implements sophisticated features including real-time chat with Socket.IO, video transcoding using Google Cloud services, task verification systems, and location-based feed generation. It uses Cloudflare Workers for authentication and implements a comprehensive notification system.

The service handles media processing through Google Cloud Storage and Video Transcoder, manages user verification flows, and implements a task-based social interaction system where users can create, verify, and interact with various challenges or tasks.

## Getting Started

### Prerequisites
- Docker and Docker Compose
- Python 3.10+
- MongoDB instance
- Redis instance
- Google Cloud Platform account with required services enabled
- Cloudflare account for CDN and Workers

### Environment Setup
1. Clone the repository
2. Create configuration files:
   - `config/.env` for base configuration
   - `config/dev.env` for development-specific settings
   - `config/prod.env` for production settings

Required environment variables are defined in the Settings class (reference: `ment_api/config.py`).

### Local Development
Start the development server:

```bash
docker compose -f docker-compose.dev.yml up --build
```

The service will be available at `http://localhost:5500`.

## Development Guidelines

### Project Structure
- `ment_api/`: Core application code
  - `routes/`: API endpoint definitions
  - `services/`: Business logic implementation
  - `models/`: Pydantic models and data structures
  - `persistence/`: Database and cache interactions
  - `workers/`: Background task processors

### API Security
The service implements API key authentication through middleware. All endpoints except health checks require a valid API key in the `x-api-key` header.

### Real-time Features
The application uses Socket.IO for real-time features like chat and live feed updates. WebSocket connections are managed through the Socket.IO integration with FastAPI.

### Media Processing
Video and image processing is handled through Google Cloud services:
- Video transcoding for different quality levels and formats
- Image storage and processing for verification media
- Sprite sheet generation for video previews

### Database Indexes
The service automatically creates necessary MongoDB indexes during initialization for optimal query performance. Key indexes are created for notifications, likes, task ratings, and other frequently queried collections.

## Testing and Deployment
The service includes a comprehensive test suite and supports different deployment environments through environment-specific configuration files. The production environment uses Google Cloud Logging for centralized logging.

## Contributing
When contributing to this project, please follow the existing code structure and naming conventions. All new endpoints should be properly documented. Make sure to add appropriate error handling and logging throughout your code. If you add new environment variables, update the configuration documentation accordingly. Before submitting pull requests, thoroughly test your changes in the development environment.
