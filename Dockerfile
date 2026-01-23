# Use official Python image that has pip pre-installed
FROM python:3.11-slim

# Install Node.js
RUN apt-get update && apt-get install -y \
    curl \
    && curl -fsSL https://deb.nodesource.com/setup_18.x | bash - \
    && apt-get install -y nodejs \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy package files
COPY package*.json ./
COPY grok-voice-agent/requirements.txt ./grok-voice-agent/

# Install dependencies
RUN npm install
RUN pip install -r grok-voice-agent/requirements.txt

# Copy application files
COPY . .

# Make start script executable
RUN chmod +x start.sh

# Expose port
EXPOSE 3000

# Start both services
CMD ["bash", "start.sh"]