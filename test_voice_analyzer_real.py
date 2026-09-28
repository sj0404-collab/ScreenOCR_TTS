# -*- coding: utf-8 -*-
"""
Тест Voice Analyzer на РЕАЛЬНЫХ голосах Edge-TTS.
Генерирует аудио, декодирует в numpy и анализирует.
"""
import asyncio
import os
import sys
import time
import tempfile

sys.path.insert(0, os.path.dirname(__file__))

from voice_analyzer import analyze_voice, get_voice_label
from tts_engine import TTSEngine
from settings import Settings
import numpy as np

TEST_VOICES = [
    ("en-US-GuyNeural",         "EN Guy (M, adult)",       "male",   "adult"),
    ("en-US-ChristopherNeural", "EN Christopher (M, old)", "male",   "elderly"),
    ("en-US-JennyNeural",       "EN Jenny (F, young)",     "female", "young"),
    ("en-US-SaraNeural",        "EN Sara (F, adult)",      "female", "adult"),
    ("en-US-AnaNeural",         "EN Ana (child)",          "child",  "child"),
    ("ru-RU-DmitryNeural",     "RU Dmitry (M, adult)",    "male",   "adult"),
    ("ru-RU-SvetlanaNeural",   "RU Svetlana (F, young)",  "female", "young"),
]

TEXT = "The quick brown fox jumps over the lazy dog. This is a test."


def decode_mp3_to_numpy(mp3_path: str) -> tuple:
    """Декодировать MP3 в numpy float32 (mono, 16kHz)."""
    try:
        import av
        container = av.open(mp3_path)
        stream = container.streams.audio[0]
        
        # Ресэмплинг в 16kHz mono
        resampler = av.audio.resampler.AudioResampler(
            format='s16',
            layout='mono',
            rate=16000
        )
        
        audio_data = []
        for frame in container.decode(stream):
            resampled = resampler.resample(frame)
            for r in resampled:
                audio_data.append(r.to_ndarray().flatten())
        
        container.close()
        
        if not audio_data:
            return None, 0
        
        samples = np.concatenate(audio_data)
        # int16 -> float32
        audio = samples.astype(np.float32) / 32768.0
        return audio, 16000
    except Exception as e:
        print(f"  [WARN] av decode failed: {e}")
        return None, 0


async def main():
    settings = Settings()
    engine = TTSEngine(settings)

    print("=" * 80)
    print("  VOICE ANALYZER: REAL AUDIO TEST")
    print("=" * 80)

    results = []

    for voice_code, label, expected_gender, expected_age in TEST_VOICES:
        print(f"\n  --- {label} ---")

        engine.set_voice(voice_code)
        engine.rate = 0
        engine.pitch = 0

        # Генерируем аудио
        t0 = time.perf_counter()
        audio_data = await engine.generate_audio(TEXT)
        t_gen = (time.perf_counter() - t0) * 1000

        if not audio_data:
            print(f"  [FAIL] No audio")
            results.append((label, "FAIL", "N/A", "N/A", "N/A", "N/A"))
            continue

        # Сохраняем во временный MP3
        tmp = os.path.join(engine.cache_dir, "_test_voice.mp3")
        with open(tmp, "wb") as f:
            f.write(audio_data)

        # Декодируем в numpy
        audio, sr = decode_mp3_to_numpy(tmp)

        # Удаляем временный файл
        try:
            os.unlink(tmp)
        except Exception:
            pass

        if audio is None or len(audio) < 100:
            print(f"  [WARN] MP3 decode failed, skipping")
            results.append((label, "SKIP", "N/A", "N/A", "N/A", "N/A"))
            continue

        # Отладка: показываем характеристики аудио
        print(f"  Audio: len={len(audio)}  range=[{audio.min():.4f}, {audio.max():.4f}]  "
              f"rms={np.sqrt(np.mean(audio**2)):.4f}")

        # Анализ
        t1 = time.perf_counter()
        features = analyze_voice(audio, sr)
        t_analyze = (time.perf_counter() - t1) * 1000
        label_str = get_voice_label(features)

        gender_ok = features.gender == expected_gender
        age_ok = features.age == expected_age

        status = "OK" if (gender_ok and age_ok) else "PARTIAL"
        if not gender_ok:
            status = "WRONG"

        print(f"  Result: {label_str}")
        print(f"  gender={features.gender} (expected={expected_gender}) {'OK' if gender_ok else 'WRONG'}")
        print(f"  age={features.age} (expected={expected_age}) {'OK' if age_ok else 'WRONG'}")
        print(f"  pitch={features.pitch_hz}Hz  confidence={features.confidence:.2f}")
        print(f"  spectral_centroid={features.spectral_centroid}Hz")
        print(f"  raw: {features.raw_features}")
        print(f"  [{status}] gen={t_gen:.0f}ms  analyze={t_analyze:.1f}ms")

        results.append((label, status, features.gender, features.age,
                        f"{features.pitch_hz}Hz", f"{features.spectral_centroid:.0f}Hz"))

    # Итоги
    print("\n" + "=" * 80)
    print("  RESULTS")
    print("=" * 80)
    print(f"  {'Voice':<35} {'Status':>8} {'Gender':>10} {'Age':>10} {'Pitch':>8} {'Centroid':>9}")
    print("  " + "-" * 85)
    for label, status, gender, age, pitch, centroid in results:
        print(f"  {label:<35} {status:>8} {gender:>10} {age:>10} {pitch:>8} {centroid:>9}")
    print("  " + "-" * 85)

    ok = sum(1 for r in results if r[1] == "OK")
    partial = sum(1 for r in results if r[1] == "PARTIAL")
    print(f"  OK: {ok}  PARTIAL: {partial}  Total: {len(results)}")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
