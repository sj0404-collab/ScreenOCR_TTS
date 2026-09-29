# -*- coding: utf-8 -*-
import time
from PIL import Image
from tflite_ocr import CyrillicTfliteOCR

engine = CyrillicTfliteOCR()
ok = engine.initialize()
print(f"Init: {ok}")

img = Image.open("screenshot.png")
print(f"Image: {img.size}")

t0 = time.time()
text, conf = engine.recognize(img)
t1 = time.time()
print(f"\nResult: {len(text)} chars, conf={conf:.0f}%, {t1-t0:.1f}s")
print(text[:500])
