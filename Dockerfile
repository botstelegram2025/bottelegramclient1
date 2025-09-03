# Multi-stage Dockerfile for Professional Client Management Bot
FROM node:20-alpine AS whatsapp-service

# Install Node.js dependencies for WhatsApp service
WORKDIR /app/whatsapp
COPY package*.json ./
RUN npm ci --only=production

# Copy WhatsApp service files
COPY whatsapp_baileys_multi.js ./
RUN mkdir -p ./sessions/

# Main Python application stage
FROM python:3.11-slim AS main

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    ENVIRONMENT=production

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create app user for security
RUN groupadd --gid 1001 app && \
    useradd --uid 1001 --gid app --shell /bin/bash --create-home app

# Set working directory
WORKDIR /app

# Copy Python requirements and install dependencies
# Try pyproject.toml first, then fallback to requirements.txt
COPY pyproject.toml* requirements.txt* ./
RUN if [ -f "pyproject.toml" ]; then \
        pip install --no-cache-dir -e .; \
    elif [ -f "requirements.txt" ]; then \
        pip install --no-cache-dir -r requirements.txt; \
    else \
        echo "ERROR: Neither pyproject.toml nor requirements.txt found!"; \
        exit 1; \
    fi

# Copy application code
COPY --chown=app:app . .

# Copy WhatsApp service from previous stage
COPY --from=whatsapp-service --chown=app:app /app/whatsapp ./whatsapp/

# Install Node.js for WhatsApp service
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y nodejs

# Create necessary directories
RUN mkdir -p /app/logs /app/sessions /app/backups && \
    chown -R app:app /app

# Switch to app user
USER app

# Expose ports
EXPOSE 5000 3001

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:3001/health || exit 1

# Create startup script
RUN echo '#!/bin/bash' > /app/start.sh && \
    echo 'set -e' >> /app/start.sh && \
    echo '' >> /app/start.sh && \
    echo 'echo "🚀 Starting Client Management Bot..."' >> /app/start.sh && \
    echo '' >> /app/start.sh && \
    echo '# Start WhatsApp service in background' >> /app/start.sh && \
    echo 'echo "📱 Starting WhatsApp service..."' >> /app/start.sh && \
    echo 'cd /app/whatsapp && node whatsapp_baileys_multi.js &' >> /app/start.sh && \
    echo 'WHATSAPP_PID=$!' >> /app/start.sh && \
    echo '' >> /app/start.sh && \
    echo '# Wait for WhatsApp service to be ready' >> /app/start.sh && \
    echo 'echo "⏳ Waiting for WhatsApp service..."' >> /app/start.sh && \
    echo 'for i in {1..30}; do' >> /app/start.sh && \
    echo '    if curl -s http://localhost:3001/health > /dev/null; then' >> /app/start.sh && \
    echo '        echo "✅ WhatsApp service is ready"' >> /app/start.sh && \
    echo '        break' >> /app/start.sh && \
    echo '    fi' >> /app/start.sh && \
    echo '    echo "Waiting for WhatsApp service... ($i/30)"' >> /app/start.sh && \
    echo '    sleep 2' >> /app/start.sh && \
    echo 'done' >> /app/start.sh && \
    echo '' >> /app/start.sh && \
    echo '# Start main bot application' >> /app/start.sh && \
    echo 'echo "🤖 Starting Telegram bot..."' >> /app/start.sh && \
    echo 'cd /app && python3 main.py &' >> /app/start.sh && \
    echo 'BOT_PID=$!' >> /app/start.sh && \
    echo '' >> /app/start.sh && \
    echo '# Function to handle shutdown' >> /app/start.sh && \
    echo 'cleanup() {' >> /app/start.sh && \
    echo '    echo "🛑 Shutting down services..."' >> /app/start.sh && \
    echo '    kill $BOT_PID $WHATSAPP_PID 2>/dev/null || true' >> /app/start.sh && \
    echo '    wait' >> /app/start.sh && \
    echo '    echo "✅ Shutdown complete"' >> /app/start.sh && \
    echo '    exit 0' >> /app/start.sh && \
    echo '}' >> /app/start.sh && \
    echo '' >> /app/start.sh && \
    echo '# Set up signal handlers' >> /app/start.sh && \
    echo 'trap cleanup SIGTERM SIGINT' >> /app/start.sh && \
    echo '' >> /app/start.sh && \
    echo '# Wait for either process to exit' >> /app/start.sh && \
    echo 'wait $BOT_PID $WHATSAPP_PID' >> /app/start.sh

RUN chmod +x /app/start.sh

# Start services
CMD ["/app/start.sh"]

# Labels for metadata
LABEL maintainer="Development Team <dev@example.com>" \
      version="1.0.0" \
      description="Professional Telegram bot for client management with WhatsApp integration" \
      org.opencontainers.image.title="Client Management Bot" \
      org.opencontainers.image.description="Professional client management solution with Telegram and WhatsApp integration" \
      org.opencontainers.image.version="1.0.0" \
      org.opencontainers.image.vendor="Your Company" \
      org.opencontainers.image.licenses="MIT"