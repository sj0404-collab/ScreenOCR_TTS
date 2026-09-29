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
    out.append(f'===== {name} =====')
    boxes = sorted(engine._detect_text_boxes(img), key=lambda b: (b.top, b.left))
    for i, b in enumerate(boxes):
        p = engine._pad(b, img.width, img.height)
        crop = img.crop((p.left, p.top, p.right, p.bottom))
        pieces = engine._split_words(crop)
        out.append(f'box{i} ({b.left},{b.top})-({b.right},{b.bottom}) npieces={len(pieces)}')
        for j, pc in enumerate(pieces):
            r = engine._recognize_crop(pc)
            out.append(f'  piece{j} {pc.size} model={r.model} n_lat={r.n_lat} n_cyr={r.n_cyr} text={repr(r.text)}')
with open('routedump.txt', 'w', encoding='utf-8') as f:
    f.write('\n'.join(out))
print('done')
