# -*- coding: utf-8 -*-
"""Полный тест Qt Avatar Manager с анимациями."""
import sys
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PyQt6.QtWidgets import QApplication
app = QApplication.instance() or QApplication(sys.argv)

from qt6_avatar import QtAvatarManager, QtAvatar

mgr = QtAvatarManager()
avatar = QtAvatar(slug="default", size=(250, 290))
mgr._avatar = avatar

print(f"Avatar created: {avatar}")
print(f"Available emotions: {list(avatar._frame_cycles.keys())}")

# Тест смены эмоций
for em in ['neutral', 'happy', 'sad', 'angry', 'surprised', 'thinking', 'speaking', 'wink', 'sleeping']:
    mgr.set_emotion(em)
    current = avatar.emotion  # используем property
    frame_count = len(avatar._frame_cycles.get(current, []))
    print(f"  {em:12s} -> current={current}, frames={frame_count}")

# Тест липсинка
for level in [0.0, 0.3, 0.6, 1.0, 0.0]:
    mgr.set_audio_level(level)
    print(f"  audio_level={level:.1f} -> mouth_open={avatar._mouth_open_target:.2f}")

print("\nFull test passed!")
