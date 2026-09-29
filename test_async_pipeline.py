# -*- coding: utf-8 -*-
"""
Тест конвейера «STT -> перевод -> озвучка» с фоновым переводом.

Зачем: раньше цепочка была строго последовательной
    Whisper -> ПЕРЕВОД (2 c) -> ОЗВУЧКА,
и Whisper следующего куска ждал перевода предыдущего. Теперь перевод
уходит в отдельный поток, STT не блокируется.

Ожидание:
  1) переводы идут ПАРАЛЛЕЛЬНО: общее время << суммы задержек перевода;
  2) озвучка идёт в ИСХОДНОМ порядке (будущее не «перепрыгивает»);
  3) при падении перевода реплика не теряется молча и не читается мусором.

Запуск: venv311\Scripts\python.exe test_async_pipeline.py
"""
import asyncio
import io
import sys
import threading
import time

import numpy as np  # noqa: F401  (не нужен, но сохраняем единый стиль импортов)

if not (getattr(sys.stdout, "encoding", "") or "").lower().startswith("utf-8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                  errors="replace")

DELAY = 0.8          # искусственная задержка перевода, c
N = 4                # столько реплик подряд


class SlowTranslator:
    """Переводчик с управляемой задержкой + управляемой ошибкой."""

    def __init__(self, delay=DELAY):
        self.delay = delay
        self.calls = []
        self.lock = threading.Lock()
        self.allow_offline_dict = True
        self.fail_on = None

    def translate(self, text, src="auto", dst="ru"):
        with self.lock:
            self.calls.append(text)
        time.sleep(self.delay)
        if self.fail_on and self.fail_on in text:
            return ""
        return f"Перевод: {text}"


class FakeTTS:
    def __init__(self):
        self.spoken = []
        self.lock = threading.Lock()
        self.voice = "ru-RU-DmitryNeural"
        self.voice_type = "edge"
        self.rate = 0
        self.playback_started_at = 0.0
        self.playback_ended_at = 0.0

    async def speak(self, text):
        await asyncio.sleep(0.05)          # «озвучка» 50 мс
        with self.lock:
            self.spoken.append(text)
        self.playback_started_at = time.time()
        self.playback_ended_at = time.time()

    def set_voice(self, v):
        self.voice = v


def main():
    from voice_translator import VoiceTranslator

    vt = VoiceTranslator()
    vt.set_languages("en", "ru")
    vt.tts = FakeTTS()
    tr = SlowTranslator()
    vt.translator = tr
    vt._running = True

    threading.Thread(target=vt._translate_worker, daemon=True).start()
    vt._tts_worker_alive = True
    threading.Thread(target=vt._tts_worker, daemon=True).start()
    time.sleep(0.3)

    lines = [f"реплика номер {i}" for i in range(1, N + 1)]

    print(f"1) Отдаём {N} реплик, задержка перевода {DELAY}c...")
    t0 = time.time()
    for ln in lines:
        # так же, как это делает _process_chunk
        fut = vt._translate_async(ln, "en", "ru")
        vt._tts_queue.put((ln, fut, None))
    # ждём озвучки всех
    deadline = time.time() + 40
    while time.time() < deadline:
        with vt.tts.lock:
            if len(vt.tts.spoken) >= N:
                break
        time.sleep(0.05)
    elapsed = time.time() - t0
    vt._running = False
    time.sleep(0.3)

    print(f"   общее время: {elapsed:.2f}c "
          f"(последовательно было бы ~{N * DELAY + N * 0.05:.2f}c)")

    print("\n--- Результат ---")
    ok = True
    with vt.tts.lock:
        spoken = list(vt.tts.spoken)

    for i, s in enumerate(spoken, 1):
        print(f"  озвучено {i}: «{s}»")

    # 1) параллельность
    serial = N * DELAY
    if elapsed >= serial * 0.6:
        print(f"[ERR] перевод идёт последовательно ({elapsed:.2f}c) — "
              f"STT снова ждёт перевода")
        ok = False
    else:
        print(f"[OK] перевод параллелен: {elapsed:.2f}c вместо "
              f"{serial:.2f}c последовательно")

    # 2) порядок
    expect = [f"Перевод: {ln}" for ln in lines]
    if spoken != expect:
        print(f"[ERR] порядок нарушен.\n       ожидалось: {expect}"
              f"\n       получено : {spoken}")
        ok = False
    else:
        print("[OK] озвучка в исходном порядке, реплики не перепрыгнули")

    # 2b) перевод, начинающийся со скобки — НЕ маркер ошибки
    if VoiceTranslator._looks_like_error("[Шёпот] Слышишь меня?"):
        print("[ERR] нормальный перевод со скобкой принят за ошибку")
        ok = False
    else:
        print("[OK] перевод вида «[Шёпот] ...» не принят за ошибку")
    for bad in ("[Zen failed: 403]", "[Google failed]",
                "[orcarouter removed: endpoint unavailable]"):
        if not VoiceTranslator._looks_like_error(bad):
            print(f"[ERR] маркер ошибки не распознан: {bad}")
            ok = False
    if ok:
        print("[OK] маркеры ошибок провайдера распознаются")

    # 2c) подпись (язык/говорящий) едет вместе со СВОЕЙ репликой
    print("\n2) Проверка подписей при нескольких кусках в очереди...")
    vt3 = VoiceTranslator()
    vt3.set_languages("en", "ru")
    vt3.tts = FakeTTS()
    vt3.translator = SlowTranslator(delay=0.35)     # перевод медленный
    seen_text = []
    vt3.on_text = lambda en, ru, lang="", spk="": seen_text.append(
        (en, lang, spk))
    vt3._running = True
    threading.Thread(target=vt3._translate_worker, daemon=True).start()
    vt3._tts_worker_alive = True
    threading.Thread(target=vt3._tts_worker, daemon=True).start()
    time.sleep(0.2)

    pairs = [("реплика А", "Англия", "мужской"),
             ("реплика Б", "Япония", "женский"),
             ("реплика В", "Англия", "женский")]
    for en, lang, spk in pairs:
        vt3._tts_queue.put((en, vt3._translate_async(en, "en", "ru"),
                            None, lang, spk))
    end = time.time() + 20
    while time.time() < end and len(seen_text) < len(pairs):
        time.sleep(0.05)
    vt3._running = False
    time.sleep(0.2)

    got = {en: (l, s) for en, l, s in seen_text}
    for en, lang, spk in pairs:
        if got.get(en) != (lang, spk):
            print(f"[ERR] подпись перепутана для {en!r}: "
                  f"ожидалось ({lang}, {spk}), получено {got.get(en)}")
            ok = False
    if ok:
        print("[OK] язык и голос каждой реплики не перепутались")

    # 3) падение перевода: реплика не озвучивается мусором, но и не теряется
    print("\n2) Проверка падения перевода...")
    vt2 = VoiceTranslator()
    vt2.set_languages("en", "ru")
    vt2.tts = FakeTTS()
    tr2 = SlowTranslator(delay=0.2)
    tr2.fail_on = "сломанная"
    vt2.translator = tr2
    vt2._running = True
    threading.Thread(target=vt2._translate_worker, daemon=True).start()
    vt2._tts_worker_alive = True
    threading.Thread(target=vt2._tts_worker, daemon=True).start()
    time.sleep(0.2)

    for ln in ("нормальная реплика", "сломанная реплика", "ещё одна нормальная"):
        vt2._tts_queue.put((ln, vt2._translate_async(ln, "en", "ru"), None))
    deadline = time.time() + 20
    while time.time() < deadline:
        with vt2.tts.lock:
            if len(vt2.tts.spoken) >= 2:
                break
        time.sleep(0.05)
    vt2._running = False
    time.sleep(0.2)

    with vt2.tts.lock:
        sp2 = list(vt2.tts.spoken)
    print(f"   озвучено: {sp2}")
    if any("сломанная" in s for s in sp2):
        print("[ERR] неозвученная реплика всё равно озвучена (пустой перевод)")
        ok = False
    else:
        print("[OK] реплика с пустым переводом не озвучена (нет мусора в динамиках)")
    if len(sp2) != 2:
        print(f"[ERR] ожидалось 2 озвученные реплики, получено {len(sp2)}")
        ok = False
    else:
        print("[OK] остальные реплики озвучены, сбой не остановил конвейер")

    print("\n[OK] ТЕСТ ПРОЙДЕН" if ok else "\n[ERR] ЕСТЬ ПРОБЛЕМЫ")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
