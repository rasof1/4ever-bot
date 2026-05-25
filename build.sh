#!/usr/bin/env bash
set -e

echo "📦 Installing system dependencies (Arabic shaping)..."
apt-get update && apt-get install -y libraqm0 libfribidi0 libharfbuzz0b || echo "⚠️ apt-get failed, continuing"

echo "🐍 Installing Python packages..."
pip install --upgrade pip
pip install -r requirements.txt

echo "🔍 Verifying ffmpeg via imageio-ffmpeg..."
python -c "import imageio_ffmpeg; print('   ffmpeg binary:', imageio_ffmpeg.get_ffmpeg_exe())" || echo "   ⚠️ imageio-ffmpeg not available"

echo "🔍 Checking Raqm (non-fatal)..."
python -c "from PIL import features; ok = features.check('raqm'); print('Raqm:', 'OK' if ok else 'MISSING (Arabic shaping may be limited)')" || echo "   ⚠️ Raqm check failed"

echo "✅ Build complete"
