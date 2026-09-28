# -*- coding: utf-8 -*-
"""
Тест сегментации реплик и защиты от галлюцинаций.

Сценарий: 3 c музыки -> фраза EN -> 3 c музыки.
Ожидание: ровно ОДНА распознанная реплика (целая фраза), музыка игнорируется.

Запуск: venv311\Scripts\python.exe test_stt_segmentation.py
"""
import asyncio
import io
import sys
import threading
import time

import numpy as np

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

SR = 16000


def _tts_speech(text, voice="en-US-BrianMultilingualNeural"):
    import av
    import edge_tts

    async def grab():
        data = b""
        async for ch in edge_tts.Communicate(text, voice).stream():
            if ch["type"] == "audio":
                data += ch["data"]
        return data

    raw = asyncio.run(grab())
    with av.open(io.BytesIO(raw)) as container:
        stream = container.streams.audio[0]
        resampler = av.AudioResampler(format="s16", layout="mono", rate=SR)
        parts = []
        for frame in container.decode(audio=0):
            for rf in resampler.resample(frame):
                parts.append(rf.to_ndarray().reshape(-1))
    audio = np.concatenate(parts).astype(np.float32) / 32768.0
    peak = float(np.abs(audio).max()) or 1.0
    return audio / peak * 0.8


def _music(seconds, seed=0):
    t = np.arange(int(seconds * SR)) / SR
    rng = np.random.RandomState(seed)
    track = (0.30 * np.sin(2 * np.pi * 220 * t)
             + 0.20 * np.sin(2 * np.pi * 330 * t)
             + 0.15 * np.sin(2 * np.pi * 440 * t)
             + 0.05 * rng.randn(len(t)))
    track = track / max(1e-9, np.abs(track).max())
    return (track * 0.75).astype(np.float32)


def main():
    from voice_translator import VoiceTranslator

    print("1) Синтез речи (Edge-TTS)...")
    speech = _tts_speech("The scenery is wonderful, my friend.")
    print(f"   речь: {len(speech)/SR:.2f}s")

    track = np.concatenate([_music(3.0, 1), speech, _music(3.0, 2)])
    print(f"2) Микс: 3s музыка + речь + 3s музыка = {len(track)/SR:.2f}s")

    vt = VoiceTranslator()
    vt.set_languages("en", "ru")
    vt.set_vad(True)
    vt.set_model("tiny")
    vt.tts = None  # только STT, без озвучки

    heard = []
    vt.on_heard = lambda text, lang="", spk="": heard.append((text, spk))

    print("3) Загрузка Whisper tiny...")
    if not vt._ensure_whisper():
        print("[ERR] Whisper не загрузился")
        return 1

    vt._running = True
    thread = threading.Thread(target=vt._process_loop, daemon=True)
    thread.start()

    print("4) Подача аудио чанками по 0.3s (имитация live capture)...")
    step = int(0.3 * SR)
    for pos in range(0, len(track), step):
        vt._audio_queue.put((track[pos:pos + step], SR))
        time.sleep(0.3)
    time.sleep(3.0)  # дать дописать последнюю реплику
    vt._running = False
    time.sleep(0.5)

    print("\n--- Результат ---")
    for i, (text, spk) in enumerate(heard, 1):
        print(f"  реплика {i}: [{spk}] {text}")

    ok = True
    if len(heard) == 0:
        print("[WARN] Речь не распознана — проверьте аудио/VAD")
        ok = False
    elif len(heard) > 1:
        print(f"[ERR] Реплика разбита на {len(heard)} частей вместо одной")
        ok = False
    else:
        print("[OK] Ровно одна целая реплика, музыка проигнорирована")

    # Проверка анти-эха
    vt._recent_ru.append("Спасибо за внимание")
    if vt._looks_like_own_voice("Thanks for watching"):
        print("[ERR] Анти-эхо не работает")
        ok = False
    else:
        print("[OK] Чужой текст не считается эхом")

    if vt._looks_like_own_voice("спасибо за внимание."):
        print("[OK] Свой текст распознаётся как эхо и будет отброшен")
    else:
        print("[ERR] Свой текст не распознаётся как эхо")
        ok = False

    print("\n[OK] ТЕСТ ПРОЙДЕН" if ok else "\n[ERR] ЕСТЬ ПРОБЛЕМЫ")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
