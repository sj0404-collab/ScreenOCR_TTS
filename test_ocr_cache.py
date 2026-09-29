# -*- coding: utf-8 -*-
"""Проверка: кэш неизменившегося кадра + новые движки в GUI-цикле."""
import io
import sys
import time

if not (getattr(sys.stdout, "encoding", "") or "").lower().startswith("utf-8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                  errors="replace")
import logging
logging.basicConfig(level=logging.WARNING)

from PIL import Image, ImageDraw, ImageFont
from ocr_wrapper import OCRWrapper
from settings import Settings


def make(text, size=(620, 90)):
    img = Image.new("RGB", size, (245, 245, 250))
    d = ImageDraw.Draw(img)
    try:
        f = ImageFont.truetype("arial.ttf", 24)
    except Exception:
        f = ImageFont.load_default()
    d.text((14, 26), text, fill=(10, 10, 10), font=f)
    return img


def main():
    s = Settings()
    for engine in ("rapidocr", "zen"):
        s.set("ocr.engine", engine)
        s.set("ocr.detect_changes", True)
        o = OCRWrapper(s)
        try:
            o.set_engine(engine)
        except Exception as e:
            print(f"[ERR] {engine}: {e}")
            continue
        print(f"\n--- {engine} ---")
        img = make("We must leave the camp")

        t0 = time.time()
        t1, c1 = o.recognize(img)
        d1 = time.time() - t0
        print(f"  1-й скан:  {d1:5.2f} c conf={c1} -> {t1[:40]!r}")

        t0 = time.time()
        t2, c2 = o.recognize(img)          # тот же кадр
        d2 = time.time() - t0
        print(f"  тот же:   {d2:5.2f} c conf={c2} -> {t2[:40]!r}")
        if d2 > d1 * 0.5:
            print("  [WARN] кэш не сработал (повтор такой же дорогой)")

        img2 = make("Inventory")
        t0 = time.time()
        t3, c3 = o.recognize(img2)         # кадр изменился
        d3 = time.time() - t0
        print(f"  изменился:{d3:5.2f} c conf={c3} -> {t3[:40]!r}")
        if "Inventory" not in (t3 or ""):
            print("  [ERR] новый кадр не распознан")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
