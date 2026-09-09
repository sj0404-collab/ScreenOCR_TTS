# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, '.')
import numpy as np
from PIL import Image
from tflite_ocr import CyrillicTfliteOCR

img = Image.open('screenshots/scan_20260909_123319_0003.png').convert('RGB')
engine = CyrillicTfliteOCR()
assert engine.initialize(), 'init failed'
boxes = sorted(engine._detect_text_boxes(img), key=lambda b: (b.top, b.left))

with open('gaps.txt', 'w', encoding='utf-8') as f:
    f.write(f'img={img.size} boxes={len(boxes)}\n')
    for i, b in enumerate(boxes):
        p = engine._pad(b, img.width, img.height)
        crop = img.crop((p.left, p.top, p.right, p.bottom))
        pieces = engine._split_words(crop)
        r = engine._recognize_crop(crop)
        f.write(f'box{i} ({b.left},{b.top})-({b.right},{b.bottom}) npieces={len(pieces)} whole={repr(r.text)}\n')
        for j, pc in enumerate(pieces):
            rr = engine._recognize_crop(pc)
            # ink gap profile of the piece
            g = np.array(pc.convert('L'), dtype=np.int32)
            col = (g < 128).sum(axis=0)
            runs, gaps = [], []
            x = 0
            while x < len(col):
                if col[x] > 0:
                    x0 = x
                    while x < len(col) and col[x] > 0:
                        x += 1
                    runs.append((x0, x - 1))
                else:
                    x += 1
            for k in range(len(runs) - 1):
                gaps.append(runs[k + 1][0] - runs[k][1] - 1)
            f.write(f'  piece{j} {pc.size}: text={repr(rr.text)} gaps={gaps}\n')
print('done')
