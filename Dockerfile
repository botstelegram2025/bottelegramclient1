FROM node:20-slim

# Install Python
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    python3-dev \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy package files
COPY package.json ./
COPY requirements.txt ./

# Install Node.js dependencies
RUN npm install

# Install Python dependencies  
RUN pip3 install -r requirements.txt

# Copy application code
COPY . .

# Create sessions directory
RUN mkdir -p sessions

# Expose port for health checks
EXPOSE 3001

# Set environment variables
ENV NODE_ENV=production
ENV PYTHONUNBUFFERED=1

# Start the unified server
CMD ["python3", "main.py"]
