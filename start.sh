#!/usr/bin/env bash
# 🚀 4Ever Bot — Quick Start Script
# يقوم بكل شيء تلقائياً: تثبيت + تحقق + تشغيل

set -e

echo "╔════════════════════════════════════════════╗"
echo "║   🌌  4Ever Telegram Bot — Quick Start    ║"
echo "╚════════════════════════════════════════════╝"
echo ""

# 1. Python check
echo "🔍 [1/5] Checking Python..."
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 not found. Install from python.org"
    exit 1
fi
PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "   ✅ Python $PY_VERSION found"

# 2. Virtual environment
echo ""
echo "📦 [2/5] Setting up virtual environment..."
if [ ! -d "venv" ]; then
    python3 -m venv venv
    echo "   ✅ Created venv/"
else
    echo "   ✓ venv/ already exists"
fi

# Activate (works on Linux/Mac)
source venv/bin/activate 2>/dev/null || {
    echo "❌ Failed to activate venv. On Windows use: venv\\Scripts\\activate"
    exit 1
}

# 3. Install dependencies
echo ""
echo "📥 [3/5] Installing dependencies..."
pip install --upgrade pip -q
pip install -r requirements.txt -q
echo "   ✅ All packages installed"

# 4. Check Raqm
echo ""
echo "🔤 [4/5] Verifying Arabic text support (Raqm)..."
RAQM=$(python -c "from PIL import features; print(features.check('raqm'))" 2>/dev/null)
if [ "$RAQM" = "True" ]; then
    echo "   ✅ Raqm available - Arabic will render perfectly"
else
    echo "   ⚠️  Raqm NOT available. Installing..."
    if [[ "$OSTYPE" == "linux-gnu"* ]]; then
        sudo apt-get install -y libraqm0 libfribidi0 libharfbuzz0b
    elif [[ "$OSTYPE" == "darwin"* ]]; then
        brew install libraqm
    fi
    pip install --upgrade --force-reinstall Pillow -q
    echo "   ✅ Reinstalled Pillow with Raqm support"
fi

# 5. Check env vars
echo ""
echo "🔑 [5/5] Checking environment variables..."
if [ ! -f ".env" ]; then
    echo "❌ .env file not found!"
    echo "   Run: cp .env.example .env"
    echo "   Then edit .env with your tokens"
    exit 1
fi

# Load .env
export $(grep -v '^#' .env | xargs)

if [ -z "$TELEGRAM_BOT_TOKEN" ] || [ "$TELEGRAM_BOT_TOKEN" = "your_telegram_bot_token_here" ]; then
    echo "❌ TELEGRAM_BOT_TOKEN not set in .env"
    exit 1
fi
echo "   ✅ TELEGRAM_BOT_TOKEN found"

if [ -z "$GEMINI_API_KEY" ] || [ "$GEMINI_API_KEY" = "your_gemini_api_key" ]; then
    echo "❌ GEMINI_API_KEY not set in .env"
    echo "   Get one FREE at: https://aistudio.google.com/app/apikey"
    echo "   Then edit .env and add: GEMINI_API_KEY=..."
    exit 1
fi
echo "   ✅ GEMINI_API_KEY found"

# Launch
echo ""
echo "╔════════════════════════════════════════════╗"
echo "║   🚀  LAUNCHING BOT...                     ║"
echo "║   Press Ctrl+C to stop                     ║"
echo "╚════════════════════════════════════════════╝"
echo ""

python bot.py
