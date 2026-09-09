# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, '.')
from PIL import Image
from tflite_ocr import CyrillicTfliteOCR
from rapid_ocr import RapidOCREngine

img = Image.open('screenshots/scan_20260909_123247_0001.png').convert('RGB')
engine = CyrillicTfliteOCR()
assert engine.initialize(), 'init failed'
boxes = sorted(engine._detect_text_boxes(img), key=lambda b: (b.top, b.left))
b = boxes[1]
p = engine._pad(b, img.width, img.height)
crop = img.crop((p.left, p.top, p.right, p.bottom))

rapid = RapidOCREngine(lang='en')
assert rapid.initialize()
with open('boxen.txt', 'w', encoding='utf-8') as f:
    f.write(f'boxcrop={crop.size}\n')
    r3 = engine._run_recognizer(crop, engine._primary, engine._primary_chars, "v3")
    f.write(f'whole v3: n_lat={r3.n_lat} n_cyr={r3.n_cyr} text={repr(r3.text)}\n')
    f.write(f'is_latin={engine._is_latin_crop(r3)}\n')
    t, c = rapid.recognize(crop)
    f.write(f'rapid whole-box: conf={c:.1f} text={repr(t)}\n')
    # padded variant for rapid
    canvas = Image.new('RGB', (crop.width + 24, crop.height + 24), (128, 128, 128))
    canvas.paste(crop, (12, 12))
    t2, c2 = rapid.recognize(canvas)
    f.write(f'rapid padded-box: conf={c2:.1f} text={repr(t2)}\n')
print('done')
