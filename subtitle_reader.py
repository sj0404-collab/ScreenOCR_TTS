# -*- coding: utf-8 -*-
"""
Чтение субтитров с экрана (OCR) для голосового конвейера.

Зачем:
  1) ФОЛБЭК. В играх бывает, что реплику не слышно (сюжетная сцена, приглушённый
     диалог, персонаж молчит), но субтитры на экране есть. Тогда голосовой
     конвейер молчит, хотя текст есть. Этот модуль читает субтитры и
     озвучивает их.
  2) УТОЧНЕНИЕ. Если голос и субтитры есть одновременно, субтитры точнее
     Whisper (правильные имена, кавычки, знаки препинания), поэтому они
     используются как эталонный текст.

Модуль самодостаточный: свой OCRWrapper и свой кадр, чтобы не мешать
OCR-сканеру GUI. Работает по нижней полосе экрана (там, где субтитры).
"""
import logging
import re
import threading
import time

logger = logging.getLogger(__name__)


def _norm(t: str) -> str:
    t = (t or "").lower()
    t = re.sub(r"[^\w\s]", " ", t, flags=re.UNICODE)
    return re.sub(r"\s+", " ", t).strip()


class SubtitleReader:
    """Читает субтитры из нижней полосы экрана окна ИГРЫ.

    region_ratio — (x, y, w, h) в долях окна. По умолчанию нижние 26 %:
    там почти всегда лежат субтитры, и туда же попадает меньше всего
    игрового интерфейса.

    ВАЖНО: захватывается именно окно целевого процесса (target_pid),
    а не весь экран. Иначе в кадр попадают терминал и другие окна, и
    «субтитры» читаются с них — вплоть до озвучки команд вида
    «Esc interrupt» / «Ctrl+P command».
    """

    def __init__(self, settings=None, region_ratio=(0.06, 0.66, 0.88, 0.17),
                 engine: str = "", target_pid: int = None,
                 require_foreground: bool = True,
                 auto_region: bool = True, fast_ocr: bool = True):
        self._settings = settings
        self._ratio = region_ratio
        self._engine = engine
        self._target_pid = int(target_pid) if target_pid else None
        self._hwnd = None
        self._hwnd_checked_at = 0.0
        self._require_fg = bool(require_foreground)
        self._warned_fg = False
        self._warned_nohwnd = False
        # Авто-подстройка области под строку субтитров
        self._auto_region_enabled = bool(auto_region)
        self._region_checked_at = 0.0
        self._region_check_sec = 2.5
        # Зелёные рамки из GUI: если заданы, сканируем только их
        self._regions = []
        # Ограничения размера кадра: OCR стоит ~5-20 c на 590x109,
        # а субтитры занимают узкую полосу — не тратим время на лишнее
        self._max_band_h = 72
        self._max_band_w = 760
        self._ocr = None
        self._fast_ocr = None
        self._fast_ocr_failed = False
        self._fast_ocr_enabled = bool(fast_ocr)
        self._lock = threading.Lock()
        self._mss = None
        self._mss_lock = threading.Lock()
        self._failed = False
        self.last_text = ""
        self.last_change_at = 0.0
        self._warned_fullscreen = False

    # ── Окно игры: ищем HWND по PID (с перепроверкой раз в 5 c) ──
    def _auto_pid(self):
        """Авто-режим: PID активного окна (игра обычно в фокусе)."""
        try:
            import ctypes
            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            if not hwnd:
                return None
            pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(int(hwnd), ctypes.byref(pid))
            return pid.value or None
        except Exception:
            return None

    def _find_hwnd(self):
        """HWND окна целевого процесса (самое большое видимое)."""
        if not self._target_pid:
            # Целевой процесс не задан: берём окно, которое сейчас в фокусе
            pid = self._auto_pid()
            if pid:
                self._target_pid = pid
                logger.info(f"[SubtitleReader] авто-режим: окно PID {pid}")
            else:
                return None
        now = time.time()
        if self._hwnd and now - self._hwnd_checked_at < 5.0:
            return self._hwnd
        self._hwnd_checked_at = now
        try:
            import ctypes
            from ctypes import wintypes as wt
            user32 = ctypes.windll.user32
            best, best_area = None, 0
            ENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

            def cb(hwnd, _):
                nonlocal best, best_area
                pid = ctypes.c_ulong()
                user32.GetWindowThreadProcessId(int(hwnd), ctypes.byref(pid))
                if pid.value != self._target_pid:
                    return True
                if not user32.IsWindowVisible(hwnd):
                    return True
                rect = wt.RECT()
                user32.GetWindowRect(int(hwnd), ctypes.byref(rect))
                area = (rect.right - rect.left) * (rect.bottom - rect.top)
                # берём самое большое видимое окно: главное окно игры,
                # а не служебные окошки в 1 пиксель
                if area > best_area:
                    best, best_area = int(hwnd), area
                return True

            user32.EnumWindows(ENUMPROC(cb), 0)
            if best and best != self._hwnd:
                logger.info(f"[SubtitleReader] окно игры PID "
                            f"{self._target_pid}: hwnd={best}")
            self._hwnd = best
        except Exception as e:
            logger.debug(f"[SubtitleReader] поиск окна не удался: {e}")
            self._hwnd = None
        return self._hwnd

    def _window_rect(self):
        """Прямоугольник окна игры в экранных координатах или None."""
        hwnd = self._find_hwnd()
        if not hwnd:
            return None
        try:
            import ctypes
            from ctypes import wintypes as wt
            user32 = ctypes.windll.user32
            rect = wt.RECT()
            user32.GetWindowRect(int(hwnd), ctypes.byref(rect))
            x, y = rect.left, rect.top
            w, h = rect.right - rect.left, rect.bottom - rect.top
            if w <= 0 or h <= 0:
                return None
            return {"left": int(x), "top": int(y),
                    "width": int(w), "height": int(h)}
        except Exception as e:
            logger.debug(f"[SubtitleReader] GetWindowRect не удался: {e}")
            return None

    # ── Ленивая инициализация: OCR-движки тяжёлые, поднимаем при первом чтении ──
    def _ensure_ocr(self):
        if self._ocr is not None or self._failed:
            return self._ocr
        try:
            from ocr_wrapper import OCRWrapper
            settings = self._settings
            if settings is None:
                # OCRWrapper требует настройки; без них он падает на .get().
                from settings import Settings
                settings = Settings()
            self._ocr = OCRWrapper(settings)
            if self._engine:
                try:
                    self._ocr.set_engine(self._engine)
                except Exception:
                    pass
            logger.info("[SubtitleReader] OCR готов для чтения субтитров")
        except Exception as e:
            self._failed = True
            logger.warning(f"[SubtitleReader] OCR недоступен: {e}")
        return self._ocr

    def _band(self, box):
        """Нижняя полоса внутри прямоугольника (окна игры или рамки).

        ВАЖНО: mss.mss.grab() ждёт ключи 'left'/'top', а не 'x'/'y'
        (мониторы описаны как {'left','top','width','height'}).

        Высота и ширина ограничены: стоимость OCR пропорциональна площади,
        а локальный движок на CPU и так медленный. Субтитры — узкая
        полоса в центре, отдавать ему пол-экрана бессмысленно.
        """
        sw, sh = box["width"], box["height"]
        left = int(box["left"] + sw * self._ratio[0])
        top = int(box["top"] + sh * self._ratio[1])
        w = int(sw * self._ratio[2])
        h = int(sh * self._ratio[3])
        w = max(1, min(w, box["left"] + sw - left))
        h = max(1, min(h, box["top"] + sh - top))
        if self._max_band_h and h > self._max_band_h:
            h = self._max_band_h
        if self._max_band_w and w > self._max_band_w:
            # центрируем по горизонтали: субтитры по центру кадра
            cx = left + w // 2
            w = self._max_band_w
            left = max(box["left"], cx - w // 2)
        return {"left": left, "top": top, "width": w, "height": h}

    def _is_foreground(self, hwnd) -> bool:
        """Окно игры сейчас в фокусе?

        Если нет, поверх игры может лежать ЧУЖОЕ окно (браузер, терминал,
        мессенджер) — и мы прочитаем его текст вместо субтитров. Именно так
        в логах появились «Esc interrupt» и «Ctrl+P command»: кадр снимался
        с чужого окна, а не с игры.

        Отдельно важный случай: hwnd=None (игра перезапустилась, PID сменился,
        окно свёрнуто). Раньше это считалось «всё в порядке» и чтение шло по
        рамке — то есть мы читали всё, что под ней оказалось. Теперь это
        однозначная пауза: нет окна игры — нечего читать.
        """
        if not self._require_fg:
            return True
        if not hwnd:
            if not self._warned_nohwnd:
                self._warned_nohwnd = True
                logger.warning("[SubtitleReader] окно игры не найдено "
                               "(перезапустилась?) — субтитры не читаются")
            return False
        try:
            import ctypes
            fg = ctypes.windll.user32.GetForegroundWindow()
            if fg and int(fg) == int(hwnd):
                if self._warned_fg or self._warned_nohwnd:
                    self._warned_fg = False
                    self._warned_nohwnd = False
                    logger.info("[SubtitleReader] игра снова в фокусе")
                return True
            if not self._warned_fg:
                self._warned_fg = True
                logger.info("[SubtitleReader] игра не в фокусе — субтитры "
                            "приостановлены (иначе читалось бы чужое окно)")
            return False
        except Exception as e:
            logger.debug(f"[SubtitleReader] проверка фокуса не удалась: {e}")
            return True

    def close(self):
        """Освободить ресурсы (mss держит GDI-дескрипторы).

        Без этого при каждом старте/остановке перевода в GUI накапливались
        бы дескрипторы, и на реальном сценарии «включил-выключил 20 раз»
        это уже источник странных ошибок в других окнах.
        """
        with self._mss_lock:
            if self._mss is not None:
                try:
                    self._mss.close()
                except Exception as e:
                    logger.debug(f"[SubtitleReader] mss.close: {e}")
                self._mss = None
        self._ocr = None
        self._fast_ocr = None

    def is_active(self) -> bool:
        """Готовы ли мы читать: окно игры найдено и сейчас в фокусе.

        Используется конвейером: если окно не в фокусе, сверять речь с
        субтитрами бессмысленно (на экране чужое окно) и ждать нечего.
        """
        try:
            hwnd = self._find_hwnd()
            if not hwnd:
                return False
            return self._is_foreground(hwnd)
        except Exception:
            return False

    def _auto_region(self, shot_img) -> None:
        """Найти полосу с субтитрами и обновить область захвата.

        Субтитры в играх не всегда на одном месте: Genshin держит их у нижней
        кромки, другие игры — выше или по центру, а в меню строка диалога
        вообще исчезает. Раньше область была жёсткой долей окна, и субтитры
        то попадали в кадр, то нет.

        Здесь ищем горизонтальные полосы, похожие на текст, и берём самую
        нижнюю группу — обычно это и есть диалог. Область обновляется
        плавно (с ограничением скорости), чтобы кадр не дёргался.
        """
        try:
            import numpy as np
            g = np.asarray(shot_img.convert("L"), dtype=np.float32)
            h, w = g.shape
            # Локальный контраст: текст = резкие перепады яркости
            dx = np.abs(np.diff(g, axis=1))
            row_score = (dx > 42).sum(axis=1)
            rows = row_score > max(6, int(w * 0.02))

            # Группы подряд идущих строк
            groups, cur = [], []
            for y, on in enumerate(rows):
                if on:
                    cur.append(y)
                elif cur:
                    groups.append((cur[0], cur[-1]))
                    cur = []
            if cur:
                groups.append((cur[0], cur[-1]))
            if not groups:
                return

            # Нижние 60 % окна — зона субтитров; не берём HUD у самой кромки
            cand = [g0 for g0 in groups if g0[0] > h * 0.40]
            if not cand:
                return
            bottom = cand[-1]
            top = bottom[0]
            # одна-две строки диалога; берём до 3 строк подряд
            prev = None
            for grp in reversed(cand):
                if prev is not None and grp[1] < prev - 6:
                    break
                top = grp[0]
                prev = grp[0]
                if prev - top > 3 * 24:
                    break

            pad = max(6, int((bottom[1] - bottom[0]) * 0.5))
            new_top = max(0, top - pad)
            new_bot = min(h, bottom[1] + pad)
            if new_bot - new_top < 24:
                return

            ratio = (max(0.0, (new_top - 3) / h), min(1.0, (new_bot + 3) / h))
            cur = self._ratio
            # ограничиваем скорость изменения: не больше 8 % высоты за раз
            if cur and abs(ratio[0] - cur[0]) < 0.08 and \
                    abs(ratio[1] - cur[1]) < 0.08:
                return
            if cur and abs(ratio[0] - cur[0]) < 0.005 and \
                    abs(ratio[1] - cur[1]) < 0.005:
                return
            self._ratio = (max(0.0, ratio[0] - 0.02), min(1.0, ratio[1] + 0.02))
            logger.info(f"[SubtitleReader] область субтитров: "
                        f"{self._ratio[0]:.2f}..{self._ratio[1]:.2f} "
                        f"(строки {top}..{bottom[1]} из {h})")
        except Exception as e:
            logger.debug(f"[SubtitleReader] авто-область не удалась: {e}")

    def set_regions(self, regions):
        """Задать области сканирования (зелёные рамки из GUI).

        Пока пользователь не нарисовал рамку, работаем по окну игры.
        Как только рамка есть — сканируем ТОЛЬКО её: так из кадра
        уходят HUD, панель задач и края экрана, а субтитры остаются.
        Рамки можно перерисовывать на ходу — они перечитываются.
        """
        out = []
        for r in (regions or []):
            try:
                w = int(r.get("width", 0))
                h = int(r.get("height", 0))
                if w > 30 and h > 12:
                    out.append({"x": int(r.get("x", 0)),
                                "y": int(r.get("y", 0)),
                                "width": w, "height": h})
            except Exception:
                continue
        changed = out != getattr(self, "_regions", None)
        self._regions = out
        if changed and out:
            # при смене рамки прошлый текст уже не про эту реплику
            self.last_text = ""
            logger.info(f"[SubtitleReader] области из рамок: {len(out)}")
        return len(out)

    def _boxes(self):
        """Абсолютные прямоугольники для сканирования."""
        regs = getattr(self, "_regions", None)
        if regs:
            return [{"left": r["x"], "top": r["y"],
                     "width": r["width"], "height": r["height"]}
                    for r in regs]
        box = self._window_rect()
        return [box] if box else []

    def _sct(self):
        """Один экземпляр mss на весь процесс.

        Раньше он создавался на каждый кадр: mss при этом падал с
        «tuple index out of range» при последовательных созданиях, и
        субтитры не читались вообще. Плюс создание дорого.
        """
        import mss
        if self._mss is None:
            self._mss = mss.mss()
        return self._mss

    def _grab(self, wide: bool = False, box=None):
        """Кадр полосы ОКНА ИГРЫ (не всего экрана).

        wide=True — широкий кадр нижней половины окна: он нужен для поиска
        строки субтитров (авто-область). В обычном режиме берётся только
        текущая полоса, чтобы OCR тратил меньше времени.
        """
        try:
            import mss
            from PIL import Image
        except Exception as e:
            logger.debug(f"[SubtitleReader] нет mss/PIL: {e}")
            return None
        try:
            # mss не потокобезопасен: захват сериализуем
            with self._mss_lock:
                sct = self._sct()
                if box is None:
                    boxes = self._boxes()
                    if not boxes:
                        # Окно игры не найдено/свернуто. Захват всего экрана
                        # опасен: в кадр попадут терминал и другие окна, и мы
                        # будем «озвучивать» их содержимое. Поэтому молчим.
                        if not self._warned_fullscreen:
                            self._warned_fullscreen = True
                            logger.warning(
                                "[SubtitleReader] окно игры не найдено — "
                                "чтение субтитров приостановлено "
                                "(иначе цеплялся бы чужой текст с экрана)")
                        return None
                    box = boxes[0]
                self._warned_fullscreen = False
                if not self._is_foreground(self._find_hwnd()):
                    return None
                region = self._band(box)
                if wide:
                    # нижние 60 % окна — зона, где ищем строку диалога
                    full_h = box["height"]
                    region = {"left": region["left"],
                              "top": box["top"] + int(full_h * 0.40),
                              "width": region["width"],
                              "height": int(full_h * 0.60)}
                shot = sct.grab(region)
            return Image.frombytes("RGB", shot.size, shot.bgra,
                                   "raw", "BGRX")
        except Exception as e:
            logger.debug(f"[SubtitleReader] grab failed: {e}")
            return None

    def _refresh_region(self) -> None:
        """Периодически искать строку субтитров и подстраивать область.

        Область ищется ВНУТРИ каждой рамки (или окна игры): рамка уже
        отсекает HUD и края, а авто-подстройка находит в ней конкретную
        строку диалога.
        """
        if not self._auto_region_enabled:
            return
        now = time.time()
        if now - self._region_checked_at < self._region_check_sec:
            return
        self._region_checked_at = now
        for b in self._boxes():
            img = self._grab(wide=True, box=b)
            if img is not None:
                self._auto_region(img)
                break

    @staticmethod
    def _looks_like_text(raw: str, conf: float, min_words: int = 1) -> bool:
        """Отсекаем мусор интерфейса: слишком много «слов» из 1-2 букв.

        min_words — минимум слов. Для самостоятельного озвучивания (когда
        голоса нет) ставим 2: одиночное «Settings» / «Inventory» — это
        элемент меню, не реплика. Для уточнения по голосу можно 1.
        """
        t = _norm(raw)
        if len(t) < 3:
            return False
        words = t.split()
        if len(words) > 24:
            return False
        if len(words) < min_words:
            return False
        short = sum(1 for w in words if len(w) <= 1)
        if words and short / len(words) > 0.45:
            return False
        letters = sum(1 for c in t if c.isalpha())
        return letters >= 3

    def _ensure_fast_ocr(self):
        """Быстрый OCR для субтитров: ОДИН движок и БЕЗ детектора.

        OCRWrapper.recognize прогоняет primary + fallback (tflite_cyrillic,
        rapid_ocr, google_lens) — на кадре 590x109 это 42 c, то есть экран
        практически не читается в реальном времени. Один RapidOCREngine
        без детектора даёт тот же кадр за ~0.3 c (ускорение ×15).

        ОГРАНИЧЕНИЕ: эта модель читает латиницу. Кириллицу она не берёт
        (и tflite_cyrillic, который умеет, но тратит 40 c). Поэтому для
        субтитров на русском нужен fast_ocr=False — ценой скорости.
        """
        if self._fast_ocr is not None or self._fast_ocr_failed:
            return self._fast_ocr
        if not self._fast_ocr_enabled:
            return None
        try:
            from rapid_ocr import RapidOCREngine
            self._fast_ocr = RapidOCREngine()
            logger.info("[SubtitleReader] быстрый OCR готов (×15 быстрее, "
                        "латиница; для кириллицы — --sub-ocr-slow)")
        except Exception as e:
            self._fast_ocr_failed = True
            logger.warning(f"[SubtitleReader] быстрый OCR недоступен "
                           f"({e}) — беру общий OCRWrapper")
        return self._fast_ocr

    def _recognize(self, img):
        """Распознать кадр: быстрый путь (без детектора), затем wrapper."""
        eng = self._ensure_fast_ocr()
        if eng is not None:
            try:
                return eng.recognize_fast(img)
            except Exception as e:
                logger.debug(f"[SubtitleReader] быстрый OCR сбой: {e}")
                self._fast_ocr = None
                self._fast_ocr_failed = True
        ocr = self._ensure_ocr()
        if ocr is None:
            return "", 0.0
        return ocr.recognize(img)

    def read(self, standalone: bool = False) -> str:
        """Возвращает НОВЫЙ текст субтитров (или "" если он не изменился).

        standalone=True — озвучиваем сами (голоса нет): тогда нужно минимум
        2 слова, чтобы не прочитать пункт меню. standalone=False — только
        уточняем распознанный голос, можно и одно слово.
        """
        if self._failed:
            return ""
        self._refresh_region()      # держим область на реальной строке
        min_words = 2 if standalone else 1

        # Областей может быть несколько (несколько зелёных рамок) — читаем
        # каждую и берём самую «репличную»: со знаками конца фразы и
        # наибольшим числом слов. Одна рамка с меню не должна вытеснять
        # вторую, где идёт диалог.
        best = ""
        best_rank = -1.0
        for box in self._boxes():
            img = self._grab(box=box)
            if img is None:
                continue
            with self._lock:
                try:
                    # recognize(img) сам применяет пресет и fallback-цепочку;
                    # область захвата тут не нужна — кадр уже вырезан.
                    raw, conf = self._recognize(img)
                except Exception as e:
                    logger.debug(f"[SubtitleReader] recognize failed: {e}")
                    continue
            text = (raw or "").strip()
            # OCR отдаёт блок: в нём бывает и заголовок окна, и служебные
            # символы («X», «Π»). Оставляем только строки-реплики; мусорные
            # однобуквенные и служебные строки отбрасываем.
            kept = []
            for raw_line in text.splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                if self._looks_like_text(line, conf, min_words=min_words):
                    kept.append(line)
            if not kept:
                continue
            cand = " ".join(kept)
            rank = (2.0 if any(c in cand for c in ".?!…") else 0.0) \
                + min(1.0, len(cand.split()) / 8.0)
            if rank > best_rank:
                best, best_rank = cand, rank

        if not best:
            return ""
        n = _norm(best)
        if n == _norm(self.last_text):
            return ""
        logger.info(f"[SubtitleReader] субтитры: {best[:80]!r}")
        self.last_text = best
        self.last_change_at = time.time()
        return best

    def current_age(self) -> float:
        """Сколько секунд прошло с последнего ЧТЕНИЯ текста на экране.

        Не путать с current(): здесь важна давность, а не сам текст —
        решатель по ней понимает, что строка ещё «живая».
        """
        if not self.last_change_at:
            return 999.0
        return max(0.0, time.time() - self.last_change_at)

    def current(self, max_age: float = 3.0) -> str:
        """Свежие субтитры, если они менялись недавно."""
        if not self.last_text:
            return ""
        if time.time() - self.last_change_at > max_age:
            return ""
        return self.last_text
