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
def prepare_background(bg_path, size, darken=0.25, height=None):
    """Prepare background for canvas. If height is provided, creates a non-square canvas
    (useful for portrait reels 1080x1920)."""
    target_w = size if isinstance(size, int) else size[0]
    target_h = height if height else (size if isinstance(size, int) else size[1] if len(size) > 1 else size)
    if height is None and isinstance(size, int):
        target_h = size

    bg = Image.open(bg_path).convert("RGB")
    bw, bh = bg.size
    # Scale to cover target dimensions
    scale = max(target_w / bw, target_h / bh)
    bg = bg.resize((int(bw * scale), int(bh * scale)), Image.LANCZOS)
    l = (bg.width - target_w) // 2
    t = (bg.height - target_h) // 2
    bg = bg.crop((l, t, l + target_w, t + target_h))
    if darken > 0:
        bg = Image.blend(bg, Image.new("RGB", (target_w, target_h), (0, 0, 0)), darken)
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
    """Fit the image in safe zone. Adapts to image's NATIVE aspect ratio.
    
    Since canvas is sized to match media aspect (via choose_canvas_size_for_aspect),
    the image fills the safe area naturally with no/minimal padding.
    """
    img = Image.open(asset_path).convert("RGB")
    img_w, img_h = img.size
    W, H = canvas.size

    SAFE_TOP = 175
    HEADLINE_RESERVED = 400
    SAFE_BOTTOM = H - HEADLINE_RESERVED
    SAFE_WIDTH = int(W * 0.85)

    available_h = SAFE_BOTTOM - SAFE_TOP
    available_w = SAFE_WIDTH

    # ⭐ Choose frame size to FIT image aspect inside available area (no crop, no excessive pad)
    aspect = img_w / img_h
    avail_aspect = available_w / available_h

    if aspect >= avail_aspect:
        # Image is wider than available → frame width = available_w
        frame_w = available_w
        frame_h = int(frame_w / aspect)
    else:
        # Image is taller than available → frame height = available_h
        frame_h = available_h
        frame_w = int(frame_h * aspect)

    target_w = frame_w
    target_h = frame_h

    # Resize image to fit perfectly inside frame (no padding needed since frame matches aspect)
    img = img.resize((target_w, target_h), Image.LANCZOS)

    # Center horizontally
    gx = (W - target_w) // 2
    # Vertically: center in available space
    gy = SAFE_TOP + (available_h - target_h) // 2

    # 🎨 Glow effect
    glow_size = 50
    glow = Image.new("RGBA",
                     (target_w + glow_size * 2, target_h + glow_size * 2),
                     (0, 0, 0, 0))
    ImageDraw.Draw(glow).rounded_rectangle(
        (glow_size, glow_size, glow_size + target_w, glow_size + target_h),
        radius=radius + 8, fill=(*hex_to_rgb(glow_color), min(glow_alpha + 30, 255))
    )
    glow = glow.filter(ImageFilter.GaussianBlur(25))
    canvas.paste(glow, (gx - glow_size, gy - glow_size), glow)

    # Rounded mask + paste image
    mask = Image.new("L", (target_w, target_h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, target_w, target_h), radius=radius, fill=255)

    rgba_img = img.convert("RGBA")
    canvas.paste(rgba_img, (gx, gy), mask)

    # Border outline
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle(
        (gx - 3, gy - 3, gx + target_w + 3, gy + target_h + 3),
        radius=radius + 3, outline=(255, 255, 255, 100), width=2
    )
    draw.rounded_rectangle(
        (gx + 1, gy + 1, gx + target_w - 1, gy + target_h - 1),
        radius=radius - 1, outline=(255, 255, 255, 50), width=1
    )

    return gx, gy, target_w, target_h


# ─── Header ─────────────────────────────────────────────────────
def draw_logo(canvas, text, color):
    """Draw page logo: circular brand image (if exists) + text "4EVER".
    Positioned top-right corner."""
    draw = ImageDraw.Draw(canvas, "RGBA")
    font = load_font("Orbitron.ttf", 44)
    W = canvas.size[0]
    bb = draw.textbbox((0, 0), text, font=font)
    text_w = bb[2] - bb[0]
    y = 38

    # 🎨 Try to load circular brand logo
    logo_path = ROOT / "assets" / "4ever_logo.png"
    logo_size = 64  # circular logo diameter
    margin_right = 50
    gap_between = 14  # gap between logo and text

    if logo_path.exists():
        try:
            logo_img = Image.open(logo_path).convert("RGBA")
            # Resize to logo_size while preserving aspect (square)
            logo_img = logo_img.resize((logo_size, logo_size), Image.LANCZOS)

            # Calculate positions: text on right, logo to its LEFT
            text_x = W - text_w - margin_right
            logo_x = text_x - gap_between - logo_size
            logo_y = y - 8  # center vertically with text

            # Paste logo with alpha
            canvas.paste(logo_img, (logo_x, logo_y), logo_img)

            # Draw text after logo
            first = text[0]; rest = text[1:]
            fw = draw.textbbox((0, 0), first, font=font)[2]
            draw.text((text_x, y), first, font=font, fill=hex_to_rgba(color))
            draw.text((text_x + fw, y), rest, font=font, fill=(255, 255, 255, 255))
            return
        except Exception as e:
            # Fallback to text-only if logo fails
            pass

    # Fallback: text only (original behavior)
    x = W - text_w - margin_right
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
    # Government/MCIT
    "mcit": {"text": "MCIT", "colors": [(0, 87, 184)] * 4},
    "egypt": {"text": "Egypt Gov", "colors": [(206, 17, 38), (255, 255, 255), (0, 0, 0)] * 3},
    # Other tech
    "huawei": {"text": "HUAWEI", "colors": [(206, 6, 14)] * 6},
    "tesla": {"text": "TESLA", "colors": [(204, 0, 0)] * 5},
    "spacex": {"text": "SpaceX", "colors": [(255, 255, 255)] * 6},
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
def fit_text_to_width(draw, text, max_width, max_font_size, min_font_size=24, font_name="Cairo.ttf", is_ar=True):
    """Find largest font size that fits text within max_width."""
    for size in range(max_font_size, min_font_size - 1, -2):
        f = load_font(font_name, size)
        if is_ar:
            bb = draw.textbbox((0, 0), text, font=f, language="ar", direction="rtl")
        else:
            bb = draw.textbbox((0, 0), text, font=f)
        w = bb[2] - bb[0]
        if w <= max_width:
            return f, size
    return load_font(font_name, min_font_size), min_font_size


def draw_headline(canvas, gy, th, headline):
    draw = ImageDraw.Draw(canvas, "RGBA")
    W = canvas.size[0]
    max_text_width = int(W * 0.78)
    accent = headline.get("highlight_color", "#00d4ff")
    line1 = headline["line1"]
    line2_ar = headline.get("line2_arabic", "")
    line2_en = headline.get("line2_english", "").strip()

    # 🌐 Language support: determines RTL vs LTR rendering
    is_rtl = headline.get("is_rtl", True)  # Default Arabic
    font_name = headline.get("font_name", "Cairo.ttf")

    base_size = headline.get("font_size", 44)

    # Auto-fit line 1
    font1, size1 = fit_text_to_width(draw, line1, max_text_width, base_size,
                                      min_font_size=20, font_name=font_name, is_ar=is_rtl)
    if is_rtl:
        bb1 = measure_ar(draw, line1, font1)
    else:
        bb1 = draw.textbbox((0, 0), line1, font=font1)
    w1, h1 = bb1[2] - bb1[0], bb1[3] - bb1[1]
    fy = gy + th + 50
    x1 = (W - w1) // 2
    draw_text_shadow(draw, (x1, fy), line1, font1, (255, 255, 255, 255), is_ar=is_rtl)

    line2_y = fy + h1 + 32
    if line2_ar:
        if line2_en and is_rtl:
            # Arabic + English split layout (only for RTL mode)
            combined_text = f"{line2_en}  {line2_ar}"
            font2, size2 = fit_text_to_width(draw, combined_text, max_text_width, base_size - 4,
                                              min_font_size=18, font_name=font_name, is_ar=True)
            bb2 = measure_ar(draw, line2_ar, font2)
            bb_en = draw.textbbox((0, 0), line2_en, font=font2)
            w2, en_w = bb2[2] - bb2[0], bb_en[2] - bb_en[0]
            gap = 18
            x_start = (W - (w2 + gap + en_w)) // 2
            draw_text_shadow(draw, (x_start, line2_y), line2_en, font2, hex_to_rgba(accent))
            draw_text_shadow(draw, (x_start + en_w + gap, line2_y),
                             line2_ar, font2, (255, 255, 255, 255), is_ar=True)
            line_height = bb2[3] - bb2[1]
        else:
            # Single-line rendering (works for AR, EN, FR)
            font2, _ = fit_text_to_width(draw, line2_ar, max_text_width, base_size - 4,
                                          min_font_size=18, font_name=font_name, is_ar=is_rtl)
            if is_rtl:
                bb2 = measure_ar(draw, line2_ar, font2)
            else:
                bb2 = draw.textbbox((0, 0), line2_ar, font=font2)
            x2 = (W - (bb2[2] - bb2[0])) // 2
            draw_text_shadow(draw, (x2, line2_y), line2_ar, font2,
                             (255, 255, 255, 255), is_ar=is_rtl)
            line_height = bb2[3] - bb2[1]
        return line2_y + line_height + 36
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
def generate_post(config: dict, output_path: str, seed: int = None,
                  return_box: bool = False, canvas_height: int = None) -> str:
    """
    Generate a 4Ever post from config dict.

    Args:
        config: Dict matching config.json structure with main_asset.file
                pointing to the actual image path.
        output_path: Where to save the final PNG.
        seed: Optional random seed for star particles.
        return_box: If True, also return the (gx, gy, tw, th) bounding box of the main image.

    Returns:
        Path to the generated PNG, or (path, box_coords) tuple if return_box=True.
    """
    size = config["output"]["size"]
    actual_h = canvas_height or size  # Default square, but can be taller
    bg_path = ROOT / config["background"]["file"]
    canvas = prepare_background(bg_path, size, config["background"]["darken_amount"], height=actual_h)
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

    if return_box:
        return output_path, (gx, gy, tw, th)
    return output_path


def generate_post_layers(config: dict, bg_path: str, overlay_path: str, seed: int = None,
                          media_w: int = None, media_h: int = None):
    """
    Generate the post as TWO layers + box coords:
    - bg_path: cosmic background + stars + headers (no main image)
    - overlay_path: RGBA with badges, source, trend arrow, headline (transparent center)
    - returns (gx, gy, tw, th) where the media should go
    
    If media_w/media_h provided, the frame is sized to match THAT aspect ratio exactly.
    """
    size = config["output"]["size"]
    canvas_height = config.get("canvas_height") or size

    # ===== LAYER 1: Background =====
    bg_canvas_path = ROOT / config["background"]["file"]
    bg_canvas = prepare_background(bg_canvas_path, size, config["background"]["darken_amount"], height=canvas_height)
    bg_canvas = add_star_particles(bg_canvas, seed=seed or 4)

    # Header
    draw_logo(bg_canvas, config["page"]["logo_text"], config["page"]["primary_color"])
    if config["socials"]["show"]:
        draw_social_icons(bg_canvas, config["socials"]["icons"], config["page"]["name"])

    # 🎯 Compute box coords directly (no dummy image hack)
    W, H = bg_canvas.size
    SAFE_TOP = 175
    HEADLINE_RESERVED = 400
    SAFE_BOTTOM = H - HEADLINE_RESERVED
    SAFE_WIDTH = int(W * 0.85)
    available_h = SAFE_BOTTOM - SAFE_TOP
    available_w = SAFE_WIDTH

    # If media dimensions given, frame matches that exact aspect (no padding!)
    if media_w and media_h:
        aspect = media_w / media_h
        avail_aspect = available_w / available_h
        if aspect >= avail_aspect:
            frame_w = available_w
            frame_h = int(frame_w / aspect)
        else:
            frame_h = available_h
            frame_w = int(frame_h * aspect)
    else:
        # Default: use full available area
        frame_w = available_w
        frame_h = available_h

    target_w = frame_w
    target_h = frame_h
    gx = (W - target_w) // 2
    gy = SAFE_TOP + (available_h - target_h) // 2

    # 🎨 Draw glow effect for the frame (so it shows behind the video)
    glow_size = 50
    glow_color = config["main_asset"]["glow_color"]
    glow_alpha = config["main_asset"]["glow_intensity"]
    radius = config["main_asset"]["corner_radius"]
    glow = Image.new("RGBA",
                     (target_w + glow_size * 2, target_h + glow_size * 2),
                     (0, 0, 0, 0))
    ImageDraw.Draw(glow).rounded_rectangle(
        (glow_size, glow_size, glow_size + target_w, glow_size + target_h),
        radius=radius + 8, fill=(*hex_to_rgb(glow_color), min(glow_alpha + 30, 255))
    )
    glow = glow.filter(ImageFilter.GaussianBlur(25))
    bg_canvas.paste(glow, (gx - glow_size, gy - glow_size), glow)

    # Save background layer
    os.makedirs(os.path.dirname(bg_path) or ".", exist_ok=True)
    bg_canvas.save(bg_path, "PNG", quality=95)

    # ===== LAYER 2: Overlay (badges + source + headline + decorations) =====
    overlay = Image.new("RGBA", (W, canvas_height), (0, 0, 0, 0))

    # Border outline around frame (visible on top of video)
    draw = ImageDraw.Draw(overlay)
    draw.rounded_rectangle(
        (gx - 3, gy - 3, gx + target_w + 3, gy + target_h + 3),
        radius=radius + 3, outline=(255, 255, 255, 100), width=2
    )

    # Source logo (top-left of frame)
    draw_source_logo(overlay, gx, gy, config["source_logo"]["type"])

    # Trend arrow (top-right of frame)
    if config["trend_indicator"]["show"]:
        overlay = draw_trend_arrow(overlay, gx, gy, target_w, config["trend_indicator"]["color"])

    # Product badge (bottom-left)
    if config["product_badge"]["show"]:
        draw_product_badge(overlay, gx, gy, target_h, config["product_badge"]["text"])

    # Live badge (bottom-right)
    if config["live_badge"]["show"]:
        draw_live_badge(overlay, gx, gy, target_w, target_h, config["live_badge"]["text"])

    # Headline (below the image area)
    line_y = draw_headline(overlay, gy, target_h, config["headline"])

    # Decorations
    if config["decorations"]["corner_brackets"]:
        draw_corner_brackets(overlay, config["page"]["primary_color"])
    if config["decorations"]["decorative_line"]:
        draw_decorative_line(overlay, line_y, config["page"]["primary_color"])

    # Re-draw header on overlay too (above any video frame leakage)
    draw_logo(overlay, config["page"]["logo_text"], config["page"]["primary_color"])
    if config["socials"]["show"]:
        draw_social_icons(overlay, config["socials"]["icons"], config["page"]["name"])

    os.makedirs(os.path.dirname(overlay_path) or ".", exist_ok=True)
    overlay.save(overlay_path, "PNG")

    return (gx, gy, target_w, target_h)
