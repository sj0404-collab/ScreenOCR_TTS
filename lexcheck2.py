# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, '.')
from ocr_text_cleaner import _ru_valid_words as rv
v = rv()
tests = ['чтобы', 'файл', 'поделиться', 'содержимым', 'получить',
         'дополнительные', 'сведения', 'выберите', 'один', 'алису', 'ai']
with open('lexcheck2.txt', 'w', encoding='utf-8') as f:
    for w in tests:
        f.write(f'{w}: {w in v}\n')
print('done')
