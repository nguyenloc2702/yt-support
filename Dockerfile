# Base image with official Playwright and headless Chromium installed
FROM mcr.microsoft.com/playwright/python:v1.48.0-noble

# Set environment
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

LABEL app="support-video-worker"

# Install system utilities: ffmpeg, rclone, curl
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    rclone \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY . .

# Create volume mount points
RUN mkdir -p /app/sessions /app/data /app/logs /root/.config/rclone

# Default entrypoint runs the background automation worker
CMD ["python", "-m", "core.worker"]
