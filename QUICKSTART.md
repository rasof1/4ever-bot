# 🚀 4Ever Bot — البدء المجاني تماماً

> ✅ **مجاني 100%** - بدون أي بطاقة ائتمان
> ✅ **البوت جاهز:** [@rasof_bot](https://t.me/rasof_bot)
> ✅ **الاسم:** 4Ever | منشورات تقنية

---

## 📊 ما الذي يعمل مجاناً؟

| الخدمة | التكلفة | الحد |
|---|---|---|
| Telegram Bot API | $0 | لا نهائي |
| Google Gemini API | $0 | 1,500 طلب/يوم |
| Render Background Worker | $0 | 750 ساعة/شهر |
| **الإجمالي** | **$0** | كافٍ تماماً |

---

## ⚡ 3 خطوات للبدء (10 دقائق)

### 1️⃣ احصل على Gemini API Key مجاناً (دقيقتان)

1. اذهب إلى: **https://aistudio.google.com/app/apikey**
2. سجّل دخول بحساب Google
3. اضغط **Create API Key** ← **Create API Key in new project**
4. انسخ المفتاح (يبدأ بـ `AIza...`)
5. احفظه — ستحتاجه في الخطوة 3

> 💡 لا يحتاج بطاقة ائتمان! المجاني يكفي لـ ~50 منشور يومياً

---

### 2️⃣ ارفع المشروع لـ GitHub (3 دقائق)

#### الطريقة السهلة (بدون أي كمندات):

1. اذهب لـ **https://github.com** ← أنشئ حساب مجاني
2. اضغط الأيقونة `+` (أعلى يمين) ← **New repository**
3. الاسم: `4ever-bot`
4. اختر **Private** ⚠️ (مهم - لحماية الكود)
5. اضغط **Create repository**
6. في الصفحة الجديدة، اضغط **uploading an existing file**
7. **اسحب وأفلت** كل محتويات مجلد `4Ever_Bot/`
   - ⚠️ تخطّى ملف `.env` (موجود؟ احذفه قبل الرفع)
8. اكتب رسالة commit: "Initial bot"
9. اضغط **Commit changes**

✅ تم! انسخ رابط الـ repo (مثلاً: `https://github.com/USERNAME/4ever-bot`)

---

### 3️⃣ انشر على Render (3 دقائق)

1. اذهب لـ **https://render.com** ← **Sign in with GitHub**
2. اضغط **New +** ← **Background Worker**
3. اختر مستودع `4ever-bot` ← **Connect**
4. املأ الحقول:

   | الحقل | القيمة |
   |---|---|
   | Name | `4ever-bot` |
   | Region | `Frankfurt` |
   | Branch | `main` |
   | Runtime | `Python 3` |
   | Build Command | `bash build.sh` |
   | Start Command | `python bot.py` |
   | Instance Type | **Free** |

5. اضغط **Advanced** ← **Add Environment Variable** (مرّتين):

   | Key | Value |
   |---|---|
   | `TELEGRAM_BOT_TOKEN` | `8903340271:AAEA7Sho4mfYTRnoKVsWGrjIcMUKUNUnNHw` |
   | `GEMINI_API_KEY` | `AIza...` (من الخطوة 1) |

6. اضغط **Create Background Worker**

7. انتظر 3-5 دقائق حتى تظهر **Live** 🟢

---

## ✅ جرّب البوت

افتح: **https://t.me/rasof_bot**

اضغط **Start** ← أرسل:
```
منشور
```

أو لـ 3 منشورات:
```
منشور 3
```

ستحصل خلال 30-60 ثانية على:
- 🖼️ صورة 1080×1080 جاهزة للنشر
- 📝 كابشن عربي كامل
- 🔗 رابط المصدر

---

## 🆘 مشاكل محتملة

### "Bot doesn't respond"
→ في Render Dashboard ← **Logs**:
- لو `GEMINI_API_KEY not set` → ارجع للخطوة 3 وأضف المتغيّر
- لو `unauthorized` → التوكن خطأ

### "الأحرف العربية منفصلة"
→ Build فشل في تثبيت Raqm. في Render:
- **Manual Deploy** ← **Clear cache & deploy**

### "البوت بطيء أول مرة"
→ طبيعي. Render Free ينام بعد 15 دقيقة. أول طلب يستغرق 30-50 ثانية للاستيقاظ.

### "Rate limit exceeded" من Gemini
→ تجاوزت 1,500 طلب/يوم. انتظر يوماً، أو أنشئ مفتاحاً ثانياً.

---

## 🎁 ميزات إضافية للمستقبل

أخبرني لو أردت:
- 📅 جدولة منشورات تلقائية (كل صباح مثلاً)
- 📤 نشر تلقائي على فيسبوك مباشرة
- 🎨 خلفيات إضافية
- 🌐 لوحة تحكم ويب

---

صُمّم بـ ❤️ لـ **4Ever** 🌌
