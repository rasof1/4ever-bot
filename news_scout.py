"""
News Scout — Uses Google Gemini 2.5 Flash + smart image extraction.

Strategy:
1. Gemini gives us: headlines, caption, source_url
2. We visit source_url and extract og:image (real, reliable image)
3. Fallback to image_url from Gemini if og:image not found
"""

import os
import json
import re
import time
import requests

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY not set")

MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"


PROMPT_TEMPLATE = """أنت كاتب محتوى تقني محترف لصفحة عربية "4Ever".

ابحث في الإنترنت عن آخر خبر تقني/AI مهم وحديث من آخر 7 أيام.
أولوية: OpenAI, Anthropic, Google, Meta, Microsoft, Apple, NVIDIA, xAI, GitHub.

⚠️ مهم: source_url يجب أن يكون رابطاً حقيقياً يمكن فتحه (تأكّد منه).

أنتج JSON صارم فقط (بدون code fences، بدون نص قبل/بعد):

{{
  "headline_line1": "عنوان عربي - أقل من 50 حرف",
  "headline_line2_ar": "السطر 2 العربي - أقل من 25 حرف",
  "headline_line2_en": "اسم المنتج بالإنجليزية أو فارغ",
  "source": "google|openai|anthropic|github|meta|microsoft|apple|nvidia|xai",
  "product_badge": "مثل: GPT-5 • REASONING MODEL",
  "live_badge": "مثل: GPT-5 • 200B • LIVE",
  "caption": "كابشن عربي كامل بهذا القالب:\\n\\n[هوك مع إيموجي 🚀]\\n\\n[شرح في 2-3 أسطر]\\n\\n[الأهمية]\\n\\n🤔 [سؤال] 👇\\n\\n💡 لمزيد من التغطيات، اشترك في 4Ever!\\n\\n#وسم #ذكاء_اصطناعي #4Ever",
  "source_url": "رابط مقال حقيقي يمكن فتحه (مهم جداً)"
}}

{extra_instructions}"""


def extract_json(text):
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    s, e = text.find("{"), text.rfind("}")
    if s == -1 or e == -1:
        raise ValueError(f"No JSON in:\n{text[:500]}")
    return json.loads(text[s:e + 1])


def extract_og_image(article_url):
    """
    Fetch article page and extract Open Graph image URL.
    Falls back to twitter:image, then any large <img>.
    """
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

        # Try og:image (most reliable for articles)
        patterns = [
            r'<meta\s+property=["\']og:image["\']\s+content=["\']([^"\']+)["\']',
            r'<meta\s+content=["\']([^"\']+)["\']\s+property=["\']og:image["\']',
            r'<meta\s+name=["\']twitter:image["\']\s+content=["\']([^"\']+)["\']',
            r'<meta\s+property=["\']og:image:url["\']\s+content=["\']([^"\']+)["\']',
        ]
        for pattern in patterns:
            m = re.search(pattern, html, re.IGNORECASE)
            if m:
                img_url = m.group(1)
                # Make absolute if relative
                if img_url.startswith("//"):
                    img_url = "https:" + img_url
                elif img_url.startswith("/"):
                    from urllib.parse import urlparse
                    p = urlparse(article_url)
                    img_url = f"{p.scheme}://{p.netloc}{img_url}"
                return img_url
        return None
    except Exception as e:
        print(f"⚠️  og:image extraction failed: {e}")
        return None


def scout_news(extra_instructions="", max_retries=3):
    """Call Gemini 2.5 Flash with Google Search, then extract og:image."""
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

            # 🎯 Smart image extraction: fetch og:image from source article
            print(f"🖼️  Extracting og:image from: {result['source_url']}")
            og_image = extract_og_image(result["source_url"])
            if og_image:
                print(f"   ✅ Found: {og_image[:80]}")
                result["image_url"] = og_image
            else:
                print(f"   ⚠️  No og:image found, will use placeholder")
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
    """Download image with browser-like headers + content-type check."""
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

    if total < 1000:  # < 1KB is probably a broken response
        os.unlink(save_path)
        raise ValueError(f"Downloaded file too small ({total} bytes)")

    # Verify it's actually a valid image
    from PIL import Image
    try:
        with Image.open(save_path) as img:
            img.verify()
    except Exception as e:
        os.unlink(save_path)
        raise ValueError(f"Invalid image file: {e}")

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
