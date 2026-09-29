# -*- coding: utf-8 -*-
"""Работа с процессами: поиск по PID / exe, мониторинг запущенной игры."""
import logging

logger = logging.getLogger(__name__)

try:
    import psutil
    _PSUTIL_OK = True
except ImportError:
    psutil = None
    _PSUTIL_OK = False
    logger.warning("[PROCESS] psutil не установлен — мониторинг процессов отключён")


def available():
    return _PSUTIL_OK


def list_processes(include_path=False):
    """Возвращает список процессов: [(pid, name[, exe]), ...].

    Системные/бездоступные процессы пропускаются.
    """
    if not _PSUTIL_OK:
        logger.debug("[PROCESS] psutil недоступен, возвращаю пустой список")
        return []
    procs = []
    skipped = 0
    for p in psutil.process_iter(["pid", "name", "exe", "username"]):
        try:
            info = p.info
            name = info.get("name") or ""
            if not name:
                continue
            exe = info.get("exe") or ""
            if include_path:
                procs.append((info["pid"], name, exe))
            else:
                procs.append((info["pid"], name))
        except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
            skipped += 1
            continue
    procs.sort(key=lambda x: x[0])
    logger.debug(f"[PROCESS] Найдено {len(procs)} процессов, пропущено {skipped}")
    return procs


def get_process(pid):
    """Возвращает psutil.Process или None."""
    if not _PSUTIL_OK:
        return None
    try:
        p = psutil.Process(int(pid))
        logger.debug(f"[PROCESS] Процесс PID {pid} найден: {p.name()}")
        return p
    except (psutil.NoSuchProcess, ValueError, TypeError) as e:
        logger.debug(f"[PROCESS] Процесс PID {pid} не найден: {e}")
        return None


def process_info(pid):
    """Краткая инфа о процессе для GUI: name, exe, status."""
    p = get_process(pid)
    if not p:
        logger.warning(f"[PROCESS] PID {pid}: процесс не найден")
        return None
    try:
        name = p.name()
    except (psutil.AccessDenied, psutil.NoSuchProcess):
        name = "?"
        logger.debug(f"[PROCESS] PID {pid}: нет доступа к имени")
    try:
        exe = p.exe()
    except (psutil.AccessDenied, psutil.NoSuchProcess):
        exe = ""
    try:
        is_running = p.is_running()
    except psutil.NoSuchProcess:
        is_running = False
    logger.info(f"[PROCESS] PID {pid}: {name}, exe={exe}, running={is_running}")
    return {"pid": pid, "name": name, "exe": exe, "running": is_running}


def find_processes_by_exe(exe_name):
    """Поиск процессов по имени exe (без учёта регистра, частичное совпадение)."""
    needle = (exe_name or "").lower().strip()
    if not needle:
        return []
    if not _PSUTIL_OK:
        return []
    matches = []
    for p in psutil.process_iter(["pid", "name"]):
        try:
            name = (p.info.get("name") or "").lower()
            if needle in name:
                matches.append((p.info["pid"], p.info.get("name") or "?"))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    logger.info(f"[PROCESS] Поиск '{exe_name}': найдено {len(matches)} совпадений")
    return matches


def is_running(pid):
    """Жив ли процесс с этим PID."""
    p = get_process(pid)
    if not p:
        return False
    try:
        running = p.is_running()
        logger.debug(f"[PROCESS] PID {pid} running={running}")
        return running
    except psutil.NoSuchProcess:
        logger.debug(f"[PROCESS] PID {pid} завершился")
        return False
