# -*- coding: utf-8 -*-
"""
Headless voice translator — слушает игру и переводит EN->RU в реальном времени.
Без GUI. Запуск: python run_voice.py

Особенности:
  * один экземпляр: второй запуск завершится, а не создаст «диалог с собой»;
  * авто-определение активного игрового EXE (в т.ч. переключение, если сменился);
  * логи в UTF-8, чтобы русский перевод не превращался в кракозябры;
  * реплика сначала пишется текстом, потом озвучивается.
"""
import argparse
import io
import os
import sys
import time
import logging

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Консоль cp1251 не умеет кириллицу -> принудительно UTF-8
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    try:
        sys.stdout = io.TextIOWrapper(
            sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(
            sys.stderr.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass

logger = logging.getLogger("run_voice")


def _setup_logging():
    handlers = []
    for stream in (sys.stdout, sys.stderr):
        try:
            handlers.append(logging.StreamHandler(stream))
        except Exception:
            pass
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s - %(message)s",
        handlers=handlers,
        force=True,
    )


def _own_process_tree():
    """PID'ы нас и наших предков (venv-launcher тоже держит run_voice.py
    в командной строке — раньше guard считал его «вторым экземпляром»
    и сразу завершался)."""
    pids = {os.getpid()}
    try:
        import psutil
        me = psutil.Process()
        pids.add(me.pid)
        for parent in me.parents():
            pids.add(parent.pid)
    except Exception:
        pass
    return pids


def _is_run_voice_cmd(cmdline) -> bool:
    """Совпадает ли командная строка с запуском run_voice.py
    (а не test_run_voice.py и не упоминания в других аргументах)."""
    for part in cmdline or []:
        name = os.path.basename(str(part).replace("\\", "/"))
        if name == "run_voice.py":
            return True
    return False


def _other_instances():
    """Список PID других (чужих) запущенных экземпляров run_voice.py."""
    pids = []
    try:
        import psutil
    except ImportError:
        return pids
    own = _own_process_tree()
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            if proc.info["pid"] in own:
                continue
            if _is_run_voice_cmd(proc.info.get("cmdline")):
                pids.append(proc.info["pid"])
        except Exception:
            continue
    return pids


def _pick_target_exe(exe_filter: str = None):
    """Найти PID процесса по имени exe (для --exe)."""
    try:
        import psutil
    except ImportError:
        return None, None
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            name = proc.info.get("name") or ""
            if not exe_filter:
                continue
            if name.lower() == exe_filter.lower():
                return proc.info["pid"], name
        except Exception:
            continue
    return None, None


def main():
    parser = argparse.ArgumentParser(description="Headless EN->RU voice translator")
    parser.add_argument("--exe", help="целевой exe, например GenshinImpact.exe")
    parser.add_argument("--model", default=None, help="Whisper: tiny/base/small")
    parser.add_argument("--no-tts", action="store_true", help="только текст, без озвучки")
    parser.add_argument("--subtitles", action="store_true",
                        help="читать субтитры с экрана: озвучивать, если голоса "
                             "нет, и уточнять распознавание, если есть и то "
                             "и другое")
    parser.add_argument("--sub-ocr-slow", action="store_true",
                        help="OCR субтитров без ускорения: нужен для "
                             "КИРИЛЛИЦЫ (медленный движок, но понимает "
                             "русские субтитры; по умолчанию быстрый "
                             "читает только латиницу)")
    args = parser.parse_args()

    _setup_logging()

    running = _other_instances()
    if running:
        logger.error(f"Уже запущен экземпляр run_voice.py: PID {running}. "
                     "Остановите его или не запускайте второй раз — иначе "
                     "переводчики слышат друг друга.")
        return 1

    from tts_engine import TTSEngine
    from settings import Settings

    settings = Settings()
    engine = TTSEngine(settings)
    engine.set_voice("en-US-BrianMultilingualNeural")  # EN+RU, один тембр
    engine.rate = 0

    from voice_translator import VoiceTranslator
    vt = VoiceTranslator()
    vt.tts = None if args.no_tts else engine

    # Переводчик ОБЯЗАТЕЛЕН: без него VoiceTranslator падал в офлайн-словарь,
    # который переводит пословно («Are you bringing the map?» ->
    # «являются Ю ПРИВЕДЕНИЕ карта»).
    # Цепочка: Zen (free, без ключа) -> Google -> DeepL -> offline.
    # OrcaRouter (404) и OpenRouter (403) удалены из проекта 26.09.2026.
    translator = None
    try:
        from translator import Translator, ZEN_MODELS
        translator = Translator()
        # Настройки лежат во вложенном словаре translation.*
        # Раньше читался несуществующий settings.translator_api_key, и
        # try/except молча съедал ошибку -> перевод шёл через мусорный Zen.
        zen_model = (settings.get("translation.zen_model", "") or "").strip()
        cfg = {
            "api_key": settings.get("translation.api_key", ""),
            "model": settings.get("translation.model", ""),
            # Пустая zen_model -> Translator сам берёт проверенную
            # space-bunny-free из ZEN_MODELS.
            "zen_model": zen_model,
        }
        translator.configure(**cfg)
        # Для озвучки словарь отключаем: пословный перевод хуже молчания
        if hasattr(translator, "allow_offline_dict"):
            translator.allow_offline_dict = False
        vt.translator = translator
        logger.info("Переводчик: Zen/%s (без ключа), fallback Google -> DeepL",
                    zen_model or ZEN_MODELS[0])
    except Exception as e:
        logger.warning(f"Сетевой переводчик недоступен ({e}) — будет офлайн-словарь")

    vt.set_languages("auto", "ru")   # авто-определение языка речи
    vt.set_dual_mode(False)   # озвучиваем только перевод
    vt.set_vad(True)
    if args.model:
        vt.set_model(args.model)

    if args.exe:
        pid, name = _pick_target_exe(args.exe)
        if pid:
            vt.set_target_pid(pid)
            logger.info(f"Целевой процесс по --exe: {name} (PID={pid})")
        else:
            logger.warning(f"Процесс {args.exe} не найден — будет авто-определение")

    def on_heard(text, lang="", speaker=""):
        logger.info("[HEARD]  [%s] %s  <%s>", lang, text, speaker or "?")

    def on_text(en, ru, lang="", speaker=""):
        # ru приходит позже оригинала (перевод идёт в фоне), поэтому
        # пока ru пуст, печатаем оригинал, а не пустую строку.
        if ru and ru.strip():
            logger.info("[RU]     %s", ru)
        else:
            logger.info("[ORIG]   %s", en)

    vt.on_heard = on_heard
    vt.on_text = on_text

    if args.subtitles:
        # Включаем ДО start(), чтобы поток субтитров поднялся сразу.
        vt.set_subtitles(True, settings, fast_ocr=not args.sub_ocr_slow)
        logger.info("Субтитры с экрана включены: фолбэк без голоса + "
                    "уточнение распознавания")

    logger.info("Запуск VoiceTranslator (Whisper грузится в фоне)...")
    vt.start()

    from game_audio_capture import game_audio
    time.sleep(1.0)
    if game_audio._game_pid:
        mode = "по процессу" if game_audio._proc_capture else "системный loopback"
        logger.info("Источник звука: %s (PID=%s), захват: %s",
                    game_audio._game_name, game_audio._game_pid, mode)
    else:
        logger.warning("Активный источник не найден — пишем всё, что слышно в системе")

    logger.info("Готово. Ctrl+C — остановка.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Остановка...")
    finally:
        try:
            vt.stop()
        except Exception as e:
            logger.warning(f"stop error: {e}")
        logger.info("Завершено.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
