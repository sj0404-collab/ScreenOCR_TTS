# -*- coding: utf-8 -*-
"""
Автотест STT Demo: прогоняет все фразы на мультилингвальных голосах.
Один голос Brian/Ava/Ana для EN и RU — единый тембр.
"""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))

from tts_engine import TTSEngine
from settings import Settings

# Мультилингвальные голоса — один для EN+RU
MALE_VOICE = "en-US-BrianMultilingualNeural"
FEMALE_VOICE = "en-US-AvaMultilingualNeural"
CHILD_VOICE = "en-US-AnaNeural"

DEMO_PHRASES = [
    # Уровень 1: Одно слово
    ("Hello", "Привет"),
    ("Water", "Вода"),
    ("Fire", "Огонь"),
    # Уровень 2: 2-3 слова
    ("I am ready", "Я готов"),
    ("Be careful", "Будь осторожен"),
    ("No time", "Нет времени"),
    # Уровень 3: Короткое предложение
    ("The dragon is coming", "Дракон приближается"),
    ("We need to run", "Нам нужно бежать"),
    # Уровень 4: Среднее предложение
    ("The ancient temple hides a powerful artifact", "Древний храм скрывает могущественный артефакт"),
    ("We must defend the village before sunset", "Мы должны защитить деревню до заката"),
    # Уровень 5: Сложное предложение
    ("The legendary sword of the fallen king can defeat the darkness that threatens our kingdom",
     "Легендарный меч павшего короля способен победить тьму, которая угрожает нашему королевству"),
]


async def main():
    settings = Settings()
    engine = TTSEngine(settings)

    print("=" * 70)
    print("  AUTO STT DEMO: Мультилингвальные голоса (EN+RU одним голосом)")
    print("=" * 70)

    results = []

    for i, (en_text, ru_text) in enumerate(DEMO_PHRASES):
        level = 1 if i < 3 else (2 if i < 6 else (3 if i < 8 else (4 if i < 10 else 5)))
        # Скорость RU: чем сложнее — тем медленнее
        ru_rate = max(-30, -5 * level)

        print(f"\n  [{level}] EN: {en_text}")
        print(f"       RU: {ru_text} (rate={ru_rate}%)")

        # Brian: тот же голос для EN и RU
        engine.set_voice(MALE_VOICE)
        
        # EN часть
        engine.rate = 0
        t0 = time.perf_counter()
        await engine.speak(en_text)
        t_en = (time.perf_counter() - t0) * 1000

        # RU часть — тот же голос Brian
        engine.rate = ru_rate
        t1 = time.perf_counter()
        await engine.speak(ru_text)
        t_ru = (time.perf_counter() - t1) * 1000

        total = t_en + t_ru
        results.append((level, en_text, ru_text, t_en, t_ru, total))
        print(f"       [OK] EN={t_en:.0f}ms RU={t_ru:.0f}ms total={total:.0f}ms")

    # Итоговая таблица
    print("\n" + "=" * 70)
    print("  ИТОГИ")
    print("=" * 70)
    print(f"  {'Lv':>2} {'EN':<40} {'EN ms':>7} {'RU ms':>7} {'Total':>7}")
    print("  " + "-" * 66)
    for lv, en, ru, t_en, t_ru, total in results:
        print(f"  {lv:>2} {en[:38]:<40} {t_en:>6.0f} {t_ru:>6.0f} {total:>6.0f}")
    print("  " + "-" * 66)
    print(f"  Voice: {MALE_VOICE} (Brian, multilingual, male)")
    print(f"  Total phrases: {len(results)}")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
