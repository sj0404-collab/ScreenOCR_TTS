"""
Виджет для выбора области экрана мышью и предпросмотра
"""
import sys
import logging
from PyQt6.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QWidget, QLabel
from PyQt6.QtCore import Qt, QRect, QTimer, QPoint, pyqtSignal
from PyQt6.QtGui import QPainter, QPen, QBrush, QColor, QPalette, QCursor, QPainterPath
from overlay_state import OverlayBox, OverlayState

logger = logging.getLogger(__name__)


class RegionSelector(QMainWindow):
    """Окно для выбора области"""
    
    def __init__(self):
        super().__init__()
        self.start_pos = None
        self.end_pos = None
        self.selector_window = None
        self._result = None
        self._loop = None
        
        logger.info("RegionSelector инициализирован")
    
    def get_region(self):
        """Blocking: show fullscreen overlay, return dict or None."""
        from PyQt6.QtCore import QEventLoop
        self._result = None
        self._loop = QEventLoop()
        overlay = SelectionOverlay()
        overlay.region_selected.connect(self._on_region_selected)
        overlay.cancelled.connect(self._loop.quit)
        overlay.show_fullscreen()
        self._loop.exec()
        return self._result

    def _on_region_selected(self, x, y, width, height):
        """Обработка выбранной области"""
        logger.info(f"Выбрана область: x={x}, y={y}, w={width}, h={height}")
        self._result = {"x": x, "y": y, "width": width, "height": height}
        if hasattr(self, '_loop') and self._loop is not None:
            self._loop.quit()


class RegionPreview(QMainWindow):
    """Окно для предпросмотра текущей области на весь экран"""
    
    def __init__(self, x: int, y: int, width: int, height: int):
        super().__init__()
        self.region_rect = QRect(x, y, width, height)
        self.on_confirm = None
        
        logger.info(f"RegionPreview: x={x}, y={y}, w={width}, h={height}")
    
    def show_fullscreen(self):
        """Показ предпросмотра на весь экран"""
        screen = QApplication.primaryScreen().geometry()
        self.setGeometry(screen)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.show()
        self.activateWindow()
        
        # Инструкция
        info = QLabel(
            "Текущая область сканирования (зеленая рамка)\n"
            "ESC - закрыть, ENTER - подтвердить и выбрать эту область",
            self
        )
        info.setStyleSheet("""
            QLabel {
                background-color: rgba(0, 0, 0, 200);
                color: white;
                padding: 15px;
                font-size: 16px;
                border-radius: 8px;
                border: 2px solid #4caf50;
            }
        """)
        info.move(50, 50)
        info.show()
        self.info_label = info
        
        # Автоматическое закрытие через 5 секунд
        self.close_timer = QTimer()
        self.close_timer.timeout.connect(self.close)
        self.close_timer.start(5000)
    
    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            logger.info("Предпросмотр закрыт")
            self.close()
        elif event.key() == Qt.Key.Key_Return or event.key() == Qt.Key.Key_Enter:
            logger.info("Подтверждение области")
            if self.on_confirm:
                rect = self.region_rect
                self.on_confirm(rect.x(), rect.y(), rect.width(), rect.height())
            self.close()
    
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        screen = QApplication.primaryScreen().geometry()
        
        # Затемнение всего экрана
        painter.fillRect(screen, QColor(0, 0, 0, 180))
        
        # Рисуем зеленую рамку
        painter.setPen(QPen(QColor(76, 175, 80), 4))
        painter.setBrush(QBrush(QColor(76, 175, 80, 50)))
        painter.drawRoundedRect(self.region_rect, 5, 5)
        
        # Размер и координаты
        text = f"{self.region_rect.width()}x{self.region_rect.height()} @ ({self.region_rect.x()}, {self.region_rect.y()})"
        painter.setPen(QColor("white"))
        painter.drawText(self.region_rect.topLeft() + QPoint(10, -30), text)
        
        # Подсказка
        hint = "ENTER - подтвердить | ESC - закрыть"
        painter.drawText(screen.bottomLeft() + QPoint(50, -50), hint)


class SelectionOverlay(QWidget):
    """Накладываемое окно для выбора области"""
    
    region_selected = pyqtSignal(int, int, int, int)  # x, y, width, height
    cancelled = pyqtSignal()
    
    def __init__(self):
        super().__init__()
        self.start_pos = None
        self.end_pos = None
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        
        screen = QApplication.primaryScreen().geometry()
        self.screen_width = screen.width()
        self.screen_height = screen.height()
        
        logger.info(f"Размер экрана: {self.screen_width}x{self.screen_height}")
    
    def show_fullscreen(self):
        """Показ на весь экран"""
        self.setGeometry(0, 0, self.screen_width, self.screen_height)
        self.show()
        self.activateWindow()
        self.raise_()
        
        instruction = QLabel("Выделите область для сканирования мышью\nESC - отмена, Двойной клик - выбрать всю область", self)
        instruction.setStyleSheet("""
            QLabel {
                background-color: rgba(0, 0, 0, 200);
                color: white;
                padding: 20px;
                font-size: 18px;
                border-radius: 10px;
            }
        """)
        instruction.move(50, 50)
        instruction.show()
        self.instruction = instruction
    
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.start_pos = event.pos()
            self.end_pos = event.pos()
            logger.debug(f"Начало выделения: {self.start_pos}")
    
    def mouseMoveEvent(self, event):
        if self.start_pos:
            self.end_pos = event.pos()
            self.update()
    
    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.start_pos:
            self.end_pos = event.pos()
            self._emit_region()
    
    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._emit_full_screen()
    
    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            logger.info("Выбор области отменён")
            self.cancelled.emit()
            self.close()
    
    def paintEvent(self, event):
        if self.start_pos and self.end_pos:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            
            rect = QRect(self.start_pos, self.end_pos).normalized()
            
            painter.setPen(QPen(QColor(0, 150, 255), 2))
            painter.setBrush(QBrush(QColor(0, 150, 255, 50)))
            painter.drawRect(rect)
            
            x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
            text = f"{w}x{h} @ ({x}, {y})"
            painter.setPen(QColor("white"))
            painter.drawText(rect.topLeft() - self.start_pos + self.start_pos, text)
    
    def _emit_region(self):
        if not self.start_pos or not self.end_pos:
            return
        rect = QRect(self.start_pos, self.end_pos).normalized()
        # Ограничиваем рамку границами экрана
        screen_rect = QRect(0, 0, self.screen_width, self.screen_height)
        rect = rect.intersected(screen_rect)
        x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
        if w < 50 or h < 50:
            self._emit_full_screen()
            return
        # Create an OverlayBox and store it
        box = OverlayBox(x=x, y=y, w=w, h=h)
        OverlayState.add_box(box)
        logger.info(f"Overlay box added: {box}")
        if hasattr(self, 'instruction'):
            self.instruction.hide()
        # Emit signal
        self.region_selected.emit(x, y, w, h)
        self.close()
    
    def _emit_full_screen(self):
        logger.info("Выбрана вся область экрана")
        
        if self.region_selected:
            self.region_selected.emit(0, 0, self.screen_width, self.screen_height)
        
        self.close()