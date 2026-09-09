# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, '.')
import numpy as np
from PIL import Image
from rapid_ocr import RapidOCREngine

eng = RapidOCREngine(lang="en")
ok = eng.initialize()
with open('rapid_test.txt', 'w', encoding='utf-8') as f:
    f.write(f'init={ok}\n')
    if ok:
        for name in ['scan_20260909_123247_0001.png',
                     'scan_20260909_123302_0002.png']:
            img = Image.open('screenshots/' + name).convert('RGB')
            text, conf = eng.recognize(img)
            f.write(f'=== {name} conf={conf:.1f} ===\n{repr(text)}\n{text}\n')
print('done')
