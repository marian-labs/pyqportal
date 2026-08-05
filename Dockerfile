# Use official lightweight Python image
FROM python:3.11-slim

# Allow setting port at build time, defaulting to 8000
ARG PORT=8000

# Prevent Python from writing pyc files and buffering stdout/stderr
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=${PORT}

# Set working directory
WORKDIR /app

# Install system dependencies (needed for native modules like psycopg2, fitz/pymupdf, Pillow)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements file first to leverage Docker cache
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY . .

# Expose port (dynamically configured via PORT variable)
EXPOSE ${PORT}

# Health check to monitor internal container process health
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://127.0.0.1:${PORT}/ || exit 1

# Run application with Gunicorn
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT} --workers 2 --threads 4 --timeout 120 app:app"]
