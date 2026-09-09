# -*- coding: utf-8 -*-
from tflite_ocr import CyrillicTfliteOCR
from PIL import Image
import time

engine = CyrillicTfliteOCR()
engine.initialize()

img = Image.open('screenshot.png')
start = time.time()
text, conf = engine.recognize(img)
elapsed = time.time() - start
print(f'Result: {len(text)} chars, conf={conf:.0f}%, {elapsed:.1f}s')
print(text[:500])
