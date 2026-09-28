# -*- coding: utf-8 -*-
"""
Тест «как в игре»: реплика -> пауза -> реплика, при этом тишина НЕ передаётся
в STT (как в реальном game_audio_capture, который отбрасывает тихие чанки).

Ожидание: 2 отдельные целые реплики (не склейка, не «хвост» в конце),
и НИ ОДНОЙ реплики наmusic-тишине.

Запуск: venv311\Scripts\python.exe test_realtime_segments.py
"""
import asyncio
import io
import sys
import threading
import time

import numpy as np

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

SR = 16000


def _tts_audio(text, voice="en-US-BrianMultilingualNeural"):
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
        resampler = av.AudioResampler(format="s16", layout="mono", rate=SR)
        parts = []
        for frame in container.decode(audio=0):
            for rf in resampler.resample(frame):
                parts.append(rf.to_ndarray().reshape(-1))
    a = np.concatenate(parts).astype(np.float32) / 32768.0
    return a / (float(np.abs(a).max()) or 1.0) * 0.85


def feed(vt, audio, chunk_sec=0.3, real_time=True):
    """Отдать аудио чанками, ПРОПУСКАЯ тишину — как реальный захват."""
    step = int(chunk_sec * SR)
    for pos in range(0, len(audio), step):
        piece = audio[pos:pos + step]
        peak = float(np.abs(piece).max()) if len(piece) else 0.0
        if peak < 0.005:          # <- как фильтр в game_audio_capture
            continue
        vt._audio_queue.put((piece, SR))
        if real_time:
            time.sleep(chunk_sec)


def main():
    from voice_translator import VoiceTranslator

    print("1) Синтез двух реплик...")
    a = _tts_audio("We should meet at the old bridge tomorrow.")
    b = _tts_audio("Are you bringing the map with you?")
    print(f"   реплика A: {len(a)/SR:.2f}s, реплика B: {len(b)/SR:.2f}s")

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
    time.sleep(0.5)

    print("2) Реплика A (тишина не передаётся)...")
    feed(vt, a)
    # Ждём событие, а не фиксированную паузу: время от закрытия реплики
    # (таймаут очереди) до результата зависит от скорости Whisper на CPU.
    deadline = time.time() + 8.0
    while not heard and time.time() < deadline:
        time.sleep(0.1)
    got_a = len(heard)
    print(f"   реплика A распознана за {8.0 - (deadline - time.time()):.1f}s")

    print("3) Пауза 1.5 c, затем реплика B...")
    time.sleep(1.5)
    feed(vt, b)
    deadline = time.time() + 8.0
    while len(heard) < 2 and time.time() < deadline:
        time.sleep(0.1)
    vt._running = False
    time.sleep(0.3)

    print("\n--- Результат ---")
    for i, t in enumerate(heard, 1):
        print(f"  реплика {i}: {t}")

    ok = True
    if got_a != 1:
        print(f"[ERR] первая реплика не распознана сразу (получено {got_a})")
        ok = False
    else:
        print("[OK] первая реплика распознана сразу после паузы")

    if len(heard) != 2:
        print(f"[ERR] ожидалось 2 реплики, получено {len(heard)}")
        ok = False
    else:
        print("[OK] две отдельные реплики, разрыв не потерян")

    if heard and "bridge" not in heard[0].lower():
        print("[ERR] первая реплика неполная")
        ok = False
    if len(heard) > 1 and "map" not in heard[1].lower():
        print("[ERR] вторая реплика неполная")
        ok = False

    print("\n[OK] ТЕСТ ПРОЙДЕН" if ok else "\n[ERR] ЕСТЬ ПРОБЛЕМЫ")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
