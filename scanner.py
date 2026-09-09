"""
Базовый OCR сканер — захват экрана, детекция изменений, распознавание текста.
LiveScanner наследуется от этого класса и добавляет TTS стриминг.
"""
import asyncio
import logging
import os
import time
from typing import Optional, Callable
from PIL import Image
import numpy as np
import cv2

logger = logging.getLogger(__name__)


class ScreenScanner:
    """Базовый сканер экрана — детекция изменений и OCR."""

    def __init__(self, ocr_engine, settings):
        self.ocr_engine = ocr_engine
        self.settings = settings
        self.running = False
        self.paused = False

        # Параметры сканирования
        self.interval_ms = settings.get("ocr.scan_interval_ms", 2000)
        self.detect_changes = settings.get("ocr.detect_changes", True)
        self.change_threshold = settings.get("ocr.change_threshold", 0.05)
        self.use_gpu = settings.get("ocr.use_gpu", True)

        # Кэш для детекции изменений
        self.last_frame_hash = None
        self.last_text = ""

        # Умный кэш текста: normalized_text -> timestamp последнего появления
        self._text_cache = {}
        self._cache_ttl = 1.0
        self._cache_cleanup_interval = 5.0
        self._last_cache_cleanup = 0.0

        # Потоки
        self._scan_task = None
        self._last_scan_time = 0.0

        # Callback'и
        self.on_text_detected: Optional[Callable[[str], None]] = None
        self.on_scan_started: Optional[Callable[[], None]] = None
        self.on_scan_stopped: Optional[Callable[[], None]] = None
        self.on_scan_timing: Optional[Callable[[float, str, float], None]] = None  # (seconds, text_or_empty, confidence)

        # OCR логирование (simple mode)
        self._ocr_log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ocr_logs_simple")
        os.makedirs(self._ocr_log_dir, exist_ok=True)
        self._ocr_log_counter = 0

        logger.info("ScreenScanner инициализирован")

    def _compute_frame_hash(self, img: Image.Image) -> int:
        """Вычисление хеша кадра для детекции изменений."""
        try:
            arr = np.array(img)
            h, w = arr.shape[:2]
            y1, y2 = h // 4, h * 3 // 4
            x1, x2 = w // 4, w * 3 // 4
            center = arr[y1:y2, x1:x2]
            arr_small = cv2.resize(center, (64, 64))
            third = arr_small.shape[1] // 3
            avg1 = arr_small[:, :third].mean()
            avg2 = arr_small[:, third:2*third].mean()
            avg3 = arr_small[:, 2*third:].mean()
            return hash((int(avg1), int(avg2), int(avg3)))
        except Exception as e:
            logger.error(f"Ошибка вычисления хеша кадра: {e}")
            return hash("")

    def _detect_changes(self, img: Image.Image) -> bool:
        """Проверка изменений в области сканирования."""
        if not self.detect_changes:
            return True

        current_hash = self._compute_frame_hash(img)

        if self.last_frame_hash is None:
            self.last_frame_hash = current_hash
            return True

        changed = (current_hash != self.last_frame_hash)
        if changed:
            self.last_frame_hash = current_hash
            logger.debug("Обнаружены изменения в кадре")
        return changed

    def _text_changed(self, text: str) -> bool:
        """Проверка изменения текста — кэш + похожесть."""
        if not text or not text.strip():
            return False

        normalized = ' '.join(text.strip().lower().split())
        normalized_no_punct = normalized.rstrip('.,!?:;-')

        if len(normalized_no_punct) <= 3:
            return False

        now = time.time()

        if now - self._last_cache_cleanup > self._cache_cleanup_interval:
            self._cleanup_text_cache(now)

        if normalized_no_punct in self._text_cache:
            self._text_cache[normalized_no_punct] = now
            return False

        if self.last_text:
            sim = self._similarity(normalized_no_punct, self.last_text)
            if sim > 0.85:
                if len(normalized_no_punct) > len(self.last_text):
                    self._text_cache[normalized_no_punct] = now
                    self.last_text = normalized_no_punct
                return False

        for cached_text in self._text_cache:
            if normalized_no_punct in cached_text or cached_text in normalized_no_punct:
                self._text_cache[cached_text] = now
                return False

        self._text_cache[normalized_no_punct] = now
        self.last_text = normalized_no_punct
        return True

    @staticmethod
    def _similarity(a: str, b: str) -> float:
        """Простое сравнение строк через longest common substring ratio."""
        if not a or not b:
            return 0.0
        if a == b:
            return 1.0
        max_len = max(len(a), len(b))
        if abs(len(a) - len(b)) > max_len * 0.3:
            return 0.0
        la, lb = len(a), len(b)
        if la < lb:
            a, b = b, a
            la, lb = lb, la
        prev = [0] * (lb + 1)
        max_len_found = 0
        for i in range(1, la + 1):
            cur = [0] * (lb + 1)
            for j in range(1, lb + 1):
                if a[i-1] == b[j-1]:
                    cur[j] = prev[j-1] + 1
                    if cur[j] > max_len_found:
                        max_len_found = cur[j]
            prev = cur
        return max_len_found / max_len if max_len else 0.0

    def _cleanup_text_cache(self, now: float):
        """Удаляет из кэша тексты которые не появлялись дольше TTL секунд."""
        expired = [k for k, t in self._text_cache.items() if now - t > self._cache_ttl]
        for k in expired:
            del self._text_cache[k]
        self._last_cache_cleanup = now
        if expired:
            logger.debug(f"Кэш очищен: удалено {len(expired)} записей, осталось {len(self._text_cache)}")

    def _get_text_delta(self, new_text: str) -> str:
        """Возвращает только НОВУЮ часть текста (дельту)."""
        if not new_text or not new_text.strip():
            return ""
        old = (self.last_text or "").strip()
        new = new_text.strip()
        if old and new.startswith(old):
            delta = new[len(old):].strip()
            return delta
        return new

    def _on_new_text(self, text: str):
        """Хук для подклассов — вызывается при обнаружении нового текста.
        По умолчанию ничего не делает. LiveScanner переопределяет для TTS."""
        pass

    def _save_ocr_result(self, raw_text: str, confidence: float, scan_time: float):
        """Сохраняет каждый OCR результат в отдельный txt файл для анализа."""
        try:
            self._ocr_log_counter += 1
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            filename = f"ocr_{timestamp}_{self._ocr_log_counter:04d}.txt"
            filepath = os.path.join(self._ocr_log_dir, filename)

            normalized = ""
            if raw_text and raw_text.strip():
                normalized = ' '.join(raw_text.strip().lower().split())

            is_new = False
            if raw_text and raw_text.strip():
                is_new = self._text_changed(raw_text)

            with open(filepath, "w", encoding="utf-8") as f:
                f.write(f"=== OCR Result #{self._ocr_log_counter} (Simple Mode) ===\n")
                f.write(f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"Scan time: {scan_time:.3f}s\n")
                f.write(f"Confidence: {confidence:.1f}%\n")
                f.write(f"Is new text: {is_new}\n")
                f.write(f"---\n")
                f.write(f"RAW TEXT:\n{raw_text or '(empty)'}\n")
                f.write(f"---\n")
                f.write(f"NORMALIZED:\n{normalized or '(empty)'}\n")
                f.write(f"---\n")
                f.write(f"LAST TEXT:\n{self.last_text or '(empty)'}\n")
                f.write(f"---\n")
                f.write(f"TEXT CACHE ({len(self._text_cache)} items):\n")
                for i, (cached, ts) in enumerate(sorted(self._text_cache.items(), key=lambda x: -x[1])[:10]):
                    f.write(f"  [{i}] {cached}\n")

            logger.debug(f"[OCR-LOG] Saved: {filename}")
        except Exception as e:
            logger.warning(f"[OCR-LOG] Failed to save: {e}")

    async def _scan_loop(self):
        """Основной цикл сканирования."""
        logger.info("Запуск цикла сканирования")

        while self.running:
            if self.paused:
                await asyncio.sleep(0.1)
                continue

            try:
                # Точное ожидание до следующего скана
                now = time.time() * 1000
                elapsed = now - self._last_scan_time
                if elapsed < self.interval_ms:
                    wait_sec = (self.interval_ms - elapsed) / 1000.0
                    await asyncio.sleep(min(wait_sec, 0.5))
                    continue

                self._last_scan_time = time.time() * 1000

                # Захват экрана + замер времени
                t_scan_start = time.time()
                img = self.ocr_engine.capture_region()
                if img is None:
                    await asyncio.sleep(0.1)
                    continue

                # Детекция изменений
                if self.detect_changes:
                    if not self._detect_changes(img):
                        logger.debug("Изменений в кадре нет, пропускаем OCR")
                        await asyncio.sleep(0.05)
                        continue
                else:
                    logger.debug("Детекция изменений отключена, сканируем всегда")

                # Распознавание текста
                result = await self.ocr_engine.recognize_async(img)
                text = result[0] if isinstance(result, tuple) else result
                confidence = result[1] if isinstance(result, tuple) and len(result) > 1 else 0.0
                scan_seconds = time.time() - t_scan_start

                # Логирование OCR результата
                self._save_ocr_result(text, confidence, scan_seconds)

                # Проверка изменения текста
                if text and self._text_changed(text):
                    logger.debug(f"Новый текст: {text[:50]}...")

                    # Вызов callback'а (GUI)
                    if self.on_text_detected:
                        self.on_text_detected(text)

                    # Хук для подклассов (TTS и т.д.)
                    self._on_new_text(text)

                # Индикатор времени сканирования (всегда, даже без нового текста)
                if self.on_scan_timing:
                    self.on_scan_timing(scan_seconds, text or "", confidence)

            except Exception as e:
                logger.error(f"Ошибка в цикле сканирования: {e}")
                await asyncio.sleep(1)

        logger.info("Цикл сканирования остановлен")

    async def start(self):
        """Запуск сканирования."""
        if self.running:
            logger.warning("Сканирование уже запущено")
            return

        self.running = True
        self.paused = False

        if self.on_scan_started:
            self.on_scan_started()

        self._scan_task = asyncio.create_task(self._scan_loop())
        logger.info("ScreenScanner запущен")

    async def stop(self):
        """Остановка сканирования."""
        if not self.running:
            return

        self.running = False

        if self._scan_task:
            self._scan_task.cancel()
            try:
                await self._scan_task
            except asyncio.CancelledError:
                pass

        if self.on_scan_stopped:
            self.on_scan_stopped()

        logger.info("ScreenScanner остановлен")

    async def toggle(self):
        """Переключение состояния сканирования."""
        if self.running:
            await self.stop()
        else:
            await self.start()

    def pause(self):
        """Пауза сканирования."""
        self.paused = True
        logger.debug("Сканирование на паузе")

    def resume(self):
        """Продолжение сканирования."""
        self.paused = False
        logger.debug("Сканирование продолжено")

    def toggle_pause(self):
        """Переключение паузы."""
        if self.paused:
            self.resume()
        else:
            self.pause()

    def set_interval(self, interval_ms: int):
        """Установка интервала сканирования."""
        self.interval_ms = interval_ms
        self.settings.set("ocr.scan_interval_ms", interval_ms)
        logger.info(f"Интервал сканирования: {interval_ms}ms")

    def set_detect_changes(self, enabled: bool):
        """Включение/выключение детекции изменений."""
        self.detect_changes = enabled
        self.settings.set("ocr.detect_changes", enabled)
        logger.info(f"Детекция изменений: {'вкл' if enabled else 'выкл'}")

    def set_region(self, x: int, y: int, width: int, height: int):
        """Установка области сканирования."""
        self.ocr_engine.set_region(x, y, width, height)
        self.last_frame_hash = None
        logger.info(f"Область сканирования: x={x}, y={y}, w={width}, h={height}")

    def clear_cache(self):
        """Очистка кэша текста."""
        self._text_cache.clear()
        self.last_text = ""
        self.last_frame_hash = None
        logger.info("Кэш текста очищен")

    def get_status(self) -> dict:
        """Получение статуса сканера."""
        return {
            "running": self.running,
            "paused": self.paused,
            "interval_ms": self.interval_ms,
            "detect_changes": self.detect_changes,
            "last_text": self.last_text,
            "last_scan_time": self._last_scan_time,
        }
