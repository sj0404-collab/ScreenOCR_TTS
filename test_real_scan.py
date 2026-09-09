# -*- coding: utf-8 -*-
import sys, time
sys.path.insert(0, '.')

from PIL import Image
import mss

with mss.mss() as sct:
    mon = sct.monitors[1]
    screenshot = sct.grab(mon)
    img = Image.frombytes("RGB", screenshot.size, screenshot.bgra, "raw", "BGRX")
    img.save('scan_test_full.png')

from tflite_ocr import CyrillicTfliteOCR
from ocr_text_cleaner import fix_lookalikes_per_word, looks_like_dictionary_ramp

engine = CyrillicTfliteOCR()
engine.initialize()

t0 = time.time()
text, confidence = engine.recognize(img)
dt = time.time() - t0

with open('ocr_result.txt', 'w', encoding='utf-8') as f:
    f.write(f'Time: {dt:.2f}s\n')
    f.write(f'Confidence: {confidence:.3f}\n')
    f.write(f'Text ({len(text)} chars):\n')
    f.write(repr(text) + '\n')
    f.write(text + '\n')

    cleaned = fix_lookalikes_per_word(text)
    f.write('\n=== AFTER CLEANUP ===\n')
    f.write(repr(cleaned) + '\n')
    f.write(cleaned + '\n')

print('Written to ocr_result.txt')
