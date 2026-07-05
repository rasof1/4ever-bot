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

# 🔑 API key pool - rotates on rate-limit failures
# Primary from env, backups hardcoded as fallback
# 🔑 مفتاح واحد فقط - بدون تدوير/احتياطي (بحسب الطلب)
GEMINI_API_KEYS = [GEMINI_API_KEY]
_DEAD_KEYS = set()   # permanently bad keys (403 leaked/blocked) - skipped forever
_KEY_INDEX = [0]     # mutable so functions can rotate

def get_current_key():
    """Get the current active Gemini API key (skipping dead ones)."""
    # If all keys are dead, fall back to first one anyway (last resort)
    if len(_DEAD_KEYS) >= len(GEMINI_API_KEYS):
        return GEMINI_API_KEYS[0]
    # Find next non-dead key starting from current index
    n = len(GEMINI_API_KEYS)
    for offset in range(n):
        idx = (_KEY_INDEX[0] + offset) % n
        if GEMINI_API_KEYS[idx] not in _DEAD_KEYS:
            _KEY_INDEX[0] = idx
            return GEMINI_API_KEYS[idx]
    return GEMINI_API_KEYS[0]

def rotate_key():
    """Move to the next key in the pool (skips dead keys)."""
    import logging
    log = logging.getLogger("4ever_bot")
    n = len(GEMINI_API_KEYS)
    if n <= 1:
        return
    old_idx = _KEY_INDEX[0]
    # Try each subsequent key, skipping dead ones
    for offset in range(1, n + 1):
        idx = (old_idx + offset) % n
        if GEMINI_API_KEYS[idx] not in _DEAD_KEYS:
            _KEY_INDEX[0] = idx
            log.info(f"🔑 Rotated key {old_idx} → {idx} (live keys: {n - len(_DEAD_KEYS)}/{n})")
            return
    log.warning(f"⚠️ All {n} keys are marked dead, staying on key {old_idx}")

def mark_key_dead(reason="unknown"):
    """Mark the CURRENT key as permanently bad (leaked/blocked).
    Will be skipped on all future requests in this session.
    """
    import logging
    log = logging.getLogger("4ever_bot")
    key = GEMINI_API_KEYS[_KEY_INDEX[0]]
    if key in _DEAD_KEYS:
        return
    _DEAD_KEYS.add(key)
    live_count = len(GEMINI_API_KEYS) - len(_DEAD_KEYS)
    log.warning(f"💀 Key {_KEY_INDEX[0]} (...{key[-6:]}) marked DEAD ({reason}). Live keys: {live_count}/{len(GEMINI_API_KEYS)}")
    # Rotate to a live key
    rotate_key()


def _gemini_request(model, body, timeout=45):
    """Make a Gemini API request with automatic key rotation on rate limits.
    Returns the response object, or None if all keys exhausted for this model.
    """
    import requests
    attempts = len(GEMINI_API_KEYS)
    for _ in range(attempts):
        key = get_current_key()
        url = f"{GEMINI_API_BASE}/{model}:generateContent?key={key}"
        try:
            r = requests.post(url, json=body, timeout=timeout)
        except Exception:
            return None
        if r.status_code == 429:
            log.warning(f"   🔄 Key {_KEY_INDEX[0]} hit 429, rotating to next key...")
            rotate_key()
            continue
        if r.status_code == 403:
            err_msg = ""
            try: err_msg = r.json().get("error", {}).get("message", "").lower()
            except Exception: pass
            if "leaked" in err_msg or "api key" in err_msg or "blocked" in err_msg:
                mark_key_dead(reason="leaked/blocked")
            else:
                rotate_key()
            continue
        # Success or non-rate-limit error → return as-is
        return r
    return None


# Fallback chain - each model has SEPARATE daily quota in Gemini Free Tier
FALLBACK_MODELS = [
    # Tier 1: Newest 3.x family (best, separate quotas each)
    "gemini-3.5-flash",                # newest flash 3.5
    "gemini-3-flash-preview",          # 3.x flash preview
    "gemini-3.1-pro-preview",          # 3.1 pro
    "gemini-3-pro-preview",            # 3 pro
    "gemini-3.1-flash-lite",           # 3.1 flash lite (high quota)
    # Tier 2: 2.5 family
    "gemini-2.5-flash",                # Flash 2.5 - very reliable
    "gemini-2.5-pro",                  # Pro 2.5
    "gemini-2.5-flash-lite",           # Lite 2.5
    # Tier 3: 2.0 family
    "gemini-2.0-flash",
    "gemini-2.0-flash-001",
    "gemini-2.0-flash-lite",
    "gemini-2.0-flash-lite-001",
    # Tier 4: Always-available "latest" aliases (good fallback)
    "gemini-flash-latest",
    "gemini-flash-lite-latest",
    "gemini-pro-latest",
]
_env_model = os.getenv("GEMINI_MODEL")
if _env_model:
    FALLBACK_MODELS = [_env_model] + [m for m in FALLBACK_MODELS if m != _env_model]

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

# Language config
DIALECTS = {
    "fusha": {
        "label": "العربية الفصحى",
        "instruction": "اكتب بالعربية الفصحى المعاصرة، احترافية وواضحة وخالية من أي لهجة.",
    },
    "egyptian": {
        "label": "🇪🇬 المصرية",
        "instruction": "اكتب باللهجة المصرية العامية الحديثة (لهجة القاهرة) - استخدم كلمات مثل: ازاي، بس، علشان، يلا، كده، هتلاقي، إيه، ده، فين، لسه، خالص. مثال: 'تخيل كده يا معلم، ده هيغير اللعبة خالص!' كن حماسياً ومحبوباً.",
    },
    "levantine": {
        "label": "🇸🇾 الشامية",
        "instruction": "اكتب باللهجة الشامية (سورية/لبنانية/فلسطينية/أردنية) - استخدم: شو، هيك، كتير، منيح، يعني، هلق، ليش، بدك، شغلة، عنجد، طلع. مثال: 'شو يعني هيدي الميزة؟ كتير منيحة عنجد!' كن دافئاً ومتحمساً.",
    },
    "saudi": {
        "label": "🇸🇦 السعودية",
        "instruction": "اكتب باللهجة السعودية النجدية - استخدم: ايش، كذا، ذي، تقدر، عقب، يبيله، حلو، يعطيك العافية، بزر، طوفنا. مثال: 'ايش رايكم في ذي الميزة؟ صراحة قمة!' كن جريئاً ومباشراً.",
    },
    "algerian": {
        "label": "🇩🇿 الجزائرية",
        "instruction": "اكتب باللهجة الجزائرية - استخدم: واش، بصح، كيما، نتاع، راني، بزاف، كيف، تاع، ماشي، يخي. ملاحظة: استخدم حروف عربية فقط. مثال: 'واش رايكم؟ هاد الحاجة راها بزاف مليحة!' كن صريحاً وحماسياً.",
    },
    "emirati": {
        "label": "🇦🇪 الإماراتية",
        "instruction": "اكتب باللهجة الإماراتية الخليجية - استخدم: شو، وايد، عاد، يهال، شوي، الحين، يبا، خلاص، طاير، فديت. مثال: 'شو رايكم يهال؟ هالميزة وايد عودة!' كن أنيقاً وعصرياً.",
    },
    "moroccan": {
        "label": "🇲🇦 المغربية",
        "instruction": "اكتب باللهجة المغربية - استخدم: واخا، بزاف، دابا، شنو، فين، علاش، كيداير، شوي، زوين، نتا. مثال: 'شنو رايكم؟ هاد الحاجة زوينة بزاف!' كن ودوداً ومحبوباً.",
    },
}


LANG_INSTRUCTIONS = {
    "ar": {
        "name_ar": "العربية",
        "instruction": "اكتب جميع النصوص باللغة العربية الفصحى المعاصرة. استخدم لهجة احترافية وودودة.",
        "headline_lang": "Arabic (Modern Standard Arabic)",
        "caption_lang": "Arabic",
        "hashtag_examples": "#تقنية #ذكاء_اصطناعي #4Ever",
        "is_rtl": True,
    },
    "en": {
        "name_ar": "الإنجليزية",
        "instruction": "Write ALL text fields in English. Use professional, engaging, viral-tech-blog style. The headline_line1 should be a catchy English headline, headline_line2_ar should be the English subtitle (use headline_line2_ar field anyway for compatibility).",
        "headline_lang": "English",
        "caption_lang": "English",
        "hashtag_examples": "#Tech #AI #4Ever #Innovation",
        "is_rtl": False,
    },
    "fr": {
        "name_ar": "الفرنسية",
        "instruction": "Écris TOUS les textes en français. Utilise un style professionnel, engageant, type blog tech viral. Le headline_line1 doit être un titre accrocheur en français, headline_line2_ar doit être le sous-titre en français (utilise quand même le champ headline_line2_ar pour compatibilité).",
        "headline_lang": "French",
        "caption_lang": "French",
        "hashtag_examples": "#Tech #IA #4Ever #Innovation",
        "is_rtl": False,
    },
}


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


PROMPT_TEMPLATE = r"""أنت كاتب محتوى تقني عربي محترف لصفحة "4Ever".

ابحث في الإنترنت عن آخر خبر حديث ومثير من آخر 7 أيام في أحد هذه المجالات:
- الذكاء الاصطناعي (OpenAI, Anthropic, Google, Meta, Microsoft, xAI)
- الهواتف الذكية (Apple, Samsung, Xiaomi, Google Pixel, OnePlus, Huawei)
- الألعاب (PlayStation, Xbox, Nintendo, Steam)
- الشرائح (NVIDIA, AMD, Intel, Qualcomm)
- التسريبات والتقنيات الناشئة

تنويع: غطّ مجالاً مختلفاً في كل مرة.

⚠️ قواعد العنوان:
- headline_line1: عنوان عربي فصحى - أقصى 35 حرف
- headline_line2_ar: سطر ثانوي عربي فصحى - أقصى 18 حرف
- headline_line2_en: اسم منتج إنجليزي قصير أو فارغ

🎯 image_query (مهم جداً):
- يحدد المنتج/التطبيق بدقة، ليس الموضوع العام
- جيد: "Apple iPhone 17 Pro Max product render"
- جيد: "Samsung Galaxy S26 official announcement photo"
- سيء: "AI" أو "smartphone" (عام جداً)

🔥 الكابشن - قواعد إلزامية صارمة:

1️⃣ ابدأ بهوك قوي مع إيموجي حماسي:
   استخدم واحداً من: 🚨 ⚡ 💥 🔥 🤯
   مثال على الهيكل (ليس النص الحرفي): إيموجي + كلمة جذابة + الحدث + إيموجي

2️⃣ شرح حماسي بفقرة (2-3 أسطر) مع إيموجي داخل النص (🤯 😱 😮)

3️⃣ سيناريو محسوس يبدأ بـ "تخيل..." أو "تصور..." مع إيموجي مناسب

4️⃣ قائمة ميزات أو نقاط - كل سطر يبدأ بإيموجي مختلف:
   🎬 ... 🎨 ... ⚡ ... 🧠 ... 🚀 ...

5️⃣ لحظة الإبهار: ابدأ بسؤال مثل "والأكثر إثارة؟" أو "أكثر شيء لافت؟"

6️⃣ قائمة بنقاط ذهبية - كل سطر يبدأ بـ ✨

7️⃣ خاتمة فلسفية قصيرة مع 🚀

8️⃣ سؤال تفاعلي يبدأ بـ 🤔 وينتهي بـ 👇

9️⃣ CTA الإلزامي: "💡 لمزيد من التحديثات الحصرية، اشترك في 4Ever الآن!"

⚠️ قواعد صارمة جداً للكابشن:
- 12-20 إيموجي على الأقل في الكابشن كله
- فقرات منفصلة بسطر فارغ بينها
- لغة عاطفية حماسية (ليست تقريراً صحفياً)
- إذا الهوية كانت "الفصحى"، استخدم العربية الفصحى المعاصرة فقط - بدون أي لهجة
- إذا كانت لهجة معينة (مصرية/شامية/سعودية/إلخ)، استخدم اللهجة المحددة بنقاء - لا تمزج بين لهجات
- لا تنسخ أمثلة من تعليمات أخرى - اكتب محتوى أصلياً يناسب الخبر الفعلي
- العنوان دائماً بالفصحى، الكابشن باللهجة المطلوبة

🏷️ hashtags: 5-8 وسوم
- 3 عامة (#تقنية #ذكاء_اصطناعي #4Ever)
- 3-5 محددة بالخبر (#اسم_الشركة #اسم_المنتج)

أنتج JSON صالح فقط بدون code fences وبدون أي نص قبله أو بعده:

{{
  "headline_line1": "...",
  "headline_line2_ar": "...",
  "headline_line2_en": "...",
  "source": "google|openai|anthropic|github|meta|microsoft|apple|nvidia|xai|samsung|sony|nintendo|xiaomi|amd|intel|playstation|xbox|qualcomm|mcit|egypt|huawei|tesla|spacex",
  "category": "ai|phone|gaming|hardware|leak|emerging",
  "product_badge": "...",
  "live_badge": "...",
  "caption": "كابشن كامل بكل القواعد أعلاه",
  "hashtags": ["#وسم1", "#وسم2", "#وسم3", "#وسم4", "#وسم5"],
  "image_prompt": "وصف بصري إنجليزي تفصيلي للصورة المثالية",
  "image_query": "بحث محدد - اسم الشركة + اسم المنتج + نوع الصورة (photo/render/screenshot/announcement)",
  "source_url": "رابط المقال الأصلي"
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



# 🚫 Domains we NEVER accept images from (NSFW, junk, off-topic)
BLOCKED_DOMAINS = {
    # NSFW
    "xcafe.com", "xcafe.tv", "xhamster.com", "pornhub.com", "redtube.com",
    "xnxx.com", "xvideos.com", "youporn.com", "rule34.xxx", "rule34.us",
    "e-hentai.org", "nhentai.net", "danbooru.donmai.us", "gelbooru.com",
    "spankbang.com", "tnaflix.com", "tube8.com", "porn.com", "sex.com",
    # Adult image hosts
    "imhentai.xxx", "imhentai.net", "hentaiera.com", "myhentaicomics.com",
    # Generic junk
    "clipart-library.com", "depositphotos.com", "shutterstock.com",
    "istockphoto.com", "alamy.com", "dreamstime.com", "123rf.com",
    "vectorstock.com", "freepik.com", "stockphoto.com", "gettyimages.com",
    # Memes/generic clipart
    "flyclipart.com", "pngwing.com", "pngegg.com", "cleanpng.com",
    "freepngs.com", "nicepng.com", "seeklogo.com",
    # Social media (low quality / unrelated)
    "pinimg.com",  # Pinterest - too many off-topic results
}

def _is_blocked_url(url):
    """Check if URL is from a blocked domain (NSFW or junk)."""
    if not url:
        return True
    url_lower = url.lower()
    # Check NSFW patterns in URL itself
    nsfw_patterns = [
        "porn", "xxx", "sex", "nude", "naked", "hentai", "ecchi",
        "nsfw", "adult", "fuck", "cock", "pussy", "milf", "anal",
    ]
    for p in nsfw_patterns:
        if p in url_lower:
            return True
    # Check blocked domains
    for domain in BLOCKED_DOMAINS:
        if domain in url_lower:
            return True
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
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=6)
        m = re.search(r'vqd=([\d-]+)', r.text)
        if not m:
            return []
        vqd = m.group(1)
        r2 = requests.get(
            f"https://duckduckgo.com/i.js?l=us-en&o=json&q={quote(query)}&vqd={vqd}&f=,,,,,&p=1",
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://duckduckgo.com/"},
            timeout=6)
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
            if _is_blocked_url(url):
                log.info(f"   🚫 BLOCKED domain/NSFW: {url[:80]}")
                continue
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
    """Try each (model, key) combination until one succeeds.
    For each model: try ALL live keys before moving to next model.
    This way if Key 0 is rate-limited but Key 1 works, we use Key 1 immediately.
    """
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
        live_keys = [k for k in GEMINI_API_KEYS if k not in _DEAD_KEYS]
        if not live_keys:
            log.error("💀 ALL keys are dead, cannot continue")
            break

        for key_attempt, key in enumerate(live_keys):
            _KEY_INDEX[0] = GEMINI_API_KEYS.index(key)
            url = f"{GEMINI_API_BASE}/{model}:generateContent?key={key}"
            try:
                log.info(f"🤖 Model {idx+1}/{len(FALLBACK_MODELS)}: {model} (key {_KEY_INDEX[0]}, attempt {key_attempt+1}/{len(live_keys)})")
                r = requests.post(url, json=body, timeout=90)

                if r.status_code == 429:
                    log.warning(f"   ⚠️  Key {_KEY_INDEX[0]} rate-limited on {model}, trying next key...")
                    last_err = RuntimeError(f"{model} key {_KEY_INDEX[0]} rate limited")
                    continue  # try next KEY

                if r.status_code == 403:
                    err_msg = ""
                    raw_body = ""
                    try:
                        raw_body = r.text[:300]
                        err_msg = r.json().get("error", {}).get("message", "").lower()
                    except Exception:
                        pass
                    if r.status_code == 403:
                    err_msg = ""
                    try:
                        err_msg = r.json().get("error", {}).get("message", "").lower()
                    except Exception:
                        pass
                    if "leaked" in err_msg or "api key" in err_msg or "blocked" in err_msg:
                        log.warning(f"   💀 Key {_KEY_INDEX[0]} LEAKED/BLOCKED - marking dead")
                        mark_key_dead(reason="leaked/blocked")
                        continue  # try next key (this one is now dead)
                    log.warning(f"   ⚠️  {model} 403 (not key-related) on key {_KEY_INDEX[0]}")
                    last_err = RuntimeError(f"{model}: {err_msg[:80] or '403'}")
                    continue  # try next key

                if r.status_code == 404:
                    log.warning(f"   ⚠️  {model} not found, skipping to next model")
                    last_err = RuntimeError(f"{model} not found")
                    break  # skip to next MODEL

                if r.status_code != 200:
                    last_err = RuntimeError(f"{model} HTTP {r.status_code}")
                    continue  # try next key

                # Success path
                data = r.json()
                if "candidates" not in data or not data["candidates"]:
                    last_err = RuntimeError(f"Empty candidates from {model}")
                    continue
                cand = data["candidates"][0]
                content_obj = cand.get("content", {})
                if not isinstance(content_obj, dict):
                    last_err = RuntimeError(f"Bad content type from {model}")
                    continue
                parts = content_obj.get("parts", [])
                if not isinstance(parts, list):
                    last_err = RuntimeError(f"Parts not a list from {model}")
                    continue
                text_pieces = []
                for p in parts:
                    if isinstance(p, dict):
                        text_pieces.append(p.get("text", ""))
                    elif isinstance(p, str):
                        text_pieces.append(p)
                text = "".join(text_pieces)
                if not text:
                    last_err = RuntimeError(f"No text from {model}")
                    continue
                log.info(f"   ✅ {model} OK via key {_KEY_INDEX[0]} ({len(text)} chars)")
                return extract_json(text)
            except RuntimeError:
                raise
            except Exception as e:
                last_err = e
                log.warning(f"   {model} key {_KEY_INDEX[0]} exception: {str(e)[:100]}")
                continue

    raise RuntimeError(f"All {len(FALLBACK_MODELS)} models × {len(GEMINI_API_KEYS)} keys exhausted. Last: {last_err}")


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


def scout_news(extra_instructions="", lang="ar", dialect=None):
    """Scout latest tech news.
    lang: 'ar' | 'en' | 'fr'
    dialect (for ar only): 'fusha' | 'egyptian' | 'levantine' | 'saudi' | 'algerian' | 'emirati' | 'moroccan' | None
    """
    lang_cfg = LANG_INSTRUCTIONS.get(lang, LANG_INSTRUCTIONS["ar"])
    lang_block = f"\n\n🌐 لغة المنشور: {lang_cfg['caption_lang']}\n{lang_cfg['instruction']}\nأمثلة هاشتاقات بهذه اللغة: {lang_cfg['hashtag_examples']}\n"

    # Add dialect instruction for Arabic
    if lang == "ar" and dialect and dialect in DIALECTS:
        dialect_cfg = DIALECTS[dialect]
        lang_block += f"\n🗣️ اللهجة المطلوبة: {dialect_cfg['label']}\n{dialect_cfg['instruction']}\nملاحظة مهمة: العنوان (headline_line1, headline_line2_ar) يبقى بالفصحى، لكن الكابشن (caption) كاملاً باللهجة المطلوبة.\n"

    full_extras = lang_block + (extra_instructions or "")
    prompt = PROMPT_TEMPLATE.format(extra_instructions=full_extras)
    result = _call_gemini(prompt)
    result["_lang"] = lang
    result["_dialect"] = dialect
    return _finalize_result(result)


def download_image(news_data, save_path):
    if find_or_generate_image(news_data, save_path):
        return save_path
    raise ValueError("All image strategies failed")


def scout_multiple(count, lang="ar"):
    results = []
    avoid = []
    for i in range(count):
        if i > 0:
            time.sleep(6)
        extra = ""
        if avoid:
            extra = f"تجنّب: {', '.join(avoid)}. خبر مختلف ومن مجال آخر."
        try:
            data = scout_news(extra_instructions=extra, lang=lang)
            results.append(data)
            topic = data.get("headline_line2_en") or data["headline_line1"][:30]
            avoid.append(topic)
        except Exception as e:
            results.append({"error": str(e)})
    return results


# ═══════════════════════════════════════════════════════════════
# REVERSE MODE: Receive URL/text/screenshot → generate post
# ═══════════════════════════════════════════════════════════════

REVERSE_PROMPT = r"""أنت محرر تقني عربي محترف لصفحة "4Ever".

استلمت المحتوى التالي من المستخدم وعليك تحويله إلى منشور 4Ever احترافي مليء بالحياة.

=== المحتوى ===
{user_content}
================

مهمتك:
1. استخرج الفكرة الرئيسية
2. تحقّق من صحة المعلومات (ابحث في الإنترنت لو احتجت)
3. أعد صياغته بأسلوب 4Ever الجذاب والحماسي
4. image_query يصف المنتج الفعلي بدقة (شركة + منتج + نوع صورة)
5. أضف 5-8 هاشتاقات

🔥 الكابشن - قواعد إلزامية صارمة:

1️⃣ هوك قوي بإيموجي: 🚨 ⚡ 💥 🔥 🤯 + عبارة جذابة
2️⃣ شرح حماسي (2-3 أسطر) مع إيموجي داخل النص
3️⃣ سيناريو "تخيل..." أو "تصور..." 😮
4️⃣ قائمة ميزات - كل سطر إيموجي مختلف: 🎬 🎨 ⚡ 🧠 🚀
5️⃣ لحظة الإبهار: "والأكثر إثارة؟"
6️⃣ قائمة ✨ نقاط ذهبية
7️⃣ خاتمة فلسفية 🚀
8️⃣ سؤال تفاعلي 🤔 ... 👇
9️⃣ CTA: "💡 لمزيد من التحديثات الحصرية، اشترك في 4Ever الآن!"

⚠️ قواعد صارمة:
- 12-20 إيموجي في الكابشن
- فقرات منفصلة
- لغة حماسية (ليس تقرير صحفي)
- استخدم اللهجة/اللغة المحددة بنقاء (لا تمزج بين لهجات)
- لا تنسخ أمثلة - اكتب محتوى أصلياً
- العنوان دائماً بالفصحى

أنتج JSON صالح فقط بدون code fences:

{{
  "headline_line1": "...",
  "headline_line2_ar": "...",
  "headline_line2_en": "...",
  "source": "google|openai|anthropic|github|meta|microsoft|apple|nvidia|xai|samsung|sony|nintendo|xiaomi|amd|intel|playstation|xbox|qualcomm|mcit|egypt|huawei|tesla|spacex",
  "category": "ai|phone|gaming|hardware|leak|emerging",
  "product_badge": "...",
  "live_badge": "...",
  "caption": "كابشن كامل",
  "hashtags": ["#وسم1", "#وسم2"],
  "image_prompt": "وصف بصري إنجليزي",
  "image_query": "بحث محدد - شركة + منتج + نوع",
  "source_url": ""
}}
"""


SMART_REQUEST_PROMPT = r"""أنت كاتب محتوى تقني محترف لصفحة عربية "4Ever".

استلمت طلباً طبيعياً من المستخدم وأريدك أن تفهمه وتنتج منشوراً كاملاً.

=== الطلب ===
{user_request}
==============

مهمتك:
1. **افهم بدقة ما يريده المستخدم**:
   - شرح ميزة؟ مقارنة؟ توتوريال؟ خبر؟ برومبت؟ مراجعة منتج؟
   - ابحث في الإنترنت إذا احتجت معلومات حديثة
2. **أنتج المحتوى المناسب**:
   - لو طلب "أقوى برومبت لـ X" → اكتب البرومبت بالإنجليزية في الكابشن + شرح بالعربية
   - لو طلب "طريقة عمل X" → خطوات مفصّلة بحماس
   - لو طلب "أفضل X" → قائمة مع شرح
   - لو طلب "مقارنة X و Y" → جدول/قائمة فروقات
   - لو طلب "ما رأيك في X" → تحليل صريح
3. **العنوان (headline) يلخّص الموضوع** بشكل جذاب
4. **image_query** يصف المحتوى الفعلي بدقة لإيجاد صورة مطابقة

🔥 الكابشن - مليء بالحياة (نمط viral):

📌 هوك قوي مع إيموجي:
   🚨 / ⚡ / 💥 / 🤯 / 🔥

📌 شرح حماسي (2-3 أسطر مع إيموجي)

📌 لو الموضوع برومبت أو كود:
   ```
   البرومبت/الكود هنا
   ```

📌 لو الموضوع خطوات:
   1️⃣ الخطوة الأولى
   2️⃣ الخطوة الثانية
   3️⃣ ...

📌 لو الموضوع قائمة ميزات:
   🎬 ...
   🎨 ...
   ⚡ ...
   🧠 ...

📌 لحظة إبهار: "واللي صراحة خلاني أنبهر..." 🔥

📌 ✨ النقاط الذهبية:
   ✨ نقطة 1
   ✨ نقطة 2

📌 خاتمة فلسفية 🚀

📌 سؤال تفاعلي: "🤔 شو رأيكم؟ ... شاركونا 👇"

📌 CTA: "💡 لمزيد من التحديثات الحصرية، اشترك في 4Ever الآن!"

⚠️ 10-20 إيموجي على الأقل، فقرات منفصلة، لغة حماسية.

أنتج JSON فقط:

{{
  "headline_line1": "عنوان عربي قصير - أقصى 35 حرف",
  "headline_line2_ar": "السطر 2 - أقصى 18 حرف",
  "headline_line2_en": "اسم إنجليزي قصير أو فارغ",
  "source": "google|openai|anthropic|github|meta|microsoft|apple|nvidia|xai|samsung|sony|nintendo|xiaomi|amd|intel|playstation|xbox|qualcomm|mcit|egypt|huawei|tesla|spacex",
  "category": "ai|phone|gaming|hardware|leak|emerging",
  "product_badge": "تسمية قصيرة",
  "live_badge": "وصف قصير",
  "caption": "كابشن كامل بالنمط أعلاه",
  "hashtags": ["#وسم1", "#وسم2", "#وسم3", "#وسم4", "#وسم5"],
  "image_prompt": "وصف بصري إنجليزي تفصيلي",
  "image_query": "بحث محدد للصورة - شركة + منتج + نوع",
  "source_url": ""
}}
"""


def reverse_scout(user_content, lang="ar", dialect=None):
    """Convert user-provided content to 4Ever post in specified language/dialect."""
    lang_cfg = LANG_INSTRUCTIONS.get(lang, LANG_INSTRUCTIONS["ar"])
    lang_block = f"\n\n🌐 اللغة المطلوبة للمنشور: {lang_cfg['caption_lang']}\n{lang_cfg['instruction']}\nأمثلة وسوم بهذه اللغة: {lang_cfg['hashtag_examples']}\n"

    if lang == "ar" and dialect and dialect in DIALECTS:
        dialect_cfg = DIALECTS[dialect]
        lang_block += f"\n🗣️ اللهجة المطلوبة: {dialect_cfg['label']}\n{dialect_cfg['instruction']}\nملاحظة: العنوان بالفصحى، الكابشن باللهجة.\n"

    full_content = user_content[:8000] + lang_block
    prompt = REVERSE_PROMPT.format(user_content=full_content)
    result = _call_gemini(prompt, body_overrides={
        "generationConfig": {"temperature": 0.7}
    })
    result["_lang"] = lang
    result["_dialect"] = dialect
    return _finalize_result(result)


# ═══════════════════════════════════════════════════════════════
# URL FETCHING (Facebook, Twitter, regular sites)
# ═══════════════════════════════════════════════════════════════

def _try_fetch(url, headers, timeout=10):
    """Single fetch attempt with given headers."""
    try:
        r = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        if r.status_code == 200 and r.text:
            return r.text
    except Exception:
        pass
    return None


def _extract_og_image(html):
    """Extract og:image URL from HTML meta tags."""
    if not html:
        return None
    for pattern in [
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image',
        r'<link[^>]+rel=["\']image_src["\'][^>]+href=["\']([^"\']+)',
    ]:
        m = re.search(pattern, html, re.IGNORECASE)
        if m:
            url = m.group(1).strip()
            if url.startswith('http'):
                return url
    return None


def _strip_html(html):
    """Extract clean text from HTML, preserving important content."""
    if not html:
        return ""
    text = re.sub(r'<script[^>]*>.*?</script>', ' ', html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<style[^>]*>.*?</style>', ' ', text, flags=re.DOTALL | re.IGNORECASE)
    extras = []
    for pattern in [
        r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)',
        r'<title>([^<]+)</title>',
    ]:
        for m in re.findall(pattern, html, re.IGNORECASE):
            extras.append(m[:500])
    text = re.sub(r'<[^>]+>', ' ', text)
    import html as html_module
    text = html_module.unescape(text)
    text = re.sub(r'\s+', ' ', text).strip()
    if extras:
        text = " | ".join(extras) + " | " + text
    return text[:6000]


def _is_useful_content(text):
    """Check if extracted content is actually useful (not just login/error page)."""
    if not text or len(text) < 200:
        return False
    text_lower = text.lower()
    junk_signals = [
        "log in or sign up", "log in to facebook", "sign up for facebook",
        "javascript is required", "enable javascript", "browser not supported",
        "unknown error", "an unknown error occurred", "return home",
        "page not found", "404 not found", "access denied", "forbidden",
        "warning: target url returned error", "this page maybe requiring",
        "don't miss what", "people on x are the first",
        "view photos and videos", "see posts, photos and more",
        "this page maybe not yet fully loaded",
        "checking your browser", "please enable cookies",
        "we couldn't find this page", "content unavailable",
        "returned error 404", "returned error 403", "returned error 401",
        "returned error 500", "returned error 503",
        "skip navigation", "skip to main content",
        "this content isn't available", "post not available",
    ]
    junk_count = sum(1 for s in junk_signals if s in text_lower)
    if junk_count >= 1 and len(text) < 800:
        return False  # short + any junk signal = junk
    if junk_count >= 2:
        return False
    # If text is short and contains login keyword
    if len(text) < 600 and any(s in text_lower for s in ["log in", "sign up", "login required", "log in to "]):
        return False
    # Pure x.com title page check
    if text_lower.startswith("title: x\n") or text_lower.startswith("title: x ") and len(text) < 500:
        return False
    return True


def _fetch_url_via_gemini(url):
    """🎯 Use Gemini with web grounding to read URL content.
    This bypasses scraping limitations - Gemini can access URLs via Google.
    Returns: (text, image_url) tuple, or (None, None) on failure.
    """
    log.info(f"   🤖 Asking Gemini to read URL via web grounding...")
    prompt = f"""You have a URL to investigate: {url}

Use web search to find information about this URL/post:
- If the URL is from Facebook/Twitter/X/Instagram/TikTok: search for the post content, author, topic
- If the URL is from a blog/news site: search for the article topic and key information
- Always try to find SOMETHING informative even if direct access fails - search for the topic mentioned in the URL slug

Return JSON only (no code fences):
{{
  "title": "post title or topic in 1-2 sentences",
  "main_content": "main text content of the post/article (in original language, up to 1500 chars)",
  "author_or_source": "who posted it (person name, organization, company)",
  "topic_summary": "brief Arabic summary of what the post is about (1-2 sentences)",
  "image_description": "if the post has a main image, describe what it shows",
  "image_url": "URL of the main image if you can find it, otherwise empty string",
  "language": "ar|en|fr|other",
  "platform": "facebook|twitter|instagram|youtube|website|other",
  "is_accessible": true,
  "key_facts": ["fact 1", "fact 2", "fact 3"]
}}

If you cannot access the URL, set is_accessible to false and explain in topic_summary."""

    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "tools": [{"google_search": {}}],
        "generationConfig": {
            "temperature": 0.3,
            "thinkingConfig": {"thinkingBudget": 0},
        }
    }

    for model in FALLBACK_MODELS:
        try:
            api_url = f"{GEMINI_API_BASE}/{model}:generateContent?key={get_current_key()}"
            r = requests.post(api_url, json=body, timeout=45)
            if r.status_code == 429:
                rotate_key()
                continue
            if r.status_code == 403:
                err_msg = ""
                try: err_msg = r.json().get("error", {}).get("message", "").lower()
                except Exception: pass
                if "leaked" in err_msg or "api key" in err_msg or "blocked" in err_msg:
                    mark_key_dead(reason="leaked/blocked")
                else:
                    rotate_key()
                continue
            if r.status_code != 200:
                continue
            data = r.json()
            candidates = data.get("candidates", [])
            if not candidates:
                continue
            cand = candidates[0]
            content_obj = cand.get("content", {})
            if not isinstance(content_obj, dict):
                continue
            parts = content_obj.get("parts", [])
            text_pieces = []
            for p in parts:
                if isinstance(p, dict):
                    text_pieces.append(p.get("text", ""))
                elif isinstance(p, str):
                    text_pieces.append(p)
            raw = "".join(text_pieces).strip()
            if not raw:
                continue
            # Strip code fences
            raw = re.sub(r'^```(?:json)?\s*', '', raw)
            raw = re.sub(r'\s*```$', '', raw)
            try:
                parsed = json.loads(raw)
            except Exception:
                # Try extracting JSON from text
                m = re.search(r'\{[^{}]*"main_content"[^{}]*\}', raw, re.DOTALL)
                if not m:
                    continue
                try:
                    parsed = json.loads(m.group(0))
                except Exception:
                    continue

            # Build natural text representation
            parts_out = []
            if parsed.get("title"):
                parts_out.append(f"العنوان: {parsed['title']}")
            if parsed.get("topic_summary"):
                parts_out.append(f"الموضوع: {parsed['topic_summary']}")
            if parsed.get("author_or_source"):
                parts_out.append(f"المصدر/الكاتب: {parsed['author_or_source']}")
            if parsed.get("main_content"):
                parts_out.append(f"المحتوى: {parsed['main_content'][:1500]}")
            if parsed.get("key_facts"):
                kf = parsed["key_facts"]
                if isinstance(kf, list) and kf:
                    parts_out.append("النقاط: " + " | ".join(str(x)[:200] for x in kf[:5]))
            if parsed.get("image_description"):
                parts_out.append(f"الصورة تظهر: {parsed['image_description']}")
            if parsed.get("platform"):
                parts_out.append(f"المنصة: {parsed['platform']}")

            text = "\n".join(parts_out)
            image_url = parsed.get("image_url", "").strip() or None
            if image_url and not image_url.startswith("http"):
                image_url = None

            # Even if "not accessible" by direct read, the grounded search may have found info
            if parsed.get("is_accessible") is False:
                if len(text) < 100:
                    log.warning(f"   Gemini said URL not accessible: {parsed.get('topic_summary', '')[:100]}")
                    return None, None
                # Otherwise text contains info from search results - use it
                log.info(f"   ℹ️ URL not directly accessible, but Gemini found info via search")

            log.info(f"   ✅ Gemini grounding via {model}: {len(text)} chars{', image found' if image_url else ''}")
            return text, image_url
        except Exception as e:
            log.warning(f"   Gemini grounding {model} failed: {str(e)[:100]}")
            continue
    return None, None


def fetch_url_content(url, return_image=False):
    """Smart URL content extraction with multiple strategies.

    Strategy order:
    1. Direct HTTP scraping with multiple URLs + user-agents
    2. Jina AI Reader fallback
    3. 🆕 Gemini web grounding (most powerful - reads any URL)

    Returns:
    - If return_image=False: text string
    - If return_image=True: (text, image_url_or_None)
    """
    log.info(f"📥 Fetching: {url[:80]}")
    url_lower = url.lower()
    transformed_urls = [url]
    found_image = None

    # === Platform-specific transforms ===
    if 'facebook.com' in url_lower or 'fb.com' in url_lower or 'fb.watch' in url_lower:
        mbasic = re.sub(r'(www\.|m\.|web\.)?facebook\.com', 'mbasic.facebook.com', url)
        if mbasic != url:
            transformed_urls.insert(0, mbasic)

    elif 'twitter.com' in url_lower or 'x.com' in url_lower:
        # vxtwitter / fxtwitter return rich og:image+og:description metadata
        transformed_urls.insert(0, re.sub(r'(twitter|x)\.com', 'vxtwitter.com', url))
        transformed_urls.insert(1, re.sub(r'(twitter|x)\.com', 'fxtwitter.com', url))
        # Also try nitter
        for nitter in ['nitter.net', 'nitter.privacydev.net']:
            transformed_urls.append(re.sub(r'(twitter|x)\.com', nitter, url))

    elif 'instagram.com' in url_lower:
        m = re.search(r'instagram\.com/(p|reel|tv)/([A-Za-z0-9_-]+)', url)
        if m:
            post_id = m.group(2)
            # ddinstagram returns full og:image
            transformed_urls.insert(0, url.replace('instagram.com', 'ddinstagram.com'))
            transformed_urls.insert(1, f"https://www.picuki.com/media/{post_id}")
            transformed_urls.insert(2, f"https://imginn.com/p/{post_id}/")

    elif 'tiktok.com' in url_lower:
        transformed_urls.insert(0, url.replace('tiktok.com', 'vxtiktok.com'))

    elif 'youtube.com' in url_lower or 'youtu.be' in url_lower:
        from urllib.parse import quote
        oembed = f"https://www.youtube.com/oembed?url={quote(url)}&format=json"
        transformed_urls.insert(0, oembed)

    # === User-Agents ===
    user_agents = [
        {"User-Agent": "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)",
         "Accept": "text/html,application/xhtml+xml"},
        {"User-Agent": "Twitterbot/1.0", "Accept": "text/html"},
        {"User-Agent": "TelegramBot (like TwitterBot)", "Accept": "text/html"},
        {"User-Agent": "WhatsApp/2.0", "Accept": "text/html"},
        {"User-Agent": "Mozilla/5.0 (compatible; Discordbot/2.0; +https://discordapp.com)",
         "Accept": "text/html"},
        {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
         "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9",
         "Accept-Language": "en-US,en;q=0.9,ar;q=0.8"},
    ]

    best_content = ""
    for try_url in transformed_urls[:5]:
        for ua in user_agents[:3]:
            log.info(f"   Try {try_url[:60]} ({ua['User-Agent'][:25]})")
            html = _try_fetch(try_url, ua, timeout=8)
            if html:
                # Try to extract og:image
                if not found_image:
                    img = _extract_og_image(html)
                    if img:
                        found_image = img
                        log.info(f"   📷 Found og:image: {img[:80]}")
                text = _strip_html(html)
                if _is_useful_content(text):
                    log.info(f"   ✅ USEFUL content from {try_url[:50]}: {len(text)} chars")
                    if len(text) > len(best_content):
                        best_content = text
                    if len(text) > 800:
                        if return_image:
                            return best_content, found_image
                        return best_content

    # Jina AI Reader fallback (often hits rate limits)
    if len(best_content) < 500:
        log.info(f"   Trying Jina AI Reader...")
        try:
            r = requests.get(f"https://r.jina.ai/{url}", timeout=15)
            if r.status_code == 200 and _is_useful_content(r.text):
                if len(r.text) > len(best_content):
                    best_content = r.text[:6000]
                    log.info(f"   ✅ Jina: {len(r.text)} chars")
        except Exception as e:
            log.warning(f"   Jina failed: {str(e)[:100]}")

    # 🤖 LAST RESORT: Gemini web grounding (most powerful)
    if not _is_useful_content(best_content):
        log.info(f"   🤖 Scraping insufficient, using Gemini grounding...")
        gemini_text, gemini_image = _fetch_url_via_gemini(url)
        if gemini_text:
            best_content = gemini_text
            if not found_image and gemini_image:
                found_image = gemini_image

    if not best_content:
        log.warning(f"   ❌ All methods failed")
        best_content = f"رابط: {url}"

    log.info(f"   📋 Final: {len(best_content)} chars{', image: yes' if found_image else ', no image'}")

    if return_image:
        return best_content, found_image
    return best_content


def extract_post_from_screenshot(image_path):
    """Use Gemini Vision to extract post content from a screenshot.
    Returns text description suitable for reverse_scout or smart_scout.
    Handles: WhatsApp/Telegram/social media/website screenshots in any language.
    """
    import base64
    log.info(f"📸 OCR via Gemini Vision: {image_path}")

    try:
        with open(image_path, "rb") as f:
            img_bytes = f.read()
        img_b64 = base64.b64encode(img_bytes).decode("utf-8")
    except Exception as e:
        log.error(f"   Cannot read image: {e}")
        return None

    ext = image_path.lower().rsplit(".", 1)[-1]
    mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
            "webp": "image/webp", "gif": "image/gif"}.get(ext, "image/jpeg")

    prompt = """انت محلل صور خبير. هذه لقطة شاشة من سوشيال ميديا أو موقع أو محادثة.

مهمتك:
1. اقرأ كل النصوص العربية والإنجليزية بدقة
2. استخرج المحتوى الرئيسي للمنشور
3. لاحظ أي روابط/شركات/منتجات/أشخاص
4. صف الصور البصرية بإيجاز

أرجع JSON فقط (بدون code fences):
{
  "extracted_text": "كل النص بالعربية والإنجليزية",
  "main_topic": "ملخص في جملة واحدة",
  "key_points": ["نقطة 1", "نقطة 2", "نقطة 3"],
  "platform": "facebook|twitter|instagram|telegram|whatsapp|website|other",
  "mentions": ["شركات أو منتجات"],
  "visual_description": "وصف بصري"
}"""

    body = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": mime, "data": img_b64}}
            ]
        }],
        "generationConfig": {
            "temperature": 0.3,
            "thinkingConfig": {"thinkingBudget": 0},
            "response_mime_type": "application/json",
        }
    }

    for model in FALLBACK_MODELS:
        try:
            url = f"{GEMINI_API_BASE}/{model}:generateContent?key={get_current_key()}"
            r = requests.post(url, json=body, timeout=30)
            if r.status_code == 429:
                rotate_key()
                continue
            if r.status_code == 403:
                err_msg = ""
                try: err_msg = r.json().get("error", {}).get("message", "").lower()
                except Exception: pass
                if "leaked" in err_msg or "api key" in err_msg or "blocked" in err_msg:
                    mark_key_dead(reason="leaked/blocked")
                else:
                    rotate_key()
                continue
            if r.status_code != 200:
                continue
            data = r.json()
            candidates = data.get("candidates", [])
            if not candidates:
                continue
            text = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
            if not text:
                continue
            text = text.strip().strip("```json").strip("```").strip()
            try:
                parsed = json.loads(text)
            except Exception:
                m = re.search(r'\{.*?"extracted_text".*?\}', text, re.DOTALL)
                if not m:
                    continue
                try:
                    parsed = json.loads(m.group(0))
                except Exception:
                    continue

            parts = []
            if parsed.get("main_topic"):
                parts.append(f"الموضوع: {parsed['main_topic']}")
            if parsed.get("extracted_text"):
                parts.append(f"النص الكامل: {parsed['extracted_text'][:2000]}")
            if parsed.get("key_points"):
                parts.append("النقاط الرئيسية: " + " | ".join(parsed['key_points'][:5]))
            if parsed.get("mentions"):
                parts.append("ذُكر: " + ", ".join(parsed['mentions'][:5]))
            if parsed.get("visual_description"):
                parts.append(f"بصرياً: {parsed['visual_description'][:300]}")
            if parsed.get("platform") and parsed["platform"] != "other":
                parts.append(f"المنصة: {parsed['platform']}")

            extracted = "\n".join(parts)
            log.info(f"   ✅ Extracted {len(extracted)} chars via {model}")
            return extracted if extracted.strip() else None
        except Exception as e:
            log.warning(f"   {model} OCR failed: {str(e)[:100]}")
            continue

    log.error("   All Vision models failed for OCR")
    return None


def acquire_validated_image(news_data, save_path, max_attempts=7):
    """
    SEARCH-FIRST image acquisition - prefer REAL photos over AI generation.

    Strategy (7 attempts):
    1: Original specific query → Bing/DDG/Google/Unsplash
    2: Broader query (product + 'photo')
    3: Company + product + 'announcement'
    4: Headline_ar translated keywords
    5: Just the company name + category
    6: AI gen with image_prompt (only if all searches failed)
    7: AI gen with category hints (last resort)
    """
    original_query = news_data.get("image_query", "")
    headline_en = news_data.get("headline_line2_en", "")
    headline_ar = news_data.get("headline_line1", "")
    source = news_data.get("source", "")
    category = news_data.get("category", "ai")

    has_any_image = False

    for attempt in range(max_attempts):
        log.info(f"📸 Image attempt {attempt+1}/{max_attempts}")
        got = False

        if attempt == 0:
            # Original specific query
            got = find_or_generate_image_search_only(news_data, save_path)
        elif attempt == 1:
            # Broader: product + photo
            if headline_en:
                broader = {**news_data, "image_query": f"{headline_en} photo official"}
                got = find_or_generate_image_search_only(broader, save_path)
        elif attempt == 2:
            # Company + product + announcement
            if source and headline_en:
                broader = {**news_data, "image_query": f"{source} {headline_en} announcement"}
                got = find_or_generate_image_search_only(broader, save_path)
        elif attempt == 3:
            # Source + category
            if source:
                cat_keywords = {
                    "phone": "smartphone",
                    "gaming": "game console",
                    "ai": "AI announcement",
                    "hardware": "chip processor",
                    "leak": "leak render",
                    "emerging": "technology",
                }
                broader = {**news_data, "image_query": f"{source} {cat_keywords.get(category, 'technology')}"}
                got = find_or_generate_image_search_only(broader, save_path)
        elif attempt == 4:
            # Just headline_en + 'new'
            if headline_en:
                broader = {**news_data, "image_query": f"new {headline_en}"}
                got = find_or_generate_image_search_only(broader, save_path)
        elif attempt == 5:
            # AI generation - high quality (only after searches exhausted)
            log.info(f"   🤖 Falling back to AI generation")
            ai_prompt = news_data.get("image_prompt") or original_query or f"{source} tech product"
            got = generate_ai_image(ai_prompt, save_path)
        else:
            # Last: AI with category hints
            log.info(f"   🤖 Last resort: AI with category")
            category_hints = {
                "phone": "modern smartphone product render dark background dramatic lighting cinematic",
                "gaming": "gaming console controller futuristic neon lighting",
                "ai": "artificial intelligence robot futuristic abstract glowing",
                "hardware": "computer chip GPU close-up cinematic lighting macro",
                "leak": "tech device leaked render mysterious dark",
                "emerging": "futuristic technology concept neon glowing",
            }
            # Always use a SAFE, specific prompt for AI generation
            base = news_data.get('image_prompt', '') or news_data.get('image_query', '')
            cat_hint = category_hints.get(category, 'professional technology product photo')
            ai_prompt = f"professional clean product photo, {base}, {cat_hint}, studio lighting, photorealistic, 8k, no people, no text"
            got = generate_ai_image(ai_prompt, save_path)

        if not got:
            continue

        # Quick local check: reject empty/monochrome images
        if not _is_image_visually_meaningful(save_path):
            log.warning(f"   🚫 Image visually empty/monochrome - skip")
            continue

        has_any_image = True

        # 🎯 ALWAYS validate with AI Vision - search results lie often!
        # E.g. searching "NVIDIA Vera Rubin" returns BICYCLES named "Rubin" - we MUST reject those
        is_valid, issues, reason = validate_post_with_ai(news_data, save_path)
        if is_valid:
            log.info(f"   ✅ Image VALIDATED on attempt {attempt+1}: {reason[:100]}")
            return True
        log.warning(f"   ❌ AI validator REJECTED: {reason[:150]}")

    # All validation failed - generate ONE last clean AI image as safe fallback
    log.warning("   ⚠️ All validation failed, generating clean fallback AI image")
    safe_prompt = (f"professional clean technology product photo, "
                   f"{news_data.get('image_query', '')[:200]}, "
                   f"studio lighting, photorealistic, no people, no text, no logos, "
                   f"corporate announcement style, 8k quality")
    if generate_ai_image(safe_prompt, save_path):
        log.info("   ✅ Generated safe fallback AI image")
        return True
    log.error("   ❌ Even fallback AI generation failed")
    return False


def search_images_via_google_images(query):
    """Scrape Google Images for the query (last resort)."""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        }
        url = f"https://www.google.com/search?q={requests.utils.quote(query)}&tbm=isch&safe=active"
        r = requests.get(url, headers=headers, timeout=12)
        if r.status_code != 200:
            return []
        html = r.text
        # Extract image URLs - Google uses various patterns
        urls = []
        # Pattern 1: "ou":"..." (older)
        urls.extend(re.findall(r'"ou":"(https?://[^"]+\.(?:jpg|jpeg|png|webp))"', html, re.IGNORECASE))
        # Pattern 2: imgurl
        urls.extend(re.findall(r'imgurl=(https?://[^&]+\.(?:jpg|jpeg|png|webp))', html, re.IGNORECASE))
        # Pattern 3: src in img tags (last resort - thumbnails)
        if not urls:
            urls.extend(re.findall(r'<img[^>]+src="(https?://[^"]+\.(?:jpg|jpeg|png|webp))"', html, re.IGNORECASE))
        # Decode URL-encoded chars
        from urllib.parse import unquote
        urls = [unquote(u) for u in urls]
        # Filter: skip Google's own assets
        urls = [u for u in urls if 'gstatic.com' not in u and 'google.com' not in u]
        return urls[:15]
    except Exception as e:
        log.warning(f"   Google Images failed: {e}")
        return []


def search_images_via_unsplash(query):
    """Free Unsplash source (no key needed for source.unsplash.com)."""
    try:
        # Unsplash source endpoint returns a random matching image
        url = f"https://source.unsplash.com/1280x720/?{requests.utils.quote(query)}"
        # We don't fetch — we trust this URL works as image
        return [url]
    except Exception:
        return []


def find_or_generate_image_search_only(news_data, save_path):
    """Search-only variant - tries Bing, DDG, Google Images, Unsplash."""
    query = news_data.get("image_query", "")
    if not query:
        return False
    log.info(f"🔍 Searching: {query}")

    # Collect URLs from all sources
    all_urls = []
    bing_urls = search_images_via_bing(query)
    log.info(f"   Bing: {len(bing_urls)} results")
    all_urls.extend(bing_urls)

    if len(all_urls) < 5:
        ddg_urls = search_images_via_duckduckgo(query)
        log.info(f"   DuckDuckGo: {len(ddg_urls)} results")
        all_urls.extend(ddg_urls)

    if len(all_urls) < 5:
        google_urls = search_images_via_google_images(query)
        log.info(f"   Google Images: {len(google_urls)} results")
        all_urls.extend(google_urls)

    if not all_urls:
        log.info(f"   Trying Unsplash as last search source")
        all_urls = search_images_via_unsplash(query)

    # Dedupe while preserving order
    seen = set()
    unique_urls = []
    for u in all_urls:
        if u not in seen:
            seen.add(u)
            unique_urls.append(u)

    # Try each URL
    for i, url in enumerate(unique_urls[:12]):  # Try up to 12 candidates
        if _is_blocked_url(url):
            log.info(f"   🚫 BLOCKED domain/NSFW: {url[:80]}")
            continue
        log.info(f"   🔗 Try {i+1}/{min(12, len(unique_urls))}: {url[:80]}")
        if try_download_image(url, save_path):
            # Quick visual check
            if _is_image_visually_meaningful(save_path):
                log.info(f"   ✅ Got valid image from URL {i+1}")
                return True
            else:
                log.info(f"   ⚠️ Image is empty/monochrome, trying next")
    return False



def smart_scout(user_request, lang="ar", dialect=None):
    """Smart scout: understand any natural-language request and produce a post.
    Handles: tutorials, prompts, comparisons, opinions, lists, etc.
    """
    lang_cfg = LANG_INSTRUCTIONS.get(lang, LANG_INSTRUCTIONS["ar"])
    lang_block = f"\n\n🌐 اللغة المطلوبة: {lang_cfg['caption_lang']}\n{lang_cfg['instruction']}\nأمثلة وسوم: {lang_cfg['hashtag_examples']}\n"

    if lang == "ar" and dialect and dialect in DIALECTS:
        dialect_cfg = DIALECTS[dialect]
        lang_block += f"\n🗣️ اللهجة: {dialect_cfg['label']}\n{dialect_cfg['instruction']}\nالعنوان بالفصحى، الكابشن باللهجة.\n"

    full_request = user_request[:5000] + lang_block
    prompt = SMART_REQUEST_PROMPT.format(user_request=full_request)
    result = _call_gemini(prompt, body_overrides={
        "generationConfig": {"temperature": 0.8}
    })
    result["_lang"] = lang
    result["_dialect"] = dialect
    return _finalize_result(result)
