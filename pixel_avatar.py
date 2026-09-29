# -*- coding: utf-8 -*-
"""Pixel Art аватар — программная генерация без API.

Рисует 32x32 пиксельный аватар с 9 эмоциями.
Масштабируется до любого размера через Image.NEAREST (pixel-perfect).
Бесплатно, мгновенно, работает офлайн.
"""
import os
import json
import logging
from PIL import Image, ImageDraw

logger = logging.getLogger(__name__)

AVATAR_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models", "avatar")
SIZE = 32  # базовый размер в пикселях

EMOTIONS = ["neutral", "happy", "sad", "angry", "surprised", "thinking", "speaking", "wink", "sleeping"]

EMOTION_LABELS = {
    "neutral": "Нейтрально", "happy": "Радость", "sad": "Грусть",
    "angry": "Злость", "surprised": "Удивление", "thinking": "Задумчивость",
    "speaking": "Речь", "wink": "Подмигивание", "sleeping": "Сон",
}

PRESET_CHARACTERS = [
    {"name": "Рыжий", "slug": "pixel_orange", "skin": (255, 200, 150), "hair": (200, 100, 50), "eyes": (60, 120, 60), "shirt": (80, 120, 200)},
    {"name": "Блондин", "slug": "pixel_blonde", "skin": (255, 210, 180), "hair": (240, 200, 80), "eyes": (60, 90, 160), "shirt": (200, 80, 80)},
    {"name": "Тёмный", "slug": "pixel_dark", "skin": (180, 130, 100), "hair": (40, 30, 30), "eyes": (80, 60, 40), "shirt": (60, 140, 80)},
    {"name": "Розовый", "slug": "pixel_pink", "skin": (255, 200, 190), "hair": (255, 140, 180), "eyes": (180, 60, 120), "shirt": (200, 160, 220)},
    {"name": "Синий", "slug": "pixel_blue", "skin": (200, 200, 220), "hair": (60, 80, 180), "eyes": (40, 60, 140), "shirt": (100, 60, 160)},
    {"name": "Зелёный", "slug": "pixel_green", "skin": (220, 200, 170), "hair": (40, 120, 60), "eyes": (30, 100, 50), "shirt": (80, 160, 100)},
]


def get_avatar_dir():
    os.makedirs(AVATAR_DIR, exist_ok=True)
    return AVATAR_DIR


def get_preset(slug):
    for p in PRESET_CHARACTERS:
        if p["slug"] == slug:
            return p
    return PRESET_CHARACTERS[0]


def get_path(emotion, slug="pixel_orange"):
    """Get path using unified asset structure: models/avatar/{slug}/{emotion}.png"""
    import avatar_assets
    return avatar_assets.get_path(emotion, slug)


def is_cached(emotion, slug="pixel_orange"):
    import avatar_assets
    return avatar_assets.is_cached(emotion, slug)


def _draw_hair(draw, skin, hair, cx, cy):
    """Шапка волос."""
    draw.rectangle([cx - 7, cy - 10, cx + 7, cy - 5], fill=hair)
    draw.rectangle([cx - 8, cy - 9, cx - 6, cy - 3], fill=hair)
    draw.rectangle([cx + 6, cy - 9, cx + 8, cy - 3], fill=hair)
    draw.rectangle([cx - 6, cy - 11, cx + 6, cy - 8], fill=hair)


def _draw_eyes(draw, cx, cy, emotion, eye_color):
    """Глаза в зависимости от эмоции."""
    ly, ry = cy - 2, cy - 2
    lx, rx = cx - 3, cx + 3

    if emotion in ("happy", "wink"):
        # Дуги вверх (закрытые радостные)
        for dx in (-1, 0, 1):
            draw.point((lx + dx, ly - 1), fill=eye_color)
            draw.point((rx + dx, ry - 1), fill=eye_color)
    elif emotion == "sleeping":
        # Горизонтальные линии
        draw.line([lx - 1, ly, lx + 1, ly], fill=eye_color)
        draw.line([rx - 1, ry, rx + 1, ry], fill=eye_color)
    elif emotion == "wink":
        # Левый открыт, правый закрыт
        draw.rectangle([lx - 1, ly - 1, lx + 1, ly + 1], fill=eye_color)
        draw.line([rx - 1, ry, rx + 1, ry], fill=eye_color)
    elif emotion == "surprised":
        # Большие круглые
        draw.rectangle([lx - 2, ly - 2, lx + 2, ly + 2], fill=eye_color)
        draw.rectangle([rx - 2, ry - 2, rx + 2, ry + 2], fill=eye_color)
        draw.rectangle([lx - 1, ly - 1, lx + 1, ly + 1], fill=(255, 255, 255))
        draw.rectangle([rx - 1, ry - 1, rx + 1, ry + 1], fill=(255, 255, 255))
    else:
        # Обычные
        draw.rectangle([lx - 1, ly - 1, lx + 1, ly + 1], fill=eye_color)
        draw.rectangle([rx - 1, ry - 1, rx + 1, ry + 1], fill=eye_color)


def _draw_mouth(draw, cx, cy, emotion):
    """Рот в зависимости от эмоции."""
    my = cy + 4
    if emotion == "happy":
        draw.arc([cx - 3, my - 1, cx + 3, my + 3], 0, 180, fill=(200, 60, 60), width=1)
    elif emotion == "sad":
        draw.arc([cx - 3, my + 1, cx + 3, my + 5], 180, 360, fill=(150, 60, 60), width=1)
    elif emotion == "angry":
        draw.line([cx - 3, my + 2, cx + 3, my + 2], fill=(180, 40, 40), width=1)
        draw.line([cx - 2, my + 1, cx - 3, my], fill=(180, 40, 40))
        draw.line([cx + 2, my + 1, cx + 3, my], fill=(180, 40, 40))
    elif emotion == "surprised":
        draw.ellipse([cx - 2, my, cx + 2, my + 4], fill=(180, 50, 50))
    elif emotion == "speaking":
        draw.rectangle([cx - 2, my + 1, cx + 2, my + 3], fill=(180, 50, 50))
    elif emotion == "thinking":
        draw.line([cx - 1, my + 2, cx + 2, my + 1], fill=(160, 60, 60), width=1)
    elif emotion == "wink":
        draw.arc([cx - 3, my - 1, cx + 3, my + 3], 0, 180, fill=(200, 60, 60), width=1)
    else:
        draw.line([cx - 2, my + 2, cx + 2, my + 2], fill=(160, 80, 80), width=1)


def _draw_brows(draw, cx, cy, emotion, hair):
    """Брови."""
    by = cy - 5
    if emotion == "angry":
        draw.line([cx - 5, by + 1, cx - 2, by - 1], fill=hair, width=1)
        draw.line([cx + 2, by - 1, cx + 5, by + 1], fill=hair, width=1)
    elif emotion == "sad":
        draw.line([cx - 5, by - 1, cx - 2, by + 1], fill=hair, width=1)
        draw.line([cx + 2, by + 1, cx + 5, by - 1], fill=hair, width=1)
    elif emotion == "surprised":
        draw.line([cx - 5, by - 2, cx - 2, by - 2], fill=hair, width=1)
        draw.line([cx + 2, by - 2, cx + 5, by - 2], fill=hair, width=1)
    else:
        draw.line([cx - 5, by, cx - 2, by], fill=hair, width=1)
        draw.line([cx + 2, by, cx + 5, by], fill=hair, width=1)


def _draw_body(draw, cx, cy, shirt):
    """Тело/рубашка."""
    draw.rectangle([cx - 6, cy + 7, cx + 6, cy + 15], fill=shirt)
    draw.rectangle([cx - 4, cy + 7, cx + 4, cy + 9], fill=shirt)


def generate_emotion(emotion, slug="pixel_orange", seed=None):
    """Генерирует пиксельный аватар для одной эмоции. Возвращает путь к PNG."""
    dst = get_path(emotion, slug)
    if os.path.exists(dst):
        return dst

    # Get color preset — check custom avatars from avatar_assets
    preset = None
    try:
        import avatar_assets
        meta_path = os.path.join(avatar_assets.get_slug_dir(slug), "_meta.json")
        if os.path.exists(meta_path):
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
            if meta.get("generator") == "pixel_custom":
                # Load colors from extracted_colors
                colors = meta.get("extracted_colors", {})
                if colors:
                    preset = {
                        "skin": tuple(colors.get("skin", [255, 200, 150])),
                        "hair": tuple(colors.get("hair", [100, 70, 40])),
                        "eyes": tuple(colors.get("eyes", [60, 100, 140])),
                        "shirt": tuple(colors.get("shirt", [80, 120, 180])),
                    }
    except Exception:
        pass

    if preset is None:
        preset = get_preset(slug)

    skin = preset["skin"]
    hair = preset["hair"]
    eye_color = preset["eyes"]
    shirt = preset["shirt"]

    # Ensure parent directory exists
    os.makedirs(os.path.dirname(dst), exist_ok=True)

    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    cx, cy = SIZE // 2, SIZE // 2 + 2

    # Фон — круг
    draw.ellipse([cx - 13, cy - 13, cx + 13, cy + 13], fill=(40, 44, 52, 255))

    # Тело
    _draw_body(draw, cx, cy, shirt)

    # Голова
    draw.ellipse([cx - 7, cy - 8, cx + 7, cy + 6], fill=skin)

    # Волосы
    _draw_hair(draw, skin, hair, cx, cy)

    # Брови
    _draw_brows(draw, cx, cy, emotion, hair)

    # Глаза
    _draw_eyes(draw, cx, cy, emotion, eye_color)

    # Рот
    _draw_mouth(draw, cx, cy, emotion)

    # Спящий — ZZZ
    if emotion == "sleeping":
        draw.text((cx + 5, cy - 10), "z", fill=(200, 200, 200))
        draw.text((cx + 8, cy - 12), "z", fill=(180, 180, 180))

    # Удивлённый — восклицательный
    if emotion == "surprised":
        draw.rectangle([cx - 1, cy - 14, cx + 1, cy - 11], fill=(255, 255, 100))
        draw.rectangle([cx - 1, cy - 9, cx + 1, cy - 8], fill=(255, 255, 100))

    img.save(dst, "PNG")
    logger.debug(f"[PIXEL] Generated {emotion} for {slug}")
    return dst


def ensure_all(slug="pixel_orange", seed=None, on_done=None, on_progress=None):
    """Генерирует все 9 эмоций."""
    for em in EMOTIONS:
        try:
            if on_progress:
                on_progress(f"Drawing {em}...")
            generate_emotion(em, slug, seed)
            if on_done:
                on_done(em, None)
        except Exception as e:
            logger.error(f"[PIXEL] {em} failed: {e}")
            if on_done:
                on_done(em, e)


def load_pixmap(emotion, slug="pixel_orange", max_w=260, max_h=300):
    """Загружает пиксельный аватар и масштабирует через NEAREST (pixel-perfect)."""
    path = get_path(emotion, slug)
    if not os.path.exists(path):
        generate_emotion(emotion, slug)
    img = Image.open(path).convert("RGBA")
    ratio = min(max_w / img.width, max_h / img.height)
    new_w = max(1, int(img.width * ratio))
    new_h = max(1, int(img.height * ratio))
    img = img.resize((new_w, new_h), Image.NEAREST)
    return img
