#!/bin/bash

echo "=========================================="
echo "Sermon Search System Setup"
echo "=========================================="

# Check if Python 3 is installed
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 is required but not installed"
    exit 1
fi

echo "✓ Python 3 found"

# Create virtual environment if it doesn't exist
if [ ! -d "venv_sermon" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv_sermon
    echo "✓ Virtual environment created"
else
    echo "✓ Virtual environment exists"
fi

# Activate virtual environment
source venv_sermon/bin/activate

# Install requirements
echo "Installing Python dependencies..."
pip install --upgrade pip > /dev/null 2>&1
pip install -r requirements_sermon.txt

echo "✓ Dependencies installed"

# Download NLTK data
echo "Downloading NLTK data..."
python3 -c "import nltk; nltk.download('punkt', quiet=True)"
echo "✓ NLTK data downloaded"

# Check if sermon data directories exist
TRANSCRIPT_DIR="/Users/valorkopeny/Desktop/Json3 sermons 1-3"
METADATA_DIR="/Users/valorkopeny/Desktop/SERMONS_ZIP_05"

if [ ! -d "$TRANSCRIPT_DIR" ]; then
    echo "⚠️  Transcript directory not found: $TRANSCRIPT_DIR"
    echo "   Please ensure sermon transcript files are available"
else
    echo "✓ Transcript directory found"
fi

if [ ! -d "$METADATA_DIR" ]; then
    echo "⚠️  Metadata directory not found: $METADATA_DIR"
    echo "   Please ensure sermon metadata files are available"
else
    echo "✓ Metadata directory found"
fi

echo ""
echo "=========================================="
echo "Ready to index sermons!"
echo "=========================================="
echo ""
echo "To index all sermons, run:"
echo "  source venv_sermon/bin/activate"
echo "  python sermon_indexer.py"
echo ""
echo "To start the sermon search API, run:"
echo "  source venv_sermon/bin/activate"
echo "  python sermon_api.py"
echo ""
echo "The API will be available at: http://localhost:5001"
echo ""