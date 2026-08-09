import os
import glob
import textwrap
import threading
from PIL import Image, ImageDraw, ImageFont

# rembg sessions load an ONNX model from disk; this used to happen on every
# single call to generate_thumbnail (including every live-preview tick while
# dragging a slider), which is the main reason thumbnail editing felt slow.
# Cached here per model name and reused across calls.
_REMBG_SESSIONS = {}
_REMBG_LOCK = threading.Lock()


def _get_rembg_session(model_name):
    if model_name not in _REMBG_SESSIONS:
        with _REMBG_LOCK:
            if model_name not in _REMBG_SESSIONS:
                from rembg import new_session
                _REMBG_SESSIONS[model_name] = new_session(model_name)
    return _REMBG_SESSIONS[model_name]


def generate_thumbnail(product_image_path, title_text, output_path, bg_folder=None,
                       bg_path=None, bg_overlay_color=(0,0,0), bg_overlay_opacity=0.4,
                       text_colors=[(255, 255, 0), (255, 255, 255)],
                       text_bg_color=(0, 0, 0), text_bg_opacity=0.9,
                       glow_color=(119, 190, 240), glow_radius_mult=1.0, glow_opacity=0.8,
                       product_x_offset=0, product_y_offset=0, product_scale_mult=1.0,
                       font_name=None, remove_bg=True, model_name="birefnet-general",
                       font_size_mult=1.0, wrap_width_override=None, line_spacing_mult=1.0,
                       text_stroke_color=None, text_stroke_width=0,
                       text_shadow_color=None, text_shadow_offset=4,
                       box_radius=10, box_padding_mult=1.0, output_quality=95):
    """
    Generates a YouTube thumbnail with custom settings.

    New in this version (all optional, default to the previous look):
      font_size_mult      - scale the base font size (110/140px) up or down
      wrap_width_override - force a specific text-wrap character width
      line_spacing_mult    - scale the gap between wrapped lines
      text_stroke_color/_width - outline around the title text (readability)
      text_shadow_color/_offset - drop shadow behind the title text
      box_radius           - corner radius of the text background box
      box_padding_mult     - scale the text background box's padding
      output_quality       - JPEG save quality (1-100)
    """
    # Detect Shorts mode based on background folder name
    is_shorts = False
    if bg_folder and ("shorts_bg_img" in bg_folder.lower()):
        is_shorts = True

    # Use 1080x1920 for Shorts, otherwise 1280x720
    canvas_w, canvas_h = (1080, 1920) if is_shorts else (1280, 720)

    # Convert hex to RGB if needed
    def parse_color(c):
        if isinstance(c, str):
            c = c.lstrip('#')
            return tuple(int(c[i:i+2], 16) for i in (0, 2, 4))
        return c

    bg_overlay_color = parse_color(bg_overlay_color)
    text_colors = [parse_color(c) for c in text_colors]
    glow_color = parse_color(glow_color)
    text_bg_color = parse_color(text_bg_color)
    text_stroke_color = parse_color(text_stroke_color) if text_stroke_color else None
    text_shadow_color = parse_color(text_shadow_color) if text_shadow_color else None

    # Convert opacity to 0-255
    def to_255(v):
        if v <= 1.0: return int(v * 255)
        return int(v)

    bg_overlay_opacity = to_255(bg_overlay_opacity)
    text_bg_opacity = to_255(text_bg_opacity)

    print(f"\n[THUMBNAIL] Generating thumbnail for: {title_text}")
    
    # 1. Background Logic
    if bg_path and os.path.exists(bg_path):
        bg_image = Image.open(bg_path).resize((canvas_w, canvas_h)).convert('RGBA')
    elif not bg_folder or not os.path.exists(bg_folder):
        bg_image = Image.new('RGBA', (canvas_w, canvas_h), (30, 30, 30, 255))
    else:
        bg_files = glob.glob(os.path.join(bg_folder, '*.jpg')) + glob.glob(os.path.join(bg_folder, '*.png'))
        if not bg_files:
            bg_image = Image.new('RGBA', (canvas_w, canvas_h), (30, 30, 30, 255))
        else:
            import random
            bg_file = random.choice(bg_files)
            bg_image = Image.open(bg_file).resize((canvas_w, canvas_h)).convert('RGBA')

    # Apply Background Overlay
    overlay = Image.new('RGBA', (canvas_w, canvas_h), (*bg_overlay_color, bg_overlay_opacity))
    bg_image = Image.alpha_composite(bg_image, overlay)

    if not product_image_path or not os.path.exists(product_image_path):
        print(f"[ERROR] Product image not found: {product_image_path}")
        # Create a blank transparent image if no product found to avoid crash
        product_no_bg = Image.new('RGBA', (100, 100), (0,0,0,0))
    else:
        # 2. Product Image Processing (Remove BG if needed)
        print("[THUMBNAIL] Processing product image...")
        product_img = Image.open(product_image_path).convert("RGBA")
        
        # Check if we should remove background (if not already transparent).
        # Uses the extrema of the alpha channel (min/max in one C call) to see
        # if there's any non-opaque pixel at all, which is enough to decide
        # "already has transparency" -- avoids iterating every pixel in pure
        # Python (which was slow on large product photos).
        is_transparent = False
        if product_img.mode == 'RGBA':
            alpha_min, alpha_max = product_img.getchannel('A').getextrema()
            if alpha_min < 250:
                is_transparent = True

        if remove_bg and not is_transparent:
            print(f"[THUMBNAIL] Removing background using {model_name}...")
            try:
                from rembg import remove
                session = _get_rembg_session(model_name)
                if product_img.width > 1200 or product_img.height > 1200:
                    product_img.thumbnail((1200, 1200), Image.Resampling.LANCZOS)

                # Use selected model without alpha matting to avoid over-removing
                product_no_bg = remove(
                    product_img,
                    session=session,
                    alpha_matting=False
                )
            except Exception as e:
                print(f"[WARNING] Background removal failed: {e}")
                product_no_bg = product_img
        else:
            product_no_bg = product_img

    # Crop and Resize product
    bbox = product_no_bg.getbbox()
    if bbox:
        product_no_bg = product_no_bg.crop(bbox)
    
    if product_no_bg.width > product_no_bg.height:
        base_width = 800 if is_shorts else 580
        new_width = int(base_width * product_scale_mult)
        new_height = int(product_no_bg.height * new_width / product_no_bg.width)
    else:
        base_height = 1000 if is_shorts else 630
        new_height = int(base_height * product_scale_mult)
        new_width = int(product_no_bg.width * new_height / product_no_bg.height)
    product_no_bg = product_no_bg.resize((new_width, new_height), Image.Resampling.LANCZOS)

    # 3. Composite: Product Layout
    margin = 35
    if is_shorts:
        # Centered for shorts mode
        x_img = (canvas_w - product_no_bg.width) // 2 + int(product_x_offset)
        y_img = (canvas_h - product_no_bg.height) // 2 + 100 + int(product_y_offset)
    else:
        # Right aligned for landscape mode
        x_img = canvas_w - product_no_bg.width - margin + int(product_x_offset)
        y_img = (canvas_h - product_no_bg.height) // 2 + int(product_y_offset)

    # 4. Product Glow
    gradient_img = Image.new('RGBA', (canvas_w, canvas_h), (0, 0, 0, 0))
    g_draw = ImageDraw.Draw(gradient_img)
    center_x = x_img + new_width // 2
    center_y = y_img + new_height // 2
    max_radius = int(max(new_width, new_height) * glow_radius_mult)
    
    # Peak alpha based on glow_opacity (0.0 to 1.0)
    peak_alpha = int(255 * glow_opacity)

    for r in range(max_radius, 0, -5):
        alpha = int(peak_alpha * (1 - r / max_radius))
        g_draw.ellipse([center_x-r, center_y-r, center_x+r, center_y+r], fill=(*glow_color, alpha))
    
    bg_image = Image.alpha_composite(bg_image, gradient_img)
    bg_image.paste(product_no_bg, (x_img, y_img), product_no_bg)

    # 5. Draw Title Text
    text_bg_layer = Image.new('RGBA', (canvas_w, canvas_h), (0, 0, 0, 0))
    draw_bg = ImageDraw.Draw(text_bg_layer)
    draw_text = ImageDraw.Draw(bg_image)
    
    script_dir = os.path.dirname(__file__)
    font_paths = []
    if font_name:
        if os.path.isabs(font_name):
            font_paths.append(font_name)
        else:
            font_paths.append(os.path.join(script_dir, font_name))
            font_paths.append(font_name)

    font_paths += [
        os.path.join(script_dir, "Roboto-Bold.ttf"),
        os.path.join(script_dir, "arialbd.ttf"),
        "C:\\Windows\\Fonts\\arialbd.ttf",
        "C:\\Windows\\Fonts\\segoeuib.ttf",
        # macOS fallbacks -- previously only Windows paths were tried, so on a
        # Mac this loop fell straight through to the tiny PIL default font
        # unless a bundled .ttf resolved first.
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Supplemental/Helvetica.ttc",
        "/System/Library/Fonts/HelveticaNeue.ttc",
        "Roboto-Bold.ttf",
        "arial.ttf"
    ]
    font = None
    font_size = int((140 if is_shorts else 110) * font_size_mult)
    for fp in font_paths:
        try:
            font = ImageFont.truetype(fp, font_size)
            break
        except: continue

    if not font:
        font = ImageFont.load_default()

    clean_title = (title_text.split('|')[0].strip().upper()) or "UNTITLED"
    wrap_w = wrap_width_override if wrap_width_override else (12 if is_shorts else 10)
    wrapped_lines = textwrap.wrap(clean_title, width=wrap_w)
    if not wrapped_lines:
        wrapped_lines = [clean_title]
    if len(wrapped_lines) < 2 and not is_shorts and not wrap_width_override:
        wrapped_lines = textwrap.wrap(clean_title, width=8) or wrapped_lines
    # BUGFIX: `if len(wrapped_lines) > 6 if is_shorts else 5:` parses as
    # `(len(wrapped_lines) > 6) if is_shorts else 5`, so the landscape branch
    # was just the truthy constant 5 -- the max-lines cap silently never
    # applied outside Shorts mode.
    max_lines = 6 if is_shorts else 5
    if len(wrapped_lines) > max_lines:
        wrapped_lines = wrapped_lines[:max_lines]

    line_heights = []
    for line in wrapped_lines:
        bbox = draw_text.textbbox((0, 0), line, font=font)
        line_heights.append(bbox[3] - bbox[1])
    spacing = int((50 if is_shorts else 40) * line_spacing_mult)
    total_height = sum(line_heights) + spacing * (len(wrapped_lines) - 1)

    if is_shorts:
        # BUGFIX: wrapped_lines[0] with no guard raised IndexError (HTTP 500)
        # whenever the cleaned title was empty; the "UNTITLED" fallback above
        # keeps this indexable.
        x_text = (canvas_w - (draw_text.textbbox((0, 0), wrapped_lines[0], font=font)[2])) // 2
        y_start = 200 # Higher up for shorts
    else:
        x_text = 40
        y_start = (canvas_h - total_height) // 2 - 30

    current_y = y_start
    padding_h = int((30 if is_shorts else 20) * box_padding_mult)
    padding_v = int((20 if is_shorts else 15) * box_padding_mult)
    for i, line in enumerate(wrapped_lines):
        t_bbox = draw_text.textbbox((x_text, current_y), line, font=font)
        if is_shorts:
            # Re-center each line's background box
            line_w = t_bbox[2] - t_bbox[0]
            line_x = (canvas_w - line_w) // 2
            t_bbox = (line_x, current_y, line_x + line_w, current_y + (t_bbox[3]-t_bbox[1]))

        bg_bbox = [t_bbox[0] - padding_h, t_bbox[1] - padding_v, t_bbox[2] + padding_h, t_bbox[3] + padding_v]
        draw_bg.rounded_rectangle(bg_bbox, radius=max(0, box_radius), fill=(*text_bg_color, text_bg_opacity))
        current_y += line_heights[i] + spacing

    bg_image = Image.alpha_composite(bg_image, text_bg_layer)

    draw_final = ImageDraw.Draw(bg_image)
    current_y = y_start
    for i, line in enumerate(wrapped_lines):
        color = text_colors[i % len(text_colors)]

        draw_x = x_text
        if is_shorts:
            line_w = draw_final.textbbox((0, 0), line, font=font)[2]
            draw_x = (canvas_w - line_w) // 2

        # Drop shadow first (behind everything), then stroke+fill on top --
        # these were the two biggest readability wins missing from the
        # original renderer (plain flat text with no separation from busy
        # backgrounds).
        if text_shadow_color and text_shadow_offset:
            draw_final.text((draw_x + text_shadow_offset, current_y + text_shadow_offset),
                             line, font=font, fill=text_shadow_color)
        if text_stroke_color and text_stroke_width > 0:
            draw_final.text((draw_x, current_y), line, font=font, fill=color,
                             stroke_width=text_stroke_width, stroke_fill=text_stroke_color)
        else:
            draw_final.text((draw_x, current_y), line, font=font, fill=color)
        current_y += line_heights[i] + spacing

    # 6. Save final image
    final_img = bg_image.convert('RGB')
    final_img.save(output_path, quality=max(1, min(100, int(output_quality))))
    print(f"[THUMBNAIL] Saved: {output_path}")
    return True

if __name__ == "__main__":
    # Test
    # generate_thumbnail("test_product.jpg", "BEST RUNNING SHOES 2026", "test_thumb.jpg")
    pass
