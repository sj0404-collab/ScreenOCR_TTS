# -*- coding: utf-8 -*-
"""Тест GlensOCR на текущем экране."""
import sys, time
sys.path.insert(0, '.')

from PIL import Image
import mss

# Захват экрана
print("[1/4] Захват экрана...")
with mss.mss() as sct:
    mon = sct.monitors[1]
    screenshot = sct.grab(mon)
    img = Image.frombytes("RGB", screenshot.size, screenshot.bgra, "raw", "BGRX")
    img.save('glens_test_screen.png')
print(f"    Экран: {img.size[0]}x{img.size[1]}")

# Инициализация GlensOCR
print("[2/4] Инициализация GlensOCR...")
from glens_ocr import GlensOCR
glens = GlensOCR()
print("    GlensOCR готов")

# Распознавание
print("[3/4] Распознавание текста...")
t0 = time.time()
try:
    text = glens.recognize(img)
    dt = time.time() - t0
    print(f"    Время: {dt:.2f}s")
    print(f"    Длина текста: {len(text)} символов")
except Exception as e:
    dt = time.time() - t0
    print(f"    ОШИБКА: {e}")
    print(f"    Время до ошибки: {dt:.2f}s")
    text = ""

# Результат
print("[4/4] Результат:")
print("=" * 60)
if text:
    print(text[:3000])
else:
    print("(пустой результат)")
print("=" * 60)

# Сохранение в файл
with open('glens_test_result.txt', 'w', encoding='utf-8') as f:
    f.write(f'Time: {dt:.2f}s\n')
    f.write(f'Text ({len(text)} chars):\n')
    f.write(text if text else "(пусто)\n")
print("\nРезультат сохранён в glens_test_result.txt")
