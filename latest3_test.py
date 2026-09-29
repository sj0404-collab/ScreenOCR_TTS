# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, '.')
from PIL import Image
from tflite_ocr import CyrillicTfliteOCR

engine = CyrillicTfliteOCR()
assert engine.initialize(), 'init failed'
out = []
for name in ['scan_20260909_123247_0001.png',
             'scan_20260909_123302_0002.png',
             'scan_20260909_123319_0003.png']:
    img = Image.open('screenshots/' + name).convert('RGB')
    text, conf = engine.recognize(img)
    out.append(f'=== {name} ({img.size}) conf={conf} ===')
    out.append(repr(text))
    out.append(text)
    out.append('')
with open('latest3.txt', 'w', encoding='utf-8') as f:
    f.write('\n'.join(out))
print('done')
