"""Test RHVoice SAPI5 voices"""
import sys, os, asyncio
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from tts_engine import TTSEngine
from settings import Settings

async def test():
    settings = Settings()
    tts = TTSEngine(settings)

    print("=" * 50)
    print("  RHVoice SAPI5 TEST")
    print("=" * 50)

    # List SAPI voices
    sapi = tts._get_sapi_voices()
    print(f"\nSAPI5 voices found: {len(sapi)}")
    for v in sapi:
        print(f"  {v['name']} ({v['culture']}, {v['gender']})")

    # Test each RHVoice
    voices_to_test = ["Pavel", "Elena", "Arina", "Victoria"]
    for voice_name in voices_to_test:
        print(f"\n--- Testing: {voice_name} ---")
        tts.voice = voice_name
        tts.voice_type = "rhvoice"
        audio = await tts.generate_audio(f"Привет! Это тест голоса {voice_name}.")
        if audio:
            print(f"  OK: {len(audio)} bytes")
            # Play via MCI
            uid = os.urandom(6).hex()
            tmp = f"tts_cache/rhvoice_{uid}.wav"
            os.makedirs("tts_cache", exist_ok=True)
            with open(tmp, "wb") as f:
                f.write(audio)
            from builtin_player import get_player
            player = get_player()
            player.play(tmp, wait=True)
            player.close()
            os.unlink(tmp)
            print(f"  Played!")
        else:
            print(f"  FAILED: no audio")

    # Edge comparison
    print("\n--- Edge-TTS: Dmitry ---")
    tts.voice = "ru-RU-DmitryNeural"
    tts.voice_type = "edge"
    audio = await tts.generate_audio("Сравнение: голос Дмитрий из Edge-TTS.")
    if audio:
        print(f"  OK: {len(audio)} bytes")

    print("\n" + "=" * 50)
    print("  DONE")
    print("=" * 50)

asyncio.run(test())
