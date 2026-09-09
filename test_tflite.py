# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, '.')
import numpy as np
from PIL import Image
import time

print('Loading TF...')
import tensorflow as tf
print(f'TF {tf.__version__}')

# Detector
print('\nLoading detector...')
det = tf.lite.Interpreter(model_path='models/cyrillic_ocr/cyrillic_detector.tflite')
det.allocate_tensors()
inp = det.get_input_details()
out = det.get_output_details()
print(f'  Input: shape={inp[0]["shape"]} dtype={inp[0]["dtype"]}')
print(f'  Output: shape={out[0]["shape"]} dtype={out[0]["dtype"]}')

# Prepare input
img = Image.open('screenshot.png')
scale = min(736 / img.width, 736 / img.height)
sw = max(1, int(img.width * scale))
sh = max(1, int(img.height * scale))
det_img = Image.new('RGB', (736, 736), (255, 255, 255))
resized = img.resize((sw, sh), Image.Resampling.LANCZOS)
ox, oy = (736 - sw) // 2, (736 - sh) // 2
det_img.paste(resized, (ox, oy))
arr = np.array(det_img, dtype=np.float32)
arr = arr[:, :, ::-1]
mean = np.array([0.485, 0.456, 0.406])
std = np.array([0.229, 0.224, 0.225])
arr = (arr / 255.0 - mean) / std
arr = arr.astype(np.float32).reshape(1, 736, 736, 3)

print('Running detector...')
t0 = time.time()
det.set_tensor(inp[0]['index'], arr)
det.invoke()
probability = det.get_tensor(out[0]['index'])
t1 = time.time()
print(f'  Output shape: {probability.shape}, time: {t1-t0:.2f}s')
print(f'  Range: [{probability.min():.3f}, {probability.max():.3f}]')

# Recognizer v3
print('\nLoading recognizer v3...')
rec = tf.lite.Interpreter(model_path='models/cyrillic_ocr/cyrillic_recognizer_v3.tflite')
rec.allocate_tensors()
inp_r = rec.get_input_details()
out_r = rec.get_output_details()
print(f'  Input: shape={inp_r[0]["shape"]} dtype={inp_r[0]["dtype"]}')
print(f'  Output: shape={out_r[0]["shape"]} dtype={out_r[0]["dtype"]}')

# Crop a text region and test
crop = det_img.crop((ox, oy, ox + sw, oy + sh))
rec_img = Image.new('RGB', (320, 48), (128, 128, 128))
r_scale = 48 / max(1, crop.height)
target_w = min(320, max(1, int(crop.width * r_scale)))
resized_r = crop.resize((target_w, 48), Image.Resampling.LANCZOS)
rec_img.paste(resized_r, (0, 0))
arr_r = np.array(rec_img, dtype=np.float32)
arr_r = arr_r[:, :, ::-1]
arr_r = (arr_r / 255.0 - 0.5) / 0.5
arr_r = arr_r.reshape(1, 48, 320, 3)

print('Running recognizer...')
t0 = time.time()
rec.set_tensor(inp_r[0]['index'], arr_r)
rec.invoke()
output = rec.get_tensor(out_r[0]['index'])
t1 = time.time()
print(f'  Output shape: {output.shape}, time: {t1-t0:.2f}s')
print(f'  Raw logits sample: {output[0, 0, :5]}')

print('\nAll TFLite models work!')
