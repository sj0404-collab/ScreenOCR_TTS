# -*- coding: utf-8 -*-
"""
Тест защиты от самовосприятия TTS (эхо-петля).

Сценарий: наш собственный голос (Edge-TTS, русский) попадает в loopback.
Ожидание: НИ ОДНОЙ реплики не распознано и НИ ОДНОЙ озвучки не запущено.

Проверяем два уровня защиты:
  1) флаг _tts_playing (STT выключен, пока говорим);
  2) анти-эхо по тексту (_recent_ru), если флаг уже снят.

Запуск: venv311\Scripts\python.exe test_echo_guard.py
"""
import asyncio
import io
import sys
import threading
import time

import numpy as np

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

SR = 16000


def _tts_audio(text, voice):
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
    audio = np.concatenate(parts).astype(np.float32) / 32768.0
    peak = float(np.abs(audio).max()) or 1.0
    return audio / peak * 0.85


class SpyTTS:
    """Заглушка TTS: считает вызовы speak()."""

    def __init__(self):
        self.calls = []
        self.voice = "en-US-BrianMultilingualNeural"
        self.voice_type = "edge"
        self.rate = 0

    def set_voice(self, v):
        self.voice = v

    async def speak(self, text, callback=None):
        self.calls.append(text)
        await asyncio.sleep(0.01)


def main():
    from voice_translator import VoiceTranslator

    print("1) Синтез собственной речи (rus)...")
    own = _tts_audio("Спасибо за внимание, до встречи!", "en-US-BrianMultilingualNeural")
    print(f"   {len(own)/SR:.2f}s")

    vt = VoiceTranslator()
    vt.set_languages("auto", "ru")   # как в run_voice.py: авто-определение
    vt.set_vad(True)
    vt.set_model("tiny")
    spy = SpyTTS()
    vt.tts = spy

    heard = []
    vt.on_heard = lambda t, l="", s="": heard.append(t)

    if not vt._ensure_whisper():
        print("[ERR] Whisper не загрузился")
        return 1

    vt._running = True
    threading.Thread(target=vt._process_loop, daemon=True).start()

    step = int(0.3 * SR)

    print("2) Фаза A: _tts_playing=True (мы говорим — STT спит)")
    vt._tts_playing = True
    for pos in range(0, len(own), step):
        vt._audio_queue.put((own[pos:pos + step], SR))
        time.sleep(0.15)
    time.sleep(1.0)
    heard_a = list(heard)
    print(f"   распознано: {len(heard_a)} {heard_a}")

    print("3) Фаза B: флаг снят, но наш текст в памяти (анти-эхо)")
    vt._tts_playing = False
    vt._echo_until = 0.0
    vt._recent_ru.append("Спасибо за внимание, до встречи")
    for pos in range(0, len(own), step):
        vt._audio_queue.put((own[pos:pos + step], SR))
        time.sleep(0.15)
    time.sleep(2.0)
    vt._running = False
    time.sleep(0.3)

    heard_b = heard[len(heard_a):]
    print(f"   распознано: {len(heard_b)} {heard_b}")

    ok = True
    if heard_a:
        print("[ERR] STT слышал во время собственной озвучки")
        ok = False
    else:
        print("[OK] Во время озвучки STT ничего не распознаёт")

    if heard_b:
        print("[ERR] Анти-эхо не сработало — будет зацикливание")
        ok = False
    else:
        print("[OK] Собственная речь отбрасывается по тексту")

    if spy.calls:
        print(f"[ERR] TTS вызван на собственную речь: {spy.calls}")
        ok = False
    else:
        print("[OK] Повторной озвучки нет — петля разорвана")

    print("\n[OK] ТЕСТ ПРОЙДЕН" if ok else "\n[ERR] ПЕТЛЯ НЕ РАЗОРВАНА")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
