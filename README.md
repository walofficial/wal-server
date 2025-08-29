# MENT Backend Service

Backend service for the MENT Gemini Competition platform. Built with FastAPI, modern Python tooling, and designed for scalability and maintainability.

## Prerequisites

- Docker and Docker Compose (recommended for ease of setup and consistent environment)
- Python 3.13 (specified in `.python-version` - use a tool like `asdf` or `pyenv` for management)
- [uv package manager](https://github.com/astral-sh/uv) (for fast and reliable dependency management)

## Getting Started

Choose one of the following methods to run the service:

### Method 1: Running with Docker (Recommended)

This approach provides the most consistent and reproducible environment.

1.  **Build and start the services:**

    ```bash
    docker compose up --build
    ```

    This command will:

    - Build the Docker image using the instructions in `docker/Dockerfile`.
    - Start the service on port 5500 (mapped to internal 5300).
    - Enable hot-reload for development (if `GUNICORN_RELOAD=true` in the environment).
    - Load environment variables from `config/.env` and `config/dev.env`.

2.  **Access the service:**

    Open your web browser and navigate to `http://localhost:5500/docs` to view the automatically generated Swagger UI documentation.

### Method 2: Local Development Environment

This approach is suitable for more granular control and debugging but requires you to manage dependencies and environment variables directly.

1.  **Install uv package manager:**

If you don't have `uv` installed globally, install it using pipx

```bash
pipx install uv
```

2.  **Install Dependencies with uv:**

    ```bash
    uv sync
    ```

    This command will:

    - Create a virtual environment (if it doesn't exist) in a `.venv` directory. (Make sure that `.venv` directory is ignored with .gitignore).
    - Install all dependencies listed in `pyproject.toml`.
    - Update existing packages if needed, based on `pyproject.toml`.

3.  **Activate the virtual environment**

    ```bash
    source .venv/bin/activate
    ```

4.  **Set up Environment Variables:**

    Create a `.env` file (or copy `config/.env` and `config/dev.env` into it) in the project root directory. Populate it with the necessary environment variables. Example:

    ```
    API_SECRET_KEY=["your_secret_key_here"]
    # Other environment variables...
    ```

5.  **Run the Application:**

    ```bash
    python main.py
    ```

    This will start the FastAPI application, usually on `http://localhost:8000`.

## Development Setup Details

### Virtual Environment Management

- **Why uv?** `uv` offers significant performance improvements over `pip` and other package managers, making dependency management faster and more efficient.

- **Commit uv.lock?** Generally, it's best practice to commit the `uv.lock` file to version control (like Git) to ensure reproducible builds and consistent environments across different machines. This is _especially_ important for projects with binary dependencies.

### Code Quality Tools

This project is configured with several code quality tools to ensure consistent style, catch potential errors early, and improve overall code maintainability.

#### Pre-commit Hooks

Pre-commit hooks automatically run checks before you commit code, preventing common issues from being introduced into the codebase.

1.  **Install pre-commit:**

    ```bash
    uv install pre-commit
    ```

2.  **Install the hooks:**

    ```bash
    pre-commit install
    ```

    The following hooks will now run automatically on each commit:

    - **Ruff Check:** `ruff check --no-cache .` (linter)
    - **Ruff Format:** `ruff format` (code formatter)

#### Static Type Checking & Linting (Manual Execution)

While pre-commit hooks run automatically, you can also run these tools manually for more immediate feedback.

```bash
uv mypy .        # Run type checking
uv ruff check .  # Run linting
uv ruff format . # Run formatting
```

## Package Management (Using uv)

- **Adding Dependencies:**

  ```bash
  uv add package_name             # For production dependencies
  uv add --dev package_name         # For development dependencies
  ```

- **Updating Dependencies:**

  While `uv sync` generally updates dependencies, you can also:

  ```bash
  uv update
  ```

  This command will update all dependencies to their latest versions, respecting any version constraints defined in `pyproject.toml`. After updating, be sure to commit the updated `pyproject.toml` and `uv.lock` files.

- **Managing dependencies with pyproject.toml:** Edit the `pyproject.toml` file directly to add or modify dependencies, or to update dependency versions. After making changes to `pyproject.toml` run `uv sync`.

## Project Structure

```
.
├── docker/              # Docker configuration files
│   └── Dockerfile       # Dockerfile for building the application image
├── config/             # Environment configuration files
│   ├── .env             # Base environment variables (DO NOT COMMIT SENSITIVE DATA)
│   └── dev.env          # Development-specific environment variables (overrides .env)
├── src/               # Application source code
│   ├── ment_api/       # Application package
│   │   ├── __init__.py   # Initialize the package
│   │   ├── app.py        # FastAPI application instance
│   │   ├── ...           # Other modules and packages
├── pyproject.toml     # Python project configuration (dependencies, build settings, etc.)
├── docker-compose.yml # Docker Compose configuration for multi-container deployments
└── README.md          # This file
```

## Key Implementation Details & Conventions

- **Environment Variables:**

  - Sensitive information (API keys, database passwords, etc.) should _never_ be committed to the repository. Store them securely in `.env` files (and ensure these files are in `.gitignore`).
  - Use `python-dotenv` to load these variables into the environment.
  - Use `pydantic-settings` to manage the environment variables.

- **API Key Security:**

  - The `APIKeyMiddleware` enforces API key authentication for all endpoints _except_ the health check, `/docs`, and `/openapi.json` endpoints.
  - API keys are configured in the `settings.api_secret_key` setting (loaded from environment variables).

- **User Authentication:**

  - The backend expects a `user_id` header in every request to user-specific endpoints.
  - This header is assumed to be set by a Cloudflare Worker (or similar) _after_ successful authentication. This means the backend does _not_ handle user authentication directly.

- **Logging:**

  - Request logging middleware is included for the `/verify-videos` endpoint, demonstrating how to track request processing time. Use a logging library like `loguru` to set up log levels.
  - Appropriate logging levels (DEBUG, INFO, WARNING, ERROR) should be used for different types of messages.

- **CORS Configuration:**

  - The `CORSMiddleware` is configured to allow all origins, credentials, methods, and headers (`allow_origins=["*"]`, etc.). _This is generally not recommended for production environments._ In production, you should restrict CORS to only the specific domains that need to access the API.

- **Asynchronous Operations:**

  - FastAPI is built on `asyncio`, so leverage asynchronous operations whenever possible to improve performance and scalability.

## Notes

- **Port Mapping:** The service runs internally on port 5300, but this is mapped to port 5500 when using Docker Compose. Adjust the port mappings as needed.
- **UV Package Management:** Use UV for all package management operations.
- **Development Variables:** Development-specific environment variables are loaded from `config/dev.env`.
- **Scalability Considerations:**
  - For production deployments, consider using a load balancer, multiple instances of the application, and a robust database setup.
  - Caching (using a library like `aiocache`) can help reduce database load for frequently accessed data.
  - Background tasks (using a library like `rq`) can be used to offload long-running operations from the main request/response cycle.

## Contributing

1.  Fork the repository.
2.  Create a new branch for your feature or bug fix.
3.  Implement your changes, ensuring that you follow the code style guidelines and include appropriate tests.
4.  Commit your changes with clear and concise commit messages.
5.  Create a pull request to the main branch.

## License

[MIT License](LICENSE)

