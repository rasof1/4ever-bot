#!/usr/bin/env bash
set -e

echo "📦 Installing system dependencies (Arabic shaping)..."
apt-get update && apt-get install -y libraqm0 libfribidi0 libharfbuzz0b || true

echo "🐍 Installing Python packages..."
pip install --upgrade pip
pip install -r requirements.txt

echo "🔍 Verifying ffmpeg via imageio-ffmpeg..."
python -c "import imageio_ffmpeg; print('   ffmpeg binary:', imageio_ffmpeg.get_ffmpeg_exe())" || echo "   ⚠️ imageio-ffmpeg not available"

echo "🔍 Verifying Raqm..."
python -c "from PIL import features; assert features.check('raqm'); print('✅ Raqm OK')"

echo "✅ Build complete"
