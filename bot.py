"""
4Ever Telegram Bot — full pipeline with reverse mode + quality control.
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
from news_scout import (
    scout_news, download_image, reverse_scout, fetch_url_content,
)

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


def sanitize_badge(text):
    """Keep only ASCII letters/digits/punct for English-only fonts."""
    if not text:
        return ""
    cleaned = re.sub(r"[^\x20-\x7E\u2022\u00b7]", "", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def base_config(main_image_path, news):
    return {
        "page": {"name": "4Ever", "logo_text": "4EVER", "primary_color": "#00d4ff"},
        "background": {"file": "backgrounds/cosmic_purple.png", "darken_amount": 0.25},
        "main_asset": {
            "file": main_image_path, "corner_radius": 32,
            "glow_color": "#a855f7", "glow_intensity": 180,
        },
        "headline": {
            "line1": news["headline_line1"],
            "line2_arabic": news.get("headline_line2_ar", ""),
            "line2_english": news.get("headline_line2_en", ""),
            "font_size": 42,
            "highlight_color": "#00d4ff",
        },
        "source_logo": {"type": news.get("source", "google")},
        "trend_indicator": {"show": True, "color": "#10b981"},
        "live_badge": {
            "show": bool(sanitize_badge(news.get("live_badge", ""))),
            "text": sanitize_badge(news.get("live_badge", "")),
        },
        "product_badge": {
            "show": bool(sanitize_badge(news.get("product_badge", ""))),
            "text": sanitize_badge(news.get("product_badge", "")),
        },
        "socials": {"show": True, "icons": ["facebook", "instagram", "x"]},
        "decorations": {"corner_brackets": True, "decorative_line": True},
        "output": {"size": 1080, "filename": "post.png", "quality": 95},
    }


async def cmd_start(update, ctx):
    msg = (
        "🌌 *مرحباً بك في بوت 4Ever* 🌌\n\n"
        "أنا بوتك الذكي لتوليد منشورات تقنية احترافية.\n\n"
        "✨ *الأوضاع المتاحة:*\n\n"
        "🎯 *الوضع التلقائي:*\n"
        "• `منشور` ← خبر تقني عشوائي\n"
        f"• `منشور 3` ← عدة منشورات (حد {MAX_POSTS})\n\n"
        "🔄 *الوضع العكسي:*\n"
        "• أرسل أي رابط خبر\n"
        "• أرسل وصف خبر بالكلمات\n"
        "• أرسل صورة لقطة شاشة + كابشن\n"
        "→ يولّد منشور 4Ever من المحتوى!\n\n"
        "🚀 جرّب الآن: أرسل `منشور`"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_help(update, ctx):
    msg = (
        "📖 الأوامر:\n\n"
        "🎯 توليد تلقائي:\n"
        "• منشور أو /post ← منشور واحد\n"
        f"• منشور N ← N منشورات (1-{MAX_POSTS})\n\n"
        "🔄 الوضع العكسي:\n"
        "• أرسل رابط خبر → يصير منشور 4Ever\n"
        "• أرسل وصف خبر (>20 حرف) → يصير منشور\n"
        "• أرسل صورة مع كابشن → يستخدمها\n\n"
        "⏱ وقت التوليد: ~30 ثانية"
    )
    await update.message.reply_text(msg)


async def cmd_status(update, ctx):
    has_gemini = bool(os.getenv("GEMINI_API_KEY"))
    bg_exists = (ROOT / "backgrounds" / "cosmic_purple.png").exists()
    fonts_exist = (ROOT / "fonts" / "Cairo.ttf").exists()
    msg = (
        "🔍 حالة البوت\n\n"
        f"• Gemini API: {'✅' if has_gemini else '❌'}\n"
        f"• الخلفيات: {'✅' if bg_exists else '❌'}\n"
        f"• الخطوط: {'✅' if fonts_exist else '❌'}\n"
        f"• الحد الأقصى: {MAX_POSTS}/طلب\n"
        f"• Render: {RENDER_URL or 'local'}\n"
        "• الإصدار: 2.0.0 (مع الوضع العكسي)"
    )
    await update.message.reply_text(msg)


def parse_count(text):
    text = (text or "").strip().lower()
    text = text.replace("/post", "").replace("منشور", "").strip()
    ar_to_en = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
    text = text.translate(ar_to_en)
    m = re.search(r"\d+", text)
    if m:
        return max(1, min(int(m.group()), MAX_POSTS))
    return 1


async def render_and_send_post(update, news, idx, total, progress):
    """Take a news dict and render it then send to user."""
    loop = asyncio.get_event_loop()
    await progress.edit_text(f"🔄 ({idx}/{total}) - جاري الحصول على الصورة...")

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False,
                                     dir=str(OUTPUT_DIR)) as tmp:
        img_path = tmp.name

    try:
        await loop.run_in_executor(None, download_image, news, img_path)
    except Exception as e:
        logger.warning(f"Image acquisition failed: {e}, using gradient placeholder")
        from PIL import Image, ImageDraw, ImageFilter
        img = Image.new("RGB", (1280, 720), (15, 15, 35))
        d = ImageDraw.Draw(img)
        for r in range(400, 0, -20):
            alpha = int(50 * (r / 400))
            d.ellipse((640 - r, 360 - r, 640 + r, 360 + r),
                      fill=(40 + alpha // 3, 30, 60 + alpha // 2))
        img = img.filter(ImageFilter.GaussianBlur(10))
        img.save(img_path)

    await progress.edit_text(f"🔄 ({idx}/{total}) - جاري تصميم المنشور...")

    out_path = str(OUTPUT_DIR / f"post_{idx}_{os.getpid()}.png")
    cfg = base_config(img_path, news)
    await loop.run_in_executor(None, generate_post, cfg, out_path, idx)

    caption = news.get("caption", "")
    short_caption = caption[:1020] + "..." if len(caption) > 1024 else caption

    with open(out_path, "rb") as f:
        await update.message.reply_photo(photo=f, caption=short_caption)

    if len(caption) > 1024:
        await update.message.reply_text(f"📝 الكابشن الكامل:\n\n{caption}")

    if news.get("source_url"):
        await update.message.reply_text(
            f"🔗 المصدر: {news['source_url']}",
            disable_web_page_preview=True
        )

    await progress.delete()
    for p in [img_path, out_path]:
        try: os.unlink(p)
        except: pass


async def generate_and_send_one(update, idx, total):
    progress = await update.message.reply_text(
        f"🔄 *جاري التوليد ({idx}/{total})...*\n🔍 البحث عن آخر ترند تقني...",
        parse_mode="Markdown"
    )
    try:
        loop = asyncio.get_event_loop()
        news = await loop.run_in_executor(None, scout_news)
        await render_and_send_post(update, news, idx, total, progress)
    except Exception as e:
        logger.error(f"Error: {e}\n{traceback.format_exc()}")
        await progress.edit_text(
            f"❌ فشل توليد المنشور {idx}/{total}\n\nالسبب: {str(e)[:200]}"
        )


async def cmd_post(update, ctx):
    text = update.message.text or ""
    count = parse_count(text)
    await update.message.reply_text(
        f"🚀 بدء التوليد\nعدد المنشورات: {count}\n⏱ المتوقّع: ~{count * 30} ثانية"
    )
    for i in range(1, count + 1):
        await generate_and_send_one(update, i, count)
    if count > 1:
        await update.message.reply_text(f"✅ تم توليد {count} منشورات!")


# ─── Reverse Mode ────────────────────────────────────────────

URL_PATTERN = re.compile(r'https?://[^\s]+')


async def handle_reverse_url(update, ctx, url, full_text):
    progress = await update.message.reply_text(
        f"🔄 *الوضع العكسي مفعّل*\n📥 جاري قراءة الرابط...",
        parse_mode="Markdown"
    )
    try:
        loop = asyncio.get_event_loop()
        content = await loop.run_in_executor(None, fetch_url_content, url)
        if content.get("error"):
            await progress.edit_text(f"❌ فشلت قراءة الرابط: {content['error']}")
            return

        user_extras = full_text.replace(url, "").strip()
        combined_parts = [f"URL: {url}"]
        if content.get("title"): combined_parts.append(f"Title: {content['title']}")
        if content.get("description"): combined_parts.append(f"Description: {content['description']}")
        if content.get("body_text"): combined_parts.append(f"Article body: {content['body_text']}")
        if user_extras: combined_parts.append(f"User context: {user_extras}")
        combined = "\n".join(combined_parts)

        await progress.edit_text(
            f"🔄 الوضع العكسي\n"
            f"✅ قرأت المقال: {content.get('title','بدون عنوان')[:50]}\n"
            f"🤖 جاري إعادة الصياغة..."
        )

        news = await loop.run_in_executor(None, reverse_scout, combined)
        news["source_url"] = url
        await render_and_send_post(update, news, 1, 1, progress)
    except Exception as e:
        logger.error(f"Reverse URL failed: {e}\n{traceback.format_exc()}")
        await progress.edit_text(f"❌ فشل: {str(e)[:200]}")


async def handle_reverse_text(update, ctx, text):
    progress = await update.message.reply_text(
        f"🔄 *الوضع العكسي*\n🤖 جاري تحويل النص لمنشور...",
        parse_mode="Markdown"
    )
    try:
        loop = asyncio.get_event_loop()
        news = await loop.run_in_executor(None, reverse_scout, text)
        await render_and_send_post(update, news, 1, 1, progress)
    except Exception as e:
        logger.error(f"Reverse text failed: {e}\n{traceback.format_exc()}")
        await progress.edit_text(f"❌ فشل: {str(e)[:200]}")


async def handle_photo(update, ctx):
    """User sent a screenshot/photo - use caption as news source."""
    caption = (update.message.caption or "").strip()
    if not caption or len(caption) < 10:
        await update.message.reply_text(
            "📸 وصلتني الصورة. لكن أحتاج وصفاً مع الصورة (في الكابشن) "
            "لأعرف الخبر. أعد الإرسال مع كابشن يصف الخبر."
        )
        return

    progress = await update.message.reply_text(
        f"🔄 *الوضع العكسي - صورة*\n🤖 جاري تحويل المحتوى لمنشور...",
        parse_mode="Markdown"
    )
    try:
        loop = asyncio.get_event_loop()
        # Use caption + indicate it came from a screenshot
        prompt_text = f"Screenshot caption from user: {caption}\n\nGenerate a 4Ever post based on this news."
        news = await loop.run_in_executor(None, reverse_scout, prompt_text)
        await render_and_send_post(update, news, 1, 1, progress)
    except Exception as e:
        logger.error(f"Photo handler failed: {e}\n{traceback.format_exc()}")
        await progress.edit_text(f"❌ فشل: {str(e)[:200]}")


async def handle_text_router(update, ctx):
    """Route any text message based on content."""
    text = (update.message.text or "").strip()

    if not text:
        return

    # Explicit post command
    if text.startswith("منشور") or text.lower().startswith("/post") or text.lower() == "post":
        await cmd_post(update, ctx)
        return

    # Contains URL → reverse mode
    urls = URL_PATTERN.findall(text)
    if urls:
        await handle_reverse_url(update, ctx, urls[0], text)
        return

    # Long descriptive text → reverse mode
    if len(text) > 20:
        await handle_reverse_text(update, ctx, text)
        return

    # Short text - just guide them
    await update.message.reply_text(
        "💡 لتوليد منشور، أرسل:\n"
        "• منشور — خبر تقني عشوائي\n"
        "• أي رابط خبر — يحوّل لمنشور\n"
        "• وصف خبر >20 حرف — يحوّل لمنشور"
    )


async def error_handler(update, ctx):
    logger.error(f"Update {update} caused error: {ctx.error}", exc_info=ctx.error)


def main():
    logger.info("🤖 Starting 4Ever Bot v2.0 (with reverse mode)...")
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("post", cmd_post))

    # Photos with captions → reverse mode
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    # All text messages → router (handles منشور / URLs / long text)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_router))

    app.add_error_handler(error_handler)

    if RENDER_URL:
        webhook_url = f"{RENDER_URL}/webhook"
        logger.info(f"✅ Webhook mode: {webhook_url}")
        app.run_webhook(
            listen="0.0.0.0", port=PORT, url_path="webhook",
            webhook_url=webhook_url, allowed_updates=Update.ALL_TYPES
        )
    else:
        logger.info("✅ Polling mode (local dev)")
        app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
