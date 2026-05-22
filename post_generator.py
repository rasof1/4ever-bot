"""
4Ever Post Generator — callable module
Generates a 1080×1080 PNG post given config dict + main image path.
"""

import os
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import random

ROOT = Path(__file__).parent
FONTS_DIR = ROOT / "fonts"


# ─── Helpers ────────────────────────────────────────────────────
def hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))


def hex_to_rgba(h, a=255):
    return (*hex_to_rgb(h), a)


def load_font(name, size):
    path = FONTS_DIR / name
    if not path.exists():
        return ImageFont.load_default()
    return ImageFont.truetype(str(path), size)


def draw_ar(draw, xy, text, font, fill):
    draw.text(xy, text, font=font, fill=fill, language="ar", direction="rtl")


def measure_ar(draw, text, font):
    return draw.textbbox((0, 0), text, font=font, language="ar", direction="rtl")


def draw_text_shadow(draw, xy, text, font, fill, is_ar=False, offset=2):
    x, y = xy
    fn = draw_ar if is_ar else (lambda d, p, t, f, c: d.text(p, t, font=f, fill=c))
    for dx, dy in [(offset, offset), (-offset, offset),
                   (offset, -offset), (-offset, -offset)]:
        fn(draw, (x + dx, y + dy), text, font, (0, 0, 0, 200))
    fn(draw, (x, y), text, font, fill)


# ─── Background ─────────────────────────────────────────────────
def prepare_background(bg_path, size, darken=0.25):
    bg = Image.open(bg_path).convert("RGB")
    bw, bh = bg.size
    scale = max(size / bw, size / bh)
    bg = bg.resize((int(bw * scale), int(bh * scale)), Image.LANCZOS)
    l = (bg.width - size) // 2
    t = (bg.height - size) // 2
    bg = bg.crop((l, t, l + size, t + size))
    if darken > 0:
        bg = Image.blend(bg, Image.new("RGB", (size, size), (0, 0, 0)), darken)
    return bg


def add_star_particles(canvas, count=35, seed=4):
    random.seed(seed)
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    W, H = canvas.size
    for _ in range(count):
        x, y = random.randint(0, W), random.randint(0, H)
        r = random.choice([1, 1, 2, 2, 3])
        a = random.randint(120, 255)
        d.ellipse((x - r, y - r, x + r, y + r), fill=(255, 255, 255, a))
    glow = layer.filter(ImageFilter.GaussianBlur(2))
    c = canvas.convert("RGBA")
    c = Image.alpha_composite(c, glow)
    c = Image.alpha_composite(c, layer)
    return c.convert("RGB")


# ─── Main asset ─────────────────────────────────────────────────
def paste_main_asset(canvas, asset_path, target_w, radius, glow_color, glow_alpha):
    img = Image.open(asset_path).convert("RGB")
    target_h = int(img.height * (target_w / img.width))
    img = img.resize((target_w, target_h), Image.LANCZOS)
    W, H = canvas.size
    gx, gy = (W - target_w) // 2, (H - target_h) // 2 - 20

    glow_size = 40
    glow = Image.new("RGBA",
                     (target_w + glow_size * 2, target_h + glow_size * 2),
                     (0, 0, 0, 0))
    ImageDraw.Draw(glow).rounded_rectangle(
        (glow_size, glow_size, glow_size + target_w, glow_size + target_h),
        radius=radius + 8, fill=(*hex_to_rgb(glow_color), glow_alpha)
    )
    glow = glow.filter(ImageFilter.GaussianBlur(20))
    canvas.paste(glow, (gx - glow_size, gy - glow_size), glow)

    mask = Image.new("L", (target_w, target_h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, target_w, target_h), radius=radius, fill=255)
    canvas.paste(img, (gx, gy), mask)

    ImageDraw.Draw(canvas).rounded_rectangle(
        (gx - 3, gy - 3, gx + target_w + 3, gy + target_h + 3),
        radius=radius + 3, outline=(255, 255, 255, 80), width=3
    )
    return gx, gy, target_w, target_h


# ─── Header ─────────────────────────────────────────────────────
def draw_logo(canvas, text, color):
    draw = ImageDraw.Draw(canvas, "RGBA")
    font = load_font("Orbitron.ttf", 44)
    W = canvas.size[0]
    bb = draw.textbbox((0, 0), text, font=font)
    x = W - (bb[2] - bb[0]) - 50
    y = 38
    first = text[0]; rest = text[1:]
    fw = draw.textbbox((0, 0), first, font=font)[2]
    draw.text((x, y), first, font=font, fill=hex_to_rgba(color))
    draw.text((x + fw, y), rest, font=font, fill=(255, 255, 255, 255))


def draw_social_icons(canvas, icons, label):
    draw = ImageDraw.Draw(canvas, "RGBA")
    size, gap, x, y = 44, 12, 50, 45
    for icon in icons:
        draw.ellipse((x, y, x + size, y + size),
                     fill=(255, 255, 255, 25),
                     outline=(255, 255, 255, 120), width=2)
        _draw_icon(draw, x, y, size, icon)
        x += size + gap
    f = load_font("Orbitron.ttf", 20)
    draw.text((x + 6, y + 10), label, font=f, fill=(255, 255, 255, 230))


def _draw_icon(draw, x, y, size, icon):
    cx, cy = x + size // 2, y + size // 2
    if icon == "facebook":
        f = load_font("Cairo.ttf", int(size * 0.55))
        bb = draw.textbbox((0, 0), "f", font=f)
        draw.text((cx - (bb[2] - bb[0]) // 2 - bb[0],
                   cy - (bb[3] - bb[1]) // 2 - bb[1]),
                  "f", font=f, fill=(255, 255, 255, 255))
    elif icon == "instagram":
        pad = int(size * 0.25)
        draw.rounded_rectangle((x + pad, y + pad, x + size - pad, y + size - pad),
                               radius=5, outline=(255, 255, 255, 255), width=2)
        ip = int(size * 0.38)
        draw.ellipse((x + ip, y + ip, x + size - ip, y + size - ip),
                     outline=(255, 255, 255, 255), width=2)
        dot = int(size * 0.08)
        dx, dy = x + size - ip + 1, y + ip - dot - 1
        draw.ellipse((dx, dy, dx + dot, dy + dot), fill=(255, 255, 255, 255))
    elif icon == "x":
        f = load_font("Orbitron.ttf", int(size * 0.55))
        bb = draw.textbbox((0, 0), "X", font=f)
        draw.text((cx - (bb[2] - bb[0]) // 2 - bb[0],
                   cy - (bb[3] - bb[1]) // 2 - bb[1]),
                  "X", font=f, fill=(255, 255, 255, 255))


# ─── Source logo ────────────────────────────────────────────────
SOURCE_LOGOS = {
    "google": {"text": "Google", "colors": [(66, 133, 244), (234, 67, 53), (251, 188, 5),
                                            (66, 133, 244), (52, 168, 83), (234, 67, 53)]},
    "openai": {"text": "OpenAI", "colors": [(16, 163, 127)] * 6},
    "anthropic": {"text": "Anthropic", "colors": [(204, 120, 92)] * 9},
    "github": {"text": "GitHub", "colors": [(36, 41, 46)] * 6},
    "meta": {"text": "Meta", "colors": [(24, 119, 242)] * 4},
    "microsoft": {"text": "Microsoft", "colors": [(243, 83, 37), (129, 188, 6),
                                                  (5, 166, 240), (255, 186, 8),
                                                  (115, 115, 115)] * 2},
    "apple": {"text": "Apple", "colors": [(40, 40, 40)] * 5},
    "nvidia": {"text": "NVIDIA", "colors": [(118, 185, 0)] * 6},
    "xai": {"text": "xAI", "colors": [(0, 0, 0)] * 3},
    # Phones
    "samsung": {"text": "SAMSUNG", "colors": [(20, 40, 160)] * 7},
    "xiaomi": {"text": "Xiaomi", "colors": [(255, 103, 0)] * 6},
    "oneplus": {"text": "OnePlus", "colors": [(235, 12, 35)] * 7},
    # Gaming
    "sony": {"text": "SONY", "colors": [(0, 0, 0)] * 4},
    "playstation": {"text": "PlayStation", "colors": [(0, 55, 145)] * 11},
    "xbox": {"text": "Xbox", "colors": [(16, 124, 16)] * 4},
    "nintendo": {"text": "Nintendo", "colors": [(229, 9, 20)] * 8},
    "steam": {"text": "Steam", "colors": [(23, 26, 33)] * 5},
    # Hardware
    "amd": {"text": "AMD", "colors": [(237, 28, 36)] * 3},
    "intel": {"text": "Intel", "colors": [(0, 113, 197)] * 5},
    "qualcomm": {"text": "Qualcomm", "colors": [(225, 27, 34)] * 8},
}


def draw_source_logo(canvas, gx, gy, src):
    draw = ImageDraw.Draw(canvas, "RGBA")
    pw, ph = 100, 50
    lx, ly = gx + 22, gy + 22
    pill = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
    ImageDraw.Draw(pill).rounded_rectangle((0, 0, pw, ph), radius=25, fill=(255, 255, 255, 245))
    canvas.paste(pill, (lx, ly), pill)
    info = SOURCE_LOGOS.get(src, SOURCE_LOGOS["google"])
    text, colors = info["text"], info["colors"]
    font = load_font("Tajawal-Bold.ttf", 22)
    widths = [draw.textbbox((0, 0), ch, font=font)[2] for ch in text]
    total = sum(widths) + len(text) - 1
    cur = lx + (pw - total) // 2
    ty = ly + (ph - 22) // 2 - 4
    for i, ch in enumerate(text):
        c = colors[i] if i < len(colors) else colors[-1]
        draw.text((cur, ty), ch, font=font, fill=c)
        cur += widths[i] + 1


# ─── Trend arrow ────────────────────────────────────────────────
def draw_trend_arrow(canvas, gx, gy, tw, color):
    W, H = canvas.size
    sz = 56; ax = gx + tw - sz - 22; ay = gy + 22
    rgb = hex_to_rgb(color)
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    cx, cy = ax + sz // 2, ay + sz // 2
    for r in range(sz // 2 + 38, sz // 2, -2):
        a = int(90 * (1 - (r - sz // 2) / 38))
        gd.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(*rgb, a))
    glow = glow.filter(ImageFilter.GaussianBlur(10))
    c = canvas.convert("RGBA")
    c.alpha_composite(glow)
    out = c.convert("RGB")
    d = ImageDraw.Draw(out, "RGBA")
    d.ellipse((ax, ay, ax + sz, ay + sz), fill=(*rgb, 255))
    cx, cy = ax + sz // 2, ay + sz // 2
    L = 18
    d.line([(cx - L // 2, cy + L // 2), (cx + L // 2, cy - L // 2)],
           fill=(255, 255, 255), width=4)
    d.line([(cx + L // 2 - 9, cy - L // 2), (cx + L // 2 + 1, cy - L // 2)],
           fill=(255, 255, 255), width=4)
    d.line([(cx + L // 2, cy - L // 2 - 1), (cx + L // 2, cy - L // 2 + 9)],
           fill=(255, 255, 255), width=4)
    return out


# ─── Badges ─────────────────────────────────────────────────────
def draw_product_badge(canvas, gx, gy, th, text):
    if not text: return
    draw = ImageDraw.Draw(canvas, "RGBA")
    font = load_font("Orbitron.ttf", 14)
    bb = draw.textbbox((0, 0), text, font=font)
    bw, bh = bb[2] - bb[0], bb[3] - bb[1]
    bx = gx + 22; by = gy + th - bh - 30
    draw.rounded_rectangle((bx - 14, by - 10, bx + bw + 14, by + bh + 14),
                           radius=12, fill=(0, 0, 0, 180),
                           outline=(255, 255, 255, 80), width=1)
    draw.text((bx, by - bb[1]), text, font=font, fill=(255, 255, 255, 255))


def draw_live_badge(canvas, gx, gy, tw, th, text):
    if not text: return
    draw = ImageDraw.Draw(canvas, "RGBA")
    font = load_font("Orbitron.ttf", 14)
    bb = draw.textbbox((0, 0), text, font=font)
    lw, lh = bb[2] - bb[0], bb[3] - bb[1]
    lx = gx + tw - lw - 50; ly = gy + th - lh - 30
    draw.rounded_rectangle((lx - 28, ly - 10, lx + lw + 14, ly + lh + 14),
                           radius=12, fill=(0, 0, 0, 180),
                           outline=(255, 255, 255, 80), width=1)
    draw.ellipse((lx - 18, ly + 4, lx - 6, ly + 16), fill=(239, 68, 68))
    draw.text((lx, ly - bb[1]), text, font=font, fill=(255, 255, 255, 255))


# ─── Headline ───────────────────────────────────────────────────
def draw_headline(canvas, gy, th, headline):
    draw = ImageDraw.Draw(canvas, "RGBA")
    W = canvas.size[0]
    font = load_font("Cairo.ttf", headline.get("font_size", 44))
    accent = headline.get("highlight_color", "#00d4ff")
    line1 = headline["line1"]
    line2_ar = headline.get("line2_arabic", "")
    line2_en = headline.get("line2_english", "").strip()

    bb1 = measure_ar(draw, line1, font)
    w1, h1 = bb1[2] - bb1[0], bb1[3] - bb1[1]
    fy = gy + th + 50
    x1 = (W - w1) // 2
    draw_text_shadow(draw, (x1, fy), line1, font, (255, 255, 255, 255), is_ar=True)

    line2_y = fy + h1 + 32
    if line2_ar:
        if line2_en:
            bb2 = measure_ar(draw, line2_ar, font)
            bb_en = draw.textbbox((0, 0), line2_en, font=font)
            w2, en_w = bb2[2] - bb2[0], bb_en[2] - bb_en[0]
            gap = 18
            x_start = (W - (w2 + gap + en_w)) // 2
            draw_text_shadow(draw, (x_start, line2_y), line2_en, font, hex_to_rgba(accent))
            draw_text_shadow(draw, (x_start + en_w + gap, line2_y),
                             line2_ar, font, (255, 255, 255, 255), is_ar=True)
        else:
            bb2 = measure_ar(draw, line2_ar, font)
            x2 = (W - (bb2[2] - bb2[0])) // 2
            draw_text_shadow(draw, (x2, line2_y), line2_ar, font,
                             (255, 255, 255, 255), is_ar=True)
        return line2_y + h1 + 36
    return fy + h1 + 36


# ─── Decorations ────────────────────────────────────────────────
def draw_corner_brackets(canvas, color, top_offset=105):
    draw = ImageDraw.Draw(canvas, "RGBA")
    W, H = canvas.size
    c = (*hex_to_rgb(color), 120); t = 3; s = 70; m = 28
    pts = [
        [(m, top_offset), (m + s, top_offset)],
        [(m, top_offset), (m, top_offset + s)],
        [(W - m - s, top_offset), (W - m, top_offset)],
        [(W - m, top_offset), (W - m, top_offset + s)],
        [(m, H - m - s), (m, H - m)],
        [(m, H - m), (m + s, H - m)],
        [(W - m - s, H - m), (W - m, H - m)],
        [(W - m, H - m - s), (W - m, H - m)],
    ]
    for p in pts:
        draw.line(p, fill=c, width=t)


def draw_decorative_line(canvas, y, color):
    draw = ImageDraw.Draw(canvas, "RGBA")
    W = canvas.size[0]
    c = (*hex_to_rgb(color), 200)
    draw.rectangle((W // 2 - 100, y, W // 2 + 100, y + 3), fill=c)


# ─── MAIN ENTRY POINT ───────────────────────────────────────────
def generate_post(config: dict, output_path: str, seed: int = None) -> str:
    """
    Generate a 4Ever post image from config dict.

    Args:
        config: Dict matching config.json structure with main_asset.file
                pointing to the actual image path.
        output_path: Where to save the final PNG.
        seed: Optional random seed for star particles.

    Returns:
        Path to the generated PNG.
    """
    size = config["output"]["size"]
    bg_path = ROOT / config["background"]["file"]
    canvas = prepare_background(bg_path, size, config["background"]["darken_amount"])
    canvas = add_star_particles(canvas, seed=seed or 4)

    asset_path = config["main_asset"]["file"]
    gx, gy, tw, th = paste_main_asset(
        canvas, asset_path,
        target_w=880,
        radius=config["main_asset"]["corner_radius"],
        glow_color=config["main_asset"]["glow_color"],
        glow_alpha=config["main_asset"]["glow_intensity"]
    )

    draw_logo(canvas, config["page"]["logo_text"], config["page"]["primary_color"])

    if config["socials"]["show"]:
        draw_social_icons(canvas, config["socials"]["icons"], config["page"]["name"])

    draw_source_logo(canvas, gx, gy, config["source_logo"]["type"])

    if config["trend_indicator"]["show"]:
        canvas = draw_trend_arrow(canvas, gx, gy, tw, config["trend_indicator"]["color"])

    if config["product_badge"]["show"]:
        draw_product_badge(canvas, gx, gy, th, config["product_badge"]["text"])

    if config["live_badge"]["show"]:
        draw_live_badge(canvas, gx, gy, tw, th, config["live_badge"]["text"])

    line_y = draw_headline(canvas, gy, th, config["headline"])

    if config["decorations"]["corner_brackets"]:
        draw_corner_brackets(canvas, config["page"]["primary_color"])
    if config["decorations"]["decorative_line"]:
        draw_decorative_line(canvas, line_y, config["page"]["primary_color"])

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    canvas.save(output_path, "PNG", quality=config["output"]["quality"], optimize=True)
    return output_path
