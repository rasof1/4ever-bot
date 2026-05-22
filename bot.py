"""
4Ever Telegram Bot — Web Service version (Free Tier compatible)
Uses webhook + lightweight HTTP server instead of polling.

Required env vars:
  TELEGRAM_BOT_TOKEN
  GEMINI_API_KEY
  PORT (auto-set by Render)
  RENDER_EXTERNAL_URL (auto-set by Render, e.g. https://4ever-bot.onrender.com)
"""

import os
import re
import asyncio
import logging
import tempfile
import traceback
from pathlib import Path

from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes
)

from post_generator import generate_post
from news_scout import scout_news, download_image

logging.basicConfig(
    format='%(asctime)s | %(name)s | %(levelname)s | %(message)s',
    level=logging.INFO
)
logger = logging.getLogger("4ever_bot")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN not set")

PORT = int(os.getenv("PORT", "10000"))
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL", "")
MAX_POSTS = 5
ROOT = Path(__file__).parent
OUTPUT_DIR = ROOT / "output"
OUTPUT_DIR.mkdir(exist_ok=True)


def base_config(main_image_path, news):
    return {
        "page": {
            "name": "4Ever",
            "logo_text": "4EVER",
            "primary_color": "#00d4ff",
        },
        "background": {
            "file": "backgrounds/cosmic_purple.png",
            "darken_amount": 0.25,
        },
        "main_asset": {
            "file": main_image_path,
            "corner_radius": 32,
            "glow_color": "#a855f7",
            "glow_intensity": 180,
        },
        "headline": {
            "line1": news["headline_line1"],
            "line2_arabic": news.get("headline_line2_ar", ""),
            "line2_english": news.get("headline_line2_en", ""),
            "font_size": 44,
            "highlight_color": "#00d4ff",
        },
        "source_logo": {"type": news.get("source", "google")},
        "trend_indicator": {"show": True, "color": "#10b981"},
        "live_badge": {
            "show": bool(news.get("live_badge")),
            "text": news.get("live_badge", ""),
        },
        "product_badge": {
            "show": bool(news.get("product_badge")),
            "text": news.get("product_badge", ""),
        },
        "socials": {"show": True, "icons": ["facebook", "instagram", "x"]},
        "decorations": {"corner_brackets": True, "decorative_line": True},
        "output": {"size": 1080, "filename": "post.png", "quality": 95},
    }


async def cmd_start(update, ctx):
    msg = (
        "🌌 *مرحباً بك في بوت 4Ever* 🌌\n\n"
        "أنا بوتك الذكي لتوليد منشورات تقنية احترافية.\n\n"
        "✨ *الأوامر المتاحة:*\n"
        "• `/post` أو `منشور` ← منشور واحد\n"
        f"• `/post 3` أو `منشور 3` ← عدة منشورات (حد أقصى {MAX_POSTS})\n"
        "• `/help` ← عرض المساعدة\n"
        "• `/status` ← حالة البوت\n\n"
        "🚀 *جرّب الآن:* أرسل `منشور`"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_help(update, ctx):
    msg = (
        "📖 *دليل الاستخدام*\n\n"
        "*الأوامر:*\n"
        "• `/post` ← منشور واحد\n"
        f"• `/post N` ← N منشورات (1-{MAX_POSTS})\n"
        "• `منشور` ← نفس الأمر بالعربية\n"
        "• `منشور N` ← N منشورات\n\n"
        "*ماذا يفعل البوت؟*\n"
        "1️⃣ يبحث عن آخر ترند تقني\n"
        "2️⃣ ينزّل صورة حقيقية من المصدر\n"
        "3️⃣ يصمّم منشور 1080×1080\n"
        "4️⃣ يكتب كابشن عربي جاهز\n\n"
        "⏱ *وقت التوليد:* ~30 ثانية لكل منشور"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_status(update, ctx):
    has_gemini = bool(os.getenv("GEMINI_API_KEY"))
    bg_exists = (ROOT / "backgrounds" / "cosmic_purple.png").exists()
    fonts_exist = (ROOT / "fonts" / "Cairo.ttf").exists()
    msg = (
        "🔍 *حالة البوت*\n\n"
        f"• Gemini API: {'✅' if has_gemini else '❌'}\n"
        f"• الخلفيات: {'✅' if bg_exists else '❌'}\n"
        f"• الخطوط: {'✅' if fonts_exist else '❌'}\n"
        f"• الحد الأقصى: {MAX_POSTS} منشورات/طلب\n"
        f"• Render URL: {RENDER_URL or 'local'}\n"
        "• الإصدار: 1.0.0"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


def parse_count(text):
    text = (text or "").strip().lower()
    text = text.replace("/post", "").replace("منشور", "").strip()
    ar_to_en = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
    text = text.translate(ar_to_en)
    m = re.search(r"\d+", text)
    if m:
        return max(1, min(int(m.group()), MAX_POSTS))
    return 1


async def generate_and_send_one(update, idx, total):
    progress = await update.message.reply_text(
        f"🔄 *جاري التوليد ({idx}/{total})...*\n"
        f"🔍 البحث عن آخر ترند تقني...",
        parse_mode="Markdown"
    )

    try:
        loop = asyncio.get_event_loop()
        news = await loop.run_in_executor(None, scout_news)

        await progress.edit_text(
            f"🔄 *جاري التوليد ({idx}/{total})...*\n"
            f"✅ وجدت الخبر\n"
            f"📥 جاري تنزيل الصورة...",
            parse_mode="Markdown"
        )

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False,
                                         dir=ROOT / "output") as tmp:
            img_path = tmp.name
        try:
            await loop.run_in_executor(None, download_image,
                                       news["image_url"], img_path)
        except Exception as e:
            logger.warning(f"Image download failed: {e}, using branded placeholder")
            from PIL import Image, ImageDraw, ImageFilter
            # Create a nice gradient placeholder with 4Ever branding hint
            img = Image.new("RGB", (1280, 720), (15, 15, 35))
            d = ImageDraw.Draw(img)
            # Add gradient circles
            for r in range(400, 0, -20):
                alpha = int(50 * (r / 400))
                d.ellipse((640 - r, 360 - r, 640 + r, 360 + r),
                          fill=(40 + alpha // 3, 30, 60 + alpha // 2))
            img = img.filter(ImageFilter.GaussianBlur(10))
            img.save(img_path)

        await progress.edit_text(
            f"🔄 *جاري التوليد ({idx}/{total})...*\n"
            f"✅ تم تنزيل الصورة\n"
            f"🎨 جاري تصميم المنشور...",
            parse_mode="Markdown"
        )

        out_path = str(ROOT / "output" / f"post_{idx}_{os.getpid()}.png")
        cfg = base_config(img_path, news)
        await loop.run_in_executor(None, generate_post, cfg, out_path, idx)

        caption = news.get("caption", "")
        short_caption = caption[:1020] + "..." if len(caption) > 1024 else caption

        with open(out_path, "rb") as f:
            await update.message.reply_photo(photo=f, caption=short_caption)

        if len(caption) > 1024:
            await update.message.reply_text(
                f"📝 *الكابشن الكامل:*\n\n{caption}",
                parse_mode="Markdown"
            )

        if news.get("source_url"):
            await update.message.reply_text(
                f"🔗 *المصدر:* {news['source_url']}",
                parse_mode="Markdown", disable_web_page_preview=True
            )

        await progress.delete()
        for p in [img_path, out_path]:
            try: os.unlink(p)
            except: pass

    except Exception as e:
        logger.error(f"Error: {e}\n{traceback.format_exc()}")
        await progress.edit_text(
            f"❌ *فشل توليد المنشور {idx}/{total}*\n\nالسبب: `{str(e)[:200]}`",
            parse_mode="Markdown"
        )


async def cmd_post(update, ctx):
    text = update.message.text or ""
    count = parse_count(text)
    await update.message.reply_text(
        f"🚀 *بدء التوليد*\nعدد المنشورات: {count}\n"
        f"⏱ الوقت المتوقّع: ~{count * 30} ثانية",
        parse_mode="Markdown"
    )
    for i in range(1, count + 1):
        await generate_and_send_one(update, i, count)
    if count > 1:
        await update.message.reply_text(
            f"✅ *تم توليد {count} منشورات بنجاح!*",
            parse_mode="Markdown"
        )


async def handle_arabic(update, ctx):
    text = (update.message.text or "").strip()
    if text.startswith("منشور"):
        await cmd_post(update, ctx)


async def error_handler(update, ctx):
    logger.error(f"Update {update} error: {ctx.error}", exc_info=ctx.error)


def main():
    logger.info("🤖 Starting 4Ever Bot...")
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("post", cmd_post))
    app.add_handler(MessageHandler(
        filters.TEXT & filters.Regex(r"^منشور"), handle_arabic
    ))
    app.add_error_handler(error_handler)

    if RENDER_URL:
        # Production: run as webhook (Web Service mode)
        webhook_url = f"{RENDER_URL}/webhook"
        logger.info(f"✅ Webhook mode: {webhook_url}")
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path="webhook",
            webhook_url=webhook_url,
            allowed_updates=Update.ALL_TYPES
        )
    else:
        # Local: polling mode
        logger.info("✅ Polling mode (local dev)")
        app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
