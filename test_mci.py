"""Direct MCI player test"""
import sys, os, asyncio
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from tts_engine import TTSEngine
from settings import Settings

async def test():
    settings = Settings()
    tts = TTSEngine(settings)

    print("Generating Edge-TTS audio...")
    tts.voice = "ru-RU-DmitryNeural"
    tts.voice_type = "edge"
    audio = await tts.generate_audio("Привет! Тест звука.")
    print(f"Audio size: {len(audio)} bytes")

    if audio:
        # Save to file
        os.makedirs("tts_cache", exist_ok=True)
        test_file = "tts_cache/test_mci.mp3"
        with open(test_file, "wb") as f:
            f.write(audio)
        print(f"Saved: {test_file}")

        # Play via MCI
        from builtin_player import get_player
        player = get_player()
        print("Playing via MCI...")
        player.play(test_file, wait=True)
        print("Done! (if you heard sound, MCI works)")

asyncio.run(test())
