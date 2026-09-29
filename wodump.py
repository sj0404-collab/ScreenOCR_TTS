# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, '.')
from PIL import Image
from tflite_ocr import CyrillicTfliteOCR
from ocr_text_cleaner import _en_valid_words as ev, _ru_valid_words as rv

evs, rvs = ev(), rv()
img = Image.open('screenshots/scan_20260909_123247_0001.png').convert('RGB')
engine = CyrillicTfliteOCR()
assert engine.initialize(), 'init failed'
boxes = sorted(engine._detect_text_boxes(img), key=lambda b: (b.top, b.left))
with open('wodump.txt', 'w', encoding='utf-8') as f:
    f.write(f'workspace in EN: {"workspace" in evs} (EN size {len(evs)})\n')
    for i, b in enumerate(boxes):
        p = engine._pad(b, img.width, img.height)
        crop = img.crop((p.left, p.top, p.right, p.bottom))
        pieces = engine._split_words(crop)
        whole = engine._recognize_crop(crop)
        f.write(f'box{i} ({b.left},{b.top})-({b.right},{b.bottom}) npieces={len(pieces)} whole_model={whole.model} whole={repr(whole.text)}\n')
        for j, pc in enumerate(pieces):
            r3 = engine._run_recognizer(pc, engine._primary, engine._primary_chars, "v3")
            f.write(f'  piece{j} {pc.size} n_lat={r3.n_lat} n_cyr={r3.n_cyr} v3text={repr(r3.text)}\n')
print('done')
