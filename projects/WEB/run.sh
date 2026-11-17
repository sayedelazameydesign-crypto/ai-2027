#!/bin/bash
# Quick start script

echo "🚀 Starting Web Dev Agent..."

# Check if .env exists
if [ ! -f .env ]; then
    echo "⚠️  .env file not found. Creating from .env.example..."
    cp .env.example .env
    echo "✅ Please edit .env with your API keys before running"
    exit 1
fi

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv venv
fi

# Activate virtual environment
source venv/bin/activate

# Install dependencies
echo "📥 Installing dependencies..."
pip install -r requirements.txt

# Create necessary directories
mkdir -p /tmp/uploads /tmp/outputs /tmp/previews

# Run the application
echo "✨ Starting server..."
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
