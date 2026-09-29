# -*- coding: utf-8 -*-
import os, sys, time
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"
import numpy as np
from PIL import Image
import tensorflow as tf

det = tf.lite.Interpreter(model_path='models/cyrillic_ocr/cyrillic_detector.tflite')
det.allocate_tensors()
inp = det.get_input_details()
out = det.get_output_details()

img = Image.open('screenshot.png')
scale = min(736 / img.width, 736 / img.height)
sw, sh = int(img.width * scale), int(img.height * scale)
det_img = Image.new('RGB', (736, 736), (255, 255, 255))
resized = img.resize((sw, sh), Image.Resampling.LANCZOS)
ox, oy = (736 - sw) // 2, (736 - sh) // 2
det_img.paste(resized, (ox, oy))

arr = np.array(det_img, dtype=np.float32)[:, :, ::-1]
mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
arr = ((arr / 255.0 - mean) / std).reshape(1, 736, 736, 3)

t0 = time.time()
det.set_tensor(inp[0]['index'], arr)
det.invoke()
prob = det.get_tensor(out[0]['index']).reshape(736, 736)
t1 = time.time()
print(f'Detector: {t1-t0:.2f}s, range=[{prob.min():.3f}, {prob.max():.3f}]')

# Count above threshold
above = (prob > 0.2).sum()
print(f'Pixels above 0.2: {above} / {736*736} = {above*100/(736*736):.1f}%')

# BFS boxes
visited = np.zeros(736*736, dtype=bool)
boxes = []
for start in range(736*736):
    if visited[start] or prob.flat[start] < 0.2:
        continue
    q = [start]
    visited[start] = True
    min_x, min_y, max_x, max_y = 736, 736, 0, 0
    area = 0
    while q:
        idx = q.pop(0)
        x, y = idx % 736, idx // 736
        min_x, min_y = min(min_x, x), min(min_y, y)
        max_x, max_y = max(max_x, x), max(max_y, y)
        area += 1
        for dy in range(-1, 2):
            for dx in range(-1, 2):
                nx, ny = x + dx, y + dy
                if 0 <= nx < 736 and 0 <= ny < 736:
                    ni = ny * 736 + nx
                    if not visited[ni] and prob.flat[ni] >= 0.2:
                        visited[ni] = True
                        q.append(ni)
    if area >= 16 and (max_x - min_x) >= 3 and (max_y - min_y) >= 3:
        boxes.append((min_x, min_y, max_x, max_y, area))

print(f'Boxes: {len(boxes)}')
for b in boxes[:10]:
    print(f'  [{b[0]},{b[1]},{b[2]},{b[3]}] area={b[4]}')

# Now test recognizer on first crop
if boxes:
    b = boxes[0]
    x1 = max(0, ox + int(b[0] / scale))
    y1 = max(0, oy + int(b[1] / scale))
    x2 = min(img.width, ox + int(b[2] / scale))
    y2 = min(img.height, oy + int(b[3] / scale))
    crop = img.crop((x1, y1, x2, y2))
    print(f'\nFirst crop: ({x1},{y1})-({x2},{y2}) = {crop.size}')
    crop.save('debug_crop.png')

    rec = tf.lite.Interpreter(model_path='models/cyrillic_ocr/cyrillic_recognizer_v3.tflite')
    rec.allocate_tensors()
    ri = rec.get_input_details()
    ro = rec.get_output_details()

    rec_img = Image.new('RGB', (320, 48), (128, 128, 128))
    rs = 48 / max(1, crop.height)
    tw = min(320, max(1, int(crop.width * rs)))
    resized_r = crop.resize((tw, 48), Image.Resampling.LANCZOS)
    rec_img.paste(resized_r, (0, 0))
    arr_r = np.array(rec_img, dtype=np.float32)[:, :, ::-1]
    arr_r = ((arr_r / 255.0 - 0.5) / 0.5).reshape(1, 48, 320, 3)

    t0 = time.time()
    rec.set_tensor(ri[0]['index'], arr_r)
    rec.invoke()
    vals = rec.get_tensor(ro[0]['index']).flatten()
    t1 = time.time()

    num_classes = len(vals) // 40
    print(f'Rec output: {vals.shape}, steps={num_classes}, time={t1-t0:.3f}s')

    # Load dict and CTC decode
    with open('models/cyrillic_ocr/cyrillic_dict_v3.txt', 'r', encoding='utf-8') as f:
        chars = [c.strip() for c in f.readlines()]

    text = []
    prev = -1
    for step in range(40):
        base = step * num_classes
        logits = vals[base:base + num_classes]
        max_l = logits.max()
        exp_l = np.exp(logits - max_l)
        probs = exp_l / exp_l.sum()
        best = probs.argmax()
        if best != 0 and best != prev and best - 1 < len(chars):
            text.append(chars[best - 1])
        prev = best

    print(f'Decoded: {"".join(text)}')
