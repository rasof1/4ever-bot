#!/usr/bin/env bash
# Render.com build script
set -e

echo "📦 Installing system dependencies..."
apt-get update && apt-get install -y libraqm0 libfribidi0 libharfbuzz0b || true

echo "🐍 Installing Python packages..."
pip install --upgrade pip
pip install -r requirements.txt

echo "🔍 Verifying Raqm (Arabic shaping) support..."
python -c "from PIL import features; assert features.check('raqm'), 'Raqm NOT available - Arabic text will break'; print('✅ Raqm OK')"

echo "✅ Build complete"
