# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, '.')
from PIL import Image
from rapid_ocr import RapidOCREngine

img = Image.open('screenshots/scan_20260909_123247_0001.png').convert('RGB')
from tflite_ocr import CyrillicTfliteOCR
te = CyrillicTfliteOCR()
assert te.initialize()
boxes = sorted(te._detect_text_boxes(img), key=lambda b: (b.top, b.left))
b = boxes[0]
p = te._pad(b, img.width, img.height)
crop = img.crop((p.left, p.top, p.right, p.bottom))

def padded(im, scale, border):
    w, h = im.size
    big = im.resize((w * scale, h * scale), Image.LANCZOS)
    canvas = Image.new('RGB', (big.width + 2 * border, big.height + 2 * border), (128, 128, 128))
    canvas.paste(big, (border, border))
    return canvas

eng = RapidOCREngine(lang='en')
assert eng.initialize()
variants = {
    'raw': crop,
    'pad12': padded(crop, 1, 12),
    'x2pad10': padded(crop, 2, 10),
    'x3pad16': padded(crop, 3, 16),
}
with open('padtest.txt', 'w', encoding='utf-8') as f:
    for tag, im in variants.items():
        t, c = eng.recognize(im)
        f.write(f'{tag} {im.size}: conf={c:.1f} text={repr(t)}\n')
print('done')
