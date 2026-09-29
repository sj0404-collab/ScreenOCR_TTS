# -*- coding: utf-8 -*-
import numpy as np
from PIL import Image
import tensorflow as tf

rec = tf.lite.Interpreter(model_path='models/cyrillic_ocr/cyrillic_recognizer_v5.tflite')
rec.allocate_tensors()
ri = rec.get_input_details()
ro = rec.get_output_details()

out_shape = ro[0]['shape']
print(f'V5 output shape: {out_shape}')

with open('models/cyrillic_ocr/cyrillic_dict_v5.txt', 'r', encoding='utf-8') as f:
    chars = [l.rstrip() for l in f.readlines()]
print(f'V5 dict size: {len(chars)}')

img = Image.open('screenshot.png')
crop = img.crop((30, 225, 145, 245))
rec_img = Image.new('RGB', (320, 48), (128, 128, 128))
rs = 48 / max(1, crop.height)
tw = min(320, max(1, int(crop.width * rs)))
resized = crop.resize((tw, 48), Image.Resampling.LANCZOS)
rec_img.paste(resized, (0, 0))

arr_bgr = np.array(rec_img, dtype=np.float32)[:, :, ::-1]
arr_bgr = ((arr_bgr / 255.0) - 0.5) / 0.5

rec.set_tensor(ri[0]['index'], arr_bgr.reshape(1, 48, 320, 3))
rec.invoke()
vals = rec.get_tensor(ro[0]['index'])[0]

num_classes = vals.shape[1]
steps = vals.shape[0]
print(f'Steps={steps}, classes={num_classes}')

# Decode with dict
text = []
prev = -1
for t in range(steps):
    probs = vals[t]
    best = int(np.argmax(probs))
    if best != 0 and best != prev and best - 1 < len(chars):
        text.append(chars[best - 1])
    prev = best
print(f'V5 decoded: {repr("".join(text))}')

# Show top indices
all_best = [int(np.argmax(vals[t])) for t in range(steps)]
non_blank = [b for b in all_best if b != 0]
print(f'Non-blank indices: {non_blank}')
