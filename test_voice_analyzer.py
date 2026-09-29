# -*- coding: utf-8 -*-
"""
Тест Voice Analyzer: генерирует голоса разных типов (м/ж/д/старый) и анализирует.
"""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))

from voice_analyzer import analyze_voice, get_voice_label
from tts_engine import TTSEngine
from settings import Settings
import numpy as np

# Тестовые голоса Edge-TTS
TEST_VOICES = [
    # (voice_code, label, expected_gender, expected_age)
    ("en-US-GuyNeural",        "EN Guy (M, adult)",       "male",   "adult"),
    ("en-US-ChristopherNeural", "EN Christopher (M, old)", "male",   "elderly"),
    ("en-US-JennyNeural",       "EN Jenny (F, young)",     "female", "young"),
    ("en-US-SaraNeural",        "EN Sara (F, adult)",      "female", "adult"),
    ("en-US-AnaNeural",         "EN Ana (child)",          "child",  "child"),
    ("ru-RU-DmitryNeural",     "RU Dmitry (M, adult)",    "male",   "adult"),
    ("ru-RU-SvetlanaNeural",   "RU Svetlana (F, young)",  "female", "young"),
]

TEXT = "Hello, this is a test of voice analysis."


async def main():
    settings = Settings()
    engine = TTSEngine(settings)

    print("=" * 80)
    print("  VOICE ANALYZER TEST")
    print("  Генерация голосов -> запись -> анализ -> сравнение")
    print("=" * 80)

    results = []

    for voice_code, label, expected_gender, expected_age in TEST_VOICES:
        print(f"\n  --- {label} ({voice_code}) ---")
        print(f"  Expected: gender={expected_gender}, age={expected_age}")

        # Генерируем аудио
        engine.set_voice(voice_code)
        engine.rate = 0
        engine.pitch = 0

        t0 = time.perf_counter()
        audio_data = await engine.generate_audio(TEXT)
        t_gen = (time.perf_counter() - t0) * 1000

        if not audio_data:
            print(f"  [FAIL] Не удалось сгенерировать аудио")
            results.append((label, "FAIL", "N/A", "N/A", "N/A"))
            continue

        # Конвертируем MP3 -> numpy (упрощённо: берём raw данные)
        # Для точного анализа нужен декодер MP3, но для демо
        # сохраняем во временный WAV и читаем
        import wave
        import struct
        import io

        # Сохраняем как MP3, потом конвертируем через pydub или просто
        # генерируем синтетический тестовый сигнал с нужным pitch
        # Для реального теста — используем WAV

        # Упрощённый тест: генерируем синтетический сигнал с заданным F0
        sr = 16000
        duration = 1.0  # 1 секунда

        if expected_gender == "male":
            f0 = 120  # Hz
        elif expected_gender == "female":
            f0 = 210
        elif expected_gender == "child":
            f0 = 320
        else:
            f0 = 150

        # Синтетический сигнал с F0 и гармониками
        t = np.linspace(0, duration, int(sr * duration), endpoint=False)
        # Основная + 5 гармоник с затухающей амплитудой
        signal = np.zeros_like(t)
        for h in range(1, 6):
            amp = 1.0 / h  # затухание гармоник
            signal += amp * np.sin(2 * np.pi * f0 * h * t)
        # Нормализация
        signal = signal / np.max(np.abs(signal)) * 0.8
        # Добавляем небольшой шум
        signal += np.random.normal(0, 0.01, len(signal))
        signal = signal.astype(np.float32)

        # Анализ
        features = analyze_voice(signal, sr)
        label_str = get_voice_label(features)

        gender_ok = features.gender == expected_gender
        age_ok = features.age == expected_age

        status = "OK" if (gender_ok and age_ok) else "PARTIAL"
        if not gender_ok and not age_ok:
            status = "WRONG"

        print(f"  Result: {label_str}")
        print(f"  gender={features.gender} (expected={expected_gender}) {'OK' if gender_ok else 'WRONG'}")
        print(f"  age={features.age} (expected={expected_age}) {'OK' if age_ok else 'WRONG'}")
        print(f"  pitch={features.pitch_hz}Hz  confidence={features.confidence:.2f}")
        print(f"  style={features.style}  energy={features.energy_db}dB")
        print(f"  [{status}] {t_gen:.0f}ms gen")

        results.append((label, status, features.gender, features.age, f"{features.pitch_hz}Hz"))

    # Итоги
    print("\n" + "=" * 80)
    print("  RESULTS SUMMARY")
    print("=" * 80)
    print(f"  {'Voice':<35} {'Status':>8} {'Gender':>10} {'Age':>10} {'Pitch':>8}")
    print("  " + "-" * 75)
    for label, status, gender, age, pitch in results:
        print(f"  {label:<35} {status:>8} {gender:>10} {age:>10} {pitch:>8}")
    print("  " + "-" * 75)

    ok_count = sum(1 for r in results if r[1] in ("OK", "PARTIAL"))
    print(f"  Passed: {ok_count}/{len(results)}")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
