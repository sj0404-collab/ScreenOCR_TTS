# -*- coding: utf-8 -*-
import numpy as np

with open('models/cyrillic_ocr/cyrillic_dict_v5.txt', 'r', encoding='utf-8') as f:
    chars = [l.rstrip() for l in f.readlines()]

print(f'Dict size: {len(chars)}')

# Check what indices 331, 474, 490, 507, 504, 513, 498, 499, 508, 511 map to
indices = [331, 851, 474, 490, 507, 504, 513, 498, 499, 508, 511]
for idx in indices:
    if idx == 0:
        print(f'  idx={idx:3d} -> BLANK')
    elif 1 <= idx <= len(chars):
        c = chars[idx-1]
        print(f'  idx={idx:3d} -> dict[{idx-1}] = U+{ord(c):04X} = {repr(c)}')
    else:
        print(f'  idx={idx:3d} -> OUT OF RANGE (dict has {len(chars)} entries)')

# Show chars around Cyrillic block
print('\nChars around idx 331:')
for i in range(328, 335):
    if 1 <= i <= len(chars):
        c = chars[i-1]
        print(f'  dict[{i-1}] = idx {i}: U+{ord(c):04X} = {repr(c)}')
