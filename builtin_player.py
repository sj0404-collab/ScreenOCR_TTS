"""
Встроенный аудиоплеер — Windows MCI через ctypes.
Никаких внешних процессов — всё внутри приложения.
"""
import ctypes
import ctypes.wintypes
import logging
import os
import tempfile
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# Windows MCI API
winmm = ctypes.windll.winmm


class MCI:
    """Обёртка над Windows MCI (Media Control Interface)."""

    @staticmethod
    def send(command: str) -> str:
        buf = ctypes.create_string_buffer(512)
        result = winmm.mciSendStringA(command.encode("utf-8"), buf, 512, 0)
        return buf.value.decode("utf-8", errors="replace") if buf.value else ""

    @staticmethod
    def send_no_output(command: str) -> bool:
        return winmm.mciSendStringA(command.encode("utf-8"), None, 0, 0) == 0


class BuiltInPlayer:
    """Встроенный плеер — MP3/WAV через Windows MCI.

    Никаких внешних процессов. Один MCI-алиас, команды через ctypes.
    """

    def __init__(self):
        self._alias = "screen_ocr_tts"
        self._opened = False
        self._lock = threading.Lock()
        self._current_file = None

    def _ensure_opened(self, file_path: str):
        """Открыть файл в MCI если ещё не открыт или другой файл."""
        abs_path = os.path.abspath(file_path)
        if self._opened and self._current_file == abs_path:
            return True
        self._close()
        safe_path = abs_path.replace("/", "\\")
        cmd = f'open "{safe_path}" type mpegvideo alias {self._alias}'
        if MCI.send_no_output(cmd):
            self._opened = True
            self._current_file = abs_path
            return True
        # Пробуем как WAV
        cmd = f'open "{safe_path}" type waveaudio alias {self._alias}'
        if MCI.send_no_output(cmd):
            self._opened = True
            self._current_file = abs_path
            return True
        logger.error(f"[MCI] Не удалось открыть: {safe_path}")
        return False

    def play(self, file_path: str, wait: bool = False) -> bool:
        """Воспроизвести аудиофайл."""
        with self._lock:
            if not self._ensure_opened(file_path):
                return False
            MCI.send_no_output(f"seek {self._alias} to start")
            if wait:
                MCI.send(f"play {self._alias} wait")
            else:
                MCI.send_no_output(f"play {self._alias}")
            logger.debug(f"[MCI] play: {os.path.basename(file_path)}")
            return True

    def stop(self):
        """Остановить воспроизведение."""
        with self._lock:
            if self._opened:
                MCI.send_no_output(f"stop {self._alias}")

    def pause(self):
        """Пауза."""
        with self._lock:
            if self._opened:
                MCI.send_no_output(f"pause {self._alias}")

    def resume(self):
        """Продолжить после паузы."""
        with self._lock:
            if self._opened:
                MCI.send_no_output(f"resume {self._alias}")

    def is_playing(self) -> bool:
        """Проверяет, играет ли сейчас аудио."""
        with self._lock:
            if not self._opened:
                return False
            status = MCI.send(f"status {self._alias} mode")
            return "playing" in status.lower()

    def get_position_ms(self) -> int:
        """Текущая позиция в миллисекундах."""
        with self._lock:
            if not self._opened:
                return 0
            pos_str = MCI.send(f"status {self._alias} position")
            try:
                return int(pos_str.strip())
            except ValueError:
                return 0

    def get_length_ms(self) -> int:
        """Общая длина трека в миллисекундах."""
        with self._lock:
            if not self._opened:
                return 0
            len_str = MCI.send(f"status {self._alias} length")
            try:
                return int(len_str.strip())
            except ValueError:
                return 0

    def wait_finish(self, timeout: float = 120.0):
        """Блокирующее ожидание завершения воспроизведения."""
        start = time.monotonic()
        while self.is_playing():
            time.sleep(0.05)
            if time.monotonic() - start > timeout:
                break

    def _close(self):
        if self._opened:
            MCI.send_no_output(f"close {self._alias}")
            self._opened = False
            self._current_file = None

    def close(self):
        """Закрыть MCI-алиас."""
        with self._lock:
            self._close()

    def __del__(self):
        self.close()


# Глобальные экземпляры
_player = None


def get_player() -> BuiltInPlayer:
    global _player
    if _player is None:
        _player = BuiltInPlayer()
    return _player
