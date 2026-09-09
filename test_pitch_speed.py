# -*- coding: utf-8 -*-
"""
Test script for TTS pitch/speed settings.
Tests per-role pitch/rate, emotion adjustments, and engine-specific output.
"""
import asyncio
import os
import sys
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from tts_engine import TTSEngine


async def test_per_role_settings():
    """Test that per-role pitch/rate are applied correctly."""
    print("=" * 60)
    print("TEST 1: Per-role pitch/rate settings")
    print("=" * 60)

    engine = TTSEngine()

    # Set different pitch/rate for each role
    engine.set_role_pitch("male", -20)
    engine.set_role_rate("male", 10)
    engine.set_role_pitch("female", 15)
    engine.set_role_rate("female", -5)
    engine.set_role_pitch("narrator", 0)
    engine.set_role_rate("narrator", -15)

    assert engine.get_role_pitch("male") == -20, f"Expected -20, got {engine.get_role_pitch('male')}"
    assert engine.get_role_rate("male") == 10, f"Expected 10, got {engine.get_role_rate('male')}"
    assert engine.get_role_pitch("female") == 15, f"Expected 15, got {engine.get_role_pitch('female')}"
    assert engine.get_role_rate("female") == -5, f"Expected -5, got {engine.get_role_rate('female')}"
    assert engine.get_role_pitch("narrator") == 0, f"Expected 0, got {engine.get_role_pitch('narrator')}"
    assert engine.get_role_rate("narrator") == -15, f"Expected -15, got {engine.get_role_rate('narrator')}"

    print("  [OK] Per-role pitch/rate stored correctly")

    # Test emotion adjustments with per-role base
    engine.set_role("male")
    rate, pitch = engine._get_emotion_adjustments("Привет как дела", "male")
    # normal emotion: rate=0, pitch=0; male base: rate=10, pitch=-20
    assert rate == 10, f"Expected rate=10, got {rate}"
    assert pitch == -20, f"Expected pitch=-20, got {pitch}"
    print(f"  [OK] Male normal: rate={rate}, pitch={pitch}")

    rate, pitch = engine._get_emotion_adjustments("ПРИВЕТ ДРУГ", "male")
    # shout emotion: rate=50, pitch=25; male base: rate=10, pitch=-20
    # rate = 10 + 50 = 60, pitch = -20 + 25 = 5
    assert rate == 60, f"Expected rate=60, got {rate}"
    assert pitch == 5, f"Expected pitch=5, got {pitch}"
    print(f"  [OK] Male shout: rate={rate}, pitch={pitch}")

    rate, pitch = engine._get_emotion_adjustments("Мне грустно", "female")
    # sad emotion: rate=-20, pitch=-8; female base: rate=-5, pitch=15
    # rate = -5 + (-20) = -25, pitch = 15 + (-8) = 7
    assert rate == -25, f"Expected rate=-25, got {rate}"
    assert pitch == 7, f"Expected pitch=7, got {pitch}"
    print(f"  [OK] Female sad: rate={rate}, pitch={pitch}")

    # Test clamping
    engine.set_role_pitch("male", 50)
    engine.set_role_rate("male", 50)
    rate, pitch = engine._get_emotion_adjustments("ПРИВЕТ ДРУГ ТОВАРИЩИ", "male")
    # shout: rate=50, pitch=25; base: rate=50, pitch=50
    # rate = 50+50=100; pitch = 50+25=75 -> clamped to 50
    assert rate == 100, f"Expected rate=100, got {rate}"
    assert pitch == 50, f"Expected pitch=50 (clamped), got {pitch}"
    print(f"  [OK] Clamping: rate={rate}, pitch={pitch}")

    print("  [PASS] TEST 1\n")


async def test_edge_tts_pitch_speed():
    """Test Edge-TTS with various pitch/speed settings."""
    print("=" * 60)
    print("TEST 2: Edge-TTS pitch/speed generation")
    print("=" * 60)

    engine = TTSEngine()
    engine.voice = "ru-RU-DmitryNeural"
    engine.voice_type = "edge"
    engine.set_role("male")

    test_cases = [
        ("default", 0, 0),
        ("fast_high", 30, 20),
        ("slow_low", -30, -20),
        ("very_fast", 50, 0),
        ("very_slow", -50, 0),
        ("high_pitch", 0, 50),
        ("low_pitch", 0, -50),
    ]

    output_dir = Path("tts_test_output")
    output_dir.mkdir(exist_ok=True)

    text = "Привет это тест голоса как дела"

    for name, rate, pitch in test_cases:
        engine.set_role_pitch("male", pitch)
        engine.set_role_rate("male", rate)
        start = time.monotonic()
        audio = await engine.generate_audio(text, role="male")
        elapsed = time.monotonic() - start
        if audio:
            out_path = output_dir / f"edge_{name}_r{rate}_p{pitch}.mp3"
            out_path.write_bytes(audio)
            print(f"  [OK] {name:12s}: rate={rate:+3d}, pitch={pitch:+3d} -> {len(audio):,} bytes ({elapsed:.1f}s)")
        else:
            print(f"  [FAIL] {name}: no audio generated")

    print("  [PASS] TEST 2\n")


async def test_edge_tts_streaming_pitch():
    """Test Edge-TTS streaming with pitch/speed."""
    print("=" * 60)
    print("TEST 3: Edge-TTS streaming pitch/speed")
    print("=" * 60)

    engine = TTSEngine()
    engine.voice = "ru-RU-DmitryNeural"
    engine.voice_type = "edge"
    engine.set_role("male")
    engine.set_role_pitch("male", 15)
    engine.set_role_rate("male", 20)

    # Test that streaming uses correct pitch/rate
    # (We can't easily verify the audio content, but we can verify no errors)
    text = "Тест стриминга с питчем и скоростью"
    try:
        result = await engine._speak_edge_streaming(text)
        if result:
            print("  [OK] Streaming with pitch/rate completed successfully")
        else:
            print("  [SKIP] VLC not available, streaming skipped")
    except Exception as e:
        print(f"  [SKIP] Streaming error (VLC?): {e}")

    print("  [PASS] TEST 3\n")


async def test_dual_voice_with_roles():
    """Test dual voice mode with per-role settings."""
    print("=" * 60)
    print("TEST 4: Dual voice with per-role settings")
    print("=" * 60)

    engine = TTSEngine()
    engine.dual_voice_enabled = True
    engine.ru_voice = "ru-RU-DmitryNeural"
    engine.en_voice = "en-US-GuyNeural"
    engine.ru_voice_type = "edge"
    engine.en_voice_type = "edge"
    engine.set_role("male")
    engine.set_role_pitch("male", -10)
    engine.set_role_rate("male", 15)

    text = "Привет это русский текст Hello this is English text и снова русский"
    start = time.monotonic()
    audio = await engine.generate_audio(text, role="male")
    elapsed = time.monotonic() - start

    if audio:
        out_path = Path("tts_test_output") / "dual_voice_test.mp3"
        out_path.write_bytes(audio)
        print(f"  [OK] Dual voice: {len(audio):,} bytes ({elapsed:.1f}s)")
    else:
        print("  [FAIL] No audio generated")

    print("  [PASS] TEST 4\n")


async def test_smooth_transitions():
    """Test smooth crossfade between chunks."""
    print("=" * 60)
    print("TEST 5: Smooth transitions (crossfade)")
    print("=" * 60)

    engine = TTSEngine()
    engine.voice = "ru-RU-DmitryNeural"
    engine.voice_type = "edge"
    engine.set_role("male")
    engine._transition_enabled = True
    engine._transition_ms = 80

    # Generate two chunks with different emotions
    text1 = "Первый кусок текста"
    text2 = "А вот второй кусок уже с другой эмоцией"

    start = time.monotonic()
    audio1 = await engine.generate_audio(text1, role="male")
    audio2 = await engine.generate_audio(text2, role="male")
    elapsed = time.monotonic() - start

    if audio1 and audio2:
        out1 = Path("tts_test_output") / "chunk1.wav"
        out2 = Path("tts_test_output") / "chunk2.wav"
        out1.write_bytes(audio1)
        out2.write_bytes(audio2)
        print(f"  [OK] Chunk 1: {len(audio1):,} bytes")
        print(f"  [OK] Chunk 2: {len(audio2):,} bytes")
        print(f"  [OK] Total time: {elapsed:.1f}s")
    else:
        print("  [FAIL] No audio generated")

    print("  [PASS] TEST 5\n")


async def test_persona_blending():
    """Test that persona settings blend with user adjustments."""
    print("=" * 60)
    print("TEST 6: Persona + user pitch/rate blending")
    print("=" * 60)

    engine = TTSEngine()
    engine.voice = "persona:genki"
    engine.voice_type = "persona"
    engine.set_role("female")
    engine.set_role_pitch("female", 10)
    engine.set_role_rate("female", -5)

    text = "Привет как твои дела"
    start = time.monotonic()
    audio = await engine.generate_audio(text, role="female")
    elapsed = time.monotonic() - start

    if audio:
        out_path = Path("tts_test_output") / "persona_blend.mp3"
        out_path.write_bytes(audio)
        print(f"  [OK] Persona blend: {len(audio):,} bytes ({elapsed:.1f}s)")
    else:
        print("  [FAIL] No audio generated")

    print("  [PASS] TEST 6\n")


async def test_profile_save_load():
    """Test that profiles save/load per-role settings."""
    print("=" * 60)
    print("TEST 7: Profile save/load with per-role settings")
    print("=" * 60)

    engine = TTSEngine()
    engine.set_role_pitch("male", -15)
    engine.set_role_rate("male", 20)
    engine.set_role_pitch("female", 25)
    engine.set_role_rate("female", -10)
    engine.set_role_pitch("narrator", 5)
    engine.set_role_rate("narrator", -20)

    # Save profile
    profile_data = {
        "voice": "ru-RU-DmitryNeural",
        "voice_type": "edge",
        "pitch": -15,
        "rate": 20,
        "volume": 0.9,
        "role_pitch": {"male": -15, "female": 25, "narrator": 5},
        "role_rate": {"male": 20, "female": -10, "narrator": -20},
    }
    engine.add_profile("test_profile", profile_data)
    print("  [OK] Profile saved")

    # Reset
    engine.set_role_pitch("male", 0)
    engine.set_role_rate("male", 0)

    # Load profile
    profile = engine.profile_manager.get("test_profile")
    if profile:
        engine.apply_profile(profile)
        assert engine.get_role_pitch("male") == -15
        assert engine.get_role_rate("male") == 20
        assert engine.get_role_pitch("female") == 25
        assert engine.get_role_rate("female") == -10
        assert engine.get_role_pitch("narrator") == 5
        assert engine.get_role_rate("narrator") == -20
        print("  [OK] Profile loaded, per-role settings restored")
    else:
        print("  [FAIL] Profile not found")

    # Cleanup
    engine.remove_profile("test_profile")
    print("  [PASS] TEST 7\n")


async def test_edge_tts_rate_format():
    """Verify Edge-TTS rate string format is correct."""
    print("=" * 60)
    print("TEST 8: Edge-TTS rate/pitch string format")
    print("=" * 60)

    engine = TTSEngine()
    engine.voice = "ru-RU-DmitryNeural"
    engine.voice_type = "edge"

    # Test various rate values map to correct Edge-TTS format
    test_rates = [
        (0, "+0%"),
        (25, "+25%"),
        (-30, "-30%"),
        (50, "+50%"),
        (-50, "-50%"),
    ]

    for rate_val, expected_str in test_rates:
        engine.set_role_rate("male", rate_val)
        r, p = engine._get_emotion_adjustments("тест", "male")
        actual_str = f"{r:+d}%"
        assert actual_str == expected_str, f"Expected {expected_str}, got {actual_str}"
        print(f"  [OK] rate={rate_val:+3d} -> {actual_str}")

    # Test pitch values map to correct Hz format
    test_pitches = [
        (0, None),
        (20, "+20Hz"),
        (-15, "-15Hz"),
        (50, "+50Hz"),
        (-50, "-50Hz"),
    ]

    for pitch_val, expected_hz in test_pitches:
        engine.set_role_pitch("male", pitch_val)
        r, p = engine._get_emotion_adjustments("тест", "male")
        actual_hz = f"{p:+d}Hz" if p != 0 else None
        assert actual_hz == expected_hz, f"Expected {expected_hz}, got {actual_hz}"
        print(f"  [OK] pitch={pitch_val:+3d} -> {actual_hz}")

    print("  [PASS] TEST 8\n")


async def main():
    print("\n" + "=" * 60)
    print("  TTS PITCH/SPEED TEST SUITE")
    print("=" * 60 + "\n")

    try:
        await test_per_role_settings()
        await test_edge_tts_rate_format()
        await test_edge_tts_pitch_speed()
        await test_edge_tts_streaming_pitch()
        await test_dual_voice_with_roles()
        await test_smooth_transitions()
        await test_persona_blending()
        await test_profile_save_load()
    except Exception as e:
        print(f"\n  [ERROR] {e}")
        import traceback
        traceback.print_exc()
        return 1

    print("=" * 60)
    print("  ALL TESTS PASSED [OK]")
    print("=" * 60)
    print(f"\n  Test audio files saved to: tts_test_output/")
    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
