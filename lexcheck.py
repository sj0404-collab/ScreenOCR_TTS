# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, '.')
from ocr_text_cleaner import _ru_valid_words as rv
v = rv()
tests = ['частник', 'участник', 'здание', 'издание', 'сущий', 'несущий',
         'брать', 'забрать', 'дать', 'отдать', 'поделиться', 'содержимым',
         'театр', 'теат', 'род', 'народ', 'воз', 'паровоз', 'ход', 'теплоход']
with open('lexcheck.txt', 'w', encoding='utf-8') as f:
    f.write(f'dict={len(v)}\n')
    for w in tests:
        f.write(f'{w}: {w in v}\n')
print('done')
