# Lightweight Python 3.11 base image (No Playwright/Chromium bloat)
FROM python:3.11-slim

# Set environment
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

LABEL app="support-video-worker"

# Copy official static Deno binary for yt-dlp JavaScript challenges (highest priority in yt-dlp)
COPY --from=denoland/deno:bin /deno /usr/local/bin/deno

# Install system utilities: ffmpeg, rclone, curl, nodejs (secondary JS runtime)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    rclone \
    curl \
    nodejs \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && (which node || ln -sf $(which nodejs) /usr/local/bin/node)

WORKDIR /app

# Install isolated worker dependencies
COPY requirements-worker.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements-worker.txt

# Copy source code
COPY . .

# Create volume mount points
RUN mkdir -p /app/sessions /app/data /app/logs /root/.config/rclone

# Default entrypoint runs the background automation worker
CMD ["python", "-m", "core.worker"]
