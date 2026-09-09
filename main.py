"""
Screen OCR + TTS — MORT-style launcher
"""
import sys
import os
import logging
import tempfile
import faulthandler
from PyQt6.QtWidgets import QApplication, QMessageBox
from PyQt6.QtCore import Qt, QTimer

# gui_compact imported FIRST: it pulls QtWebEngineWidgets at module level,
# which Qt requires BEFORE any QCoreApplication/QApplication instance exists.
from gui_compact import CompactWindow

from settings import Settings

LOCK_FILE = os.path.join(tempfile.gettempdir(), "screen_ocr_tts.lock")


def check_already_running():
    try:
        import msvcrt
        lock_fd = open(LOCK_FILE, 'w')
        msvcrt.locking(lock_fd.fileno(), msvcrt.LK_NBLCK, 1)
        return lock_fd
    except (IOError, OSError):
        return None


def setup_logging():
    settings = Settings()
    log_level = settings.get("general.log_level", "INFO")
    level = getattr(logging, log_level.upper(), logging.INFO)
    logger = logging.getLogger()
    logger.setLevel(level)
    logger.handlers.clear()

    class SafeStreamHandler(logging.StreamHandler):
        def emit(self, record):
            try:
                msg = self.format(record)
                stream = self.stream
                stream.write(msg + self.terminator)
                self.flush()
            except UnicodeEncodeError:
                try:
                    stream.write(record.getMessage().encode('utf-8', errors='replace').decode('utf-8') + '\n')
                except Exception:
                    pass
            except Exception:
                self.handleError(record)

    ch = SafeStreamHandler(sys.stdout)
    ch.setLevel(level)
    ch.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
    logger.addHandler(ch)

    # Prevent non-fatal logging errors in threads from killing the process
    logging.raiseExceptions = False

    if settings.get("general.log_enabled", True):
        from logging.handlers import RotatingFileHandler
        fh = RotatingFileHandler(
            'screen_ocr.log', encoding='utf-8', mode='a',
            maxBytes=5 * 1024 * 1024, backupCount=5
        )
        fh.setLevel(level)
        fh.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
        logger.addHandler(fh)


def main():
    lock_fd = check_already_running()
    if lock_fd is None:
        try:
            app = QApplication.instance() or QApplication(sys.argv)
            QMessageBox.warning(None, "Screen OCR + TTS", "Already running!")
        except Exception:
            pass
        sys.exit(0)

    setup_logging()
    logger = logging.getLogger(__name__)

    def _excepthook(exc_type, exc_value, exc_tb):
        import traceback
        logger.error("Unhandled:", exc_info=(exc_type, exc_value, exc_tb))
        traceback.print_exception(exc_type, exc_value, exc_tb)
    sys.excepthook = _excepthook

    # Catch unhandled exceptions in daemon threads (prevents silent crash)
    import threading
    _orig_thread_init = threading.Thread.__init__
    def _safe_thread_init(self, *args, **kwargs):
        _orig_thread_init(self, *args, **kwargs)
        _orig_run = self.run
        def _safe_run():
            try:
                _orig_run()
            except Exception as e:
                logger.error(f"Thread crash: {e}", exc_info=True)
        self.run = _safe_run
    threading.Thread.__init__ = _safe_thread_init

    logger.info("Starting Screen OCR + TTS (MORT style)")

    app = QApplication(sys.argv)
    app.setApplicationName("Screen OCR + TTS")
    app.setStyle("Fusion")

    # ── Hang detector (watchdog) ───────────────────────────
    # Dumps every thread's stack to hang_trace.txt only when the GUI
    # event loop is genuinely stuck (not processing 1s keepalive timer).
    import sys as _sys, threading as _threading, time as _time
    _main_alive = {"t": _time.time()}

    def _mark_alive():
        _main_alive["t"] = _time.time()

    _ka_timer = QTimer()
    _ka_timer.timeout.connect(_mark_alive)
    _ka_timer.start(1000)

    def _hang_watchdog():
        _trace_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "hang_trace.txt")
        while True:
            _time.sleep(3)
            if _time.time() - _main_alive["t"] > 12:
                try:
                    frames = _sys._current_frames()
                    with open(_trace_path, "a", buffering=1) as _f:
                        _f.write("\n=== HANG DETECTED %s ===\n" %
                                 _time.strftime("%Y-%m-%d %H:%M:%S"))
                        for _tid, _fr in frames.items():
                            _f.write(f"\n--- Thread {_tid} ---\n")
                            import traceback as _tb
                            _tb.print_stack(_fr, file=_f)
                except Exception:
                    pass
                break

    _threading.Thread(target=_hang_watchdog, daemon=True).start()

    from PyQt6.QtGui import QPalette, QColor
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#1e1e1e"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#d4d4d4"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#1e1e1e"))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#2d2d30"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#d4d4d4"))
    palette.setColor(QPalette.ColorRole.Button, QColor("#0e639c"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#264f78"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    app.setPalette(palette)

    window = CompactWindow()
    window.setStyleSheet(window._mort_theme())
    window.show()
    window.raise_()
    window.activateWindow()
    window.setFocus()

    logger.info("Ready")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
