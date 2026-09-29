"""
Keyboard visualization widget — показывает симуляцию нажатия клавиш.
Отображает клавиатуру с подсветкой нажимаемой клавиши.
"""
import logging
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QRect
from PyQt6.QtGui import QPainter, QColor, QFont, QPen, QBrush, QRadialGradient

logger = logging.getLogger(__name__)

# Keyboard layout (QWERTY)
KEYBOARD_ROWS = [
    ["`", "1", "2", "3", "4", "5", "6", "7", "8", "9", "0", "-", "="],
    ["Q", "W", "E", "R", "T", "Y", "U", "I", "O", "P", "[", "]", "\\"],
    ["A", "S", "D", "F", "G", "H", "J", "K", "L", ";", "'"],
    ["Z", "X", "C", "V", "B", "N", "M", ",", ".", "/"],
    ["SPACE"],
]

# Key dimensions (compact)
KEY_W = 22
KEY_H = 22
KEY_GAP = 2
KEY_RADIUS = 3


class KeyboardWidget(QWidget):
    """Виджет клавиатуры с подсветкой нажимаемых клавиш."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._active_key = ""
        self._active_color = QColor("#ff6b35")
        self._fade_timer = QTimer(self)
        self._fade_timer.setSingleShot(True)
        self._fade_timer.timeout.connect(self._fade_key)
        self._pressed_keys = set()

        # Calculate widget size
        max_row_len = max(len(row) for row in KEYBOARD_ROWS)
        self._width = max_row_len * (KEY_W + KEY_GAP) + KEY_GAP
        self._height = len(KEYBOARD_ROWS) * (KEY_H + KEY_GAP) + KEY_GAP
        self.setFixedSize(self._width, self._height)

    def key_press(self, key: str):
        """Подсветить нажатую клавишу."""
        self._active_key = key.upper()
        self._pressed_keys.add(key.upper())
        self.update()
        self._fade_timer.start(200)

    def animate_text(self, text: str, delay_ms: int = 30):
        """Анимировать набор текста — последовательное нажатие клавиш."""
        import re
        # Extract only typeable characters
        chars = [c for c in text if re.match(r'[a-zA-Z0-9 `~!@#$%^&*()_\-+=\[\]{}|;:\'",.<>?/\\]', c)]
        if not chars:
            return
        self._anim_chars = chars
        self._anim_pos = 0
        self._anim_delay = delay_ms
        if not hasattr(self, '_anim_timer'):
            self._anim_timer = QTimer(self)
            self._anim_timer.timeout.connect(self._anim_tick)
        self._anim_timer.start(delay_ms)

    def _anim_tick(self):
        if self._anim_pos < len(self._anim_chars):
            ch = self._anim_chars[self._anim_pos]
            self.key_press(ch)
            self._anim_pos += 1
        else:
            self._anim_timer.stop()

    def _fade_key(self):
        """Убрать подсветку."""
        self._pressed_keys.clear()
        self._active_key = ""
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        y = KEY_GAP
        for row in KEYBOARD_ROWS:
            x = KEY_GAP
            for key in row:
                w = KEY_W * 2 + KEY_GAP if key == "SPACE" else KEY_W
                rect = QRect(x, y, w, KEY_H)

                # Determine key color
                if key in self._pressed_keys:
                    color = self._active_color
                    text_color = QColor("#ffffff")
                elif key == self._active_key:
                    color = QColor("#ff6b35")
                    text_color = QColor("#ffffff")
                else:
                    color = QColor("#3a3a4a")
                    text_color = QColor("#cccccc")

                # Draw key background
                painter.setPen(QPen(QColor("#555555"), 1))
                painter.setBrush(QBrush(color))
                painter.drawRoundedRect(rect, KEY_RADIUS, KEY_RADIUS)

                # Draw key text
                painter.setPen(text_color)
                font = QFont("Consolas", 7)
                painter.setFont(font)
                painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, key)

                x += w + KEY_GAP
            y += KEY_H + KEY_GAP

        painter.end()

    def get_key_rect(self, key: str) -> QRect:
        """Get rectangle for a specific key."""
        y = KEY_GAP
        for row in KEYBOARD_ROWS:
            x = KEY_GAP
            for k in row:
                w = KEY_W * 2 + KEY_GAP if k == "SPACE" else KEY_W
                if k == key.upper():
                    return QRect(x, y, w, KEY_H)
                x += w + KEY_GAP
            y += KEY_H + KEY_GAP
        return QRect()
