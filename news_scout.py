"""
News Scout — Gemini 2.5 Flash + robust image fetching.
"""

import os
import json
import re
import time
import logging
import requests
from urllib.parse import urlparse, quote

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

⚠️ مهم جداً عن image_query: لا تعطنا روابط صور (لأنها تفشل غالباً).
بدلاً من ذلك، أعطنا **استعلام بحث صور باللغة الإنجليزية فقط** يصف المنتج/الموضوع بدقة.
أمثلة جيدة:
- "NVIDIA Blackwell B200 GPU"
- "Apple iPhone 17 Pro"
- "GTA 6 official screenshot"
- "Samsung Galaxy S26 Ultra"

أنتج JSON فقط (بدون code fences):

{{
  "headline_line1": "عنوان عربي - أقل من 50 حرف",
  "headline_line2_ar": "السطر 2 - أقل من 25 حرف",
  "headline_line2_en": "اسم المنتج بالإنجليزية أو فارغ",
  "source": "google|openai|anthropic|github|meta|microsoft|apple|nvidia|xai|samsung|sony|nintendo|xiaomi|amd|intel|playstation|xbox|qualcomm",
  "category": "ai|phone|gaming|hardware|leak|emerging",
  "product_badge": "تسمية إنجليزية بأحرف لاتينية كبيرة فقط",
  "live_badge": "مواصفات بالإنجليزية فقط",
  "caption": "كابشن عربي كامل:\\n\\n[هوك مع إيموجي 🚀]\\n\\n[شرح في 2-3 أسطر]\\n\\n[الأهمية]\\n\\n🤔 [سؤال تفاعلي] 👇\\n\\n💡 لمزيد من التغطيات، اشترك في 4Ever!\\n\\n#وسم #تقنية #4Ever",
  "image_query": "استعلام بحث صور بالإنجليزية يصف المنتج/الموضوع",
  "source_url": "رابط المقال الأصلي"
}}

{extra_instructions}"""


# Browser-like headers that work on most sites
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
}

# Wikipedia requires a specific user-agent
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
    """Try downloading an image, return True on success."""
    if not url:
        return False
    try:
        # Pick right headers based on domain
        if "wikipedia.org" in url or "wikimedia.org" in url:
            headers = WIKIPEDIA_HEADERS
        else:
            headers = DEFAULT_HEADERS

        r = requests.get(url, headers=headers, timeout=20, stream=True,
                         allow_redirects=True)
        if r.status_code != 200:
            log.warning(f"   HTTP {r.status_code} for {url[:60]}")
            return False
        ct = r.headers.get("content-type", "").lower()
        if not (ct.startswith("image/") or "octet-stream" in ct):
            log.warning(f"   Not an image: {ct} for {url[:60]}")
            return False

        os.makedirs(os.path.dirname(save_path) or ".", exist_ok=True)
        total = 0
        with open(save_path, "wb") as f:
            for chunk in r.iter_content(8192):
                f.write(chunk)
                total += len(chunk)

        if total < 5000:
            os.unlink(save_path)
            log.warning(f"   Too small: {total} bytes")
            return False

        from PIL import Image
        try:
            with Image.open(save_path) as img:
                img.verify()
            with Image.open(save_path) as img:
                w, h = img.size
                if w < 400 or h < 200:
                    os.unlink(save_path)
                    log.warning(f"   Bad dimensions: {w}x{h}")
                    return False
            log.info(f"   ✅ Got {total//1024}KB, {w}x{h}")
            return True
        except Exception as ex:
            if os.path.exists(save_path):
                os.unlink(save_path)
            log.warning(f"   Invalid image: {ex}")
            return False
    except Exception as e:
        log.warning(f"   Exception: {e}")
        return False


def search_images_via_bing(query, max_results=8):
    """
    Use Bing image search via its public scrape endpoint.
    Returns list of direct image URLs.
    """
    try:
        url = f"https://www.bing.com/images/search?q={quote(query)}&form=HDRSC2&first=1"
        r = requests.get(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
        }, timeout=15)
        if r.status_code != 200:
            log.warning(f"   Bing search returned {r.status_code}")
            return []

        # Bing embeds image metadata in 'm' attribute as JSON
        # Pattern: m="{...&quot;murl&quot;:&quot;https://...&quot;...}"
        urls = []
        for m in re.finditer(r'murl&quot;:&quot;(https?://[^&"]+?)&quot;', r.text):
            img_url = m.group(1).replace("&amp;", "&")
            urls.append(img_url)
            if len(urls) >= max_results:
                break
        log.info(f"   Bing found {len(urls)} candidate images")
        return urls
    except Exception as e:
        log.warning(f"   Bing search failed: {e}")
        return []


def search_images_via_duckduckgo(query, max_results=8):
    """Fallback: DuckDuckGo image search (no API key needed)."""
    try:
        # Get vqd token
        r = requests.get(f"https://duckduckgo.com/?q={quote(query)}&iax=images&ia=images",
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        m = re.search(r'vqd=([\d-]+)', r.text)
        if not m:
            return []
        vqd = m.group(1)

        r2 = requests.get(
            f"https://duckduckgo.com/i.js?l=us-en&o=json&q={quote(query)}&vqd={vqd}&f=,,,,,&p=1",
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://duckduckgo.com/",
            }, timeout=15
        )
        data = r2.json()
        urls = [r.get("image") for r in data.get("results", [])[:max_results] if r.get("image")]
        log.info(f"   DuckDuckGo found {len(urls)} candidate images")
        return urls
    except Exception as e:
        log.warning(f"   DuckDuckGo failed: {e}")
        return []


def find_working_image(image_query, save_path):
    """
    Strategy: Search Bing → DuckDuckGo for the query, try each result.
    """
    if not image_query:
        log.warning("   No image_query provided")
        return False

    log.info(f"🔍 Searching for: {image_query}")

    # Try Bing first
    urls = search_images_via_bing(image_query)

    # Fallback to DuckDuckGo
    if not urls:
        log.info("   Bing returned nothing, trying DuckDuckGo...")
        urls = search_images_via_duckduckgo(image_query)

    if not urls:
        log.warning("   No image candidates from any search engine")
        return False

    # Try each URL until one works
    for i, url in enumerate(urls):
        log.info(f"   🔗 Try {i+1}/{len(urls)}: {url[:80]}")
        if try_download_image(url, save_path):
            return True

    log.warning("   ❌ All search results failed")
    return False


def scout_news(extra_instructions="", max_retries=3):
    """Call Gemini, get news + image search query."""
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

            # Build a smart fallback query from EN headline + source
            if not result.get("image_query"):
                src = result.get("source", "")
                en = result.get("headline_line2_en", "")
                cat = result.get("category", "")
                if en and src:
                    result["image_query"] = f"{src} {en}"
                elif en:
                    result["image_query"] = en
                elif src:
                    result["image_query"] = f"{src} {cat}"
                else:
                    result["image_query"] = result["headline_line1"][:50]

            return result
        except Exception as e:
            last_err = e
            if attempt < max_retries - 1:
                time.sleep(5)

    if last_err is None:
        raise RuntimeError("Gemini failed after all retries")
    raise last_err


def download_image(news_data, save_path):
    """Search for and download an image based on news_data."""
    query = news_data.get("image_query", "")
    if find_working_image(query, save_path):
        return save_path
    raise ValueError(f"No working image found for query: {query}")


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
