"""
Screen OCR + TTS — Live mode for NTE game.
Speaks ONLY when game window text changes. Hotkeys to control.
"""
import sys
import os
import ctypes
from ctypes import wintypes

os.chdir(os.path.dirname(os.path.abspath(__file__)))

try:
    hwnd_console = ctypes.windll.kernel32.GetConsoleWindow()
    if hwnd_console:
        ctypes.windll.user32.ShowWindow(hwnd_console, 0)
except Exception:
    pass

import logging
import tempfile
import time
import asyncio
import threading
import hashlib
import json
import keyboard

HWND_GAME = 787670
SCAN_MS = 400

logging.basicConfig(
    filename="live_mode.log", encoding="utf-8", level=logging.INFO,
    format="%(asctime)s - %(message)s"
)
log = logging.getLogger("live")


class GameState:
    """Tracks what was spoken for the game window only."""

    def __init__(self):
        self._spoken_hash = ""
        self._spoken_text = ""
        self._spoken_count = 0
        self._same_seen = 0

    def is_new(self, text: str) -> bool:
        h = self._hash(text)
        if h == self._spoken_hash:
            self._same_seen += 1
            return False
        self._same_seen = 0
        return True

    def mark_spoken(self, text: str):
        self._spoken_hash = self._hash(text)
        self._spoken_text = text
        self._spoken_count += 1
        self._same_seen = 0

    @staticmethod
    def _hash(text: str) -> str:
        clean = " ".join(text.lower().split())
        return hashlib.md5(clean.encode()).hexdigest()[:12]


class LiveRunner:
    def __init__(self):
        self.running = False
        self._tts = None
        self._reader = None
        self._game = GameState()
        self._loop = asyncio.new_event_loop()
        self._region = self._load_region()

    def _load_region(self):
        """Load first region from config.json for OCR cropping."""
        try:
            cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            regions = cfg.get("regions", [])
            if regions:
                r = regions[0]
                return (r.get("x", 0), r.get("y", 0),
                        r.get("width", 1366), r.get("height", 768))
        except Exception:
            pass
        return None

    def start(self):
        if self.running:
            return
        self.running = True
        threading.Thread(target=self._run_loop, daemon=True).start()
        threading.Thread(target=self._scan_thread, daemon=True).start()
        log.info("STARTED")
        print("[START] Live OCR+TTS over NTE")
        print("[INFO] Hotkeys disabled (F-keys conflict with games)")

    def stop(self):
        self.running = False
        if self._tts:
            self._tts.force_stop()
        log.info("STOPPED")
        print("[STOP] Live mode off")

    def _run_loop(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _init_tts(self):
        from settings import Settings
        from tts_engine import TTSEngine
        settings = Settings()
        self._tts = TTSEngine(settings)
        print(f"[TTS] {self._tts.voice} ({self._tts.voice_type})")

    def _init_ocr(self):
        import easyocr
        cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            lang_str = cfg.get("ocr", {}).get("language", "rus+eng")
        except Exception:
            lang_str = "rus+eng"

        # Map UI codes to EasyOCR language lists
        lang_map = {
            "rus+eng": ["ru", "en"],
            "ru": ["ru"],
            "eng": ["en"],
            "jpn": ["ja"],
            "jpn+eng": ["ja", "en"],
        }
        langs = lang_map.get(lang_str, ["ru", "en"])
        print(f"[OCR] Languages: {langs}")
        self._reader = easyocr.Reader(langs, gpu=False, verbose=False)
        print("[OCR] Ready")

    def _is_game_focused(self) -> bool:
        fg = ctypes.windll.user32.GetForegroundWindow()
        return fg == HWND_GAME

    def _capture_game(self):
        """Bring game to foreground, then grab screen pixels at game rect.
        If a region is defined in config, crop to only that area."""
        from PyQt6.QtGui import QGuiApplication

        # Bring game window to front
        ctypes.windll.user32.SetForegroundWindow(HWND_GAME)
        time.sleep(0.15)

        rect = wintypes.RECT()
        ctypes.windll.user32.GetWindowRect(HWND_GAME, ctypes.byref(rect))
        x, y = rect.left, rect.top
        w = rect.right - rect.left
        h = rect.bottom - rect.top
        if w <= 0 or h <= 0:
            return None

        screen = QGuiApplication.primaryScreen()
        if not screen:
            return None

        img = screen.grabWindow(0, x, y, w, h).toImage()

        # Crop to region if defined
        if self._region:
            rx, ry, rw, rh = self._region
            # Region coords are relative to game window
            rx = max(0, min(rx, w))
            ry = max(0, min(ry, h))
            rw = max(1, min(rw, w - rx))
            rh = max(1, min(rh, h - ry))
            img = img.copy(rx, ry, rw, rh)

        return img

    def _scan_thread(self):
        self._init_tts()
        self._init_ocr()

        if self._region:
            rx, ry, rw, rh = self._region
            print(f"[REGION] OCR limited to: {rw}x{rh} at ({rx},{ry})")
        else:
            print("[REGION] Full window capture")

        skip = {
            'import', 'print', 'save', 'ocr', 'png', 'format',
            'butes', 'screenshots', 'test_path', 'engine',
            'directly', 'captured', 'screenshot', 'conuert',
            'chuf', 'img', 'huf', 'buf', 'test', 'path',
            'running', 'use', 'image', 'build', 'mimo',
            'opencode', 'zen', 'high', 'commands', 'interrupt',
            'esc', 'ctrl', 'continue', 'self', 'last',
            'erroraction', 'silentl', 'process', 'getprocess',
            'start-sleep', 'select-object', 'format-table',
            'foreach', 'powershell', 'command', 'scriptblock',
            'pyqt6', 'qapplication', 'subprocess', 'datetime',
            'encoding', 'utf-8', 'logging', 'getlogger',
            'information', 'settings', 'voice', 'engine',
            'start', 'stop', 'test', 'apply', 'close',
        }

        print("[SCAN] Running — speak new text only")

        while self.running:
            try:
                img = self._capture_game()
                if img is None:
                    time.sleep(0.5)
                    continue

                tmp = os.path.join(tempfile.gettempdir(), "live_scan.png")
                img.save(tmp)

                results = self._reader.readtext(tmp)

                texts = []
                for bbox, text, conf in results:
                    text = text.strip()
                    if conf < 0.5 or len(text) < 3:
                        continue
                    if any(s in text.lower() for s in skip):
                        continue
                    # Must have at least 3 letters
                    alpha = sum(1 for c in text if c.isalpha())
                    if alpha < 3:
                        continue
                    texts.append(text)

                full_text = " ".join(texts)

                if not full_text:
                    time.sleep(SCAN_MS / 1000.0)
                    continue

                # ONLY speak if text in game window changed
                if self._game.is_new(full_text):
                    self._game.mark_spoken(full_text)
                    log.info(f"NEW: {full_text[:120]}")
                    print(f"  >> {full_text[:80]}")

                    try:
                        asyncio.run_coroutine_threadsafe(
                            self._tts.speak(full_text), self._loop
                        )
                    except Exception as e:
                        log.error(f"TTS error: {e}")
                # else: same text, stay silent

                time.sleep(SCAN_MS / 1000.0)

            except Exception as e:
                log.error(f"Error: {e}")
                time.sleep(1)


def main():
    print("=" * 45)
    print("  Screen OCR + TTS — Live (NTE)")
    print("  Hotkeys disabled (F-keys conflict with games)")
    print("=" * 45)

    from PyQt6.QtWidgets import QApplication
    app = QApplication(sys.argv)

    runner = LiveRunner()
    runner.start()

    # Hotkeys disabled (F-keys conflict with games)

    try:
        app.exec()
    except KeyboardInterrupt:
        pass
    finally:
        runner.stop()


if __name__ == "__main__":
    main()
