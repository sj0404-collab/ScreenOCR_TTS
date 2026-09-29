"""
Оверлей - прозрачное окно поверх игры для отображения распознанного текста
"""
import logging
from PyQt6.QtWidgets import (
    QLabel, QWidget, QVBoxLayout, QApplication, 
    QFrame, QGraphicsOpacityEffect
)
from PyQt6.QtCore import Qt, QTimer, QPoint, QPropertyAnimation, QEasingCurve, QRectF, QRect
from PyQt6.QtGui import QFont, QColor, QPainter, QBrush, QPainterPath

logger = logging.getLogger(__name__)


class OverlayWidget(QWidget):
    """Прозрачное окно для отображения текста поверх игр"""
    
    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.visible = settings.get("overlay.enabled", True)
        self.text = ""
        self.current_opacity = settings.get("overlay.opacity", 0.8)
        
        self._setup_ui()
        self._apply_styles()
    
    def _setup_ui(self):
        """Настройка интерфейса"""
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(5)
        
        self.text_container = QFrame()
        
        text_layout = QVBoxLayout(self.text_container)
        text_layout.setContentsMargins(15, 10, 15, 10)
        
        self.text_label = QLabel("Нажмите F9 для распознавания")
        self.text_label.setWordWrap(True)
        self.text_label.setStyleSheet("color: white; background: transparent;")
        
        text_layout.addWidget(self.text_label)
        layout.addWidget(self.text_container)
        
        self.opacity_effect = QGraphicsOpacityEffect(self)
        self.opacity_effect.setOpacity(self.current_opacity)
        self.setGraphicsEffect(self.opacity_effect)
        
        self.update_timer = QTimer()
        self.update_timer.timeout.connect(self._animate_update)
        
        self.setMinimumWidth(300)
        self.setMaximumWidth(800)
        self.adjustSize()
        
        self._update_position()
    
    def _apply_styles(self):
        """Применение стилей"""
        font_size = self.settings.get("overlay.font_size", 14)
        font_color = self.settings.get("overlay.font_color", "#FFFFFF")
        bg_color = self.settings.get("overlay.background_color", "#000000AA")
        self.current_opacity = self.settings.get("overlay.opacity", 0.8)
        
        font = QFont("Segoe UI", font_size)
        self.text_label.setFont(font)
        self.text_label.setStyleSheet(f"color: {font_color}; background: transparent;")
        
        # Применяем текущую прозрачность
        self.opacity_effect.setOpacity(self.current_opacity)
        
        # Обновляем фон с текущей прозрачностью
        self._update_background_color()
    
    def _update_background_color(self):
        """Обновление цвета фона с текущей прозрачностью"""
        bg_color = self.settings.get("overlay.background_color", "#000000")
        if not bg_color.startswith("#"):
            bg_color = "#000000"
        
        hex_color = bg_color[1:]
        if len(hex_color) == 6:
            r = int(hex_color[0:2], 16)
            g = int(hex_color[2:4], 16)
            b = int(hex_color[4:6], 16)
            a = int(self.current_opacity * 255)
            bg_rgba = f"rgba({r}, {g}, {b}, {a})"
            self.text_container.setStyleSheet(
                f"background-color: {bg_rgba}; "
                "border-radius: 10px; "
                "border: 2px solid rgba(255, 255, 255, 100);"
            )
    
    def set_opacity(self, opacity: float):
        """Установка прозрачности (0.0 - 1.0)"""
        self.current_opacity = max(0.0, min(1.0, opacity))
        self.opacity_effect.setOpacity(self.current_opacity)
        self._update_background_color()
        self.settings.set("overlay.opacity", self.current_opacity)
        logger.info(f"Прозрачность оверлея: {self.current_opacity:.1f}")
    
    def _update_position(self):
        """Обновление позиции оверлея"""
        position = self.settings.get("overlay.position", "top-right")
        
        screen = QApplication.primaryScreen().geometry()
        screen_width = screen.width()
        screen_height = screen.height()
        
        self.adjustSize()
        overlay_width = self.width()
        overlay_height = self.height()
        
        margin = 20
        
        if position == "top-right":
            x = screen_width - overlay_width - margin
            y = margin
        elif position == "top-left":
            x = margin
            y = margin
        elif position == "bottom-right":
            x = screen_width - overlay_width - margin
            y = screen_height - overlay_height - margin
        elif position == "bottom-left":
            x = margin
            y = screen_height - overlay_height - margin
        else:
            x = screen_width // 2 - overlay_width // 2
            y = screen_height // 2 - overlay_height // 2
        
        self.move(x, y)
    
    def show_text(self, text: str):
        """Отображение текста с анимацией"""
        self.text = text
        
        if not text:
            self.text_label.setText("Нет текста для отображения")
        else:
            max_length = 800
            if len(text) > max_length:
                text = text[:max_length] + "..."
            self.text_label.setText(text)
        
        if self.visible:
            self._animate_show()
    
    def _animate_show(self):
        """Анимация появления"""
        if not self.visible:
            return
        
        # Прячем оверлей чтобы перезапустить анимацию
        super().hide()
        
        # Показываем без анимации сначала
        super().show()
        
        # Запускаем анимацию прозрачности
        self.fade_animation = QPropertyAnimation(self.opacity_effect, b"opacity")
        self.fade_animation.setDuration(300)
        self.fade_animation.setStartValue(0)
        self.fade_animation.setEndValue(self.current_opacity)
        self.fade_animation.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self.fade_animation.start()
    
    def _animate_update(self):
        """Обновление позиции оверлея при изменении экрана"""
        self._update_position()
    
    def toggle_visibility(self):
        """Переключение видимости"""
        self.visible = not self.visible
        if self.visible:
            self._animate_show()
        else:
            super().hide()
    
    def hide(self):
        """Скрытие оверлея"""
        super().hide()
    
    def show(self):
        """Показ оверлея без рекурсии"""
        super().show()
        self._apply_win32_styles()
        if self.visible:
            self._animate_show()

    def _apply_win32_styles(self):
        """WS_EX_NOACTIVATE + WS_EX_TOOLWINDOW для fullscreen-игр."""
        try:
            import ctypes
            hwnd = int(self.winId())
            GWL_EXSTYLE = -20
            WS_EX_NOACTIVATE = 0x08000000
            WS_EX_TOOLWINDOW = 0x00000080
            flags = WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
            old = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, old | flags)
        except Exception:
            pass