# Use official Python image that has pip pre-installed
FROM python:3.11-slim

# Install Node.js and build dependencies
RUN apt-get update && apt-get install -y \
    curl \
    build-essential \
    && curl -fsSL https://deb.nodesource.com/setup_18.x | bash - \
    && apt-get install -y nodejs \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy package files
COPY package*.json ./
COPY grok-voice-agent/requirements.txt ./grok-voice-agent/
COPY chromadb_api/requirements.txt ./chromadb_api/

# Install dependencies
RUN npm install
RUN pip install -r grok-voice-agent/requirements.txt
RUN pip install -r chromadb_api/requirements.txt

# Copy application files
COPY . .

# Make start script executable
RUN chmod +x start.sh

# Expose ports
EXPOSE 3000 5001

# Start all services
CMD ["bash", "start.sh"]
