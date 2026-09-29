# -*- coding: utf-8 -*-
"""
Тест скорости TTS — без bing probe, с замером каждого этапа.
"""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))

from tts_engine import TTSEngine
from settings import Settings

TEXT_SHORT = "Привет мир!"
TEXT_MEDIUM = "Привет! Меня зовут Тимофей. Сегодня хорошая погода. Мы гуляли в парке."
TEXT_LONG = "Привет! Меня зовут Тимофей. Сегодня хорошая погода. Мы гуляли в парке. Потом пошли в магазин и купили мороженое. Вечером смотрели фильм."


async def bench():
    settings = Settings()
    engine = TTSEngine(settings)

    print("=" * 60)
    print("  BENCHMARK: Edge-TTS + BuiltinPlayer (MCI)")
    print("=" * 60)

    for label, text in [("short", TEXT_SHORT), ("medium", TEXT_MEDIUM), ("long", TEXT_LONG)]:
        # --- Генерация ---
        t0 = time.perf_counter()
        audio = await engine.generate_audio(text)
        t_gen = (time.perf_counter() - t0) * 1000

        if not audio:
            print(f"  [{label:>6}] generation: FAILED")
            continue

        sz = len(audio)

        # --- Воспроизведение ---
        from builtin_player import get_player
        tmp = os.path.join(engine.cache_dir, "_bench.mp3")
        with open(tmp, "wb") as f:
            f.write(audio)

        t1 = time.perf_counter()
        player = get_player()
        player.play(tmp, wait=True)
        t_play = (time.perf_counter() - t1) * 1000
        player.close()

        t_total = t_gen + t_play

        try:
            os.unlink(tmp)
        except Exception:
            pass

        print(f"  [{label:>6}] gen={t_gen:6.0f}ms  play={t_play:6.0f}ms  total={t_total:6.0f}ms  size={sz}b")

    print("=" * 60)

    # --- Проверка cache hit ---
    print("\n  --- CACHE HIT TEST ---")
    t0 = time.perf_counter()
    audio = await engine.generate_audio(TEXT_SHORT)
    t_cached = (time.perf_counter() - t0) * 1000
    print(f"  [cached] gen={t_cached:6.0f}ms  size={len(audio) if audio else 0}b")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(bench())
