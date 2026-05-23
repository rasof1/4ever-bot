"""
News Scout — Gemini multi-model fallback + smart image acquisition.
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

# Fallback chain - each model has SEPARATE daily quota in Gemini Free Tier
FALLBACK_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-flash-latest",
]
_env_model = os.getenv("GEMINI_MODEL")
if _env_model:
    FALLBACK_MODELS = [_env_model] + [m for m in FALLBACK_MODELS if m != _env_model]

log = logging.getLogger("news_scout")
log.setLevel(logging.INFO)
if not log.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter('%(asctime)s | news_scout | %(message)s'))
    log.addHandler(h)


# Domains that serve mostly logos
LOGO_DOMAINS = {
    'freepnglogos.com', 'seeklogo.com', 'logos-world.net', 'logodix.com',
    'logo.wine', 'logosvector.net', 'pngwing.com', 'pngegg.com',
    'pngitem.com', 'pngmart.com', 'cleanpng.com', 'freelogovectors.net',
    'brandfetch.com', 'brandslogos.com', '1000logos.net', 'logotyp.us',
    'pngall.com', 'iconfinder.com', 'flaticon.com', 'icons8.com',
    'shutterstock.com', 'istockphoto.com', 'gettyimages.com',
}


PROMPT_TEMPLATE = r"""أنت كاتب محتوى تقني محترف لصفحة عربية "4Ever".

ابحث في الإنترنت عن آخر خبر حديث ومثير من آخر 7 أيام في أحد هذه المجالات:
- الذكاء الاصطناعي (OpenAI, Anthropic, Google, Meta, Microsoft, xAI)
- الهواتف الذكية (Apple, Samsung, Xiaomi, Google Pixel, OnePlus)
- الألعاب (PlayStation, Xbox, Nintendo, Steam)
- التقنيات (NVIDIA, AMD, Intel, شرائح)
- التسريبات التقنية
- التقنيات الناشئة (سيارات ذاتية، روبوتات، VR/AR)

تنويع: لو غطّيت AI، اختر هاتف أو لعبة في المرة القادمة.

قواعد صارمة:
- headline_line1: أقصى 35 حرف عربي
- headline_line2_ar: أقصى 18 حرف عربي
- headline_line2_en: اسم منتج إنجليزي قصير أو فارغ

image_query مهم جداً للحصول على صورة دقيقة:
- يجب أن يحدد المنتج/التطبيق/الميزة الفعلية بدقة، ليس الموضوع العام
- جيد: "Egypt Digital ID app MCIT screenshot" (يحدد التطبيق والشركة)
- جيد: "NVIDIA RTX 5090 GPU product photo"
- جيد: "iPhone 17 Pro Max camera module render"
- سيء: "digital identity" (عام جداً → نتائج عشوائية)
- سيء: "technology" (عام جداً)
- اذكر دائماً: اسم الشركة/المنتج المحدد + كلمة وصفية (screenshot/photo/render/device)

image_prompt: وصف بصري تفصيلي إنجليزي للصورة (لتوليد AI كاحتياطي)
مثال: "Egyptian Digital Identity mobile app interface, modern UI, fingerprint scanner, blue colors, professional product photography"

hashtags: قائمة 5-8 وسوم:
- 3 وسوم عامة: #تقنية #ذكاء_اصطناعي #4Ever
- 3-5 وسوم محددة بالخبر (شركة + منتج + موضوع)
- مثال للهوية الرقمية: #الهوية_الرقمية #مصر_الرقمية #MCIT #تحول_رقمي #DigitalID
- مثال لـ iPhone: #iPhone17 #Apple #آبل #هواتف_ذكية #iOS

أنتج JSON فقط بدون code fences:

{{
  "headline_line1": "عنوان عربي قصير - أقصى 35 حرف",
  "headline_line2_ar": "السطر 2 - أقصى 18 حرف",
  "headline_line2_en": "اسم منتج إنجليزي قصير أو فارغ",
  "source": "google|openai|anthropic|github|meta|microsoft|apple|nvidia|xai|samsung|sony|nintendo|xiaomi|amd|intel|playstation|xbox|qualcomm|mcit|egypt|huawei|tesla|spacex",
  "category": "ai|phone|gaming|hardware|leak|emerging",
  "product_badge": "تسمية إنجليزية كبيرة قصيرة",
  "live_badge": "مواصفات إنجليزية قصيرة",
  "caption": "كابشن عربي كامل: هوك جذاب + شرح موجز + الأهمية + سؤال تفاعلي + CTA اشتراك 4Ever (لا تضع الهاشتاقات هنا - ستضاف منفصلة)",
  "hashtags": ["#وسم1", "#وسم2", "#وسم3", "#وسم4", "#وسم5"],
  "image_prompt": "وصف بصري إنجليزي تفصيلي للصورة",
  "image_query": "استعلام بحث محدد - شركة + منتج + screenshot/photo/render",
  "source_url": "رابط المقال"
}}

{extra_instructions}"""


DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
}

WIKIPEDIA_HEADERS = {
    "User-Agent": "4EverBot/1.0 (https://t.me/rasof_bot; bot@4ever.com)",
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
    if not url:
        return False
    try:
        host = (urlparse(url).hostname or "").replace("www.", "").lower()
        if host in LOGO_DOMAINS:
            log.warning(f"   🚫 Logo domain: {host}")
            return False
        path_lower = url.lower()
        if any(x in path_lower for x in ['/logo', 'logo.', '-logo-', '_logo_', '/icon', 'icon.', '-icon-']):
            log.warning(f"   🚫 Logo URL: {url[:80]}")
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

        from PIL import Image, ImageStat
        try:
            with Image.open(save_path) as img:
                img.verify()
            with Image.open(save_path) as img:
                w, h = img.size
                if w < 400 or h < 200:
                    os.unlink(save_path)
                    log.warning(f"   🚫 Too small: {w}x{h}")
                    return False
                ratio = max(w, h) / min(w, h)
                if ratio < 1.25:
                    os.unlink(save_path)
                    log.warning(f"   🚫 Too square: {w}x{h}, ratio={ratio:.2f}")
                    return False
                kilopixels = (w * h) / 1000
                bytes_per_kpx = total / kilopixels
                if bytes_per_kpx < 8:
                    os.unlink(save_path)
                    log.warning(f"   🚫 Placeholder: {total//1024}KB for {w}x{h}")
                    return False

            with Image.open(save_path) as img2:
                rgb = img2.convert("RGB")
                stat = ImageStat.Stat(rgb)
                mean_r, mean_g, mean_b = stat.mean
                if mean_r > 230 and mean_g > 230 and mean_b > 230:
                    os.unlink(save_path)
                    log.warning(f"   🚫 Mostly white")
                    return False
                stddev = sum(stat.stddev) / 3
                if stddev < 15:
                    os.unlink(save_path)
                    log.warning(f"   🚫 Low variance")
                    return False
            log.info(f"   ✅ Got {total//1024}KB, {w}x{h}")
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
    log.info(f"🎨 AI image: {prompt[:80]}")
    try:
        clean_prompt = re.sub(r'[^\w\s,.-]', ' ', prompt)
        clean_prompt = re.sub(r'\s+', ' ', clean_prompt).strip()
        full_prompt = f"{clean_prompt}, professional tech photography, dramatic lighting, high detail, 8k"
        import random
        seed = random.randint(1, 999999)
        url = f"https://image.pollinations.ai/prompt/{quote(full_prompt)}?width=1280&height=720&seed={seed}&nologo=true&enhance=true"
        r = requests.get(url, timeout=60)
        if r.status_code != 200:
            return False
        os.makedirs(os.path.dirname(save_path) or ".", exist_ok=True)
        with open(save_path, "wb") as f:
            f.write(r.content)
        from PIL import Image
        img = Image.open(save_path)
        w, h = img.size
        log.info(f"   ✅ AI: {len(r.content)//1024}KB, {w}x{h}")
        return True
    except Exception as e:
        log.warning(f"   AI failed: {e}")
        return False


def search_images_via_bing(query, max_results=8):
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
        log.info(f"   Bing: {len(urls)} candidates")
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
            timeout=15)
        data = r2.json()
        urls = [r.get("image") for r in data.get("results", [])[:max_results] if r.get("image")]
        log.info(f"   DuckDuckGo: {len(urls)}")
        return urls
    except Exception as e:
        log.warning(f"   DDG failed: {e}")
        return []


def find_or_generate_image(news_data, save_path):
    query = news_data.get("image_query", "")
    prompt = news_data.get("image_prompt", "")

    if query:
        log.info(f"🔍 Searching: {query}")
        urls = search_images_via_bing(query)
        if not urls:
            urls = search_images_via_duckduckgo(query)

        for i, url in enumerate(urls[:5]):
            log.info(f"   🔗 Try {i+1}: {url[:80]}")
            if try_download_image(url, save_path):
                return True

    log.info("📡 Search exhausted, generating AI image...")
    ai_prompt = prompt or query or news_data.get("headline_line2_en") or "futuristic technology"
    if generate_ai_image(ai_prompt, save_path):
        return True
    return False


def quality_check(news_data):
    issues = []
    h1 = news_data.get("headline_line1", "")
    if len(h1) > 50:
        issues.append(f"headline_line1 too long ({len(h1)})")
    if len(h1) < 5:
        issues.append("headline_line1 too short")
    h2 = news_data.get("headline_line2_ar", "")
    if len(h2) > 30:
        issues.append(f"headline_line2_ar too long ({len(h2)})")
    if not news_data.get("caption"):
        issues.append("missing caption")
    if not news_data.get("source"):
        issues.append("missing source")
    return (len(issues) == 0, issues)


def truncate_headline(text, max_len):
    if len(text) <= max_len:
        return text
    cut = text[:max_len].rsplit(" ", 1)[0]
    return cut if cut else text[:max_len]


def _call_gemini(prompt, body_overrides=None):
    """Try each fallback model until one succeeds."""
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
    if body_overrides and "generationConfig" in body_overrides:
        body["generationConfig"].update(body_overrides["generationConfig"])

    last_err = None
    for idx, model in enumerate(FALLBACK_MODELS):
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}"
        try:
            log.info(f"🤖 Model {idx+1}/{len(FALLBACK_MODELS)}: {model}")
            r = requests.post(url, json=body, timeout=90)
            if r.status_code == 429:
                log.warning(f"   ⚠️  {model} rate limited, trying next...")
                last_err = RuntimeError(f"{model} rate limited")
                time.sleep(2)
                continue
            if r.status_code == 404:
                log.warning(f"   ⚠️  {model} not found")
                last_err = RuntimeError(f"{model} not found")
                continue
            r.raise_for_status()
            data = r.json()
            if "candidates" not in data or not data["candidates"]:
                last_err = RuntimeError(f"Empty response from {model}")
                continue
            cand = data["candidates"][0]
            parts = cand.get("content", {}).get("parts", [])
            text = "".join(p.get("text", "") for p in parts)
            if not text:
                last_err = RuntimeError(f"No text from {model}")
                continue
            log.info(f"   ✅ {model} OK ({len(text)} chars)")
            return extract_json(text)
        except RuntimeError:
            raise
        except Exception as e:
            last_err = e
            log.warning(f"   {model} failed: {e}")
            continue

    raise RuntimeError(f"All {len(FALLBACK_MODELS)} models exhausted. Last: {last_err}")


def _finalize_result(result):
    """Common post-processing for both scout_news and reverse_scout."""
    for k in ["headline_line1", "headline_line2_ar", "source", "caption"]:
        if k not in result:
            raise ValueError(f"Missing: {k}")
    result["headline_line1"] = truncate_headline(result["headline_line1"], 50)
    result["headline_line2_ar"] = truncate_headline(result["headline_line2_ar"], 30)

    # Ensure hashtags exist and are appended to caption
    hashtags = result.get("hashtags", [])
    if isinstance(hashtags, list) and hashtags:
        hashtag_line = " ".join(h if h.startswith("#") else f"#{h}" for h in hashtags)
        # Only append if not already in caption
        if hashtag_line not in result["caption"]:
            result["caption"] = result["caption"].rstrip() + "\n\n" + hashtag_line

    if not result.get("image_prompt"):
        src = result.get("source", "")
        en = result.get("headline_line2_en", "")
        result["image_prompt"] = f"{src} {en} product, professional".strip()
    if not result.get("image_query"):
        result["image_query"] = result.get("headline_line2_en") or result.get("image_prompt", "")[:50]
    return result


def scout_news(extra_instructions=""):
    prompt = PROMPT_TEMPLATE.format(extra_instructions=extra_instructions or "")
    result = _call_gemini(prompt)
    return _finalize_result(result)


def download_image(news_data, save_path):
    if find_or_generate_image(news_data, save_path):
        return save_path
    raise ValueError("All image strategies failed")


def scout_multiple(count):
    results = []
    avoid = []
    for i in range(count):
        if i > 0:
            time.sleep(6)
        extra = ""
        if avoid:
            extra = f"تجنّب: {', '.join(avoid)}. خبر مختلف ومن مجال آخر."
        try:
            data = scout_news(extra_instructions=extra)
            results.append(data)
            topic = data.get("headline_line2_en") or data["headline_line1"][:30]
            avoid.append(topic)
        except Exception as e:
            results.append({"error": str(e)})
    return results


# ═══════════════════════════════════════════════════════════════
# REVERSE MODE: Receive URL/text/screenshot → generate post
# ═══════════════════════════════════════════════════════════════

REVERSE_PROMPT = r"""أنت محرر تقني محترف لصفحة عربية "4Ever".

استلمت المحتوى التالي من المستخدم وأريدك أن تحوّله إلى منشور 4Ever احترافي.

=== المحتوى ===
{user_content}
================

مهمتك:
1. استخرج الفكرة الرئيسية للخبر
2. تحقّق من صحة المعلومات (ابحث إذا احتجت)
3. أعد صياغته بأسلوب 4Ever الجذاب
4. image_query يجب أن يصف المنتج/التطبيق الفعلي (ليس الموضوع العام!)
5. أضف 5-8 هاشتاقات ترند متعلقة بالخبر

أنتج JSON فقط:

{{
  "headline_line1": "عنوان عربي قصير - أقصى 35 حرف",
  "headline_line2_ar": "السطر 2 - أقصى 18 حرف",
  "headline_line2_en": "اسم منتج إنجليزي قصير أو فارغ",
  "source": "google|openai|anthropic|github|meta|microsoft|apple|nvidia|xai|samsung|sony|nintendo|xiaomi|amd|intel|playstation|xbox|qualcomm|mcit|egypt|huawei|tesla|spacex",
  "category": "ai|phone|gaming|hardware|leak|emerging",
  "product_badge": "تسمية إنجليزية كبيرة قصيرة",
  "live_badge": "مواصفات إنجليزية قصيرة",
  "caption": "كابشن عربي كامل: هوك + شرح + أهمية + سؤال تفاعلي + CTA اشتراك 4Ever",
  "hashtags": ["#وسم1", "#وسم2", "#وسم3", "#وسم4", "#وسم5"],
  "image_prompt": "وصف بصري إنجليزي تفصيلي",
  "image_query": "بحث محدد - شركة + منتج + screenshot/photo/render",
  "source_url": "الرابط الأصلي"
}}
"""


def reverse_scout(user_content):
    prompt = REVERSE_PROMPT.format(user_content=user_content[:8000])
    result = _call_gemini(prompt, body_overrides={
        "generationConfig": {"temperature": 0.7}
    })
    return _finalize_result(result)


# ═══════════════════════════════════════════════════════════════
# URL FETCHING (Facebook, Twitter, regular sites)
# ═══════════════════════════════════════════════════════════════

def fetch_url_content(url):
    log.info(f"📥 Fetching: {url[:80]}")

    fetch_attempts = [
        {"url": url, "headers": {
            "User-Agent": "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)",
            "Accept": "text/html,application/xhtml+xml",
        }},
        {"url": url, "headers": {
            "User-Agent": "Twitterbot/1.0",
            "Accept": "text/html",
        }},
        {"url": url, "headers": {
            "User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
            "Accept": "text/html",
        }},
        {"url": url, "headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html",
            "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
        }},
    ]

    html = ""
    final_url = url
    for i, attempt in enumerate(fetch_attempts):
        try:
            log.info(f"   Attempt {i+1}/{len(fetch_attempts)}")
            r = requests.get(attempt["url"], headers=attempt["headers"],
                             timeout=20, allow_redirects=True)
            if r.status_code == 200 and len(r.text) > 500:
                html = r.text
                final_url = r.url
                log.info(f"   ✅ Got {len(html)} chars")
                break
            else:
                log.warning(f"   Status {r.status_code}, size {len(r.text)}")
        except Exception as e:
            log.warning(f"   Failed: {e}")

    if not html:
        try:
            log.info(f"   🔄 Trying Jina AI reader fallback...")
            r = requests.get(f"https://r.jina.ai/{url}", timeout=30,
                             headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code == 200 and len(r.text) > 200:
                log.info(f"   ✅ Jina returned {len(r.text)} chars")
                return {
                    "title": "", "description": "",
                    "body_text": r.text[:5000], "url": url,
                }
        except Exception as e:
            log.warning(f"   Jina failed: {e}")
        return {"title": "", "description": "", "body_text": "", "url": url,
                "error": "All fetch attempts failed"}

    title_m = re.search(r'<title[^>]*>([^<]+)</title>', html, re.IGNORECASE)
    og_title_m = re.search(r'<meta\s+property=["\']og:title["\']\s+content=["\']([^"\']+)["\']', html, re.IGNORECASE)
    og_desc_m = re.search(r'<meta\s+property=["\']og:description["\']\s+content=["\']([^"\']+)["\']', html, re.IGNORECASE)

    title = (og_title_m.group(1) if og_title_m else (title_m.group(1) if title_m else "")).strip()
    desc = (og_desc_m.group(1) if og_desc_m else "").strip()

    cleaned = re.sub(r'<script.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r'<style.*?</style>', '', cleaned, flags=re.DOTALL | re.IGNORECASE)
    text_only = re.sub(r'<[^>]+>', ' ', cleaned)
    text_only = re.sub(r'\s+', ' ', text_only).strip()

    log.info(f"   📰 Title: {title[:60]}")
    log.info(f"   📝 Body: {len(text_only)} chars")

    return {
        "title": title, "description": desc,
        "body_text": text_only[:5000], "url": final_url,
    }


# ═══════════════════════════════════════════════════════════════
# FINAL VALIDATOR — checks image+headline coherence
# ═══════════════════════════════════════════════════════════════

def validate_post_with_ai(news_data, image_path):
    """
    Final AI quality check: does the image actually match the news?
    Uses Gemini Vision to verify.
    Returns: (is_valid, issues_list, suggestions)
    """
    log.info("🔍 Final AI validator: checking image-headline coherence...")
    try:
        # Encode image as base64
        import base64
        with open(image_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode()

        # Detect mime type
        from PIL import Image
        with Image.open(image_path) as img:
            fmt = img.format.lower() if img.format else "jpeg"
        mime = f"image/{'jpeg' if fmt == 'jpg' else fmt}"

        validate_prompt = (
            f"أنا أعمل منشور تقني عن: {news_data['headline_line1']}\n"
            f"المنتج: {news_data.get('headline_line2_en','')}\n"
            f"المصدر: {news_data.get('source','')}\n\n"
            "تفحّص الصورة المرفقة:\n"
            "1. هل الصورة تتعلق فعلاً بالموضوع؟\n"
            "2. هل تحتوي على ما يخدم الخبر بصرياً؟\n\n"
            'أجب بـ JSON فقط: {"matches": true/false, "reason": "سبب قصير"}'
        )

        body = {
            "contents": [{
                "parts": [
                    {"text": validate_prompt},
                    {"inline_data": {"mime_type": mime, "data": img_b64}}
                ]
            }],
            "generationConfig": {
                "temperature": 0.3,
                "maxOutputTokens": 200,
                "thinkingConfig": {"thinkingBudget": 0},
            }
        }

        # Try just the first 2 fast models for validation (save quota)
        for model in FALLBACK_MODELS[:2]:
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}"
                r = requests.post(url, json=body, timeout=30)
                if r.status_code != 200:
                    continue
                data = r.json()
                if not data.get("candidates"):
                    continue
                text = "".join(p.get("text", "") for p in data["candidates"][0].get("content", {}).get("parts", []))
                if not text:
                    continue
                result = extract_json(text)
                matches = result.get("matches", True)
                reason = result.get("reason", "")
                if matches:
                    log.info(f"   ✅ Image matches: {reason}")
                    return (True, [], reason)
                else:
                    log.warning(f"   ⚠️  Image DOES NOT match: {reason}")
                    return (False, [reason], reason)
            except Exception as e:
                log.warning(f"   Validator {model} failed: {e}")
                continue

        # If validator itself fails, assume OK (don't block on validator error)
        log.warning("   ⚠️  Validator unavailable, proceeding")
        return (True, [], "validator unavailable")
    except Exception as e:
        log.warning(f"   Validator error: {e}")
        return (True, [], str(e))


def acquire_validated_image(news_data, save_path, max_attempts=3):
    """
    Smart image acquisition with AI validation.
    Tries to get an image AND verify it matches the news.
    If validation fails, tries AI generation as fallback.
    """
    for attempt in range(max_attempts):
        log.info(f"📸 Image attempt {attempt+1}/{max_attempts}")

        if attempt == 0:
            # First try: search
            got = find_or_generate_image(news_data, save_path)
        elif attempt == 1:
            # Second try: force AI generation with detailed prompt
            ai_prompt = news_data.get("image_prompt") or news_data.get("image_query") or "tech product"
            got = generate_ai_image(ai_prompt, save_path)
        else:
            # Third: regenerate with category hint
            cat = news_data.get("category", "ai")
            category_hints = {
                "phone": "modern smartphone product render dark background",
                "gaming": "gaming console controller futuristic neon",
                "ai": "artificial intelligence neural network abstract",
                "hardware": "computer chip GPU close up dramatic lighting",
                "leak": "mysterious tech device leaked render",
                "emerging": "futuristic technology concept",
            }
            ai_prompt = f"{news_data.get('image_prompt', '')} {category_hints.get(cat, '')}"
            got = generate_ai_image(ai_prompt, save_path)

        if not got:
            continue

        # Validate
        is_valid, issues, reason = validate_post_with_ai(news_data, save_path)
        if is_valid:
            return True

        log.warning(f"   Image rejected by AI validator: {reason}")
        # Continue to next attempt

    # Last resort: keep whatever we have
    log.warning("   Using last attempted image despite validation issues")
    return os.path.exists(save_path)
