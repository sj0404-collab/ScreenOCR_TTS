"""
Визуальная индикация процесса OCR.
Прогресс-бар и анимация сканирования.
"""
import logging
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QProgressBar, QLabel, QGraphicsOpacityEffect
from PyQt6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve
from PyQt6.QtGui import QColor, QPainter, QPen, QBrush

logger = logging.getLogger(__name__)


class OCRIndicator(QWidget):
    """Виджет индикации процесса OCR"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()
        self._animation_timer = QTimer()
        self._animation_timer.timeout.connect(self._pulse)
        self._pulse_value = 0
        self._pulse_direction = 1

    def _setup_ui(self):
        self.setFixedHeight(30)
        self.setStyleSheet("""
            QWidget {
                background-color: transparent;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 2, 5, 2)
        layout.setSpacing(2)

        # Метка статуса
        self.status_label = QLabel("Готов")
        self.status_label.setStyleSheet("""
            QLabel {
                color: #4caf50;
                font-size: 11px;
                font-weight: bold;
                background: transparent;
            }
        """)
        layout.addWidget(self.status_label)

        # Прогресс-бар
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFixedHeight(16)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                background-color: rgba(50, 50, 50, 150);
                border-radius: 8px;
                border: 1px solid rgba(100, 100, 100, 100);
                color: white;
                font-size: 10px;
            }
            QProgressBar::chunk {
                background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #4caf50,
                    stop:1 #8bc34a);
                border-radius: 7px;
            }
        """)
        layout.addWidget(self.progress_bar)

        self.hide()

    def start_scanning(self):
        """Начало сканирования"""
        self.show()
        self.progress_bar.setValue(0)
        self.status_label.setText("⏳ Сканирование...")
        self.status_label.setStyleSheet("""
            QLabel {
                color: #ff9800;
                font-size: 11px;
                font-weight: bold;
                background: transparent;
            }
        """)
        self._animation_timer.start(30)
        logger.info("[INDICATOR] Сканирование начато")

    def update_progress(self, value: int):
        """Обновление прогресса (0-100)"""
        self.progress_bar.setValue(value)
        if value < 30:
            self.status_label.setText("📷 Захват экрана...")
        elif value < 70:
            self.status_label.setText("🔍 Распознавание...")
        elif value < 100:
            self.status_label.setText("✅ Завершение...")
        else:
            self.status_label.setText("✅ Готово!")

    def finish_scanning(self, success=True):
        """Завершение сканирования"""
        self._animation_timer.stop()
        if success:
            self.progress_bar.setValue(100)
            self.status_label.setText("✅ Текст распознан")
            self.status_label.setStyleSheet("""
                QLabel {
                    color: #4caf50;
                    font-size: 11px;
                    font-weight: bold;
                    background: transparent;
                }
            """)
        else:
            self.progress_bar.setValue(0)
            self.status_label.setText("❌ Ошибка")
            self.status_label.setStyleSheet("""
                QLabel {
                    color: #f44336;
                    font-size: 11px;
                    font-weight: bold;
                    background: transparent;
                }
            """)

        # Автоскрытие через 1.2 секунды
        QTimer.singleShot(1200, self.hide)

    def _pulse(self):
        """Пульсация прогресс-бара при активном сканировании"""
        self._pulse_value += self._pulse_direction * 8
        if self._pulse_value >= 100:
            self._pulse_direction = -1
        elif self._pulse_value <= 0:
            self._pulse_direction = 1

        current = self.progress_bar.value()
        if current < 90:
            self.progress_bar.setValue(self._pulse_value)


class ScanOverlay(QWidget):
    """Оверлей с анимацией сканирования поверх выбранной области"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scan_position = 0
        self._scan_timer = QTimer()
        self._scan_timer.timeout.connect(self._update_scan)
        self.setStyleSheet("background: transparent;")

    def start_animation(self):
        """Запуск анимации сканирования"""
        self._scan_position = 0
        self._scan_timer.start(30)
        self.show()

    def stop_animation(self):
        """Остановка анимации"""
        self._scan_timer.stop()
        self.hide()

    def _update_scan(self):
        """Обновление позиции линии сканирования"""
        self._scan_position += 5
        if self._scan_position > self.height():
            self._scan_position = 0
        self.update()

    def paintEvent(self, event):
        """Отрисовка линии сканирования"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Линия сканирования
        gradient_color = QColor(76, 175, 80, 150)
        painter.setPen(QPen(gradient_color, 2))
        painter.drawLine(0, self._scan_position, self.width(), self._scan_position)

        # Свечение вокруг линии
        glow = QColor(76, 175, 80, 50)
        painter.setPen(QPen(glow, 8))
        painter.drawLine(0, self._scan_position, self.width(), self._scan_position)
