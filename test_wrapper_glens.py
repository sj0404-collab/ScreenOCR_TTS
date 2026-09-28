# -*- coding: utf-8 -*-
"""Тест OCRWrapper с GlensOCR как основным движком."""
import sys, time, json
sys.path.insert(0, '.')

from PIL import Image
import mss

# Захват экрана
print("[1/5] Захват экрана...")
with mss.mss() as sct:
    mon = sct.monitors[1]
    screenshot = sct.grab(mon)
    img = Image.frombytes("RGB", screenshot.size, screenshot.bgra, "raw", "BGRX")
print(f"    Экран: {img.size[0]}x{img.size[1]}")

# Загрузка настроек
print("[2/5] Загрузка настроек...")
from settings import Settings
s = Settings()
print(f"    engine: {s.get('ocr.engine')}")

# Инициализация OCRWrapper
print("[3/5] Инициализация OCRWrapper...")
from ocr_wrapper import OCRWrapper
ocr = OCRWrapper(s)
print(f"    Активный движок: {ocr._active_engine_id}")

# Распознавание
print("[4/5] Распознавание...")
t0 = time.time()
text, conf = ocr.recognize(img)
dt = time.time() - t0
print(f"    Время: {dt:.2f}s")
print(f"    Confidence: {conf:.1f}%")
print(f"    Длина текста: {len(text)} символов")

# Результат
print("[5/5] Результат:")
print("=" * 60)
if text:
    print(text[:3000])
else:
    print("(пустой результат)")
print("=" * 60)

with open('wrapper_test_result.txt', 'w', encoding='utf-8') as f:
    f.write(f'Engine: {ocr._active_engine_id}\n')
    f.write(f'Time: {dt:.2f}s\n')
    f.write(f'Confidence: {conf:.1f}%\n')
    f.write(f'Text ({len(text)} chars):\n')
    f.write(text if text else "(пусто)\n")
print("\nРезультат сохранён в wrapper_test_result.txt")
