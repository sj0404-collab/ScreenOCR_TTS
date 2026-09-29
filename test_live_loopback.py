# -*- coding: utf-8 -*-
"""
Живой тест:.loopback захват + собственный TTS = должен быть ноль реплик.

Это ровно та ситуация, из-за которой переводчик «зацикливался»:
наш голос выходит в колонки, loopback его слышит, STT распознаёт и
озвучивает снова. Ожидание — полная тишина на выходе.

Запуск: venv311\Scripts\python.exe test_live_loopback.py
"""
import asyncio
import io
import sys
import threading
import time

import numpy as np

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


def main():
    from game_audio_capture import game_audio
    from voice_translator import VoiceTranslator

    print("1) Подключаем реальный захват (auto-определение источника)...")
    found = game_audio.find_game_process()
    if found:
        print(f"   активный источник: {found['name']} (PID={found['pid']})")
    else:
        print("   активного источника нет -> fallback на системный loopback")

    vt = VoiceTranslator()
    vt.set_languages("en", "ru")
    vt.set_vad(True)
    vt.set_model("tiny")
    vt.tts = None
    heard = []
    vt.on_heard = lambda t, l="", s="": heard.append(t)

    if not vt._ensure_whisper():
        print("[ERR] Whisper не загрузился")
        return 1

    vt._running = True
    threading.Thread(target=vt._process_loop, daemon=True).start()

    # Запускаем захват и скармливаем STT всё, что он слышит
    def on_audio(audio, sr):
        vt._audio_queue.put((audio, sr))

    game_audio.add_audio_callback(on_audio)
    time.sleep(1.5)
    mode = "по процессу" if game_audio._proc_capture else "системный loopback"
    print(f"2) Захват работает: {mode}")

    # Проверка: наш PID не должен быть источником
    import os
    if game_audio._game_pid == os.getpid():
        print("[ERR] Источником выбран наш собственный процесс")
        return 1
    print(f"   источник: {game_audio._game_name} (PID={game_audio._game_pid}) — не self [OK]")

    print("3) Говорим ЧЕРЕЗ КОНВЕЙЕР (vt._speak_safe) — как в реальной работе...")
    from tts_engine import TTSEngine
    from settings import Settings
    engine = TTSEngine(Settings())
    engine.set_voice("en-US-BrianMultilingualNeural")
    engine.rate = 0
    vt.tts = engine          # конвейер сам владеет озвучкой

    text = "Спасибо за внимание, до встречи на следующей неделе."
    vt._speak_safe(text, tts_voice="en-US-BrianMultilingualNeural")

    deadline = time.time() + 40
    while vt._tts_playing and time.time() < deadline:
        time.sleep(0.2)
    print(f"   озвучка завершена, ждём остаток эха...")
    time.sleep(6.0)

    vt._running = False
    game_audio.remove_audio_callback(on_audio)
    game_audio.stop()
    time.sleep(0.5)

    print("\n--- Результат ---")
    print(f"распознано реплик: {len(heard)}")
    for t in heard:
        print(f"  - {t}")

    ok = not heard
    print("[OK] Самовосприятия нет — петля разорвана" if ok
          else "[ERR] Мы услышали сами себя")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
