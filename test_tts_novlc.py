"""Test TTS without VLC — all via MCI player"""
import sys, os, asyncio
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from settings import Settings
from tts_engine import TTSEngine

async def test():
    print("=" * 50)
    print("  TTS TEST (no VLC, MCI only)")
    print("=" * 50)

    settings = Settings()
    tts = TTSEngine(settings)

    print(f"Voice: {tts.voice}")
    print(f"Type: {tts.voice_type}")
    print(f"Rate: {tts.rate}")
    print(f"Pitch: {tts.pitch}")

    # Test 1: Male Russian
    print("\n--- Test 1: Male Russian (Dmitry) ---")
    tts.voice = "ru-RU-DmitryNeural"
    tts.voice_type = "edge"
    await tts.speak("Привет! Это тест голоса Дмитрия.")

    # Test 2: Female Russian
    print("\n--- Test 2: Female Russian (Svetlana) ---")
    tts.voice = "ru-RU-SvetlanaNeural"
    tts.voice_type = "edge"
    await tts.speak("Привет! Это тест голоса Светланы.")

    # Test 3: English
    print("\n--- Test 3: English (Guy) ---")
    tts.voice = "en-US-GuyNeural"
    tts.voice_type = "edge"
    await tts.speak("Hello! This is a test of Guy's voice.")

    # Test 4: Emotion detection
    print("\n--- Test 4: Emotion (shout) ---")
    tts.voice = "ru-RU-DmitryNeural"
    tts.voice_type = "edge"
    await tts.speak("НЕТ! Я НЕ МОГУ ЭТО СДЕЛАТЬ!")

    # Test 5: RHVoice (offline)
    print("\n--- Test 5: RHVoice offline ---")
    tts.voice = "rhvoice:Aleksandr"
    tts.voice_type = "rhvoice"
    await tts.speak("Тест офлайн голоса.")

    print("\n" + "=" * 50)
    print("  ALL TTS TESTS COMPLETE")
    print("=" * 50)

if __name__ == "__main__":
    asyncio.run(test())
