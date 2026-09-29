# -*- coding: utf-8 -*-
import sys, time
sys.path.insert(0, '.')
from PIL import Image
from tflite_ocr import CyrillicTfliteOCR

EXPECTED = [
    'настройки', 'балаболка', 'этот', 'компьютер', 'корзина',
    'корпорати', 'терминал', 'яндекс', 'браузер', 'языка',
    'поиск', 'клонирование', 'yomihon',
]

img = Image.open('desktop_current.png').convert('RGB')
engine = CyrillicTfliteOCR()
assert engine.initialize(), 'init failed'

t0 = time.time()
text, conf = engine.recognize(img)
dt = time.time() - t0

low = text.lower()
hits, miss = [], []
for w in EXPECTED:
    (hits if w in low else miss).append(w)

with open('desktop_score.txt', 'w', encoding='utf-8') as f:
    f.write(f'time={dt:.1f}s conf={conf} chars={len(text)}\n')
    f.write(f'HIT {len(hits)}/{len(EXPECTED)}: {", ".join(hits)}\n')
    f.write(f'MISS: {", ".join(miss)}\n')
    f.write('---TEXT---\n')
    f.write(text + '\n')
print(f'HIT {len(hits)}/{len(EXPECTED)} MISS={miss} time={dt:.1f}s')
