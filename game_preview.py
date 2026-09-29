# -*- coding: utf-8 -*-
"""
Game Preview — лёгкое окно предпросмотра захваченной области (только игра).
Без рамок, без рабочего стола, всегда поверх. Режим просмотра.
"""
import logging
import time
from PyQt6.QtWidgets import QWidget, QLabel, QVBoxLayout, QHBoxLayout, QGraphicsOpacityEffect
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QSize
from PyQt6.QtGui import QImage, QPixmap, QFont, QColor, QPainter

logger = logging.getLogger(__name__)


class StatusIcons(QWidget):
    """Плавающая полоска мигающих иконок-индикаторов процесса.

    Иконки:
      🎬 — трансляция (захват кадра)
      🔍 — сканирование (OCR running)
      📦 — буферизация (TTS queue)
      ✅ — распознавание (текст получен)
      🎵 — голос (audio / voice detected)
    """
    BLINK_MS = 400  # скорость мигания

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedHeight(28)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(6)

        self._icons = {}
        self._timers = {}

        defs = [
            ("capture",  "🎬", "Трансляция"),
            ("scan",     "🔍", "Сканирование"),
            ("buffer",   "📦", "Буферизация"),
            ("recognize","✅", "Распознавание"),
            ("voice",    "🎵", "Голос"),
        ]
        for key, emoji, tip in defs:
            lbl = QLabel(emoji)
            lbl.setFixedSize(22, 22)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet("color: #333; font-size: 16px; background: transparent;")
            lbl.setToolTip(tip)
            layout.addWidget(lbl)
            self._icons[key] = lbl

        self.setStyleSheet("background: transparent;")

    def blink(self, key: str, on: bool = True):
        """Мигнуть иконкой: on=True — горит, on=False — погасла."""
        lbl = self._icons.get(key)
        if not lbl:
            return
        if on:
            lbl.setStyleSheet("color: #4cff4c; font-size: 16px; background: transparent;")
        else:
            lbl.setStyleSheet("color: #333; font-size: 16px; background: transparent;")

    def pulse(self, key: str, duration_ms: int = 600):
        """Одиночный импульс: включить → выключить через duration_ms."""
        self.blink(key, True)
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda k=key: self.blink(k, False))
        timer.start(duration_ms)
        # Prevent GC
        self._timers[f"{key}_{time.time()}"] = timer

    def show_all_off(self):
        """Все иконки погашены."""
        for key in self._icons:
            self.blink(key, False)


class GamePreview(QWidget):
    """Окно предпросмотра захваченной области игры.

    Всегда поверх, без рамок, прозрачный фон.
    Показывает только захваченную область — без рабочего стола.
    """
    frame_ready = pyqtSignal(object)  # PIL Image

    def __init__(self, parent=None, region=None):
        super().__init__(parent)
        self.setWindowTitle("Game Preview")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMinimumSize(200, 100)
        self.resize(400, 300)

        # Region (x, y, w, h) — какая область захватывается
        self._region = region or {"x": 0, "y": 0, "width": 800, "height": 450}

        # Main layout
        outer = QVBoxLayout(self)
        outer.setContentsMargins(2, 2, 2, 2)

        # Image label
        self._image_label = QLabel()
        self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_label.setStyleSheet(
            "background: #0a0a0a; border: 1px solid #222; border-radius: 6px;"
        )
        outer.addWidget(self._image_label)

        # Status icons (bottom of window)
        self.status = StatusIcons()
        outer.addWidget(self.status)

        # Opacity
        self._opacity = QGraphicsOpacityEffect(self._image_label)
        self._opacity.setOpacity(0.92)
        self._image_label.setGraphicsEffect(self._opacity)

        self._visible = True
        logger.info(f"GamePreview: region={self._region}")

    def set_region(self, region: dict):
        """Обновить захватываемую область."""
        self._region = region
        logger.info(f"GamePreview: region updated = {region}")

    def update_frame(self, pil_img):
        """Обновить кадр (PIL Image)."""
        if not self._visible or not pil_img:
            return
        try:
            img = pil_img.copy()
            # Масштабируем под размер окна, сохраняя пропорции
            w_label = self._image_label.width()
            h_label = self._image_label.height()
            if w_label < 10 or h_label < 10:
                return
            img.thumbnail((w_label, h_label))

            data = img.tobytes("raw", "RGB")
            qimg = QImage(data, img.width, img.height,
                          3 * img.width, QImage.Format.Format_RGB888)
            pixmap = QPixmap.fromImage(qimg.copy())
            self._image_label.setPixmap(pixmap)
        except Exception as e:
            logger.debug(f"GamePreview update_frame error: {e}")

    def toggle_visible(self):
        """Показать/скрыть окно."""
        self._visible = not self._visible
        if self._visible:
            self.show()
            self.raise_()
        else:
            self.hide()

    def showEvent(self, event):
        super().showEvent(event)
        self._visible = True

    def hideEvent(self, event):
        super().hideEvent(event)
        self._visible = False


# ── Standalone test ───────────────────────────────────────
if __name__ == "__main__":
    import sys
    from PyQt6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    preview = GamePreview(region={"x": 100, "y": 100, "width": 800, "height": 450})
    preview.resize(640, 380)
    preview.show()

    # Test: capture screen and show
    import mss
    from PIL import Image

    def capture_and_update():
        with mss.mss() as sct:
            r = preview._region
            shot = sct.grab({"left": r["x"], "top": r["y"],
                             "width": r["width"], "height": r["height"]})
            img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
            preview.update_frame(img)

    timer = QTimer()
    timer.timeout.connect(capture_and_update)
    timer.start(500)

    # Test blink
    def cycle_icons():
        for key in ["capture", "scan", "buffer", "recognize", "voice"]:
            preview.status.pulse(key, 400)

    blink_timer = QTimer()
    blink_timer.timeout.connect(cycle_icons)
    blink_timer.start(2000)

    sys.exit(app.exec())
