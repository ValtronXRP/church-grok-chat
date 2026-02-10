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

# Install PyTorch CPU-only (for reranker)
RUN pip install --no-cache-dir torch==2.1.2 --index-url https://download.pytorch.org/whl/cpu

# Copy package files
COPY package*.json ./
COPY grok-voice-agent/requirements.txt ./grok-voice-agent/
COPY chromadb_api/requirements.txt ./chromadb_api/
COPY reranker_requirements.txt ./reranker_requirements.txt

# Install all dependencies in a single pip resolve to avoid version conflicts
RUN npm install
COPY combined_requirements.txt ./combined_requirements.txt
RUN pip install --no-cache-dir -r combined_requirements.txt

# Pre-download reranker models into the image
RUN python3 -c "from sentence_transformers import SentenceTransformer, CrossEncoder; SentenceTransformer('sentence-transformers/all-mpnet-base-v2'); CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2')"

# Copy application files
COPY . .

# Make start script executable
RUN chmod +x start.sh

# Expose ports
EXPOSE 3000 5001 5050

# Start all services
CMD ["bash", "start.sh"]
