# -*- coding: utf-8 -*-
import sys, os
sys.path.insert(0, '.')

from PIL import Image
import numpy as np
from tflite_ocr import CyrillicTfliteOCR

engine = CyrillicTfliteOCR()
engine.initialize()

# Check what the recognizer gives for various boxes
boxes_to_test = [
    ('box_0_w603_h82.png', 'big right panel'),
    ('box_12_w376_h13.png', 'medium strip'),
    ('scan_test_full.png', 'full screenshot'),
]

for path, desc in boxes_to_test:
    if not os.path.exists(path):
        continue
    img = Image.open(path)
    result = engine.recognize(img)
    with open('ocr_debug.txt', 'a', encoding='utf-8') as f:
        f.write(f'\n=== {desc}: {path} ({img.size}) ===\n')
        f.write(f'Text: {repr(result[0])}\n')
        f.write(f'Conf: {result[1]:.3f}\n')