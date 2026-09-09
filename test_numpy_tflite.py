# -*- coding: utf-8 -*-
"""Test the numpy TFLite interpreter on the detector model."""
import time
import numpy as np
from PIL import Image
from numpy_tflite import TfliteInterpreter

print('Loading detector...')
interp = TfliteInterpreter('models/cyrillic_ocr/cyrillic_detector.tflite')
print(f'  Subgraph: {interp._sg.Name().decode()}')
print(f'  Input: tensor {interp._sg.Inputs(0)}')
print(f'  Output: tensor {interp._sg.Outputs(0)}')

# Prepare input: 736x736 BGR ImageNet-normalized
img = Image.open('screenshot.png')
scale = min(736 / img.width, 736 / img.height)
sw = max(1, int(img.width * scale))
sh = max(1, int(img.height * scale))
det_img = Image.new('RGB', (736, 736), (255, 255, 255))
resized = img.resize((sw, sh), Image.Resampling.LANCZOS)
ox, oy = (736 - sw) // 2, (736 - sh) // 2
det_img.paste(resized, (ox, oy))

arr = np.array(det_img, dtype=np.float32)
arr = arr[:, :, ::-1]  # RGB -> BGR
mean = np.array([0.485, 0.456, 0.406])
std = np.array([0.229, 0.224, 0.225])
arr = (arr / 255.0 - mean) / std

print(f'Input shape: {arr.shape}')
print('Running detector...')
t0 = time.time()
output = interp.invoke(arr)
t1 = time.time()
print(f'Output shape: {output.shape}, time: {t1-t0:.2f}s')
print(f'Output range: [{output.min():.3f}, {output.max():.3f}]')
print(f'Output mean: {output.mean():.3f}')
