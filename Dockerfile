FROM python:3.12-slim

# Prevent Python from writing pyc files to disc & stdout/stderr buffering
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=7860

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install CPU-only torch to keep image size small
RUN pip install torch --index-url https://download.pytorch.org/whl/cpu

# Copy backend files
COPY backend/pyproject.toml backend/alembic.ini /app/
COPY backend/alembic /app/alembic
COPY backend/app /app/app

# Install python dependencies
RUN pip install -e .

# Pre-download and cache embedding model so cold starts are instant
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-large-en-v1.5')" || true

# Set up non-root user for security (Hugging Face Spaces runs as user 1000)
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 7860

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-7860}"]
