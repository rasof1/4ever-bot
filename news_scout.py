"""
News Scout — Uses Google Gemini (FREE tier) with Google Search grounding.
"""

import os
import json
import re
import time
import requests

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY not set")

MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"


PROMPT_TEMPLATE = """أنت كاتب محتوى تقني لصفحة عربية "4Ever".

ابحث في الإنترنت عن آخر خبر تقني/AI من آخر 7 أيام.
أولوية: OpenAI, Anthropic, Google, Meta, Microsoft, Apple, NVIDIA, xAI, GitHub.

أنتج JSON صارم فقط (بدون code fences، بدون نص قبل/بعد):

{{
  "headline_line1": "عنوان عربي - أقل من 50 حرف",
  "headline_line2_ar": "السطر 2 العربي - أقل من 25 حرف",
  "headline_line2_en": "اسم المنتج بالإنجليزية أو فارغ",
  "source": "google|openai|anthropic|github|meta|microsoft|apple|nvidia|xai",
  "product_badge": "مثل: GPT-5 • REASONING MODEL",
  "live_badge": "مثل: GPT-5 • 200B • LIVE",
  "caption": "كابشن عربي كامل بهذا القالب:\\n\\n[هوك مع إيموجي 🚀]\\n\\n[شرح في 2-3 أسطر]\\n\\n[الأهمية]\\n\\n🤔 [سؤال] 👇\\n\\n💡 لمزيد من التغطيات، اشترك في 4Ever!\\n\\n#وسم #ذكاء_اصطناعي #4Ever",
  "image_url": "رابط HTTPS مباشر لصورة .jpg/.png من المصدر",
  "source_url": "رابط المقال"
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


def scout_news(extra_instructions="", max_retries=3):
    """Call Gemini with Google Search. Retries on 429 with backoff."""
    prompt = PROMPT_TEMPLATE.format(extra_instructions=extra_instructions or "")

    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "tools": [{"google_search": {}}],
        "generationConfig": {
            "temperature": 0.9,
            "topP": 0.95,
            "maxOutputTokens": 4096,
        }
    }

    url = f"{API_URL}?key={GEMINI_API_KEY}"

    last_err = None
    for attempt in range(max_retries):
        try:
            r = requests.post(url, json=body, timeout=90)
            if r.status_code == 429:
                wait = 30 * (attempt + 1)
                last_err = RuntimeError(f"Gemini rate limited (429) on attempt {attempt+1}")
                print(f"⏳ Rate limited, waiting {wait}s...")
                time.sleep(wait)
                continue
            r.raise_for_status()
            data = r.json()

            if "candidates" not in data or not data["candidates"]:
                raise RuntimeError(f"Empty response: {data}")

            cand = data["candidates"][0]
            parts = cand.get("content", {}).get("parts", [])
            text = "".join(p.get("text", "") for p in parts)

            if not text:
                finish = cand.get("finishReason", "")
                raise RuntimeError(f"No text (finish: {finish})")

            result = extract_json(text)

            for k in ["headline_line1", "headline_line2_ar", "source", "caption", "image_url"]:
                if k not in result:
                    raise ValueError(f"Missing field: {k}")

            return result
        except Exception as e:
            last_err = e
            if attempt < max_retries - 1:
                time.sleep(10)

    # Always raise a real exception, never None
    if last_err is None:
        raise RuntimeError("Gemini failed after all retries (unknown error)")
    raise last_err


def download_image(url, save_path):
    """Download an image from URL with content-type validation."""
    headers = {"User-Agent": "Mozilla/5.0 (4EverBot/1.0)"}
    r = requests.get(url, headers=headers, timeout=30, stream=True, allow_redirects=True)
    r.raise_for_status()

    ct = r.headers.get("content-type", "")
    if not ct.startswith("image/"):
        raise ValueError(f"Not an image (got {ct})")

    os.makedirs(os.path.dirname(save_path) or ".", exist_ok=True)
    with open(save_path, "wb") as f:
        for chunk in r.iter_content(8192):
            f.write(chunk)
    return save_path


def scout_multiple(count):
    """Generate N distinct news items, with delays to respect rate limits."""
    results = []
    avoid = []
    for i in range(count):
        if i > 0:
            time.sleep(8)
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
