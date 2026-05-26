"""4Ever Telegram Bot v2.3 — reverse mode with custom image upload option."""

import os
import time
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

from post_generator import generate_post, generate_post_layers
from news_scout import (
    scout_news, download_image, reverse_scout, fetch_url_content,
    acquire_validated_image, LANG_INSTRUCTIONS, DIALECTS, smart_scout,
    extract_post_from_screenshot,
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
POST_HISTORY = {}  # user_id -> {news, cfg_template} for regeneration

def cleanup_old_temp_files(user_id=None, max_age_seconds=300):
    """Clean up old temporary files in OUTPUT_DIR.
    Removes ALL files older than 5 minutes by default.
    If user_id given, also removes files containing that user_id in their name.
    Ensures no stale images leak into new requests.
    """
    try:
        now = time.time()
        cleaned = 0
        for fname in os.listdir(OUTPUT_DIR):
            fpath = OUTPUT_DIR / fname
            try:
                if not fpath.is_file():
                    continue
                # Always delete user-specific files
                if user_id and str(user_id) in fname:
                    fpath.unlink()
                    cleaned += 1
                    continue
                # Delete anything older than max_age
                age = now - fpath.stat().st_mtime
                if age > max_age_seconds:
                    fpath.unlink()
                    cleaned += 1
            except Exception:
                pass
        if cleaned:
            logger.info(f"🧹 Cleaned {cleaned} old temp files")
    except Exception as e:
        logger.warning(f"Cleanup failed: {e}")



USER_PREFS = {}  # user_id -> {"default_lang": "ar|en|fr"}


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
    # Reply keyboard with main actions
    from telegram import ReplyKeyboardMarkup, KeyboardButton
    kb = ReplyKeyboardMarkup(
        [
            [KeyboardButton("📰 منشور جديد"), KeyboardButton("🧹 تنظيف الذاكرة")],
            [KeyboardButton("📖 المساعدة"), KeyboardButton("⚙️ الحالة")],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )
    msg = (
        "🌌 *مرحباً بك في بوت 4Ever* 🌌\n\n"
        "أنا بوتك الذكي لتوليد منشورات تقنية احترافية.\n\n"
        "✨ *الأوضاع:*\n\n"
        "🎯 الوضع التلقائي:\n"
        "• `منشور` ← خبر تقني عشوائي\n"
        f"• `منشور 3` ← عدة منشورات (حد {MAX_POSTS})\n\n"
        "🔄 الوضع العكسي:\n"
        "• أرسل رابط خبر/Facebook/Twitter/Instagram\n"
        "• أرسل وصف خبر\n"
        "• أرسل صورة/فيديو + كابشن\n\n"
        "🧠 طلب ذكي:\n"
        "• `اريد منشور عن أحدث AI...`\n"
        "• `اعمل لي بوست يشرح...`\n\n"
        "🧹 *تنظيف الذاكرة:*\n"
        "اضغط الزر أو `/clean` لمسح كل الصور والجلسات المؤقتة\n\n"
        "🚀 جرّب الآن!"
    )
    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=kb)


async def cmd_clean(update, ctx):
    """🧹 Clean ALL temp files + user state for the calling user."""
    user_id = update.effective_user.id
    cleaned_files = 0
    try:
        # Force-clean ALL temp files (not just old ones)
        if OUTPUT_DIR.exists():
            for fname in os.listdir(OUTPUT_DIR):
                fpath = OUTPUT_DIR / fname
                try:
                    if fpath.is_file():
                        fpath.unlink()
                        cleaned_files += 1
                except: pass
    except Exception as e:
        logger.warning(f"clean failed: {e}")

    # Clear user state
    PENDING_NEWS.pop(user_id, None)
    POST_HISTORY.pop(user_id, None)

    await update.message.reply_text(
        f"🧹 *تم تنظيف الذاكرة المؤقتة!*\n\n"
        f"✅ حُذفت {cleaned_files} ملف مؤقت\n"
        f"✅ مُسحت جلستك الحالية\n"
        f"✅ لن تظهر صور قديمة في المنشورات القادمة\n\n"
        f"💡 ابدأ من جديد بإرسال: `منشور` أو رابط",
        parse_mode="Markdown"
    )


async def cmd_help(update, ctx):
    msg = (
        "📖 *دليل أوامر 4Ever* 📖\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🎯 *توليد تلقائي:*\n"
        "• `منشور` / `/post` ← منشور واحد\n"
        f"• `منشور N` ← N منشورات (1-{MAX_POSTS})\n\n"
        "🔄 *الوضع العكسي:*\n"
        "• أرسل رابط خبر 🌐\n"
        "• أرسل وصف خبر (>20 حرف) 📝\n"
        "• أرسل صورة + كابشن 📸\n"
        "• أرسل فيديو + كابشن 🎬\n"
        "→ يسألك: عندك صورة/فيديو؟\n"
        "  • نعم → ارفع → يصمم\n"
        "  • لا → يبحث/يولّد تلقائياً\n\n"
        "🌐 *اللغات:*\n"
        "• `/lang ar` ← العربية افتراضي\n"
        "• `/lang ar egyptian` ← عربي مصري\n"
        "• `/lang ar levantine` ← شامي\n"
        "• `/lang ar saudi` ← سعودي\n"
        "• `/lang ar algerian` ← جزائري\n"
        "• `/lang ar emirati` ← إماراتي\n"
        "• `/lang ar moroccan` ← مغربي\n"
        "• `/lang ar fusha` ← فصحى فقط\n"
        "• `/lang en` ← English افتراضي\n"
        "• `/lang fr` ← Français افتراضي\n"
        "• `/lang off` ← اسأل كل مرة\n\n"
        "🛠️ *عام:*\n"
        "• `/start` ← الترحيب\n"
        "• `/status` ← حالة البوت\n"
        "• `/cancel` ← إلغاء العملية\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "🎬 *للفيديو:*\n"
        "• الحد الأقصى: 3 دقائق\n"
        "• الحجم الأقصى: 100 MB"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


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
        "• الإصدار: 3.6 (فيديو بأبعاده الأصلية + زر تنظيف الذاكرة + حد 20MB صادق)"
    )
    await update.message.reply_text(msg)


async def cmd_cancel(update, ctx):
    user_id = update.effective_user.id
    if user_id in PENDING_NEWS:
        PENDING_NEWS.pop(user_id)
        await update.message.reply_text("✅ تم الإلغاء")
    else:
        await update.message.reply_text("لا توجد عملية معلّقة")


async def cmd_lang(update, ctx):
    """Set default language to skip language picker.
    Usage: /lang ar | /lang en | /lang fr | /lang off"""
    user_id = update.effective_user.id
    text = (update.message.text or "").strip().lower()
    parts = text.split(maxsplit=2)

    if len(parts) < 2:
        current = USER_PREFS.get(user_id, {}).get("default_lang", "off")
        await update.message.reply_text(
            f"🌐 *اللغة الافتراضية الحالية*: `{current}`\n\n"
            f"الأوامر:\n"
            f"• `/lang ar` ← العربية دائماً\n"
            f"• `/lang en` ← English دائماً\n"
            f"• `/lang fr` ← Français دائماً\n"
            f"• `/lang off` ← اسأل في كل مرة (الافتراضي)",
            parse_mode="Markdown"
        )
        return

    choice = parts[1]
    # Support: /lang ar egyptian, /lang ar fusha, etc.
    sub_choice = parts[2] if len(parts) > 2 else None

    if choice in ("ar", "en", "fr"):
        USER_PREFS.setdefault(user_id, {})["default_lang"] = choice
        labels = {"ar": "🇸🇦 العربية", "en": "🇬🇧 English", "fr": "🇫🇷 Français"}

        # For Arabic: allow sub-choice for dialect
        if choice == "ar" and sub_choice and sub_choice in DIALECTS:
            USER_PREFS[user_id]["default_dialect"] = sub_choice
            dialect_label = DIALECTS[sub_choice]["label"]
            await update.message.reply_text(
                f"✅ تم تعيين {labels[choice]} ({dialect_label}) كافتراضي\n"
                f"لن أسألك عن اللغة ولا اللهجة.\n"
                f"لإلغاء: `/lang off`",
                parse_mode="Markdown"
            )
        else:
            # Remove dialect preference if switching to non-ar or without sub-choice
            USER_PREFS[user_id].pop("default_dialect", None)
            extra = ""
            if choice == "ar":
                extra = "\n💡 لإضافة لهجة: `/lang ar egyptian` (أو: levantine, saudi, algerian, emirati, moroccan, fusha)"
            await update.message.reply_text(
                f"✅ تم تعيين {labels[choice]} كلغة افتراضية{extra}\n"
                f"لإلغاء: `/lang off`",
                parse_mode="Markdown"
            )
    elif choice == "off":
        if user_id in USER_PREFS:
            USER_PREFS[user_id].pop("default_lang", None)
        await update.message.reply_text("✅ سأسألك عن اللغة في كل مرة الآن.")
    else:
        await update.message.reply_text(
            "❌ خيار غير معروف. استخدم: ar / en / fr / off"
        )


def parse_count(text):
    text = (text or "").strip().lower()
    text = text.replace("/post", "").replace("منشور", "").strip()
    ar_to_en = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
    text = text.translate(ar_to_en)
    m = re.search(r"\d+", text)
    if m:
        return max(1, min(int(m.group()), MAX_POSTS))
    return 1


async def composite_video_into_design(bg_png, overlay_png, design_box, video_path, output_mp4,
                                       canvas_w=1080, canvas_h=1080):
    """
    Composite a video INTO the 4Ever design with 3 layers:
    
    Layer 1 (bottom): bg_png    - cosmic background + header logos (canvas_w x canvas_h)
    Layer 2 (middle): video     - user video SCALED TO FIT (no crop) inside design_box
    Layer 3 (top):    overlay   - source logo, trend arrow, badges, headline (RGBA)
    
    Result: MP4 reel at canvas_w x canvas_h with FULL 4Ever branding on top.
    The video is FIT (preserving aspect) inside the design box - no cropping!
    """
    import subprocess
    import imageio_ffmpeg

    ffmpeg_bin = imageio_ffmpeg.get_ffmpeg_exe()
    gx, gy, w, h = design_box
    logger.info(f"🎬 3-layer composite: canvas {canvas_w}x{canvas_h}, box ({gx},{gy}) {w}x{h}")

    # Filter:
    # [0]=bg.png (looped), [1]=video, [2]=overlay.png (looped)
    # - Scale video to FIT inside box (preserve aspect, may add padding)
    #   force_original_aspect_ratio=decrease + pad to fill the box with black bars
    # - Overlay video on bg at (gx, gy)
    # - Then overlay the badges/source/headline RGBA on top
    filter_complex = (
        # Since box was sized to match video aspect, scale directly fits perfectly
        f"[1:v]scale={w}:{h}:flags=lanczos[vid];"
        f"[0:v][vid]overlay={gx}:{gy}[bg_with_vid];"
        f"[bg_with_vid][2:v]overlay=0:0:shortest=1[out]"
    )

    try:
        cmd = [
            ffmpeg_bin, "-y",
            # Fast image inputs at 24fps for speed (TikTok/IG default)
            "-framerate", "24", "-loop", "1", "-i", bg_png,
            "-i", video_path,
            "-framerate", "24", "-loop", "1", "-i", overlay_png,
            "-filter_complex", filter_complex,
            "-map", "[out]",
            "-map", "1:a?",                          # audio from video (optional)
            # 🚀 FAST encoding settings
            "-c:v", "libx264",
            "-preset", "ultrafast",                  # fastest preset
            "-tune", "fastdecode",                   # faster decode
            "-crf", "30",                            # slightly lower quality but 2x faster
            "-pix_fmt", "yuv420p",
            "-r", "24",  # Output 24fps (40% faster than 30fps)
            "-c:a", "aac",
            "-b:a", "64k",                           # lower audio bitrate
            "-ar", "44100",
            "-shortest",                             # stop when shortest stream ends (the video)
            "-movflags", "+faststart",
            "-t", "180",                             # max 3 min
            "-threads", "0",                         # use all CPU cores
            output_mp4
        ]
        logger.info(f"   Running ffmpeg composite (ultrafast preset)...")
        result = subprocess.run(cmd, capture_output=True, timeout=300)  # 5 min timeout (was 7)

        if result.returncode != 0:
            stderr = result.stderr.decode('utf-8', errors='ignore')[-800:]
            logger.error(f"   ffmpeg failed:\n{stderr}")
            return False

        if not os.path.exists(output_mp4) or os.path.getsize(output_mp4) < 10000:
            logger.error("   Output file missing or too small")
            return False

        size_mb = os.path.getsize(output_mp4) / 1024 / 1024
        logger.info(f"   ✅ Composite video: {size_mb:.1f} MB")
        return True
    except subprocess.TimeoutExpired:
        logger.error("   ffmpeg composite timeout (>3 min)")
        return False
    except Exception as e:
        logger.error(f"   Composite error: {e}")
        return False


async def render_with_image(message, news, img_path, progress, video_path=None, user_id=None):
    """Render the 4Ever post.
    - If video_path provided: composite video INTO the design → output MP4 reels
    - Otherwise: standard image post
    """
    loop = asyncio.get_event_loop()
    caption = news.get("caption", "")
    out_path = str(OUTPUT_DIR / f"post_{os.getpid()}_{id(news)}.png")
    cfg = base_config(img_path, news)

    # 🎬 VIDEO MODE: build a video reel with 4Ever frame
    if video_path and os.path.exists(video_path):
        await progress.edit_text("🔍 جاري تحليل الفيديو...")

        # 🎯 Detect video dimensions to choose canvas size
        vid_w, vid_h = get_video_dimensions(video_path)
        canvas_w, canvas_h = choose_canvas_size_for_aspect(vid_w, vid_h)

        # 🎯 PRESERVE video native resolution - canvas matches video size, not forced 1080
        # Frame inside = 85% of canvas_w, so canvas_w = ceil(video_w / 0.85)
        # Min 1080 (so chrome is readable), max 1920 (avoid huge files)
        native_canvas_w = int(vid_w / 0.85)
        canvas_w = max(1080, min(native_canvas_w, 1920))
        canvas_w = (canvas_w // 2) * 2  # even number for codec

        # Recalculate canvas_h based on new canvas_w
        media_display_w = int(canvas_w * 0.85)
        media_display_h = int(media_display_w / (vid_w / vid_h))
        CHROME_TOP = 140; CHROME_BOTTOM = 280; PADDING = 30
        canvas_h = media_display_h + CHROME_TOP + CHROME_BOTTOM + PADDING
        canvas_h = max(1080, min(canvas_h, 2400))
        canvas_h = (canvas_h // 2) * 2
        logger.info(f"   📐 Native canvas for video: {canvas_w}x{canvas_h} (video {vid_w}x{vid_h})")

        cfg["canvas_height"] = canvas_h
        cfg["output"]["size"] = canvas_w

        await progress.edit_text(
            f"🎨 جاري تصميم طبقات المنشور...\n"
            f"📐 الكانفس: {canvas_w}x{canvas_h}"
        )

        # Generate two layers: background and overlay (badges/source/headline)
        bg_layer = str(OUTPUT_DIR / f"bg_{os.getpid()}_{id(news)}.png")
        overlay_layer = str(OUTPUT_DIR / f"ov_{os.getpid()}_{id(news)}.png")

        def _gen_layers():
            return generate_post_layers(cfg, bg_layer, overlay_layer,
                                         media_w=vid_w, media_h=vid_h)
        box_coords = await loop.run_in_executor(None, _gen_layers)

        # Also generate fallback static image (in case ffmpeg fails)
        def _gen_static():
            return generate_post(cfg, out_path, canvas_height=canvas_h)
        await loop.run_in_executor(None, _gen_static)

        await progress.edit_text(
            "🎬 جاري دمج الفيديو في تصميم 4Ever...\n"
            f"📐 {canvas_w}x{canvas_h}\n"
            "⏳ سيستغرق دقيقة لكل ~30 ثانية فيديو..."
        )

        # Composite: bg → video → overlay (3 layers) at chosen canvas size
        video_out = str(OUTPUT_DIR / f"reel_{os.getpid()}_{id(news)}.mp4")
        success = await composite_video_into_design(
            bg_layer, overlay_layer, box_coords, video_path, video_out,
            canvas_w=canvas_w, canvas_h=canvas_h
        )

        # Cleanup intermediate layers
        for p in [bg_layer, overlay_layer]:
            try: os.unlink(p)
            except: pass

        if success and os.path.exists(video_out):
            # Send as VIDEO reel
            try:
                await progress.edit_text("📤 جاري الإرسال...")
                with open(video_out, "rb") as f:
                    if len(caption) <= 1024:
                        await message.reply_video(
                            video=f,
                            caption=caption,
                            supports_streaming=True,
                            width=canvas_w, height=canvas_h,
                        )
                    else:
                        await message.reply_video(
                            video=f,
                            supports_streaming=True,
                            width=canvas_w, height=canvas_h,
                        )
                        for chunk_start in range(0, len(caption), 4000):
                            await message.reply_text(caption[chunk_start:chunk_start + 4000])
                logger.info(f"   ✅ Reel sent: {os.path.getsize(video_out)//1024} KB")
            except Exception as e:
                logger.error(f"   Failed to send reel: {e}")
                # Fallback: send as document
                try:
                    with open(video_out, "rb") as f:
                        await message.reply_document(document=f, caption=caption[:1024])
                except Exception as e2:
                    logger.error(f"   Also failed as document: {e2}")
                    # Final fallback: send static design image
                    with open(out_path, "rb") as f:
                        await message.reply_photo(photo=f, caption=caption[:1024])
            # Cleanup video output
            try: os.unlink(video_out)
            except: pass
        else:
            # Compositing failed - fall back to image post + send original video after
            await progress.edit_text("⚠️ تعذر دمج الفيديو، أرسل تصميم بصورة من الفيديو...")
            if len(caption) <= 1024:
                with open(out_path, "rb") as f:
                    await message.reply_photo(photo=f, caption=caption)
            else:
                with open(out_path, "rb") as f:
                    await message.reply_photo(photo=f)
                for chunk_start in range(0, len(caption), 4000):
                    await message.reply_text(caption[chunk_start:chunk_start + 4000])
            # Send original video separately
            try:
                with open(video_path, "rb") as f:
                    await message.reply_video(video=f, supports_streaming=True)
            except Exception:
                pass
    else:
        # 📸 IMAGE-ONLY MODE: detect aspect, choose canvas, design
        img_w, img_h = get_image_dimensions(img_path)
        canvas_w, canvas_h = choose_canvas_size_for_aspect(img_w, img_h)

        await progress.edit_text(
            f"🎨 جاري تصميم المنشور...\n"
            f"📐 الكانفس: {canvas_w}x{canvas_h}"
        )

        def _gen():
            return generate_post(cfg, out_path, canvas_height=canvas_h)
        await loop.run_in_executor(None, _gen)

        if len(caption) <= 1024:
            with open(out_path, "rb") as f:
                await message.reply_photo(photo=f, caption=caption)
        else:
            with open(out_path, "rb") as f:
                await message.reply_photo(photo=f)
            for chunk_start in range(0, len(caption), 4000):
                await message.reply_text(caption[chunk_start:chunk_start + 4000])

    # Source link
    if news.get("source_url"):
        await message.reply_text(
            f"🔗 المصدر: {news['source_url']}",
            disable_web_page_preview=True
        )

    try:
        await progress.delete()
    except Exception:
        pass

    # 🆕 Offer post-generation options: regenerate image with AI, upload custom, or done
    try:
        # 🎯 Use explicit user_id (caller knows correct user) - falls back to message.from_user only if not provided
        actual_user_id = user_id
        if actual_user_id is None and hasattr(message, "from_user") and message.from_user:
            actual_user_id = message.from_user.id
        if actual_user_id and not video_path:  # Only for image posts (video has its own flow)
            # Save news data for potential regeneration
            POST_HISTORY[actual_user_id] = {
                "news": news,
                "cfg_template": dict(news),
            }
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("🎨 صورة AI جديدة", callback_data=f"regen_ai_{actual_user_id}"),
                InlineKeyboardButton("📸 سأرفع صورة/فيديو", callback_data=f"regen_upload_{actual_user_id}"),
            ], [
                InlineKeyboardButton("✅ تمام، خلاص", callback_data=f"regen_done_{actual_user_id}"),
            ]])
            await message.reply_text(
                "هل تريد تجربة صورة مختلفة لهذا المنشور؟",
                reply_markup=kb
            )
    except Exception as e:
        logger.warning(f"Failed to offer regen options: {e}")

    # 🧹 ALWAYS cleanup temporary files - never keep cached images
    for p in [img_path, out_path]:
        try: os.unlink(p)
        except: pass
    # Note: video_path cleanup handled by caller


async def render_and_send_post(message, news, idx, total, progress):
    """Full pipeline: auto-acquire validated image + render + send.
    If news has _og_image_url (from URL fetch), try that first.
    """
    loop = asyncio.get_event_loop()
    await progress.edit_text(f"🔄 ({idx}/{total}) - جاري البحث عن صورة مناسبة...")

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False,
                                     dir=str(OUTPUT_DIR)) as tmp:
        img_path = tmp.name

    image_acquired = False

    # 🆕 Try og:image first if available (from URL fetch)
    og_url = news.get("_og_image_url")
    if og_url:
        try:
            logger.info(f"   📷 Trying og:image first: {og_url[:80]}")
            def _dl():
                from news_scout import try_download_image, _is_image_visually_meaningful
                if try_download_image(og_url, img_path):
                    if _is_image_visually_meaningful(img_path):
                        return True
                return False
            if await loop.run_in_executor(None, _dl):
                logger.info(f"   ✅ Using og:image directly!")
                image_acquired = True
        except Exception as e:
            logger.warning(f"   og:image download failed: {e}")

    if not image_acquired:
        try:
            await loop.run_in_executor(None, acquire_validated_image, news, img_path, 3)
            if not os.path.exists(img_path) or os.path.getsize(img_path) < 1000:
                raise ValueError("No valid image acquired")
            image_acquired = True
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

    user_id = getattr(message, "from_user", None) and message.from_user.id
    await render_with_image(message, news, img_path, progress, user_id=user_id)


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
    If user has a default language set, skip the picker.
    """
    user_id = message.from_user.id

    # Check for saved default language
    prefs = USER_PREFS.get(user_id, {})
    default_lang = prefs.get("default_lang")
    default_dialect = prefs.get("default_dialect")  # only for ar
    if default_lang:
        # Skip picker, go straight to execution
        dialect_note = f" + {DIALECTS.get(default_dialect, {}).get('label', '')}" if (default_lang == "ar" and default_dialect) else ""
        logger.info(f"   Using saved default lang: {default_lang}{dialect_note}")
        progress = await message.reply_text(
            f"🚀 جاري التنفيذ باللغة المحفوظة: {default_lang.upper()}{dialect_note}..."
        )
        try:
            if callback_kind == "auto":
                count = payload.get("count", 1)
                for i in range(1, count + 1):
                    await generate_and_send_one_with_lang(progress, user_id, i, count, default_lang, ctx, dialect=default_dialect)
                if count > 1:
                    await message.reply_text(f"✅ تم توليد {count} منشورات!")
            elif callback_kind == "url":
                await execute_reverse_url(progress, user_id, payload["url"], payload["full_text"], default_lang, ctx, dialect=default_dialect)
            elif callback_kind == "text":
                await execute_reverse_text(progress, user_id, payload["text"], default_lang, ctx, dialect=default_dialect)
            elif callback_kind == "photo_caption":
                await execute_reverse_photo_caption(progress, user_id, payload["caption"], default_lang, ctx, dialect=default_dialect)
            elif callback_kind == "smart":
                await execute_smart_request(progress, user_id, payload["request"], default_lang, ctx, dialect=default_dialect)
        except Exception as e:
            logger.error(f"Direct lang dispatch failed: {e}\n{traceback.format_exc()}")
            await message.reply_text(f"❌ فشل: {str(e)[:200]}")
        return

    # No default - ask normally
    PENDING_NEWS[user_id] = {
        "kind": "lang_choice",
        "callback_kind": callback_kind,
        "payload": payload,
        "chat_id": message.chat_id,
        "user_id": user_id,
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


async def ask_about_dialect(query, user_id, callback_kind, payload, chat_id, ctx):
    """After user picks Arabic, ask which dialect (or Fusha)."""
    keyboard = [
        [
            InlineKeyboardButton("📚 الفصحى", callback_data=f"dial_fusha_{user_id}"),
            InlineKeyboardButton("🇪🇬 المصرية", callback_data=f"dial_egyptian_{user_id}"),
        ],
        [
            InlineKeyboardButton("🇸🇾 الشامية", callback_data=f"dial_levantine_{user_id}"),
            InlineKeyboardButton("🇸🇦 السعودية", callback_data=f"dial_saudi_{user_id}"),
        ],
        [
            InlineKeyboardButton("🇩🇿 الجزائرية", callback_data=f"dial_algerian_{user_id}"),
            InlineKeyboardButton("🇦🇪 الإماراتية", callback_data=f"dial_emirati_{user_id}"),
        ],
        [
            InlineKeyboardButton("🇲🇦 المغربية", callback_data=f"dial_moroccan_{user_id}"),
        ],
    ]
    PENDING_NEWS[user_id] = {
        "kind": "dial_choice",
        "callback_kind": callback_kind,
        "payload": payload,
        "chat_id": chat_id,
        "user_id": user_id,
    }
    await query.edit_message_text(
        "🗣️ *اختر اللهجة:*\n\n"
        "📚 *الفصحى* - عربية معاصرة احترافية\n"
        "🇪🇬 *المصرية* - ازاي، علشان، يلا، كده\n"
        "🇸🇾 *الشامية* - شو، هيك، كتير، منيح\n"
        "🇸🇦 *السعودية* - ايش، كذا، حلو\n"
        "🇩🇿 *الجزائرية* - واش، بصح، كيما\n"
        "🇦🇪 *الإماراتية* - شو، وايد، عاد\n"
        "🇲🇦 *المغربية* - واخا، بزاف، دابا\n\n"
        "_ملاحظة: العنوان يبقى بالفصحى، الكابشن باللهجة المختارة_",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )


async def handle_dialect_choice(update, ctx):
    """Callback when user selects an Arabic dialect."""
    query = update.callback_query
    await query.answer()

    parts = query.data.split("_")
    if len(parts) < 3:
        return
    dialect = parts[1]
    try:
        user_id = int(parts[2])
    except ValueError:
        return

    if query.from_user.id != user_id:
        await query.answer("⚠️ هذا الزر لمستخدم آخر", show_alert=True)
        return

    pending = PENDING_NEWS.get(user_id)
    if not pending or pending.get("kind") != "dial_choice":
        await query.edit_message_text("⚠️ انتهت المهلة. حاول مرة أخرى.")
        return

    callback_kind = pending["callback_kind"]
    payload = pending["payload"]
    chat_id = pending.get("chat_id")
    PENDING_NEWS.pop(user_id, None)

    dialect_label = DIALECTS.get(dialect, {}).get("label", dialect)
    progress = await ctx.bot.send_message(chat_id, f"✅ {dialect_label}\n🚀 جاري التنفيذ...")

    try:
        if callback_kind == "auto":
            count = payload.get("count", 1)
            for i in range(1, count + 1):
                await generate_and_send_one_with_lang(progress, user_id, i, count, "ar", ctx, dialect=dialect)
            if count > 1:
                await ctx.bot.send_message(chat_id, f"✅ تم توليد {count} منشورات!")
        elif callback_kind == "url":
            await execute_reverse_url(progress, user_id, payload["url"], payload["full_text"], "ar", ctx, dialect=dialect)
        elif callback_kind == "text":
            await execute_reverse_text(progress, user_id, payload["text"], "ar", ctx, dialect=dialect)
        elif callback_kind == "photo_caption":
            await execute_reverse_photo_caption(progress, user_id, payload["caption"], "ar", ctx, dialect=dialect)
        elif callback_kind == "smart":
            await execute_smart_request(progress, user_id, payload["request"], "ar", ctx, dialect=dialect)
    except Exception as e:
        logger.error(f"Dialect dispatch failed: {e}\n{traceback.format_exc()}")
        await ctx.bot.send_message(chat_id, f"❌ فشل: {str(e)[:200]}")



async def handle_regen_callback(update, ctx):
    """Handle regeneration options after a post is sent."""
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id

    try:
        if data.startswith("regen_done_"):
            try:
                await query.message.delete()
            except: pass
            POST_HISTORY.pop(user_id, None)
            return

        elif data.startswith("regen_upload_"):
            # Ask user to upload a custom image/video
            PENDING_NEWS[user_id] = {
                "kind": "awaiting_custom_media",
                "news": POST_HISTORY.get(user_id, {}).get("news"),
                "chat_id": query.message.chat_id,
                "user_id": user_id,
            }
            try:
                await query.edit_message_text(
                    "📸 *تمام، ارفع الآن:*\n"
                    "━━━━━━━━━━━━━━━━━━\n"
                    "🖼️ صورة (PNG/JPG)\n"
                    "🎬 فيديو (MP4/MOV) - حتى 3 دقائق\n"
                    "━━━━━━━━━━━━━━━━━━\n\n"
                    "⏳ بانتظار محتواك...\n"
                    "لإلغاء: /cancel",
                    parse_mode="Markdown"
                )
            except Exception:
                pass
            return

        elif data.startswith("regen_ai_"):
            # Regenerate the image using AI based on the news content
            history = POST_HISTORY.get(user_id)
            if not history or not history.get("news"):
                await query.edit_message_text(
                    "⚠️ انتهت الجلسة المؤقتة. أرسل `/post` أو رابط للبدء من جديد.\n\n"
                    "💡 الذاكرة المؤقتة تنتهي بعد 30 دقيقة من آخر منشور.",
                    parse_mode="Markdown"
                )
                return

            news = history["news"]
            try:
                await query.edit_message_text(
                    "🎨 *جاري توليد صورة جديدة بالذكاء الاصطناعي...*\n"
                    f"🎯 الموضوع: {news.get('headline_line1', '')[:50]}\n"
                    f"⏳ ~20-40 ثانية..."
                , parse_mode="Markdown")
            except: pass

            # 🆕 Use multiple seeds + try best models for HIGH quality
            from news_scout import generate_ai_image
            import random, time
            img_path = str(OUTPUT_DIR / f"regen_ai_{user_id}_{int(time.time())}.jpg")

            # 🎯 Build a HIGH-QUALITY prompt that focuses on the actual subject
            subject = (news.get("image_prompt") or news.get("image_query")
                       or news.get("headline_line2_en") or news.get("headline_line1") or "tech product")
            subject = subject[:300]

            # Use professional AI image style
            ai_prompt = (
                f"{subject}, professional product photography, ultra detailed, sharp focus, "
                f"studio lighting, cinematic, 8k quality, photorealistic, "
                f"professional composition, no text overlay, no watermark"
            )

            # Random seed for variety on re-clicks
            seed = random.randint(1, 999999)

            loop = asyncio.get_event_loop()
            def gen():
                try:
                    return generate_ai_image(ai_prompt, img_path, seed=seed)
                except TypeError:
                    return generate_ai_image(ai_prompt, img_path)

            got = await loop.run_in_executor(None, gen)

            if not got or not os.path.exists(img_path):
                try:
                    await query.edit_message_text(
                        "❌ فشل توليد الصورة.\n\n"
                        "حاول مرة أخرى - أحياناً الخادم مشغول لحظياً.\n"
                        "أو ارفع صورة من جهازك بدلاً منها."
                    )
                except: pass
                return

            # Render new post with the new AI image
            try: await query.message.delete()
            except: pass

            new_progress = await ctx.bot.send_message(
                chat_id=query.message.chat_id,
                text="✅ تم توليد الصورة!\n🎨 جاري إعادة بناء المنشور..."
            )
            await render_with_image(query.message, news, img_path, new_progress, user_id=user_id)

    except Exception as e:
        logger.error(f"regen callback failed: {e}\n{traceback.format_exc()}")
        try:
            await query.edit_message_text(f"❌ فشل: {str(e)[:200]}")
        except: pass


async def handle_language_choice(update, ctx):
    """Callback when user selects a language.
    For Arabic, this routes to dialect picker first.
    """
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

    # 🆕 If Arabic, ask for dialect first
    if lang == "ar":
        await ask_about_dialect(query, user_id, callback_kind, payload, chat_id, ctx)
        return

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


async def generate_and_send_one_with_lang(message, user_id, idx, total, lang, ctx, dialect=None):
    """Auto-mode generator with language + optional Arabic dialect."""
    progress = await message.reply_text(
        f"🔄 *جاري التوليد ({idx}/{total})...*\n🔍 البحث عن آخر ترند تقني...",
        parse_mode="Markdown"
    )
    try:
        loop = asyncio.get_event_loop()
        news = await loop.run_in_executor(
            None,
            lambda: scout_news(extra_instructions="", lang=lang, dialect=dialect)
        )
        await render_and_send_post(message, news, idx, total, progress)
    except Exception as e:
        logger.error(f"Error: {e}\n{traceback.format_exc()}")
        await progress.edit_text(
            f"❌ فشل توليد المنشور {idx}/{total}\n\nالسبب: {str(e)[:200]}"
        )


async def execute_reverse_url(message, user_id, url, full_text, lang, ctx, dialect=None):
    """Execute reverse URL: fetch content + extract image + reverse_scout + ask about image.
    message=bot's progress message, user_id=original user.
    """
    progress = await message.reply_text("📥 جاري قراءة الرابط...")
    chat_id = message.chat_id
    try:
        loop = asyncio.get_event_loop()
        # fetch_url_content now returns (text, image_url) tuple when return_image=True
        text_content, og_image_url = await loop.run_in_executor(
            None, lambda: fetch_url_content(url, return_image=True)
        )

        if not text_content or len(text_content) < 100:
            await progress.edit_text(
                "❌ تعذر استخراج محتوى من الرابط.\n\n"
                "حاول:\n"
                "• إرسال الرابط مع وصف مختصر للمنشور\n"
                "• أو لقطة شاشة للمنشور"
            )
            return

        user_extras = full_text.replace(url, "").strip()
        combined = f"URL: {url}\n\nContent:\n{text_content}"
        if user_extras:
            combined += f"\n\nUser context: {user_extras}"

        await progress.edit_text(
            f"✅ قرأت المحتوى ({len(text_content)} حرف)\n"
            f"🤖 جاري إعادة الصياغة..."
        )

        news = await loop.run_in_executor(
            None, lambda: reverse_scout(combined, lang=lang, dialect=dialect)
        )
        news["source_url"] = url

        # 🆕 If we found an og:image, try to use it as the main asset
        if og_image_url:
            news["_og_image_url"] = og_image_url
            logger.info(f"   📷 og:image will be used: {og_image_url[:80]}")

        # Ask user if they have their own image, or use ours / AI
        await ask_about_image(message, ctx, news, progress, user_id=user_id, chat_id=chat_id)
    except Exception as e:
        logger.error(f"Reverse URL failed: {e}\n{traceback.format_exc()}")
        await progress.edit_text(f"❌ فشل: {str(e)[:200]}")


async def execute_smart_request(message, user_id, request_text, lang, ctx, dialect=None):
    """Execute a smart natural-language request → smart_scout → ask for image → render."""
    progress = await message.reply_text(
        "🧠 *جاري فهم طلبك وتوليد المحتوى...*",
        parse_mode="Markdown"
    )
    try:
        loop = asyncio.get_event_loop()
        news = await loop.run_in_executor(
            None,
            lambda: smart_scout(request_text, lang=lang, dialect=dialect)
        )
        # Ask if user has custom image/video
        await ask_about_image(message, user_id, news, ctx, callback_kind="smart_followup")
    except Exception as e:
        logger.error(f"Smart request failed: {e}\n{traceback.format_exc()}")
        await progress.edit_text(f"❌ فشل: {str(e)[:200]}")


async def execute_reverse_text(message, user_id, text, lang, ctx, dialect=None):
    progress = await message.reply_text("🤖 جاري تحويل النص لمنشور...")
    chat_id = message.chat_id
    try:
        loop = asyncio.get_event_loop()
        news = await loop.run_in_executor(None, reverse_scout, text, lang)
        await ask_about_image(message, ctx, news, progress, user_id=user_id, chat_id=chat_id)
    except Exception as e:
        logger.error(f"Reverse text failed: {e}\n{traceback.format_exc()}")
        await progress.edit_text(f"❌ فشل: {str(e)[:200]}")


async def execute_reverse_photo_caption(message, user_id, caption, lang, ctx, dialect=None):
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
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🖼️ *صورة* (PNG/JPG)\n"
            f"   • تُستخدم مباشرة في التصميم\n\n"
            f"🎬 *فيديو* (MP4/MOV)\n"
            f"   • يُدمج داخل تصميم 4Ever (ريلز كامل)\n"
            f"   ⚠️ *الحد الأقصى للمدة: 3 دقائق*\n"
            f"   ⚠️ *الحد الأقصى للحجم: 100 MB*\n"
            f"   ⏱ المعالجة تستغرق 3-5 دقائق\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"📰 العنوان: {news.get('headline_line1','')[:60]}\n\n"
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
    cleanup_old_temp_files(user_id=update.effective_user.id)
    """Photo handler — two cases:
    1. User is uploading a custom image for pending news
    2. User sent a screenshot with caption (extract news from caption)
    """
    user_id = update.effective_user.id
    pending = PENDING_NEWS.get(user_id)

    # Case 1: User uploading custom image for pending news (either fresh or regen flow)
    if pending and (pending.get("awaiting_image") or pending.get("kind") == "awaiting_custom_media"):
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
            await render_with_image(update.message, news, user_img_path, progress, user_id=user_id)
        except Exception as e:
            logger.error(f"Custom image handler failed: {e}\n{traceback.format_exc()}")
            await progress.edit_text(f"❌ فشل معالجة الصورة: {str(e)[:200]}")
            PENDING_NEWS.pop(user_id, None)
        return

    # Case 2: Screenshot - either with caption or extract content from image
    caption = (update.message.caption or "").strip()

    if caption and len(caption) >= 10:
        # User provided caption - use it
        await ask_about_language(update.message, ctx, "photo_caption", {"caption": caption})
        return

    # 🆕 No caption (or too short) → extract content from screenshot via OCR
    progress = await update.message.reply_text(
        "📸 *وصلتني لقطة الشاشة!*\n"
        "🔍 جاري قراءة محتواها واستخراج المعلومات...\n"
        "⏳ لحظات...",
        parse_mode="Markdown"
    )
    try:
        photo = update.message.photo[-1]
        tg_file = await ctx.bot.get_file(photo.file_id)
        screenshot_path = str(OUTPUT_DIR / f"screen_{user_id}_{os.getpid()}.jpg")
        await tg_file.download_to_drive(screenshot_path)

        loop = asyncio.get_event_loop()
        extracted = await loop.run_in_executor(None, extract_post_from_screenshot, screenshot_path)

        # Clean up screenshot
        try: os.unlink(screenshot_path)
        except: pass

        if not extracted or len(extracted) < 50:
            await progress.edit_text(
                "⚠️ لم أتمكن من استخراج محتوى واضح من الصورة.\n\n"
                "حاول:\n"
                "• إرسال الصورة مع كابشن يصف الخبر\n"
                "• أو إرسال صورة أوضح بنصوص قابلة للقراءة"
            )
            return

        await progress.edit_text(
            f"✅ *استخرجت المحتوى:*\n\n"
            f"```\n{extracted[:400]}{'...' if len(extracted) > 400 else ''}\n```\n\n"
            f"🌐 الآن اختر اللغة...",
            parse_mode="Markdown"
        )

        # Route to language picker (use the extracted text as if it were caption)
        await ask_about_language(update.message, ctx, "photo_caption", {"caption": extracted})

    except Exception as e:
        logger.error(f"Screenshot OCR failed: {e}\n{traceback.format_exc()}")
        await progress.edit_text(f"❌ فشل قراءة الصورة: {str(e)[:200]}")


async def handle_video(update, ctx):
    cleanup_old_temp_files(user_id=update.effective_user.id)
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

    # Case 1: User uploading custom video for pending news (fresh or regen)
    if pending and (pending.get("awaiting_image") or pending.get("kind") == "awaiting_custom_media"):
        news = pending["news"]

        # 🎯 Validate video constraints BEFORE downloading
        file_obj = video or document
        file_size_mb = (file_obj.file_size or 0) / (1024 * 1024)
        duration_s = getattr(video, "duration", 0) if video else 0

        # 🎯 Telegram Bot API hard limit: 20 MB per file download (cannot be raised on free tier)
        # We allow up to 19.5MB to be safe
        TELEGRAM_MAX_MB = 19.5
        MAX_DURATION_S = 180  # 3 minutes - softer limit, video is trimmed via ffmpeg -t

        warnings = []
        if duration_s > MAX_DURATION_S:
            warnings.append(f"⚠️ المدة {duration_s}s ستُقص إلى 3 دقائق")

        # HARD STOP for files over Telegram's download limit
        if file_size_mb > TELEGRAM_MAX_MB:
            await update.message.reply_text(
                f"❌ *الفيديو حجمه {file_size_mb:.1f} MB*\n\n"
                f"تيليجرام يحدد تحميل الفيديوهات من البوت بـ **20 MB كحد أقصى** (حد فني من تيليجرام لا يمكن تجاوزه).\n\n"
                f"💡 *الحلول:*\n"
                f"• اضغط الفيديو إلى أقل من 20 MB (موجود تطبيقات ضغط فيديو)\n"
                f"• أو قص جزء أقصر من الفيديو\n"
                f"• أو ارفع جودة أقل (720p بدل 4K)\n\n"
                f"⚠️ هذا قيد من تيليجرام نفسه، ليس من البوت.",
                parse_mode="Markdown"
            )
            PENDING_NEWS.pop(user_id, None)
            return

        warning_text = ""
        if warnings:
            warning_text = "\n" + "\n".join(warnings) + "\n"

        progress = await update.message.reply_text(
            f"🎬 *تم استلام الفيديو!*\n"
            f"📊 الحجم: {file_size_mb:.1f}MB | المدة: {duration_s}s\n"
            f"{warning_text}\n"
            f"🎨 سأدمجه داخل تصميم 4Ever الكامل\n"
            f"⏳ سيستغرق 3-5 دقائق على Render Free...\n"
            f"☕ خذ كوب قهوة وأرجع!",
            parse_mode="Markdown"
        )
        try:
            # Download video - may fail if > 20MB
            try:
                tg_file = await ctx.bot.get_file(file_obj.file_id)
            except Exception as e:
                err = str(e).lower()
                if "file is too big" in err or "too large" in err:
                    await progress.edit_text(
                        f"❌ تيليجرام رفض تحميل الفيديو ({file_size_mb:.1f}MB).\n"
                        f"الحد الأقصى للتحميل عبر البوت = 20 MB. اضغط الفيديو وأعد المحاولة."
                    )
                    PENDING_NEWS.pop(user_id, None)
                    return
                raise

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
            await render_with_image(update.message, news, frame_path, progress, video_path=video_path, user_id=user_id)

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


def get_video_dimensions(video_path):
    """Get video width, height using ffmpeg.
    Uses strict regex that only matches Video stream lines (avoiding codec tags like 0x31637661).
    """
    import subprocess
    import imageio_ffmpeg
    ffmpeg_bin = imageio_ffmpeg.get_ffmpeg_exe()
    try:
        result = subprocess.run(
            [ffmpeg_bin, "-i", video_path],
            capture_output=True, text=False, timeout=10
        )
        stderr = result.stderr.decode('utf-8', errors='ignore')

        # Look ONLY at lines that contain "Video:" stream info
        import re
        for line in stderr.split('\n'):
            if 'Video:' not in line:
                continue
            # Match WIDTHxHEIGHT but NOT hex codes (0x...)
            # The dimensions appear as " 1080x1920 " or " 1080x1920," or " 1080x1920 [SAR..."
            matches = re.findall(r'(?<![x\w])(\d{2,5})x(\d{2,5})(?![\w])', line)
            for w_str, h_str in matches:
                w, h = int(w_str), int(h_str)
                # Sanity: real video dims are 100-7680 typically
                if 100 <= w <= 7680 and 100 <= h <= 7680:
                    logger.info(f"   ✅ Video dimensions: {w}x{h}")
                    return w, h
        logger.warning(f"   Could not parse dimensions from ffmpeg output")
        # Log first 500 chars of stderr for debugging
        logger.warning(f"   ffmpeg stderr preview: {stderr[:500]}")
    except Exception as e:
        logger.warning(f"   Could not probe video: {e}")
    return None, None


def choose_canvas_size_for_aspect(media_w, media_h):
    """Choose canvas size (W, H) that matches media aspect ratio.
    For VIDEO performance, keep canvas smaller (less ffmpeg work).
    """
    if not media_w or not media_h:
        return 1080, 1080

    aspect = media_w / media_h
    media_display_w = int(1080 * 0.85)  # 918
    media_display_h = int(media_display_w / aspect)

    # 🚀 Reduced chrome for faster video encoding (smaller canvas = faster ffmpeg)
    CHROME_TOP = 140      # was 175
    CHROME_BOTTOM = 280   # was 400 (was way too much for portrait video)
    PADDING = 30          # was 50

    target_h = media_display_h + CHROME_TOP + CHROME_BOTTOM + PADDING

    # Bounded
    target_h = max(1080, min(target_h, 2100))   # was 2400 - cap lower for speed
    target_h = (target_h // 2) * 2

    logger.info(f"   📐 Media aspect {aspect:.2f} → media {media_display_w}x{media_display_h} → canvas 1080x{target_h}")
    return 1080, target_h


def get_image_dimensions(img_path):
    """Get image dimensions using PIL."""
    try:
        from PIL import Image
        with Image.open(img_path) as img:
            return img.size
    except Exception:
        return None, None


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
    cleanup_old_temp_files(user_id=update.effective_user.id)

    # 🆕 Handle persistent ReplyKeyboard buttons
    if text == "🧹 تنظيف الذاكرة":
        return await cmd_clean(update, ctx)
    if text == "📰 منشور جديد":
        return await cmd_post(update, ctx)
    if text == "📖 المساعدة":
        return await cmd_help(update, ctx)
    if text == "⚙️ الحالة":
        return await cmd_status(update, ctx)
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
    logger.info("🤖 Starting 4Ever Bot v3.6 (NATIVE-VIDEO+CLEAN-MENU+20MB-LIMIT+BETTER-AI)...")
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("post", cmd_post))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("lang", cmd_lang))
    app.add_handler(CommandHandler("clean", cmd_clean))

    # Callback query handlers for inline buttons
    app.add_handler(CallbackQueryHandler(handle_image_choice, pattern=r"^img_(yes|no)_\d+$"))
    app.add_handler(CallbackQueryHandler(handle_language_choice, pattern=r"^lang_(ar|en|fr)_\d+$"))
    app.add_handler(CallbackQueryHandler(handle_dialect_choice, pattern=r"^dial_(fusha|egyptian|levantine|saudi|algerian|emirati|moroccan)_\d+$"))
    app.add_handler(CallbackQueryHandler(handle_regen_callback, pattern=r"^regen_(ai|upload|done)_\d+$"))

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
