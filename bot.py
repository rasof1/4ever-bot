"""4Ever Telegram Bot v2.3 — reverse mode with custom image upload option."""

import os
import re
import asyncio
import logging
import tempfile
import traceback
from pathlib import Path

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)

from post_generator import generate_post
from news_scout import (
    scout_news, download_image, reverse_scout, fetch_url_content,
    acquire_validated_image,
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

# In-memory state: user_id → {"news": ..., "awaiting_image": bool}
PENDING_NEWS = {}


def sanitize_badge(text):
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
        "✨ *الأوضاع:*\n\n"
        "🎯 الوضع التلقائي:\n"
        "• `منشور` ← خبر تقني عشوائي\n"
        f"• `منشور 3` ← عدة منشورات (حد {MAX_POSTS})\n\n"
        "🔄 الوضع العكسي (جديد!):\n"
        "• أرسل رابط خبر/Facebook/Twitter\n"
        "• أرسل وصف خبر\n"
        "• أرسل صورة + كابشن\n"
        "→ سيسألك: هل عندك صورة؟ (نعم/لا)\n\n"
        "🚀 جرّب الآن: أرسل `منشور`"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_help(update, ctx):
    msg = (
        "📖 الأوامر:\n\n"
        "🎯 توليد تلقائي:\n"
        "• منشور / /post ← منشور واحد\n"
        f"• منشور N ← N منشورات (1-{MAX_POSTS})\n\n"
        "🔄 الوضع العكسي:\n"
        "• أرسل رابط خبر\n"
        "• أرسل وصف خبر (>20 حرف)\n"
        "• أرسل صورة + كابشن\n"
        "→ يسألك: 📸 عندك صورة؟\n"
        "  • نعم → ارفع صورتك\n"
        "  • لا → يبحث/يولّد تلقائياً\n\n"
        "/cancel ← إلغاء العملية المعلّقة"
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
        f"• Render: {RENDER_URL or 'local'}\n"
        f"• Pending: {len(PENDING_NEWS)}\n"
        "• الإصدار: 2.3.0 (مع رفع صورة مخصصة)"
    )
    await update.message.reply_text(msg)


async def cmd_cancel(update, ctx):
    user_id = update.effective_user.id
    if user_id in PENDING_NEWS:
        PENDING_NEWS.pop(user_id)
        await update.message.reply_text("✅ تم الإلغاء")
    else:
        await update.message.reply_text("لا توجد عملية معلّقة")


def parse_count(text):
    text = (text or "").strip().lower()
    text = text.replace("/post", "").replace("منشور", "").strip()
    ar_to_en = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
    text = text.translate(ar_to_en)
    m = re.search(r"\d+", text)
    if m:
        return max(1, min(int(m.group()), MAX_POSTS))
    return 1


async def render_with_image(message, news, img_path, progress):
    """Render post using a specific image path (no auto-acquisition)."""
    loop = asyncio.get_event_loop()
    await progress.edit_text("🎨 جاري تصميم المنشور...")

    out_path = str(OUTPUT_DIR / f"post_{os.getpid()}_{id(news)}.png")
    cfg = base_config(img_path, news)
    await loop.run_in_executor(None, generate_post, cfg, out_path)

    caption = news.get("caption", "")

    if len(caption) <= 1024:
        with open(out_path, "rb") as f:
            await message.reply_photo(photo=f, caption=caption)
    else:
        with open(out_path, "rb") as f:
            await message.reply_photo(photo=f)
        for chunk_start in range(0, len(caption), 4000):
            await message.reply_text(caption[chunk_start:chunk_start + 4000])

    if news.get("source_url"):
        await message.reply_text(
            f"🔗 المصدر: {news['source_url']}",
            disable_web_page_preview=True
        )

    try:
        await progress.delete()
    except Exception:
        pass
    for p in [img_path, out_path]:
        try: os.unlink(p)
        except: pass


async def render_and_send_post(message, news, idx, total, progress):
    """Full pipeline: auto-acquire validated image + render + send."""
    loop = asyncio.get_event_loop()
    await progress.edit_text(f"🔄 ({idx}/{total}) - جاري البحث عن صورة مناسبة...")

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False,
                                     dir=str(OUTPUT_DIR)) as tmp:
        img_path = tmp.name

    try:
        await loop.run_in_executor(None, acquire_validated_image, news, img_path, 3)
        if not os.path.exists(img_path) or os.path.getsize(img_path) < 1000:
            raise ValueError("No valid image acquired")
    except Exception as e:
        logger.warning(f"Image acquisition failed: {e}, using placeholder")
        from PIL import Image, ImageDraw, ImageFilter
        img = Image.new("RGB", (1280, 720), (15, 15, 35))
        d = ImageDraw.Draw(img)
        for r in range(400, 0, -20):
            alpha = int(50 * (r / 400))
            d.ellipse((640 - r, 360 - r, 640 + r, 360 + r),
                      fill=(40 + alpha // 3, 30, 60 + alpha // 2))
        img = img.filter(ImageFilter.GaussianBlur(10))
        img.save(img_path)

    await render_with_image(message, news, img_path, progress)


async def generate_and_send_one(update, idx, total):
    progress = await update.message.reply_text(
        f"🔄 *جاري التوليد ({idx}/{total})...*\n🔍 البحث عن آخر ترند تقني...",
        parse_mode="Markdown"
    )
    try:
        loop = asyncio.get_event_loop()
        news = await loop.run_in_executor(None, scout_news)
        await render_and_send_post(update.message, news, idx, total, progress)
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


async def ask_about_image(message, ctx, news, progress):
    """After news is generated, ask user if they have a custom image."""
    user_id = message.from_user.id

    PENDING_NEWS[user_id] = {
        "news": news,
        "chat_id": message.chat_id,
        "awaiting_image": False,
    }

    keyboard = [[
        InlineKeyboardButton("📸 نعم، عندي صورة", callback_data=f"img_yes_{user_id}"),
        InlineKeyboardButton("🤖 لا، اختر أنت", callback_data=f"img_no_{user_id}"),
    ]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    preview = (
        f"✅ *جاهز للتصميم!*\n\n"
        f"📰 العنوان: {news.get('headline_line1','')[:60]}\n"
        f"🏢 المصدر: {news.get('source','')}\n\n"
        f"🖼️ *هل عندك صورة مناسبة تريد استخدامها؟*\n\n"
        f"• نعم → ارفع صورتك\n"
        f"• لا → سأبحث/أولّد صورة مناسبة"
    )
    await progress.edit_text(preview, parse_mode="Markdown", reply_markup=reply_markup)


async def handle_image_choice(update, ctx):
    """Callback: user clicked Yes/No on the image question."""
    query = update.callback_query
    await query.answer()

    data = query.data
    parts = data.split("_")
    if len(parts) < 3:
        return
    choice = parts[1]
    user_id_str = parts[2]

    try:
        user_id = int(user_id_str)
    except ValueError:
        return

    if query.from_user.id != user_id:
        await query.answer("⚠️ هذا الزر لمستخدم آخر", show_alert=True)
        return

    pending = PENDING_NEWS.get(user_id)
    if not pending:
        await query.edit_message_text("⚠️ انتهت المهلة. أرسل مرة أخرى.")
        return

    news = pending["news"]

    if choice == "yes":
        PENDING_NEWS[user_id]["awaiting_image"] = True
        await query.edit_message_text(
            f"📸 *تمام، ارفع الصورة الآن*\n\n"
            f"العنوان: {news.get('headline_line1','')[:60]}\n\n"
            f"⏳ بانتظار صورتك...\n"
            f"لإلغاء: /cancel",
            parse_mode="Markdown"
        )
    elif choice == "no":
        await query.edit_message_text(
            f"🤖 *سأبحث/أولّد صورة مناسبة...*\n\n"
            f"العنوان: {news.get('headline_line1','')[:60]}",
            parse_mode="Markdown"
        )
        PENDING_NEWS.pop(user_id, None)
        # query.message is the bot's message - use it for editing progress
        # but reply with photo on the original chat
        await render_and_send_post(query.message, news, 1, 1, query.message)


async def handle_reverse_url(update, ctx, url, full_text):
    progress = await update.message.reply_text(
        "🔄 *الوضع العكسي*\n📥 جاري قراءة الرابط...",
        parse_mode="Markdown"
    )
    try:
        loop = asyncio.get_event_loop()
        content_data = await loop.run_in_executor(None, fetch_url_content, url)
        if content_data.get("error"):
            await progress.edit_text(f"❌ فشلت قراءة الرابط: {content_data['error']}")
            return

        user_extras = full_text.replace(url, "").strip()
        combined_parts = [f"URL: {url}"]
        if content_data.get("title"): combined_parts.append(f"Title: {content_data['title']}")
        if content_data.get("description"): combined_parts.append(f"Description: {content_data['description']}")
        if content_data.get("body_text"): combined_parts.append(f"Article body: {content_data['body_text']}")
        if user_extras: combined_parts.append(f"User context: {user_extras}")
        combined = "\n".join(combined_parts)

        await progress.edit_text(
            f"🔄 الوضع العكسي\n"
            f"✅ قرأت المقال: {content_data.get('title','بدون عنوان')[:50]}\n"
            f"🤖 جاري إعادة الصياغة..."
        )

        news = await loop.run_in_executor(None, reverse_scout, combined)
        news["source_url"] = url
        await ask_about_image(update.message, ctx, news, progress)
    except Exception as e:
        logger.error(f"Reverse URL failed: {e}\n{traceback.format_exc()}")
        await progress.edit_text(f"❌ فشل: {str(e)[:200]}")


async def handle_reverse_text(update, ctx, text):
    progress = await update.message.reply_text(
        "🔄 *الوضع العكسي*\n🤖 جاري تحويل النص لمنشور...",
        parse_mode="Markdown"
    )
    try:
        loop = asyncio.get_event_loop()
        news = await loop.run_in_executor(None, reverse_scout, text)
        await ask_about_image(update.message, ctx, news, progress)
    except Exception as e:
        logger.error(f"Reverse text failed: {e}\n{traceback.format_exc()}")
        await progress.edit_text(f"❌ فشل: {str(e)[:200]}")


async def handle_photo(update, ctx):
    """Photo handler — two cases:
    1. User is uploading a custom image for pending news
    2. User sent a screenshot with caption (extract news from caption)
    """
    user_id = update.effective_user.id
    pending = PENDING_NEWS.get(user_id)

    # Case 1: User uploading custom image for pending news
    if pending and pending.get("awaiting_image"):
        news = pending["news"]
        progress = await update.message.reply_text(
            "📸 *تم استلام صورتك! جاري التصميم...*",
            parse_mode="Markdown"
        )
        try:
            photo = update.message.photo[-1]  # Highest resolution
            tg_file = await ctx.bot.get_file(photo.file_id)
            user_img_path = str(OUTPUT_DIR / f"user_{user_id}_{os.getpid()}.jpg")
            await tg_file.download_to_drive(user_img_path)
            logger.info(f"📥 Downloaded user image: {user_img_path}")

            PENDING_NEWS.pop(user_id, None)
            await render_with_image(update.message, news, user_img_path, progress)
        except Exception as e:
            logger.error(f"Custom image handler failed: {e}\n{traceback.format_exc()}")
            await progress.edit_text(f"❌ فشل معالجة الصورة: {str(e)[:200]}")
            PENDING_NEWS.pop(user_id, None)
        return

    # Case 2: Screenshot with caption
    caption = (update.message.caption or "").strip()
    if not caption or len(caption) < 10:
        await update.message.reply_text(
            "📸 وصلتني الصورة. لكن أحتاج وصفاً مع الصورة (في الكابشن) "
            "لأعرف الخبر. أعد الإرسال مع كابشن يصف الخبر."
        )
        return

    progress = await update.message.reply_text(
        "🔄 *الوضع العكسي - صورة*\n🤖 جاري تحويل المحتوى لمنشور...",
        parse_mode="Markdown"
    )
    try:
        loop = asyncio.get_event_loop()
        prompt_text = f"Screenshot caption from user: {caption}\n\nGenerate a 4Ever post based on this news."
        news = await loop.run_in_executor(None, reverse_scout, prompt_text)
        await ask_about_image(update.message, ctx, news, progress)
    except Exception as e:
        logger.error(f"Photo handler failed: {e}\n{traceback.format_exc()}")
        await progress.edit_text(f"❌ فشل: {str(e)[:200]}")


async def handle_text_router(update, ctx):
    text = (update.message.text or "").strip()
    if not text:
        return

    if text.startswith("منشور") or text.lower().startswith("/post") or text.lower() == "post":
        await cmd_post(update, ctx)
        return

    urls = URL_PATTERN.findall(text)
    if urls:
        await handle_reverse_url(update, ctx, urls[0], text)
        return

    if len(text) > 20:
        await handle_reverse_text(update, ctx, text)
        return

    await update.message.reply_text(
        "💡 لتوليد منشور:\n"
        "• منشور — خبر تقني عشوائي\n"
        "• أي رابط خبر — يحوّل لمنشور\n"
        "• وصف خبر >20 حرف — يحوّل لمنشور"
    )


async def error_handler(update, ctx):
    logger.error(f"Update caused error: {ctx.error}", exc_info=ctx.error)


def main():
    logger.info("🤖 Starting 4Ever Bot v2.3...")
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("post", cmd_post))
    app.add_handler(CommandHandler("cancel", cmd_cancel))

    # 🆕 Callback query handler for inline buttons
    app.add_handler(CallbackQueryHandler(handle_image_choice, pattern=r"^img_(yes|no)_\d+$"))

    # Photos → handle_photo (which checks for pending state)
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    # Text → router
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
