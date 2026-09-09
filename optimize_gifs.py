# -*- coding: utf-8 -*-
"""Оптимизация GIF: меньше размер, та же плавность."""
import os
from PIL import Image

import avatar_animation as aa

EMOTIONS = ["neutral", "happy", "sad", "angry", "surprised", "thinking", "speaking", "wink", "sleeping"]

for em in EMOTIONS:
    anim_dir = aa.get_anim_dir(em)
    gif_path = os.path.join(anim_dir, "output.gif")
    if not os.path.exists(gif_path):
        continue
    
    # Открываем и пересохраняем с оптимизацией
    img = Image.open(gif_path)
    
    # Сохраняем все кадры
    frames = []
    durations = []
    try:
        while True:
            frames.append(img.copy())
            durations.append(img.info.get('duration', 83))  # ~12fps
            img.seek(img.tell() + 1)
    except EOFError:
        pass
    
    # Оптимизированное сохранение
    frames[0].save(
        gif_path,
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=0,
        optimize=True,
        colors=128,           # палитра 128 цветов вместо 256
        disposal=2,           # restore to background
        transparency=0,
    )
    
    size_kb = os.path.getsize(gif_path) // 1024
    print(f"{em:12s} -> {size_kb} KB ({len(frames)} frames)")

print("\nDone.")
