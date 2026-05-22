"""
News Scout — Gemini 2.5 Flash + multi-source image strategy.

Strategy:
1. Gemini finds the news AND lists multiple potential image URLs.
2. We try each URL in order until one downloads successfully.
3. Last resort: try og:image from source article.
"""

import os
import json
import re
import time
import requests
import logging
from urllib.parse import urlparse, quote_plus

log = logging.getLogger('news_scout')
log.setLevel(logging.INFO)
if not log.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter('%(asctime)s | news_scout | %(message)s'))
    log.addHandler(h)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY not set")

MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"


PROMPT_TEMPLATE = """أنت كاتب محتوى تقني محترف لصفحة عربية "4Ever".

ابحث في الإنترنت عن آخر خبر حديث ومثير من آخر 7 أيام في أحد هذه المجالات:
- 🤖 الذكاء الاصطناعي (OpenAI, Anthropic, Google, Meta, Microsoft, xAI)
- 📱 الهواتف الذكية (Apple, Samsung, Xiaomi, Google Pixel, OnePlus)
- 🎮 الألعاب والـ Gaming (PlayStation, Xbox, Nintendo, Steam, إطلاقات ألعاب)
- 💻 التقنيات والحوسبة (NVIDIA, AMD, Intel, شرائح, GPU, معالجات)
- 🔓 التسريبات التقنية (تسريبات منتجات قادمة، شائعات موثوقة)
- 🚗 التقنيات الناشئة (سيارات ذاتية القيادة، روبوتات، VR/AR)

تنويع: لا تختر دائماً نفس النوع. لو سبق وغطّيت AI، اختر هاتف أو لعبة.

⚠️ قواعد صور المنتجات:
- استخدم Google Search لتجد روابط صور حقيقية مباشرة
- اقترح 3 روابط صور مختلفة (في حال فشل أحدها)
- اختر صور كبيرة (>800x400 بكسل) من مصادر موثوقة:
  - Wikipedia/Wikimedia: upload.wikimedia.org
  - مواقع التقنية: theverge.com, techcrunch.com, engadget.com
  - مواقع الألعاب: ign.com, gamespot.com, polygon.com
  - مواقع الشركات الرسمية
- الصور يجب أن تكون متاحة بدون تسجيل دخول

أنتج JSON فقط (بدون code fences):

{{
  "headline_line1": "عنوان عربي - أقل من 50 حرف",
  "headline_line2_ar": "السطر 2 - أقل من 25 حرف",
  "headline_line2_en": "اسم المنتج بالإنجليزية أو فارغ",
  "source": "google|openai|anthropic|github|meta|microsoft|apple|nvidia|xai|samsung|sony|nintendo|xiaomi|amd|intel",
  "category": "ai|phone|gaming|hardware|leak|emerging",
  "product_badge": "تسمية إنجليزية كبيرة فقط - مثل: GPT-5 • REASONING أو iPhone 17 • PRO",
  "live_badge": "مواصفات بالإنجليزية - مثل: A19 PRO • 200B • LIVE",
  "caption": "كابشن عربي كامل:\\n\\n[هوك مع إيموجي 🚀]\\n\\n[شرح في 2-3 أسطر]\\n\\n[الأهمية]\\n\\n🤔 [سؤال تفاعلي] 👇\\n\\n💡 لمزيد من التغطيات، اشترك في 4Ever!\\n\\n#وسم #تقنية #4Ever",
  "image_urls": [
    "https://رابط_صورة_1_مباشر.jpg",
    "https://رابط_صورة_2_احتياطي.jpg",
    "https://رابط_صورة_3_احتياطي.jpg"
  ],
  "source_url": "رابط المقال الأصلي"
}}

{extra_instructions}"""


# Generic / blacklisted images
BAD_IMAGE_PATTERNS = [
    "google-200x200", "blog.google/static", "favicon",
    "logo-only", "default-thumbnail", "placeholder",
    "/logo.", "social-image", "site-icon",
]


def extract_json(text):
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    s, e = text.find("{"), text.rfind("}")
    if s == -1 or e == -1:
        raise ValueError(f"No JSON in:\n{text[:500]}")
    return json.loads(text[s:e + 1])


def is_bad_image(url):
    if not url:
        return True
    low = url.lower()
    return any(bad in low for bad in BAD_IMAGE_PATTERNS)


def try_download_image(url, save_path):
    """Try downloading an image, return True on success."""
    if not url or is_bad_image(url):
        return False
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0.0.0 Safari/537.36",
            "Accept": "image/*,*/*",
            "Referer": "https://www.google.com/",
        }
        r = requests.get(url, headers=headers, timeout=20, stream=True,
                         allow_redirects=True)
        if r.status_code != 200:
            return False
        ct = r.headers.get("content-type", "").lower()
        if not (ct.startswith("image/") or "octet-stream" in ct):
            return False

        os.makedirs(os.path.dirname(save_path) or ".", exist_ok=True)
        total = 0
        with open(save_path, "wb") as f:
            for chunk in r.iter_content(8192):
                f.write(chunk)
                total += len(chunk)

        if total < 5000:
            os.unlink(save_path)
            return False

        from PIL import Image
        try:
            with Image.open(save_path) as img:
                img.verify()
            with Image.open(save_path) as img:
                w, h = img.size
                if w < 400 or h < 200:
                    os.unlink(save_path)
                    return False
            return True
        except Exception:
            if os.path.exists(save_path):
                os.unlink(save_path)
            return False
    except Exception as e:
        log.warning(f"   ⚠️  Download attempt failed: {e}")
        return False


def extract_og_image(article_url):
    """Last-resort: fetch article and extract og:image."""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0.0.0 Safari/537.36",
        }
        r = requests.get(article_url, headers=headers, timeout=15,
                         allow_redirects=True)
        if r.status_code != 200:
            return None
        html = r.text
        patterns = [
            r'<meta\s+property=["\']og:image:secure_url["\']\s+content=["\']([^"\']+)["\']',
            r'<meta\s+property=["\']og:image["\']\s+content=["\']([^"\']+)["\']',
            r'<meta\s+content=["\']([^"\']+)["\']\s+property=["\']og:image["\']',
            r'<meta\s+name=["\']twitter:image["\']\s+content=["\']([^"\']+)["\']',
        ]
        for pattern in patterns:
            for m in re.finditer(pattern, html, re.IGNORECASE):
                img_url = m.group(1)
                if img_url.startswith("//"):
                    img_url = "https:" + img_url
                elif img_url.startswith("/"):
                    p = urlparse(article_url)
                    img_url = f"{p.scheme}://{p.netloc}{img_url}"
                if not is_bad_image(img_url):
                    return img_url
        return None
    except Exception:
        return None


def find_working_image(image_urls, source_url, save_path):
    """Try each candidate URL, then fall back to og:image."""
    # Try each Gemini-provided URL
    for i, url in enumerate(image_urls or []):
        log.info(f"   🔗 Try {i+1}/{len(image_urls)}: {url[:80]}")
        if try_download_image(url, save_path):
            log.info(f"   ✅ Success!")
            return True
        log.info(f"   ❌ Failed")

    # Fallback: og:image from source article
    if source_url:
        log.info(f"   🔄 Fallback: extracting og:image from source...")
        og = extract_og_image(source_url)
        if og:
            log.info(f"   🔗 og:image: {og[:80]}")
            if try_download_image(og, save_path):
                log.info(f"   ✅ Success!")
                return True

    log.info(f"   ❌ All image sources failed")
    return False


def scout_news(extra_instructions="", max_retries=3):
    """Call Gemini, get news + multiple image URLs."""
    prompt = PROMPT_TEMPLATE.format(extra_instructions=extra_instructions or "")

    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "tools": [{"google_search": {}}],
        "generationConfig": {
            "temperature": 1.0,
            "topP": 0.95,
            "maxOutputTokens": 4096,
            "thinkingConfig": {"thinkingBudget": 0},
        }
    }

    url = f"{API_URL}?key={GEMINI_API_KEY}"

    last_err = None
    for attempt in range(max_retries):
        try:
            r = requests.post(url, json=body, timeout=90)
            if r.status_code == 429:
                last_err = RuntimeError(f"Gemini rate limited (attempt {attempt+1})")
                wait = 20 * (attempt + 1)
                log.info(f"⏳ 429, waiting {wait}s...")
                time.sleep(wait)
                continue
            r.raise_for_status()
            data = r.json()

            if "candidates" not in data or not data["candidates"]:
                raise RuntimeError(f"Empty: {str(data)[:300]}")

            cand = data["candidates"][0]
            parts = cand.get("content", {}).get("parts", [])
            text = "".join(p.get("text", "") for p in parts)

            if not text:
                raise RuntimeError(f"No text (finish: {cand.get('finishReason')})")

            result = extract_json(text)

            for k in ["headline_line1", "headline_line2_ar", "source", "caption"]:
                if k not in result:
                    raise ValueError(f"Missing: {k}")

            # Normalize image_urls (handle both list and single string)
            if "image_urls" not in result:
                result["image_urls"] = []
            if isinstance(result["image_urls"], str):
                result["image_urls"] = [result["image_urls"]]
            # Also accept old 'image_url' field
            if "image_url" in result and result["image_url"]:
                result["image_urls"].insert(0, result["image_url"])

            return result
        except Exception as e:
            last_err = e
            if attempt < max_retries - 1:
                time.sleep(5)

    if last_err is None:
        raise RuntimeError("Gemini failed after all retries")
    raise last_err


def download_image(news_data, save_path):
    """Smart image download: tries multiple URLs from news_data."""
    image_urls = news_data.get("image_urls", [])
    source_url = news_data.get("source_url", "")
    if find_working_image(image_urls, source_url, save_path):
        return save_path
    raise ValueError("No working image found after trying all sources")


def scout_multiple(count):
    results = []
    avoid = []
    for i in range(count):
        if i > 0:
            time.sleep(6)
        extra = ""
        if avoid:
            extra = f"تجنّب هذه المواضيع: {', '.join(avoid)}. خبر مختلف ومن مجال آخر."
        try:
            data = scout_news(extra_instructions=extra)
            results.append(data)
            topic = data.get("headline_line2_en") or data["headline_line1"][:30]
            avoid.append(topic)
        except Exception as e:
            results.append({"error": str(e)})
    return results
