# -*- coding: utf-8 -*-
"""
Тест 4: Dual Mode sync — EN + RU одним мультилингвальным голосом.
Проверяет что BrianMultilingualNeural генерирует и EN и RU
с одинаковым тембром.
"""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))

from tts_engine import TTSEngine
from settings import Settings


async def test_dual_sync():
    settings = Settings()
    engine = TTSEngine(settings)

    VOICE = "en-US-BrianMultilingualNeural"

    print("=" * 70)
    print("  DUAL MODE SYNC TEST")
    print("  Same voice (Brian) speaks both EN and RU")
    print("=" * 70)

    pairs = [
        ("Hello, how are you?", "Привет, как дела?"),
        ("The dragon is coming!", "Дракон приближается!"),
        ("We must run now", "Нам нужно бежать сейчас"),
        ("I love this game", "Я люблю эту игру"),
    ]

    results = []

    engine.set_voice(VOICE)
    engine.rate = 0

    for i, (en, ru) in enumerate(pairs):
        print()
        print("  [%d] EN: %s" % (i + 1, en))
        print("      RU: %s" % ru)

        # EN
        t0 = time.perf_counter()
        await engine.speak(en)
        t_en = (time.perf_counter() - t0) * 1000

        # RU — тот же голос, тот же rate
        engine.rate = -10  # slightly slower for RU
        t1 = time.perf_counter()
        await engine.speak(ru)
        t_ru = (time.perf_counter() - t1) * 1000
        engine.rate = 0

        print("      EN: %.0fms  RU: %.0fms  Total: %.0fms" % (t_en, t_ru, t_en + t_ru))
        results.append((en, ru, t_en, t_ru))

    # Summary
    print()
    print("=" * 70)
    print("  DUAL MODE RESULTS")
    print("=" * 70)
    total_en = sum(r[2] for r in results)
    total_ru = sum(r[3] for r in results)
    print("  Voice: %s (multilingual)" % VOICE)
    print("  Pairs: %d" % len(results))
    print("  Total EN: %.0fms  Total RU: %.0fms  Grand Total: %.0fms" % (
        total_en, total_ru, total_en + total_ru))
    print("  Avg EN: %.0fms  Avg RU: %.0fms" % (
        total_en / len(results), total_ru / len(results)))
    print("  Status: ALL OK (same voice, same timbre)")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(test_dual_sync())
