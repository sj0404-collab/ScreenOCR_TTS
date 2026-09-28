# -*- coding: utf-8 -*-
"""
Тест потоковой разбивки длинной реплики.

Проблема, которую проверяем: раньше реплика озвучивалась только ПОСЛЕ конца
фразы (ждали 450 мс тишины + 0.7 c idle), поэтому задержка = длина реплики.
Новый режим режет реплику по естественной паузе ВНУТРИ неё и начинает
озвучку, пока пользователь ещё говорит.

Ожидание:
  1) первая часть распознана ДО конца аудио (низкая задержка);
  2) в сумме кусков есть слова И начала, И конца реплики (ничего не потеряно);
  3) нет повторов (поток не дублирует текст).

Запуск: venv311\Scripts\python.exe test_streaming_chunks.py
"""
import asyncio
import io
import sys
import threading
import time

import numpy as np

if not (getattr(sys.stdout, "encoding", "") or "").lower().startswith("utf-8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                  errors="replace")

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


def main():
    from voice_translator import VoiceTranslator

    # Одна длинная реплика из НЕСКОЛЬКИХ фраз: после точки Edge-TTS кладёт
    # натуральную паузу — это и есть внутренняя граница для потока.
    # Реплика длинная (10+ c), иначе выигрыш невозможно измерить: на короткой
    # фразе даже медленный STT успевает раньше, чем реплика кончится.
    line = ("We should meet at the old bridge tomorrow. "
            "Are you bringing the map with you? "
            "I found some old coins near the river bank. "
            "We can trade them at the shop in the morning.")
    print("1) Синтез одной длинной реплики с паузами внутри...")
    a = _tts_audio(line)
    total = len(a) / SR
    print(f"   длительность: {total:.2f}s")
    if total < 8.0:
        print(f"[WARN] реплика слишком короткая ({total:.2f}s) для проверки")

    vt = VoiceTranslator()
    vt.set_languages("en", "ru")
    vt.set_vad(True)
    vt.set_model("tiny")
    vt.tts = None                      # озвучка тут не нужна, только текст
    vt._dst_lang = "ru"

    if not vt._ensure_whisper():
        print("[ERR] Whisper не загрузился")
        return 1

    # событие: (время, сколько аудио уже подано, текст)
    events = []
    fed = {"sec": 0.0}
    lock = threading.Lock()

    def on_heard(t, lang="", spk=""):
        with lock:
            events.append((time.time(), fed["sec"], t))

    vt.on_heard = on_heard

    # Момент РАЗРЕЗА: сколько аудио было подано, когда кусок пошёл в STT.
    # Именно это определяет задержку: озвучка первого куска не может
    # начаться раньше, чем он распознан.
    cuts = []
    orig_process = vt._process_chunk

    def timed_process(audio, sr):
        with lock:
            cuts.append((fed["sec"], len(audio) / float(sr)))
        return orig_process(audio, sr)

    vt._process_chunk = timed_process

    vt._running = True
    threading.Thread(target=vt._process_loop, daemon=True).start()
    time.sleep(0.5)

    print("2) Подача аудио в реальном времени (тишина не передаётся)...")
    step = int(0.3 * SR)
    t0 = time.time()
    for pos in range(0, len(a), step):
        piece = a[pos:pos + step]
        peak = float(np.abs(piece).max()) if len(piece) else 0.0
        if peak < 0.005:               # как фильтр в game_audio_capture
            fed["sec"] = pos / SR
            time.sleep(0.3)
            continue
        vt._audio_queue.put((piece, SR))
        fed["sec"] = (pos + len(piece)) / SR
        time.sleep(0.3)
    print(f"   аудио подано за {time.time() - t0:.1f}s")

    # ждём ВСЕ куски: после подачи аудио последний кусок ещё распознаётся
    # (STT на CPU медленнее реального времени), поэтому просто ждём, пока
    # новые события перестанут приходить.
    deadline = time.time() + 25.0
    last_n = -1
    quiet_since = time.time()
    while time.time() < deadline:
        with lock:
            n = len(events)
        if n != last_n:
            last_n = n
            quiet_since = time.time()
        elif n > 0 and time.time() - quiet_since > 3.0:
            break        # всё распознано, событий нет 3 c
        time.sleep(0.2)
    vt._running = False
    time.sleep(0.5)

    print("\n--- Результат ---")
    ok = True
    if not cuts:
        print("  (ничего не распознано)")
    for i, (fsec, slen) in enumerate(cuts, 1):
        print(f"  кусок {i}: {slen:.2f}c аудио, начало озвучки на {fsec:.1f}s "
              f"из {total:.1f}s")
    for i, (ts, fsec, text) in enumerate(events, 1):
        print(f"    текст {i}: «{text}»")

    if not cuts:
        print("[ERR] ни одного куска")
        return 1

    # 1) низкая задержка: первый кусок распознан, пока реплика НЕ закончилась
    first_cut = cuts[0][0]
    if first_cut >= total - 0.6:
        print(f"[ERR] первый кусок ждал конца реплики ({first_cut:.1f}s "
              f"из {total:.1f}s) — потоковая разбивка не сработала")
        ok = False
    else:
        saved = total - first_cut
        print(f"[OK] первый кусок пошёл в озвучку на {first_cut:.1f}s из "
              f"{total:.1f}s — выигрыш {saved:.1f}s, конца фразы не ждём")

    # 1b) реплика реально разбилась на несколько кусков, а не одним
    if len(cuts) < 2:
        print(f"[ERR] реплика не разбита на куски (кусков: {len(cuts)})")
        ok = False
    else:
        print(f"[OK] реплика разбита на {len(cuts)} кусков по паузам")

    # 2) ничего не потеряно: проверяем ВСЕ фразы реплики
    joined = " ".join(e[2] for e in events).lower()
    wanted = {
        "bridge": "начало",
        "map": "конец 1-й фразы",
        "coins": "конец 2-й фразы",
        "shop": "конец реплики",
    }
    missing = [f"{w} ({why})" for w, why in wanted.items() if w not in joined]
    if missing:
        print(f"[ERR] потеряны куски реплики: {', '.join(missing)}")
        ok = False
    else:
        print("[OK] все фразы реплики озвучены, ничего не потеряно")

    # 3) нет дублей: сравниваем СОСЕДНИЕ куски (частые слова типа «the»
    #    не считаем дублем — сравниваем долю общих слов подряд)
    STOP = {"the", "a", "an", "you", "i", "we", "is", "are", "at", "in",
            "to", "of", "it", "and", "do", "did", "can", "me", "my", "your"}
    texts = [e[2].lower() for e in events]
    dupe = False
    for a_txt, b_txt in zip(texts, texts[1:]):
        wa = {w.strip(".,!?") for w in a_txt.split()} - STOP
        wb = {w.strip(".,!?") for w in b_txt.split()} - STOP
        if not wa or not wb:
            continue
        inter = len(wa & wb)
        if inter >= 2 and inter / min(len(wa), len(wb)) > 0.6:
            print(f"[ERR] куски повторяются: «{a_txt}» / «{b_txt}»")
            dupe = True
    if dupe:
        ok = False
    else:
        print("[OK] повторов нет")

    print("\n[OK] ТЕСТ ПРОЙДЕН" if ok else "\n[ERR] ЕСТЬ ПРОБЛЕМЫ")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
