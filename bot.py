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
    acquire_validated_image, LANG_INSTRUCTIONS,
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
            "is_rtl": LANG_INSTRUCTIONS.get(news.get("_lang", "ar"), LANG_INSTRUCTIONS["ar"])["is_rtl"],
            "font_name": "Orbitron.ttf" if news.get("_lang") in ("en", "fr") else "Cairo.ttf",
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
        "• الإصدار: 2.4 (لغات متعددة + فيديو)"
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


async def render_with_image(message, news, img_path, progress, video_path=None):
    """Render post using a specific image path.
    If video_path provided, also send the original video AFTER the post.
    """
    loop = asyncio.get_event_loop()
    await progress.edit_text("🎨 جاري تصميم المنشور...")

    out_path = str(OUTPUT_DIR / f"post_{os.getpid()}_{id(news)}.png")
    cfg = base_config(img_path, news)
    await loop.run_in_executor(None, generate_post, cfg, out_path)

    caption = news.get("caption", "")

    # 📸 Send the designed post image first
    if len(caption) <= 1024:
        with open(out_path, "rb") as f:
            await message.reply_photo(photo=f, caption=caption)
    else:
        with open(out_path, "rb") as f:
            await message.reply_photo(photo=f)
        for chunk_start in range(0, len(caption), 4000):
            await message.reply_text(caption[chunk_start:chunk_start + 4000])

    # 🎬 If user provided a video, send it AFTER the post (so they have both)
    if video_path and os.path.exists(video_path):
        try:
            await message.reply_text("🎬 الفيديو الأصلي:")
            with open(video_path, "rb") as f:
                await message.reply_video(
                    video=f,
                    supports_streaming=True,
                    width=1280,
                    height=720,
                )
            logger.info(f"   ✅ Original video sent ({os.path.getsize(video_path)//1024}KB)")
        except Exception as e:
            logger.warning(f"   Failed to send original video: {e}")
            # Try as document if reply_video fails (e.g. format issue)
            try:
                with open(video_path, "rb") as f:
                    await message.reply_document(document=f, caption="الفيديو الأصلي")
            except Exception as e2:
                logger.error(f"   Also failed as document: {e2}")

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
    # Note: video_path cleanup handled by caller (handle_video)


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
    await ask_about_language(update.message, ctx, "auto", {"count": count})


# ─── Reverse Mode ────────────────────────────────────────────

URL_PATTERN = re.compile(r'https?://[^\s]+')


async def ask_about_language(message, ctx, callback_kind, payload):
    """Ask user which language for the upcoming post.
    callback_kind: 'auto' | 'url' | 'text' | 'photo_caption'
    payload: dict with data needed to continue
    """
    user_id = message.from_user.id

    # Store pending payload with chat_id (so we can reply in the right chat later)
    PENDING_NEWS[user_id] = {
        "kind": "lang_choice",
        "callback_kind": callback_kind,
        "payload": payload,
        "chat_id": message.chat_id,
        "user_id": user_id,  # explicit
    }

    keyboard = [[
        InlineKeyboardButton("🇸🇦 العربية", callback_data=f"lang_ar_{user_id}"),
        InlineKeyboardButton("🇬🇧 English", callback_data=f"lang_en_{user_id}"),
        InlineKeyboardButton("🇫🇷 Français", callback_data=f"lang_fr_{user_id}"),
    ]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    msg = (
        "🌐 *اختر لغة المنشور:*\n\n"
        "🇸🇦 العربية - منشور باللغة العربية\n"
        "🇬🇧 English - English post\n"
        "🇫🇷 Français - Publication en français"
    )
    return await message.reply_text(msg, parse_mode="Markdown", reply_markup=reply_markup)


async def handle_language_choice(update, ctx):
    """Callback when user selects a language."""
    query = update.callback_query
    await query.answer()

    parts = query.data.split("_")
    if len(parts) < 3:
        return
    lang = parts[1]  # ar, en, fr
    try:
        user_id = int(parts[2])
    except ValueError:
        return

    if query.from_user.id != user_id:
        await query.answer("⚠️ هذا الزر لمستخدم آخر", show_alert=True)
        return

    pending = PENDING_NEWS.get(user_id)
    if not pending or pending.get("kind") != "lang_choice":
        await query.edit_message_text("⚠️ انتهت المهلة. حاول مرة أخرى.")
        return

    callback_kind = pending["callback_kind"]
    payload = pending["payload"]
    chat_id = pending.get("chat_id")
    PENDING_NEWS.pop(user_id, None)

    lang_label = {"ar": "🇸🇦 العربية", "en": "🇬🇧 English", "fr": "🇫🇷 Français"}[lang]
    progress = query.message
    await progress.edit_text(f"✅ {lang_label}\n\n🔄 جاري التنفيذ...")

    # 🎯 Pass user_id + chat_id explicitly to downstream functions
    try:
        if callback_kind == "auto":
            count = payload.get("count", 1)
            for i in range(1, count + 1):
                await generate_and_send_one_with_lang(query.message, user_id, i, count, lang, ctx)
            if count > 1:
                await ctx.bot.send_message(chat_id=chat_id, text=f"✅ تم توليد {count} منشورات!")
        elif callback_kind == "url":
            await execute_reverse_url(query.message, user_id, payload["url"], payload["full_text"], lang, ctx)
        elif callback_kind == "text":
            await execute_reverse_text(query.message, user_id, payload["text"], lang, ctx)
        elif callback_kind == "photo_caption":
            await execute_reverse_photo_caption(query.message, user_id, payload["caption"], lang, ctx)
    except Exception as e:
        logger.error(f"Lang dispatch failed: {e}\n{traceback.format_exc()}")
        await ctx.bot.send_message(chat_id=chat_id, text=f"❌ فشل: {str(e)[:200]}")


async def generate_and_send_one_with_lang(message, user_id, idx, total, lang, ctx):
    """Auto-mode generator with language."""
    progress = await message.reply_text(
        f"🔄 *جاري التوليد ({idx}/{total})...*\n🔍 البحث عن آخر ترند تقني...",
        parse_mode="Markdown"
    )
    try:
        loop = asyncio.get_event_loop()
        news = await loop.run_in_executor(None, scout_news, "", lang)
        await render_and_send_post(message, news, idx, total, progress)
    except Exception as e:
        logger.error(f"Error: {e}\n{traceback.format_exc()}")
        await progress.edit_text(
            f"❌ فشل توليد المنشور {idx}/{total}\n\nالسبب: {str(e)[:200]}"
        )


async def execute_reverse_url(message, user_id, url, full_text, lang, ctx):
    """Execute reverse URL with a chosen language. message=bot's progress message, user_id=original user."""
    progress = await message.reply_text("📥 جاري قراءة الرابط...")
    chat_id = message.chat_id
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
            f"✅ قرأت المقال: {content_data.get('title','بدون عنوان')[:50]}\n"
            f"🤖 جاري إعادة الصياغة..."
        )

        news = await loop.run_in_executor(None, reverse_scout, combined, lang)
        news["source_url"] = url
        # ✅ Pass explicit user_id + chat_id (since message.from_user.id is the bot)
        await ask_about_image(message, ctx, news, progress, user_id=user_id, chat_id=chat_id)
    except Exception as e:
        logger.error(f"Reverse URL failed: {e}\n{traceback.format_exc()}")
        await progress.edit_text(f"❌ فشل: {str(e)[:200]}")


async def execute_reverse_text(message, user_id, text, lang, ctx):
    progress = await message.reply_text("🤖 جاري تحويل النص لمنشور...")
    chat_id = message.chat_id
    try:
        loop = asyncio.get_event_loop()
        news = await loop.run_in_executor(None, reverse_scout, text, lang)
        await ask_about_image(message, ctx, news, progress, user_id=user_id, chat_id=chat_id)
    except Exception as e:
        logger.error(f"Reverse text failed: {e}\n{traceback.format_exc()}")
        await progress.edit_text(f"❌ فشل: {str(e)[:200]}")


async def execute_reverse_photo_caption(message, user_id, caption, lang, ctx):
    progress = await message.reply_text("🤖 جاري تحويل المحتوى لمنشور...")
    chat_id = message.chat_id
    try:
        loop = asyncio.get_event_loop()
        prompt_text = f"Screenshot caption from user: {caption}\n\nGenerate a 4Ever post."
        news = await loop.run_in_executor(None, reverse_scout, prompt_text, lang)
        await ask_about_image(message, ctx, news, progress, user_id=user_id, chat_id=chat_id)
    except Exception as e:
        logger.error(f"Photo+caption failed: {e}\n{traceback.format_exc()}")
        await progress.edit_text(f"❌ فشل: {str(e)[:200]}")


async def ask_about_image(message, ctx, news, progress, user_id=None, chat_id=None):
    """After news is generated, ask user if they have a custom image.
    user_id and chat_id must be explicit (since 'message' might be the bot's own message)."""
    if user_id is None:
        user_id = message.from_user.id
    if chat_id is None:
        chat_id = message.chat_id

    PENDING_NEWS[user_id] = {
        "news": news,
        "chat_id": chat_id,
        "user_id": user_id,
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
        f"🖼️ *هل عندك صورة أو فيديو مناسب تريد استخدامه؟*\n\n"
        f"• نعم → ارفع صورة أو فيديو (سأستخرج إطار منه)\n"
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
            f"📸 *تمام، ارفع الآن:*\n\n"
            f"• صورة (PNG/JPG) — تُستخدم في التصميم\n"
            f"• فيديو (MP4/MOV) — سأستخرج إطار للتصميم + أرسل الفيديو كاملاً\n\n"
            f"العنوان: {news.get('headline_line1','')[:60]}\n\n"
            f"⏳ بانتظار محتواك...\n"
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

    # Case 2: Screenshot with caption → ask language first
    caption = (update.message.caption or "").strip()
    if not caption or len(caption) < 10:
        await update.message.reply_text(
            "📸 وصلتني الصورة. لكن أحتاج وصفاً مع الصورة (في الكابشن) "
            "لأعرف الخبر. أعد الإرسال مع كابشن يصف الخبر."
        )
        return

    await ask_about_language(update.message, ctx, "photo_caption", {"caption": caption})


async def handle_video(update, ctx):
    """User uploaded a video. Two cases:
    1. They're providing custom video for pending news (extract frame)
    2. They're sending a video with caption to convert to post
    """
    user_id = update.effective_user.id
    pending = PENDING_NEWS.get(user_id)

    # Get the video object (could be video, animation, video_note, or document with video MIME)
    video = update.message.video or update.message.animation or update.message.video_note
    document = update.message.document

    # If it's a document, check if it's actually a video
    is_video_doc = document and document.mime_type and document.mime_type.startswith("video/")

    if not video and not is_video_doc:
        return  # not actually a video

    # Case 1: User uploading custom video for pending news
    if pending and pending.get("awaiting_image"):
        news = pending["news"]
        progress = await update.message.reply_text(
            "🎬 *تم استلام الفيديو!*\n"
            "📸 جاري استخراج إطار للتصميم...\n"
            "🎥 وسأرسل الفيديو الأصلي بعد المنشور",
            parse_mode="Markdown"
        )
        try:
            # Download video
            file_obj = video or document
            tg_file = await ctx.bot.get_file(file_obj.file_id)

            video_path = str(OUTPUT_DIR / f"vid_{user_id}_{os.getpid()}.mp4")
            await tg_file.download_to_drive(video_path)
            logger.info(f"📥 Downloaded user video: {video_path} ({os.path.getsize(video_path)//1024}KB)")

            # Extract a frame using ffmpeg or PIL fallback
            frame_path = str(OUTPUT_DIR / f"frame_{user_id}_{os.getpid()}.jpg")
            extract_success = await extract_video_frame(video_path, frame_path)

            if not extract_success:
                await progress.edit_text("❌ فشل استخراج إطار من الفيديو. جرّب صورة بدلاً منه.")
                PENDING_NEWS.pop(user_id, None)
                try: os.unlink(video_path)
                except: pass
                return

            PENDING_NEWS.pop(user_id, None)
            # 🎯 Pass video_path so the user's original video also gets sent after the post
            await render_with_image(update.message, news, frame_path, progress, video_path=video_path)

            # Cleanup AFTER sending
            try: os.unlink(video_path)
            except: pass

        except Exception as e:
            logger.error(f"Custom video failed: {e}\n{traceback.format_exc()}")
            await progress.edit_text(f"❌ فشل: {str(e)[:200]}")
            PENDING_NEWS.pop(user_id, None)
        return

    # Case 2: Video with caption as news source
    caption = (update.message.caption or "").strip()
    if not caption or len(caption) < 10:
        await update.message.reply_text(
            "🎬 وصلني الفيديو. لكن أحتاج وصف الخبر في الكابشن. "
            "أعد الإرسال مع كابشن يصف الخبر."
        )
        return

    await ask_about_language(update.message, ctx, "photo_caption", {"caption": caption})


async def extract_video_frame(video_path, frame_path):
    """Extract a representative frame from a video.
    Uses imageio-ffmpeg (Python package that ships a static ffmpeg binary).
    """
    import subprocess

    try:
        import imageio_ffmpeg
        ffmpeg_bin = imageio_ffmpeg.get_ffmpeg_exe()
        logger.info(f"   Using ffmpeg from: {ffmpeg_bin}")

        # Quick duration probe via ffmpeg (no ffprobe needed)
        # We'll just seek to a few different times and pick the first that works
        seek_candidates = ["2", "1", "0.5", "0"]
        for seek in seek_candidates:
            try:
                result = subprocess.run(
                    [ffmpeg_bin, "-y", "-ss", seek, "-i", video_path,
                     "-vframes", "1", "-q:v", "2",
                     "-vf", "scale=1280:720:force_original_aspect_ratio=decrease",
                     frame_path],
                    capture_output=True, timeout=45
                )
                if os.path.exists(frame_path) and os.path.getsize(frame_path) > 5000:
                    # Validate it's a real frame (not all black/empty)
                    from PIL import Image, ImageStat
                    with Image.open(frame_path) as img:
                        rgb = img.convert("RGB")
                        stat = ImageStat.Stat(rgb)
                        mean = sum(stat.mean) / 3
                        if mean > 10:  # not pitch-black
                            logger.info(f"   ✅ Frame extracted at t={seek}s, mean={mean:.0f}")
                            return True
            except subprocess.TimeoutExpired:
                logger.warning(f"   ffmpeg timeout at t={seek}s")
                continue
            except Exception as e:
                logger.warning(f"   ffmpeg attempt at t={seek}s failed: {e}")
                continue

    except ImportError:
        logger.error("   imageio-ffmpeg not installed")
    except Exception as e:
        logger.error(f"   Frame extraction error: {e}")

    return False


async def handle_text_router(update, ctx):
    text = (update.message.text or "").strip()
    if not text:
        return

    # Explicit auto-post command
    if text.startswith("منشور") or text.lower().startswith("/post") or text.lower() == "post":
        count = parse_count(text)
        # Ask for language before generating
        await ask_about_language(update.message, ctx, "auto", {"count": count})
        return

    # URL → ask language → reverse mode
    urls = URL_PATTERN.findall(text)
    if urls:
        await ask_about_language(update.message, ctx, "url",
                                 {"url": urls[0], "full_text": text})
        return

    # Long descriptive text → ask language → reverse mode
    if len(text) > 20:
        await ask_about_language(update.message, ctx, "text", {"text": text})
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
    logger.info("🤖 Starting 4Ever Bot v2.4 (lang+video)...")
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("post", cmd_post))
    app.add_handler(CommandHandler("cancel", cmd_cancel))

    # Callback query handlers for inline buttons
    app.add_handler(CallbackQueryHandler(handle_image_choice, pattern=r"^img_(yes|no)_\d+$"))
    app.add_handler(CallbackQueryHandler(handle_language_choice, pattern=r"^lang_(ar|en|fr)_\d+$"))

    # 🆕 Videos & animations (GIF) → handle_video
    app.add_handler(MessageHandler(
        filters.VIDEO | filters.ANIMATION | filters.VIDEO_NOTE | filters.Document.VIDEO,
        handle_video
    ))
    # Photos → handle_photo
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
