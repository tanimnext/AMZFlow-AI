"""Generates the "Check Price" bottom-right CTA badge overlaid on every
product segment. Badge design (color scheme) is picked deterministically per
product so consecutive products in one video don't all show the same badge,
without needing any extra state threaded through the render pipeline.
"""
import hashlib
import os

from PIL import Image, ImageDraw, ImageFont

BADGE_W, BADGE_H = 480, 200

_VARIANTS = [
    {  # red
        "bg": (211, 32, 39, 255),
        "bg2": (150, 18, 24, 255),
        "text": (255, 255, 255, 255),
        "accent": (255, 214, 0, 255),
    },
    {  # blue
        "bg": (17, 92, 217, 255),
        "bg2": (9, 52, 130, 255),
        "text": (255, 255, 255, 255),
        "accent": (0, 224, 255, 255),
    },
    {  # black / gold
        "bg": (24, 24, 24, 255),
        "bg2": (0, 0, 0, 255),
        "text": (255, 255, 255, 255),
        "accent": (255, 191, 0, 255),
    },
]


def _variant_for(seed_text):
    digest = hashlib.sha1(str(seed_text or "").encode("utf-8")).hexdigest()
    return _VARIANTS[int(digest, 16) % len(_VARIANTS)]


def _rounded_gradient(w, h, radius, top, bottom):
    base = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    grad = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    for y in range(h):
        t = y / max(h - 1, 1)
        row = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(4))
        for x in range(w):
            grad.putpixel((x, y), row)
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1], radius=radius, fill=255)
    base.paste(grad, (0, 0), mask)
    return base


def _load_font(font_path, size):
    try:
        return ImageFont.truetype(font_path, size)
    except Exception:
        return ImageFont.load_default()


def build_cta_badge(font_path, output_path, seed_text=""):
    """Renders a "CHECK PRICE" badge PNG (with alpha) to output_path and
    returns output_path, or None on failure. seed_text drives which of the
    color variants is used, so different products get a different badge."""
    try:
        variant = _variant_for(seed_text)
        img = _rounded_gradient(BADGE_W, BADGE_H, 22, variant["bg"], variant["bg2"])
        draw = ImageDraw.Draw(img)

        border_c = variant["accent"]
        draw.rounded_rectangle(
            [3, 3, BADGE_W - 4, BADGE_H - 4], radius=20, outline=border_c, width=4
        )

        title_font = _load_font(font_path, 58)
        sub_font = _load_font(font_path, 26)

        title = "CHECK PRICE"
        tb = draw.textbbox((0, 0), title, font=title_font)
        tw, th = tb[2] - tb[0], tb[3] - tb[1]
        tx = (BADGE_W - tw) / 2
        ty = 30
        draw.text((tx, ty), title, font=title_font, fill=variant["text"], stroke_width=2, stroke_fill=variant["bg2"])

        sub = "Product Link in Description"
        sb = draw.textbbox((0, 0), sub, font=sub_font)
        sw = sb[2] - sb[0]
        sx = (BADGE_W - sw) / 2
        sy = ty + th + 20
        draw.text((sx, sy), sub, font=sub_font, fill=variant["accent"])

        # Downward arrow beneath the subtitle
        ax, ay = BADGE_W / 2, sy + 40
        arrow_w, arrow_h = 26, 22
        draw.polygon(
            [
                (ax - arrow_w, ay),
                (ax + arrow_w, ay),
                (ax, ay + arrow_h),
            ],
            fill=variant["accent"],
        )

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        img.save(output_path, "PNG")
        return output_path
    except Exception as e:
        print(f"Error building CTA badge: {e}")
        return None
