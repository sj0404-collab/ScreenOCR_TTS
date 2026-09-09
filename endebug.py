# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, '.')
from PIL import Image
from tflite_ocr import CyrillicTfliteOCR

img = Image.open('screenshots/scan_20260909_123247_0001.png').convert('RGB')
engine = CyrillicTfliteOCR()
assert engine.initialize(), 'init failed'
boxes = sorted(engine._detect_text_boxes(img), key=lambda b: (b.top, b.left))
b = boxes[0]
p = engine._pad(b, img.width, img.height)
crop = img.crop((p.left, p.top, p.right, p.bottom))
with open('endebug.txt', 'w', encoding='utf-8') as f:
    f.write(f'crop={crop.size}\n')
    r3 = engine._run_recognizer(crop, engine._primary, engine._primary_chars, "v3")
    f.write(f'r3: n_lat={r3.n_lat} n_cyr={r3.n_cyr} text={repr(r3.text)}\n')
    f.write(f'is_latin={engine._is_latin_crop(r3)}\n')
    re_ = engine._recognize_en(crop)
    if re_ is None:
        f.write('_recognize_en -> None\n')
        f.write(f'rapid_attr={engine._rapid}\n')
    else:
        f.write(f'_recognize_en -> model={re_.model} conf={re_.confidence:.3f} text={repr(re_.text)}\n')
    from rapid_ocr import RapidOCREngine
    r = RapidOCREngine(lang='en')
    f.write(f'rapid init={r.initialize()}\n')
    t, c = r.recognize(crop)
    f.write(f'rapid direct: conf={c:.1f} text={repr(t)}\n')
print('done')
