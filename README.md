# MENT Backend Service
A robust FastAPI-based backend service powering MENT, a location based social network. The service handles users feed, real-time chat, and media processing through a microservices architecture.

## Overview
MENT is designed as a social platform where interactions are driven by tasks and challenges rather than traditional social networking features. Users can create, verify, and interact with various challenges, making social connections through shared activities and achievements.

## Key Features
At its core, MENT implements a sophisticated task-based social interaction system. Users can create and participate in various challenges, with each task requiring verification through our advanced media processing system. The platform supports real-time interactions through chat functionality and provides a dynamic, location-based feed generation system.

The verification system is particularly robust, utilizing Google Cloud Storage and Video Transcoder for processing user-submitted evidence. This ensures that all task completions can be properly validated while maintaining high performance and reliability.

Our notification system keeps users engaged and informed about task updates, verification statuses, and social interactions. The system is designed to handle high throughput while maintaining low latency, essential for a responsive social platform.

## Security and Performance
Security is paramount in MENT's architecture. The service implements comprehensive API key authentication through middleware, ensuring that all sensitive endpoints are properly protected. Our CDN integration through Cloudflare Workers provides an additional layer of security and performance optimization.

The database layer is optimized for social networking operations, with carefully designed indexes that support efficient querying of notifications, likes, task ratings, and other frequently accessed data. This ensures smooth performance even as the platform scales.

## Contributing
For detailed information about contributing to the project, including setup instructions, development guidelines, and coding standards, please refer to our [Contributing Guidelines](CONTRIBUTING.md).
