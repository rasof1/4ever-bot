# 🤖 4Ever Telegram Bot — دليل النشر الكامل

> بوت تلغرام ذكي يولّد منشورات تقنية احترافية لصفحة 4Ever بأمر واحد.

---

## 🎯 ما الذي يفعله البوت؟

| الأمر | الوظيفة |
|---|---|
| `/start` | رسالة ترحيب |
| `/help` | عرض كل الأوامر |
| `/status` | فحص صحة البوت |
| `/post` أو `منشور` | توليد منشور واحد |
| `/post 3` أو `منشور 3` | توليد 3 منشورات (حد أقصى 5) |

كل منشور = صورة 1080×1080 PNG + كابشن عربي جاهز + رابط المصدر.

---

## 🔐 الخطوة 0: الأمان أولاً!

### ⚠️ توكن البوت الذي شاركته معي مسرّب — يجب تغييره الآن:

1. افتح **@BotFather** في تلغرام
2. أرسل: `/revoke`
3. اختر بوتك → احصل على **توكن جديد**
4. **لا تشاركه مع أي شخص أو أي شات** — احفظه فقط في ملف `.env`

---

## 🔑 الخطوة 1: احصل على المفاتيح المطلوبة

### أ) Telegram Bot Token

من **@BotFather**:
- إنشاء بوت جديد: `/newbot`
- أو استرجاع توكن موجود: `/mybots` → اختر البوت → **API Token**

### ب) Anthropic API Key

1. اذهب إلى: https://console.anthropic.com
2. سجّل حساب (مجاناً)
3. **API Keys** → **Create Key**
4. **مهم**: ضع كريديت (5$ كافٍ لـ ~500 منشور)

---

## 🚀 الخطوة 2: النشر على Render.com (الموصى به)

### لماذا Render؟
- ✅ مجاني تماماً (Free tier)
- ✅ يعمل 24/7 (لكنه ينام بعد 15 دقيقة عدم نشاط)
- ✅ نشر بضغطة واحدة من GitHub
- ✅ يدعم متغيرات البيئة بأمان

### الطريقة:

#### 1. ارفع المشروع لـ GitHub

```bash
cd 4Ever_Bot
git init
git add .
git commit -m "Initial commit"
# أنشئ repo جديد على github.com ثم:
git remote add origin https://github.com/YOUR_USERNAME/4ever-bot.git
git branch -M main
git push -u origin main
```

#### 2. أنشئ خدمة على Render

1. اذهب إلى: https://render.com → **New** → **Background Worker**
2. اربط حساب GitHub → اختر المستودع
3. اضبط:
   - **Name**: `4ever-telegram-bot`
   - **Runtime**: `Python 3`
   - **Build Command**: `bash build.sh`
   - **Start Command**: `python bot.py`
   - **Plan**: `Free`

#### 3. أضف Environment Variables

في صفحة الخدمة → **Environment**:

| Key | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | التوكن الجديد من BotFather |
| `ANTHROPIC_API_KEY` | المفتاح من console.anthropic.com |

#### 4. اضغط **Create Background Worker** → انتظر 2-3 دقائق

البوت سيشتغل تلقائياً 🎉

---

## 🚀 الخطوة 2 (بديل): النشر على Railway

```bash
# 1. ثبّت Railway CLI
npm i -g @railway/cli

# 2. سجّل دخول
railway login

# 3. أنشئ مشروع
cd 4Ever_Bot
railway init

# 4. أضف المتغيرات
railway variables set TELEGRAM_BOT_TOKEN=your_token
railway variables set ANTHROPIC_API_KEY=sk-ant-...

# 5. ارفع
railway up
```

---

## 💻 الخطوة 2 (بديل): التشغيل المحلي

```bash
cd 4Ever_Bot

# 1. أنشئ بيئة افتراضية
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate

# 2. ثبّت المتطلبات
pip install -r requirements.txt

# 3. تأكّد من دعم Raqm (للعربية)
python -c "from PIL import features; print('Raqm:', features.check('raqm'))"
# يجب أن يطبع: Raqm: True

# 4. أنشئ ملف .env
cp .env.example .env
# عدّل القيم في .env

# 5. حمّل المتغيرات وشغّل
export $(cat .env | xargs)  # Linux/Mac
python bot.py
```

### إذا Raqm غير مدعوم:

```bash
# Ubuntu/Debian
sudo apt install libraqm0 libfribidi0 libharfbuzz0b
pip install --upgrade --force-reinstall Pillow

# macOS
brew install libraqm
pip install --upgrade --force-reinstall Pillow

# Windows: Pillow على Windows يحتاج بناءً خاصاً، استخدم WSL أو Docker
```

---

## 📁 بنية المشروع

```
4Ever_Bot/
├── bot.py                  ← البوت الرئيسي (هاندلر الأوامر)
├── post_generator.py       ← مولّد التصميم (1080×1080)
├── news_scout.py           ← باحث الأخبار (Anthropic + web search)
├── requirements.txt        ← اعتماديات Python
├── build.sh                ← سكريبت بناء Render
├── render.yaml             ← إعدادات Render
├── .env.example            ← قالب متغيرات البيئة
├── .gitignore              ← لا ترفع .env!
│
├── backgrounds/
│   ├── cosmic_purple.png   ← الخلفية الافتراضية
│   └── cosmic_gold.png     ← الخلفية البديلة
│
├── fonts/
│   ├── Cairo.ttf
│   ├── Orbitron.ttf
│   └── Tajawal-*.ttf
│
└── output/                 ← مخرجات مؤقتة (تُحذف تلقائياً)
```

---

## 💰 التكلفة المتوقّعة

| البند | التكلفة |
|---|---|
| Render Free | $0 (يكفي تماماً) |
| Telegram Bot API | $0 (مجاني للأبد) |
| Anthropic API | ~$0.01 لكل منشور |
| **شهرياً** (100 منشور) | **~$1** |

---

## 🐛 المشاكل الشائعة

### "البوت لا يردّ"

```bash
# في Render → Logs، تحقّق من الأخطاء
# الأشهر: متغيرات البيئة غير مضبوطة
```

### "النص العربي بأحرف منفصلة"

→ Raqm غير مثبّت. شغّل `build.sh` يدوياً أو راجع قسم Raqm أعلاه.

### "Image download failed"

→ بعض المصادر تحظر التنزيل المباشر. البوت يستخدم placeholder تلقائياً.

### "البوت ينام على Render Free"

→ هذه ميزة (لتوفير الموارد). يستيقظ خلال 30 ثانية من أول رسالة.
→ للبقاء دائماً: ارفع لـ Render Starter ($7/شهر) أو استخدم خدمة كـ UptimeRobot لإيقاظه.

---

## 🎨 تخصيص البوت

### تغيير عدد المنشورات الأقصى

في `bot.py`:
```python
MAX_POSTS = 5  # غيّرها لما تريد
```

### تغيير الخلفية الافتراضية

في `bot.py` → `base_config()`:
```python
"file": "backgrounds/cosmic_gold.png",  # بدل البنفسجية
```

### إضافة أوامر جديدة

```python
async def cmd_custom(update, ctx):
    await update.message.reply_text("My custom command!")

app.add_handler(CommandHandler("custom", cmd_custom))
```

---

## 📊 المراقبة

في Render → **Logs**، ستشاهد:
```
2026-05-22 14:30:12 | 4ever_bot | INFO | 🤖 Starting 4Ever Telegram Bot...
2026-05-22 14:30:13 | 4ever_bot | INFO | ✅ Bot ready. Polling...
2026-05-22 14:35:42 | 4ever_bot | INFO | Generating post 1/3
```

---

## 🔄 التحديثات

أي تعديل تدفعه لـ GitHub → Render سينشره تلقائياً خلال دقيقتين.

```bash
git add .
git commit -m "Update bot"
git push
# لا تحتاج لمس Render!
```

---

## 🆘 الدعم

- مشاكل تقنية: راجع `Logs` في Render
- مشاكل في التصميم: عدّل `post_generator.py`
- تحسين الكابشن: عدّل `PROMPT` في `news_scout.py`

---

صُمّم بـ ❤️ لـ **4Ever** 🌌
