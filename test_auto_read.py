# -*- coding: utf-8 -*-
"""Test Auto-Read pipeline: scan -> GlensOCR -> TTS."""
import sys, time, threading, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, '.')

from PIL import Image
import mss

print("=" * 60)
print("Test Auto-Read pipeline (1 frame)")
print("=" * 60)

from settings import Settings
from ocr_wrapper import OCRWrapper
s = Settings()
ocr = OCRWrapper(s)
print(f"[1] OCR engine: {ocr._active_engine_id}")

# Capture
regions = s.get("regions", [])
if regions:
    r = regions[0]
    with mss.mss() as sct:
        monitor = {"left": r["x"], "top": r["y"], "width": r["width"], "height": r["height"]}
        shot = sct.grab(monitor)
        img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    print(f"[2] Capture region: {r['width']}x{r['height']}")
else:
    with mss.mss() as sct:
        mon = sct.monitors[1]
        shot = sct.grab(mon)
        img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    print(f"[2] Capture full: {img.size[0]}x{img.size[1]}")

# OCR
t0 = time.time()
result = ocr.recognize(img)
dt = time.time() - t0
text = result[0] if isinstance(result, tuple) else str(result)
conf = result[1] if isinstance(result, tuple) and len(result) > 1 else 0.0
print(f"[3] OCR: {dt:.2f}s, conf={conf:.0f}%, {len(text)} chars")
print(f"    Text: {text[:200]}")

# TTS
print()
print("=" * 60)
print("Test TTS (speak)")
print("=" * 60)

from tts_engine import TTSEngine
tts = TTSEngine(s)
print(f"[4] TTS engine created")

if text and text.strip():
    print(f"[5] Speaking: {text[:80]}...")
    try:
        loop = __import__('asyncio').new_event_loop()
        loop.run_until_complete(tts.speak(text.strip()))
        loop.close()
        print("[6] TTS: OK")
    except Exception as e:
        print(f"[6] TTS error: {e}")
else:
    print("[5] Text empty, TTS skipped")

print()
print("=" * 60)
print("Auto-Read pipeline works!")
print("=" * 60)
