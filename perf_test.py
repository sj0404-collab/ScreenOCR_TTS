# -*- coding: utf-8 -*-
import sys, time
sys.path.insert(0, '.')
from PIL import Image
from tflite_ocr import CyrillicTfliteOCR
import tflite_ocr as T

img = Image.open('desktop_current.png').convert('RGB')
engine = CyrillicTfliteOCR()
assert engine.initialize(), 'init failed'

# Counters via monkeypatch
calls = {'run_rec': 0, 'infer': 0, 'rapid': 0, 'rescue': 0}
_orig_run = CyrillicTfliteOCR._run_recognizer
_orig_inf = CyrillicTfliteOCR._infer_window
_orig_en = CyrillicTfliteOCR._recognize_en
_orig_rescue = CyrillicTfliteOCR._rescue_narrow_gap

def run_rec(self, crop, model, chars, name):
    calls['run_rec'] += 1
    return _orig_run(self, crop, model, chars, name)

def inf(self, crop, model, chars):
    calls['infer'] += 1
    return _orig_inf(self, crop, model, chars)

def en(self, crop):
    calls['rapid'] += 1
    return _orig_en(self, crop)

def rescue(self, piece, whole):
    calls['rescue'] += 1
    return _orig_rescue(self, piece, whole)

CyrillicTfliteOCR._run_recognizer = run_rec
CyrillicTfliteOCR._infer_window = inf
CyrillicTfliteOCR._recognize_en = en
CyrillicTfliteOCR._rescue_narrow_gap = rescue

def log(msg):
    with open('perf_progress.txt', 'a', encoding='utf-8') as f:
        f.write(msg + '\n')

open('perf_progress.txt', 'w').close()
t0 = time.time()
boxes = engine._detect_text_boxes(img)
log(f'detect done: {len(boxes)} boxes in {time.time()-t0:.1f}s')
for i, b in enumerate(sorted(boxes, key=lambda x: (x.top, x.left))):
    bt = time.time()
    p = engine._pad(b, img.width, img.height)
    crop = img.crop((p.left, p.top, p.right, p.bottom))
    r = engine._recognize_line_bitmap(crop)
    log(f'box{i} {crop.size} model={r.model} dt={time.time()-bt:.2f}s '
        f'run_rec={calls["run_rec"]} infer={calls["infer"]} '
        f'rapid={calls["rapid"]} rescue={calls["rescue"]} text={repr(r.text[:40])}')
log(f'TOTAL {time.time()-t0:.1f}s calls={calls}')
print('done')
