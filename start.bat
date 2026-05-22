@echo off
chcp 65001 >nul
title 4Ever Bot

echo ============================================
echo    4Ever Telegram Bot - Quick Start
echo ============================================
echo.

echo [1/5] Checking Python...
where python >nul 2>&1
if errorlevel 1 (
    echo Python not found. Install from python.org
    pause
    exit /b 1
)
echo OK

echo.
echo [2/5] Setting up virtual environment...
if not exist venv (
    python -m venv venv
    echo Created venv\
) else (
    echo venv\ already exists
)
call venv\Scripts\activate.bat

echo.
echo [3/5] Installing dependencies...
pip install --upgrade pip -q
pip install -r requirements.txt -q
echo Done

echo.
echo [4/5] Checking Arabic text support...
python -c "from PIL import features; print('Raqm:', features.check('raqm'))"

echo.
echo [5/5] Loading .env...
if not exist .env (
    echo .env not found! Copy .env.example to .env and fill it.
    pause
    exit /b 1
)
for /f "tokens=*" %%a in (.env) do (
    set %%a 2>nul
)

echo.
echo ============================================
echo    LAUNCHING BOT... Press Ctrl+C to stop
echo ============================================
echo.

python bot.py
pause
