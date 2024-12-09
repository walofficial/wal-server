# Contributing Guidelines
Thank you for considering contributing to the MENT Backend Service! This document provides guidelines for contributing to the project. Feel free to propose changes to this document if you think something is missing or needs clarification.

## Overview
The MENT backend is built using Python 3.10 and FastAPI, leveraging MongoDB for data persistence and Redis for caching. It implements a comprehensive API layer that supports both REST endpoints and WebSocket connections for real-time features. The service integrates with various Google Cloud Platform services for media processing, task management, and logging.

## Getting Started
Before you begin, ensure you have Python 3.10+, Docker and Docker Compose installed. You'll also need access to MongoDB and Redis instances, as well as accounts for Google Cloud Platform and Cloudflare services.

To set up your development environment, clone the repository and create the necessary configuration files:

```bash
# Clone the repository
git clone https://github.com/mntorg/mnt-server

# Create necessary configuration files
cp config/.env.template config/.env
```

Start the development server using Docker Compose:

```bash
docker compose -f docker-compose.dev.yml up --build
```

The service will be available at `http://localhost:5500`.

## Project Structure
The application follows a modular architecture with clear separation of concerns. The core application code resides in the `ment_api/` directory, which contains routes for API endpoint definitions, services for business logic implementation, models for data structures, and dedicated modules for database access and background task processing.

## Development Guidelines

### Code Style and Quality
We use Black for Python code formatting and follow PEP 8 guidelines. All functions should include type hints for parameters and return values, with complex functions documented using docstrings. This helps maintain code readability and makes the codebase more maintainable.

### API Development
When developing new API endpoints, ensure they are properly documented and follow the existing patterns for route organization. Use Pydantic models for request/response validation, and implement appropriate error handling and logging. All endpoints require API key authentication through middleware (via the `x-api-key` header).

### Real-time Features
The application uses Socket.IO for real-time features like chat and live feed updates. When working with WebSocket connections, follow the existing patterns for connection management and implement proper error handling and reconnection strategies.

### Media Processing
Media processing is a critical part of our application. When working with video and image processing, use the established patterns for video transcoding through Google Cloud services. Consider caching strategies where appropriate, and ensure proper error handling for media uploads. The system supports multiple video quality levels and formats, including sprite sheet generation for video previews.

### Environment Configuration
Configuration management is handled through environment variables. When adding new features that require configuration, add the variables to both `.env.example` and `dev.env.example` files. Document all new variables in the Settings class within `ment_api/config.py`. Keep sensitive information in environment variables and follow the established naming conventions.

### Database Operations
Database interactions should follow the existing patterns for MongoDB usage. Consider query performance when designing new features and implement appropriate indexing for frequently queried collections. Always implement proper error handling for database operations and use appropriate data types and validation.

### Testing
Testing is crucial for maintaining code quality. Write unit tests for new functionality and ensure they cover edge cases. Mock external service calls in tests and verify that all tests pass before submitting a pull request. Test your changes thoroughly in the development environment.

## Pull Request Process
To submit changes, fork the repository and create a branch from `main`. Ensure your code follows our standards and includes appropriate tests and documentation. When submitting a pull request, provide a clear description of the changes and reference any related issues. Include screenshots for UI changes if applicable.

## Issue Reporting
When reporting issues, first check if the issue has already been reported. Provide as much relevant information as possible, including steps to reproduce the issue and any relevant system information or logs. Use the issue template if one is provided.

## Communication

Maintain clear and constructive communication in all interactions. Be respectful of others' contributions and ask questions if something is unclear. When discussing changes, provide adequate context to help others understand your perspective.

## Additional Resources
- [FastAPI Documentation](https://fastapi.tiangolo.com/)
- [Project README](README.md)
- [Google Cloud Documentation](https://cloud.google.com/docs)
- [MongoDB Documentation](https://docs.mongodb.com/)
- [Socket.IO Documentation](https://socket.io/docs/v4/)
- [Redis Documentation](https://redis.io/documentation)

Thank you for contributing to the MENT!
