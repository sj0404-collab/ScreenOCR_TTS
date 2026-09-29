# -*- coding: utf-8 -*-
import sys, os
sys.path.insert(0, '.')

# Read the ocr_result.txt and show the text
with open('ocr_result.txt', 'r', encoding='utf-8') as f:
    content = f.read()

idx = content.find("Text (111 chars):")
if idx >= 0:
    idx2 = content.find(chr(10), idx)
    idx3 = content.find(chr(10), idx2+1)
    text = content[idx2+1:idx3]

with open('ocr_debug.txt', 'w', encoding='utf-8') as f:
    f.write("RAW TEXT:\n")
    f.write(repr(text) + "\n")
    f.write("\nChar codes:\n")
    for i, c in enumerate(text):
        cp = ord(c)
        if cp > 127 or c in ' \n\t':
            f.write(f"  [{i}] U+{cp:04X} = {repr(c)}\n")