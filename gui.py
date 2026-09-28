"""
Screen OCR + TTS - GUI for text recognition and speech synthesis.

The interface code in this module is kept fully in English. Every
user-facing string is pulled from localization.py via tr(), so the UI
can switch between English and Russian without touching this file.
"""
import sys
import logging
import asyncio
import threading
import json
import os
import re
import time
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QLineEdit, QComboBox, QSlider, QTabWidget,
    QGroupBox, QSpinBox, QDoubleSpinBox, QCheckBox, QMessageBox, QFileDialog,
    QFrame, QScrollArea, QSystemTrayIcon, QMenu, QStyle, QTextEdit,
    QListWidget, QListWidgetItem, QInputDialog, QDialog, QDialogButtonBox,
    QSizePolicy, QProgressBar, QSplitter, QTreeWidget, QTreeWidgetItem,
    QStackedWidget, QGridLayout, QToolButton, QRadioButton
)
from PyQt6.QtGui import (QFont, QIcon, QCursor, QShortcut, QAction, QKeySequence,
                          QPixmap, QPainter, QColor, QTextCharFormat, QTextCursor,
                          QImage)
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal, QSize
from PyQt6.QtGui import QTextOption

from settings import Settings
from ocr_wrapper import OCRWrapper as OCREngine
from tts_engine import TTSEngine
from overlay import OverlayWidget
from live_scanner import LiveScanner
from region_selector import RegionSelector
from region_overlay import RegionOverlay
from ocr_indicator import OCRIndicator
from localization import tr, set_language
from translator import Translator, LANGUAGES
from icons import icon_male, icon_female, icon_narrator, icon_play, icon_stop

logger = logging.getLogger(__name__)

# Voices are loaded lazily from tts_engine.
VOICES = []


class OCRWorker(QThread):
    result_ready = pyqtSignal(str)
    error_occurred = pyqtSignal(str)
    progress = pyqtSignal(int)  # 0-100

    def __init__(self, ocr_engine):
        super().__init__()
        self.ocr_engine = ocr_engine

    def run(self):
        loop = None
        try:
            self.progress.emit(10)
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self.progress.emit(30)
            result = loop.run_until_complete(self.ocr_engine.recognize_async())
            self.progress.emit(90)
            if isinstance(result, tuple):
                text = result[0] if result else ""
            else:
                text = result or ""
            self.progress.emit(100)
            self.result_ready.emit(str(text))
        except Exception as e:
            self.progress.emit(0)
            self.error_occurred.emit(str(e))
        finally:
            if loop is not None:
                loop.close()


class RegionDialog(QDialog):
    COLORS = [
        ("color_green", "#4caf50"),
        ("color_blue", "#2196f3"),
        ("color_orange", "#ff9800"),
        ("color_purple", "#9c27b0"),
        ("color_red", "#f44336"),
        ("color_cyan", "#00bcd4"),
        ("color_yellow", "#ffeb3b"),
        ("color_white", "#ffffff"),
        ("color_black", "#000000"),
        ("color_pink", "#e91e63"),
    ]

    SHAPES = [
        ("shape_rect", "rect"),
        ("shape_circle", "circle"),
        ("shape_ellipse", "ellipse"),
    ]

    def __init__(self, region=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("dlg_region_props"))
        self.setMinimumSize(350, 300)
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel(tr("lbl_name")))
        self.name_edit = QLineEdit(region.get("name", "") if region else "")
        layout.addWidget(self.name_edit)

        coords = QHBoxLayout()
        coords.addWidget(QLabel("X:"))
        self.x_spin = QSpinBox()
        self.x_spin.setRange(-10000, 10000)
        self.x_spin.setValue(region.get("x", 0) if region else 0)
        coords.addWidget(self.x_spin)
        coords.addWidget(QLabel("Y:"))
        self.y_spin = QSpinBox()
        self.y_spin.setRange(-10000, 10000)
        self.y_spin.setValue(region.get("y", 0) if region else 0)
        coords.addWidget(self.y_spin)
        layout.addLayout(coords)

        size = QHBoxLayout()
        size.addWidget(QLabel(tr("lbl_width")))
        self.w_spin = QSpinBox()
        self.w_spin.setRange(50, 10000)
        self.w_spin.setValue(region.get("width", 200) if region else 200)
        size.addWidget(self.w_spin)
        size.addWidget(QLabel(tr("lbl_height")))
        self.h_spin = QSpinBox()
        self.h_spin.setRange(50, 10000)
        self.h_spin.setValue(region.get("height", 100) if region else 100)
        size.addWidget(self.h_spin)
        layout.addLayout(size)

        # Color
        layout.addWidget(QLabel(tr("lbl_frame_color")))
        self.color_combo = QComboBox()
        for name_key, hex_color in self.COLORS:
            self.color_combo.addItem(tr(name_key), hex_color)
        if region and region.get("color"):
            for i, (name_key, hex_color) in enumerate(self.COLORS):
                if hex_color == region["color"]:
                    self.color_combo.setCurrentIndex(i)
                    break
        layout.addWidget(self.color_combo)

        # Shape
        layout.addWidget(QLabel(tr("lbl_frame_shape")))
        self.shape_combo = QComboBox()
        for name_key, shape_id in self.SHAPES:
            self.shape_combo.addItem(tr(name_key), shape_id)
        if region and region.get("shape"):
            for i, (name_key, shape_id) in enumerate(self.SHAPES):
                if shape_id == region["shape"]:
                    self.shape_combo.setCurrentIndex(i)
                    break
        layout.addWidget(self.shape_combo)

        # Click-through option
        self.click_through_check = QCheckBox(tr("chk_click_through"))
        self.click_through_check.setChecked(region.get("click_through", False) if region else False)
        self.click_through_check.setToolTip(tr("tip_click_through"))
        layout.addWidget(self.click_through_check)

        layout.addWidget(QLabel(tr("lbl_tts_voice")))
        self.voice_combo = QComboBox()
        self.voice_combo.addItems(self._get_voices_list())
        if region and region.get("voice"):
            idx = self.voice_combo.findText(region["voice"])
            if idx >= 0:
                self.voice_combo.setCurrentIndex(idx)
        layout.addWidget(self.voice_combo)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _get_voices_list(self):
        try:
            from settings import Settings
            settings = Settings()
            tts = TTSEngine(settings)
            voices = tts.get_voices()
            return [v["code"] for v in voices]
        except Exception:
            return ["ru-RU-DmitryNeural", "ru-RU-SvetlanaNeural", "en-US-JennyNeural", "en-US-GuyNeural"]

    def get_region(self):
        return {
            "name": self.name_edit.text() or tr("region_default_name", x=self.x_spin.value(), y=self.y_spin.value()),
            "x": self.x_spin.value(),
            "y": self.y_spin.value(),
            "width": self.w_spin.value(),
            "height": self.h_spin.value(),
            "voice": self.voice_combo.currentText(),
            "color": self.color_combo.currentData(),
            "shape": self.shape_combo.currentData(),
            "click_through": self.click_through_check.isChecked(),
        }


class Toast(QFrame):
    """Всплывающее уведомление (toast) — появляется и исчезает."""

    def __init__(self, text: str, color: str = "#4caf50", duration: int = 3000, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setStyleSheet(
            f"background-color: {color}; border-radius: 8px;"
        )
        self._label = QLabel(text, self)
        self._label.setStyleSheet(
            "color: white; font-weight: bold; font-size: 14px; "
            "background: transparent; padding: 8px 16px;"
        )
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedHeight(36)
        self.setMinimumWidth(200)
        self.setMaximumWidth(500)
        self.hide()

        self._color = color
        self._fade_timer = QTimer(self)
        self._fade_timer.setSingleShot(True)
        self._fade_timer.timeout.connect(self._start_fade)

        self._opacity = 1.0
        self._fade_step_timer = QTimer(self)
        self._fade_step_timer.setInterval(30)
        self._fade_step_timer.timeout.connect(self._fade_step)

        self._duration = duration

    def show_toast(self):
        """Показать toast и запустить таймер скрытия."""
        if self.parent():
            pw = self.parent().width()
            self.setFixedWidth(min(max(200, self._label.sizeHint().width() + 32), pw - 40))
        self.adjustSize()
        self.show()
        self.raise_()
        self._position()
        self._fade_timer.start(self._duration)

    def _position(self):
        if self.parent():
            pw = self.parent().width()
            ph = self.parent().height()
            x = (pw - self.width()) // 2
            y = ph - self.height() - 30
            self.move(x, y)

    def _start_fade(self):
        self._opacity = 1.0
        self._fade_step_timer.start()

    def _fade_step(self):
        self._opacity -= 0.1
        if self._opacity <= 0:
            self._fade_step_timer.stop()
            self.hide()
            self._opacity = 1.0
        else:
            alpha = int(self._opacity * 255)
            self.setStyleSheet(
                f"background-color: rgba(76, 175, 80, {alpha}); border-radius: 8px;"
            )
            self._label.setStyleSheet(
                f"color: rgba(255, 255, 255, {alpha}); font-weight: bold; font-size: 14px; "
                f"background: transparent; padding: 8px 16px;"
            )


class _DetachedDialog(QDialog):
    """Окно-диалог для отдельного запуска вкладки.

    Оборачивает QWidget (созданный _create_*_tab) в отдельное окно.
    Запоминает размер/позицию между сессиями.
    """
    def __init__(self, title: str, parent=None, settings_key: str = ""):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(700, 500)
        self.resize(850, 650)
        self._settings_key = settings_key or title
        self.setStyleSheet("""
            QDialog { background: #1C1B1F; }
            QGroupBox { color: #E6E1E5; font-weight: bold; border: 1px solid #49454F;
                         border-radius: 12px; margin-top: 12px; padding: 12px 8px 8px 8px; }
            QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px; }
            QLabel { color: #E6E1E5; }
            QCheckBox { color: #E6E1E5; spacing: 6px; }
            QComboBox { background: #2B2930; color: #E6E1E5; border: 1px solid #49454F;
                         border-radius: 8px; padding: 4px 8px; min-height: 24px; }
            QComboBox::drop-down { border: none; width: 20px; }
            QComboBox::down-arrow { image: none; border-left: 4px solid transparent;
                                     border-right: 4px solid transparent;
                                     border-top: 5px solid #E6E1E5; margin-right: 6px; }
            QComboBox QAbstractItemView { background: #2B2930; color: #E6E1E5;
                                           selection-background-color: #3E4758; }
            QLineEdit, QTextEdit, QPlainTextEdit { background: #2B2930; color: #E6E1E5;
                                                     border: 1px solid #49454F; border-radius: 8px; padding: 4px; }
            QPushButton { background: #3E4758; color: #E6E1E5; border: none;
                          border-radius: 20px; padding: 8px 16px; font-weight: bold; }
            QPushButton:hover { background: #49454F; }
            QPushButton:pressed { background: #2B2930; }
            QSpinBox { background: #2B2930; color: #E6E1E5; border: 1px solid #49454F;
                        border-radius: 8px; padding: 4px; }
            QSlider::groove:horizontal { background: #49454F; height: 4px; border-radius: 2px; }
            QSlider::handle:horizontal { background: #A8C7FA; width: 16px; height: 16px;
                                          margin: -6px 0; border-radius: 8px; }
            QScrollArea { border: none; background: #1C1B1F; }
            QScrollBar:vertical { background: #1C1B1F; width: 8px; }
            QScrollBar::handle:vertical { background: #49454F; border-radius: 4px; min-height: 30px; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._content_widget = None

    def set_content(self, widget: QWidget):
        """Embed a widget into this dialog."""
        self._content_widget = widget
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(widget)
        self.layout().addWidget(scroll)

    def showEvent(self, event):
        super().showEvent(event)
        # Restore saved geometry
        geo = self.parent().settings.get(f"dialog.{self._settings_key}.geometry") if hasattr(self.parent(), 'settings') else None
        if geo:
            try:
                self.restoreGeometry(bytes.fromhex(geo))
            except Exception:
                pass

    def closeEvent(self, event):
        # Save geometry
        if hasattr(self.parent(), 'settings'):
            self.parent().settings.set(f"dialog.{self._settings_key}.geometry",
                                        bytes(self.saveGeometry()).hex())
        super().closeEvent(event)


class _TTSEngineDialog(QDialog):
    """Компактное окно выбора TTS-движка.

    Позволяет выбрать движок (Edge-TTS онлайн / RHVoice офлайн / Silero / SAPI5),
    голос, скорость, питч и протестировать. Выбор сохраняется как движок по умолчанию.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setModal(False)
        self.setWindowTitle(tr("cat_voice", default="TTS Движок"))
        self.setMinimumSize(480, 420)
        self.resize(520, 480)
        self.setStyleSheet("""
            QDialog { background: #1C1B1F; }
            QGroupBox { color: #E6E1E5; font-weight: bold; border: 1px solid #49454F;
                         border-radius: 12px; margin-top: 12px; padding: 12px 8px 8px 8px; }
            QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px; }
            QLabel { color: #E6E1E5; }
            QRadioButton { color: #E6E1E5; spacing: 6px; }
            QRadioButton::indicator { width: 14px; height: 14px; }
            QComboBox { background: #2B2930; color: #E6E1E5; border: 1px solid #49454F;
                         border-radius: 8px; padding: 4px 8px; min-height: 24px; }
            QComboBox::drop-down { border: none; width: 20px; }
            QComboBox QAbstractItemView { background: #2B2930; color: #E6E1E5;
                                           selection-background-color: #3E4758; }
            QPushButton { background: #3E4758; color: #E6E1E5; border: none;
                          border-radius: 20px; padding: 8px 16px; font-weight: bold; }
            QPushButton:hover { background: #49454F; }
            QSlider::groove:horizontal { background: #49454F; height: 4px; border-radius: 2px; }
            QSlider::handle:horizontal { background: #A8C7FA; width: 16px; height: 16px;
                                          margin: -6px 0; border-radius: 8px; }
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # ── Движок ──────────────────────────────────────────────
        engine_group = QGroupBox(tr("lbl_engine", default="Движок"))
        engine_layout = QVBoxLayout(engine_group)

        self._radio_edge = QRadioButton("Edge-TTS  (" + tr("engine_online", default="онлайн") + ")")
        self._radio_rhvoice = QRadioButton("RHVoice  (" + tr("engine_offline", default="офлайн") + ")")
        self._radio_silero = QRadioButton("Silero  (" + tr("engine_offline", default="офлайн") + ")")
        self._radio_sapi = QRadioButton("SAPI5  (" + tr("engine_offline", default="офлайн") + ")")

        engine_layout.addWidget(self._radio_edge)
        engine_layout.addWidget(self._radio_rhvoice)
        engine_layout.addWidget(self._radio_silero)
        engine_layout.addWidget(self._radio_sapi)

        # Restore saved engine
        try:
            saved = self.parent().settings.get("tts.voice_type", "edge") if self.parent() else "edge"
        except Exception:
            saved = "edge"
        {"edge": self._radio_edge, "rhvoice": self._radio_rhvoice,
         "silero": self._radio_silero, "sapi": self._radio_sapi}.get(saved, self._radio_edge).setChecked(True)

        layout.addWidget(engine_group)

        # ── Голос ──────────────────────────────────────────────
        voice_group = QGroupBox(tr("cat_voice", default="Голос"))
        voice_layout = QVBoxLayout(voice_group)

        voice_row = QHBoxLayout()
        voice_row.addWidget(QLabel(tr("lbl_voice", default="Голос:")))
        self._voice_combo = QComboBox()
        voice_row.addWidget(self._voice_combo)
        voice_layout.addLayout(voice_row)

        # Тест
        test_row = QHBoxLayout()
        test_btn = QPushButton("\u25b6 " + tr("btn_test_voice", default="Тест"))
        test_btn.clicked.connect(self._test_voice)
        test_row.addWidget(test_btn)
        test_row.addStretch()
        voice_layout.addLayout(test_row)

        layout.addWidget(voice_group)

        # ── Параметры ──────────────────────────────────────────
        params_group = QGroupBox(tr("params", default="Параметры"))
        params_layout = QVBoxLayout(params_group)

        # Скорость
        speed_row = QHBoxLayout()
        speed_row.addWidget(QLabel(tr("lbl_speed", default="Скорость:")))
        self._speed_slider = QSlider(Qt.Orientation.Horizontal)
        self._speed_slider.setRange(-50, 50)
        self._speed_slider.setValue(0)
        speed_row.addWidget(self._speed_slider)
        self._speed_label = QLabel("0")
        self._speed_slider.valueChanged.connect(lambda v: self._speed_label.setText(str(v)))
        speed_row.addWidget(self._speed_label)
        params_layout.addLayout(speed_row)

        # Питч
        pitch_row = QHBoxLayout()
        pitch_row.addWidget(QLabel(tr("lbl_pitch", default="Питч:")))
        self._pitch_slider = QSlider(Qt.Orientation.Horizontal)
        self._pitch_slider.setRange(-50, 50)
        self._pitch_slider.setValue(0)
        pitch_row.addWidget(self._pitch_slider)
        self._pitch_label = QLabel("0")
        self._pitch_slider.valueChanged.connect(lambda v: self._pitch_label.setText(str(v)))
        pitch_row.addWidget(self._pitch_label)
        params_layout.addLayout(pitch_row)

        layout.addWidget(params_group)

        # ── Кнопки ─────────────────────────────────────────────
        btn_layout = QHBoxLayout()
        apply_btn = QPushButton(tr("btn_apply", default="Применить"))
        apply_btn.setStyleSheet("background-color: #4caf50; color: white; font-weight: bold;")
        apply_btn.clicked.connect(self._apply)
        btn_layout.addWidget(apply_btn)
        close_btn = QPushButton(tr("btn_close", default="Закрыть"))
        close_btn.clicked.connect(self.close)
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)

        # ── Сигналы ────────────────────────────────────────────
        self._radio_edge.toggled.connect(self._on_engine_toggled)
        self._radio_rhvoice.toggled.connect(self._on_engine_toggled)
        self._radio_silero.toggled.connect(self._on_engine_toggled)
        self._radio_sapi.toggled.connect(self._on_engine_toggled)

        # Загрузить голоса для сохранённого движка
        self._refresh_voices()
        # Восстановить сохранённые параметры
        try:
            self._speed_slider.setValue(self.parent().settings.get("tts.male_rate", 0) if self.parent() else 0)
            self._pitch_slider.setValue(self.parent().settings.get("tts.male_pitch", 0) if self.parent() else 0)
        except Exception:
            pass

    def _selected_engine(self) -> str:
        if self._radio_edge.isChecked():
            return "edge"
        if self._radio_rhvoice.isChecked():
            return "rhvoice"
        if self._radio_silero.isChecked():
            return "silero"
        if self._radio_sapi.isChecked():
            return "sapi"
        return "edge"

    def _on_engine_toggled(self, checked):
        if checked:
            self._refresh_voices()

    def _refresh_voices(self):
        """Обновить список голосов для выбранного движка."""
        self._voice_combo.clear()
        engine = self._selected_engine()
        tts = self.parent().tts if self.parent() and hasattr(self.parent(), 'tts') else None
        if not tts:
            return
        try:
            voices = tts.get_voices()
        except Exception:
            return
        for v in voices:
            vtype = v.get("type", "edge")
            code = v.get("code", "")
            name = v.get("name", code)
            # Фильтруем по движку
            if engine == "edge" and vtype in ("edge", "profile"):
                self._voice_combo.addItem(name, code)
            elif engine == "rhvoice" and vtype in ("rhvoice",):
                self._voice_combo.addItem(name, code)
            elif engine == "silero" and vtype in ("silero",):
                self._voice_combo.addItem(name, code)
            elif engine == "sapi" and vtype in ("sapi",):
                self._voice_combo.addItem(name, code)
        # Восстановить текущий голос
        if tts.voice:
            idx = self._voice_combo.findData(tts.voice)
            if idx >= 0:
                self._voice_combo.setCurrentIndex(idx)

    def _test_voice(self):
        """Протестировать выбранный голос."""
        tts = self.parent().tts if self.parent() and hasattr(self.parent(), 'tts') else None
        if not tts:
            return
        code = self._voice_combo.currentData()
        if code:
            tts.voice = code
        engine = self._selected_engine()
        tts.voice_type = engine
        tts.rate = self._speed_slider.value()
        tts.pitch = self._pitch_slider.value()
        import asyncio
        def _run():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(tts.speak(tr("test_text", default="Привет! Это тест голоса.")))
            except Exception:
                pass
            finally:
                loop.close()
        threading.Thread(target=_run, daemon=True).start()

    def _apply(self):
        """Применить выбранный движок и голос."""
        tts = self.parent().tts if self.parent() and hasattr(self.parent(), 'tts') else None
        settings = self.parent().settings if self.parent() and hasattr(self.parent(), 'settings') else None
        if not tts or not settings:
            return
        try:
            engine = self._selected_engine()
            code = self._voice_combo.currentData()
            # Сначала ставим движок, потом голос — чтобы set_voice не перезаписал движок
            tts.voice_type = engine
            if code:
                tts.voice = code
            tts.rate = self._speed_slider.value()
            tts.pitch = self._pitch_slider.value()
            settings.set("tts.voice_type", engine)
            if code:
                settings.set("tts.male_voice", code)
                settings.set("tts.male_ru_voice", code)
            settings.set("tts.male_rate", tts.rate)
            settings.set("tts.male_pitch", tts.pitch)
            # Обновить GUI если открыт
            if hasattr(self.parent(), 'engine_combo'):
                idx_map = {"edge": 0, "rhvoice": 2, "sapi": 2, "silero": 2}
                self.parent().engine_combo.blockSignals(True)
                self.parent().engine_combo.setCurrentIndex(idx_map.get(engine, 0))
                self.parent().engine_combo.blockSignals(False)
            # Обновить индикатор
            if hasattr(self.parent(), '_update_voice_indicator'):
                self.parent()._update_voice_indicator()
            logger.info(f"[TTS] Engine applied: {engine}, voice: {code}, rate: {tts.rate}, pitch: {tts.pitch}")
        except Exception as e:
            logger.error(f"[TTS] Engine apply error: {e}")


class LiveModeWindow(QWidget):
    """Отдельное окно управления Live Mode (как в ArtMoney)."""

    def __init__(self, main_window):
        super().__init__()
        self.main = main_window
        self.setWindowTitle("Live Mode")
        self.setMinimumWidth(420)
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # --- Включение Live Mode ---
        live_toggle_group = QGroupBox("Управление")
        live_toggle_layout = QHBoxLayout(live_toggle_group)
        self.live_enable_check = QCheckBox(tr("chk_enable_live"))
        self.live_enable_check.stateChanged.connect(self.main._toggle_live_mode)
        live_toggle_layout.addWidget(self.live_enable_check)
        self.live_interval = QSpinBox()
        self.live_interval.setRange(100, 10000)
        self.live_interval.setValue(200)
        self.live_interval.setSuffix(tr("suffix_ms"))
        live_toggle_layout.addWidget(QLabel(tr("lbl_interval")))
        live_toggle_layout.addWidget(self.live_interval)
        layout.addWidget(live_toggle_group)

        # --- Детекция изменений + выбор окна ---
        detect_group = QGroupBox("Захват")
        detect_layout = QVBoxLayout(detect_group)

        detect_row = QHBoxLayout()
        self.detect_changes_check = QCheckBox(tr("chk_detect_changes"))
        self.detect_changes_check.setChecked(True)
        detect_row.addWidget(self.detect_changes_check)
        detect_row.addStretch()
        detect_layout.addLayout(detect_row)

        window_row = QHBoxLayout()
        self.select_window_btn = QPushButton("Выбрать окно")
        self.select_window_btn.setToolTip("Выбрать окно приложения для сканирования")
        self.select_window_btn.clicked.connect(self.main._select_app_window)
        window_row.addWidget(self.select_window_btn)
        self.selected_window_label = QLabel("")
        self.selected_window_label.setStyleSheet("color: #81C784; font-weight: bold;")
        window_row.addWidget(self.selected_window_label)
        window_row.addStretch()
        detect_layout.addLayout(window_row)

        layout.addWidget(detect_group)

        # --- Режим сканирования ---
        mode_group = QGroupBox(tr("scan_mode_group"))
        mode_layout = QHBoxLayout(mode_group)
        mode_layout.addWidget(QLabel(tr("lbl_scan_mode")))
        self.scan_mode_combo = QComboBox()
        self.scan_mode_combo.addItem(tr("scan_mode_normal"), "normal")
        self.scan_mode_combo.addItem(tr("scan_mode_realtime_clear"), "realtime_clear")
        self.scan_mode_combo.addItem(tr("scan_mode_realtime_keep"), "realtime_keep")
        current_mode = self.main.settings.get("scan.mode", "realtime_keep")
        idx = self.scan_mode_combo.findData(current_mode)
        if idx >= 0:
            self.scan_mode_combo.setCurrentIndex(idx)
        self.scan_mode_combo.currentIndexChanged.connect(self.main._on_scan_mode_changed)
        mode_layout.addWidget(self.scan_mode_combo)

        self.separate_regions_check = QCheckBox(tr("chk_separate_regions"))
        self.separate_regions_check.setChecked(self.main.settings.get("scan.separate_regions", True))
        self.separate_regions_check.stateChanged.connect(self.main._on_separate_regions_changed)
        mode_layout.addWidget(self.separate_regions_check)
        mode_layout.addStretch()
        layout.addWidget(mode_group)

        # --- Режимы озвучки ---
        speech_group = QGroupBox(tr("speech_modes_group"))
        speech_layout = QVBoxLayout(speech_group)
        speech_btn_layout = QHBoxLayout()
        self._speech_mode_buttons = {}
        for n, key in ((1, "speech_mode_1"), (2, "speech_mode_2"),
                       (3, "speech_mode_3"), (4, "speech_mode_4")):
            btn = QPushButton(tr(key))
            btn.setCheckable(True)
            btn.clicked.connect(lambda _checked, mode=n: self.main._toggle_speech_mode(mode))
            speech_btn_layout.addWidget(btn)
            self._speech_mode_buttons[n] = btn
        speech_layout.addLayout(speech_btn_layout)
        self.speech_mode_indicator = QLabel(tr("speech_mode_none"))
        self.speech_mode_indicator.setStyleSheet("color: #EF5350; font-weight: bold;")
        speech_layout.addWidget(self.speech_mode_indicator)
        layout.addWidget(speech_group)

        # --- Live TTS ---
        live_tts_group = QGroupBox(tr("live_tts_group"))
        live_tts_layout = QHBoxLayout(live_tts_group)
        self.live_tts_btn = QPushButton(tr("btn_start_live_tts"))
        self.live_tts_btn.clicked.connect(self.main._start_live_tts)
        live_tts_layout.addWidget(self.live_tts_btn)
        self.live_tts_stop_btn = QPushButton(tr("btn_stop_live_tts"))
        self.live_tts_stop_btn.clicked.connect(self.main._stop_live_tts)
        self.live_tts_stop_btn.setEnabled(False)
        live_tts_layout.addWidget(self.live_tts_stop_btn)
        self.live_tts_status = QLabel(tr("live_tts_idle"))
        self.live_tts_status.setStyleSheet("color: #EF5350; font-weight: bold;")
        live_tts_layout.addWidget(self.live_tts_status)
        layout.addWidget(live_tts_group)

        # --- Построчный режим ---
        line_mode_group = QGroupBox(tr("line_by_line_group", default="Построчный режим"))
        line_mode_layout = QHBoxLayout(line_mode_group)
        self.line_by_line_check = QCheckBox(tr("chk_line_by_line", default="Озвучивать построчно (мгновенно)"))
        self.line_by_line_check.setToolTip(tr("tip_line_by_line", default="Каждая новая строка текста озвучивается сразу, не дожидаясь следующего сканирования"))
        self.line_by_line_check.stateChanged.connect(self.main._on_line_by_line_changed)
        line_mode_layout.addWidget(self.line_by_line_check)
        self.line_by_line_status = QLabel("")
        self.line_by_line_status.setStyleSheet("color: #CAC4D0; font-size: 12px;")
        line_mode_layout.addWidget(self.line_by_line_status)
        line_mode_layout.addStretch()
        layout.addWidget(line_mode_group)

        # --- Sync mode ---
        sync_group = QGroupBox("Синхронизация голоса")
        sync_layout = QHBoxLayout(sync_group)
        self.sync_mode_check = QCheckBox("Голос = приоритет (скан ждёт озвучки)")
        self.sync_mode_check.setToolTip("Сканирование暂停ается пока TTS не закончит говорить.\nСубтитры и голос идут синхронно.")
        self.sync_mode_check.stateChanged.connect(self.main._on_sync_mode_changed)
        sync_layout.addWidget(self.sync_mode_check)
        self.sync_mode_status = QLabel("")
        self.sync_mode_status.setStyleSheet("color: #CAC4D0; font-size: 12px;")
        sync_layout.addWidget(self.sync_mode_status)
        sync_layout.addStretch()
        layout.addWidget(sync_group)

        # --- Sentence mode ---
        sentence_group = QGroupBox("Пофразовое чтение")
        sentence_layout = QHBoxLayout(sentence_group)
        self.sentence_mode_check = QCheckBox("Читать по предложениям (текст стирается)")
        self.sentence_mode_check.setToolTip("Каждое предложение читается отдельно (~1с).\nПрочитанное стирается из результата. Голос и текст синхронны.")
        self.sentence_mode_check.stateChanged.connect(self.main._on_sentence_mode_changed)
        sentence_layout.addWidget(self.sentence_mode_check)
        self.sentence_mode_status = QLabel("")
        self.sentence_mode_status.setStyleSheet("color: #CAC4D0; font-size: 12px;")
        sentence_layout.addWidget(self.sentence_mode_status)
        sentence_layout.addStretch()
        layout.addWidget(sentence_group)

        # --- Chunk+Clear mode ---
        chunk_group = QGroupBox("Chunk+Clear режим")
        chunk_layout = QHBoxLayout(chunk_group)
        self.chunk_clear_check = QCheckBox("Читать по очереди (очистка после каждого)")
        self.chunk_clear_check.setToolTip(
            "OCR → буфер → TTS читает по порядку → очистка.\n"
            "Текст накапливается, голос читает по очереди.\n"
            "Прочитанное исчезает, можно перечитать.\n"
            "Взаимоисключается с построчным и пофразовым режимами.")
        self.chunk_clear_check.stateChanged.connect(self.main._on_chunk_clear_changed)
        chunk_layout.addWidget(self.chunk_clear_check)
        self.chunk_clear_status = QLabel("")
        self.chunk_clear_status.setStyleSheet("color: #CAC4D0; font-size: 12px;")
        chunk_layout.addWidget(self.chunk_clear_status)
        chunk_layout.addStretch()
        layout.addWidget(chunk_group)

        layout.addStretch()

    def closeEvent(self, event):
        """При закрытии — просто скрываем, не уничтожаем."""
        event.ignore()
        self.hide()


class MainWindow(QMainWindow):
    text_detected_signal = pyqtSignal(str)
    _on_sentence_spoken_signal = pyqtSignal(str)
    _on_scan_timing_signal = pyqtSignal(float, str, float)
    _ocr_ready_signal = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.settings = Settings()

        # Initialize language from settings (default "en")
        ui_lang = self.settings.get("ui.language", "en")
        set_language(ui_lang)

        # Defer heavy OCR init — show window first, then load EasyOCR
        self.ocr = None
        self.ocr_ready = False
        self.tts = TTSEngine(self.settings)
        # Load per-role pitch/rate from settings
        for role_key, role_name in [("male", "male"), ("female", "female"), ("narrator", "narrator")]:
            rate_val = self.settings.get(f"tts.{role_key}_rate", 0)
            pitch_val = self.settings.get(f"tts.{role_key}_pitch", 0)
            self.tts.set_role_rate(role_name, rate_val)
            self.tts.set_role_pitch(role_name, pitch_val)
        self.overlay = OverlayWidget(self.settings)

        # Region list
        self.regions = []
        self.region_results = {}
        self._load_regions()

        # LiveScanner — created with None OCR, will get it when ready
        self.live_scanner = LiveScanner(None, self.settings, self.tts)
        self.live_scanner.enable_tts_streaming(self.tts)

        # Live Mode — отдельное окно (создаётся после _setup_ui)
        self.live_window = None

        # Start EasyOCR in background
        self._ocr_ready_signal.connect(self._on_ocr_ready)
        threading.Thread(target=self._init_ocr_background, daemon=True).start()

        # Активный голос для озвучки — переключается кнопками [Мужчина][Девушка][Рассказчик]
        self._active_voice = self.settings.get("tts.voice", "ru-RU-DmitryNeural")
        self._active_voice_type = self.settings.get("tts.voice_type", "edge")
        self._active_role = "male"
        self._active_ru_voice = self.settings.get("tts.male_ru_voice", "ru-RU-DmitryNeural")
        self._active_en_voice = self.settings.get("tts.male_en_voice", "en-US-GuyNeural")
        self._active_voice_pitch = 0

        # Bridge scanner callbacks to GUI thread
        def _safe_text_detected(text):
            self.text_detected_signal.emit(text)
        self.live_scanner.on_text_detected = _safe_text_detected

        def _safe_scan_started():
            self._on_scan_started_gui()
        self.live_scanner.on_scan_started = _safe_scan_started

        def _safe_scan_stopped():
            self._on_scan_stopped_gui()
        self.live_scanner.on_scan_stopped = _safe_scan_stopped

        self.text_detected_signal.connect(self._on_text_detected_gui)

        # Scan timing callback
        def _safe_scan_timing(seconds, text, confidence=0.0):
            self._on_scan_timing_signal.emit(seconds, text, confidence)
        self.live_scanner.on_scan_timing = _safe_scan_timing
        self._on_scan_timing_signal.connect(self._on_scan_timing_gui)

        # Пофразовый режим: callback когда предложение стёрто
        def _safe_text_spoken(remaining_text):
            self._on_sentence_spoken_signal.emit(remaining_text)
        self.live_scanner.on_text_spoken = _safe_text_spoken
        self._on_sentence_spoken_signal.connect(self._on_sentence_spoken_gui)

        self.ocr_worker = None
        self.scanning = False
        self._live_mode_lock = threading.Lock()
        # Активный режим озвучки (1-4) или None. Хоткеи 1-4 включают/выключают.
        self._active_speech_mode = None

        # Dedicated event loop for LiveScanner
        self._live_event_loop = asyncio.new_event_loop()
        self._live_loop_thread = threading.Thread(target=self._run_live_loop, daemon=True)
        self._live_loop_thread.start()

        # Region overlay
        self.region_overlay = RegionOverlay()
        self.region_overlay.region_moved.connect(self._on_region_moved)
        self.region_overlay.region_deleted.connect(self._on_region_deleted)
        self.region_overlay.region_right_clicked.connect(self._show_region_context_menu)
        self.region_overlay.voice_speak.connect(self._on_overlay_voice_speak)
        self.region_overlay.name_speak.connect(self._on_overlay_name_speak)
        self.region_overlay.start_clicked.connect(self._on_overlay_start)
        self.region_overlay.stop_clicked.connect(self._on_overlay_stop)
        self.region_overlay.frame_opacity_changed.connect(
            lambda v: self.settings.set("overlay.opacity", v / 100.0))
        self.region_overlay.panel_opacity_changed.connect(
            lambda v: self.settings.set("overlay.panel_opacity", v / 100.0))
        self._tts_playing = False
        self._highlight_words = []
        self._highlight_index = 0
        self._highlight_start_time = 0
        self._prev_highlight_pos = None
        self._prev_highlight_len = 0

        # Tray
        self.tray_icon = None
        self.tray_menu = None

        # Apply saved font scale
        saved_scale = self.settings.get("ui.scale", 12)
        if saved_scale != 12:
            app = QApplication.instance()
            if app:
                app.setStyleSheet(f"* {{ font-size: {saved_scale}px; }}")

        # Build UI
        self._logs_tab_shown = False
        self._setup_ui()
        self._setup_tray()
        self._setup_shortcuts()
        self._load_settings()

        # Show region overlay
        self.region_overlay.set_opacity(
            frame_opacity=int(self.settings.get("overlay.opacity", 0.8) * 100),
            panel_opacity=int(self.settings.get("overlay.panel_opacity", 0.9) * 100)
        )
        self.region_overlay.set_regions(self.regions)
        self.region_overlay.show_overlay()
        # Raise main window above overlay so tabs are accessible
        self.raise_()
        self.activateWindow()

        logger.info("=" * 60)
        logger.info("[START] Application started")
        if self.ocr:
            logger.info(f"   OCR: {self.ocr.engine_type}, GPU: {'ON' if self.ocr.use_gpu else 'OFF'}")
        logger.info(f"   Regions: {len(self.regions)}")
        logger.info("=" * 60)

    def _run_live_loop(self):
        asyncio.set_event_loop(self._live_event_loop)
        self._live_event_loop.run_forever()

    def _init_ocr_background(self):
        """Load EasyOCR in background thread to avoid blocking the UI."""
        try:
            ocr = OCREngine(self.settings)
            self.ocr = ocr
            self.ocr_ready = True
            self.live_scanner.ocr_engine = ocr
            logger.info(f"[OCR] Ready: {ocr.engine_type}, GPU={'ON' if ocr.use_gpu else 'OFF'}")
            self._ocr_ready_signal.emit()
        except Exception as e:
            logger.error(f"[OCR] Init failed: {e}")

    def _on_ocr_ready(self):
        self.statusBar().showMessage("OCR ready", 3000)

    # ==================== UI ====================

    def _setup_ui(self):
        self.setWindowTitle(tr("app_title"))
        self.setMinimumSize(640, 360)
        self.resize(960, 620)

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setSpacing(0)
        main_layout.setContentsMargins(0, 0, 0, 0)

        # ── Toolbar ─────────────────────────────────────────────────
        toolbar = QFrame()
        toolbar.setStyleSheet(
            "QFrame { background: #211F26; border-bottom: 1px solid #49454F; }")
        toolbar.setFixedHeight(38)
        tb_layout = QHBoxLayout(toolbar)
        tb_layout.setContentsMargins(8, 2, 8, 2)
        tb_layout.setSpacing(6)

        tb_layout.addWidget(QLabel("\U0001f50d"))
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText(tr("search_placeholder", default="Поиск..."))
        self._search_input.setFixedWidth(180)
        self._search_input.setStyleSheet(
            "QLineEdit { background: #211F26; color: #E6E1E5; border: 1px solid #49454F; "
            "border-radius: 12px; padding: 6px 12px; }")
        self._search_input.textChanged.connect(self._on_search_changed)
        tb_layout.addWidget(self._search_input)

        tb_layout.addStretch()

        self._view_grid_btn = QToolButton()
        self._view_grid_btn.setText("\u2b1a")
        self._view_grid_btn.setToolTip(tr("view_grid", default="Сетка"))
        self._view_grid_btn.setCheckable(True)
        self._view_grid_btn.setChecked(True)
        self._view_grid_btn.clicked.connect(lambda: self._set_view_mode("grid"))
        tb_layout.addWidget(self._view_grid_btn)

        self._view_list_btn = QToolButton()
        self._view_list_btn.setText("\u2630")
        self._view_list_btn.setToolTip(tr("view_list", default="Список"))
        self._view_list_btn.setCheckable(True)
        self._view_list_btn.clicked.connect(lambda: self._set_view_mode("list"))
        tb_layout.addWidget(self._view_list_btn)

        tb_layout.addWidget(QLabel(tr("sort_label", default="Сортировка:")))
        self._sort_combo = QComboBox()
        self._sort_combo.addItems([
            tr("sort_importance", default="По важности"),
            tr("sort_name", default="По имени"),
            tr("sort_recent", default="Недавние"),
        ])
        self._sort_combo.setFixedWidth(140)
        self._sort_combo.currentIndexChanged.connect(self._on_sort_changed)
        tb_layout.addWidget(self._sort_combo)

        main_layout.addWidget(toolbar)

        # ── Splitter: tree + content ────────────────────────────────
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setStyleSheet(
            "QSplitter::handle { background: #49454F; width: 2px; }")
        main_layout.addWidget(splitter)

        # ── Left: tree navigation ───────────────────────────────────
        self._nav_tree = QTreeWidget()
        self._nav_tree.setHeaderHidden(True)
        self._nav_tree.setAnimated(True)
        self._nav_tree.setIndentation(16)
        self._nav_tree.setMinimumWidth(160)
        self._nav_tree.setMaximumWidth(240)
        self._nav_tree.setStyleSheet("""
            QTreeWidget {
                background: #1D1B20; color: #E6E1E5;
                border: none; outline: none; font-size: 13px;
            }
            QTreeWidget::item {
                padding: 8px 12px; border-radius: 8px; margin: 2px 6px;
            }
            QTreeWidget::item:selected {
                background: #3E4758; color: #DAE2F9;
            }
            QTreeWidget::item:hover:!selected {
                background: #2B2930;
            }
            QTreeWidget::branch { background: #1D1B20; }
        """)
        self._nav_tree.setIconSize(QPixmap(20, 20).size())

        tree_items = []
        categories = [
            ("\U0001f3e0 " + tr("cat_main",    default="Главная"),      "main",      "a6e3a1"),
            ("\U0001f4dd " + tr("tab_result",  default="Результат"),    "result",    "a6e3a1"),
            ("\U0001f4cb " + tr("cat_logs",    default="Логи"),         "logs",      "a6adc8"),
        ]
        for label, key, _color in categories:
            item = QTreeWidgetItem([label])
            item.setData(0, Qt.ItemDataRole.UserRole, key)
            item.setSizeHint(0, item.sizeHint(0) + QSize(0, 6))
            self._nav_tree.addTopLevelItem(item)
            tree_items.append((key, item))

        splitter.addWidget(self._nav_tree)

        # ── Right: stacked content ──────────────────────────────────
        self._content_stack = QStackedWidget()
        self._content_stack.setStyleSheet("QStackedWidget { background: #1C1B1F; }")
        splitter.addWidget(self._content_stack)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([180, 780])

        # ── Build pages ─────────────────────────────────────────────
        self._page_home     = self._create_home_page()
        self._page_result   = self._create_result_tab()
        self._page_logs     = self._create_logs_tab()

        self._content_stack.addWidget(self._page_home)
        self._content_stack.addWidget(self._page_result)
        self._content_stack.addWidget(self._page_logs)

        # ── Detached dialogs (вкладки, вынесенные в отдельные окна) ──
        self._dialog_voice = _DetachedDialog(
            tr("cat_voice", default="Голос"), self, "voice")
        self._dialog_voice.set_content(self._create_tts_tab())

        self._dialog_translate = _DetachedDialog(
            tr("cat_translate", default="Переводчик"), self, "translate")
        self._dialog_translate.set_content(self._create_translator_tab())

        self._dialog_web = _DetachedDialog(
            tr("cat_web", default="Веб-сервер"), self, "web")
        self._dialog_web.set_content(self._create_web_tab())

        self._dialog_settings = _DetachedDialog(
            tr("cat_settings", default="Настройки"), self, "settings")
        self._dialog_settings.set_content(self._create_settings_tab())

        self._page_map = {
            "main":      self._page_home,
            "result":    self._page_result,
            "logs":      self._page_logs,
        }
        self._tree_item_map = {key: item for key, item in tree_items}

        self._nav_tree.currentItemChanged.connect(self._on_nav_changed)
        self._nav_tree.setCurrentItem(tree_items[0][1])

        # ── Status bar ──────────────────────────────────────────────
        self.statusBar().showMessage(tr("status_ready"))
        self.live_status_label = QLabel(tr("live_off_label"))
        self.statusBar().addPermanentWidget(self.live_status_label)

        self.ocr_indicator = OCRIndicator(self)
        self.statusBar().addPermanentWidget(self.ocr_indicator)

        self.scan_timing_label = QLabel("")
        self.scan_timing_label.setStyleSheet("color: #a6adc8; font-size: 11px; padding: 0 8px;")
        self.statusBar().addPermanentWidget(self.scan_timing_label)

        tray_btn = QPushButton(tr("btn_to_tray"))
        tray_btn.setStyleSheet("padding: 2px 8px; font-size: 11px;")
        tray_btn.clicked.connect(self._minimize_to_tray)
        self.statusBar().addPermanentWidget(tray_btn)

        self._pipette_mode = False

        # Live Mode — отдельное окно
        self.live_window = LiveModeWindow(self)

    # ==================== NAVIGATION ====================

    def _on_nav_changed(self, current, previous):
        if current is None:
            return
        key = current.data(0, Qt.ItemDataRole.UserRole)
        page = self._page_map.get(key)
        if page:
            self._content_stack.setCurrentWidget(page)
            if key == "logs" and not self._logs_tab_shown:
                self._logs_tab_shown = True
                self._refresh_logs()

    # ==================== HOME PAGE ====================

    def _create_home_page(self):
        page = QWidget()
        page.setStyleSheet("background: #1C1B1F;")
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { border: none; background: #1C1B1F; }")

        content = QWidget()
        content.setStyleSheet("background: #1C1B1F;")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(10)

        hdr = QLabel("\U0001f3e0 " + tr("cat_main", default="Главная"))
        hdr.setStyleSheet("color: #A8C7FA; font-size: 20px; font-weight: bold; padding: 4px 0;")
        layout.addWidget(hdr)

        cards_widget = QWidget()
        cards_widget.setStyleSheet("background: transparent;")
        self._home_cards_layout = QGridLayout(cards_widget)
        self._home_cards_layout.setSpacing(8)
        self._home_cards_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(cards_widget)

        self._home_cards_data = [
            {"id": "scan_region", "icon": "\U0001f50d", "label": tr("btn_scan_selected", default="Сканировать"),
             "priority": 1, "cat": "main", "action": self._scan_selected_region},
            {"id": "scan_all",    "icon": "\U0001f50e", "label": tr("btn_scan_all", default="Все области"),
             "priority": 2, "cat": "main", "action": self._scan_all_regions},
            {"id": "live_tts",    "icon": "\U0001f50a", "label": tr("btn_start_live_tts", default="Live TTS"),
             "priority": 3, "cat": "main", "action": self._start_live_tts},
            {"id": "stop_live",   "icon": "\u23f9",     "label": tr("btn_stop_live_tts", default="Стоп"),
             "priority": 4, "cat": "main", "action": self._stop_live_tts},
            {"id": "add_region",  "icon": "\u2795",     "label": tr("btn_add", default="Добавить"),
             "priority": 5, "cat": "main", "action": self._add_region},
            {"id": "result",     "icon": "\U0001f4dd", "label": tr("tab_result", default="Результат"),
             "priority": 6, "cat": "main", "action": lambda: self._nav_tree.setCurrentItem(self._tree_item_map["result"])},
            {"id": "translate",   "icon": "\U0001f4d6", "label": tr("tab_translator", default="Переводчик"),
             "priority": 7, "cat": "online", "action": lambda: self._dialog_translate.show()},
            {"id": "web_server",  "icon": "\U0001f310", "label": tr("tab_web_live", default="Веб"),
             "priority": 8, "cat": "online", "action": lambda: self._dialog_web.show()},
            {"id": "voice_set",   "icon": "\U0001f3a4", "label": tr("cat_voice", default="Голос"),
             "priority": 9, "cat": "voice", "action": lambda: self._dialog_voice.show()},
            {"id": "profiles",    "icon": "\u2b50",     "label": tr("profiles_group", default="Профили"),
             "priority": 10, "cat": "voice", "action": lambda: self._dialog_voice.show()},
            {"id": "tts_edge",    "icon": "\u2601\uFE0F", "label": "Edge (онлайн)",
             "priority": 10, "cat": "voice", "action": lambda: self._open_tts_engine_dialog("edge")},
            {"id": "tts_rhvoice", "icon": "\U0001f399",  "label": "RHVoice (офлайн)",
             "priority": 11, "cat": "voice", "action": lambda: self._open_tts_engine_dialog("rhvoice")},
            {"id": "tts_silero",  "icon": "\U0001f916",  "label": "Silero (офлайн)",
             "priority": 12, "cat": "voice", "action": lambda: self._open_tts_engine_dialog("silero")},
            {"id": "tts_sapi",    "icon": "\U0001f4bb",  "label": "SAPI5 (офлайн)",
             "priority": 13, "cat": "voice", "action": lambda: self._open_tts_engine_dialog("sapi")},
            {"id": "settings",    "icon": "\u2699\uFE0F",  "label": tr("cat_settings", default="Настройки"),
             "priority": 14, "cat": "system", "action": lambda: self._dialog_settings.show()},
        ]
        self._view_mode = "grid"
        self._rebuild_home_cards()

        layout.addWidget(self._embed(self._create_regions_tab()))
        layout.addWidget(self._embed(self._create_scan_tab()))

        layout.addStretch()
        scroll.setWidget(content)
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        return page

    @staticmethod
    def _embed(widget):
        frame = QFrame()
        frame.setStyleSheet("QFrame { background: #211F26; border: 1px solid #49454F; border-radius: 12px; }")
        fl = QVBoxLayout(frame)
        fl.setContentsMargins(8, 8, 8, 8)
        fl.addWidget(widget)
        return frame

    def _rebuild_home_cards(self):
        layout = self._home_cards_layout
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        cards = list(self._home_cards_data)
        sort_idx = self._sort_combo.currentIndex() if hasattr(self, '_sort_combo') else 0
        if sort_idx == 0:
            cards.sort(key=lambda c: c["priority"])
        elif sort_idx == 1:
            cards.sort(key=lambda c: c["label"])
        elif sort_idx == 2:
            cards.sort(key=lambda c: c["priority"], reverse=True)

        search = self._search_input.text().strip().lower() if hasattr(self, '_search_input') else ""
        if search:
            cards = [c for c in cards if search in c["label"].lower() or search in c["id"].lower()]

        self._home_cards = []
        for i, card in enumerate(cards):
            btn = QPushButton(f"{card['icon']}\n{card['label']}")
            btn.setMinimumSize(120, 70)
            btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            btn.clicked.connect(card["action"])

            cat = card.get("cat", "")
            colors = {"main": ("#0D3B2E", "#81C784"), "online": ("#0D2E4A", "#64B5F6"),
                      "voice": ("#4A3520", "#FFB74D"), "system": ("#3D1F2E", "#EF5350")}
            bg, fg = colors.get(cat, ("#211F26", "#E6E1E5"))

            btn.setStyleSheet(f"""
                QPushButton {{
                    background: {bg}; color: {fg}; border: 1px solid #49454F;
                    border-radius: 12px; font-size: 13px; padding: 12px 4px;
                    text-align: center;
                }}
                QPushButton:hover {{ border: 1px solid {fg}; background: {bg}22; }}
            """)

            if self._view_mode == "grid":
                row, col = divmod(i, 4)
                layout.addWidget(btn, row, col)
            else:
                layout.addWidget(btn, i, 0)

            self._home_cards.append(btn)

    def _set_view_mode(self, mode):
        self._view_mode = mode
        self._view_grid_btn.setChecked(mode == "grid")
        self._view_list_btn.setChecked(mode == "list")
        self._rebuild_home_cards()

    def _on_sort_changed(self, index):
        self._rebuild_home_cards()

    def _on_search_changed(self, text):
        self._rebuild_home_cards()

    # ==================== TAB HELPERS ====================

    def _on_scale_changed(self, value):
        self.scale_label.setText(f"{value}px")
        app = QApplication.instance()
        if app:
            app.setStyleSheet(f"* {{ font-size: {value}px; }}")
        self.settings.set("ui.scale", value)

    def _get_voices_list(self):
        try:
            voices = self.tts.get_voices()
            return [v["code"] for v in voices]
        except Exception:
            return ["ru-RU-DmitryNeural", "ru-RU-SvetlanaNeural", "en-US-JennyNeural", "en-US-GuyNeural"]

    # ---------- Tab builders ----------

    def _create_regions_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(12)

        info = QLabel(tr("regions_hint"))
        info.setStyleSheet("color: #81C784; font-size: 13px; padding: 8px;")
        layout.addWidget(info)

        list_group = QGroupBox(tr("regions_list_group"))
        list_layout = QVBoxLayout(list_group)

        self.edit_mode_btn = QPushButton(tr("btn_edit_mode_off"))
        self.edit_mode_btn.setCheckable(True)
        self.edit_mode_btn.setChecked(True)
        self.edit_mode_btn.clicked.connect(self._toggle_edit_mode)
        list_layout.addWidget(self.edit_mode_btn)

        self.region_list = QListWidget()
        self.region_list.setMinimumHeight(200)
        self._refresh_region_list()
        list_layout.addWidget(self.region_list)

        btn_layout = QHBoxLayout()
        add_btn = QPushButton(tr("btn_add"))
        add_btn.clicked.connect(self._add_region)
        btn_layout.addWidget(add_btn)
        edit_btn = QPushButton(tr("btn_edit"))
        edit_btn.clicked.connect(self._edit_region)
        btn_layout.addWidget(edit_btn)
        del_btn = QPushButton(tr("btn_delete"))
        del_btn.clicked.connect(self._delete_region)
        btn_layout.addWidget(del_btn)
        list_layout.addLayout(btn_layout)

        layout.addWidget(list_group)
        layout.addStretch()
        return tab

    def _create_scan_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(12)

        ocr_group = QGroupBox(tr("ocr_settings_group"))
        ocr_layout = QHBoxLayout(ocr_group)
        self.lang_combo = QComboBox()
        self.lang_combo.addItems(["rus+eng", "rus", "eng", "rus+eng+ukr"])
        ocr_layout.addWidget(QLabel(tr("lbl_language")))
        ocr_layout.addWidget(self.lang_combo)
        self.ocr_engine_combo = QComboBox()
        self.ocr_engine_combo.addItems(["easyocr"])
        ocr_layout.addWidget(QLabel(tr("lbl_engine")))
        ocr_layout.addWidget(self.ocr_engine_combo)
        self.gpu_check = QCheckBox(tr("chk_use_gpu"))
        ocr_layout.addWidget(self.gpu_check)
        ocr_layout.addStretch()
        layout.addWidget(ocr_group)

        # ── Live Mode: кнопка открытия отдельного окна ──
        live_open_group = QGroupBox(tr("live_mode_group"))
        live_open_layout = QHBoxLayout(live_open_group)
        self._open_live_btn = QPushButton("Открыть Live Mode")
        self._open_live_btn.setToolTip("Открыть панель управления Live Mode в отдельном окне")
        self._open_live_btn.clicked.connect(self._toggle_live_window)
        live_open_layout.addWidget(self._open_live_btn)
        self._live_window_status = QLabel("")
        self._live_window_status.setStyleSheet("color: #CAC4D0; font-size: 12px;")
        live_open_layout.addWidget(self._live_window_status)
        live_open_layout.addStretch()
        layout.addWidget(live_open_group)

        # Режимы сканирования (выбор в интерфейсе)
        mode_group = QGroupBox(tr("scan_mode_group"))
        mode_layout = QHBoxLayout(mode_group)
        mode_layout.addWidget(QLabel(tr("lbl_scan_mode")))
        self.scan_mode_combo = QComboBox()
        self.scan_mode_combo.addItem(tr("scan_mode_normal"), "normal")
        self.scan_mode_combo.addItem(tr("scan_mode_realtime_clear"), "realtime_clear")
        self.scan_mode_combo.addItem(tr("scan_mode_realtime_keep"), "realtime_keep")
        current_mode = self.settings.get("scan.mode", "realtime_keep")
        idx = self.scan_mode_combo.findData(current_mode)
        if idx >= 0:
            self.scan_mode_combo.setCurrentIndex(idx)
        self.scan_mode_combo.currentIndexChanged.connect(self._on_scan_mode_changed)
        mode_layout.addWidget(self.scan_mode_combo)

        self.separate_regions_check = QCheckBox(tr("chk_separate_regions"))
        self.separate_regions_check.setChecked(self.settings.get("scan.separate_regions", True))
        self.separate_regions_check.stateChanged.connect(self._on_separate_regions_changed)
        mode_layout.addWidget(self.separate_regions_check)
        mode_layout.addStretch()
        layout.addWidget(mode_group)

        # Режимы озвучки: ручной выбор кнопками + индикатор активного режима
        speech_group = QGroupBox(tr("speech_modes_group"))
        speech_layout = QVBoxLayout(speech_group)
        speech_btn_layout = QHBoxLayout()
        self._speech_mode_buttons = {}
        for n, key in ((1, "speech_mode_1"), (2, "speech_mode_2"),
                       (3, "speech_mode_3"), (4, "speech_mode_4")):
            btn = QPushButton(tr(key))
            btn.setCheckable(True)
            btn.clicked.connect(lambda _checked, mode=n: self._toggle_speech_mode(mode))
            speech_btn_layout.addWidget(btn)
            self._speech_mode_buttons[n] = btn
        speech_layout.addLayout(speech_btn_layout)
        self.speech_mode_indicator = QLabel(tr("speech_mode_none"))
        self.speech_mode_indicator.setStyleSheet("color: #EF5350; font-weight: bold;")
        speech_layout.addWidget(self.speech_mode_indicator)
        layout.addWidget(speech_group)

        scan_group = QGroupBox(tr("scan_group"))
        scan_layout = QHBoxLayout(scan_group)
        scan_btn = QPushButton(tr("btn_scan_selected"))
        scan_btn.clicked.connect(self._scan_selected_region)
        scan_layout.addWidget(scan_btn)
        scan_all_btn = QPushButton(tr("btn_scan_all"))
        scan_all_btn.clicked.connect(self._scan_all_regions)
        scan_layout.addWidget(scan_all_btn)
        layout.addWidget(scan_group)

        layout.addStretch()
        return tab

    def _create_web_tab(self):
        """Вкладка управления веб-сервером: старт/стоп, логи, подключённые клиенты."""
        from PyQt6.QtCore import QTimer

        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        hdr = QLabel("\U0001f310 " + tr("tab_web_live") + "  \u2014  " + tr("online_tab_info",
            default="Онлайн: Edge-TTS + Переводчик + Веб-сервер + Телефон"))
        hdr.setStyleSheet("color: #A8C7FA; font-size: 13px; padding: 6px 10px; "
                          "background: #0D2E4A; border-radius: 8px; font-weight: bold;")
        layout.addWidget(hdr)

        # === Статус и IP ===
        status_row = QHBoxLayout()

        self._ws_status = QLabel("● Сервер остановлен")
        self._ws_status.setStyleSheet("color: #f85149; font-size: 14px; font-weight: bold;")
        status_row.addWidget(self._ws_status)

        status_row.addStretch()

        self._ws_ip_label = QLabel("")
        self._ws_ip_label.setStyleSheet("color: #89b4fa; font-size: 13px;")
        status_row.addWidget(self._ws_ip_label)

        layout.addLayout(status_row)

        # === Подключение к телефону (обратный маршрут) ===
        phone_group = QGroupBox("Подключение к телефону")
        phone_layout = QVBoxLayout(phone_group)

        phone_ip_row = QHBoxLayout()
        phone_ip_row.addWidget(QLabel("IP телефона:"))
        self._phone_ip_input = QLineEdit("")
        self._phone_ip_input.setPlaceholderText("192.168.0.xxx")
        self._phone_ip_input.setStyleSheet("color: #FFFFFF;")
        phone_ip_row.addWidget(self._phone_ip_input)
        btn_phone_connect = QPushButton("🔗 Подключиться")
        btn_phone_connect.setStyleSheet(
            "QPushButton { background: #7B68EE; color: white; border-radius: 6px; padding: 4px 12px; }")
        btn_phone_connect.clicked.connect(self._ws_connect_to_phone)
        phone_ip_row.addWidget(btn_phone_connect)
        phone_layout.addLayout(phone_ip_row)

        self._phone_status = QLabel("")
        self._phone_status.setStyleSheet("color: #8B949E; font-size: 12px;")
        phone_layout.addWidget(self._phone_status)

        layout.addWidget(phone_group)

        # === Порт + кнопки ===
        ctrl_row = QHBoxLayout()

        ctrl_row.addWidget(QLabel("Порт:"))
        self._ws_port = QLineEdit(str(self.settings.get("web_tts.port", 8080)))
        self._ws_port.setFixedWidth(80)
        ctrl_row.addWidget(self._ws_port)

        self._ws_btn_start = QPushButton("▶ Старт")
        self._ws_btn_start.setStyleSheet(
            "QPushButton { background: #238636; color: white; border-radius: 6px; padding: 6px 16px; font-weight: bold; }"
            "QPushButton:hover { background: #2ea043; }")
        self._ws_btn_start.clicked.connect(self._ws_start)
        ctrl_row.addWidget(self._ws_btn_start)

        self._ws_btn_stop = QPushButton("■ Стоп")
        self._ws_btn_stop.setStyleSheet(
            "QPushButton { background: #b62324; color: white; border-radius: 6px; padding: 6px 16px; font-weight: bold; }"
            "QPushButton:hover { background: #da3633; }")
        self._ws_btn_stop.clicked.connect(self._ws_stop)
        self._ws_btn_stop.setEnabled(False)
        ctrl_row.addWidget(self._ws_btn_stop)

        layout.addLayout(ctrl_row)

        # === Клиенты ===
        clients_row = QHBoxLayout()
        clients_row.addWidget(QLabel("Подключений SSE:"))
        self._ws_clients_label = QLabel("0")
        self._ws_clients_label.setStyleSheet("color: #3fb950; font-weight: bold;")
        clients_row.addWidget(self._ws_clients_label)
        clients_row.addStretch()
        layout.addLayout(clients_row)

        # === Логи ===
        layout.addWidget(QLabel("Логи сервера:"))
        self._ws_log = QTextEdit()
        self._ws_log.setReadOnly(True)
        self._ws_log.setMaximumHeight(200)
        self._ws_log.setStyleSheet(
            "QTextEdit { background: #0d1117; color: #8b949e; font-family: Consolas, monospace; "
            "font-size: 12px; border: 1px solid #30363d; border-radius: 6px; padding: 4px; }")
        layout.addWidget(self._ws_log)

        # === Кнопка открытия в браузере ===
        open_row = QHBoxLayout()
        btn_browser = QPushButton("🌐 Открыть /live в браузере")
        btn_browser.setStyleSheet("color: #89b4fa;")
        btn_browser.clicked.connect(lambda: self._ws_open_browser())
        open_row.addWidget(btn_browser)

        # === QR-код для подключения телефона ===
        qr_row = QHBoxLayout()
        btn_qr = QPushButton("📱 QR-код для телефона")
        btn_qr.setStyleSheet("color: #7B68EE;")
        btn_qr.clicked.connect(self._ws_show_qr)
        qr_row.addWidget(btn_qr)

        # === Поиск телефона в сети ===
        phone_row = QHBoxLayout()
        btn_find_phone = QPushButton("🔍 Найти телефон")
        btn_find_phone.setStyleSheet("color: #3FB950;")
        btn_find_phone.clicked.connect(self._ws_find_phone)
        phone_row.addWidget(btn_find_phone)
        self._ws_phone_status = QLabel("")
        self._ws_phone_status.setStyleSheet("color: #8B949E; font-size: 12px;")
        phone_row.addWidget(self._ws_phone_status)
        phone_row.addStretch()

        open_row.addStretch()
        layout.addLayout(open_row)
        layout.addLayout(qr_row)
        layout.addLayout(phone_row)

        layout.addStretch()

        # Таймер обновления статуса
        self._ws_timer = QTimer(self)
        self._ws_timer.timeout.connect(self._ws_update_status)
        self._ws_timer.start(2000)
        # Register log callback if server already running
        try:
            from web_tts_server import web_tts, _log_callbacks, _log_lock
            if web_tts.is_running:
                self._ws_log_cb = lambda msg: self._ws_log_append_safe(msg)
                with _log_lock:
                    if self._ws_log_cb not in _log_callbacks:
                        _log_callbacks.append(self._ws_log_cb)
        except Exception:
            pass
        self._ws_update_status()

        return tab

    def _ws_start(self):
        port = int(self._ws_port.text() or "8080")
        self.settings.set("web_tts.port", port)
        self.settings.set("web_tts.enabled", True)
        try:
            from web_tts_server import web_tts, _log_callbacks, _log_lock
            web_tts.port = port
            if web_tts.is_running:
                web_tts.stop()
                QTimer.singleShot(300, lambda: self._ws_start_after_stop(port))
                return
            self._ws_start_after_stop(port)
        except Exception as e:
            self._ws_log.append(f"[ERROR] {e}")

    def _ws_start_after_stop(self, port):
        try:
            from web_tts_server import web_tts, _log_callbacks, _log_lock, register_gui_callbacks
            web_tts.start()
            self._ws_log.append("[OK] Сервер запущен на порту " + str(port))
            self._ws_log_cb = lambda msg: self._ws_log_append_safe(msg)
            with _log_lock:
                if self._ws_log_cb not in _log_callbacks:
                    _log_callbacks.append(self._ws_log_cb)
            # Регистрируем callback-и для связи web API с GUI
            register_gui_callbacks({
                "start_live": self._web_start_live,
                "stop_live": self._web_stop_live,
                "force_stop_tts": self._web_force_stop_tts,
                "is_live_running": lambda: self.live_scanner.running if self.live_scanner else False,
                "is_ocr_ready": lambda: self.ocr is not None and self.ocr.easyocr_reader is not None,
                "select_window": self._web_select_window,
                "clear_window": self._web_clear_window,
                "get_ocr": lambda: self.ocr,
            })
            self._ws_update_status()
        except Exception as e:
            self._ws_log.append(f"[ERROR] {e}")

    def _ws_stop(self):
        try:
            from web_tts_server import web_tts, _log_callbacks, _log_lock
            if hasattr(self, '_ws_log_cb'):
                with _log_lock:
                    try:
                        _log_callbacks.remove(self._ws_log_cb)
                    except ValueError:
                        pass
            if web_tts.is_running:
                web_tts.stop()
            QTimer.singleShot(500, self._ws_stop_done)
        except Exception as e:
            self._ws_log.append(f"[ERROR] {e}")

    def _ws_stop_done(self):
        self._ws_log.append("[OK] Сервер остановлен")
        self._ws_update_status()

    def _ws_log_append_safe(self, msg):
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(0, lambda m=msg: self._ws_log.append(m))

    def _ws_update_status(self):
        try:
            from web_tts_server import web_tts, _subtitle_clients
            running = web_tts.is_running
            clients = len(_subtitle_clients)
            if running:
                self._ws_status.setText("● Сервер работает")
                self._ws_status.setStyleSheet("color: #3fb950; font-size: 14px; font-weight: bold;")
                self._ws_btn_start.setEnabled(False)
                self._ws_btn_stop.setEnabled(True)
                # Показать IP для телефона
                import socket
                try:
                    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    s.connect(("8.8.8.8", 80))
                    local_ip = s.getsockname()[0]
                    s.close()
                except Exception:
                    local_ip = "127.0.0.1"
                self._ws_ip_label.setText(f"Для телефона: {local_ip}:{web_tts.port}")
            else:
                self._ws_status.setText("● Сервер остановлен")
                self._ws_status.setStyleSheet("color: #f85149; font-size: 14px; font-weight: bold;")
                self._ws_btn_start.setEnabled(True)
                self._ws_btn_stop.setEnabled(False)
                self._ws_ip_label.setText("")
            self._ws_clients_label.setText(str(clients))
        except Exception:
            pass

    def _ws_open_browser(self):
        import webbrowser
        port = self.settings.get("web_tts.port", 8080)
        webbrowser.open(f"http://127.0.0.1:{port}/control")

    def _ws_show_qr(self):
        """Показывает QR-код для подключения телефона к PC-серверу."""
        import socket
        import io

        port = self.settings.get("web_tts.port", 8080)
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
        except Exception:
            local_ip = "127.0.0.1"

        url = f"http://{local_ip}:{port}"

        # Генерируем QR-код
        try:
            import qrcode
            qr = qrcode.QRCode(version=1, box_size=6, border=2)
            qr.add_data(url)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            png_data = buf.getvalue()
        except ImportError:
            # Если qrcode не установлен — показываем URL текстом
            QMessageBox = __import__("PyQt6.QtWidgets", fromlist=["QMessageBox"]).QMessageBox
            QMessageBox.information(self, "QR-код", f"Отсканируйте камерой телефона:\n\n{url}")
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("📱 QR-код для телефона")
        dialog.setFixedSize(340, 380)
        layout = QVBoxLayout(dialog)

        label_title = QLabel("Отсканируйте QR-код камерой телефона")
        label_title.setStyleSheet("color: #C9D1D9; font-size: 14px;")
        label_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(label_title)

        label_qr = QLabel()
        qimg = QImage.fromData(png_data)
        label_qr.setPixmap(QPixmap.fromImage(qimg))
        label_qr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(label_qr)

        label_url = QLabel(url)
        label_url.setStyleSheet("color: #89B4FA; font-size: 13px; font-family: monospace;")
        label_url.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label_url.setWordWrap(True)
        layout.addWidget(label_url)

        dialog.exec()

    def _ws_connect_to_phone(self):
        """ПК подключается к телефону как клиент для TTS."""
        from PyQt6.QtCore import QThread, pyqtSignal
        import socket

        ip = self._phone_ip_input.text().strip()
        if not ip:
            self._phone_status.setText("✕ Введите IP телефона")
            self._phone_status.setStyleSheet("color: #F85149; font-size: 12px;")
            return

        port = self.settings.get("web_tts.port", 8080)
        self._phone_status.setText(f"Подключение к {ip}...")
        self._phone_status.setStyleSheet("color: #FFAA00; font-size: 12px;")

        class ConnectThread(QThread):
            result = pyqtSignal(bool, str)

            def run(self):
                try:
                    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    s.settimeout(3)
                    s.connect((ip, port))
                    request = f"GET /health HTTP/1.1\r\nHost: {ip}:{port}\r\nConnection: close\r\n\r\n"
                    s.sendall(request.encode())
                    response = b""
                    while True:
                        chunk = s.recv(1024)
                        if not chunk:
                            break
                        response += chunk
                        if b"\r\n\r\n" in response:
                            break
                    s.close()
                    text = response.decode("utf-8", errors="ignore")
                    if "200 OK" in text and '"ok":true' in text:
                        self.result.emit(True, f"✓ Подключено к {ip}")
                    else:
                        self.result.emit(False, f"✕ Неверный ответ от {ip}")
                except Exception as e:
                    self.result.emit(False, f"✕ Ошибка: {e}")

        def on_result(ok, msg):
            if ok:
                self._phone_status.setText(msg)
                self._phone_status.setStyleSheet("color: #3FB950; font-size: 12px;")
                self._ws_log.append(f"[PHONE] {msg}")
            else:
                self._phone_status.setText(msg)
                self._phone_status.setStyleSheet("color: #F85149; font-size: 12px;")
                self._ws_log.append(f"[PHONE] {msg}")

        thread = ConnectThread()
        thread.result.connect(on_result)
        thread.start()

    def _ws_find_phone(self):
        """Ищет Phone TTS Server в локальной сети."""
        from PyQt6.QtCore import QThread, pyqtSignal
        from phone_scanner import scan_for_phone

        self._ws_phone_status.setText("Поиск...")
        self._ws_phone_status.setStyleSheet("color: #FFAA00; font-size: 12px;")

        class PhoneSearchThread(QThread):
            found = pyqtSignal(str, str)
            not_found = pyqtSignal()

            def run(self):
                result = scan_for_phone(port=8080, timeout=1.0, max_workers=30)
                if result:
                    self.found.emit(result[0], result[1])
                else:
                    self.not_found.emit()

        def on_found(ip, engine):
            self._ws_phone_status.setText(f"✓ Найден: {ip} ({engine})")
            self._ws_phone_status.setStyleSheet("color: #3FB950; font-size: 12px;")
            self._ws_log.append(f"[PHONE] Найден телефон: {ip} ({engine})")
            # Автоматически заполняем поле IP для подключения к телефону
            if hasattr(self, '_phone_ip_input'):
                self._phone_ip_input.setText(ip)

        def on_not_found():
            self._ws_phone_status.setText("✕ Телефон не найден")
            self._ws_phone_status.setStyleSheet("color: #F85149; font-size: 12px;")
            self._ws_log.append("[PHONE] Телефон не найден в сети")

        thread = PhoneSearchThread()
        thread.found.connect(on_found)
        thread.not_found.connect(on_not_found)
        thread.start()

    def _create_result_tab(self):
        page = QWidget()
        page.setStyleSheet("background: #1C1B1F;")
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { border: none; background: #1C1B1F; }")

        tab = QWidget()
        tab.setStyleSheet("background: #1C1B1F;")
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(10)

        hdr = QLabel("\U0001f4dd " + tr("tab_result", default="Результат"))
        hdr.setStyleSheet("color: #a6e3a1; font-size: 18px; font-weight: bold; padding: 4px 0;")
        layout.addWidget(hdr)

        result_group = QGroupBox(tr("result_group"))
        result_layout = QVBoxLayout(result_group)
        self.result_text = QTextEdit()
        self.result_text.setReadOnly(False)
        self.result_text.setMinimumHeight(150)
        self.result_text.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.result_text.setWordWrapMode(QTextOption.WrapMode.WordWrap)
        result_layout.addWidget(self.result_text)

        # --- Быстрый выбор голоса (кнопки снаружи рамки) ---
        voice_sel_layout = QHBoxLayout()
        voice_sel_layout.setSpacing(6)

        self._voice_btn_male = QPushButton(tr("vp_male", default="Мужчина"))
        self._voice_btn_male.setIcon(icon_male())
        self._voice_btn_male.setCheckable(True)
        self._voice_btn_male.setStyleSheet("QPushButton { padding: 5px 12px; font-weight: bold; }"
                                           "QPushButton:checked { background-color: #2196F3; color: white; }")
        self._voice_btn_male.clicked.connect(lambda: self._switch_active_voice("male"))
        self._voice_btn_male.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._voice_btn_male.customContextMenuRequested.connect(
            lambda pos: self._show_voice_tree_menu("male", self._voice_btn_male.mapToGlobal(pos)))
        voice_sel_layout.addWidget(self._voice_btn_male)

        self._voice_btn_female = QPushButton(tr("vp_female", default="Девушка"))
        self._voice_btn_female.setIcon(icon_female())
        self._voice_btn_female.setCheckable(True)
        self._voice_btn_female.setStyleSheet("QPushButton { padding: 5px 12px; font-weight: bold; }"
                                             "QPushButton:checked { background-color: #E91E63; color: white; }")
        self._voice_btn_female.clicked.connect(lambda: self._switch_active_voice("female"))
        self._voice_btn_female.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._voice_btn_female.customContextMenuRequested.connect(
            lambda pos: self._show_voice_tree_menu("female", self._voice_btn_female.mapToGlobal(pos)))
        voice_sel_layout.addWidget(self._voice_btn_female)

        self._voice_btn_narrator = QPushButton(tr("vp_narrator", default="Рассказчик"))
        self._voice_btn_narrator.setIcon(icon_narrator())
        self._voice_btn_narrator.setCheckable(True)
        self._voice_btn_narrator.setStyleSheet("QPushButton { padding: 5px 12px; font-weight: bold; }"
                                               "QPushButton:checked { background-color: #9C27B0; color: white; }")
        self._voice_btn_narrator.clicked.connect(lambda: self._switch_active_voice("narrator"))
        self._voice_btn_narrator.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._voice_btn_narrator.customContextMenuRequested.connect(
            lambda pos: self._show_voice_tree_menu("narrator", self._voice_btn_narrator.mapToGlobal(pos)))
        voice_sel_layout.addWidget(self._voice_btn_narrator)

        voice_sel_layout.addSpacing(12)

        # --- Быстрый выбор движка TTS ---
        self._engine_btn_edge = QPushButton("\u2601\uFE0F Edge")
        self._engine_btn_edge.setToolTip("Edge-TTS (онлайн)")
        self._engine_btn_edge.setStyleSheet(
            "QPushButton { padding: 3px 8px; font-size: 11px; border-radius: 3px; "
            "background: #313244; color: #89b4fa; }"
            "QPushButton:hover { background: #45475a; }")
        self._engine_btn_edge.clicked.connect(lambda: self._open_tts_engine_dialog("edge"))
        voice_sel_layout.addWidget(self._engine_btn_edge)

        self._engine_btn_rhvoice = QPushButton("\U0001f399 RH")
        self._engine_btn_rhvoice.setToolTip("RHVoice (офлайн)")
        self._engine_btn_rhvoice.setStyleSheet(
            "QPushButton { padding: 3px 8px; font-size: 11px; border-radius: 3px; "
            "background: #313244; color: #a6e3a1; }"
            "QPushButton:hover { background: #45475a; }")
        self._engine_btn_rhvoice.clicked.connect(lambda: self._open_tts_engine_dialog("rhvoice"))
        voice_sel_layout.addWidget(self._engine_btn_rhvoice)

        self._engine_btn_silero = QPushButton("\U0001f916 Silero")
        self._engine_btn_silero.setToolTip("Silero (офлайн)")
        self._engine_btn_silero.setStyleSheet(
            "QPushButton { padding: 3px 8px; font-size: 11px; border-radius: 3px; "
            "background: #313244; color: #f9e2af; }"
            "QPushButton:hover { background: #45475a; }")
        self._engine_btn_silero.clicked.connect(lambda: self._open_tts_engine_dialog("silero"))
        voice_sel_layout.addWidget(self._engine_btn_silero)

        self._engine_btn_sapi = QPushButton("\U0001f4bb SAPI")
        self._engine_btn_sapi.setToolTip("SAPI5 (офлайн)")
        self._engine_btn_sapi.setStyleSheet(
            "QPushButton { padding: 3px 8px; font-size: 11px; border-radius: 3px; "
            "background: #313244; color: #cba6f7; }"
            "QPushButton:hover { background: #45475a; }")
        self._engine_btn_sapi.clicked.connect(lambda: self._open_tts_engine_dialog("sapi"))
        voice_sel_layout.addWidget(self._engine_btn_sapi)

        voice_sel_layout.addSpacing(8)
        self._voice_indicator = QLabel()
        self._voice_indicator.setStyleSheet(
            "color: #a6adc8; font-size: 11px; padding: 2px 6px; "
            "border-radius: 3px; background: #1e1e2e;")
        self._voice_indicator.setMinimumWidth(200)
        voice_sel_layout.addWidget(self._voice_indicator)
        voice_sel_layout.addStretch()
        result_layout.addLayout(voice_sel_layout)
        self._update_voice_indicator()

        # TTS state polling timer — обновляет индикатор каждые 200мс
        self._tts_state_timer = QTimer(self)
        self._tts_state_timer.setInterval(200)
        self._tts_state_timer.timeout.connect(self._tick_tts_state)
        self._tts_state_timer.start()

        btn_layout = QHBoxLayout()
        speak_btn = QPushButton(tr("btn_speak"))
        speak_btn.clicked.connect(self._speak_result)
        btn_layout.addWidget(speak_btn)
        save_audio_btn = QPushButton(tr("btn_save_audio"))
        save_audio_btn.clicked.connect(self._save_audio_result)
        btn_layout.addWidget(save_audio_btn)
        save_btn = QPushButton(tr("btn_save_txt"))
        save_btn.clicked.connect(self._save_result)
        btn_layout.addWidget(save_btn)
        copy_btn = QPushButton(tr("btn_copy"))
        copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(self.result_text.toPlainText()))
        btn_layout.addWidget(copy_btn)
        clear_btn = QPushButton(tr("btn_clear"))
        clear_btn.clicked.connect(self._clear_result)
        btn_layout.addWidget(clear_btn)
        stop_btn = QPushButton(tr("btn_stop_speaking", default="Стоп"))
        stop_btn.setIcon(icon_stop())
        stop_btn.setStyleSheet("background-color: #f44336; color: white; font-weight: bold;")
        stop_btn.clicked.connect(self._stop_all_speech)
        btn_layout.addWidget(stop_btn)
        result_layout.addLayout(btn_layout)

        # Test dialogue — эмоциональный диалог для проверки голосов
        test_dialogue_btn = QPushButton("\U0001f3ad " + tr("btn_test_dialogue", default="Тест диалога"))
        test_dialogue_btn.setStyleSheet(
            "background-color: #9c27b0; color: white; "
            "font-weight: bold; padding: 6px 16px;"
        )
        test_dialogue_btn.clicked.connect(self._test_dialogue)
        btn_layout2 = QHBoxLayout()
        btn_layout2.addWidget(test_dialogue_btn)
        result_layout.addLayout(btn_layout2)

        layout.addWidget(result_group)
        layout.addStretch()

        scroll.setWidget(tab)
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        return page

    # ==================== VOICE TREE MENU ====================

    def _show_voice_tree_menu(self, role: str, global_pos):
        """Дерево голосов по ПКМ на кнопке М/Ж/Р.
        Категории: Edge (RU), RHVoice, Silero, Persona, Accent, EN, Профили.
        Сворачиваемые группы, скрытые категории запоминаются.
        """
        if not hasattr(self, '_hidden_voice_categories'):
            self._hidden_voice_categories = self.settings.get("tts.hidden_voice_categories", {})

        try:
            params = self.region_overlay.get_voice_params(role) if hasattr(self, 'region_overlay') else {}
            current_ru = params.get("ru_voice", "ru-RU-DmitryNeural")
            current_en = params.get("en_voice", "en-US-GuyNeural")
        except Exception:
            current_ru = self.settings.get("tts.male_voice", "ru-RU-DmitryNeural")
            current_en = self.settings.get("tts.en_voice", "en-US-GuyNeural")

        all_voices = self.tts.EDGE_VOICES if hasattr(self.tts, 'EDGE_VOICES') else []

        hidden_types = set(self.settings.get("tts.hidden_voice_types", []))
        def _type_visible(v):
            return v.get("type", "edge") not in hidden_types

        ru_all = [v for v in all_voices if v.get("lang") == "ru" and _type_visible(v)]
        en_all = [v for v in all_voices if v.get("lang") == "en" and _type_visible(v)]
        _slavic_langs = {"uk", "pl", "cs", "sk", "bg", "hr", "sr", "sl"}
        accent_all = [v for v in all_voices if _type_visible(v) and
                      ("multilingual" in v.get("code", "").lower() or
                       "multilingual" in v.get("name", "").lower() or
                       v.get("lang") in _slavic_langs)]
        persona_all = [v for v in all_voices if v.get("type") == "persona"]

        if role == "male":
            ru_filtered = [v for v in ru_all if v.get("gender", "").lower() == "male"] + persona_all
            en_filtered = [v for v in en_all if v.get("gender", "").lower() == "male"]
            accent_filtered = [v for v in accent_all if v.get("gender", "").lower() == "male"]
        elif role == "female":
            ru_filtered = [v for v in ru_all if v.get("gender", "").lower() == "female"] + persona_all
            en_filtered = [v for v in en_all if v.get("gender", "").lower() == "female"]
            accent_filtered = [v for v in accent_all if v.get("gender", "").lower() == "female"]
        else:
            ru_filtered = ru_all + persona_all
            en_filtered = en_all
            accent_filtered = accent_all

        ru_edge = [v for v in ru_filtered if v.get("type") == "edge"]
        ru_rhvoice = [v for v in ru_filtered if v.get("type") == "rhvoice"]
        ru_silero = [v for v in ru_filtered if v.get("type") == "silero"]
        ru_persona = [v for v in ru_filtered if v.get("type") == "persona"]
        en_edge = [v for v in en_filtered if v.get("type") == "edge"]
        accent_multi = [v for v in accent_filtered if "multilingual" in v.get("code", "").lower()
                        or "multilingual" in v.get("name", "").lower()]
        accent_slavic = [v for v in accent_filtered if v.get("lang") in _slavic_langs]

        # User profiles
        profile_voices = []
        if hasattr(self, 'tts') and hasattr(self.tts, 'profile_manager'):
            for p in self.tts.profile_manager.get_all():
                profile_voices.append({"code": f"profile:{p.name}", "name": f"★ {p.name}", "type": "profile"})

        categories = [
            ("🇷🇺 Edge-TTS", "edge_ru", ru_edge, current_ru, "ru"),
            ("🇷🇺 RHVoice", "rhvoice", ru_rhvoice, current_ru, "ru"),
            ("🇷🇺 Silero", "silero", ru_silero, current_ru, "ru"),
            ("🎭 Персонажи", "persona", ru_persona, current_ru, "ru"),
            ("🌍 Multilingual", "accent_multi", accent_multi, current_ru, "ru"),
            ("🌍 Славянские", "accent_slavic", accent_slavic, current_ru, "ru"),
            ("🇬🇧 English", "en_edge", en_edge, current_en, "en"),
            ("★ Профили", "profiles", profile_voices, current_ru, "ru"),
        ]

        parent = None
        for w in QApplication.topLevelWidgets():
            if w.isVisible() and w.windowFlags() & Qt.WindowType.Window:
                parent = w
                break
        if parent is None:
            parent = self

        menu = QMenu(parent)
        menu.setStyleSheet(
            "QMenu { background-color: #1e1e2e; color: #cdd6f4; border: 1px solid #444; padding: 4px; }"
            "QMenu::item { padding: 4px 16px; }"
            "QMenu::item:selected { background-color: #0f3460; }"
            "QMenu::item:disabled { color: #666; }"
            "QMenu::separator { height: 1px; background: #444; margin: 2px 4px; }"
        )

        title = menu.addAction(f"--- {role.upper()} ---")
        title.setEnabled(False)
        menu.addSeparator()

        for cat_label, cat_key, voices, current_voice, lang_key in categories:
            hidden = self._hidden_voice_categories.get(cat_key, False)

            # Category header (clickable toggle)
            if hidden:
                header_text = f"▶ {cat_label} ({len(voices)})"
            else:
                header_text = f"▼ {cat_label} ({len(voices)})"

            header_action = menu.addAction(header_text)
            header_action.triggered.connect(
                lambda checked, k=cat_key: self._toggle_voice_category(k))

            if not hidden:
                for v in voices:
                    code = v["code"]
                    a = menu.addAction(f"    {v['name']}")
                    if code == current_voice:
                        font = a.font()
                        font.setBold(True)
                        a.setFont(font)
                        a.setText(f"    ✓ {v['name']}")
                    if lang_key == "ru":
                        a.triggered.connect(
                            lambda checked, c=code, r=role: self._on_voice_tree_selected(r, "ru", c))
                    else:
                        a.triggered.connect(
                            lambda checked, c=code, r=role: self._on_voice_tree_selected(r, "en", c))

            menu.addSeparator()

        menu.exec(global_pos)

    def _toggle_voice_category(self, cat_key: str):
        """Скрыть/показать категорию голосов."""
        if not hasattr(self, '_hidden_voice_categories'):
            self._hidden_voice_categories = {}
        current = self._hidden_voice_categories.get(cat_key, False)
        self._hidden_voice_categories[cat_key] = not current
        self.settings.set("tts.hidden_voice_categories", self._hidden_voice_categories)

    def _on_voice_tree_selected(self, role: str, lang: str, voice_code: str):
        """Обработка выбора голоса из дерева."""
        key = f"tts.{role}_{lang}_voice" if lang else f"tts.{role}_voice"
        self.settings.set(key, voice_code)

        # Определяем тип голоса по коду
        voice_type = self._detect_voice_type(voice_code)
        type_key = f"tts.{role}_{lang}_voice_type" if lang else f"tts.{role}_voice_type"
        self.settings.set(type_key, voice_type)

        if hasattr(self, 'region_overlay'):
            params = self.region_overlay.get_voice_params(role)
            params[f"{lang}_voice"] = voice_code
            params[f"{lang}_voice_type"] = voice_type
            self.region_overlay.set_voice_params(role, params)

        if role == getattr(self, '_active_role', None):
            if lang == "ru":
                self._active_ru_voice = voice_code
                self._active_voice = voice_code
                self.tts.ru_voice = voice_code
                self.tts.ru_voice_type = voice_type
            else:
                self._active_en_voice = voice_code
                self.tts.en_voice = voice_code
                self.tts.en_voice_type = voice_type
            self._active_voice_type = voice_type
            self.tts.voice = voice_code
            self.tts.voice_type = voice_type
            self._update_voice_indicator()

        self.statusBar().showMessage(f"Голос {role} ({lang}): {voice_code} [{voice_type}]")
        logger.info(f"[VOICE] {role} ({lang}) -> {voice_code} (type={voice_type})")

    def _switch_active_voice(self, role: str):
        """Переключить активный голос: male / female / narrator.
        Каждая роль имеет ru_voice и en_voice — voice выбирается по языку текста.
        """
        self._active_role = role
        # Per-role ru/en voice settings
        ru_defaults = {"male": "ru-RU-DmitryNeural", "female": "ru-RU-SvetlanaNeural", "narrator": "ru-RU-DmitryNeural"}
        en_defaults = {"male": "en-US-GuyNeural", "female": "en-US-JennyNeural", "narrator": "en-US-GuyMultilingualNeural"}
        ru_key = f"tts.{role}_ru_voice"
        en_key = f"tts.{role}_en_voice"

        if hasattr(self, 'region_overlay'):
            params = self.region_overlay.get_voice_params(role)
            self._active_ru_voice = params.get("ru_voice", self.settings.get(ru_key, ru_defaults.get(role, "ru-RU-DmitryNeural")))
            self._active_en_voice = params.get("en_voice", self.settings.get(en_key, en_defaults.get(role, "en-US-GuyNeural")))
            self._active_voice_pitch = params.get("pitch", 0)
            self._active_voice_rate = params.get("speed", 0)
        else:
            self._active_ru_voice = self.settings.get(ru_key, ru_defaults.get(role, "ru-RU-DmitryNeural"))
            self._active_en_voice = self.settings.get(en_key, en_defaults.get(role, "en-US-GuyNeural"))
            self._active_voice_pitch = self.settings.get(f"tts.{role}_pitch", 0)
            self._active_voice_rate = self.settings.get(f"tts.{role}_rate", 0)

        self._active_voice = self._active_ru_voice
        self._active_voice_type = self._detect_voice_type(self._active_ru_voice)
        self.tts.voice = self._active_voice
        self.tts.voice_type = self._active_voice_type
        self.tts.ru_voice = self._active_ru_voice
        self.tts.ru_voice_type = self._detect_voice_type(self._active_ru_voice)
        self.tts.en_voice = self._active_en_voice
        self.tts.en_voice_type = self._detect_voice_type(self._active_en_voice)
        self.tts.set_role(role)
        self._active_voice_pitch = self.tts.get_role_pitch(role)
        self._active_voice_rate = self.tts.get_role_rate(role)
        self._update_voice_indicator()
        for btn_role, btn in [("male", self._voice_btn_male),
                              ("female", self._voice_btn_female),
                              ("narrator", self._voice_btn_narrator)]:
            btn.setChecked(btn_role == role)
        if hasattr(self, 'region_overlay'):
            self.region_overlay.set_active_role(role)
        logger.info(f"[VOICE] Активный: {role} -> ru={self._active_ru_voice} en={self._active_en_voice}")

    def _detect_voice_type(self, voice_code: str) -> str:
        """Detect voice engine type from voice code prefix."""
        if voice_code.startswith("rhvoice:"):
            return "rhvoice"
        elif voice_code.startswith("silero:"):
            return "silero"
        elif voice_code.startswith("persona:"):
            return "persona"
        return "edge"

    def _select_voice_for_text(self, text: str):
        """Выбрать ru или en голос в зависимости от языка текста."""
        cyrillic = sum(1 for c in text if '\u0400' <= c <= '\u04FF')
        total = sum(1 for c in text if c.isalpha())
        if total > 0 and cyrillic / total > 0.3:
            self._active_voice = self._active_ru_voice
            self._active_voice_type = self._detect_voice_type(self._active_ru_voice)
        else:
            self._active_voice = self._active_en_voice
            self._active_voice_type = self._detect_voice_type(self._active_en_voice)
        self.tts.voice = self._active_voice
        self.tts.voice_type = self._active_voice_type

    def _update_voice_indicator(self):
        """Обновить индикатор: голос + статус воспроизведения."""
        voice = getattr(self, '_active_voice', '...')
        is_playing = getattr(self, '_tts_playing', False)

        if is_playing:
            self._voice_indicator.setText(f"  ▶ {voice}")
            self._voice_indicator.setStyleSheet(
                "color: #a6e3a1; font-size: 11px; padding: 2px 6px; "
                "border-radius: 3px; background: #1e1e2e; font-weight: bold;")
        else:
            self._voice_indicator.setText(f"  ■ {voice}")
            self._voice_indicator.setStyleSheet(
                "color: #a6adc8; font-size: 11px; padding: 2px 6px; "
                "border-radius: 3px; background: #1e1e2e;")

        # Подсветка активного движка
        engine = getattr(self.tts, 'voice_type', 'edge') if self.tts else 'edge'
        engine_btn_map = {
            "edge": self._engine_btn_edge,
            "rhvoice": self._engine_btn_rhvoice,
            "silero": self._engine_btn_silero,
            "sapi": self._engine_btn_sapi,
        }
        active_color = {"edge": "#89b4fa", "rhvoice": "#a6e3a1",
                        "silero": "#f9e2af", "sapi": "#cba6f7"}
        for eng, btn in engine_btn_map.items():
            if eng == engine:
                btn.setStyleSheet(
                    f"QPushButton {{ padding: 3px 8px; font-size: 11px; border-radius: 3px; "
                    f"background: #45475a; color: {active_color[eng]}; font-weight: bold; "
                    f"border: 1px solid {active_color[eng]}; }}")
            else:
                btn.setStyleSheet(
                    f"QPushButton {{ padding: 3px 8px; font-size: 11px; border-radius: 3px; "
                    f"background: #313244; color: {active_color[eng]}; }}"
                    f"QPushButton:hover {{ background: #45475a; }}")

    def _tick_tts_state(self):
        """Периодическая проверка состояния TTS — обновляет индикатор."""
        if not hasattr(self, 'tts') or not self.tts:
            return
        playing = self.tts.is_playing
        if playing != self._tts_playing:
            self._tts_playing = playing
        self._update_voice_indicator()

    def _create_tts_tab(self):
        """Unified voice tab — all voice settings in one place."""
        tab = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_content = QWidget()
        layout = QVBoxLayout(scroll_content)
        layout.setSpacing(8)

        hdr = QLabel("\U0001f50a " + tr("tab_tts") + "  \u2014  " + tr("combined_tab_info",
            default="Все голоса: Edge (онлайн) + RHVoice/Silero/SAPI5 (офлайн)"))
        hdr.setStyleSheet("color: #f9e2af; font-size: 12px; padding: 4px 8px; "
                          "background: #3a2e1e; border-radius: 4px; font-weight: bold;")
        layout.addWidget(hdr)

        voices = self._get_voices_list()

        # ═══════════════════════════════════════════════════════════════
        # 1. РОЛИ: голоса для мужского / женского / рассказчика (ru + en)
        # ═══════════════════════════════════════════════════════════════
        roles_group = QGroupBox(tr("voice_roles_group", default="Голоса для ролей"))
        roles_layout = QVBoxLayout(roles_group)

        def _make_role_row(label, ru_key, en_key, ru_default, en_default, test_cb):
            """Create a row: Label | ru_combo | en_combo | test_btn."""
            row = QHBoxLayout()
            row.addWidget(QLabel(label, minimumWidth=90))
            ru_combo = QComboBox()
            ru_combo.addItems(voices)
            ru_combo.setMinimumWidth(200)
            saved_ru = self.settings.get(ru_key, ru_default)
            idx = ru_combo.findText(saved_ru)
            if idx >= 0:
                ru_combo.setCurrentIndex(idx)
            row.addWidget(QLabel("RU:"))
            row.addWidget(ru_combo)
            en_combo = QComboBox()
            en_combo.addItems(voices)
            en_combo.setMinimumWidth(200)
            saved_en = self.settings.get(en_key, en_default)
            idx = en_combo.findText(saved_en)
            if idx >= 0:
                en_combo.setCurrentIndex(idx)
            else:
                for i, v in enumerate(voices):
                    if "Multilingual" in v or "Guy" in v:
                        en_combo.setCurrentIndex(i)
                        break
            row.addWidget(QLabel("EN:"))
            row.addWidget(en_combo)
            test_btn = QPushButton("\u25b6")
            test_btn.setMaximumWidth(30)
            test_btn.setToolTip(tr("btn_test_this_voice", default="Тест"))
            test_btn.clicked.connect(lambda: test_cb(ru_combo))
            row.addWidget(test_btn)
            return ru_combo, en_combo, row

        self.voice_combo, self.male_en_voice_combo, row_male = _make_role_row(
            "\u2642 " + tr("lbl_male_voice", default="Мужской:"),
            "tts.male_ru_voice", "tts.male_en_voice",
            "ru-RU-DmitryNeural", "en-US-GuyNeural",
            self._test_single_voice)
        self.voice_combo.currentTextChanged.connect(self._on_voice_changed)
        self.male_en_voice_combo.currentTextChanged.connect(self._on_male_en_voice_changed)
        roles_layout.addLayout(row_male)

        self.female_voice_combo, self.female_en_voice_combo, row_female = _make_role_row(
            "\u2640 " + tr("lbl_female_voice", default="Женский:"),
            "tts.female_ru_voice", "tts.female_en_voice",
            "ru-RU-SvetlanaNeural", "en-US-JennyNeural",
            self._test_single_voice)
        self.female_voice_combo.currentTextChanged.connect(self._on_female_voice_changed)
        self.female_en_voice_combo.currentTextChanged.connect(self._on_female_en_voice_changed)
        roles_layout.addLayout(row_female)

        self.narrator_combo, self.narrator_en_voice_combo, row_narr = _make_role_row(
            "\U0001f3ad " + tr("lbl_narrator", default="Рассказчик:"),
            "tts.narrator_ru_voice", "tts.narrator_en_voice",
            "ru-RU-DmitryNeural", "en-US-GuyMultilingualNeural",
            self._test_single_voice)
        self.narrator_combo.currentTextChanged.connect(self._on_narrator_voice_changed)
        self.narrator_en_voice_combo.currentTextChanged.connect(self._on_narrator_en_voice_changed)
        roles_layout.addLayout(row_narr)

        refresh_btn = QPushButton("\u21bb " + tr("btn_refresh_voices", default="Обновить голоса"))
        refresh_btn.clicked.connect(self._refresh_voices_list)
        roles_layout.addWidget(refresh_btn)

        layout.addWidget(roles_group)

        # ═══════════════════════════════════════════════════════════════
        # 2. ПАРАМЕТРЫ: скорость, питч по ролям + громкость + движок
        # ═══════════════════════════════════════════════════════════════
        params_group = QGroupBox(tr("voice_settings", default="Параметры воспроизведения"))
        params_layout = QVBoxLayout(params_group)

        # ── Per-role speed/pitch ──
        role_params_header = QLabel(
            tr("role_params_header",
               default="Скорость и питч по ролям:"))
        role_params_header.setStyleSheet("font-weight: bold; margin-top: 4px;")
        params_layout.addWidget(role_params_header)

        self._role_sliders = {}  # role -> {"speed": (slider, label), "pitch": (slider, label)}

        _role_labels = {
            "male":   "\u2642 " + tr("lbl_male_voice", default="Мужской"),
            "female": "\u2640 " + tr("lbl_female_voice", default="Женский"),
            "narrator": "\U0001f3ad " + tr("lbl_narrator", default="Рассказчик"),
        }
        _role_setting_keys = {
            "male":   ("tts.male_rate",   "tts.male_pitch"),
            "female": ("tts.female_rate", "tts.female_pitch"),
            "narrator": ("tts.narrator_rate", "tts.narrator_pitch"),
        }

        for role_key, role_label in _role_labels.items():
            rate_key, pitch_key = _role_setting_keys[role_key]

            role_row = QHBoxLayout()
            role_row.addWidget(QLabel(role_label + ":", minimumWidth=80))

            # Speed slider for this role
            role_row.addWidget(QLabel("S:"))
            speed_slider = QSlider(Qt.Orientation.Horizontal)
            speed_slider.setRange(-50, 50)
            speed_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
            speed_slider.setTickInterval(25)
            saved_rate = self.settings.get(rate_key, 0)
            speed_slider.setValue(saved_rate)
            speed_label = QLabel(f"{saved_rate:+d}")
            speed_label.setMinimumWidth(30)
            role_row.addWidget(speed_slider)
            role_row.addWidget(speed_label)

            # Pitch slider for this role
            role_row.addWidget(QLabel("P:"))
            pitch_slider = QSlider(Qt.Orientation.Horizontal)
            pitch_slider.setRange(-50, 50)
            pitch_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
            pitch_slider.setTickInterval(25)
            saved_pitch = self.settings.get(pitch_key, 0)
            pitch_slider.setValue(saved_pitch)
            pitch_label = QLabel(f"{saved_pitch:+d}")
            pitch_label.setMinimumWidth(30)
            role_row.addWidget(pitch_slider)
            role_row.addWidget(pitch_label)

            # Connect signals
            speed_slider.valueChanged.connect(
                lambda v, rk=rate_key, lbl=speed_label, rk2=rate_key:
                    self._on_role_param_changed(rk, v, lbl))
            pitch_slider.valueChanged.connect(
                lambda v, rk=pitch_key, lbl=pitch_label:
                    self._on_role_pitch_changed(rk, v, lbl))

            params_layout.addLayout(role_row)
            self._role_sliders[role_key] = {
                "speed": (speed_slider, speed_label),
                "pitch": (pitch_slider, pitch_label),
            }

        # ── Global volume ──
        vol_row = QHBoxLayout()
        vol_row.addWidget(QLabel(tr("lbl_volume", default="Громкость:")))
        self.vol_slider = QSlider(Qt.Orientation.Horizontal)
        self.vol_slider.setRange(0, 100)
        self.vol_slider.setValue(100)
        self.vol_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.vol_slider.setTickInterval(10)
        vol_row.addWidget(self.vol_slider)
        self.vol_label = QLabel("100%")
        self.vol_label.setMinimumWidth(40)
        vol_row.addWidget(self.vol_label)
        self.vol_slider.valueChanged.connect(self._on_volume_changed)
        params_layout.addLayout(vol_row)

        # ── Smooth transition checkbox ──
        smooth_row = QHBoxLayout()
        self.smooth_check = QCheckBox(tr("chk_smooth", default="Плавные переходы между репликами"))
        self.smooth_check.setChecked(True)
        self.smooth_check.stateChanged.connect(self._on_smooth_changed)
        smooth_row.addWidget(self.smooth_check)
        smooth_row.addStretch()
        params_layout.addLayout(smooth_row)

        # Движок + кэш
        engine_row = QHBoxLayout()
        engine_row.addWidget(QLabel(tr("lbl_engine", default="Движок:")))
        self.engine_combo = QComboBox()
        self.engine_combo.addItems([
            tr("engine_edge_playback", default="Edge-TTS (файл)"),
            tr("engine_edge_vlc", default="Edge-TTS (VLC-стриминг)"),
            tr("engine_sapi", default="SAPI (офлайн)"),
        ])
        self.engine_combo.setCurrentIndex(0)
        engine_row.addWidget(self.engine_combo)
        # Load saved engine type
        saved_engine = self.settings.get("tts.voice_type", "edge")
        engine_index = {"edge": 0, "rhvoice": 2, "sapi": 2, "silero": 2, "persona": 0}.get(saved_engine, 0)
        self.engine_combo.setCurrentIndex(engine_index)
        self.engine_combo.currentIndexChanged.connect(self._on_engine_changed)
        self.settings_cache_check = QCheckBox(tr("chk_cache_audio", default="Кэш"))
        self.settings_cache_check.setChecked(self.settings.get("tts.cache_audio", True))
        self.settings_cache_check.stateChanged.connect(
            lambda v: self.settings.set("tts.cache_audio", v == Qt.CheckState.Checked.value))
        engine_row.addWidget(self.settings_cache_check)
        engine_row.addStretch()
        params_layout.addLayout(engine_row)

        layout.addWidget(params_group)

        # ═══════════════════════════════════════════════════════════════
        # 3. УПРАВЛЕНИЕ: тест / мгновенно / стоп / MP3
        # ═══════════════════════════════════════════════════════════════
        ctrl_layout = QHBoxLayout()
        test_btn = QPushButton(tr("btn_test_voice", default="\u25b6 Тест"))
        test_btn.clicked.connect(self._test_voice)
        ctrl_layout.addWidget(test_btn)

        instant_btn = QPushButton(tr("btn_instant_speak", default="\u26a1 Мгновенно"))
        instant_btn.setIcon(icon_play())
        instant_btn.setStyleSheet(
            "background-color: #ff9800; color: white; "
            "font-weight: bold; padding: 6px 12px;")
        instant_btn.clicked.connect(self._speak_instant)
        ctrl_layout.addWidget(instant_btn)

        stop_voice_btn = QPushButton(tr("btn_stop_speaking", default="\u23f9 Стоп"))
        stop_voice_btn.setIcon(icon_stop())
        stop_voice_btn.setStyleSheet(
            "background-color: #f44336; color: white; "
            "font-weight: bold; padding: 6px 12px;")
        stop_voice_btn.clicked.connect(self._stop_all_speech)
        ctrl_layout.addWidget(stop_voice_btn)

        save_mp3_btn = QPushButton(tr("btn_save_mp3", default="\U0001f4be MP3"))
        save_mp3_btn.clicked.connect(self._save_audio_result)
        ctrl_layout.addWidget(save_mp3_btn)

        layout.addLayout(ctrl_layout)

        # ═══════════════════════════════════════════════════════════════
        # 4. ПРОФИЛИ: сохранение / загрузка всех настроек голоса
        # ═══════════════════════════════════════════════════════════════
        profiles_group = QGroupBox(tr("profiles_group", default="Сохранённые профили"))
        profiles_layout = QVBoxLayout(profiles_group)

        name_row = QHBoxLayout()
        self.profile_name_input = QLineEdit()
        self.profile_name_input.setPlaceholderText(
            tr("profile_name_ph", default="Имя профиля..."))
        name_row.addWidget(self.profile_name_input)
        save_profile_btn = QPushButton(
            "\U0001f4be " + tr("btn_save_profile", default="Сохранить"))
        save_profile_btn.clicked.connect(self._save_voice_profile)
        name_row.addWidget(save_profile_btn)
        profiles_layout.addLayout(name_row)

        profile_row = QHBoxLayout()
        self.profile_combo = QComboBox()
        self.profile_combo.setMinimumWidth(200)
        self._refresh_profiles()
        profile_row.addWidget(self.profile_combo)
        load_profile_btn = QPushButton(
            "\U0001f4c2 " + tr("btn_load_profile", default="Загрузить"))
        load_profile_btn.clicked.connect(self._load_voice_profile)
        profile_row.addWidget(load_profile_btn)
        del_profile_btn = QPushButton(
            "\U0001f5d1 " + tr("btn_del_profile", default="Удалить"))
        del_profile_btn.clicked.connect(self._delete_voice_profile)
        profile_row.addWidget(del_profile_btn)
        profiles_layout.addLayout(profile_row)

        layout.addWidget(profiles_group)

        # ═══════════════════════════════════════════════════════════════
        # 5. МАРШРУТИЗАЦИЯ: язык → голос
        # ═══════════════════════════════════════════════════════════════
        router_group = QGroupBox(tr("vp_router", default="Маршрутизация: язык \u2192 голос"))
        router_layout = QVBoxLayout(router_group)
        router_desc = QLabel(tr("vp_router_desc",
            default="Автоматический выбор голоса по языку текста."))
        router_desc.setWordWrap(True)
        router_desc.setStyleSheet("color: #a6adc8; font-size: 12px;")
        router_layout.addWidget(router_desc)

        from voice_profiles import SUPPORTED_LANGUAGES
        self._router_combos = {}
        for lang_code, lang_name in SUPPORTED_LANGUAGES.items():
            row = QHBoxLayout()
            row.addWidget(QLabel(f"{lang_name} ({lang_code}):", minimumWidth=140))
            combo = QComboBox()
            combo.setMinimumWidth(250)
            combo.addItems(voices)
            if hasattr(self.tts, 'voice_router'):
                current_voice = self.tts.voice_router.get_voice_for_language(lang_code)
                idx = combo.findText(current_voice)
                if idx >= 0:
                    combo.setCurrentIndex(idx)
            combo.currentTextChanged.connect(
                lambda voice, lc=lang_code: self._on_router_voice_changed(lc, voice))
            row.addWidget(combo)
            row.addStretch()
            router_layout.addLayout(row)
            self._router_combos[lang_code] = combo

        # Fallback
        fb_row = QHBoxLayout()
        fb_row.addWidget(QLabel(tr("vp_fallback", default="По умолчанию:"), minimumWidth=140))
        self._router_fallback_combo = QComboBox()
        self._router_fallback_combo.setMinimumWidth(250)
        self._router_fallback_combo.addItems(voices)
        if hasattr(self.tts, 'voice_router'):
            fb = self.tts.voice_router.get_fallback_voice()
            idx = self._router_fallback_combo.findText(fb)
            if idx >= 0:
                self._router_fallback_combo.setCurrentIndex(idx)
        self._router_fallback_combo.currentTextChanged.connect(self._on_router_fallback_changed)
        fb_row.addWidget(self._router_fallback_combo)
        fb_row.addStretch()
        router_layout.addLayout(fb_row)

        reset_router_btn = QPushButton(
            "\U0001f504 " + tr("vp_reset_router", default="Сбросить маршрутизацию"))
        reset_router_btn.clicked.connect(self._on_reset_router)
        router_layout.addWidget(reset_router_btn)

        layout.addWidget(router_group)

        # ═══════════════════════════════════════════════════════════════
        # 6. ПЕРСОНАЖИ: имя → голос (для «Имя: реплика»)
        # ═══════════════════════════════════════════════════════════════
        chars_group = QGroupBox(tr("vp_characters", default="Персонажи (имя \u2192 голос)"))
        chars_layout = QVBoxLayout(chars_group)
        char_desc = QLabel(tr("vp_characters_desc",
            default="Задайте имя персонажа и голос. Текст «Имя: реплика» будет озвучен этим голосом."))
        char_desc.setWordWrap(True)
        char_desc.setStyleSheet("color: #a6adc8; font-size: 12px;")
        chars_layout.addWidget(char_desc)

        self._char_rows = []
        char_data = self.settings.get("tts.characters", {})
        for i in range(6):
            row = QHBoxLayout()
            name_edit = QLineEdit()
            name_edit.setPlaceholderText(tr("vp_char_name_ph", default="Имя..."))
            name_edit.setMinimumWidth(120)
            voice_combo = QComboBox()
            voice_combo.addItems(voices)
            voice_combo.setMinimumWidth(240)
            char_key = f"char_{i}"
            if char_key in char_data:
                name_edit.setText(char_data[char_key].get("name", ""))
                saved_voice = char_data[char_key].get("voice", "")
                idx = voice_combo.findText(saved_voice)
                if idx >= 0:
                    voice_combo.setCurrentIndex(idx)
            row.addWidget(name_edit)
            row.addWidget(QLabel("\u2192"))
            row.addWidget(voice_combo)
            row.addStretch()
            chars_layout.addLayout(row)
            self._char_rows.append((name_edit, voice_combo))

        save_chars_btn = QPushButton(
            "\U0001f4be " + tr("vp_save_characters", default="Сохранить персонажей"))
        save_chars_btn.clicked.connect(self._vp_save_characters)
        chars_layout.addWidget(save_chars_btn)

        layout.addWidget(chars_group)

        # ═══════════════════════════════════════════════════════════════
        # 7. LIVE DIALOG: мультиголосовой диалог
        # ═══════════════════════════════════════════════════════════════
        live_group = QGroupBox("\U0001f3ad Live Dialog")
        live_layout = QVBoxLayout(live_group)

        live_desc = QLabel(
            tr("live_dialog_desc",
               default="Мультиголосовое воспроизведение диалога с субтитрами.\n"
               "Формат: Имя: реплика (по одной на строку)."))
        live_desc.setWordWrap(True)
        live_desc.setStyleSheet("color: #a6adc8; font-size: 12px;")
        live_layout.addWidget(live_desc)

        self.live_dialog_text = QTextEdit()
        self.live_dialog_text.setPlaceholderText(
            "Dmitry: Привет!\nSvetlana: Привет, Дмитрий!\nGuy: Hey there!")
        self.live_dialog_text.setMinimumHeight(100)
        self.live_dialog_text.setMaximumHeight(180)
        live_layout.addWidget(self.live_dialog_text)

        live_btn_row = QHBoxLayout()
        self.live_play_btn = QPushButton("\u25b6 " + tr("btn_play", default="Воспроизвести"))
        self.live_play_btn.setStyleSheet(
            "background-color: #4caf50; color: white; "
            "font-weight: bold; padding: 6px 16px;")
        self.live_play_btn.clicked.connect(self._start_live_mode)
        live_btn_row.addWidget(self.live_play_btn)

        self.live_stop_btn = QPushButton("\u23f9 " + tr("btn_stop", default="Остановить"))
        self.live_stop_btn.setEnabled(False)
        self.live_stop_btn.clicked.connect(self._stop_live_mode)
        live_btn_row.addWidget(self.live_stop_btn)

        live_load_btn = QPushButton(
            "\U0001f4c2 " + tr("btn_load_file", default="Из файла"))
        live_load_btn.clicked.connect(self._load_live_dialog_file)
        live_btn_row.addWidget(live_load_btn)

        live_layout.addLayout(live_btn_row)

        self.live_dialog_status = QLabel(tr("status_ready", default="Готов"))
        self.live_dialog_status.setStyleSheet("color: #a6adc8;")
        live_layout.addWidget(self.live_dialog_status)

        layout.addWidget(live_group)
        layout.addStretch()

        scroll.setWidget(scroll_content)
        main_layout = QVBoxLayout(tab)
        main_layout.addWidget(scroll)
        return tab

    def _create_logs_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setStyleSheet("font-family: Consolas, monospace; font-size: 12px;")
        layout.addWidget(self.log_text)

        btn_layout = QHBoxLayout()
        refresh_btn = QPushButton(tr("btn_refresh"))
        refresh_btn.clicked.connect(self._refresh_logs)
        btn_layout.addWidget(refresh_btn)
        self.auto_refresh_check = QCheckBox(tr("chk_auto_refresh"))
        self.auto_refresh_check.setChecked(True)
        self.auto_refresh_check.stateChanged.connect(self._toggle_auto_refresh)
        btn_layout.addWidget(self.auto_refresh_check)
        btn_layout.addStretch()
        clear_btn = QPushButton(tr("btn_clear_logs"))
        clear_btn.clicked.connect(self._clear_logs)
        btn_layout.addWidget(clear_btn)
        layout.addLayout(btn_layout)

        self._log_timer = QTimer(self)
        self._log_timer.timeout.connect(self._refresh_logs)
        self._log_timer.start(5000)
        self._refresh_logs()
        return tab

    # ==================== REGIONS ====================

    def _refresh_region_list(self):
        self.region_list.clear()
        for i, region in enumerate(self.regions):
            item = QListWidgetItem(f"[{region['width']}x{region['height']}] @ {region['x']},{region['y']}")
            item.setData(Qt.ItemDataRole.UserRole, i)
            self.region_list.addItem(item)
        self.region_overlay.set_regions(self.regions)

    def _add_region(self):
        import mss
        with mss.mss() as sct:
            monitor = sct.monitors[1]
            w, h = monitor["width"], monitor["height"]
        name = self._get_active_window_title()
        if not name:
            name = tr("region_default_name", x=len(self.regions) + 1, y="")
        region = {
            "name": name,
            "x": w // 4,
            "y": h // 4,
            "width": w // 2,
            "height": h // 4,
            "voice": self.voice_combo.currentText(),
        }
        self.regions.append(region)
        self._refresh_region_list()
        self._save_regions()
        self.statusBar().showMessage(tr("status_added", name=region['name']))

    def _add_region_at_cursor(self):
        """Create a new region at the current cursor position."""
        import mss
        cursor_pos = QCursor.pos()
        with mss.mss() as sct:
            monitor = sct.monitors[1]
            w, h = monitor["width"], monitor["height"]
        region_w = w // 3
        region_h = h // 5
        x = max(0, min(cursor_pos.x() - region_w // 2, w - region_w))
        y = max(0, min(cursor_pos.y() - region_h // 2, h - region_h))
        name = tr("region_default_name", x=len(self.regions) + 1, y="")
        region = {
            "name": name,
            "x": x, "y": y,
            "width": region_w, "height": region_h,
            "voice": self.voice_combo.currentText() if hasattr(self, 'voice_combo') else "ru-RU-DmitryNeural",
        }
        self.regions.append(region)
        self._refresh_region_list()
        self._save_regions()
        self.statusBar().showMessage(tr("status_added_at_cursor", name=name))

    def _get_active_window_title(self):
        try:
            import ctypes
            GetWindowTextLength = ctypes.windll.user32.GetWindowTextLengthW
            GetWindowText = ctypes.windll.user32.GetWindowTextW
            IsWindowVisible = ctypes.windll.user32.IsWindowVisible
            GetForegroundWindow = ctypes.windll.user32.GetForegroundWindow
            hwnd = GetForegroundWindow()
            if not IsWindowVisible(hwnd):
                return ""
            length = GetWindowTextLength(hwnd) + 1
            buf = ctypes.create_unicode_buffer(length)
            GetWindowText(hwnd, buf, length)
            return buf.value.strip() if buf.value else ""
        except Exception:
            return ""

    def _get_active_window_rect(self):
        """Get the foreground window's title and bounding rect (x, y, width, height).
        Returns (title, x, y, w, h) or None if unavailable."""
        try:
            import ctypes
            GetForegroundWindow = ctypes.windll.user32.GetForegroundWindow
            IsWindowVisible = ctypes.windll.user32.IsWindowVisible
            GetWindowTextLength = ctypes.windll.user32.GetWindowTextLengthW
            GetWindowText = ctypes.windll.user32.GetWindowTextW

            hwnd = GetForegroundWindow()
            if not hwnd or not IsWindowVisible(hwnd):
                return None

            length = GetWindowTextLength(hwnd) + 1
            buf = ctypes.create_unicode_buffer(length)
            GetWindowText(hwnd, buf, length)
            title = buf.value.strip() if buf.value else ""

            # Get window rect
            class RECT(ctypes.Structure):
                _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                            ("right", ctypes.c_long), ("bottom", ctypes.c_long)]
            rect = RECT()
            ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
            x, y = rect.left, rect.top
            w, h = rect.right - rect.left, rect.bottom - rect.top
            if w <= 0 or h <= 0:
                return None
            return (title, x, y, w, h)
        except Exception as e:
            logger.debug(f"[WINDOW] Failed to get foreground rect: {e}")
            return None

    def _capture_focused_window(self):
        """Create a scan region from the foreground window's exact bounds."""
        info = self._get_active_window_rect()
        if not info:
            self.statusBar().showMessage(tr("status_no_active_window", default="No active window detected"))
            return
        title, x, y, w, h = info
        if not title:
            title = tr("region_default_name", x=len(self.regions) + 1, y="")
        region = {
            "name": title,
            "x": x, "y": y,
            "width": w, "height": h,
            "voice": self.voice_combo.currentText() if hasattr(self, 'voice_combo') else "ru-RU-DmitryNeural",
            "locked": True,
        }
        self.regions.append(region)
        self._refresh_region_list()
        self._save_regions()
        # Set as active region for live TTS
        self.live_scanner.set_region(x, y, w, h)
        self.statusBar().showMessage(tr("status_captured_window", name=title))

    def _edit_region(self):
        item = self.region_list.currentItem()
        if not item:
            QMessageBox.information(self, tr("dlg_info"), tr("msg_select_region"))
            return
        idx = item.data(Qt.ItemDataRole.UserRole)
        self._edit_region_by_index(idx)

    def _delete_region(self):
        item = self.region_list.currentItem()
        if not item:
            return
        idx = item.data(Qt.ItemDataRole.UserRole)
        self._delete_region_by_index(idx)

    def _on_region_moved(self, idx, x, y, w, h):
        if idx < len(self.regions):
            self.regions[idx]["x"] = x
            self.regions[idx]["y"] = y
            self.regions[idx]["width"] = w
            self.regions[idx]["height"] = h
            self._refresh_region_list()
            self._save_regions()
            # Обновляем live_scanner если сканирует этот регион
            if self.live_scanner.running and idx == 0:
                self.live_scanner.set_region(x, y, w, h)

    def _on_region_deleted(self, idx):
        if idx < len(self.regions):
            del self.regions[idx]
            self._refresh_region_list()
            self._save_regions()
            # Останавливаем live_scanner если удалили активный регион
            if self.live_scanner.running and idx == 0:
                self._stop_live_mode()

    def _on_overlay_voice_speak(self, role):
        """Клик по кнопке голоса на оверлее — озвучить текст."""
        self._switch_active_voice(role)
        for btn_role, btn in [("male", self._voice_btn_male),
                              ("female", self._voice_btn_female),
                              ("narrator", self._voice_btn_narrator)]:
            btn.setChecked(btn_role == role)
        # Остановить live TTS streaming чтобы не конфликтовал
        self._stop_live_tts_if_needed()
        # Всегда остановить предыдущую озвучку перед запуском новой
        self._stop_all_speech()
        self._tts_playing = False
        # Запустить новую озвучку
        self._speak_result()

    def _on_overlay_voice_params_changed(self, role, params):
        """Обработка изменения питча/скорости/имени с оверлея."""
        if not hasattr(self, 'region_overlay'):
            return
        if "pitch" in params:
            key = f"tts.{role}_pitch" if role != "narrator" else "tts.narrator_pitch"
            self.settings.set(key, params["pitch"])
            self.tts.set_role_pitch(role, params["pitch"])
            if role == getattr(self, '_active_role', None):
                self._active_voice_pitch = params["pitch"]
        if "speed" in params:
            key = f"tts.{role}_speed" if role != "narrator" else "tts.narrator_speed"
            self.settings.set(key, params["speed"])
            self.tts.set_role_rate(role, params["speed"])
            if role == getattr(self, '_active_role', None):
                self._active_voice_rate = params["speed"]
        if "name" in params:
            characters = self.settings.get("tts.characters", {})
            char_key = f"overlay_{role}"
            if char_key not in characters:
                characters[char_key] = {}
            characters[char_key]["name"] = params["name"]
            characters[char_key]["voice"] = self.region_overlay.get_voice_params(role).get("voice", "")
            self.settings.set("tts.characters", characters)
        # Sync profile manager
        try:
            from voice_profiles import VoiceProfileManager
            mgr = VoiceProfileManager()
            overlay_params = self.region_overlay.get_voice_params(role)
            ru_voice = overlay_params.get("ru_voice", "")
            for p in mgr.get_all():
                if p.voice == ru_voice:
                    update = {}
                    if "pitch" in params:
                        update["pitch"] = params["pitch"]
                    if "speed" in params:
                        update["rate"] = params["speed"]
                    if update:
                        mgr.update(p.name, {**p.to_dict(), **update})
                    break
        except Exception:
            pass

    def _on_overlay_name_speak(self, name):
        characters = self.settings.get("tts.characters", {})
        for key, char_data in characters.items():
            if char_data.get("name", "").lower() == name.lower():
                voice_code = char_data.get("voice", "")
                if voice_code:
                    self.tts.voice = voice_code
                    self._speak_result()
                    return
        self._speak_result()

    def _on_overlay_start(self):
        """Кнопка Старт на панели оверлея."""
        self._stop_live_tts_if_needed()
        self._tts_playing = False
        self._speak_result()

    def _on_overlay_stop(self):
        """Кнопка Стоп на панели оверлея."""
        self._stop_live_tts_if_needed()
        self._stop_all_speech()
        self._tts_playing = False

    def _stop_live_tts_if_needed(self):
        """Остановить live TTS streaming если активен, чтобы не конфликтовал с ручной озвучкой."""
        if self.live_scanner and self.live_scanner.running:
            try:
                self.live_scanner._tts_paused = True
                asyncio.run_coroutine_threadsafe(
                    self.live_scanner.stop_tts_streaming(), self._live_event_loop
                )
            except Exception as e:
                logger.debug(f"[OVERLAY] stop_tts_streaming error: {e}")

    def _save_regions(self):
        try:
            with open("regions.json", "w", encoding="utf-8") as f:
                json.dump(self.regions, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Error saving regions: {e}")

    def _load_regions(self):
        try:
            if os.path.exists("regions.json"):
                with open("regions.json", "r", encoding="utf-8") as f:
                    self.regions = json.load(f)
        except Exception as e:
            logger.error(f"Error loading regions: {e}")
            self.regions = []

    # ==================== SCANNING ====================

    def _scan_selected_region(self):
        item = self.region_list.currentItem()
        if not item:
            QMessageBox.information(self, tr("dlg_info"), tr("msg_select_region"))
            return
        idx = item.data(Qt.ItemDataRole.UserRole)
        self._do_scan(self.regions[idx])

    def _scan_all_regions(self):
        if not self.regions:
            QMessageBox.information(self, tr("dlg_info"), tr("msg_no_regions_to_scan"))
            return
        self._scan_queue = list(self.regions)
        self._scan_next_in_queue()

    def _scan_next_in_queue(self):
        if not self._scan_queue:
            return
        if self.scanning:
            QTimer.singleShot(200, self._scan_next_in_queue)
            return
        region = self._scan_queue.pop(0)
        self._do_scan(region)
        QTimer.singleShot(300, self._scan_next_in_queue)

    def _scan_full_screen_only(self):
        import mss
        with mss.mss() as sct:
            monitor = sct.monitors[1]
            w, h = monitor["width"], monitor["height"]
        region = {
            "name": tr("full_screen_name"),
            "x": 0, "y": 0, "width": w, "height": h,
            "voice": self.voice_combo.currentText(),
        }
        self._do_scan(region)

    def _do_scan(self, region):
        if self.scanning:
            return
        if not self.ocr:
            self.statusBar().showMessage("OCR загружается...")
            return
        self.scanning = True
        self.statusBar().showMessage(tr("status_recognizing", name=region['name']))
        self.ocr.set_region(region["x"], region["y"], region["width"], region["height"])
        self.ocr_worker = OCRWorker(self.ocr)
        self.ocr_worker.result_ready.connect(lambda text, r=region: self._on_scan_complete(text, r))
        self.ocr_worker.error_occurred.connect(self._on_scan_error)
        self.ocr_worker.progress.connect(self.ocr_indicator.update_progress)
        self.ocr_worker.start()
        self.ocr_indicator.start_scanning()

    def _on_scan_complete(self, text, region):
        self.ocr_indicator.finish_scanning(success=True)
        self.scanning = False
        region_name = region["name"]
        if text:
            filtered = self._filter_ocr_text(text)
            if filtered:
                # Push OCR result to web interface via SSE
                try:
                    from web_tts_server import push_ocr_result
                    push_ocr_result(filtered, region_name)
                except Exception:
                    pass
                # Режим "отдельные результаты регионов" (вкл/выкл в настройках)
                if self.settings.get("scan.separate_regions", True):
                    self.region_results[region_name] = filtered
                    self._rebuild_result_text()
                else:
                    self._update_region_text(region_name, filtered)
                self.statusBar().showMessage(tr("status_recognized", name=region_name, count=len(filtered)))
            else:
                self.statusBar().showMessage(tr("status_empty_filtered", name=region_name))
        else:
            self.statusBar().showMessage(tr("status_no_text", name=region_name))

    def _rebuild_result_text(self):
        """Пересобирает result_text из отдельных результатов регионов.

        Каждый регион хранится независимо, поэтому новое сканирование
        одного региона не смешивается с результатами других.
        """
        parts = []
        for region in self.regions:
            name = region.get("name", "")
            if name in self.region_results:
                parts.append(f"[{name}] {self.region_results[name]}")
        self.result_text.setPlainText("\n".join(parts))

    def _update_region_text(self, region_name, new_text):
        """Старое поведение: обновление строки региона в общем тексте."""
        prefix = f"[{region_name}] "
        current = self.result_text.toPlainText()
        lines = current.split("\n")
        new_lines = []
        found = False
        for line in lines:
            if line.startswith(prefix):
                new_lines.append(prefix + new_text)
                found = True
            else:
                new_lines.append(line)
        if not found:
            new_lines.append(prefix + new_text)
        self.result_text.setPlainText("\n".join(new_lines))

    def _on_scan_error(self, error):
        self.ocr_indicator.finish_scanning(success=False)
        self.scanning = False
        self.statusBar().showMessage(tr("status_error", err=error))

    def _clear_result(self):
        self.result_text.clear()
        self.region_results.clear()

    def _on_scan_mode_changed(self, index):
        """Смена режима сканирования из интерфейса."""
        mode = self.scan_mode_combo.itemData(index)
        if mode:
            self.settings.set("scan.mode", mode)
            logger.info(f"[SCAN] Режим сканирования: {mode}")

    def _on_separate_regions_changed(self, state):
        """Переключение режима отдельных результатов регионов."""
        enabled = self.separate_regions_check.isChecked()
        self.settings.set("scan.separate_regions", enabled)
        logger.info(f"[SCAN] Отдельные результаты регионов: {enabled}")

    def _on_line_by_line_changed(self, state):
        """Переключение построчного режима озвучки."""
        lw = self.live_window
        enabled = lw.line_by_line_check.isChecked()
        self.live_scanner.set_line_by_line_mode(enabled)
        if enabled:
            lw.line_by_line_status.setText(tr("line_by_line_active", default="Активен"))
            lw.line_by_line_status.setStyleSheet("color: #81C784; font-weight: bold; font-size: 12px;")
            lw.chunk_clear_check.blockSignals(True)
            lw.chunk_clear_check.setChecked(False)
            lw.chunk_clear_check.blockSignals(False)
            lw.chunk_clear_status.setText("")
            lw.chunk_clear_status.setStyleSheet("color: #CAC4D0; font-size: 12px;")
            lw.sentence_mode_check.blockSignals(True)
            lw.sentence_mode_check.setChecked(False)
            lw.sentence_mode_check.blockSignals(False)
            lw.sentence_mode_status.setText("")
            lw.sentence_mode_status.setStyleSheet("color: #CAC4D0; font-size: 12px;")
        else:
            lw.line_by_line_status.setText("")
            lw.line_by_line_status.setStyleSheet("color: #CAC4D0; font-size: 12px;")
        logger.info(f"[SCAN] Построчный режим: {enabled}")

    def _on_sync_mode_changed(self, state):
        """Переключение sync-режима: голос = приоритет."""
        lw = self.live_window
        enabled = lw.sync_mode_check.isChecked()
        self.live_scanner.set_sync_mode(enabled)
        if enabled:
            lw.sync_mode_status.setText("Активен")
            lw.sync_mode_status.setStyleSheet("color: #81C784; font-weight: bold; font-size: 12px;")
        else:
            lw.sync_mode_status.setText("")
            lw.sync_mode_status.setStyleSheet("color: #CAC4D0; font-size: 12px;")
        logger.info(f"[SCAN] Sync-режим: {enabled}")

    def _on_sentence_mode_changed(self, state):
        """Переключение пофразового режима: текст стирается по мере чтения."""
        lw = self.live_window
        enabled = lw.sentence_mode_check.isChecked()
        self.live_scanner.set_sentence_mode(enabled)
        if enabled:
            lw.sentence_mode_status.setText("Активен")
            lw.sentence_mode_status.setStyleSheet("color: #81C784; font-weight: bold; font-size: 12px;")
            lw.chunk_clear_check.blockSignals(True)
            lw.chunk_clear_check.setChecked(False)
            lw.chunk_clear_check.blockSignals(False)
            lw.chunk_clear_status.setText("")
            lw.chunk_clear_status.setStyleSheet("color: #CAC4D0; font-size: 12px;")
            lw.line_by_line_check.blockSignals(True)
            lw.line_by_line_check.setChecked(False)
            lw.line_by_line_check.blockSignals(False)
            lw.line_by_line_status.setText("")
            lw.line_by_line_status.setStyleSheet("color: #CAC4D0; font-size: 12px;")
        else:
            lw.sentence_mode_status.setText("")
            lw.sentence_mode_status.setStyleSheet("color: #CAC4D0; font-size: 12px;")
        logger.info(f"[SCAN] Пофразовый режим: {enabled}")

    def _on_chunk_clear_changed(self, state):
        """Переключение chunk+clear режима: OCR → буфер → TTS по порядку → очистка."""
        lw = self.live_window
        enabled = lw.chunk_clear_check.isChecked()
        self.live_scanner.set_chunk_clear_mode(enabled)
        if enabled:
            lw.chunk_clear_status.setText("Активен")
            lw.chunk_clear_status.setStyleSheet("color: #81C784; font-weight: bold; font-size: 12px;")
            lw.line_by_line_check.blockSignals(True)
            lw.line_by_line_check.setChecked(False)
            lw.line_by_line_check.blockSignals(False)
            lw.line_by_line_status.setText("")
            lw.line_by_line_status.setStyleSheet("color: #CAC4D0; font-size: 12px;")
            lw.sentence_mode_check.blockSignals(True)
            lw.sentence_mode_check.setChecked(False)
            lw.sentence_mode_check.blockSignals(False)
            lw.sentence_mode_status.setText("")
            lw.sentence_mode_status.setStyleSheet("color: #CAC4D0; font-size: 12px;")
        else:
            lw.chunk_clear_status.setText("")
            lw.chunk_clear_status.setStyleSheet("color: #CAC4D0; font-size: 12px;")
        logger.info(f"[SCAN] Chunk+Clear режим: {enabled}")

    def _update_web_tts_url_label(self):
        """Обновить URL-метку веб-сервера."""
        if not hasattr(self, "web_tts_url_label"):
            return
        if self.settings.get("web_tts.enabled", True):
            port = self.settings.get("web_tts.port", 8080)
            self.web_tts_url_label.setText(f"http://127.0.0.1:{port}/control")
        else:
            self.web_tts_url_label.setText("")

    def _on_web_tts_toggled(self, state):
        """Включение/выключение веб-сервера TTS."""
        enabled = self.settings_web_tts_check.isChecked()
        self.settings.set("web_tts.enabled", enabled)
        try:
            from web_tts_server import web_tts
            port = self.settings.get("web_tts.port", 8080)
            web_tts.port = port
            if enabled:
                web_tts.start()
                self.statusBar().showMessage(tr("status_web_tts_on", port=port))
            else:
                web_tts.stop()
                self.statusBar().showMessage(tr("status_web_tts_off"))
        except Exception as e:
            logger.error(f"[WEB-TTS] Ошибка переключения: {e}")
        self._update_web_tts_url_label()

    # ==================== TTS ====================

    def _on_voice_changed(self, voice_code):
        if voice_code and hasattr(self, 'tts'):
            self.tts.set_voice(voice_code)
            self.settings.set("tts.male_ru_voice", voice_code)
            self._active_voice = voice_code
            self._active_voice_type = self._detect_voice_type(voice_code)
            self._update_voice_indicator()
            self.statusBar().showMessage(tr("status_voice_set", voice=voice_code))

    def _on_female_voice_changed(self, voice_code):
        if voice_code and hasattr(self, 'tts'):
            self.settings.set("tts.female_ru_voice", voice_code)
            logger.info(f"[TTS] Женский голос (RU): {voice_code}")

    def _on_narrator_voice_changed(self, voice_code):
        if voice_code and hasattr(self, 'tts'):
            self.settings.set("tts.narrator_ru_voice", voice_code)
            logger.info(f"[TTS] Рассказчик (RU): {voice_code}")

    def _on_male_en_voice_changed(self, voice_code):
        if voice_code and hasattr(self, 'tts'):
            self.settings.set("tts.male_en_voice", voice_code)
            logger.info(f"[TTS] Мужской (EN): {voice_code}")

    def _on_female_en_voice_changed(self, voice_code):
        if voice_code and hasattr(self, 'tts'):
            self.settings.set("tts.female_en_voice", voice_code)
            logger.info(f"[TTS] Женский (EN): {voice_code}")

    def _on_narrator_en_voice_changed(self, voice_code):
        if voice_code and hasattr(self, 'tts'):
            self.settings.set("tts.narrator_en_voice", voice_code)
            logger.info(f"[TTS] Рассказчик (EN): {voice_code}")

    def _on_volume_changed(self, value):
        if hasattr(self, 'tts'):
            volume = value / 100.0
            self.tts.set_volume(volume)
            self.settings.set("tts.volume", volume)
            self.vol_label.setText(f"{value}%")

    def _on_role_param_changed(self, setting_key, value, label):
        """Handle per-role speed slider change."""
        label.setText(f"{value:+d}")
        self.settings.set(setting_key, value)
        if hasattr(self, 'tts'):
            role_map = {
                "tts.male_rate":   "male",
                "tts.female_rate": "female",
                "tts.narrator_rate": "narrator",
            }
            role = role_map.get(setting_key)
            if role:
                self.tts.set_role_rate(role, value)

    def _on_role_pitch_changed(self, setting_key, value, label):
        """Handle per-role pitch slider change."""
        label.setText(f"{value:+d}")
        self.settings.set(setting_key, value)
        if hasattr(self, 'tts'):
            role_map = {
                "tts.male_pitch":   "male",
                "tts.female_pitch": "female",
                "tts.narrator_pitch": "narrator",
            }
            role = role_map.get(setting_key)
            if role:
                self.tts.set_role_pitch(role, value)

    def _on_smooth_changed(self, state):
        """Toggle smooth transitions between chunks."""
        if hasattr(self, 'tts'):
            self.tts._transition_enabled = bool(state)
        self.settings.set("tts.smooth_transitions", bool(state))

    def _on_engine_changed(self, index):
        """Handle engine combo change — switch TTS engine type and voice."""
        engine_map = {0: "edge", 1: "edge", 2: "rhvoice"}
        voice_type = engine_map.get(index, "edge")
        self.settings.set("tts.voice_type", voice_type)
        if hasattr(self, 'tts'):
            self.tts.voice_type = voice_type
            # Auto-switch to a compatible voice for the selected engine
            if voice_type == "rhvoice":
                # Switch to RHVoice if available, else SAPI
                rhvoice = self.settings.get("tts.male_voice", "")
                if rhvoice.startswith("rhvoice:"):
                    self.tts.set_voice(rhvoice)
                else:
                    self.tts.set_voice("rhvoice:Aleksandr")
            else:
                # Switch to Edge-TTS voice
                edge_voice = self.settings.get("tts.male_ru_voice", "")
                if not edge_voice.startswith("rhvoice:") and not edge_voice.startswith("silero:"):
                    self.tts.set_voice(edge_voice)
                else:
                    self.tts.set_voice("ru-RU-DmitryNeural")
            # Update the voice combo to reflect the change
            current_voice = self.tts.voice
            idx = self.voice_combo.findText(current_voice)
            if idx >= 0:
                self.voice_combo.setCurrentIndex(idx)
        logger.info(f"[TTS] Engine switched to: {voice_type}, voice: {self.tts.voice if hasattr(self, 'tts') else 'N/A'}")

    # --- Voice Profiles (сессии) ---

    def _profiles_path(self):
        return os.path.join(os.path.dirname(__file__), "voice_profiles.json")

    def _load_profiles_data(self):
        import json
        path = self._profiles_path()
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def _save_profiles_data(self, data):
        import json
        with open(self._profiles_path(), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _refresh_profiles(self):
        data = self._load_profiles_data()
        self.profile_combo.clear()
        self.profile_combo.addItems(sorted(data.keys()))

    def _save_voice_profile(self):
        name = self.profile_name_input.text().strip()
        if not name:
            return
        data = self._load_profiles_data()
        # Get per-role slider values
        male_speed = self._role_sliders["male"]["speed"][0].value() if "male" in self._role_sliders else 0
        male_pitch = self._role_sliders["male"]["pitch"][0].value() if "male" in self._role_sliders else 0
        female_speed = self._role_sliders["female"]["speed"][0].value() if "female" in self._role_sliders else 0
        female_pitch = self._role_sliders["female"]["pitch"][0].value() if "female" in self._role_sliders else 0
        narrator_speed = self._role_sliders["narrator"]["speed"][0].value() if "narrator" in self._role_sliders else 0
        narrator_pitch = self._role_sliders["narrator"]["pitch"][0].value() if "narrator" in self._role_sliders else 0
        data[name] = {
            "male_ru": self.voice_combo.currentText(),
            "male_en": self.male_en_voice_combo.currentText(),
            "female_ru": self.female_voice_combo.currentText(),
            "female_en": self.female_en_voice_combo.currentText(),
            "narrator_ru": self.narrator_combo.currentText(),
            "narrator_en": self.narrator_en_voice_combo.currentText(),
            "volume": self.vol_slider.value(),
            # Per-role speed/pitch
            "male_speed": male_speed,
            "male_pitch": male_pitch,
            "female_speed": female_speed,
            "female_pitch": female_pitch,
            "narrator_speed": narrator_speed,
            "narrator_pitch": narrator_pitch,
            # Legacy fields for backward compat
            "speed": male_speed,
            "pitch": male_pitch,
        }
        self._save_profiles_data(data)
        self._refresh_profiles()
        self.statusBar().showMessage(f"Профиль «{name}» сохранён")

    def _load_voice_profile(self):
        name = self.profile_combo.currentText()
        if not name:
            return
        data = self._load_profiles_data()
        if name not in data:
            return
        p = data[name]
        idx = self.voice_combo.findText(
            p.get("male_ru", p.get("voice_male", p.get("voice", ""))))
        if idx >= 0:
            self.voice_combo.setCurrentIndex(idx)
        idx = self.male_en_voice_combo.findText(p.get("male_en", "en-US-GuyNeural"))
        if idx >= 0:
            self.male_en_voice_combo.setCurrentIndex(idx)
        idx = self.female_voice_combo.findText(
            p.get("female_ru", p.get("voice_female", "")))
        if idx >= 0:
            self.female_voice_combo.setCurrentIndex(idx)
        idx = self.female_en_voice_combo.findText(p.get("female_en", "en-US-JennyNeural"))
        if idx >= 0:
            self.female_en_voice_combo.setCurrentIndex(idx)
        idx = self.narrator_combo.findText(
            p.get("narrator_ru", p.get("voice_narrator", p.get("narrator", ""))))
        if idx >= 0:
            self.narrator_combo.setCurrentIndex(idx)
        idx = self.narrator_en_voice_combo.findText(
            p.get("narrator_en", "en-US-GuyMultilingualNeural"))
        if idx >= 0:
            self.narrator_en_voice_combo.setCurrentIndex(idx)
        # Per-role speed/pitch
        if "male" in self._role_sliders:
            self._role_sliders["male"]["speed"][0].setValue(p.get("male_speed", p.get("speed", 0)))
            self._role_sliders["male"]["pitch"][0].setValue(p.get("male_pitch", p.get("pitch", 0)))
        if "female" in self._role_sliders:
            self._role_sliders["female"]["speed"][0].setValue(p.get("female_speed", 0))
            self._role_sliders["female"]["pitch"][0].setValue(p.get("female_pitch", 0))
        if "narrator" in self._role_sliders:
            self._role_sliders["narrator"]["speed"][0].setValue(p.get("narrator_speed", 0))
            self._role_sliders["narrator"]["pitch"][0].setValue(p.get("narrator_pitch", 0))
        self.vol_slider.setValue(p.get("volume", 100))
        self.statusBar().showMessage(f"Профиль «{name}» загружен")

    def _delete_voice_profile(self):
        name = self.profile_combo.currentText()
        if not name:
            return
        data = self._load_profiles_data()
        if name in data:
            del data[name]
            self._save_profiles_data(data)
            self._refresh_profiles()
            self.statusBar().showMessage(f"Профиль «{name}» удалён")

    def _test_single_voice(self, combo):
        """Тест одного голоса с текущими настройками роли (скорость, громкость, питч)."""
        if not hasattr(self, 'tts'):
            return
        voice = combo.currentText()
        self.tts.set_voice(voice)
        self.tts.set_volume(self.vol_slider.value() / 100.0)

        # Determine which role this combo belongs to
        role = "male"
        if combo is self.female_voice_combo:
            role = "female"
        elif combo is self.narrator_combo:
            role = "narrator"
        self.tts.set_role(role)

        text = tr("test_phrase")

        def _run():
            loop = None
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(self.tts.speak_instant(text))
            except Exception as e:
                logger.error(f"[TTS] Test voice error: {e}")
            finally:
                if loop is not None:
                    loop.close()
        threading.Thread(target=_run, daemon=True).start()
        self.statusBar().showMessage(f"Тест: {voice}")

    def _test_voice(self):
        if hasattr(self, 'tts'):
            self.tts.set_voice(self.voice_combo.currentText())
            self.tts.set_role("male")
            text = tr("test_phrase")

            def _run():
                loop = None
                try:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    loop.run_until_complete(self.tts.speak_instant(text))
                except Exception as e:
                    logger.error(f"[TTS] Test error: {e}")
                finally:
                    if loop is not None:
                        loop.close()
            threading.Thread(target=_run, daemon=True).start()
            self.statusBar().showMessage(tr("status_test_speaking"))

    def _speak_instant(self):
        """Мгновенное чтение текста с toast-таймером задержки."""
        text = self.result_text.toPlainText().strip()
        if not text:
            QMessageBox.information(self, tr("dlg_info"), tr("msg_text_empty"))
            return
        if not hasattr(self, 'tts'):
            return
        self.tts.set_voice(self.voice_combo.currentText())
        self.tts.set_role("male")

        import time
        t_start = time.monotonic()
        parent_widget = self.centralWidget()

        def _on_first_audio():
            elapsed = time.monotonic() - t_start
            QTimer.singleShot(0, lambda e=elapsed, p=parent_widget: Toast(
                f"\u26a1 Задержка: {e:.1f} сек", "#4caf50", 4000, p
            ).show_toast())

        def _run():
            loop = None
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(self.tts.speak_instant(text, callback=_on_first_audio))
            except Exception as e:
                logger.error(f"[INSTANT] Error: {e}")
            finally:
                if loop is not None:
                    loop.close()
        threading.Thread(target=_run, daemon=True).start()
        self.statusBar().showMessage(tr("status_instant_speaking", default="Мгновенное чтение..."))

    def _test_dialogue(self):
        """Диалог в стиле аниме/сериала — контекстный парсер."""
        dialogue = (
            "Павел проснулся от звонка телефона. Он потянулся и посмотрел на экран."
            "\nЛера говорит: Привет! Ты уже проснулся?"
            "\nПавел ответил: Да, только что. Что случилось?"
            "\nЛера сказала: Я нашла билеты в зоопарк! Давай сходим вместе!"
            "\nПавел подумал и произнёс: Звучит как план. Когда?"
            "\nЛера ответила: Завтра утром, в десять часов."
            "\nПавел кивнул и сказал: Договорились. Я буду готов."
            "\nЛера добавила: И возьми с собой фотоаппарат, там будут лемуры!"
            "\nНа следующий день они встретились у входа в зоопарк."
            "\nЛера воскликнула: Ого, какие красивые птицы! Посмотри!"
            "\nПавел улыбнулся и ответил: Да, действительно впечатляет."
            "\nОни медленно шли вдоль вольеров и любовались животными."
            "\nЛера сказала: Давай подойдём к лемурам, я хочу их покормить!"
            "\nПавел засмеялся и произнёс: Ты всегда такая энергичная по утрам."
            "\nОни подошли к вольеру с лемурами. Маленькие зверьки забавно прыгали с ветки на ветку."
            "\nЛера воскликнула: Посмотри, тот большой лемур на тебя смотрит!"
            "\nПавел засмеялся: Похоже, он тоже хочет фотоаппарат."
        )
        self.result_text.setPlainText(dialogue)
        self.tts.set_voice(self.voice_combo.currentText())

        voice_a = self.voice_combo.currentText()
        voice_b = self.female_voice_combo.currentText()
        voice_narrator = self.narrator_combo.currentText()
        # Если женский совпадает с мужским — ищем другой
        if voice_b == voice_a:
            voices = self._get_voices_list()
            for v in voices:
                if v != voice_a:
                    voice_b = v
                    break
        logger.info(f"[DIALOGUE GUI] A={voice_a}, B={voice_b}, narrator={voice_narrator}")

        import time
        t_start = time.monotonic()
        parent_widget = self.centralWidget()

        def _on_first_audio():
            elapsed = time.monotonic() - t_start
            n = len([l for l in dialogue.split("\n") if l.strip()])
            QTimer.singleShot(0, lambda e=elapsed, c=n, p=parent_widget: Toast(
                f"\u26a1 Задержка: {e:.1f} сек | {c} строк, контекстный парсер",
                "#9c27b0", 5000, p
            ).show_toast())

        def _run():
            loop = None
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(
                    self.tts.speak_dialogue(
                        dialogue,
                        voice_a=voice_a,
                        voice_b=voice_b,
                        voice_narrator=voice_narrator,
                        callback=_on_first_audio,
                    )
                )
            except Exception as e:
                logger.error(f"[TTS] Dialogue error: {e}")
            finally:
                if loop is not None:
                    loop.close()
        threading.Thread(target=_run, daemon=True).start()
        self.statusBar().showMessage(tr("status_multi_test", default="Диалог..."))

    def _toggle_speech_mode(self, mode: int):
        """Включение/выключение режима озвучки по номеру (1-4).

        Повторное нажатие той же цифры останавливает озвучку.
        Режимы:
          1 — один голос (однократная озвучка)
          2 — много языков (автоопределение языка/пола)
          3 — реальное время, один голос (как радио)
          4 — реальное время, мультиголос (авто по полу)
        """
        # Повторное нажатие активного режима — остановка
        if self._active_speech_mode == mode:
            self._stop_all_speech()
            self._active_speech_mode = None
            self._update_speech_indicator()
            self.statusBar().showMessage(tr("status_speech_stopped", mode=mode))
            logger.info(f"[SPEECH] Режим {mode} остановлен")
            return

        # Переключение с другого режима — сначала остановить предыдущий
        if self._active_speech_mode is not None:
            self._stop_all_speech()

        self._active_speech_mode = mode
        logger.info(f"[SPEECH] Режим {mode} включён")

        if mode == 1:
            self._speak_result()
            self._active_speech_mode = None
        elif mode == 2:
            self._speak_result()
            self._active_speech_mode = None
        elif mode == 3:
            self.settings.set("tts.multi_voice", False)
            self._start_live_tts()
            self.statusBar().showMessage(tr("status_speech_started", mode=mode))
        elif mode == 4:
            self.settings.set("tts.multi_voice", True)
            self._start_live_tts()
            self.statusBar().showMessage(tr("status_speech_started", mode=mode))

        self._update_speech_indicator()

    def _update_speech_indicator(self):
        """Обновляет индикатор активного режима озвучки и состояние кнопок."""
        lw = self.live_window
        mode = self._active_speech_mode
        # Индикатор в live_window
        if lw and hasattr(lw, "speech_mode_indicator"):
            if mode:
                lw.speech_mode_indicator.setText(tr("speech_mode_active", mode=mode))
                lw.speech_mode_indicator.setStyleSheet("color: #81C784; font-weight: bold;")
            else:
                lw.speech_mode_indicator.setText(tr("speech_mode_none"))
                lw.speech_mode_indicator.setStyleSheet("color: #EF5350; font-weight: bold;")
        # Индикатор в scan tab (если есть)
        if hasattr(self, "speech_mode_indicator"):
            if mode:
                self.speech_mode_indicator.setText(tr("speech_mode_active", mode=mode))
                self.speech_mode_indicator.setStyleSheet("color: #81C784; font-weight: bold;")
            else:
                self.speech_mode_indicator.setText(tr("speech_mode_none"))
                self.speech_mode_indicator.setStyleSheet("color: #EF5350; font-weight: bold;")
        # Подсветка кнопок в live_window
        if lw and hasattr(lw, "_speech_mode_buttons"):
            for n, btn in lw._speech_mode_buttons.items():
                btn.setChecked(n == mode)
        # Подсветка кнопок в scan tab
        if hasattr(self, "_speech_mode_buttons"):
            for n, btn in self._speech_mode_buttons.items():
                btn.setChecked(n == mode)

    # ==================== TTS ERROR HANDLING ====================

    def _on_tts_error(self, error_msg: str):
        """Обработка ошибки TTS: показать в индикаторе + автофоллбэк."""
        # Show error in indicator
        self._voice_indicator.setText(f"  ⚠ Ошибка TTS")
        self._voice_indicator.setStyleSheet(
            "color: #f38ba8; font-size: 11px; padding: 2px 6px; "
            "border-radius: 3px; background: #1e1e2e; font-weight: bold;")

        # Auto-fallback: try another voice
        fallback_voices = [
            ("ru-RU-SvetlanaNeural", "edge"),
            ("ru-RU-DmitryNeural", "edge"),
            ("en-US-GuyNeural", "edge"),
            ("en-US-JennyNeural", "edge"),
        ]
        current = getattr(self, '_active_voice', '')
        for code, vtype in fallback_voices:
            if code != current:
                self._active_voice = code
                self._active_voice_type = vtype
                self.tts.voice = code
                self.tts.voice_type = vtype
                self._update_voice_indicator()
                logger.info(f"[TTS] Auto-fallback: {current} → {code}")
                self.statusBar().showMessage(f"Голос недоступен → переключено на {code}")
                break

        QTimer.singleShot(3000, self._update_voice_indicator)

    def _stop_all_speech(self):
        """Останавливает любую текущую озвучку мгновенно."""
        self._clear_highlight()
        self._tts_playing = False
        # Ядро: force_stop убивает ВСЕ процессы (MCI, VLC, subprocess)
        try:
            self.tts.force_stop()
        except Exception as e:
            logger.debug(f"[SPEECH] tts.force_stop error: {e}")
        # Дополнительно: принудительно убить subprocess если остался
        try:
            if hasattr(self.tts, "_current_process") and self.tts._current_process:
                self.tts._current_process.kill()
                self.tts._current_process = None
        except Exception:
            pass
        try:
            if self.live_scanner.running:
                self._stop_live_tts()
        except Exception as e:
            logger.debug(f"[SPEECH] stop live error: {e}")

    def _get_translator_config(self):
        """Возвращает (api_key, model) из настроек."""
        api_key = self.settings.get("translation.api_key", "")
        model = self.settings.get("translation.model", "")
        return api_key, model

    def _translate_for_tts(self, text: str) -> str:
        """Переводит текст перед озвучкой, если опция включена.
        Выполняется синхронно — вызывать только из потока, НЕ из GUI.
        """
        if not self.settings.get("translation.enabled", False):
            return text
        if not text or not text.strip():
            return text
        dst = self.settings.get("translation.dst_lang", "ru")
        api_key, model = self._get_translator_config()
        try:
            from translator import translator
            result = translator.translate(text, src="auto", dst=dst,
                                         api_key=api_key, model=model)
            if result and "Translation failed" not in result:
                return result
        except Exception as e:
            logger.debug(f"[TTS] Translate error: {e}")
        return text

    def _speak_result(self):
        text = self.result_text.toPlainText().strip()
        if not text:
            return
        self._stop_all_speech()
        try:
            self._select_voice_for_text(text)
            self.tts.voice_type = self._active_voice_type
            self.tts.set_role(getattr(self, '_active_role', 'male'))
            self._tts_playing = True
            self._update_voice_indicator()

            def _run():
                loop = None
                try:
                    speak_text = self._translate_for_tts(text)
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    loop.run_until_complete(self.tts.speak(speak_text))
                except Exception as e:
                    logger.error(f"[TTS] Error: {e}")
                    QTimer.singleShot(0, lambda: self._on_tts_error(str(e)))
                finally:
                    self._tts_playing = False
                    QTimer.singleShot(0, self._update_voice_indicator)
                    if loop is not None:
                        loop.close()
            threading.Thread(target=_run, daemon=True).start()
            self.statusBar().showMessage(tr("status_instant_speaking", default="Мгновенное чтение..."))
        except Exception as e:
            logger.error(f"[TTS] Error: {e}")
            self._tts_playing = False

    def _start_highlight_playback(self, audio_path, word_timings):
        """Запуск воспроизведения с подсветкой слов по таймингам."""
        self._highlight_words = word_timings
        self._highlight_index = 0
        self._highlight_start_time = time.time()
        self._highlight_audio_path = audio_path

        # Оригинальный текст result_text (с пробелами между словами)
        full_text = self.result_text.toPlainText()
        self._highlight_full_text = full_text

        # Запускаем воспроизведение в фоне
        def _play():
            loop = None
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(self.tts._play_audio(audio_path))
            except Exception as e:
                logger.error(f"[HIGHLIGHT] Play error: {e}")
            finally:
                self._tts_playing = False
                QTimer.singleShot(0, self._clear_highlight)
                if loop:
                    loop.close()
        threading.Thread(target=_play, daemon=True).start()

        # Таймер подсветки — 30 FPS
        self._highlight_timer = QTimer()
        self._highlight_timer.setInterval(33)
        self._highlight_timer.timeout.connect(self._tick_highlight)
        self._highlight_timer.start()

    def _tick_highlight(self):
        """Одиночный тик таймера — подсветка текущего слова."""
        if not self._tts_playing or self._highlight_index >= len(self._highlight_words):
            return

        elapsed = time.time() - self._highlight_start_time
        words = self._highlight_words

        # Найти слово, которое сейчас озвучивается
        while (self._highlight_index < len(words) and
               words[self._highlight_index][1] + words[self._highlight_index][2] < elapsed):
            self._highlight_index += 1

        if self._highlight_index >= len(words):
            return

        word_text, start_sec, dur_sec = words[self._highlight_index]
        # Подсветка если слово сейчас активно
        if start_sec <= elapsed <= start_sec + dur_sec + 0.05:
            self._highlight_word_in_text(self._highlight_index)

    def _highlight_word_in_text(self, word_index):
        """Подсветка слова по индексу в QTextEdit."""
        words = self._highlight_words
        if word_index >= len(words):
            return

        cursor = self.result_text.textCursor()
        cursor.beginEditBlock()

        # Убрать предыдущую подсветку
        if hasattr(self, '_prev_highlight_pos') and self._prev_highlight_pos is not None:
            cursor.setPosition(self._prev_highlight_pos)
            cursor.setPosition(self._prev_highlight_pos + self._prev_highlight_len,
                             QTextCursor.MoveMode.KeepAnchor)
            fmt = QTextCharFormat()
            fmt.setBackground(QColor(0, 0, 0, 0))
            fmt.setForeground(QColor(255, 255, 255))
            cursor.mergeCharFormat(fmt)

        # Найти позицию слова в полном тексте
        full_text = self._highlight_full_text
        search_from = getattr(self, '_prev_highlight_pos', 0) or 0
        word_text = words[word_index][0]
        pos = full_text.find(word_text, search_from)
        if pos == -1:
            # Попробовать с начала
            pos = full_text.find(word_text, 0)

        if pos != -1:
            cursor.setPosition(pos)
            cursor.setPosition(pos + len(word_text), QTextCursor.MoveMode.KeepAnchor)
            fmt = QTextCharFormat()
            fmt.setBackground(QColor(33, 150, 243, 100))
            fmt.setForeground(QColor(130, 200, 255))
            fmt.setFontWeight(QFont.Weight.Bold)
            cursor.mergeCharFormat(fmt)

            self._prev_highlight_pos = pos
            self._prev_highlight_len = len(word_text)

        cursor.endEditBlock()

    def _clear_highlight(self):
        """Снять подсветку после окончания воспроизведения."""
        if hasattr(self, '_highlight_timer'):
            self._highlight_timer.stop()
        if hasattr(self, '_prev_highlight_pos') and self._prev_highlight_pos is not None:
            cursor = self.result_text.textCursor()
            cursor.beginEditBlock()
            cursor.setPosition(self._prev_highlight_pos)
            cursor.setPosition(self._prev_highlight_pos + self._prev_highlight_len,
                             QTextCursor.MoveMode.KeepAnchor)
            fmt = QTextCharFormat()
            fmt.setBackground(QColor(0, 0, 0, 0))
            fmt.setForeground(QColor(255, 255, 255))
            cursor.mergeCharFormat(fmt)
            cursor.endEditBlock()
        self._prev_highlight_pos = None
        self._prev_highlight_len = 0
        self._highlight_words = []
        self._highlight_index = 0

    def _save_audio_result(self):
        text = self.result_text.toPlainText()
        if not text:
            QMessageBox.information(self, tr("dlg_save_audio"), tr("msg_nothing_to_save"))
            return
        try:
            self.tts.set_voice(self.voice_combo.currentText())
            default_name = f"ocr_text_{int(time.time())}.mp3"
            path, _ = QFileDialog.getSaveFileName(
                self, tr("dlg_save_audio"), default_name, "MP3 Files (*.mp3)"
            )
            if not path:
                return

            def _run_generate():
                loop = None
                try:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    file_path = loop.run_until_complete(
                        self.tts.generate_audio_file(text, path)
                    )
                    from PyQt6.QtWidgets import QApplication
                    app = QApplication.instance()
                    if app:
                        from PyQt6.QtCore import QTimer
                        QTimer.singleShot(0, lambda: self.statusBar().showMessage(tr("status_saved_to", path=file_path), 5000))
                        QTimer.singleShot(0, lambda: QMessageBox.information(
                            self, tr("audio_saved_title"),
                            tr("audio_saved_body", path=file_path, dur=len(text) // 15)
                        ))
                except Exception as e:
                    logger.error(f"[TTS] Audio generation error: {e}")
                    from PyQt6.QtWidgets import QApplication
                    app = QApplication.instance()
                    if app:
                        from PyQt6.QtCore import QTimer
                        QTimer.singleShot(0, lambda: QMessageBox.critical(
                            self, tr("dlg_error"), tr("audio_save_failed", err=e)
                        ))
                finally:
                    if loop is not None:
                        loop.close()
            threading.Thread(target=_run_generate, daemon=True).start()
            self.statusBar().showMessage(tr("status_generating_audio"))
        except Exception as e:
            logger.error(f"[TTS] Error: {e}")
            self.statusBar().showMessage(tr("tts_error_prefix", err=e))

    def _translate_and_speak_region(self, region):
        """Сканирует регион, переводит текст и озвучивает."""
        current_text = self.result_text.toPlainText().strip()
        if current_text:
            text = current_text
        else:
            self._do_scan(region)
            QTimer.singleShot(500, lambda: self._translate_and_speak_deferred(region))
            return

        self._translate_and_speak_do(text)

    def _translate_and_speak_deferred(self, region):
        text = self.result_text.toPlainText().strip()
        if not text:
            self.statusBar().showMessage(tr("status_no_text", name=region['name']))
            return
        self._translate_and_speak_do(text)

    def _translate_and_speak_do(self, text):
        dst = self.settings.get("translation.dst_lang", "ru")
        # Если dst не задан — берём из комбобокса переводчика
        if dst == "ru" and hasattr(self, 'trans_dst_combo'):
            dst_code = self.trans_dst_combo.currentData()
            if dst_code:
                dst = dst_code
        api_key, model = self._get_translator_config()
        self.statusBar().showMessage(tr("status_translating"))
        try:
            from translator import translator
            translated = translator.translate(text, src="auto", dst=dst,
                                             api_key=api_key, model=model)
            if not translated or "Translation failed" in translated:
                translated = text
        except Exception as e:
            logger.error(f"[TTS] Translate error: {e}")
            translated = text

        self.result_text.setPlainText(translated)

        # Озвучиваем переведённый текст активным голосом
        try:
            self.tts.voice = self._active_voice
            self.tts.voice_type = self._active_voice_type
            self.tts.set_role(getattr(self, '_active_role', 'male'))

            def _run():
                loop = None
                try:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    loop.run_until_complete(self.tts.speak_instant(translated))
                except Exception as e:
                    logger.error(f"[TTS] Error: {e}")
                finally:
                    if loop is not None:
                        loop.close()
            threading.Thread(target=_run, daemon=True).start()
            self.statusBar().showMessage(tr("status_translated_tts", text=translated[:50]))
        except Exception as e:
            logger.error(f"[TTS] Error: {e}")

    def _start_live_tts(self):
        """Запуск live TTS — делегирует в _start_live_mode с синхронизацией кнопок."""
        if not self.ocr:
            self.statusBar().showMessage("OCR загружается... Подождите.")
            return
        if not self.regions:
            QMessageBox.information(self, "Live TTS", tr("msg_create_at_least_one"))
            return
        region = self.regions[0]
        self.live_scanner.set_region(region["x"], region["y"], region["width"], region["height"])
        self._start_live_mode()
        self.live_scanner.clear_cache()
        self.statusBar().showMessage(tr("status_live_active"))

    def _stop_live_tts(self):
        """Остановка live TTS — делегирует в _stop_live_mode."""
        self._stop_live_mode()

    def _start_live_for_region(self, index):
        if index < 0 or index >= len(self.regions):
            return
        region = self.regions[index]
        # Если уже запущен — останавливаем (toggle)
        if self.live_scanner.running:
            self._stop_live_mode()
            return
        # Синхронизируем голос из combo
        if hasattr(self, 'tts') and hasattr(self, 'voice_combo'):
            self.tts.set_voice(self.voice_combo.currentText())
        self.live_scanner.set_region(region["x"], region["y"], region["width"], region["height"])
        lw = self.live_window
        self.live_scanner.set_interval(lw.live_interval.value())
        self.live_scanner.set_detect_changes(lw.detect_changes_check.isChecked())
        self.live_scanner.multi_voice = self.settings.get("tts.multi_voice", False)
        self.live_scanner.set_line_by_line_mode(lw.line_by_line_check.isChecked())
        self.live_scanner.set_sync_mode(lw.sync_mode_check.isChecked())
        self.live_scanner.set_sentence_mode(lw.sentence_mode_check.isChecked())
        self.live_scanner.set_chunk_clear_mode(lw.chunk_clear_check.isChecked())
        if hasattr(self, 'tts'):
            self.tts.set_role(getattr(self, '_active_role', 'male'))
            self.live_scanner.tts_engine = self.tts
        self.live_scanner._tts_paused = False
        asyncio.run_coroutine_threadsafe(
            self.live_scanner.start_tts_streaming(), self._live_event_loop
        )
        asyncio.run_coroutine_threadsafe(self.live_scanner.start(), self._live_event_loop)
        self._sync_live_buttons(running=True)
        self.statusBar().showMessage(tr("status_live_for_region", name=region['name']))

    def _save_result(self):
        text = self.result_text.toPlainText()
        if not text:
            QMessageBox.information(self, tr("dlg_save"), tr("msg_nothing_to_save"))
            return
        path, _ = QFileDialog.getSaveFileName(self, tr("dlg_save"), "result.txt", "Text Files (*.txt)")
        if path:
            try:
                with open(path, 'w', encoding='utf-8') as f:
                    f.write(text)
                self.statusBar().showMessage(tr("status_saved_to", path=path))
            except Exception as e:
                logger.error(f"[SAVE] Error: {e}")
                QMessageBox.warning(self, tr("dlg_error"), str(e))

    # ==================== CONTEXT MENU ====================

    def _show_region_context_menu(self, index):
        if index == -1:
            menu = QMenu(self)
            menu.setStyleSheet("""
                QMenu { background-color: #2b2b3b; color: #cdd6f4; border: 1px solid #45475a; }
                QMenu::item:selected { background-color: #585b70; }
            """)
            create_action = menu.addAction(tr("ctx_create_new", default="Создать рамку здесь"))
            action = menu.exec(QCursor.pos())
            if action == create_action:
                self._add_region_at_cursor()
            return

        if index < 0 or index >= len(self.regions):
            return
        region = self.regions[index]
        locked = region.get("locked", False)
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu { background-color: #2b2b3b; color: #cdd6f4; border: 1px solid #45475a; }
            QMenu::item:selected { background-color: #585b70; }
        """)
        edit_action = menu.addAction(tr("ctx_edit"))
        delete_action = menu.addAction(tr("ctx_delete"))
        lock_text = tr("ctx_unlock", default="Разблокировать рамку") if locked else tr("ctx_lock", default="Зафиксировать рамку")
        region_lock_action = menu.addAction(lock_text)
        menu.addSeparator()
        scan_action = menu.addAction(tr("ctx_scan"))
        translate_speak_action = menu.addAction(tr("ctx_translate_speak"))
        live_action = menu.addAction(tr("ctx_live"))
        action = menu.exec(QCursor.pos())
        if action == edit_action:
            self._edit_region_by_index(index)
        elif action == delete_action:
            self._delete_region_by_index(index)
        elif action == region_lock_action:
            region["locked"] = not locked
            self._save_regions()
            status = tr("status_locked", default="Зафиксирована") if region["locked"] else tr("status_unlocked", default="Разблокирована")
            self.statusBar().showMessage(tr("status_region_lock_set", name=region['name'], status=status))
        elif action == scan_action:
            self._do_scan(region)
        elif action == translate_speak_action:
            self._translate_and_speak_region(region)
        elif action == live_action:
            self._start_live_for_region(index)

    def _edit_region_by_index(self, index):
        if index < 0 or index >= len(self.regions):
            return
        dialog = RegionDialog(region=self.regions[index], parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.regions[index] = dialog.get_region()
            self._refresh_region_list()
            self._save_regions()

    def _delete_region_by_index(self, index):
        if index < 0 or index >= len(self.regions):
            return
        reply = QMessageBox.question(
            self, tr("dlg_delete_region"),
            tr("msg_delete_confirm", name=self.regions[index]['name']),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            del self.regions[index]
            self._refresh_region_list()
            self._save_regions()
            self.statusBar().showMessage(tr("status_region_deleted"))


    # ==================== WINDOW SELECTOR ====================

    def _select_app_window(self):
        """Показать список окон для выбора (как в ArtMoney)."""
        from ocr_wrapper import OCRWrapper
        windows = OCRWrapper.list_windows()
        if not windows:
            QMessageBox.information(self, "Выбор окна", "Не удалось найти видимые окна.")
            return

        items = [f"{title} [0x{hwnd:08X}]" for hwnd, title in windows]
        item, ok = QInputDialog.getItem(
            self, "Выбор окна приложения",
            "Выберите окно для сканирования:",
            items, 0, False
        )
        if ok and item:
            idx = items.index(item)
            hwnd, title = windows[idx]
            self.ocr.select_window(hwnd)
            # Привязать оверлей к окну
            if hasattr(self, 'region_overlay'):
                self.region_overlay.set_target_window(hwnd)
            lw = self.live_window
            lw.selected_window_label.setText(f"Окно: {title}")
            lw.select_window_btn.setText("Сбросить окно")
            lw.select_window_btn.clicked.disconnect()
            lw.select_window_btn.clicked.connect(self._clear_app_window)
            self.statusBar().showMessage(f"Выбрано окно: {title}", 3000)

    def _clear_app_window(self):
        """Сбросить выбор окна."""
        self.ocr.clear_window()
        # Снять привязку оверлея
        if hasattr(self, 'region_overlay'):
            self.region_overlay.set_target_window(None)
        lw = self.live_window
        lw.selected_window_label.setText("")
        lw.select_window_btn.setText("Выбрать окно")
        lw.select_window_btn.clicked.disconnect()
        lw.select_window_btn.clicked.connect(self._select_app_window)
        self.statusBar().showMessage("Выбор окна сброшен", 2000)

    def _web_select_window(self, hwnd: int):
        """Выбор окна из web API (без диалога). Вызывается из HTTP-потока — безопасно через QTimer."""
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(0, lambda h=hwnd: self._web_select_window_impl(h))

    def _web_select_window_impl(self, hwnd: int):
        if not self.ocr:
            return
        self.ocr.select_window(hwnd)
        if hasattr(self, 'region_overlay'):
            self.region_overlay.set_target_window(hwnd)
        lw = self.live_window
        lw.selected_window_label.setText(f"Окно: HWND {hwnd}")
        lw.select_window_btn.setText("Сбросить окно")
        try:
            lw.select_window_btn.clicked.disconnect()
        except TypeError:
            pass
        lw.select_window_btn.clicked.connect(self._clear_app_window)

    def _web_clear_window(self):
        """Сброс окна из web API. Вызывается из HTTP-потока — безопасно через QTimer."""
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(0, self._clear_app_window)


    # ==================== LIVE MODE ====================

    def _web_start_live(self):
        """Запуск Live Mode из web API (HTTP-поток → QTimer)."""
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(0, self._start_live_mode)

    def _web_stop_live(self):
        """Остановка Live Mode из web API (HTTP-поток → QTimer)."""
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(0, self._stop_live_mode)

    def _web_force_stop_tts(self):
        """Принудительная остановка всего TTS из web API (HTTP-поток → QTimer)."""
        from PyQt6.QtCore import QTimer
        def _do():
            try:
                if hasattr(self, 'tts') and self.tts:
                    self.tts.force_stop()
                if self.live_scanner and self.live_scanner.running:
                    asyncio.run_coroutine_threadsafe(self.live_scanner.stop(), self._live_event_loop)
                    asyncio.run_coroutine_threadsafe(self.live_scanner.stop_tts_streaming(), self._live_event_loop)
                    self.live_scanner.clear_spoken_lines()
                    self._sync_live_buttons(running=False)
            except Exception as e:
                logger.error(f"[WEB] force_stop_tts error: {e}")
        QTimer.singleShot(0, _do)

    def _toggle_live_mode(self, state=0):
        if not self._live_mode_lock.acquire(blocking=False):
            return
        try:
            if self.live_window.live_enable_check.isChecked():
                self._start_live_mode()
            else:
                self._stop_live_mode()
        finally:
            self._live_mode_lock.release()

    def _start_live_mode(self):
        lw = self.live_window
        try:
            if self.live_scanner.running:
                if hasattr(self, 'tts') and self.tts:
                    self.tts.force_stop()
                asyncio.run_coroutine_threadsafe(self.live_scanner.stop(), self._live_event_loop)
                asyncio.run_coroutine_threadsafe(
                    self.live_scanner.stop_tts_streaming(), self._live_event_loop
                )
            if not self.ocr:
                self.statusBar().showMessage("OCR загружается... Подождите.")
                return
            if not self.regions and not self.ocr._target_hwnd:
                QMessageBox.information(self, "Live Mode", tr("msg_create_at_least_one"))
                lw.live_enable_check.setChecked(False)
                return
            self.live_scanner.set_interval(lw.live_interval.value())
            self.live_scanner.set_detect_changes(lw.detect_changes_check.isChecked())
            self.live_scanner.multi_voice = self.settings.get("tts.multi_voice", False)
            self.live_scanner.set_line_by_line_mode(lw.line_by_line_check.isChecked())
            self.live_scanner.set_sync_mode(lw.sync_mode_check.isChecked())
            self.live_scanner.set_sentence_mode(lw.sentence_mode_check.isChecked())
            self.live_scanner.set_chunk_clear_mode(lw.chunk_clear_check.isChecked())
            if self.regions and not self.ocr._target_hwnd:
                r = self.regions[0]
                self.live_scanner.set_region(r["x"], r["y"], r["width"], r["height"])
            # Синхронизируем голос и движок с текущими настройками
            if hasattr(self, 'tts'):
                self.tts.set_role(getattr(self, '_active_role', 'male'))
                self.live_scanner.tts_engine = self.tts
            # Снять паузу TTS перед запуском live
            self.live_scanner._tts_paused = False
            asyncio.run_coroutine_threadsafe(
                self.live_scanner.start_tts_streaming(), self._live_event_loop
            )
            asyncio.run_coroutine_threadsafe(self.live_scanner.start(), self._live_event_loop)
            self._sync_live_buttons(running=True)
            self.statusBar().showMessage(tr("status_live_started"), 3000)
        except Exception as e:
            logger.error(f"[LIVE] _start_live_mode error: {e}", exc_info=True)
            QMessageBox.critical(self, tr("dlg_error"), f"Live Mode failed to start: {e}")
            self._sync_live_buttons(running=False)

    def _stop_live_mode(self):
        try:
            # Сначала убиваем текущее аудио мгновенно
            if hasattr(self, 'tts') and self.tts:
                self.tts.force_stop()
            asyncio.run_coroutine_threadsafe(self.live_scanner.stop(), self._live_event_loop)
            asyncio.run_coroutine_threadsafe(
                self.live_scanner.stop_tts_streaming(), self._live_event_loop
            )
            self.live_scanner.clear_spoken_lines()
            self._sync_live_buttons(running=False)
            self.statusBar().showMessage(tr("status_live_stopped"))
        except Exception as e:
            logger.error(f"[LIVE] _stop_live_mode error: {e}", exc_info=True)
            QMessageBox.warning(self, tr("dlg_error"), f"Live Mode failed to stop: {e}")

    def _sync_live_buttons(self, running: bool):
        """Синхронизирует все кнопки live-режима во всех вкладках + LiveModeWindow."""
        lw = self.live_window
        # LiveModeWindow — checkbox
        if lw and hasattr(lw, 'live_enable_check'):
            lw.live_enable_check.blockSignals(True)
            lw.live_enable_check.setChecked(running)
            lw.live_enable_check.blockSignals(False)
        # Scan tab — status label
        if hasattr(self, 'live_status_label'):
            self.live_status_label.setText(tr("live_on_label") if running else tr("live_off_label"))
        # Scan tab — Live TTS кнопки
        if lw and hasattr(lw, 'live_tts_btn'):
            lw.live_tts_btn.setEnabled(not running)
        if lw and hasattr(lw, 'live_tts_stop_btn'):
            lw.live_tts_stop_btn.setEnabled(running)
        if lw and hasattr(lw, 'live_tts_status'):
            if running:
                lw.live_tts_status.setText(tr("live_tts_active"))
                lw.live_tts_status.setStyleSheet("color: #81C784; font-weight: bold;")
            else:
                lw.live_tts_status.setText(tr("live_tts_idle"))
                lw.live_tts_status.setStyleSheet("color: #EF5350; font-weight: bold;")
        # Scan tab — open live button status
        if hasattr(self, '_live_window_status'):
            self._live_window_status.setText("Активен" if running else "")
            self._live_window_status.setStyleSheet(
                "color: #81C784; font-weight: bold;" if running else "color: #CAC4D0; font-size: 12px;")
        # TTS tab — play/stop кнопки
        if hasattr(self, 'live_play_btn'):
            self.live_play_btn.setEnabled(not running)
        if hasattr(self, 'live_stop_btn'):
            self.live_stop_btn.setEnabled(running)
        # Сброс активного режима озвучки при остановке
        if not running and self._active_speech_mode in (3, 4):
            self._active_speech_mode = None
            self._update_speech_indicator()

    def _load_live_dialog_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Загрузить диалог", "", "Text Files (*.txt);;All Files (*)"
        )
        if path:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    self.live_dialog_text.setPlainText(f.read())
                self.statusBar().showMessage(f"Диалог загружен: {path}", 3000)
            except Exception as e:
                QMessageBox.warning(self, "Ошибка", f"Не удалось загрузить файл: {e}")

    def _on_text_detected_gui(self, text):
        logger.info(f"[LIVE-GUI] Text received: {len(text)} chars.")
        if not text:
            return
        # Обновить текст под рамкой оверлея
        if hasattr(self, 'region_overlay'):
            self.region_overlay.set_ocr_text(text)
        filtered = self._filter_ocr_text(text)
        if not filtered:
            return

        # Режим реального времени С ОЧИСТКОЙ: каждое новое распознавание
        # заменяет предыдущий результат (управляется из интерфейса).
        scan_mode = self.settings.get("scan.mode", "realtime_keep")
        if scan_mode == "realtime_clear":
            self.result_text.setPlainText(filtered)
            scrollbar = self.result_text.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())
            logger.info("[LIVE-GUI] Text replaced (realtime clear mode)")
            return

        # Режим реального времени БЕЗ ОЧИСТКИ: накапливаем (поведение по умолчанию)
        prefix = ""
        current = self.result_text.toPlainText()
        if current:
            lines = current.split("\n")
            recent_lines = lines[-10:] if len(lines) >= 10 else lines
            for line in recent_lines:
                existing = line.strip()
                if not existing:
                    continue
                norm_existing = ' '.join(existing.lower().split())
                norm_new = ' '.join(filtered.lower().split())
                # Fuzzy dedup: если текст очень похож (>90%) — пропускаем
                if norm_existing == norm_new:
                    return
                if len(norm_existing) > 5 and len(norm_new) > 5:
                    # Проверяем вхождение нового текста в существующий или наоборот
                    if norm_new in norm_existing or norm_existing in norm_new:
                        return
            new_text = current + "\n" + prefix + filtered
            # Limit to last 200 lines to prevent unbounded growth
            new_lines = new_text.split("\n")
            if len(new_lines) > 200:
                new_text = "\n".join(new_lines[-200:])
            self.result_text.setPlainText(new_text)
        else:
            self.result_text.setPlainText(prefix + filtered)
        scrollbar = self.result_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
        logger.info(f"[LIVE-GUI] Text appended (total lines: {len(self.result_text.toPlainText().split(chr(10)))})")

    def _on_sentence_spoken_gui(self, remaining_text: str):
        """Пофразовый режим: обновление текста после стирания прочитанного предложения."""
        if hasattr(self, 'result_text') and self.result_text:
            self.result_text.setPlainText(remaining_text)
            logger.debug(f"[SENTENCE-GUI] Текст обновлён: {len(remaining_text)} символов")

    def _filter_ocr_text(self, text: str) -> str:
        """
        Минимальная очистка распознанного текста.

        В оригинальной рабочей версии текст показывался НАПРЯМУЮ, без
        фильтрации. Поэтому здесь убираем только настоящие ANSI escape-
        последовательности (с управляющим байтом ESC) — они невидимы и
        реально мешают. Никакой построчной обработки/отсева, чтобы НЕ
        терять буквы и слова.
        """
        if not text:
            return ""
        # Strip only real ANSI escape sequences (with the ESC byte 0x1b).
        cleaned = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', text)
        cleaned = cleaned.replace('\x1b', '')
        return cleaned.strip()

    def _on_scan_started_gui(self):
        from PyQt6.QtCore import QMetaObject, Qt, Q_ARG
        QMetaObject.invokeMethod(self.live_status_label, "setText",
                                 Qt.ConnectionType.QueuedConnection,
                                 Q_ARG(str, tr("live_on_label")))
        QMetaObject.invokeMethod(self.statusBar(), "showMessage",
                                 Qt.ConnectionType.QueuedConnection,
                                 Q_ARG(str, tr("status_live_mode_started")))

    def _on_scan_stopped_gui(self):
        from PyQt6.QtCore import QMetaObject, Qt, Q_ARG
        QMetaObject.invokeMethod(self.live_status_label, "setText",
                                 Qt.ConnectionType.QueuedConnection,
                                 Q_ARG(str, tr("live_off_label")))
        QMetaObject.invokeMethod(self.statusBar(), "showMessage",
                                 Qt.ConnectionType.QueuedConnection,
                                 Q_ARG(str, tr("status_live_stopped")))

    def _on_scan_timing_gui(self, seconds, text, confidence=0.0):
        """Обновление индикатора времени сканирования."""
        has_text = bool(text and text.strip())
        if has_text:
            self.scan_timing_label.setText(f"✅ {seconds:.1f}с")
            self.scan_timing_label.setStyleSheet("color: #4caf50; font-size: 11px; font-weight: bold; padding: 0 8px;")
        else:
            self.scan_timing_label.setText(f"— {seconds:.1f}с")
        self.scan_timing_label.setStyleSheet("color: #CAC4D0; font-size: 11px; padding: 0 8px;")
        # Скрыть через 3 секунды
        QTimer.singleShot(3000, lambda: self.scan_timing_label.setText(""))
        # Обновить индикатор под рамкой оверлея
        if hasattr(self, 'region_overlay'):
            self.region_overlay.set_ocr_text(self.region_overlay._last_ocr_text, seconds, confidence)

    # ==================== VOICES / TRAY ====================

    def _refresh_voices_list(self):
        try:
            voices = self.tts.get_voices()
            sapi_voices = []
            # Update all voice combos
            for combo_attr in ('voice_combo', 'male_en_voice_combo',
                               'female_voice_combo', 'female_en_voice_combo',
                               'narrator_combo', 'narrator_en_voice_combo'):
                if not hasattr(self, combo_attr):
                    continue
                combo = getattr(self, combo_attr)
                combo.blockSignals(True)
                old_text = combo.currentText()
                combo.clear()
                for v in voices:
                    combo.addItem(v["code"])
                    if v.get("type") == "sapi":
                        sapi_voices.append(v["code"])
                idx = combo.findText(old_text)
                if idx >= 0:
                    combo.setCurrentIndex(idx)
                combo.blockSignals(False)
            if hasattr(self, 'sapi_voices_label'):
                if sapi_voices:
                    self.sapi_voices_label.setText(
                        tr("sapi_voices_prefix", default="SAPI: ") +
                        ", ".join(sapi_voices[:5]) +
                        ("..." if len(sapi_voices) > 5 else ""))
                else:
                    self.sapi_voices_label.setText(tr("sapi_voices_none", default="SAPI: нет"))
            self.statusBar().showMessage(tr("status_voices_loaded", count=len(voices)))
        except Exception as e:
            if hasattr(self, 'sapi_voices_label'):
                self.sapi_voices_label.setText(tr("status_error", err=e))

    def _minimize_to_tray(self):
        if self.tray_icon and self.tray_icon.isVisible():
            self.hide()
            self.tray_icon.showMessage(
                tr("tray_minimized_title"),
                tr("tray_minimized_body"),
                QSystemTrayIcon.MessageIcon.Information,
                2000
            )
        else:
            self.showMinimized()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == event.Type.WindowStateChange:
            if self.isMinimized():
                self.region_overlay.hide()
            elif self.windowState() == Qt.WindowState.WindowNoState:
                self.region_overlay.show_overlay()

    # ==================== VOICE PROFILES ====================

    def _vp_profiles_path(self):
        return os.path.join(os.path.dirname(__file__), "voice_individual_profiles.json")

    def _vp_save_characters(self):
        """Сохранить привязки персонажей к голосам."""
        characters = {}
        for i, (name_edit, voice_combo) in enumerate(self._char_rows):
            name = name_edit.text().strip()
            if name:
                characters[f"char_{i}"] = {
                    "name": name,
                    "voice": voice_combo.currentText()
                }
        self.settings.set("tts.characters", characters)
        self.statusBar().showMessage(tr("vp_characters_saved", default="Персонажи сохранены"))
        logger.info(f"[VP] Сохранены персонажи: {list(characters.values())}")

    # ==================== VOICE ROUTER ====================

    def _on_router_voice_changed(self, lang_code: str, voice_code: str):
        """Обработчик изменения голоса для языка."""
        if hasattr(self.tts, 'voice_router') and voice_code:
            self.tts.voice_router.set_voice_for_language(lang_code, voice_code)
            logger.info(f"[ROUTER] {lang_code} → {voice_code}")

    def _on_router_fallback_changed(self, voice_code: str):
        """Обработчик изменения fallback голоса."""
        if hasattr(self.tts, 'voice_router') and voice_code:
            self.tts.voice_router.set_fallback_voice(voice_code)
            logger.info(f"[ROUTER] fallback → {voice_code}")

    def _on_reset_router(self):
        """Сброс маршрутизации к дефолтным значениям."""
        if hasattr(self.tts, 'voice_router'):
            self.tts.voice_router._set_defaults()
            # Обновляем UI
            for lang_code, combo in self._router_combos.items():
                voice = self.tts.voice_router.get_voice_for_language(lang_code)
                idx = combo.findText(voice)
                if idx >= 0:
                    combo.setCurrentIndex(idx)
            fb = self.tts.voice_router.get_fallback_voice()
            idx = self._router_fallback_combo.findText(fb)
            if idx >= 0:
                self._router_fallback_combo.setCurrentIndex(idx)
            self.statusBar().showMessage(tr("vp_router_reset", default="Маршрутизация сброшена"))

    def _create_translator_tab(self):
        """Вкладка переводчика — бесплатные API без ключей."""
        tab = QWidget()
        layout = QVBoxLayout(tab)

        hdr = QLabel("\U0001f4d6 " + tr("tab_translator") + "  \u2014  " + tr("online_tab_info",
            default="Онлайн: Edge-TTS + Переводчик + Веб-сервер + Телефон"))
        hdr.setStyleSheet("color: #89b4fa; font-size: 12px; padding: 4px 8px; "
                          "background: #1e2e3a; border-radius: 4px; font-weight: bold;")
        layout.addWidget(hdr)

        # ── Выбор языков ──────────────────────────────────────────────
        lang_row = QHBoxLayout()

        lang_row.addWidget(QLabel(tr("translator_src_lang")))
        self.trans_src_combo = QComboBox()
        for code, name in sorted(LANGUAGES.items(), key=lambda x: x[1]):
            self.trans_src_combo.addItem(f"{name} ({code})", code)
        lang_row.addWidget(self.trans_src_combo)

        lang_row.addWidget(QLabel(tr("translator_dst_lang")))
        self.trans_dst_combo = QComboBox()
        for code, name in sorted(LANGUAGES.items(), key=lambda x: x[1]):
            if code != "auto":
                self.trans_dst_combo.addItem(f"{name} ({code})", code)
        # По умолчанию: src=auto, dst=ru (или en)
        self.trans_dst_combo.setCurrentIndex(
            max(0, self.trans_dst_combo.findData("ru"))
        )
        lang_row.addWidget(self.trans_dst_combo)

        layout.addLayout(lang_row)

        # ── Кнопки ─────────────────────────────────────────────────────
        btn_row = QHBoxLayout()

        trans_btn = QPushButton(tr("translator_btn_translate"))
        trans_btn.clicked.connect(self._on_translate)
        btn_row.addWidget(trans_btn)

        speak_src_btn = QPushButton(tr("translator_btn_speak_src"))
        speak_src_btn.clicked.connect(self._on_speak_source)
        btn_row.addWidget(speak_src_btn)

        speak_dst_btn = QPushButton(tr("translator_btn_speak_dst"))
        speak_dst_btn.clicked.connect(self._on_speak_translation)
        btn_row.addWidget(speak_dst_btn)

        copy_btn = QPushButton(tr("translator_copy"))
        copy_btn.clicked.connect(self._on_copy_translation)
        btn_row.addWidget(copy_btn)

        layout.addLayout(btn_row)

        # ── Исходный текст ─────────────────────────────────────────────
        layout.addWidget(QLabel(tr("translator_input")))
        self.trans_input = QTextEdit()
        self.trans_input.setPlaceholderText("Enter text to translate...")
        self.trans_input.setMinimumHeight(120)
        layout.addWidget(self.trans_input)

        # ── Перевод ────────────────────────────────────────────────────
        layout.addWidget(QLabel(tr("translator_output")))
        self.trans_output = QTextEdit()
        self.trans_output.setReadOnly(True)
        self.trans_output.setPlaceholderText("Translation will appear here...")
        self.trans_output.setMinimumHeight(120)
        layout.addWidget(self.trans_output)

        # ── Статус ─────────────────────────────────────────────────────
        self.trans_status = QLabel("")
        layout.addWidget(self.trans_status)

        return tab

    def _on_translate(self):
        """Слот: нажата кнопка 'Перевести'."""
        text = self.trans_input.toPlainText().strip()
        if not text:
            self.trans_status.setText("Enter text first")
            return

        src = self.trans_src_combo.currentData()
        dst = self.trans_dst_combo.currentData()

        self.trans_status.setText(tr("translator_loading"))
        self.trans_output.setPlainText("")

        # Переводим в отдельном потоке чтобы не блокировать GUI
        api_key, model = self._get_translator_config()
        def _do_translate():
            try:
                from translator import translator
                result = translator.translate(text, src=src, dst=dst,
                                             api_key=api_key, model=model)
                # Обновляем GUI из главного потока
                from PyQt6.QtCore import QTimer
                QTimer.singleShot(0, lambda: self.trans_output.setPlainText(result))
                QTimer.singleShot(0, lambda: self.trans_status.setText(""))
            except Exception as e:
                from PyQt6.QtCore import QTimer
                err_msg = tr("translator_error", err=str(e))
                QTimer.singleShot(0, lambda: self.trans_status.setText(err_msg))

        import threading
        threading.Thread(target=_do_translate, daemon=True).start()

    def _on_speak_source(self):
        """Слот: озвучить исходный текст."""
        text = self.trans_input.toPlainText().strip()
        if text and hasattr(self, 'tts') and self.tts:
            self._speak_text(text)

    def _on_speak_translation(self):
        """Слот: озвучить перевод."""
        text = self.trans_output.toPlainText().strip()
        if text and hasattr(self, 'tts') and self.tts:
            self._speak_text(text)

    def _on_copy_translation(self):
        """Слот: копировать перевод в буфер."""
        text = self.trans_output.toPlainText().strip()
        if text:
            from PyQt6.QtWidgets import QApplication
            QApplication.clipboard().setText(text)
            self.statusBar().showMessage(tr("translator_copy"), 2000)

    def _on_check_translation(self):
        """Слот: проверить соединение с переводчиком (Zen, без ключа)."""
        api_key = self.settings_translate_apikey_input.text().strip()
        model = self.settings_translate_model_combo.currentText().strip()
        self.settings_translate_result.setText(tr("translate_check_loading"))
        self.settings_translate_check_btn.setEnabled(False)

        def _do_check():
            try:
                from translator import translator
                # OrcaRouter/OpenRouter удалены; проверяем Zen.
                r = translator.test_connection(api_key, model, engine="zen")
                if r["ok"]:
                    msg = (
                        f"<span style='color:#a6e3a1;'>✓ {r['message']}</span><br>"
                        f"<span style='color:#cdd6f4;'>"
                        f"Latency: {r['latency_ms']}ms | "
                        f"Tokens: {r['tokens_prompt']} prompt + "
                        f"{r['tokens_completion']} completion = "
                        f"<b>{r['tokens_total']} total</b></span>"
                    )
                else:
                    msg = f"<span style='color:#f38ba8;'>✗ {r['message']}</span>"
                from PyQt6.QtCore import QTimer
                QTimer.singleShot(0, lambda: self.settings_translate_result.setText(msg))
            except Exception as e:
                from PyQt6.QtCore import QTimer
                QTimer.singleShot(0, lambda: self.settings_translate_result.setText(
                    f"<span style='color:#f38ba8;'>Error: {e}</span>"
                ))
            finally:
                from PyQt6.QtCore import QTimer
                QTimer.singleShot(0, lambda: self.settings_translate_check_btn.setEnabled(True))

        import threading
        threading.Thread(target=_do_check, daemon=True).start()

    def _speak_text(self, text: str):
        """Озвучивает текст через TTS в отдельном потоке."""
        import threading

        def _run():
            loop = None
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(self.tts.speak_instant(text))
            except Exception as e:
                logger.error(f"[TTS] Speak error: {e}")
            finally:
                if loop is not None:
                    loop.close()

        threading.Thread(target=_run, daemon=True).start()

    def _create_settings_tab(self):
        """Вкладка настроек — все настройки приложения с прокруткой."""
        # Обёртка с прокруткой для маленьких экранов
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        tab = QWidget()
        scroll.setWidget(tab)

        # Единый шрифт
        tab.setStyleSheet("""
            QLabel, QCheckBox, QComboBox, QSpinBox, QPushButton, QGroupBox {
                font-size: 13px;
            }
            QGroupBox { font-weight: bold; }
            QGroupBox::title { font-weight: bold; }
        """)
        layout = QVBoxLayout(tab)
        layout.setSpacing(10)

        # ═══════════════════════════════════════════════════════════════
        # OCR settings
        # ═══════════════════════════════════════════════════════════════
        ocr_group = QGroupBox(tr("settings_ocr_group"))
        ocr_layout = QVBoxLayout(ocr_group)

        # Language
        lang_layout = QHBoxLayout()
        lang_layout.addWidget(QLabel(tr("lbl_language")))
        self.settings_lang_combo = QComboBox()
        self.settings_lang_combo.addItems(["rus+eng", "rus", "eng", "rus+eng+ukr"])
        self.settings_lang_combo.setCurrentText(self.settings.get("ocr.language", "rus+eng"))
        self.settings_lang_combo.currentTextChanged.connect(
            lambda v: self.settings.set("ocr.language", v)
        )
        lang_layout.addWidget(self.settings_lang_combo)
        ocr_layout.addLayout(lang_layout)

        # Engine
        engine_layout = QHBoxLayout()
        engine_layout.addWidget(QLabel(tr("lbl_engine")))
        self.settings_engine_combo = QComboBox()
        self.settings_engine_combo.addItems([
            "Google Lens",
            "TFLite Cyrillic",
            "EasyOCR",
        ])
        # Map display names to engine IDs
        self._engine_id_map = {
            "TFLite Cyrillic": "tflite_cyrillic",
            "EasyOCR": "easyocr",
            "Google Lens": "google_lens",
        }
        self._engine_name_map = {v: k for k, v in self._engine_id_map.items()}
        current_engine = self.settings.get("ocr.engine", "google_lens")
        if current_engine == "cyrillic_onnx":
            current_engine = "tflite_cyrillic"
        current_name = self._engine_name_map.get(current_engine, "Google Lens")
        self.settings_engine_combo.setCurrentText(current_name)
        self.settings_engine_combo.currentTextChanged.connect(
            lambda v: self.settings.set("ocr.engine", self._engine_id_map.get(v, "google_lens"))
        )
        engine_layout.addWidget(self.settings_engine_combo)
        ocr_layout.addLayout(engine_layout)

        # Content Preset
        preset_layout = QHBoxLayout()
        preset_layout.addWidget(QLabel(tr("lbl_content_preset")))
        self.settings_preset_combo = QComboBox()
        self.settings_preset_combo.addItems([
            "Сбалансированный",
            "Манга",
            "Комикс",
            "Манхва / Вебтун",
            "Визуальная новелла",
            "Игры (RPG)",
            "Субтитры",
            "Лайт-новелла",
            "Рукописный текст",
            "Веб-новелла",
        ])
        self._preset_id_map = {
            "Сбалансированный": "balanced",
            "Манга": "manga",
            "Комикс": "comic",
            "Манхва / Вебтун": "manhwa",
            "Визуальная новелла": "visual_novel",
            "Игры (RPG)": "rpg_games",
            "Субтитры": "subtitles",
            "Лайт-новелла": "light_novel",
            "Рукописный текст": "handwritten",
            "Веб-новелла": "webnovel",
        }
        self._preset_name_map = {v: k for k, v in self._preset_id_map.items()}
        current_preset = self.settings.get("ocr.content_preset", "balanced")
        current_preset_name = self._preset_name_map.get(current_preset, "Сбалансированный")
        self.settings_preset_combo.setCurrentText(current_preset_name)
        self.settings_preset_combo.currentTextChanged.connect(
            lambda v: self.settings.set("ocr.content_preset", self._preset_id_map.get(v, "balanced"))
        )
        preset_layout.addWidget(self.settings_preset_combo)
        ocr_layout.addLayout(preset_layout)

        # GPU
        gpu_layout = QHBoxLayout()
        self.settings_gpu_check = QCheckBox(tr("chk_use_gpu"))
        self.settings_gpu_check.setChecked(self.settings.get("ocr.use_gpu", True))
        self.settings_gpu_check.stateChanged.connect(
            lambda v: self.settings.set("ocr.use_gpu", v == Qt.CheckState.Checked.value)
        )
        gpu_layout.addWidget(self.settings_gpu_check)
        ocr_layout.addLayout(gpu_layout)

        # Confidence threshold
        conf_layout = QHBoxLayout()
        conf_layout.addWidget(QLabel(tr("lbl_confidence")))
        self.settings_conf_spin = QSpinBox()
        self.settings_conf_spin.setRange(10, 100)
        self.settings_conf_spin.setValue(self.settings.get("ocr.confidence_threshold", 60))
        self.settings_conf_spin.setSuffix("%")
        self.settings_conf_spin.valueChanged.connect(
            lambda v: self.settings.set("ocr.confidence_threshold", v)
        )
        conf_layout.addWidget(self.settings_conf_spin)
        ocr_layout.addLayout(conf_layout)

        # Scan interval
        interval_layout = QHBoxLayout()
        interval_layout.addWidget(QLabel(tr("lbl_scan_interval")))
        self.settings_interval_spin = QSpinBox()
        self.settings_interval_spin.setRange(100, 10000)
        self.settings_interval_spin.setSingleStep(100)
        self.settings_interval_spin.setValue(self.settings.get("ocr.scan_interval_ms", 1000))
        self.settings_interval_spin.setSuffix(tr("suffix_ms"))
        self.settings_interval_spin.valueChanged.connect(
            lambda v: self.settings.set("ocr.scan_interval_ms", v)
        )
        interval_layout.addWidget(self.settings_interval_spin)
        ocr_layout.addLayout(interval_layout)

        # Detect changes
        dc_layout = QHBoxLayout()
        self.settings_detect_changes_check = QCheckBox(tr("chk_detect_changes_detect"))
        self.settings_detect_changes_check.setChecked(self.settings.get("ocr.detect_changes", True))
        self.settings_detect_changes_check.stateChanged.connect(
            lambda v: self.settings.set("ocr.detect_changes", v == Qt.CheckState.Checked.value)
        )
        dc_layout.addWidget(self.settings_detect_changes_check)
        ocr_layout.addLayout(dc_layout)

        # Change threshold
        ct_layout = QHBoxLayout()
        ct_layout.addWidget(QLabel(tr("lbl_change_threshold")))
        self.settings_change_threshold_spin = QSpinBox()
        self.settings_change_threshold_spin.setRange(1, 50)
        self.settings_change_threshold_spin.setValue(
            int(self.settings.get("ocr.change_threshold", 0.05) * 100)
        )
        self.settings_change_threshold_spin.setSuffix("%")
        self.settings_change_threshold_spin.valueChanged.connect(
            lambda v: self.settings.set("ocr.change_threshold", v / 100.0)
        )
        ct_layout.addWidget(self.settings_change_threshold_spin)
        ocr_layout.addLayout(ct_layout)

        # Auto-detect region
        ad_layout = QHBoxLayout()
        self.settings_auto_detect_check = QCheckBox(tr("chk_auto_detect_region"))
        self.settings_auto_detect_check.setChecked(self.settings.get("ocr.auto_detect_region", False))
        self.settings_auto_detect_check.stateChanged.connect(
            lambda v: self.settings.set("ocr.auto_detect_region", v == Qt.CheckState.Checked.value)
        )
        ad_layout.addWidget(self.settings_auto_detect_check)
        ocr_layout.addLayout(ad_layout)

        # Contrast/Brightness
        cb_layout = QHBoxLayout()
        cb_layout.addWidget(QLabel("Контраст"))
        self.settings_contrast_spin = QDoubleSpinBox()
        self.settings_contrast_spin.setRange(0.5, 3.0)
        self.settings_contrast_spin.setSingleStep(0.1)
        self.settings_contrast_spin.setValue(self.settings.get("ocr.contrast", 1.5))
        self.settings_contrast_spin.valueChanged.connect(
            lambda v: self.settings.set("ocr.contrast", v)
        )
        cb_layout.addWidget(self.settings_contrast_spin)
        cb_layout.addWidget(QLabel("Яркость"))
        self.settings_brightness_spin = QDoubleSpinBox()
        self.settings_brightness_spin.setRange(0.5, 2.0)
        self.settings_brightness_spin.setSingleStep(0.1)
        self.settings_brightness_spin.setValue(self.settings.get("ocr.brightness", 1.0))
        self.settings_brightness_spin.valueChanged.connect(
            lambda v: self.settings.set("ocr.brightness", v)
        )
        cb_layout.addWidget(self.settings_brightness_spin)
        ocr_layout.addLayout(cb_layout)

        # Pipette button
        pipette_layout = QHBoxLayout()
        self.settings_pipette_btn = QPushButton("🎨 Пипетка (цвета)")
        self.settings_pipette_btn.clicked.connect(self._on_pipette_clicked)
        pipette_layout.addWidget(self.settings_pipette_btn)
        ocr_layout.addLayout(pipette_layout)

        # Invert checkbox
        invert_layout = QHBoxLayout()
        self.settings_invert_check = QCheckBox("Инвертировать (светлый текст на тёмном)")
        self.settings_invert_check.setChecked(self.settings.get("ocr.invert", False))
        self.settings_invert_check.stateChanged.connect(
            lambda v: self.settings.set("ocr.invert", v == Qt.CheckState.Checked.value)
        )
        invert_layout.addWidget(self.settings_invert_check)
        ocr_layout.addLayout(invert_layout)

        layout.addWidget(ocr_group)

        # ═══════════════════════════════════════════════════════════════
        # TTS settings (minimal — main controls are in Voice tab)
        # ═══════════════════════════════════════════════════════════════
        tts_group = QGroupBox(tr("settings_tts_group"))
        tts_layout = QVBoxLayout(tts_group)

        tts_link = QLabel(tr("settings_tts_link",
            default="Основные настройки голоса — на вкладке «Голоса»"))
        tts_link.setStyleSheet("color: #a6adc8; font-style: italic;")
        tts_layout.addWidget(tts_link)

        # Cache audio
        cache_layout = QHBoxLayout()
        self.settings_cache_check = QCheckBox(tr("chk_cache_audio"))
        self.settings_cache_check.setChecked(self.settings.get("tts.cache_audio", True))
        self.settings_cache_check.stateChanged.connect(
            lambda v: self.settings.set("tts.cache_audio", v == Qt.CheckState.Checked.value)
        )
        cache_layout.addWidget(self.settings_cache_check)
        tts_layout.addLayout(cache_layout)

        # SAPI voices info
        self.sapi_voices_label = QLabel(tr("sapi_voices_loading"))
        self.sapi_voices_label.setStyleSheet("color: #a6adc8;")
        self.sapi_voices_label.setWordWrap(True)
        tts_layout.addWidget(self.sapi_voices_label)

        layout.addWidget(tts_group)

        # ═══════════════════════════════════════════════════════════════
        # Translation before TTS settings
        # ═══════════════════════════════════════════════════════════════
        trans_tts_group = QGroupBox(tr("translation_tts_group"))
        trans_tts_layout = QVBoxLayout(trans_tts_group)

        # Enable translation before TTS
        trans_tts_enable_layout = QHBoxLayout()
        self.settings_translate_tts_check = QCheckBox(tr("chk_translate_tts"))
        self.settings_translate_tts_check.setChecked(self.settings.get("translation.enabled", False))
        self.settings_translate_tts_check.stateChanged.connect(
            lambda v: self.settings.set("translation.enabled", v == Qt.CheckState.Checked.value)
        )
        trans_tts_enable_layout.addWidget(self.settings_translate_tts_check)
        trans_tts_layout.addLayout(trans_tts_enable_layout)

        # Target language for translation
        trans_dst_layout = QHBoxLayout()
        trans_dst_layout.addWidget(QLabel(tr("lbl_translate_dst")))
        self.settings_translate_dst_combo = QComboBox()
        for code, name in sorted(LANGUAGES.items(), key=lambda x: x[1]):
            if code != "auto":
                self.settings_translate_dst_combo.addItem(f"{name} ({code})", code)
        dst_idx = self.settings_translate_dst_combo.findData(
            self.settings.get("translation.dst_lang", "ru")
        )
        if dst_idx >= 0:
            self.settings_translate_dst_combo.setCurrentIndex(dst_idx)
        self.settings_translate_dst_combo.currentTextChanged.connect(
            lambda: self.settings.set(
                "translation.dst_lang",
                self.settings_translate_dst_combo.currentData()
            )
        )
        trans_dst_layout.addWidget(self.settings_translate_dst_combo)
        trans_tts_layout.addLayout(trans_dst_layout)

        # Ключ API (не требуется для Zen, оставлен для совместимости)
        trans_apikey_layout = QHBoxLayout()
        trans_apikey_layout.addWidget(QLabel(tr("lbl_translate_apikey")))
        self.settings_translate_apikey_input = QLineEdit()
        self.settings_translate_apikey_input.setText(self.settings.get("translation.api_key", ""))
        self.settings_translate_apikey_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.settings_translate_apikey_input.setPlaceholderText("sk-or-...")
        self.settings_translate_apikey_input.textChanged.connect(
            lambda v: self.settings.set("translation.api_key", v)
        )
        trans_apikey_layout.addWidget(self.settings_translate_apikey_input)
        trans_tts_layout.addLayout(trans_apikey_layout)

        # Модель перевода: Zen (бесплатно, без ключа).
        # Модели OpenRouter/OrcaRouter удалены вместе с провайдерами
        # (404 / 403) 26.09.2026.
        trans_model_layout = QHBoxLayout()
        trans_model_layout.addWidget(QLabel(tr("lbl_translate_model")))
        self.settings_translate_model_combo = QComboBox()
        self.settings_translate_model_combo.setEditable(True)
        _default_models = [
            "space-bunny-free",
        ]
        self.settings_translate_model_combo.addItems(_default_models)
        saved_model = self.settings.get("translation.model", "")
        if saved_model:
            idx = self.settings_translate_model_combo.findText(saved_model)
            if idx >= 0:
                self.settings_translate_model_combo.setCurrentIndex(idx)
            else:
                self.settings_translate_model_combo.setEditText(saved_model)
        self.settings_translate_model_combo.currentTextChanged.connect(
            lambda v: self.settings.set("translation.model", v.strip())
        )
        trans_model_layout.addWidget(self.settings_translate_model_combo)
        trans_tts_layout.addLayout(trans_model_layout)

        # Note about fallback
        trans_note = QLabel(tr("lbl_translate_note"))
        trans_note.setStyleSheet("color: #a6adc8; font-size: 11px;")
        trans_note.setWordWrap(True)
        trans_tts_layout.addWidget(trans_note)

        # Check connection button + result
        trans_check_layout = QHBoxLayout()
        self.settings_translate_check_btn = QPushButton(tr("btn_translate_check"))
        self.settings_translate_check_btn.clicked.connect(self._on_check_translation)
        trans_check_layout.addWidget(self.settings_translate_check_btn)
        trans_tts_layout.addLayout(trans_check_layout)

        self.settings_translate_result = QLabel("")
        self.settings_translate_result.setStyleSheet("font-size: 11px;")
        self.settings_translate_result.setWordWrap(True)
        trans_tts_layout.addWidget(self.settings_translate_result)

        layout.addWidget(trans_tts_group)

        # ═══════════════════════════════════════════════════════════════
        # Overlay settings
        # ═══════════════════════════════════════════════════════════════
        overlay_group = QGroupBox(tr("settings_overlay_group"))
        overlay_layout = QVBoxLayout(overlay_group)

        # Enabled
        oe_layout = QHBoxLayout()
        self.settings_overlay_enabled_check = QCheckBox(tr("chk_overlay_enabled"))
        self.settings_overlay_enabled_check.setChecked(self.settings.get("overlay.enabled", True))
        self.settings_overlay_enabled_check.stateChanged.connect(
            lambda v: self.settings.set("overlay.enabled", v == Qt.CheckState.Checked.value)
        )
        oe_layout.addWidget(self.settings_overlay_enabled_check)
        overlay_layout.addLayout(oe_layout)

        # Show text
        ost_layout = QHBoxLayout()
        self.settings_overlay_text_check = QCheckBox(tr("chk_overlay_text"))
        self.settings_overlay_text_check.setChecked(self.settings.get("overlay.show_text", True))
        self.settings_overlay_text_check.stateChanged.connect(
            lambda v: self.settings.set("overlay.show_text", v == Qt.CheckState.Checked.value)
        )
        ost_layout.addWidget(self.settings_overlay_text_check)
        overlay_layout.addLayout(ost_layout)

        # Opacity
        op_layout = QHBoxLayout()
        op_layout.addWidget(QLabel(tr("lbl_opacity")))
        self.settings_opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.settings_opacity_slider.setRange(10, 100)
        self.settings_opacity_slider.setValue(int(self.settings.get("overlay.opacity", 0.8) * 100))
        op_layout.addWidget(self.settings_opacity_slider)
        self.settings_opacity_label = QLabel(f"{int(self.settings.get('overlay.opacity', 0.8) * 100)}%")
        op_layout.addWidget(self.settings_opacity_label)
        self.settings_opacity_slider.valueChanged.connect(
            lambda v: (
                self.settings.set("overlay.opacity", v / 100.0),
                self.region_overlay.set_opacity(frame_opacity=v),
                self.settings_opacity_label.setText(f"{v}%")
            )
        )
        overlay_layout.addLayout(op_layout)

        # Panel opacity
        pop_layout = QHBoxLayout()
        pop_layout.addWidget(QLabel(tr("lbl_panel_opacity")))
        self.settings_panel_opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.settings_panel_opacity_slider.setRange(10, 100)
        self.settings_panel_opacity_slider.setValue(int(self.settings.get("overlay.panel_opacity", 0.9) * 100))
        pop_layout.addWidget(self.settings_panel_opacity_slider)
        self.settings_panel_opacity_label = QLabel(f"{int(self.settings.get('overlay.panel_opacity', 0.9) * 100)}%")
        pop_layout.addWidget(self.settings_panel_opacity_label)
        self.settings_panel_opacity_slider.valueChanged.connect(
            lambda v: (
                self.settings.set("overlay.panel_opacity", v / 100.0),
                self.region_overlay.set_opacity(panel_opacity=v),
                self.settings_panel_opacity_label.setText(f"{v}%")
            )
        )
        overlay_layout.addLayout(pop_layout)

        # Font size
        fs_layout = QHBoxLayout()
        fs_layout.addWidget(QLabel(tr("lbl_overlay_font_size")))
        self.settings_overlay_font_spin = QSpinBox()
        self.settings_overlay_font_spin.setRange(8, 48)
        self.settings_overlay_font_spin.setValue(self.settings.get("overlay.font_size", 14))
        self.settings_overlay_font_spin.valueChanged.connect(
            lambda v: self.settings.set("overlay.font_size", v)
        )
        fs_layout.addWidget(self.settings_overlay_font_spin)
        overlay_layout.addLayout(fs_layout)

        # Font color
        fc_layout = QHBoxLayout()
        fc_layout.addWidget(QLabel(tr("lbl_font_color")))
        self.settings_font_color_edit = QLineEdit(self.settings.get("overlay.font_color", "#FFFFFF"))
        self.settings_font_color_edit.setMaximumWidth(100)
        self.settings_font_color_edit.textChanged.connect(
            lambda v: self.settings.set("overlay.font_color", v)
        )
        fc_layout.addWidget(self.settings_font_color_edit)
        overlay_layout.addLayout(fc_layout)

        # Background color
        bc_layout = QHBoxLayout()
        bc_layout.addWidget(QLabel(tr("lbl_bg_color")))
        self.settings_bg_color_edit = QLineEdit(self.settings.get("overlay.background_color", "#000000AA"))
        self.settings_bg_color_edit.setMaximumWidth(120)
        self.settings_bg_color_edit.textChanged.connect(
            lambda v: self.settings.set("overlay.background_color", v)
        )
        bc_layout.addWidget(self.settings_bg_color_edit)
        overlay_layout.addLayout(bc_layout)

        # Position
        pos_layout = QHBoxLayout()
        pos_layout.addWidget(QLabel(tr("lbl_position")))
        self.settings_position_combo = QComboBox()
        self.settings_position_combo.addItems(["top-left", "top-right", "bottom-left", "bottom-right", "center"])
        self.settings_position_combo.setCurrentText(self.settings.get("overlay.position", "top-right"))
        self.settings_position_combo.currentTextChanged.connect(
            lambda v: self.settings.set("overlay.position", v)
        )
        pos_layout.addWidget(self.settings_position_combo)
        overlay_layout.addLayout(pos_layout)

        layout.addWidget(overlay_group)

        # ═══════════════════════════════════════════════════════════════
        # Web TTS server
        # ═══════════════════════════════════════════════════════════════
        web_tts_group = QGroupBox(tr("settings_web_tts_group"))
        web_tts_layout = QVBoxLayout(web_tts_group)

        # Enabled
        we_layout = QHBoxLayout()
        self.settings_web_tts_check = QCheckBox(tr("chk_web_tts"))
        self.settings_web_tts_check.setChecked(self.settings.get("web_tts.enabled", True))
        self.settings_web_tts_check.stateChanged.connect(self._on_web_tts_toggled)
        we_layout.addWidget(self.settings_web_tts_check)
        self.web_tts_url_label = QLabel("")
        self.web_tts_url_label.setStyleSheet("color: #89b4fa;")
        we_layout.addWidget(self.web_tts_url_label)
        we_layout.addStretch()
        web_tts_layout.addLayout(we_layout)
        self._update_web_tts_url_label()

        # Port
        wp_layout = QHBoxLayout()
        wp_layout.addWidget(QLabel(tr("lbl_web_tts_port")))
        self.settings_web_port_spin = QSpinBox()
        self.settings_web_port_spin.setRange(1024, 65535)
        self.settings_web_port_spin.setValue(self.settings.get("web_tts.port", 8080))
        self.settings_web_port_spin.valueChanged.connect(
            lambda v: self.settings.set("web_tts.port", v)
        )
        wp_layout.addWidget(self.settings_web_port_spin)
        web_tts_layout.addLayout(wp_layout)

        layout.addWidget(web_tts_group)

        # ═══════════════════════════════════════════════════════════════
        # General settings
        # ═══════════════════════════════════════════════════════════════
        general_group = QGroupBox(tr("settings_general_group"))
        general_layout = QVBoxLayout(general_group)

        # Start minimized
        sm_layout = QHBoxLayout()
        self.settings_minimized_check = QCheckBox(tr("chk_start_minimized"))
        self.settings_minimized_check.setChecked(self.settings.get("general.start_minimized", False))
        self.settings_minimized_check.stateChanged.connect(
            lambda v: self.settings.set("general.start_minimized", v == Qt.CheckState.Checked.value)
        )
        sm_layout.addWidget(self.settings_minimized_check)
        general_layout.addLayout(sm_layout)

        # Log enabled
        le_layout = QHBoxLayout()
        self.settings_log_check = QCheckBox(tr("chk_log_enabled"))
        self.settings_log_check.setChecked(self.settings.get("general.log_enabled", True))
        self.settings_log_check.stateChanged.connect(
            lambda v: self.settings.set("general.log_enabled", v == Qt.CheckState.Checked.value)
        )
        le_layout.addWidget(self.settings_log_check)
        general_layout.addLayout(le_layout)

        # Log level
        ll_layout = QHBoxLayout()
        ll_layout.addWidget(QLabel(tr("lbl_log_level")))
        self.settings_log_level_combo = QComboBox()
        self.settings_log_level_combo.addItems(["DEBUG", "INFO", "WARNING", "ERROR"])
        self.settings_log_level_combo.setCurrentText(self.settings.get("general.log_level", "INFO"))
        self.settings_log_level_combo.currentTextChanged.connect(
            lambda v: self.settings.set("general.log_level", v)
        )
        ll_layout.addWidget(self.settings_log_level_combo)
        general_layout.addLayout(ll_layout)

        layout.addWidget(general_group)

        # ═══════════════════════════════════════════════════════════════
        # Interface settings
        # ═══════════════════════════════════════════════════════════════
        ui_group = QGroupBox(tr("scale_group"))
        ui_layout = QVBoxLayout(ui_group)

        # Language
        ui_lang_row = QHBoxLayout()
        ui_lang_row.addWidget(QLabel("Language / Язык:"))
        self.settings_ui_lang_combo = QComboBox()
        self.settings_ui_lang_combo.addItem("English", "en")
        self.settings_ui_lang_combo.addItem("Русский", "ru")
        cur_ui_lang = self.settings.get("ui.language", "en")
        self.settings_ui_lang_combo.setCurrentIndex(0 if cur_ui_lang == "en" else 1)
        self.settings_ui_lang_combo.currentIndexChanged.connect(self._on_ui_language_changed)
        ui_lang_row.addWidget(self.settings_ui_lang_combo)
        ui_layout.addLayout(ui_lang_row)

        # Font scale
        scale_layout = QHBoxLayout()
        scale_layout.addWidget(QLabel(tr("lbl_font_size")))
        self.scale_slider = QSlider(Qt.Orientation.Horizontal)
        self.scale_slider.setRange(8, 24)
        self.scale_slider.setValue(self.settings.get("ui.scale", 12))
        scale_layout.addWidget(self.scale_slider)
        self.scale_label = QLabel(f"{self.settings.get('ui.scale', 12)}px")
        scale_layout.addWidget(self.scale_label)
        self.scale_slider.valueChanged.connect(self._on_scale_changed)
        ui_layout.addLayout(scale_layout)

        layout.addWidget(ui_group)

        # ═══════════════════════════════════════════════════════════════
        # Hotkeys
        # ═══════════════════════════════════════════════════════════════
        hotkeys_group = QGroupBox(tr("hotkeys_group"))
        hotkeys_layout = QVBoxLayout(hotkeys_group)
        hotkeys_info = QLabel(tr("hotkeys_info"))
        hotkeys_info.setStyleSheet("color: #a6adc8;")
        hotkeys_layout.addWidget(hotkeys_info)
        layout.addWidget(hotkeys_group)

        # ═══════════════════════════════════════════════════════════════
        # About
        # ═══════════════════════════════════════════════════════════════
        about_group = QGroupBox(tr("about_group"))
        about_layout = QVBoxLayout(about_group)
        about_label = QLabel(tr("about_text"))
        about_label.setStyleSheet("color: #a6adc8;")
        about_layout.addWidget(about_label)
        layout.addWidget(about_group)

        layout.addStretch()
        return scroll

    def _on_ui_language_changed(self, index):
        lang = self.settings_ui_lang_combo.itemData(index)
        self.settings.set("ui.language", lang)
        set_language(lang)
        QMessageBox.information(self, tr("dlg_info"), tr("about_group"))
        self.statusBar().showMessage("Language will fully apply after restart / Язык применится после перезапуска")

    # ==================== TRAY ====================

    def _setup_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray_menu = QMenu()
        show_action = QAction(tr("tray_show"), self)
        show_action.triggered.connect(self._restore_from_tray)
        self.tray_menu.addAction(show_action)
        self.tray_menu.addSeparator()
        scan_action = QAction(tr("tray_scan_all"), self)
        scan_action.triggered.connect(self._scan_all_regions)
        self.tray_menu.addAction(scan_action)
        live_action = QAction(tr("tray_live"), self)
        live_action.triggered.connect(self._toggle_live_mode_btn)
        live_action.triggered.connect(self._toggle_live_window)
        self.tray_menu.addAction(live_action)
        self.tray_menu.addSeparator()
        minimize_action = QAction(tr("tray_minimize"), self)
        minimize_action.triggered.connect(self._minimize_to_tray)
        self.tray_menu.addAction(minimize_action)
        self.tray_menu.addSeparator()
        quit_action = QAction(tr("tray_quit"), self)
        quit_action.triggered.connect(self._save_and_quit)
        self.tray_menu.addAction(quit_action)
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(self._create_tray_icon())
        self.tray_icon.setToolTip(tr("app_title"))
        self.tray_icon.setContextMenu(self.tray_menu)
        self.tray_icon.activated.connect(self._on_tray_activated)
        self.tray_icon.show()

    def _create_tray_icon(self):
        from PyQt6.QtGui import QPixmap, QPainter, QColor, QFont
        pixmap = QPixmap(64, 64)
        pixmap.fill(QColor("#1e1e2e"))
        painter = QPainter(pixmap)
        painter.setPen(QColor("#cdd6f4"))
        painter.setFont(QFont("Arial", 22, QFont.Weight.Bold))
        painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "OCR")
        painter.end()
        return QIcon(pixmap)

    def _restore_from_tray(self):
        self.show()
        self.raise_()
        self.activateWindow()

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._restore_from_tray()

    # ==================== SHORTCUTS ====================

    def _setup_shortcuts(self):
        shortcuts = {
            "F6": self._scan_full_screen_only,
            "F8": self._toggle_live_mode_btn,
            "F9": self._scan_all_regions,
            "F10": self._scan_selected_region,
            "F11": self.overlay.toggle_visibility,
            "F12": self._speak_result,
        }
        for key, func in shortcuts.items():
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.activated.connect(func)

        # 0 — toggle edit mode (единственный хоткей для этой функции)
        zero_shortcut = QShortcut(QKeySequence("0"), self)
        zero_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        zero_shortcut.activated.connect(self._toggle_edit_mode)

        self._init_global_hotkeys()

    def _toggle_live_mode_btn(self):
        self.live_window.live_enable_check.setChecked(not self.live_scanner.running)

    def _toggle_live_window(self):
        """Показать/скрыть окно Live Mode."""
        if self.live_window.isVisible():
            self.live_window.hide()
        else:
            self.live_window.show()
            self.live_window.raise_()
            self.live_window.activateWindow()

    def _open_tts_engine_dialog(self, engine: str = ""):
        """Открыть компактное окно выбора TTS-движка. engine: edge/rhvoice/silero/sapi"""
        if not hasattr(self, '_tts_engine_dialog'):
            self._tts_engine_dialog = _TTSEngineDialog(self)
        if engine:
            radio_map = {"edge": self._tts_engine_dialog._radio_edge,
                         "rhvoice": self._tts_engine_dialog._radio_rhvoice,
                         "silero": self._tts_engine_dialog._radio_silero,
                         "sapi": self._tts_engine_dialog._radio_sapi}
            radio = radio_map.get(engine)
            if radio:
                radio.setChecked(True)
                self._tts_engine_dialog._refresh_voices()
        self._tts_engine_dialog.show()
        self._tts_engine_dialog.raise_()
        self._tts_engine_dialog.activateWindow()

    def _toggle_edit_mode(self):
        enabled = not self.region_overlay._edit_mode
        self.edit_mode_btn.setChecked(enabled)
        self.region_overlay._edit_mode = enabled
        self.region_overlay.set_edit_mode(enabled)
        if enabled:
            self.edit_mode_btn.setText(tr("btn_edit_mode_on"))
            self.edit_mode_btn.setStyleSheet("background-color: #4caf50; color: white;")
            self.statusBar().showMessage(tr("status_edit_on"))
        else:
            self.edit_mode_btn.setText(tr("btn_edit_mode_off"))
            self.edit_mode_btn.setStyleSheet("")
            self.statusBar().showMessage(tr("status_edit_off"))

    # ==================== LOGS ====================

    def _refresh_logs(self):
        try:
            with open("screen_ocr.log", "r", encoding="utf-8") as f:
                f.seek(0, 2)
                size = f.tell()
                if size > 5000:
                    f.seek(max(0, size - 5000))
                    f.readline()
                content = f.read()
                self.log_text.setPlainText(content)
                scrollbar = self.log_text.verticalScrollBar()
                scrollbar.setValue(scrollbar.maximum())
        except FileNotFoundError:
            self.log_text.setPlainText(tr("log_not_found"))
        except Exception as e:
            self.log_text.setPlainText(tr("log_read_error", err=e))

    def _toggle_auto_refresh(self):
        if self.auto_refresh_check.isChecked():
            self._log_timer.start(5000)
        else:
            self._log_timer.stop()

    def _clear_logs(self):
        try:
            open("screen_ocr.log", "w", encoding="utf-8").close()
            self.log_text.setPlainText(tr("log_cleared"))
        except Exception as e:
            self.log_text.setPlainText(tr("log_clear_error", err=e))

    # ==================== SETTINGS ====================

    def _load_settings(self):
        self.gpu_check.setChecked(self.ocr.use_gpu if self.ocr else False)
        # Восстановление настроек TTS
        saved_rate = self.settings.get("tts.rate", 0)
        saved_volume = self.settings.get("tts.volume", 1.0)
        # Восстановить мужской RU голос (совместимость со старым ключом)
        saved_male = self.settings.get("tts.male_ru_voice",
                        self.settings.get("tts.male_voice", "ru-RU-DmitryNeural"))
        if hasattr(self, 'voice_combo'):
            idx = self.voice_combo.findText(saved_male)
            if idx >= 0:
                self.voice_combo.setCurrentIndex(idx)
        # Восстановить мужской EN голос
        saved_male_en = self.settings.get("tts.male_en_voice", "en-US-GuyNeural")
        if hasattr(self, 'male_en_voice_combo'):
            idx = self.male_en_voice_combo.findText(saved_male_en)
            if idx >= 0:
                self.male_en_voice_combo.setCurrentIndex(idx)
        # Восстановить скорость (per-role)
        if hasattr(self, '_role_sliders'):
            for role_key in ("male", "female", "narrator"):
                if role_key in self._role_sliders:
                    rate_val = self.settings.get(f"tts.{role_key}_rate", 0)
                    pitch_val = self.settings.get(f"tts.{role_key}_pitch", 0)
                    self._role_sliders[role_key]["speed"][0].setValue(rate_val)
                    self._role_sliders[role_key]["pitch"][0].setValue(pitch_val)
        # Восстановить громкость
        if hasattr(self, 'vol_slider'):
            self.vol_slider.setValue(int(saved_volume * 100))
        # Восстановить женский RU голос
        saved_female = self.settings.get("tts.female_ru_voice",
                        self.settings.get("tts.female_voice", "ru-RU-SvetlanaNeural"))
        if hasattr(self, 'female_voice_combo'):
            idx = self.female_voice_combo.findText(saved_female)
            if idx >= 0:
                self.female_voice_combo.setCurrentIndex(idx)
        # Восстановить женский EN голос
        saved_female_en = self.settings.get("tts.female_en_voice", "en-US-JennyNeural")
        if hasattr(self, 'female_en_voice_combo'):
            idx = self.female_en_voice_combo.findText(saved_female_en)
            if idx >= 0:
                self.female_en_voice_combo.setCurrentIndex(idx)
        # Восстановить рассказчика RU
        saved_narrator = self.settings.get("tts.narrator_ru_voice",
                         self.settings.get("tts.narrator_voice", "ru-RU-DmitryNeural"))
        if hasattr(self, 'narrator_combo'):
            idx = self.narrator_combo.findText(saved_narrator)
            if idx >= 0:
                self.narrator_combo.setCurrentIndex(idx)
        # Восстановить рассказчика EN
        saved_narrator_en = self.settings.get("tts.narrator_en_voice", "en-US-GuyMultilingualNeural")
        if hasattr(self, 'narrator_en_voice_combo'):
            idx = self.narrator_en_voice_combo.findText(saved_narrator_en)
            if idx >= 0:
                self.narrator_en_voice_combo.setCurrentIndex(idx)

    def _save_settings(self):
        self.settings.set("ocr.use_gpu", self.gpu_check.isChecked())
        # Сохранение голосов
        if hasattr(self, 'voice_combo'):
            self.settings.set("tts.male_ru_voice", self.voice_combo.currentText())
        if hasattr(self, 'male_en_voice_combo'):
            self.settings.set("tts.male_en_voice", self.male_en_voice_combo.currentText())
        if hasattr(self, 'female_voice_combo'):
            self.settings.set("tts.female_ru_voice", self.female_voice_combo.currentText())
        if hasattr(self, 'female_en_voice_combo'):
            self.settings.set("tts.female_en_voice", self.female_en_voice_combo.currentText())
        if hasattr(self, 'narrator_combo'):
            self.settings.set("tts.narrator_ru_voice", self.narrator_combo.currentText())
        if hasattr(self, 'narrator_en_voice_combo'):
            self.settings.set("tts.narrator_en_voice", self.narrator_en_voice_combo.currentText())
        # Save per-role speed/pitch
        if hasattr(self, '_role_sliders'):
            for role_key in ("male", "female", "narrator"):
                if role_key in self._role_sliders:
                    self.settings.set(f"tts.{role_key}_rate",
                                      self._role_sliders[role_key]["speed"][0].value())
                    self.settings.set(f"tts.{role_key}_pitch",
                                       self._role_sliders[role_key]["pitch"][0].value())
        if hasattr(self, 'vol_slider'):
            self.settings.set("tts.volume", self.vol_slider.value() / 100.0)
        try:
            self.settings._save_config(self.settings.config)
        except Exception as e:
            logger.error(f"Error saving settings: {e}")

    def _save_and_quit(self):
        """Сохранить настройки и выйти (для tray menu)."""
        self._save_settings()
        self._quit_app()

    def _quit_app(self):
        logger.info("=== Shutdown ===")
        # Мгновенно остановить ВСЮ озвучку
        try:
            self._stop_all_speech()
        except Exception:
            pass
        if hasattr(self, '_log_timer'):
            self._log_timer.stop()
        try:
            self._stop_live_mode()
        except Exception:
            pass
        try:
            self._cleanup_global_hotkeys()
        except Exception:
            pass
        # Финальный force_kill всего что живое
        try:
            self.tts.force_stop()
        except Exception:
            pass
        self.region_overlay.hide()
        if self.tray_icon:
            self.tray_icon.hide()
        QApplication.quit()

    def closeEvent(self, event):
        # Unregister web server log callback
        try:
            from web_tts_server import _log_callbacks, _log_lock
            if hasattr(self, '_ws_log_cb'):
                with _log_lock:
                    try:
                        _log_callbacks.remove(self._ws_log_cb)
                    except ValueError:
                        pass
        except Exception:
            pass
        self._save_settings()
        self._quit_app()
        event.accept()

    # ==================== GLOBAL HOTKEYS ====================

    def _init_global_hotkeys(self):
        try:
            from pynput import keyboard as pynput_keyboard

            def _safe_wrap(callback):
                def wrapper():
                    try:
                        callback()
                    except Exception as e:
                        logger.error(f"[HOTKEY] Callback {getattr(callback, '__name__', callback)} failed: {e}")
                return wrapper

            self._global_hotkeys = {
                '<f5>': _safe_wrap(self._capture_focused_window),
                '<f6>': _safe_wrap(self._scan_full_screen_only),
                '<f7>': _safe_wrap(self._stop_all_speech),
                '<f8>': _safe_wrap(self._toggle_live_mode_btn),
                '<f9>': _safe_wrap(self._scan_all_regions),
                '<f10>': _safe_wrap(self._scan_selected_region),
                '<f11>': _safe_wrap(self.overlay.toggle_visibility),
                '<f12>': _safe_wrap(self._speak_result),
                # Режимы озвучки 1-4 (повторное нажатие выключает)
                "'1'": _safe_wrap(lambda: self._toggle_speech_mode(1)),
                "'2'": _safe_wrap(lambda: self._toggle_speech_mode(2)),
                "'3'": _safe_wrap(lambda: self._toggle_speech_mode(3)),
                "'4'": _safe_wrap(lambda: self._toggle_speech_mode(4)),
            }

            def on_press(key):
                try:
                    key_str = str(key).lower()
                    if key_str in self._global_hotkeys:
                        callback = self._global_hotkeys[key_str]
                        logger.debug(f"[HOTKEY] {key_str} → {callback.__name__ if hasattr(callback, '__name__') else callback}")
                        QTimer.singleShot(0, callback)
                except Exception as e:
                    logger.error(f"[HOTKEY] Error processing {key}: {e}")

            self._hotkey_listener = pynput_keyboard.Listener(on_press=on_press)
            self._hotkey_listener.start()
            logger.info("Global hotkeys (F6-F12) registered via pynput")
        except ImportError:
            logger.warning("pynput not installed. Global hotkeys disabled. Install: pip install pynput")
            logger.info("Hotkeys still work when the window is focused")
        except Exception as e:
            logger.error(f"Error initializing global hotkeys: {e}")

    def _cleanup_global_hotkeys(self):
        try:
            if hasattr(self, '_hotkey_listener'):
                self._hotkey_listener.stop()
                self._hotkey_listener = None
        except Exception as e:
            logger.error(f"Error cleaning up hotkeys: {e}")

    def _on_pipette_clicked(self):
        """Запускает режим пипетки — захват скриншота и ожидание клика."""
        if not self.ocr or not hasattr(self.ocr, 'region') or not self.ocr.region:
            QMessageBox.warning(self, "Ошибка", "Сначала выберите область захвата")
            return
        # Делаем скриншот области
        img = self.ocr.capture_region()
        if img is None:
            QMessageBox.warning(self, "Ошибка", "Не удалось захватить область")
            return
        self._pipette_img = img
        self._pipette_mode = True
        self.statusBar().showMessage("Кликните по тексту в окне предпросмотра...")
        QApplication.setOverrideCursor(Qt.CursorShape.CrossCursor)
        self._show_pipette_preview(img)

    def _show_pipette_preview(self, img):
        """Показывает окно предпросмотра для выбора цвета."""
        from PyQt6.QtCore import Qt, QPoint
        from PyQt6.QtGui import QPixmap, QPainter, QColor, QPen

        self._pipette_dialog = QDialog(self)
        self._pipette_dialog.setWindowTitle("Пипетка — кликните по тексту")
        self._pipette_dialog.setModal(True)

        layout = QVBoxLayout(self._pipette_dialog)

        # Инструкция
        info = QLabel("Кликните на текст ЛЕВОЙ кнопкой мыши\n"
                       "Кликните на фон ПРАВОЙ кнопкой мыши\n"
                       "ESC — отмена")
        info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(info)

        # Изображение
        img_label = QLabel()
        img_label.setFixedSize(img.width, img.height)
        img_label.setScaledContents(True)

        # Конвертируем PIL в QPixmap
        from PyQt6.QtGui import QImage
        qimage = QImage(img.tobytes(), img.width, img.height, img.width * 3, QImage.Format.Format_RGB888)
        pixmap = QPixmap.fromImage(qimage)
        img_label.setPixmap(pixmap)
        layout.addWidget(img_label)

        # Цвета
        color_layout = QHBoxLayout()
        self._bg_color_label = QLabel("Фон: не выбран")
        self._bg_color_label.setStyleSheet("padding: 5px; border: 1px solid gray;")
        self._text_color_label = QLabel("Текст: не выбран")
        self._text_color_label.setStyleSheet("padding: 5px; border: 1px solid gray;")
        color_layout.addWidget(self._bg_color_label)
        color_layout.addWidget(self._text_color_label)
        layout.addLayout(color_layout)

        # Кнопки
        btn_layout = QHBoxLayout()
        apply_btn = QPushButton("Применить")
        cancel_btn = QPushButton("Отмена")
        btn_layout.addWidget(apply_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)

        self._pipette_bg_color = None
        self._pipette_text_color = None

        def on_image_click(event):
            pos = event.pos()
            x, y = pos.x(), pos.y()
            scale_x = img.width / img_label.width()
            scale_y = img.height / img_label.height()
            orig_x = int(x * scale_x)
            orig_y = int(y * scale_y)
            orig_x = max(0, min(orig_x, img.width - 1))
            orig_y = max(0, min(orig_y, img.height - 1))
            color = img.getpixel((orig_x, orig_y))
            return orig_x, orig_y, color

        def on_left_click(event):
            _, _, color = on_image_click(event)
            self._pipette_text_color = color
            self._text_color_label.setText(f"Текст: RGB{color}")
            self._text_color_label.setStyleSheet(
                f"padding: 5px; border: 1px solid gray; background: rgb({color[0]},{color[1]},{color[2]}); "
                f"color: {'white' if sum(color) < 384 else 'black'};")

        def on_right_click(event):
            _, _, color = on_image_click(event)
            self._pipette_bg_color = color
            self._bg_color_label.setText(f"Фон: RGB{color}")
            self._bg_color_label.setStyleSheet(
                f"padding: 5px; border: 1px solid gray; background: rgb({color[0]},{color[1]},{color[2]}); "
                f"color: {'white' if sum(color) < 384 else 'black'};")

        def on_apply():
            if self._pipette_bg_color is None or self._pipette_text_color is None:
                QMessageBox.warning(self._pipette_dialog, "Ошибка", "Выберите и фон, и текст")
                return
            # Сохраняем в настройки
            self.settings.set("ocr.bg_color", list(self._pipette_bg_color))
            self.settings.set("ocr.text_color", list(self._pipette_text_color))
            # Вычисляем параметры предобработки
            bg_b = sum(self._pipette_bg_color) / 3
            txt_b = sum(self._pipette_text_color) / 3
            if bg_b > txt_b:
                self.settings.set("ocr.invert", True)
            else:
                self.settings.set("ocr.invert", False)
            diff = abs(bg_b - txt_b)
            contrast = min(3.0, max(1.0, 1.0 + (64 - diff) / 32))
            self.settings.set("ocr.contrast", round(contrast, 1))
            self.statusBar().showMessage(
                f"Цвета заданы: фон=RGB{self._pipette_bg_color}, текст=RGB{self._pipette_text_color}, "
                f"контраст={contrast:.1f}, инверсия={'да' if bg_b > txt_b else 'нет'}")
            QApplication.restoreOverrideCursor()
            self._pipette_dialog.accept()

        def on_cancel():
            QApplication.restoreOverrideCursor()
            self._pipette_dialog.reject()

        img_label.mousePressEvent = lambda e: (
            on_left_click(e) if e.button() == Qt.MouseButton.LeftButton
            else on_right_click(e) if e.button() == Qt.MouseButton.RightButton
            else None
        )

        apply_btn.clicked.connect(on_apply)
        cancel_btn.clicked.connect(on_cancel)

        self._pipette_dialog.finished.connect(
            lambda: QApplication.restoreOverrideCursor()
        )

        # Масштабируем если изображение слишком большое
        max_size = 800
        if img.width > max_size or img.height > max_size:
            scale = min(max_size / img.width, max_size / img.height)
            img_label.setFixedSize(int(img.width * scale), int(img.height * scale))

        self._pipette_dialog.exec()

    def _stop_pipette_mode(self):
        """Останавливает режим пипетки."""
        self._pipette_mode = False
        QApplication.restoreOverrideCursor()


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler("screen_ocr.log", encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
