# -*- coding: utf-8 -*-
"""Avatar Animation Builder — create, store, preview animated GIFs from any frames.

Features:
  - Unlimited frames from any source (emotion PNGs, custom photos, URLs)
  - Each frame replaceable with any image
  - Reorder, delete, duplicate frames
  - Export as animated GIF with adjustable FPS
  - Auto-save animation projects to models/animations/{name}/
"""
import os
import io
import json
import shutil
import logging
from pathlib import Path
from typing import List, Optional, Tuple

from PIL import Image

logger = logging.getLogger(__name__)

APP_DIR = os.path.dirname(os.path.abspath(__file__))
ANIMATIONS_DIR = os.path.join(APP_DIR, "models", "animations")


def ensure_dirs():
    os.makedirs(ANIMATIONS_DIR, exist_ok=True)


def get_anim_dir(name):
    ensure_dirs()
    d = os.path.join(ANIMATIONS_DIR, _sanitize(name))
    os.makedirs(d, exist_ok=True)
    return d


def _sanitize(name):
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name)[:50]


def list_animations():
    """List all saved animation projects."""
    ensure_dirs()
    result = []
    for entry in sorted(os.listdir(ANIMATIONS_DIR)):
        d = os.path.join(ANIMATIONS_DIR, entry)
        if not os.path.isdir(d):
            continue
        meta = _load_meta(entry)
        frames = _list_frames(entry)
        if frames:
            result.append({
                "name": meta.get("name", entry),
                "slug": entry,
                "frame_count": len(frames),
                "fps": meta.get("fps", 4),
                "loop": meta.get("loop", True),
            })
    return result


def _load_meta(name):
    d = os.path.join(ANIMATIONS_DIR, _sanitize(name))
    p = os.path.join(d, "_meta.json")
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_meta(name, meta):
    d = get_anim_dir(name)
    p = os.path.join(d, "_meta.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)


def _list_frames(name):
    d = os.path.join(ANIMATIONS_DIR, _sanitize(name))
    if not os.path.isdir(d):
        return []
    frames = []
    for f in sorted(os.listdir(d)):
        if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp")):
            frames.append(os.path.join(d, f))
    return frames


def create_animation(name, fps=4, loop=True):
    """Create a new empty animation project."""
    slug = _sanitize(name)
    _save_meta(slug, {"name": name, "fps": fps, "loop": loop, "frame_order": []})
    logger.info(f"[ANIM] Created animation: {slug}")
    return slug


def add_frame(name, image_path, index=-1):
    """Add a frame from an image file. Returns the frame path."""
    d = get_anim_dir(name)
    frames = _list_frames(name)
    idx = len(frames) if index < 0 else min(index, len(frames))
    ext = Path(image_path).suffix.lower() or ".png"
    frame_name = f"frame_{idx:04d}{ext}"
    dst = os.path.join(d, frame_name)

    img = Image.open(image_path).convert("RGBA")
    img.save(dst, "PNG")
    logger.info(f"[ANIM] Added frame {idx} to '{name}': {dst}")
    return dst


def add_frames_from_emotions(name, slug, emotions=None):
    """Add emotion frames from an avatar slug as animation frames."""
    import avatar_assets
    if emotions is None:
        emotions = avatar_assets.EMOTIONS
    count = 0
    for em in emotions:
        path = avatar_assets.get_path(em, slug)
        if os.path.exists(path):
            add_frame(name, path)
            count += 1
    logger.info(f"[ANIM] Added {count} emotion frames from '{slug}' to '{name}'")
    return count


def add_frames_from_gif(name, gif_path):
    """Extract all frames from an existing GIF and add them."""
    img = Image.open(gif_path)
    count = 0
    try:
        while True:
            frame = img.copy().convert("RGBA")
            d = get_anim_dir(name)
            frames = _list_frames(name)
            idx = len(frames)
            dst = os.path.join(d, f"frame_{idx:04d}.png")
            frame.save(dst, "PNG")
            count += 1
            img.seek(img.tell() + 1)
    except EOFError:
        pass
    logger.info(f"[ANIM] Extracted {count} frames from GIF: {gif_path}")
    return count


def add_frames_from_url(name, url, prefix="url"):
    """Download an image from URL and add as frame."""
    import urllib.request
    data = urllib.request.urlopen(url, timeout=30).read()
    img = Image.open(io.BytesIO(data)).convert("RGBA")
    d = get_anim_dir(name)
    frames = _list_frames(name)
    idx = len(frames)
    dst = os.path.join(d, f"frame_{idx:04d}.png")
    img.save(dst, "PNG")
    logger.info(f"[ANIM] Added URL frame {idx} to '{name}'")
    return dst


def replace_frame(frame_path, new_image_path):
    """Replace a frame with a new image (same position, any source)."""
    img = Image.open(new_image_path).convert("RGBA")
    # Keep original size
    orig = Image.open(frame_path)
    img = img.resize(orig.size, Image.LANCZOS)
    img.save(frame_path, "PNG")
    logger.info(f"[ANIM] Replaced frame: {frame_path}")


def remove_frame(frame_path):
    """Delete a single frame."""
    if os.path.exists(frame_path):
        os.remove(frame_path)
        logger.info(f"[ANIM] Removed frame: {frame_path}")


def move_frame(frame_path, new_index):
    """Move a frame to a new position (reindex all frames)."""
    d = os.path.dirname(frame_path)
    frames = sorted([f for f in os.listdir(d)
                     if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp"))])
    if frame_path not in frames:
        return
    frames.remove(frame_path)
    frame_file = os.path.basename(frame_path)
    ext = Path(frame_file).suffix
    insert_at = min(new_index, len(frames))
    frames.insert(insert_at, f"__temp__{ext}")
    # Rename all
    for i, f in enumerate(frames):
        new_name = f"frame_{i:04d}{Path(f).suffix if not f.startswith('__temp__') else ext}"
        old = os.path.join(d, f)
        new = os.path.join(d, new_name)
        if old != new and os.path.exists(old):
            os.rename(old, new)


def duplicate_frame(frame_path):
    """Duplicate a frame (insert after it)."""
    d = os.path.dirname(frame_path)
    frames = sorted([f for f in os.listdir(d)
                     if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp"))])
    idx = frames.index(os.path.basename(frame_path)) if os.path.basename(frame_path) in frames else len(frames)
    ext = Path(frame_path).suffix
    new_name = f"frame_{idx + 1:04d}{ext}"
    dst = os.path.join(d, new_name)
    shutil.copy2(frame_path, dst)
    # Renumber all after
    move_frame(dst, idx + 1)
    logger.info(f"[ANIM] Duplicated frame: {frame_path}")
    return dst


def set_fps(name, fps):
    meta = _load_meta(name)
    meta["fps"] = max(1, min(30, fps))
    _save_meta(name, meta)


def set_loop(name, loop):
    meta = _load_meta(name)
    meta["loop"] = loop
    _save_meta(name, meta)


def export_gif(name, output_path=None, fps=None, loop=True, size=None):
    """Export animation as animated GIF.
    
    Args:
        name: animation project name
        output_path: where to save (default: models/animations/{name}/output.gif)
        fps: override FPS (default: from meta)
        loop: loop count (0=infinite, 1=once)
        size: (width, height) to resize all frames
    Returns:
        path to saved GIF
    """
    meta = _load_meta(name)
    if fps is None:
        fps = meta.get("fps", 4)
    if loop is None:
        loop = meta.get("loop", True)

    frames_raw = _list_frames(name)
    if not frames_raw:
        raise ValueError(f"No frames in animation '{name}'")

    frames = []
    for fp in frames_raw:
        img = Image.open(fp).convert("RGBA")
        # Composite on white background for GIF
        bg = Image.new("RGB", img.size, (30, 30, 40))
        bg.paste(img, mask=img.split()[3])
        if size:
            bg = bg.resize(size, Image.LANCZOS)
        frames.append(bg)

    if output_path is None:
        output_path = os.path.join(get_anim_dir(name), "output.gif")

    duration_ms = max(20, int(1000 / fps))
    loop_count = 0 if loop else 1

    frames[0].save(
        output_path,
        save_all=True,
        append_images=frames[1:],
        duration=duration_ms,
        loop=loop_count,
        optimize=True,
    )
    logger.info(f"[ANIM] Exported GIF: {output_path} ({len(frames)} frames, {fps} fps)")
    return output_path


def delete_animation(name):
    """Delete entire animation project."""
    d = os.path.join(ANIMATIONS_DIR, _sanitize(name))
    if os.path.isdir(d):
        shutil.rmtree(d)
        logger.info(f"[ANIM] Deleted animation: {name}")
        return True
    return False


def rename_animation(old_name, new_name):
    old = os.path.join(ANIMATIONS_DIR, _sanitize(old_name))
    new = os.path.join(ANIMATIONS_DIR, _sanitize(new_name))
    if os.path.isdir(old) and not os.path.isdir(new):
        os.rename(old, new)
        meta = _load_meta(_sanitize(new_name))
        meta["name"] = new_name
        _save_meta(_sanitize(new_name), meta)
        return True
    return False


def get_frame_thumbnail(frame_path, size=64):
    """Get a thumbnail of a frame for UI display."""
    img = Image.open(frame_path).convert("RGBA")
    img.thumbnail((size, size), Image.LANCZOS)
    return img
