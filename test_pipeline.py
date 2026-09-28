# -*- coding: utf-8 -*-
"""
Тест 2: Полный пайплайн
EN text -> TTS -> Voice Analyzer -> Pick Voice -> RU TTS
Проверяет что мужской EN -> мужской RU, женский EN -> женский RU
"""
import asyncio
import os
import sys
import time
import numpy as np

sys.path.insert(0, os.path.dirname(__file__))

from tts_engine import TTSEngine
from voice_analyzer import analyze_voice, get_voice_label
from voice_translator import VoiceTranslator
from settings import Settings


async def test_pipeline():
    settings = Settings()
    engine = TTSEngine(settings)
    vt = VoiceTranslator()

    print("=" * 70)
    print("  FULL PIPELINE TEST")
    print("  EN text -> TTS -> Voice Analyzer -> Pick Voice -> RU TTS")
    print("=" * 70)

    # Simulate: different speakers generate EN audio
    scenarios = [
        ("Hello world, this is a test", "en-US-GuyNeural",       "male"),
        ("The quick brown fox jumps over the lazy dog", "en-US-GuyNeural", "male"),
        ("I am so happy today", "en-US-JennyNeural",             "female"),
        ("Please be careful with that", "en-US-JennyNeural",     "female"),
        ("I want to play with my toys", "en-US-AnaNeural",       "child"),
    ]

    results = []

    for i, (text, source_voice, expected_gender) in enumerate(scenarios):
        print()
        print("  [%d] Source: %s (expected %s)" % (i + 1, source_voice, expected_gender))
        print("      Text: %s" % text)

        # Step 1: generate EN audio (simulates microphone input)
        engine.set_voice(source_voice)
        engine.rate = 0
        t0 = time.perf_counter()
        audio_data = await engine.generate_audio(text)
        t_gen = (time.perf_counter() - t0) * 1000

        if not audio_data:
            print("      [FAIL] No audio generated")
            results.append((source_voice, "FAIL", "N/A", "N/A"))
            continue

        # Step 2: decode MP3 -> numpy
        import av
        tmp = os.path.join(engine.cache_dir, "_test_pipe.mp3")
        with open(tmp, "wb") as f:
            f.write(audio_data)
        container = av.open(tmp)
        resampler = av.audio.resampler.AudioResampler(
            format="s16", layout="mono", rate=16000
        )
        audio_np = []
        for frame in container.decode(container.streams.audio[0]):
            for r in resampler.resample(frame):
                audio_np.append(r.to_ndarray().flatten())
        container.close()
        os.unlink(tmp)

        samples = np.concatenate(audio_np).astype(np.float32) / 32768.0
        dur = len(samples) / 16000.0
        print("      Audio: %d samples (%.1fs)" % (len(samples), dur))

        # Step 3: analyze voice
        t1 = time.perf_counter()
        voice_info = analyze_voice(samples, 16000)
        speaker_label = get_voice_label(voice_info)
        t_analyze = (time.perf_counter() - t1) * 1000

        # Step 4: pick TTS voice
        chosen_voice = vt._pick_tts_voice(voice_info)

        # Step 5: generate RU with same voice
        engine.set_voice(chosen_voice)
        engine.rate = 0
        t2 = time.perf_counter()
        ru_data = await engine.generate_audio("Тест перевода на русский язык")
        t_ru = (time.perf_counter() - t2) * 1000

        ru_ok = "OK" if ru_data and len(ru_data) > 100 else "FAIL"
        gender_ok = voice_info.gender == expected_gender

        print("      Detected: %s (gender=%s pitch=%.0fHz)" % (
            speaker_label, voice_info.gender, voice_info.pitch_hz))
        print("      Gender match: %s" % ("OK" if gender_ok else "WRONG"))
        print("      Chosen voice: %s" % chosen_voice)
        print("      RU TTS: %s (%d bytes, %.0fms)" % (
            ru_ok, len(ru_data) if ru_data else 0, t_ru))
        print("      Timing: gen=%.0fms  analyze=%.0fms  ru=%.0fms" % (
            t_gen, t_analyze, t_ru))

        results.append((source_voice, speaker_label, chosen_voice, ru_ok, gender_ok))

    # Summary
    print()
    print("=" * 70)
    print("  PIPELINE RESULTS")
    print("=" * 70)
    header = "  %-28s %-20s %-36s %-5s %-6s" % (
        "Source", "Detected", "RU Voice", "RU", "Gender")
    print(header)
    print("  " + "-" * 97)
    for r in results:
        line = "  %-28s %-20s %-36s %-5s %-6s" % (
            r[0], r[1], r[2], r[3], "OK" if r[4] else "ERR")
        print(line)
    print("  " + "-" * 97)
    ok = sum(1 for r in results if r[3] == "OK")
    gok = sum(1 for r in results if r[4])
    print("  RU TTS OK: %d/%d   Gender OK: %d/%d" % (ok, len(results), gok, len(results)))
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(test_pipeline())
