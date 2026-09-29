# -*- coding: utf-8 -*-
import sys, os
sys.path.insert(0, '.')

import numpy as np
from PIL import Image
import tensorflow as tf

# Load v5 dict
with open('models/cyrillic_ocr/cyrillic_dict_v5.txt', 'r', encoding='utf-8') as f:
    chars_v5 = [l.rstrip() for l in f.readlines() if l.strip()]

print(f'V5 dict: {len(chars_v5)} chars')

# V5 model
rec5 = tf.lite.Interpreter(model_path='models/cyrillic_ocr/cyrillic_recognizer_v5.tflite')
rec5.allocate_tensors()
inp5 = rec5.get_input_details()
out5 = rec5.get_output_details()

img = Image.open('scan_test_full.png')
crop = img.crop((700, 260, 900, 300))

rec_img = Image.new('RGB', (320, 48), (128, 128, 128))
scale = 48 / max(1, crop.height)
target_w = min(320, max(1, int(crop.width * scale)))
resized = crop.resize((target_w, 48), Image.Resampling.LANCZOS)
rec_img.paste(resized, (0, 0))

arr = np.array(rec_img, dtype=np.float32)
arr = arr[:, :, ::-1]
arr = (arr / 255.0 - 0.5) / 0.5

rec5.set_tensor(inp5[0]['index'], arr.reshape(1, 48, 320, 3))
rec5.invoke()
vals5 = rec5.get_tensor(out5[0]['index'])[0]

steps5, classes5 = vals5.shape
print(f'V5: {steps5} steps x {classes5} classes')

# Show top classes per step for v5
with open('debug_v5.txt', 'w', encoding='utf-8') as f:
    f.write(f'V5 dict: {len(chars_v5)} chars\n')
    f.write(f'V5 model: {steps5} steps x {classes5} classes\n')
    f.write('\n=== STEP-BY-STEP (top 3 classes) ===\n')
    for t in range(steps5):
        probs = vals5[t]
        top3 = np.argsort(probs)[-3:][::-1]
        parts = []
        for idx in top3:
            if idx == 0:
                parts.append(f'blank({probs[idx]:.3f})')
            elif idx - 1 < len(chars_v5):
                c = chars_v5[idx - 1]
                parts.append(f'{repr(c)}({probs[idx]:.3f})')
            else:
                parts.append(f'OUT[{idx}]({probs[idx]:.3f})')
        f.write(f'Step {t:2d}: {", ".join(parts)}\n')

print('Written debug_v5.txt')