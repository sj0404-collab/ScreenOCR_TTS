# -*- coding: utf-8 -*-
from tflite_ocr import CyrillicTfliteOCR
from PIL import Image
import numpy as np

engine = CyrillicTfliteOCR()
engine.initialize()

img_path = 'screenshots/scan_20260905_110520_0001.png'
img = Image.open(img_path)
print(f'Image: {img.size}')

max_dim = 1600
scale_factor = 1.0
if max(img.width, img.height) > max_dim:
    scale_factor = max_dim / max(img.width, img.height)
    new_w = max(1, int(img.width * scale_factor))
    new_h = max(1, int(img.height * scale_factor))
    img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
    print(f'Resized to: {img.size}, scale={scale_factor:.3f}')

arr = np.array(img.convert('RGB'))
result = engine._engine(arr)
entries = result[0] if result and result[0] else []
print(f'Raw entries: {len(entries)}')

if not entries:
    print('No results from OCR')
    exit()

boxes = engine._build_boxes(entries, scale_factor)
print(f'Boxes: {len(boxes)}')

texts = []
for b in boxes[:20]:
    texts.append(b.text)
    print(f'  [{b.left:4d},{b.top:4d},{b.right:4d},{b.bottom:4d}] conf={b.confidence:.2f} "{b.text}"')

raw_text = " ".join(texts)
print(f'\nRaw ({len(raw_text)} chars): {raw_text[:300]}')

from ocr_text_cleaner import (
    normalize_local_cyrillic_caption, looks_like_dictionary_ramp,
    is_acceptable_cyrillic_text, filter_garbage_tokens,
)
cleaned = normalize_local_cyrillic_caption(raw_text).strip()
print(f'After normalize ({len(cleaned)} chars): {cleaned[:300]}')
print(f'  is_dictionary_ramp: {looks_like_dictionary_ramp(cleaned)}')
print(f'  is_acceptable: {is_acceptable_cyrillic_text(cleaned)}')
final = filter_garbage_tokens(cleaned)
print(f'After filter ({len(final)} chars): {final[:300]}')

# Now test full pipeline
text, conf = engine.recognize(Image.open(img_path))
print(f'\nFull pipeline: {len(text)} chars, conf={conf:.0f}%')
print(text[:300])
