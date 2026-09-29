# -*- coding: utf-8 -*-
"""Проверка новых OCR-движков: Zen и RapidOCR."""
import io
import sys
import time

if not (getattr(sys.stdout, "encoding", "") or "").lower().startswith("utf-8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                  errors="replace")

from PIL import Image, ImageDraw, ImageFont
from ocr_engines import create_engine, list_engines
from settings import Settings


def sample(text="Level 89", size=(520, 90)):
    img = Image.new("RGB", size, (255, 255, 255))
    d = ImageDraw.Draw(img)
    try:
        f = ImageFont.truetype("arial.ttf", 26)
    except Exception:
        f = ImageFont.load_default()
    d.text((14, 24), text, fill=(0, 0, 0), font=f)
    return img


def main():
    s = Settings()
    print("--- реестр движков ---")
    for e in list_engines():
        print(f"  {e.id:18} priority={e.priority:3} net={e.requires_network} "
              f"| {e.name}")

    img = sample("Level 89")
    ok = True
    for eid in ("rapidocr", "zen"):
        print(f"\n--- {eid} ---")
        eng = create_engine(eid, s.__dict__)
        if eng is None:
            print("  [ERR] движок не создался")
            ok = False
            continue
        t0 = time.time()
        try:
            res = eng.recognize(img)
            text, conf = (res if isinstance(res, tuple) else (res, 0.0))
            dt = time.time() - t0
            hit = "level" in (text or "").lower()
            print(f"  {dt:5.1f} c conf={conf} -> {text!r}")
            if not hit:
                print("  [WARN] не нашёл ожидаемый текст")
        except Exception as ex:
            print(f"  [ERR] {type(ex).__name__}: {ex}")
            ok = False

    print("\n[OK] новые движки работают" if ok else "\n[ERR] есть проблемы")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
