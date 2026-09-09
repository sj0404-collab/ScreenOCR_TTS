# -*- coding: utf-8 -*-
"""Тест загрузки кадров в Qt6."""
import sys
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"  # headless

from PyQt6.QtWidgets import QApplication
app = QApplication.instance() or QApplication(sys.argv)

from qt6_avatar import _load_frames_from_disk

for em in ['neutral','happy','sad','angry','surprised','thinking','speaking','wink','sleeping']:
    frames = _load_frames_from_disk(em, 250, 290)
    print(f'{em}: {len(frames)} frames loaded')
    if frames:
        print(f'  first frame: {frames[0].width()}x{frames[0].height()}')
    else:
        print(f'  WARNING: no frames!')

print("\nTest complete.")
