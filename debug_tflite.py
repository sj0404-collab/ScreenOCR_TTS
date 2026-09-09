# -*- coding: utf-8 -*-
from tflite_ocr import CyrillicTfliteOCR
from PIL import Image
import numpy as np

engine = CyrillicTfliteOCR()
engine.initialize()

img = Image.open('screenshot.png')
max_dim = 1600
scale_factor = 1.0
if max(img.width, img.height) > max_dim:
    scale_factor = max_dim / max(img.width, img.height)
    new_w = max(1, int(img.width * scale_factor))
    new_h = max(1, int(img.height * scale_factor))
    img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)

arr = np.array(img.convert('RGB'))
result = engine._engine(arr)
print(f'Result: {type(result)}, len={len(result)}')
print(f'Result[0] type: {type(result[0])}')
print(f'Result[0] len: {len(result[0])}')

boxes = engine._build_boxes(result[0], scale_factor)
print(f'Boxes: {len(boxes)}')
for b in boxes[:5]:
    print(f'  [{b.left},{b.top},{b.right},{b.bottom}] "{b.text}" conf={b.confidence:.3f}')

# Test cleanup
from ocr_text_cleaner import (
    normalize_local_cyrillic_caption, looks_like_dictionary_ramp,
    is_acceptable_cyrillic_text, filter_garbage_tokens,
)
text = " ".join(b.text for b in boxes)
print(f'\nRaw text: {text[:100]}')
cleaned = normalize_local_cyrillic_caption(text).strip()
print(f'After normalize: {cleaned[:100]}')
print(f'is_dictionary_ramp: {looks_like_dictionary_ramp(cleaned)}')
print(f'is_acceptable: {is_acceptable_cyrillic_text(cleaned)}')
final = filter_garbage_tokens(cleaned)
print(f'Final: {final[:100]}')
