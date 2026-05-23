"""
News Scout — Gemini 2.5 Flash + multi-strategy image fetching.

Image strategy (in order):
1. Bing image search (works in dev)
2. DuckDuckGo image search (works in dev)  
3. Pollinations.ai AI image generation (ALWAYS works, free)
"""

import os
import json
import re
import time
import logging
import requests
from urllib.parse import urlparse, quote

# Domains that serve mostly logos (avoid these)
LOGO_DOMAINS = {
    'freepnglogos.com', 'seeklogo.com', 'logos-world.net', 'logodix.com',
    'logo.wine', 'logosvector.net', 'pngwing.com', 'pngegg.com',
    'pngitem.com', 'pngmart.com', 'cleanpng.com', 'freelogovectors.net',
    'brandfetch.com', 'brandslogos.com', '1000logos.net', 'logotyp.us',
    'pngall.com', 'iconfinder.com', 'flaticon.com', 'icons8.com',
    'shutterstock.com', 'istockphoto.com', 'gettyimages.com',  # watermarks
}

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY not set")

MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"

log = logging.getLogger("news_scout")
log.setLevel(logging.INFO)
if not log.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter('%(asctime)s | news_scout | %(message)s'))
    log.addHandler(h)


PROMPT_TEMPLATE = """أنت كاتب محتوى تقني محترف لصفحة عربية "4Ever".

ابحث في الإنترنت عن آخر خبر حديث ومثير من آخر 7 أيام في أحد هذه المجالات:
- 🤖 الذكاء الاصطناعي (OpenAI, Anthropic, Google, Meta, Microsoft, xAI)
- 📱 الهواتف الذكية (Apple, Samsung, Xiaomi, Google Pixel, OnePlus)
- 🎮 الألعاب (PlayStation, Xbox, Nintendo, Steam)
- 💻 التقنيات (NVIDIA, AMD, Intel, شرائح)
- 🔓 التسريبات التقنية
- 🚗 التقنيات الناشئة (سيارات ذاتية، روبوتات، VR/AR)

تنويع: لو غطّيت AI، اختر هاتف أو لعبة في المرة القادمة.

⚠️ قواعد صارمة:
- headline_line1: أقصى 35 حرف عربي (يجب أن يدخل في إطار التصميم)
- headline_line2_ar: أقصى 18 حرف عربي
- headline_line2_en: اسم منتج إنجليزي قصير، أو فارغ
- image_prompt: وصف إنجليزي تفصيلي للصورة (سيُستخدم لتوليد صورة AI)

أنتج JSON فقط (بدون code fences):

{{
  "headline_line1": "عنوان عربي قصير - أقصى 35 حرف",
  "headline_line2_ar": "السطر 2 - أقصى 18 حرف",
  "headline_line2_en": "اسم منتج إنجليزي قصير أو فارغ",
  "source": "google|openai|anthropic|github|meta|microsoft|apple|nvidia|xai|samsung|sony|nintendo|xiaomi|amd|intel|playstation|xbox|qualcomm",
  "category": "ai|phone|gaming|hardware|leak|emerging",
  "product_badge": "تسمية إنجليزية كبيرة قصيرة",
  "live_badge": "مواصفات إنجليزية قصيرة",
  "caption": "كابشن عربي كامل بالقالب المعتاد",
  "image_prompt": "وصف تفصيلي إنجليزي بصري للصورة - ركّز على الشكل البصري والألوان، تجنّب النصوص والشعارات. مثال جيد: 'futuristic smartphone with multiple cameras, glossy black surface, dramatic lighting, professional product photography' أو 'gaming console controller floating in dark space with neon lights, cinematic'",
  "image_query": "استعلام بحث صور بسيط بالإنجليزية - 4-6 كلمات",
  "source_url": "رابط المقال"
}}

⚠️ مثال على الكابشن:

🚀 [هوك جذاب]

[شرح في 2-3 أسطر]

[الأهمية]

🤔 [سؤال] 👇

💡 لمزيد من التغطيات، اشترك في 4Ever!

#تقنية #ذكاء_اصطناعي #4Ever

{extra_instructions}"""


DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
}

WIKIPEDIA_HEADERS = {
    "User-Agent": "4EverBot/1.0 (https://t.me/rasof_bot; bot@4ever.com) requests/2.32",
    "Accept": "image/*",
}


def extract_json(text):
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    s, e = text.find("{"), text.rfind("}")
    if s == -1 or e == -1:
        raise ValueError(f"No JSON in:\n{text[:500]}")
    return json.loads(text[s:e + 1])


def try_download_image(url, save_path):
    """Try downloading an image, return True on success.
    Rejects logos, watermarks, and square images (likely logos)."""
    if not url:
        return False

    # Reject known logo/watermark domains
    try:
        host = urlparse(url).hostname or ""
        host = host.replace("www.", "").lower()
        if host in LOGO_DOMAINS:
            log.warning(f"   🚫 Logo domain rejected: {host}")
            return False
        # Also reject if URL path contains 'logo' or 'icon'
        path_lower = url.lower()
        if any(x in path_lower for x in ['/logo', 'logo.', '-logo-', '_logo_', '/icon', 'icon.', '-icon-']):
            log.warning(f"   🚫 Logo URL rejected: {url[:80]}")
            return False
    except Exception:
        pass

    try:
        headers = WIKIPEDIA_HEADERS if ("wikipedia.org" in url or "wikimedia.org" in url) else DEFAULT_HEADERS
        r = requests.get(url, headers=headers, timeout=25, stream=True, allow_redirects=True)
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
                    log.warning(f"   🚫 Too small: {w}x{h}")
                    return False
                # 🎯 Reject square-ish images (logos are usually 1:1, photos are 16:9 or 4:3)
                ratio = max(w, h) / min(w, h)
                if ratio < 1.25:
                    os.unlink(save_path)
                    log.warning(f"   🚫 Too square (logo likely): {w}x{h}, ratio={ratio:.2f}")
                    return False
            log.info(f"   ✅ Got {total//1024}KB, {w}x{h}, ratio={ratio:.2f}")
            return True
        except Exception as ex:
            if os.path.exists(save_path):
                os.unlink(save_path)
            log.warning(f"   Invalid: {ex}")
            return False
    except Exception as e:
        log.warning(f"   Exception: {e}")
        return False


def generate_ai_image(prompt, save_path):
    """
    Generate an image using Pollinations.ai (free, unlimited, no API key).
    Always succeeds.
    """
    log.info(f"🎨 Generating AI image: {prompt[:80]}")
    try:
        # Clean prompt and add quality boosters
        clean_prompt = re.sub(r'[^\w\s,.-]', ' ', prompt)
        clean_prompt = re.sub(r'\s+', ' ', clean_prompt).strip()
        full_prompt = f"{clean_prompt}, professional tech photography, dramatic lighting, high detail, 8k"

        # Pollinations.ai endpoint
        encoded = quote(full_prompt)
        # seed for variety, nologo to avoid watermark
        import random
        seed = random.randint(1, 999999)
        url = f"https://image.pollinations.ai/prompt/{encoded}?width=1280&height=720&seed={seed}&nologo=true&enhance=true"

        r = requests.get(url, timeout=60)
        if r.status_code != 200:
            log.warning(f"   Pollinations returned {r.status_code}")
            return False

        os.makedirs(os.path.dirname(save_path) or ".", exist_ok=True)
        with open(save_path, "wb") as f:
            f.write(r.content)

        from PIL import Image
        img = Image.open(save_path)
        w, h = img.size
        log.info(f"   ✅ AI image generated: {len(r.content)//1024}KB, {w}x{h}")
        return True
    except Exception as e:
        log.warning(f"   AI generation failed: {e}")
        return False


def search_images_via_bing(query, max_results=8):
    """Bing image search via public scrape."""
    try:
        url = f"https://www.bing.com/images/search?q={quote(query)}&form=HDRSC2&first=1"
        r = requests.get(url, headers=DEFAULT_HEADERS, timeout=15)
        if r.status_code != 200:
            return []
        urls = []
        for m in re.finditer(r'murl&quot;:&quot;(https?://[^&"]+?)&quot;', r.text):
            img_url = m.group(1).replace("&amp;", "&")
            urls.append(img_url)
            if len(urls) >= max_results:
                break
        log.info(f"   Bing found {len(urls)} candidates")
        return urls
    except Exception as e:
        log.warning(f"   Bing failed: {e}")
        return []


def search_images_via_duckduckgo(query, max_results=8):
    try:
        r = requests.get(f"https://duckduckgo.com/?q={quote(query)}&iax=images&ia=images",
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        m = re.search(r'vqd=([\d-]+)', r.text)
        if not m:
            return []
        vqd = m.group(1)
        r2 = requests.get(
            f"https://duckduckgo.com/i.js?l=us-en&o=json&q={quote(query)}&vqd={vqd}&f=,,,,,&p=1",
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://duckduckgo.com/"},
            timeout=15
        )
        data = r2.json()
        urls = [r.get("image") for r in data.get("results", [])[:max_results] if r.get("image")]
        log.info(f"   DuckDuckGo found {len(urls)}")
        return urls
    except Exception as e:
        log.warning(f"   DuckDuckGo failed: {e}")
        return []


def find_or_generate_image(news_data, save_path):
    """
    Multi-strategy image acquisition:
    1. Try Bing search for real photo
    2. Try DuckDuckGo
    3. Fall back to AI generation (always works)
    """
    query = news_data.get("image_query", "")
    prompt = news_data.get("image_prompt", "")

    # Strategy 1+2: Real image search (faster, more authentic when works)
    if query:
        log.info(f"🔍 Searching: {query}")
        urls = search_images_via_bing(query)
        if not urls:
            urls = search_images_via_duckduckgo(query)

        for i, url in enumerate(urls[:5]):  # Try only top 5 to save time
            log.info(f"   🔗 Try {i+1}: {url[:80]}")
            if try_download_image(url, save_path):
                return True

    # Strategy 3: AI generation (always works on Render)
    log.info("📡 Real image search failed, generating with AI...")
    ai_prompt = prompt or query or news_data.get("headline_line2_en") or "futuristic technology"
    if generate_ai_image(ai_prompt, save_path):
        return True

    return False


def quality_check(news_data):
    """
    Quality control: validate news data before sending.
    Returns (is_valid, issues) tuple.
    """
    issues = []

    h1 = news_data.get("headline_line1", "")
    if len(h1) > 50:
        issues.append(f"headline_line1 too long ({len(h1)} chars, max 50)")
    if len(h1) < 5:
        issues.append("headline_line1 too short")

    h2 = news_data.get("headline_line2_ar", "")
    if len(h2) > 30:
        issues.append(f"headline_line2_ar too long ({len(h2)} chars, max 30)")

    en = news_data.get("headline_line2_en", "")
    if len(en) > 30:
        issues.append(f"headline_line2_en too long ({len(en)} chars)")

    if not news_data.get("caption"):
        issues.append("missing caption")
    elif len(news_data["caption"]) < 50:
        issues.append("caption too short")

    if not news_data.get("source"):
        issues.append("missing source")

    return (len(issues) == 0, issues)


def truncate_headline(text, max_len):
    """Smart truncation: cut at word boundary."""
    if len(text) <= max_len:
        return text
    # Find last space before max_len
    cut = text[:max_len].rsplit(" ", 1)[0]
    return cut if cut else text[:max_len]


def scout_news(extra_instructions="", max_retries=3):
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

            # 🎯 Quality control: enforce length limits
            result["headline_line1"] = truncate_headline(result["headline_line1"], 50)
            result["headline_line2_ar"] = truncate_headline(result["headline_line2_ar"], 30)

            # Validate
            ok, issues = quality_check(result)
            if not ok:
                log.warning(f"⚠️  Quality issues: {issues}")
                # Don't fail, just log

            # Ensure image_prompt exists for AI fallback
            if not result.get("image_prompt"):
                src = result.get("source", "")
                en = result.get("headline_line2_en", "")
                cat = result.get("category", "")
                result["image_prompt"] = f"{src} {en} {cat} product photography, professional".strip()
            if not result.get("image_query"):
                result["image_query"] = result.get("headline_line2_en") or result.get("image_prompt", "")[:50]

            return result
        except Exception as e:
            last_err = e
            if attempt < max_retries - 1:
                time.sleep(5)

    if last_err is None:
        raise RuntimeError("Gemini failed after all retries")
    raise last_err


def download_image(news_data, save_path):
    """Multi-strategy image acquisition."""
    if find_or_generate_image(news_data, save_path):
        return save_path
    raise ValueError("All image strategies failed (this should never happen)")


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
