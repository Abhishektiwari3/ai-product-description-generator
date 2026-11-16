from __future__ import annotations

import io
import math
from typing import Dict, List, Tuple

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps, UnidentifiedImageError

ALLOWED_FORMATS = {"PNG", "JPEG", "WEBP", "GIF", "BMP"}
MAX_SIDE = 1600

NAMED_COLOURS: Dict[str, Tuple[int, int, int]] = {
    "black": (20, 20, 20), "charcoal": (60, 60, 64), "grey": (128, 128, 128), "silver": (192, 192, 196),
    "white": (245, 245, 245), "red": (200, 40, 40), "maroon": (120, 20, 30), "orange": (240, 130, 30),
    "yellow": (240, 210, 40), "gold": (200, 165, 70), "beige": (225, 205, 170), "brown": (120, 80, 45),
    "green": (50, 150, 70), "olive": (110, 120, 50), "teal": (30, 130, 130), "blue": (40, 90, 200),
    "navy": (25, 35, 90), "sky blue": (120, 180, 230), "purple": (120, 60, 170), "pink": (235, 130, 170),
}

STYLE_PALETTES = {
    "studio_white": ((250, 250, 252), (232, 234, 240), (30, 30, 40)),
    "lifestyle": ((255, 236, 210), (200, 225, 210), (40, 40, 40)),
    "luxury_dark": ((28, 26, 32), (8, 8, 10), (230, 205, 150)),
    "minimal_pastel": ((236, 228, 250), (214, 238, 236), (60, 60, 80)),
    "vibrant_pop": ((255, 90, 120), (255, 190, 60), (255, 255, 255)),
}


class ImageProblem(Exception):
    pass


def _pixels(im: Image.Image) -> list:
    """Pixel values as a list. Pillow 12.1+ renamed getdata() to get_flattened_data() (getdata goes in Pillow 14)."""
    getter = getattr(im, "get_flattened_data", None) or im.getdata
    return list(getter())


def load_image(data: bytes, max_mb: float = 8) -> Image.Image:
    if not data:
        raise ImageProblem("No image was uploaded.")
    if len(data) > max_mb * 1024 * 1024:
        raise ImageProblem(f"Image is larger than {max_mb:g} MB.")
    try:
        img = Image.open(io.BytesIO(data))
        fmt = img.format
        img.load()
    except (UnidentifiedImageError, OSError) as e:
        raise ImageProblem("File is not a readable image (use PNG, JPG or WEBP).") from e
    if fmt not in ALLOWED_FORMATS:
        raise ImageProblem(f"Unsupported image format {fmt}.")
    img = ImageOps.exif_transpose(img)
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA" if "A" in img.getbands() else "RGB")
    img.thumbnail((MAX_SIDE, MAX_SIDE))
    return img


def to_png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _font(size: int, bold: bool = False):
    candidates = [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
    ]
    for c in candidates:
        try:
            return ImageFont.truetype(c, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _nearest_name(rgb: Tuple[int, int, int]) -> str:
    return min(NAMED_COLOURS, key=lambda n: sum((a - b) ** 2 for a, b in zip(NAMED_COLOURS[n], rgb)))


def _border_stats(img: Image.Image) -> Tuple[Tuple[int, int, int], float]:
    """Average colour of the outer border and how uniform it is (low = plain background)."""
    rgb = img.convert("RGB")
    w, h = rgb.size
    b = max(2, min(w, h) // 40)
    px = []
    for box in [(0, 0, w, b), (0, h - b, w, h), (0, 0, b, h), (w - b, 0, w, h)]:
        px += _pixels(rgb.crop(box).resize((20, 20)))
    avg = tuple(int(sum(p[i] for p in px) / len(px)) for i in range(3))
    var = sum(sum((p[i] - avg[i]) ** 2 for i in range(3)) for p in px) / len(px)
    return avg, math.sqrt(var)


def analyze(img: Image.Image) -> dict:
    """Cheap, honest visual facts: size, orientation, dominant colours, background."""
    rgb = img.convert("RGB")
    w, h = rgb.size
    small = rgb.resize((120, int(120 * h / w) or 1))
    bg, spread = _border_stats(rgb)
    q = small.quantize(colors=6, method=Image.Quantize.MEDIANCUT)
    pal = q.getpalette()
    counts = sorted(q.getcolors(), reverse=True)
    colours: List[str] = []
    for count, idx in counts:
        c = tuple(pal[idx * 3: idx * 3 + 3])
        if sum((a - b) ** 2 for a, b in zip(c, bg)) < 900 and spread < 25:
            continue
        name = _nearest_name(c)
        if name not in colours:
            colours.append(name)
    brightness = sum(_pixels(ImageOps.grayscale(small))) / (small.width * small.height)
    return {
        "width": w,
        "height": h,
        "orientation": "square" if abs(w - h) < 0.05 * max(w, h) else ("landscape" if w > h else "portrait"),
        "dominant_colours": colours[:3] or [_nearest_name(bg)],
        "background": ("plain " + _nearest_name(bg)) if spread < 25 else "busy / scene",
        "brightness": round(brightness / 255, 2),
    }


def _gradient(size: Tuple[int, int], top, bottom) -> Image.Image:
    w, h = size
    base = Image.new("RGB", (1, h))
    for y in range(h):
        t = y / max(1, h - 1)
        base.putpixel((0, y), tuple(int(top[i] * (1 - t) + bottom[i] * t) for i in range(3)))
    return base.resize((w, h))


def _cutout(img: Image.Image) -> Image.Image:
    """Return RGBA with a background mask when the background is plain; otherwise rounded card."""
    rgba = img.convert("RGBA")
    bg, spread = _border_stats(img)
    if spread < 22:
        rgb = img.convert("RGB")
        diff = Image.new("L", rgb.size)
        diff.putdata([min(255, int(math.sqrt(sum((p[i] - bg[i]) ** 2 for i in range(3))) * 4)) for p in _pixels(rgb)])
        mask = diff.point(lambda v: 255 if v > 70 else 0).filter(ImageFilter.MinFilter(3)).filter(ImageFilter.GaussianBlur(1.0))
        if 0.03 < sum(_pixels(mask)) / (255 * mask.width * mask.height) < 0.95:
            rgba.putalpha(mask)
            box = mask.getbbox()
            return rgba.crop(box) if box else rgba
    mask = Image.new("L", rgba.size, 0)
    r = min(rgba.size) // 14
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, rgba.width - 1, rgba.height - 1], radius=r, fill=255)
    rgba.putalpha(mask)
    return rgba


def restyle(img: Image.Image, style: str, size: int = 1024) -> Image.Image:
    """Image -> image offline: cut out (if possible), enhance, and place on a styled square canvas."""
    top, bottom, _ = STYLE_PALETTES.get(style, STYLE_PALETTES["studio_white"])
    canvas = _gradient((size, size), top, bottom).convert("RGBA")
    subject = _cutout(img)
    subject = ImageOps.contain(subject, (int(size * 0.74), int(size * 0.74)))
    rgb = subject.convert("RGB")
    rgb = ImageOps.autocontrast(rgb, cutoff=1)
    rgb = ImageEnhance.Sharpness(rgb).enhance(1.4)
    if style == "vibrant_pop":
        rgb = ImageEnhance.Color(rgb).enhance(1.35)
    elif style == "minimal_pastel":
        rgb = ImageEnhance.Color(rgb).enhance(0.9)
    elif style == "luxury_dark":
        rgb = ImageEnhance.Contrast(rgb).enhance(1.15)
    subject = Image.merge("RGBA", (*rgb.split(), subject.getchannel("A")))
    x = (size - subject.width) // 2
    y = (size - subject.height) // 2 - size // 40
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    sh_alpha = subject.getchannel("A").point(lambda v: int(v * (0.55 if style == "luxury_dark" else 0.28)))
    shadow.paste((0, 0, 0, 255), (x + size // 60, y + size // 30), sh_alpha)
    shadow = shadow.filter(ImageFilter.GaussianBlur(size // 45))
    canvas = Image.alpha_composite(canvas, shadow)
    canvas.alpha_composite(subject, (x, y))
    if style == "luxury_dark":
        vig = Image.new("L", canvas.size, 0)
        ImageDraw.Draw(vig).ellipse([-size // 4, -size // 4, size * 5 // 4, size * 5 // 4], fill=255)
        vig = vig.filter(ImageFilter.GaussianBlur(size // 8))
        dark = Image.new("RGBA", canvas.size, (0, 0, 0, 255))
        canvas = Image.composite(canvas, dark, vig)
    return canvas.convert("RGB")


def render_concept(name: str, category: str, features: List[str], style: str, size: int = 1024) -> Image.Image:
    """Text -> image offline: a designed concept card (not a photo) built from the facts."""
    top, bottom, ink = STYLE_PALETTES.get(style, STYLE_PALETTES["studio_white"])
    img = _gradient((size, size), top, bottom).convert("RGBA")
    over = Image.new("RGBA", img.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(over)
    od.ellipse([size * 0.55, -size * 0.15, size * 1.2, size * 0.5], fill=ink + (28,))
    od.ellipse([-size * 0.2, size * 0.6, size * 0.45, size * 1.25], fill=ink + (20,))
    img = Image.alpha_composite(img, over.filter(ImageFilter.GaussianBlur(30)))
    d = ImageDraw.Draw(img)
    cx, cy, r = size // 2, int(size * 0.36), int(size * 0.17)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=ink + (235,))
    initials = "".join(w[0] for w in name.split()[:2]).upper() or "?"
    f_big = _font(int(r * 0.9), bold=True)
    d.text((cx, cy), initials, font=f_big, fill=top, anchor="mm")
    f_title = _font(int(size * 0.055), bold=True)
    title = name if len(name) < 32 else name[:30] + "…"
    d.text((cx, int(size * 0.62)), title, font=f_title, fill=ink, anchor="mm")
    if category:
        d.text((cx, int(size * 0.69)), category.upper(), font=_font(int(size * 0.026)), fill=ink, anchor="mm")
    f_chip = _font(int(size * 0.024))
    y = int(size * 0.77)
    chips = [f[:28] for f in features[:3]]
    widths = [d.textlength(c, font=f_chip) + 40 for c in chips]
    x = cx - (sum(widths) + 14 * (len(chips) - 1)) / 2
    for c, wdt in zip(chips, widths):
        d.rounded_rectangle([x, y, x + wdt, y + size * 0.055], radius=size * 0.03, outline=ink, width=2)
        d.text((x + wdt / 2, y + size * 0.0275), c, font=f_chip, fill=ink, anchor="mm")
        x += wdt + 14
    d.text((cx, int(size * 0.94)), "Offline concept preview - add an API key for photoreal images",
           font=_font(int(size * 0.018)), fill=ink, anchor="mm")
    return img.convert("RGB")
