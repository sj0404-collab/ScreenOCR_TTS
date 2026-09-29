# -*- coding: utf-8 -*-
"""Unified avatar asset manager.

Storage: models/avatar/{slug}/{emotion}.png
Once generated, assets persist forever — no re-generation needed.

Supports:
  - Preset characters (pixel, pollinations, replicate)
  - Custom upload: user provides reference image → colors extracted → pixel art
  - Custom upload: user provides reference image → used as style for pollinations/replicate
"""
import os
import json
import shutil
import logging
from pathlib import Path
from collections import Counter

from PIL import Image

logger = logging.getLogger(__name__)

APP_DIR = os.path.dirname(os.path.abspath(__file__))
ASSETS_DIR = os.path.join(APP_DIR, "models", "avatar")
CUSTOM_DIR = os.path.join(ASSETS_DIR, "custom")
CONFIG_PATH = os.path.join(APP_DIR, "config.json")

EMOTIONS = ["neutral", "happy", "sad", "angry", "surprised", "thinking", "speaking", "wink", "sleeping"]
EMOTION_LABELS = {
    "neutral": "Нейтрально", "happy": "Радость", "sad": "Грусть",
    "angry": "Злость", "surprised": "Удивление", "thinking": "Задумчивость",
    "speaking": "Речь", "wink": "Подмигивание", "sleeping": "Сон",
}


def ensure_dirs():
    os.makedirs(ASSETS_DIR, exist_ok=True)
    os.makedirs(CUSTOM_DIR, exist_ok=True)


def get_slug_dir(slug):
    """Return path to slug folder (creates if needed)."""
    ensure_dirs()
    d = os.path.join(ASSETS_DIR, slug)
    os.makedirs(d, exist_ok=True)
    return d


def get_path(emotion, slug="default"):
    """Get path to emotion PNG for a given slug."""
    d = get_slug_dir(slug)
    return os.path.join(d, f"{emotion}.png")


def is_cached(emotion, slug="default"):
    return os.path.exists(get_path(emotion, slug))


def is_all_cached(slug="default"):
    return all(is_cached(e, slug) for e in EMOTIONS)


def get_asset_count(slug="default"):
    return sum(1 for e in EMOTIONS if is_cached(e, slug))


def list_all_avatars():
    """List all avatar slugs with metadata."""
    ensure_dirs()
    avatars = []
    for entry in sorted(os.listdir(ASSETS_DIR)):
        d = os.path.join(ASSETS_DIR, entry)
        if not os.path.isdir(d):
            continue
        count = sum(1 for e in EMOTIONS if os.path.exists(os.path.join(d, f"{e}.png")))
        if count == 0:
            continue
        meta_path = os.path.join(d, "_meta.json")
        meta = {}
        if os.path.exists(meta_path):
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
            except Exception:
                pass
        avatars.append({
            "slug": entry,
            "name": meta.get("name", entry),
            "generator": meta.get("generator", "unknown"),
            "count": count,
            "total": len(EMOTIONS),
            "has_ref": os.path.exists(os.path.join(d, "_reference.png")),
        })
    return avatars


def save_meta(slug, name=None, generator=None, ref_path=None):
    """Save metadata for an avatar slug."""
    d = get_slug_dir(slug)
    meta_path = os.path.join(d, "_meta.json")
    meta = {}
    if os.path.exists(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            pass
    if name:
        meta["name"] = name
    if generator:
        meta["generator"] = generator
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)
    if ref_path and os.path.exists(ref_path):
        dst = os.path.join(d, "_reference.png")
        shutil.copy2(ref_path, dst)
        logger.info(f"[ASSETS] Reference image saved: {dst}")


def delete_avatar(slug):
    """Delete all assets for a slug."""
    d = os.path.join(ASSETS_DIR, slug)
    if os.path.isdir(d):
        shutil.rmtree(d)
        logger.info(f"[ASSETS] Deleted avatar: {slug}")
        return True
    return False


def rename_avatar(slug, new_slug):
    """Rename a slug folder."""
    old = os.path.join(ASSETS_DIR, slug)
    new = os.path.join(ASSETS_DIR, new_slug)
    if os.path.isdir(old) and not os.path.isdir(new):
        os.rename(old, new)
        logger.info(f"[ASSETS] Renamed: {slug} -> {new_slug}")
        return True
    return False


def extract_colors_from_image(image_path, n_colors=4):
    """Extract dominant colors from a reference image.
    
    Returns dict with keys: skin, hair, eyes, shirt
    Uses k-means-like clustering via color counting.
    """
    try:
        img = Image.open(image_path).convert("RGB")
        img_small = img.resize((100, 100), Image.LANCZOS)
        pixels = list(img_small.getdata())

        # Count most common colors, excluding near-white and near-black
        filtered = []
        for r, g, b in pixels:
            brightness = (r + g + b) / 3
            if brightness < 30 or brightness > 225:
                continue
            # Quantize to reduce noise
            rq = (r // 16) * 16
            gq = (g // 16) * 16
            bq = (b // 16) * 16
            filtered.append((rq, gq, bq))

        if not filtered:
            # Fallback colors
            return {
                "skin": (255, 200, 150),
                "hair": (100, 70, 40),
                "eyes": (60, 100, 140),
                "shirt": (80, 120, 180),
            }

        counter = Counter(filtered)
        top_colors = [c for c, _ in counter.most_common(20)]

        # Sort by brightness to assign roles
        by_brightness = sorted(top_colors, key=lambda c: sum(c))
        
        # Heuristic: darkest = hair, brightest warm = skin, middle = shirt, dark cool = eyes
        skin_candidates = [c for c in top_colors if c[0] > 180 and c[1] > 120]
        hair_candidates = [c for c in top_colors if sum(c) < 350 and c not in skin_candidates]
        shirt_candidates = [c for c in top_colors if c not in skin_candidates and c not in hair_candidates]
        
        skin = skin_candidates[0] if skin_candidates else (255, 200, 150)
        hair = hair_candidates[0] if hair_candidates else (100, 70, 40)
        eyes = (40, 80, 120)  # Default blue-ish
        shirt = shirt_candidates[0] if shirt_candidates else (80, 120, 180)

        logger.info(f"[ASSETS] Extracted colors: skin={skin}, hair={hair}, eyes={eyes}, shirt={shirt}")
        return {"skin": skin, "hair": hair, "eyes": eyes, "shirt": shirt}
    except Exception as e:
        logger.error(f"[ASSETS] Color extraction failed: {e}")
        return {
            "skin": (255, 200, 150),
            "hair": (100, 70, 40),
            "eyes": (60, 100, 140),
            "shirt": (80, 120, 180),
        }


def create_custom_from_image(image_path, name=None):
    """Create a custom pixel-art avatar from a reference image.
    
    1. Extract colors
    2. Generate pixel art emotions with those colors
    3. Save to assets
    """
    slug = f"custom_{Path(image_path).stem}"
    # Sanitize slug
    slug = "".join(c if c.isalnum() or c in "-_" else "_" for c in slug)[:40]
    
    colors = extract_colors_from_image(image_path)
    save_meta(slug, name=name or slug, generator="pixel_custom", ref_path=image_path)
    
    import pixel_avatar as px
    
    # Override get_preset for this slug
    custom_preset = {
        "name": name or slug,
        "slug": slug,
        "skin": colors["skin"],
        "hair": colors["hair"],
        "eyes": colors["eyes"],
        "shirt": colors["shirt"],
    }
    
    # Add to presets temporarily
    if not any(p["slug"] == slug for p in px.PRESET_CHARACTERS):
        px.PRESET_CHARACTERS.append(custom_preset)
    
    return slug, colors


def get_reference_path(slug):
    """Get path to reference image for a slug."""
    d = os.path.join(ASSETS_DIR, slug)
    ref = os.path.join(d, "_reference.png")
    return ref if os.path.exists(ref) else None
