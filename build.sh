#!/usr/bin/env bash
set -e

echo "📦 Installing system dependencies..."
# Render runs as root in build phase - apt-get works directly
apt-get update -y && apt-get install -y libraqm0 libfribidi0 libharfbuzz0b ffmpeg || \
    (echo "⚠️ apt failed, trying alternative..." && \
     apt-get install -y libraqm0 libfribidi0 libharfbuzz0b ffmpeg)

echo "🔍 Checking ffmpeg..."
ffmpeg -version | head -1 || echo "⚠️ ffmpeg not available (video extraction will be unavailable)"

echo "🐍 Installing Python packages..."
pip install --upgrade pip
pip install -r requirements.txt

echo "🔍 Verifying Raqm (Arabic shaping)..."
python -c "from PIL import features; assert features.check('raqm'), 'Raqm NOT available'; print('✅ Raqm OK')"

echo "✅ Build complete"
