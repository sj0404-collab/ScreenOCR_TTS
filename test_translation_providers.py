# -*- coding: utf-8 -*-
"""Проверка переводчиков: какой сервис реально работает.

Запуск:  venv311\\Scripts\\python.exe test_translation_providers.py

Проверяет оставшиеся провайдеры: Zen (free, без ключа) / Google / DeepL,
плюс сквозной тест цепочки auto на игровых фразах.

Удалённые провайдеры (26.09.2026):
  * OrcaRouter — api.orcarouter.ai/v1/chat/completions отдаёт 404;
  * OpenRouter — ключ из настроек отклоняется с 403.
Кода для них в проекте больше нет.
"""
import io
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace", line_buffering=True)

from settings import Settings
from translator import Translator, ZEN_MODELS

PHRASES = [
    "Adventure time!",
    "Off we go!",
    "Are you bringing the map with you?",
    "We should meet at the old bridge tomorrow.",
    "Paimon, wait for me!",
]


def check(name, fn):
    """fn -> (ok, result_or_error)"""
    t0 = time.time()
    try:
        r = fn()
        dt = time.time() - t0
        if r and not str(r).startswith("["):
            print(f"  [OK]   {name:16} {dt:5.1f}c  {r!r}")
            return True, r
        print(f"  [FAIL] {name:16} {dt:5.1f}c  {str(r)[:70]!r}")
        return False, r
    except Exception as e:
        dt = time.time() - t0
        msg = str(e)
        for frag, human in (("404", "404 — эндпоинт не существует"),
                            ("403", "403 — ключ отклонён / free tier только внутри OpenCode"),
                            ("429", "429 — троттлинг (слишком много запросов)"),
                            ("401", "401 — не авторизован"),
                            ("timeout", "таймаут"),
                            ("Max retries", "сеть недоступна")):
            if frag.lower() in msg.lower():
                msg = human
                break
        print(f"  [ERR]  {name:16} {dt:5.1f}c  {msg[:70]}")
        return False, msg


def main():
    s = Settings()
    zen_model = (s.get("translation.zen_model", "") or "").strip()
    cfg = {
        "api_key": s.get("translation.api_key", ""),
        "model": s.get("translation.model", ""),
        "zen_model": zen_model,
    }

    print("=" * 68)
    print("НАСТРОЙКИ ПЕРЕВОДА")
    print("=" * 68)
    for k, v in cfg.items():
        if "key" in k:
            shown = (v[:8] + "..." + v[-4:]) if len(v) > 14 else ("(пусто)" if not v else v)
            print(f"  {k:20} = {shown}")
        else:
            print(f"  {k:20} = {v!r}")
    print(f"  {'zen_model (эффективно)':20} = "
          f"{(zen_model or ZEN_MODELS[0])!r}")
    print("  Удалено: OrcaRouter (404), OpenRouter (403)")

    t = Translator()
    t.configure(**cfg)
    # Как в голосовом конвейере: словарный мусор в озвучку не пускаем.
    t.allow_offline_dict = False

    print()
    print("=" * 68)
    print("ПРОВЕРКА ПРОВАЙДЕРОВ")
    print("=" * 68)

    results = {}
    for mdl in t._zen_candidates():
        results[f"Zen/{mdl}"] = check(
            f"Zen/{mdl}",
            lambda m=mdl: t._translate_zen(PHRASES[0], "en", "ru", m))
    results["Google"] = check(
        "Google", lambda: t._translate_google(PHRASES[0], "en", "ru"))
    results["DeepL"] = check(
        "DeepL", lambda: t._translate_deepl_free(PHRASES[0], "en", "ru"))

    print()
    print("=" * 68)
    print("СКВОЗНОЙ ТЕСТ (цепочка auto, как в run_voice)")
    print("=" * 68)
    for p in PHRASES:
        t0 = time.time()
        r = t.translate(p, src="en", dst="ru")
        print(f"  {p!r:44} -> {r!r} [{time.time() - t0:.1f}c]")

    working = [k for k, (ok, _) in results.items() if ok]
    print()
    if working:
        print(f"[OK] Работают провайдеры: {', '.join(working)}")
        print("     Озвучка перевода включена.")
    else:
        print("[ERR] НИ ОДИН переводчик не работает.")
        print("     Озвучка перевода будет пропущена (показывается оригинал).")
        print("     Что делать:")
        print(f"       1) Zen: проверить модель — сейчас {ZEN_MODELS[0]}")
        print("       2) Google/DeepL free: троттлинг (429) с этого IP, ключ не поможет")
        print("       3) Локальный перевод без сети: transformers + opus-mt-en-ru")
        print("          (~300 МБ, torch уже есть)")
    return 0 if working else 1


if __name__ == "__main__":
    sys.exit(main())
