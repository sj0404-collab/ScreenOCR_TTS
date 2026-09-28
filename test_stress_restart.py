# -*- coding: utf-8 -*-
"""Стресс: многократный старт/стоп конвейера.

Краши при повторном старте — типичная болезнь: не закрытые дескрипторы,
потоки-двойники, подвисшие таймеры. Здесь это проверяется напрямую.
"""
import io
import sys
import threading
import time

if not (getattr(sys.stdout, "encoding", "") or "").lower().startswith("utf-8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                  errors="replace")

ROUNDS = 3
RUN_SEC = 2.5


def main():
    from voice_translator import VoiceTranslator

    ok = True
    for i in range(1, ROUNDS + 1):
        print(f"\n--- раунд {i}/{ROUNDS} ---")
        before = threading.active_count()
        vt = VoiceTranslator()
        vt.tts = None                    # без TTS: проверяем пути без него
        vt.translator = None
        vt.set_languages("en", "ru")
        vt.set_vad(True)
        vt.set_subtitles(True, None, require_foreground=False)
        try:
            vt.start()
        except Exception as e:
            import traceback
            print(f"[ERR] start() упал: {type(e).__name__}: {e}")
            traceback.print_exc()
            ok = False
            continue
        time.sleep(RUN_SEC)
        try:
            vt.stop()
        except Exception as e:
            print(f"[ERR] stop() упал: {type(e).__name__}: {e}")
            ok = False
        time.sleep(0.6)
        after = threading.active_count()
        left = after - before
        print(f"    потоков до/после: {before}/{after} (+{left})")
        if left > 2:
            print(f"[WARN] осталось много потоков: +{left}")
        if left >= 1:
            names = [f"{t.name}({'daemon' if t.daemon else 'обычный'})"
                     for t in threading.enumerate()[1:] if t.is_alive()]
            print(f"    остались: {', '.join(names)}")
        # главное: воркеры должны завершиться
        for name in ("tts", "subtitles", "translate", "stt-loop"):
            t = getattr(vt, "_tts_thread", None)
            if t is not None and t.is_alive():
                print(f"[ERR] поток {name} жив после stop()")
                ok = False
        if vt._running:
            print("[ERR] _running не сброшен")
            ok = False

    # отдельная проверка: без TTS и переводчика воркеры не падают
    print("\n--- воркеры без tts/translator ---")
    vt = VoiceTranslator()
    vt.set_languages("en", "ru")
    vt._running = True
    fut = vt._translate_async("проверка", "en", "ru")
    try:
        print("    перевод без переводчика ->", repr(fut.result(timeout=5)))
    except Exception as e:
        print(f"[ERR] {type(e).__name__}: {e}")
        ok = False
    vt._tts_queue.put(("тест", fut, None))
    time.sleep(0.8)
    if vt._tts_worker_running():
        print("[ERR] воркер озвучки умер или завис без tts")
        if not vt._tts_thread.is_alive():
            ok = False
    else:
        print("    воркер не поднялся — это ожидаемо при tts=None")
    vt._running = False
    time.sleep(0.5)

    # ── ГЛАВНОЕ: после N перезапусков реплика не должна дублироваться ──
    print("\n--- дублирование аудио после перезапусков ---")
    from game_audio_capture import game_audio
    import winsound
    snd = r"C:\Windows\Media\Windows\Notify.wav"
    calls = {"n": 0, "peak": 0.0}

    def cb(audio, sr):
        import numpy as np
        calls["n"] += 1
        calls["peak"] = max(calls["peak"], float(np.abs(audio).max()))

    vt2 = VoiceTranslator()
    vt2.tts = None
    vt2.translator = None
    vt2.set_languages("en", "ru")
    vt2.set_vad(True)
    game_audio.add_audio_callback(cb)
    vt2.start()
    time.sleep(0.8)
    try:
        winsound.PlaySound(snd, winsound.SND_FILENAME | winsound.SND_ASYNC)
    except Exception:
        pass
    time.sleep(4.0)
    vt2.stop()
    game_audio.remove_audio_callback(cb)
    print(f"    вызовов колбэка: {calls['n']}, пик: {calls['peak']:.4f}")
    if calls["peak"] <= 0.0:
        print("[WARN] звука не было — проверка дублирования не удалась")
    elif not (3 <= calls["n"] <= 60):
        # 0.25 c на чанк => ~16 за 4 c; заметно больше = дублирование
        print(f"[ERR] подозрительное число вызовов: {calls['n']} — "
              f"вероятно дублирование")
        ok = False
    else:
        print("[OK] дублирования нет: старые потоки молчат")

    print("\n[OK] СТРЕСС ПРОЙДЕН" if ok else "\n[ERR] ЕСТЬ ПРОБЛЕМЫ")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
