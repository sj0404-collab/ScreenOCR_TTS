# -*- coding: utf-8 -*-
import sys, os
sys.path.insert(0, '.')

with open('models/cyrillic_ocr/cyrillic_dict_v3.txt', 'r', encoding='utf-8') as f:
    chars = [l.rstrip() for l in f.readlines() if l.strip()]

with open('dict_analysis.txt', 'w', encoding='utf-8') as f:
    f.write(f'Total: {len(chars)} chars\n')
    f.write('All chars:\n')
    for i, c in enumerate(chars):
        cp = ord(c)
        f.write(f'  [{i}] U+{cp:04X} = {repr(c)}\n')

print(f'Written dict_analysis.txt with {len(chars)} chars')