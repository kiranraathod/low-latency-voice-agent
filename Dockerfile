FROM python:3.11-slim

# Install uv from official Astral image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Add labels
LABEL maintainer="AI Architect"

# Setup working directory
WORKDIR /app

# Enable bytecode compilation
ENV UV_COMPILE_BYTECODE=1
# Use system python
ENV UV_PROJECT_ENVIRONMENT=/opt/venv

# First, install dependency layer
COPY pyproject.toml uv.lock ./
RUN uv venv /opt/venv && uv sync --frozen --no-install-project --no-dev

# Copy application files
COPY ./app ./app
COPY ./client ./client

# Expose port
EXPOSE 8000

# Set path to the venv
ENV PATH="/opt/venv/bin:$PATH"

# Run FastAPI with unvicorn
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
