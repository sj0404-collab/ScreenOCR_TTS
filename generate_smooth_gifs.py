# -*- coding: utf-8 -*-
"""Генерация плавных GIF-анимаций для всех эмоций аватара.

Создаёт 12 кадров на эмоцию с мягкими вариациями:
- дыхание (scale + vertical bob)
- лёгкий поворот головы
- моргание (для speaking, wink, sleeping)
- открытие рта (speaking)

Фон: мягкий градиент. Без резких сдвигов. Размер ~256x256.
"""
import os
import math
import logging
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter

import avatar_assets
import avatar_animation as aa

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

# Настройки
TARGET_W = 256
TARGET_H = 256
FRAMES = 12          # кадров на эмоцию (плавный цикл)
FPS = 12             # 12 fps = 1 секунда цикла
BG_COLOR_TOP = (0x6a, 0x8c, 0xb8)    # мягкий синий
BG_COLOR_BOTTOM = (0x4a, 0x6a, 0x8a) # темнее

EMOTIONS = avatar_assets.EMOTIONS
SLUG = "default"


def make_gradient_bg(w, h, top, bottom):
    """Вертикальный градиент."""
    img = Image.new("RGB", (w, h), top)
    draw = ImageDraw.Draw(img)
    for y in range(h):
        ratio = y / h
        r = int(top[0] * (1 - ratio) + bottom[0] * ratio)
        g = int(top[1] * (1 - ratio) + bottom[1] * ratio)
        b = int(top[2] * (1 - ratio) + bottom[2] * ratio)
        draw.line([(0, y), (w, y)], fill=(r, g, b))
    return img


def add_vignette(img, strength=0.35):
    """Виньетка по краям."""
    w, h = img.size
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for y in range(h):
        for x in range(w):
            dx = (x - w/2) / (w/2)
            dy = (y - h/2) / (h/2)
            dist = math.sqrt(dx*dx + dy*dy)
            alpha = int(255 * strength * max(0, dist - 0.5) * 2)
            if alpha > 0:
                draw.point((x, y), fill=(0, 0, 0, min(255, alpha)))
    return Image.alpha_composite(img.convert("RGBA"), overlay)


def transform_frame(base_img, emotion, frame_idx, total_frames):
    """Применяет мягкую трансформацию к кадру."""
    t = frame_idx / total_frames
    angle = 0.0
    scale = 1.0
    offset_x = 0.0
    offset_y = 0.0
    blink = False
    mouth_open = 0.0
    
    # Общее дыхание для всех эмоций
    breath = math.sin(t * 2 * math.pi) * 0.015  # ±1.5% scale
    bob = math.sin(t * 2 * math.pi) * 1.5       # ±1.5px vertical
    
    if emotion == "neutral":
        scale = 1.0 + breath
        offset_y = bob
        offset_x = math.sin(t * 2 * math.pi * 0.5) * 0.8
        
    elif emotion == "happy":
        # Прыгает от радости
        bounce = abs(math.sin(t * 2 * math.pi)) * 3.0
        scale = 1.0 + breath + 0.008 * math.sin(t * 4 * math.pi)
        offset_y = bob - bounce
        offset_x = math.sin(t * 2 * math.pi * 0.5) * 0.5
        
    elif emotion == "sad":
        # Опускается, слегка качается
        droop = abs(math.sin(t * 2 * math.pi)) * 2.0
        scale = 1.0 + breath - 0.006 * abs(math.sin(t * 2 * math.pi))
        offset_y = bob + droop
        angle = -1.5 * math.sin(t * 2 * math.pi)
        
    elif emotion == "angry":
        # Трясение + пульсация
        shake = math.sin(t * 10 * math.pi) * 1.2
        pulse = abs(math.sin(t * 5 * math.pi)) * 0.01
        scale = 1.0 + breath + pulse
        offset_x = shake
        offset_y = bob
        angle = 0.8 * math.sin(t * 7 * math.pi)
        
    elif emotion == "surprised":
        # Всплеск (pop)
        pop = 1.0 + 0.035 * abs(math.sin(t * 3 * math.pi))
        scale = 1.0 + breath
        offset_y = bob - 2.0 * abs(math.sin(t * 3 * math.pi))
        
    elif emotion == "thinking":
        # Медленный поворот головы вверх-вбок
        angle = 3.0 * math.sin(t * 2 * math.pi)
        offset_y = bob - 1.5 * math.sin(t * 2 * math.pi)
        offset_x = 1.2 * math.cos(t * 2 * math.pi)
        scale = 1.0 + breath
        
    elif emotion == "speaking":
        # Рот открывается/закрывается + лёгкое дыхание
        mouth_open = abs(math.sin(t * 4 * math.pi))  # 0..1
        scale = 1.0 + breath
        offset_y = bob + mouth_open * 1.5
        # Моргание раз в цикл
        blink = (0.45 < (t % 1.0) < 0.55)
        
    elif emotion == "wink":
        # Подмигивание
        blink = (0.3 < (t % 1.0) < 0.5)
        scale = 1.0 + breath
        offset_y = bob
        offset_x = math.sin(t * 2 * math.pi * 0.5) * 0.5
        
    elif emotion == "sleeping":
        # Медленный наклон, ZZZ не рисуем (сложно на пикселях)
        angle = 4.0 * math.sin(t * 2 * math.pi)
        offset_y = bob + 1.0 * math.sin(t * 2 * math.pi)
        offset_x = 0.8 * math.cos(t * 2 * math.pi)
        scale = 1.0 + breath
        # Глаза закрыты большую часть времени
        blink = (t % 1.0) > 0.15
    
    # Применяем трансформацию
    w, h = base_img.size
    
    # Масштаб
    new_w = max(1, int(w * scale))
    new_h = max(1, int(h * scale))
    scaled = base_img.resize((new_w, new_h), Image.LANCZOS)
    
    # Поворот (вокруг центра)
    if angle != 0:
        scaled = scaled.rotate(-angle, expand=True, fillcolor=(0, 0, 0, 0))
    
    # Создаём холст с фоном
    canvas = make_gradient_bg(TARGET_W, TARGET_H, BG_COLOR_TOP, BG_COLOR_BOTTOM)
    canvas = add_vignette(canvas, 0.25)
    
    # Накладываем аватар по центру со смещением
    cx = (TARGET_W - scaled.width) // 2 + int(offset_x)
    cy = (TARGET_H - scaled.height) // 2 + int(offset_y)
    canvas.paste(scaled, (cx, cy), scaled)
    
    # Моргание: накладываем тёмную полосу в области глаз
    if blink:
        draw = ImageDraw.Draw(canvas)
        # Примерная область глаз (верхняя треть аватара)
        eye_y = cy + int(scaled.height * 0.25)
        eye_h = max(2, int(scaled.height * 0.08))
        eye_x1 = cx + int(scaled.width * 0.25)
        eye_x2 = cx + int(scaled.width * 0.75)
        draw.rectangle([eye_x1, eye_y, eye_x2, eye_y + eye_h], fill=(0, 0, 0, 200))
    
    # Для speaking: дополнительное открытие рта (нижняя часть лица)
    if emotion == "speaking" and mouth_open > 0.3:
        draw = ImageDraw.Draw(canvas)
        mouth_y = cy + int(scaled.height * 0.65)
        mouth_h = int(4 * mouth_open)
        mouth_x1 = cx + int(scaled.width * 0.4)
        mouth_x2 = cx + int(scaled.width * 0.6)
        draw.ellipse([mouth_x1, mouth_y, mouth_x2, mouth_y + mouth_h], fill=(120, 40, 40, 255))
    
    return canvas


def generate_emotion_gif(emotion):
    """Генерирует GIF для одной эмоции."""
    base_path = avatar_assets.get_path(emotion, SLUG)
    if not os.path.exists(base_path):
        logger.warning(f"[{emotion}] Базовый PNG не найден: {base_path}")
        return None
    
    base_img = Image.open(base_path).convert("RGBA")
    
    # Масштабируем базу под целевой размер (с запасом для трансформаций)
    ratio = min(TARGET_W / base_img.width, TARGET_H / base_img.height) * 0.85
    base_w = max(1, int(base_img.width * ratio))
    base_h = max(1, int(base_img.height * ratio))
    base_img = base_img.resize((base_w, base_h), Image.LANCZOS)
    
    # Создаём проект анимации
    anim_name = f"{SLUG}_{emotion}"
    aa.create_animation(anim_name, fps=FPS, loop=True)
    
    frames_dir = aa.get_anim_dir(anim_name)
    
    for i in range(FRAMES):
        frame = transform_frame(base_img, emotion, i, FRAMES)
        frame_path = os.path.join(frames_dir, f"frame_{i:04d}.png")
        frame.save(frame_path, "PNG")
    
    # Экспорт в GIF
    output_path = os.path.join(frames_dir, "output.gif")
    aa.export_gif(anim_name, output_path=output_path, fps=FPS, loop=True, size=(TARGET_W, TARGET_H))
    
    logger.info(f"[{emotion}] Готово: {output_path} ({FRAMES} кадров, {FPS} fps)")
    return output_path


def main():
    logger.info(f"=== Генерация GIF для slug='{SLUG}' ===")
    logger.info(f"Размер: {TARGET_W}x{TARGET_H}, кадров: {FRAMES}, fps: {FPS}")
    
    results = {}
    for em in EMOTIONS:
        try:
            path = generate_emotion_gif(em)
            if path:
                results[em] = path
        except Exception as e:
            logger.error(f"[{em}] Ошибка: {e}")
    
    logger.info("\n=== Итог ===")
    for em, path in results.items():
        size_kb = os.path.getsize(path) // 1024
        logger.info(f"  {em:12s} -> {path} ({size_kb} KB)")
    
    return results

if __name__ == "__main__":
    main()
