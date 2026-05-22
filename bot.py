"""
4Ever Telegram Bot
─────────────────
Commands:
  /start          → Welcome message
  /help           → Show all commands
  /post           → Generate 1 post
  /post N         → Generate N posts (max 5)
  منشور           → Same as /post
  منشور N         → Same as /post N
  /status         → Bot health check

Required env vars:
  TELEGRAM_BOT_TOKEN
  ANTHROPIC_API_KEY
"""

import os
import re
import asyncio
import logging
import tempfile
import traceback
from pathlib import Path

from telegram import Update, InputMediaPhoto
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes
)

from post_generator import generate_post
from news_scout import scout_news, download_image

# ─── Setup ──────────────────────────────────────────────────────
logging.basicConfig(
    format='%(asctime)s | %(name)s | %(levelname)s | %(message)s',
    level=logging.INFO
)
logger = logging.getLogger("4ever_bot")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN not set")

MAX_POSTS = 5
ROOT = Path(__file__).parent


def base_config(main_image_path: str, news: dict) -> dict:
    """Build a config dict from news data + main image."""
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
        "source_logo": {
            "type": news.get("source", "google"),
        },
        "trend_indicator": {"show": True, "color": "#10b981"},
        "live_badge": {
            "show": bool(news.get("live_badge")),
            "text": news.get("live_badge", ""),
        },
        "product_badge": {
            "show": bool(news.get("product_badge")),
            "text": news.get("product_badge", ""),
        },
        "socials": {
            "show": True,
            "icons": ["facebook", "instagram", "x"],
        },
        "decorations": {
            "corner_brackets": True,
            "decorative_line": True,
        },
        "output": {"size": 1080, "filename": "post.png", "quality": 95},
    }


# ─── Handlers ───────────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = (
        "🌌 *مرحباً بك في بوت 4Ever* 🌌\n\n"
        "أنا بوتك الذكي لتوليد منشورات تقنية احترافية\\.\n\n"
        "✨ *الأوامر المتاحة:*\n"
        "• `/post` أو `منشور` → منشور واحد\n"
        f"• `/post 3` أو `منشور 3` → عدة منشورات \\(حد أقصى {MAX_POSTS}\\)\n"
        "• `/help` → عرض المساعدة\n"
        "• `/status` → حالة البوت\n\n"
        "🚀 *جرّب الآن:* أرسل `منشور`"
    )
    await update.message.reply_text(msg, parse_mode="MarkdownV2")


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = (
        "📖 *دليل الاستخدام*\n\n"
        "*الأوامر:*\n"
        "• `/post` — منشور واحد\n"
        f"• `/post N` — N منشورات (1-{MAX_POSTS})\n"
        "• `منشور` — نفس الأمر بالعربية\n"
        f"• `منشور N` — N منشورات\n\n"
        "*ماذا يفعل البوت؟*\n"
        "1️⃣ يبحث عن آخر ترند تقني\n"
        "2️⃣ ينزّل صورة حقيقية من المصدر\n"
        "3️⃣ يصمّم منشور 1080×1080\n"
        "4️⃣ يكتب كابشن عربي جاهز\n\n"
        f"⏱ *وقت التوليد:* ~30 ثانية لكل منشور"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    has_gemini = bool(os.getenv("GEMINI_API_KEY"))
    bg_exists = (ROOT / "backgrounds" / "cosmic_purple.png").exists()
    fonts_exist = (ROOT / "fonts" / "Cairo.ttf").exists()
    msg = (
        "🔍 *حالة البوت*\n\n"
        f"• Gemini API: {'✅' if has_gemini else '❌'}\n"
        f"• الخلفيات: {'✅' if bg_exists else '❌'}\n"
        f"• الخطوط: {'✅' if fonts_exist else '❌'}\n"
        f"• الحد الأقصى: {MAX_POSTS} منشورات/طلب\n"
        "• الإصدار: 1.0.0"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


def parse_count(text: str) -> int:
    """Extract number from messages like 'منشور 3' or '/post 3'."""
    text = (text or "").strip().lower()
    text = text.replace("/post", "").replace("منشور", "").strip()
    # Convert Arabic-Indic numerals
    ar_to_en = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
    text = text.translate(ar_to_en)
    m = re.search(r"\d+", text)
    if m:
        n = int(m.group())
        return max(1, min(n, MAX_POSTS))
    return 1


async def generate_and_send_one(update: Update, idx: int, total: int):
    """Generate one post and send it to the user."""
    progress = await update.message.reply_text(
        f"🔄 *جاري التوليد ({idx}/{total})...*\n"
        f"🔍 البحث عن آخر ترند تقني...",
        parse_mode="Markdown"
    )

    try:
        # 1. Scout news (this is the slow step)
        loop = asyncio.get_event_loop()
        news = await loop.run_in_executor(None, scout_news)

        await progress.edit_text(
            f"🔄 *جاري التوليد ({idx}/{total})...*\n"
            f"✅ وجدت الخبر\n"
            f"📥 جاري تنزيل الصورة...",
            parse_mode="Markdown"
        )

        # 2. Download main image
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False, dir=ROOT / "output") as tmp:
            img_path = tmp.name
        try:
            await loop.run_in_executor(None, download_image, news["image_url"], img_path)
        except Exception as e:
            logger.warning(f"Image download failed: {e}, using placeholder")
            # Fallback: use a generic dark gradient
            from PIL import Image
            placeholder = Image.new("RGB", (1280, 720), (20, 20, 40))
            placeholder.save(img_path)

        await progress.edit_text(
            f"🔄 *جاري التوليد ({idx}/{total})...*\n"
            f"✅ تم تنزيل الصورة\n"
            f"🎨 جاري تصميم المنشور...",
            parse_mode="Markdown"
        )

        # 3. Generate post image
        out_path = str(ROOT / "output" / f"post_{idx}_{os.getpid()}.png")
        cfg = base_config(img_path, news)
        await loop.run_in_executor(None, generate_post, cfg, out_path, idx)

        # 4. Send to user
        caption = news.get("caption", "")
        if len(caption) > 1024:
            # Telegram caption limit
            short_caption = caption[:1020] + "..."
        else:
            short_caption = caption

        with open(out_path, "rb") as f:
            await update.message.reply_photo(
                photo=f,
                caption=short_caption
            )

        # 5. If caption was truncated, send full version
        if len(caption) > 1024:
            await update.message.reply_text(
                f"📝 *الكابشن الكامل:*\n\n{caption}",
                parse_mode="Markdown"
            )

        # 6. Source URL
        if news.get("source_url"):
            await update.message.reply_text(
                f"🔗 *المصدر:* {news['source_url']}",
                parse_mode="Markdown",
                disable_web_page_preview=True
            )

        await progress.delete()

        # Cleanup
        for p in [img_path, out_path]:
            try:
                os.unlink(p)
            except Exception:
                pass

    except Exception as e:
        logger.error(f"Error generating post {idx}: {e}\n{traceback.format_exc()}")
        await progress.edit_text(
            f"❌ *فشل توليد المنشور {idx}/{total}*\n\n"
            f"السبب: `{str(e)[:200]}`\n\n"
            "حاول مرة أخرى بعد قليل\\.",
            parse_mode="Markdown"
        )


async def cmd_post(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    count = parse_count(text)

    intro = (
        f"🚀 *بدء التوليد*\n"
        f"عدد المنشورات: {count}\n"
        f"⏱ الوقت المتوقّع: ~{count * 30} ثانية"
    )
    await update.message.reply_text(intro, parse_mode="Markdown")

    for i in range(1, count + 1):
        await generate_and_send_one(update, i, count)

    if count > 1:
        await update.message.reply_text(
            f"✅ *تم توليد {count} منشورات بنجاح!*",
            parse_mode="Markdown"
        )


async def handle_arabic_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Catch Arabic 'منشور' messages."""
    text = (update.message.text or "").strip()
    if text.startswith("منشور") or text == "منشور":
        await cmd_post(update, ctx)


async def error_handler(update: object, ctx: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Update {update} caused error: {ctx.error}", exc_info=ctx.error)


# ─── Main ───────────────────────────────────────────────────────
def main():
    logger.info("🤖 Starting 4Ever Telegram Bot...")
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("post", cmd_post))
    app.add_handler(MessageHandler(
        filters.TEXT & filters.Regex(r"^منشور"),
        handle_arabic_command
    ))

    app.add_error_handler(error_handler)

    logger.info("✅ Bot ready. Polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
