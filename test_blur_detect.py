# -*- coding: utf-8 -*-
"""Test blur detection for Auto-Read."""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, '.')

import numpy as np
from PIL import Image, ImageFilter
from scipy.signal import convolve2d

def calc_blur(img):
    gray = np.array(img.convert("L"), dtype=np.float32)
    small = gray[::4, ::4]
    laplacian = np.array([[-1,-1,-1],[-1,8,-1],[-1,-1,-1]], dtype=np.float32)
    h, w = small.shape
    if h < 3 or w < 3:
        return 0.0
    lap = convolve2d(small, laplacian, mode='valid')
    return float(np.var(lap))

# Capture screen
import mss
with mss.mss() as sct:
    mon = sct.monitors[1]
    shot = sct.grab(mon)
    sharp = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")

# Sharp frame
blur_sharp = calc_blur(sharp)
print(f"Sharp frame:   blur_score = {blur_sharp:.1f}  (should be > 50)")

# Blurry frame (Gaussian blur = animation simulation)
blurry = sharp.filter(ImageFilter.GaussianBlur(radius=5))
blur_blurry = calc_blur(blurry)
print(f"Blurry frame:  blur_score = {blur_blurry:.1f}  (should be < 50)")

# Motion blur
motion = sharp.filter(ImageFilter.GaussianBlur(radius=10))
blur_motion = calc_blur(motion)
print(f"Motion blur:   blur_score = {blur_motion:.1f}  (should be < 50)")

# Threshold check
THRESHOLD = 50.0
print(f"\nThreshold: {THRESHOLD}")
print(f"Sharp:   {'PASS (OCR runs)' if blur_sharp > THRESHOLD else 'SKIP'}")
print(f"Blurry:  {'SKIP (animation)' if blur_blurry < THRESHOLD else 'PASS (OCR runs)'}")
print(f"Motion:  {'SKIP (animation)' if blur_motion < THRESHOLD else 'PASS (OCR runs)'}")
