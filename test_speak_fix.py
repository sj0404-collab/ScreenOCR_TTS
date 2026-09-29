"""
Тест исправления голосового воспроизведения.
Проверяет что tts.speak() работает через event loop
(как в исправленном _speak_and_reset).
"""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))

from tts_engine import TTSEngine
from settings import Settings

TEXT = "Привет! Это тест озвучки. Проверка работает."


async def test_edge():
    print("=" * 50)
    print("ТЕСТ 1: Edge-TTS (основной движок)")
    print("=" * 50)
    settings = Settings()
    engine = TTSEngine(settings)
    print(f"  voice: {engine.voice}")
    print(f"  voice_type: {engine.voice_type}")

    t0 = time.perf_counter()
    audio = await engine.generate_audio(TEXT)
    ms = (time.perf_counter() - t0) * 1000

    if audio:
        print(f"  [OK] Аудио сгенерировано: {len(audio)} байт за {ms:.0f}мс")

        # Сохраняем во временный файл и воспроизводим через builtin_player
        from builtin_player import get_player
        tmp = os.path.join(os.path.dirname(__file__), "tts_cache", "_test_output.mp3")
        os.makedirs(os.path.dirname(tmp), exist_ok=True)
        with open(tmp, "wb") as f:
            f.write(audio)

        print(f"  [PLAY] Воспроизведение: {tmp}")
        player = get_player()
        player.play(tmp, wait=True)
        player.close()
        print("  [OK] Воспроизведение завершено")

        # Удаляем временный файл
        try:
            os.unlink(tmp)
        except Exception:
            pass
    else:
        print("  [ERR] Аудио НЕ сгенерировано!")
    return bool(audio)


async def test_speak_method():
    print("\n" + "=" * 50)
    print("ТЕСТ 2: tts.speak() через event loop")
    print("=" * 50)
    settings = Settings()
    engine = TTSEngine(settings)
    print(f"  voice: {engine.voice}")

    print(f"  [PLAY] speak('{TEXT[:30]}...')")
    t0 = time.perf_counter()
    await engine.speak(TEXT)
    ms = (time.perf_counter() - t0) * 1000
    print(f"  [OK] speak() завершён за {ms:.0f}мс")
    return True


async def test_speak_from_thread():
    """Имитация того, как _speak_and_reset работает в gui_compact.py"""
    print("\n" + "=" * 50)
    print("ТЕСТ 3: speak() из отдельного потока (как в GUI)")
    print("=" * 50)

    settings = Settings()
    engine = TTSEngine(settings)

    def speak_in_thread():
        print("  [thread] Запуск event loop...")
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(engine.speak(TEXT))
            print("  [thread] speak() выполнен успешно")
        except Exception as e:
            print(f"  [thread] ОШИБКА: {e}")
        finally:
            loop.close()

    t0 = time.perf_counter()
    import threading
    thread = threading.Thread(target=speak_in_thread, daemon=True)
    thread.start()
    thread.join(timeout=60)
    ms = (time.perf_counter() - t0) * 1000
    print(f"  [OK] Поток завершён за {ms:.0f}мс")
    return True


async def test_sapi_fallback():
    print("\n" + "=" * 50)
    print("ТЕСТ 4: RHVoice/SAPI fallback (если нет интернета)")
    print("=" * 50)
    settings = Settings()
    engine = TTSEngine(settings)
    engine.voice_type = "rhvoice"
    engine.voice = "rhvoice:Anna"
    print(f"  voice: {engine.voice}")

    t0 = time.perf_counter()
    audio = await engine.generate_audio(TEXT)
    ms = (time.perf_counter() - t0) * 1000

    if audio:
        print(f"  [OK] RHVoice: {len(audio)} байт за {ms:.0f}мс")
    else:
        print(f"  [WARN] RHVoice вернул пусто ({ms:.0f}мс) — попробуйте без интернета")
    return True


async def main():
    results = []
    results.append(("Edge-TTS генерация", await test_edge()))
    results.append(("speak() через event loop", await test_speak_method()))
    results.append(("speak() из потока (как GUI)", await test_speak_from_thread()))
    results.append(("RHVoice fallback", await test_sapi_fallback()))

    print("\n" + "=" * 50)
    print("ИТОГИ")
    print("=" * 50)
    for name, ok in results:
        icon = "[OK]" if ok else "[FAIL]"
        print(f"  {icon} {name}")
    print("=" * 50)


if __name__ == "__main__":
    asyncio.run(main())
