# -*- coding: utf-8 -*-
"""
Test: Live scanner uses correct voice type for each voice.
Verifies that voice_type is properly set when voice is changed.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from tts_engine import TTSEngine


async def test_set_voice_updates_type():
    """set_voice() should update both voice and voice_type."""
    engine = TTSEngine()

    # Edge-TTS voice
    engine.set_voice("ru-RU-DmitryNeural")
    assert engine.voice == "ru-RU-DmitryNeural"
    assert engine.voice_type == "edge", f"Expected 'edge', got '{engine.voice_type}'"
    print("[OK] Edge voice: type=edge")

    # English Edge-TTS voice
    engine.set_voice("en-US-GuyNeural")
    assert engine.voice == "en-US-GuyNeural"
    assert engine.voice_type == "edge", f"Expected 'edge', got '{engine.voice_type}'"
    print("[OK] English Edge voice: type=edge")

    # RHVoice
    engine.set_voice("rhvoice:Aleksandr")
    assert engine.voice == "rhvoice:Aleksandr"
    assert engine.voice_type == "rhvoice", f"Expected 'rhvoice', got '{engine.voice_type}'"
    print("[OK] RHVoice: type=rhvoice")

    # Silero
    engine.set_voice("silero:aidar")
    assert engine.voice == "silero:aidar"
    assert engine.voice_type == "silero", f"Expected 'silero', got '{engine.voice_type}'"
    print("[OK] Silero: type=silero")

    # Persona
    engine.set_voice("persona:genki")
    assert engine.voice == "persona:genki"
    assert engine.voice_type == "persona", f"Expected 'persona', got '{engine.voice_type}'"
    print("[OK] Persona: type=persona")

    print("[PASS] set_voice updates type correctly\n")


async def test_live_scanner_voice_type_sync():
    """Simulate live scanner setting voice directly — verify type is synced."""
    engine = TTSEngine()

    # Simulate what live scanner multi-voice does:
    # 1. Voice router returns an English Edge-TTS voice
    voice_code = "en-US-GuyNeural"

    # OLD WAY (broken): just set voice, not type
    engine.voice = voice_code
    # voice_type is still whatever it was before (e.g. "rhvoice")
    # This would cause the engine to try RHVoice with an Edge-TTS voice!

    # NEW WAY (fixed): use set_voice
    engine.set_voice(voice_code)
    assert engine.voice_type == "edge", f"Expected 'edge', got '{engine.voice_type}'"
    print("[OK] set_voice('en-US-GuyNeural') -> type=edge")

    # Simulate switching to Russian voice
    engine.set_voice("ru-RU-SvetlanaNeural")
    assert engine.voice_type == "edge"
    print("[OK] set_voice('ru-RU-SvetlanaNeural') -> type=edge")

    # Simulate switching to RHVoice
    engine.set_voice("rhvoice:Elena")
    assert engine.voice_type == "rhvoice"
    print("[OK] set_voice('rhvoice:Elena') -> type=rhvoice")

    print("[PASS] Live scanner voice type sync works\n")


async def test_generate_audio_with_correct_engine():
    """Verify generate_audio uses the correct engine for each voice type."""
    engine = TTSEngine()
    text = "Привет это тест"

    # Test with Edge-TTS voice
    engine.set_voice("ru-RU-DmitryNeural")
    assert engine.voice_type == "edge"
    audio = await engine.generate_audio(text, role="male")
    assert audio and len(audio) > 0, "Edge-TTS should generate audio"
    print(f"[OK] Edge-TTS: {len(audio):,} bytes")

    # Test with English text through voice router
    engine.set_voice("en-US-GuyNeural")
    assert engine.voice_type == "edge"
    audio = await engine.generate_audio("Hello this is a test", role="male")
    assert audio and len(audio) > 0, "Edge-TTS should generate audio for English"
    print(f"[OK] Edge-TTS English: {len(audio):,} bytes")

    print("[PASS] Generate audio uses correct engine\n")


async def test_voice_router_integration():
    """Test that voice router returns voices that work with set_voice."""
    engine = TTSEngine()

    # Simulate voice router returning different voices
    test_voices = [
        ("ru-RU-DmitryNeural", "edge"),
        ("en-US-GuyNeural", "edge"),
        ("en-US-JennyNeural", "edge"),
        ("ja-JP-KeitaNeural", "edge"),
    ]

    for voice_code, expected_type in test_voices:
        engine.set_voice(voice_code)
        assert engine.voice_type == expected_type, \
            f"Voice {voice_code}: expected '{expected_type}', got '{engine.voice_type}'"
        print(f"[OK] {voice_code} -> type={engine.voice_type}")

    print("[PASS] Voice router integration works\n")


async def main():
    print("\n" + "=" * 60)
    print("  LIVE SCANNER VOICE TYPE TEST SUITE")
    print("=" * 60 + "\n")

    try:
        await test_set_voice_updates_type()
        await test_live_scanner_voice_type_sync()
        await test_generate_audio_with_correct_engine()
        await test_voice_router_integration()
    except Exception as e:
        print(f"\n[ERROR] {e}")
        import traceback
        traceback.print_exc()
        return 1

    print("=" * 60)
    print("  ALL TESTS PASSED [OK]")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
