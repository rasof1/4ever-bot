"""
News Scout — Uses Google Gemini 2.5 Flash + smart image extraction.
"""

import os
import json
import re
import time
import requests
from urllib.parse import urlparse

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY not set")

MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"


PROMPT_TEMPLATE = """أنت كاتب محتوى تقني محترف لصفحة عربية "4Ever".

ابحث في الإنترنت عن آخر خبر تقني/AI مهم وحديث من آخر 7 أيام.
أولوية: OpenAI, Anthropic, Google, Meta, Microsoft, Apple, NVIDIA, xAI, GitHub.

⚠️ قواعد صارمة لـ source_url:
- يجب أن يكون رابط مقالة محدّدة (article) وليس صفحة فئة أو قسم
- مثال صحيح: https://blog.google/technology/ai/gemini-2-5-pro-update/
- مثال خاطئ: https://blog.google/products/gemini/ (هذا category)
- يجب أن يحتوي على مسار طويل (3+ شرطات في الـ URL بعد domain)
- يجب أن يكون من المصدر الرسمي للشركة (blog.google, openai.com/blog, anthropic.com/news, apple.com/newsroom, github.blog, ...)

أنتج JSON صارم فقط (بدون code fences، بدون نص قبل/بعد):

{{
  "headline_line1": "عنوان عربي - أقل من 50 حرف",
  "headline_line2_ar": "السطر 2 العربي - أقل من 25 حرف",
  "headline_line2_en": "اسم المنتج بالإنجليزية أو فارغ",
  "source": "google|openai|anthropic|github|meta|microsoft|apple|nvidia|xai",
  "product_badge": "تسمية إنجليزية بأحرف لاتينية كبيرة فقط، لا عربية ولا إيموجي",
  "live_badge": "مواصفات بالإنجليزية فقط لا عربية",
  "caption": "كابشن عربي كامل بهذا القالب:\\n\\n[هوك مع إيموجي 🚀]\\n\\n[شرح في 2-3 أسطر]\\n\\n[الأهمية]\\n\\n🤔 [سؤال] 👇\\n\\n💡 لمزيد من التغطيات، اشترك في 4Ever!\\n\\n#وسم #ذكاء_اصطناعي #4Ever",
  "source_url": "رابط مقالة محدّدة كاملة (ليس صفحة فئة)"
}}

{extra_instructions}"""


# Generic / blacklisted images we should reject
BAD_IMAGE_PATTERNS = [
    "google-200x200",  # Google blog fallback logo
    "blog.google/static",  # Static blog assets
    "favicon",
    "logo-only",
    "default-thumbnail",
    "placeholder",
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


def is_article_url(url):
    """Check if URL looks like a specific article (not category page)."""
    if not url:
        return False
    try:
        p = urlparse(url)
        # Path should have substantial content (article slug)
        path = p.path.strip("/")
        if not path:
            return False
        # Article URLs usually have at least 2 segments and the last is descriptive
        segments = path.split("/")
        last = segments[-1]
        # Last segment should be descriptive (>15 chars with hyphens, OR contain digits/year)
        if len(last) < 15 and not re.search(r"\d{4}", last):
            return False
        return True
    except Exception:
        return False


def extract_og_image(article_url):
    """Fetch article page and extract og:image. Reject obviously bad images."""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml",
        }
        r = requests.get(article_url, headers=headers, timeout=20,
                         allow_redirects=True)
        r.raise_for_status()
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
                # Make absolute
                if img_url.startswith("//"):
                    img_url = "https:" + img_url
                elif img_url.startswith("/"):
                    p = urlparse(article_url)
                    img_url = f"{p.scheme}://{p.netloc}{img_url}"

                # Reject blacklisted/generic images
                low = img_url.lower()
                if any(bad in low for bad in BAD_IMAGE_PATTERNS):
                    print(f"   🚫 Rejected generic: {img_url[:80]}")
                    continue

                return img_url
        return None
    except Exception as e:
        print(f"⚠️  og:image extraction failed: {e}")
        return None


def verify_image_url(url):
    """HEAD request to verify image exists and is large enough."""
    if not url:
        return False
    try:
        r = requests.head(url, timeout=15, allow_redirects=True,
                          headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            return False
        ct = r.headers.get("content-type", "").lower()
        if not ct.startswith("image/"):
            return False
        size = int(r.headers.get("content-length", "0"))
        # Reject tiny images (likely logos/placeholders)
        if 0 < size < 5000:  # <5KB
            print(f"   🚫 Image too small ({size} bytes)")
            return False
        return True
    except Exception:
        return False


def scout_news(extra_instructions="", max_retries=3):
    """Call Gemini, then extract verified article image."""
    prompt = PROMPT_TEMPLATE.format(extra_instructions=extra_instructions or "")

    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "tools": [{"google_search": {}}],
        "generationConfig": {
            "temperature": 0.9,
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
                print(f"⏳ 429, waiting {wait}s...")
                time.sleep(wait)
                continue
            r.raise_for_status()
            data = r.json()

            if "candidates" not in data or not data["candidates"]:
                raise RuntimeError(f"Empty response: {str(data)[:300]}")

            cand = data["candidates"][0]
            parts = cand.get("content", {}).get("parts", [])
            text = "".join(p.get("text", "") for p in parts)

            if not text:
                finish = cand.get("finishReason", "")
                raise RuntimeError(f"No text (finish: {finish})")

            result = extract_json(text)

            for k in ["headline_line1", "headline_line2_ar", "source",
                      "caption", "source_url"]:
                if k not in result:
                    raise ValueError(f"Missing field: {k}")

            # 🎯 Validate source_url is an article (not category)
            src_url = result["source_url"]
            if not is_article_url(src_url):
                print(f"⚠️  source_url is not an article: {src_url}")
                print(f"   Retrying with hint to Gemini...")
                last_err = ValueError(f"Got category URL: {src_url}")
                # Refine prompt
                extra_instructions = (extra_instructions or "") + \
                    f"\n\nالمحاولة السابقة فشلت: أعطيت رابط فئة بدل مقالة ({src_url}). "\
                    "أعطني رابط مقالة محدّدة فيها slug وصفي طويل."
                prompt = PROMPT_TEMPLATE.format(extra_instructions=extra_instructions)
                body["contents"][0]["parts"][0]["text"] = prompt
                time.sleep(3)
                continue

            # 🎯 Smart image extraction
            print(f"🖼️  Extracting og:image from: {src_url}")
            og_image = extract_og_image(src_url)

            if og_image and verify_image_url(og_image):
                print(f"   ✅ Verified: {og_image[:80]}")
                result["image_url"] = og_image
            else:
                print(f"   ⚠️  No valid og:image found")
                result["image_url"] = ""

            return result
        except Exception as e:
            last_err = e
            if attempt < max_retries - 1:
                time.sleep(5)

    if last_err is None:
        raise RuntimeError("Gemini failed after all retries")
    raise last_err


def download_image(url, save_path):
    """Download image with browser-like headers + validation."""
    if not url:
        raise ValueError("Empty image URL")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/120.0.0.0 Safari/537.36",
        "Accept": "image/*,*/*",
        "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
        "Referer": "https://www.google.com/",
    }
    r = requests.get(url, headers=headers, timeout=30, stream=True,
                     allow_redirects=True)
    r.raise_for_status()

    ct = r.headers.get("content-type", "").lower()
    if not (ct.startswith("image/") or "octet-stream" in ct):
        raise ValueError(f"Not an image (got {ct})")

    os.makedirs(os.path.dirname(save_path) or ".", exist_ok=True)
    total = 0
    with open(save_path, "wb") as f:
        for chunk in r.iter_content(8192):
            f.write(chunk)
            total += len(chunk)

    if total < 5000:  # <5KB is probably a logo/placeholder
        os.unlink(save_path)
        raise ValueError(f"Image too small ({total} bytes)")

    from PIL import Image
    try:
        with Image.open(save_path) as img:
            img.verify()
        # Re-open for dimensions (verify closes the file)
        with Image.open(save_path) as img:
            w, h = img.size
            if w < 400 or h < 200:
                os.unlink(save_path)
                raise ValueError(f"Image too small dimensions ({w}x{h})")
    except Exception as e:
        if os.path.exists(save_path):
            os.unlink(save_path)
        raise ValueError(f"Invalid image: {e}")

    return save_path


def scout_multiple(count):
    results = []
    avoid = []
    for i in range(count):
        if i > 0:
            time.sleep(6)
        extra = ""
        if avoid:
            extra = f"تجنّب هذه المواضيع: {', '.join(avoid)}. خبر مختلف."
        try:
            data = scout_news(extra_instructions=extra)
            results.append(data)
            topic = data.get("headline_line2_en") or data["headline_line1"][:30]
            avoid.append(topic)
        except Exception as e:
            results.append({"error": str(e)})
    return results
