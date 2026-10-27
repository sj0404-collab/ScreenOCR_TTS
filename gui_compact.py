"""
Screen OCR + TTS — Compact single-page GUI
All features on one page with Unicode icons.
"""
import os
import sys
import json
import logging
import threading
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QGroupBox, QPushButton, QLabel, QComboBox, QSlider, QCheckBox,
    QTextEdit, QListWidget, QLineEdit, QSpinBox, QScrollArea,
    QSplitter, QFrame, QSystemTrayIcon, QMenu, QMessageBox,
    QApplication, QSizePolicy, QTabWidget
)
from PyQt6.QtCore import Qt, QTimer, QSize, QUrl, QRect, QPoint, QEvent, pyqtSignal
from PyQt6.QtGui import QFont, QIcon, QImage, QPalette, QColor, QDesktopServices

# NOTE: must be imported BEFORE QApplication exists, otherwise Qt raises
# "must be imported or AA_ShareOpenGLContexts must be set".
try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    _WEBENGINE_AVAILABLE = True
except Exception:
    QWebEngineView = None
    _WEBENGINE_AVAILABLE = False

logger = logging.getLogger(__name__)

# Unicode icons
ICON = {
    "scan": "🔍",
    "live_on": "▶️",
    "live_off": "⏹️",
    "tts": "🔊",
    "tts_stop": "🔇",
    "stt": "🎤",
    "stt_stop": "🔴",
    "translate": "🌐",
    "copy": "📋",
    "clear": "🗑️",
    "save": "💾",
    "add": "➕",
    "edit": "✏️",
    "delete": "❌",
    "settings": "⚙️",
    "region": "📐",
    "process": "🎯",
    "voice": "🗣️",
    "speed": "⚡",
    "pitch": "🎵",
    "overlay": "👁️",
    "minimize": "➖",
    "quit": "🚪",
    "eng": "🔤",
    "ru": "🇷🇺",
    "auto": "🔄",
    "refresh": "🔃",
    "play": "▶️",
    "stop": "⏹️",
    "pause": "⏸️",
    "ok": "✅",
    "error": "❌",
    "warning": "⚠️",
}


class CompactWindow(QMainWindow):
    """Compact single-page window with all features."""
    gui_invoke = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self.gui_invoke.connect(self._on_gui_invoke)
        self.setWindowTitle("Screen OCR + TTS")
        self.setMinimumSize(700, 600)
        self.resize(800, 700)

        # Load settings
        from settings import Settings
        self.settings = Settings()

        # Central widget
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(4, 4, 4, 4)
        main_layout.setSpacing(4)

        # ═══════════════════════════════════════════════
        # TOP BAR: Language + Quick Actions
        # ═══════════════════════════════════════════════
        top = QFrame()
        top.setStyleSheet("QFrame { background: #2d2d30; border-radius: 4px; }")
        top_layout = QHBoxLayout(top)
        top_layout.setContentsMargins(6, 3, 6, 3)
        top_layout.setSpacing(4)

        top_layout.addWidget(QLabel("From:"))
        self.src_lang = QComboBox()
        self.src_lang.addItems(["Auto", "EN", "RU", "JP", "ZH"])
        self.src_lang.setFixedWidth(60)
        top_layout.addWidget(self.src_lang)

        top_layout.addWidget(QLabel("→"))

        self.dst_lang = QComboBox()
        self.dst_lang.addItems(["RU", "EN", "JP", "ZH", "Auto"])
        self.dst_lang.setFixedWidth(60)
        top_layout.addWidget(self.dst_lang)

        top_layout.addSpacing(8)

        # Quick action buttons
        self.btn_scan = QPushButton(f"{ICON['scan']} Scan")
        self.btn_scan.setFixedHeight(28)
        self.btn_scan.clicked.connect(self._on_scan)
        top_layout.addWidget(self.btn_scan)

        # Auto Speak: озвучивать результат сразу после Scan / Live
        self.auto_speak = QCheckBox("🔊 Auto Speak")
        self.auto_speak.setChecked(self.settings.get("ocr.auto_speak", True))
        self.auto_speak.setToolTip("Автоматически озвучивать распознанный текст после сканирования")
        self.auto_speak.stateChanged.connect(lambda s: self.settings.set("ocr.auto_speak", bool(s)))
        top_layout.addWidget(self.auto_speak)

        self.btn_live = QPushButton(f"{ICON['live_on']} Live")
        self.btn_live.setFixedHeight(28)
        self.btn_live.setCheckable(True)
        self.btn_live.clicked.connect(self._on_live_toggle)
        top_layout.addWidget(self.btn_live)

        # Рамки вокруг распознанного текста поверх экрана (как в читалке
        # KOMA/yomikai). Включено по умолчанию; рамки гаснут сами через
        # несколько секунд без новых прогонов.
        self.btn_boxes = QPushButton("▢ Рамки")
        self.btn_boxes.setFixedHeight(28)
        self.btn_boxes.setCheckable(True)
        self.btn_boxes.setChecked(self.settings.get("ocr.show_boxes", True))
        self.btn_boxes.setToolTip(
            "Рамки вокруг распознанного текста поверх экрана "
            "(как в читалке KOMA / yomikai)")
        self.btn_boxes.clicked.connect(self._toggle_boxes_overlay)
        top_layout.addWidget(self.btn_boxes)

        # ── Auto-Read: реальное время — скан + сразу озвучка ──
        self._auto_read_active = False
        self._auto_read_last_text = ""
        # Debounce: текст должен повториться 2 скана подряд (печать диалога)
        self._auto_read_pending = ""
        self._auto_read_pending_count = 0
        # Защита от накопления OCR-потоков: скан занимает 1.5-4 c, а таймер
        # может бить каждые 500 мс. Без этих полей запускалось бы 3-8
        # параллельных распознаваний — интерфейс «зависал», CPU грузился.
        self._auto_read_busy = False
        self._auto_read_last = 0.0
        self._auto_read_gap = 1.0      # минимальная пауза между сканами, c
        self._auto_read_timer = QTimer()
        self._auto_read_timer.timeout.connect(self._auto_read_tick)

        self.btn_auto_read = QPushButton(f"▶ Speak")
        self.btn_auto_read.setFixedHeight(32)
        self.btn_auto_read.setFixedWidth(110)
        self.btn_auto_read.setCheckable(True)
        self.btn_auto_read.setToolTip("Auto-Read: сканирует и озвучивает (Ctrl+Shift+F7)")
        self.btn_auto_read.setStyleSheet(
            "QPushButton { background: #1a3a1a; color: #777; border: 2px solid #333; border-radius: 5px; font-weight: bold; }"
            "QPushButton:hover { background: #1e4a1e; }"
            "QPushButton:checked { background: #0d5a0d; color: #4cff4c; border-color: #4cff4c; }"
        )
        self.btn_auto_read.clicked.connect(self._toggle_auto_read)
        top_layout.addWidget(self.btn_auto_read)

        self.auto_read_led = QLabel("●")
        self.auto_read_led.setFixedWidth(16)
        self.auto_read_led.setStyleSheet("color: #333; font-size: 14px;")
        self.auto_read_led.setToolTip("Auto-Read: OFF")
        top_layout.addWidget(self.auto_read_led)

        self.btn_preview = QPushButton(f"🖥 Preview")
        self.btn_preview.setFixedHeight(28)
        self.btn_preview.setToolTip("Предпросмотр захваченной области игры")
        self.btn_preview.clicked.connect(self._toggle_game_preview)
        top_layout.addWidget(self.btn_preview)

        self.btn_tts = QPushButton(f"{ICON['tts']} TTS")
        self.btn_tts.setFixedHeight(28)
        self.btn_tts.clicked.connect(self._on_speak)
        top_layout.addWidget(self.btn_tts)

        self.btn_stop = QPushButton(f"{ICON['tts_stop']} Stop")
        self.btn_stop.setFixedHeight(28)
        self.btn_stop.clicked.connect(self._on_stop_speak)
        top_layout.addWidget(self.btn_stop)

        # TTS/STT status icons (top bar indicators)
        self.tts_icon = QLabel(f"{ICON['tts']}")
        self.tts_icon.setToolTip("TTS: idle")
        self.tts_icon.setStyleSheet("color: #555555; font-size: 18px;")
        top_layout.addWidget(self.tts_icon)

        self.stt_icon = QLabel(f"{ICON['stt']}")
        self.stt_icon.setToolTip("STT: off")
        self.stt_icon.setStyleSheet("color: #555555; font-size: 18px;")
        top_layout.addWidget(self.stt_icon)

        top_layout.addStretch()
        main_layout.addWidget(top)

        # ═══════════════════════════════════════════════
        # SPLITTER: Left (controls) | Right (text)
        # ═══════════════════════════════════════════════
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(4)

        # ── LEFT PANEL: Controls ──
        # Раньше здесь было 7 мелких групп (Process/Region/Scan/Voice/
        # OCR/Overlay). При 1366x768 они не влезали по высоте, нижние
        # уезжали под экран, а из-за сжатия панели splitter'ом подписи
        # обрезались ("Elena (RH", "Cop", "Clea", "Occal").
        # Теперь — 4 компактных блока, ширина защищена минимумом.
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Минимальная ширина: иначе splitter сжимает панель и текст режется.
        left_scroll.setMinimumWidth(340)
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(4, 4, 4, 4)
        left_layout.setSpacing(6)

        # ── 1. Область: игра + зелёная рамка ──
        grp_region = QGroupBox(f"{ICON['region']} Область")
        region_layout = QVBoxLayout(grp_region)
        region_layout.setSpacing(3)

        proc_row = QHBoxLayout()
        proc_row.setSpacing(4)
        proc_row.addWidget(QLabel(f"{ICON['process']} Игра:"))
        self.proc_label = QLabel("Auto")
        self.proc_label.setStyleSheet("color: #969696;")
        self.proc_label.setToolTip("Процесс, из которого берётся кадр")
        proc_row.addWidget(self.proc_label, 1)
        btn_proc = QPushButton(f"{ICON['edit']}...")
        btn_proc.setFixedWidth(34)
        btn_proc.setToolTip("Выбрать процесс игры вручную")
        btn_proc.clicked.connect(self._select_process)
        proc_row.addWidget(btn_proc)
        region_layout.addLayout(proc_row)

        self.region_list = QListWidget()
        self.region_list.setMaximumHeight(70)
        self.region_list.setToolTip("Зелёные рамки: кадр для OCR берётся отсюда")
        region_layout.addWidget(self.region_list)

        btn_row = QHBoxLayout()
        for icon, text, handler, tip in [
            (ICON['add'], "Add", self._add_region, "Добавить область"),
            (ICON['edit'], "Edit", self._edit_region, "Изменить область"),
            (ICON['delete'], "Del", self._delete_region, "Удалить область"),
        ]:
            b = QPushButton(f"{icon} {text}")
            b.setFixedHeight(24)
            b.setToolTip(tip)
            b.clicked.connect(handler)
            btn_row.addWidget(b)
        region_layout.addLayout(btn_row)
        left_layout.addWidget(grp_region)

        # ── 2. Распознавание: Scan + OCR в одном блоке ──
        grp_scan = QGroupBox(f"{ICON['scan']} Распознавание")
        scan_layout = QGridLayout(grp_scan)
        scan_layout.setHorizontalSpacing(4)
        scan_layout.setVerticalSpacing(3)

        scan_layout.addWidget(QLabel("Режим:"), 0, 0)
        self.scan_mode = QComboBox()
        self.scan_mode.addItems(["OCR", "STT", "Both"])
        self.scan_mode.setToolTip("Что делает Scan / Live: текст с экрана, речь, или оба")
        scan_layout.addWidget(self.scan_mode, 0, 1)

        scan_layout.addWidget(QLabel("Пауза:"), 0, 2)
        self.interval = QSpinBox()
        self.interval.setRange(100, 5000)
        self.interval.setValue(400)
        self.interval.setSuffix(" ms")
        self.interval.setToolTip("Пауза между сканами: больше = меньше нагрузка на FPS")
        scan_layout.addWidget(self.interval, 0, 3)

        scan_layout.addWidget(QLabel("Язык:"), 1, 0)
        self.lang_combo = QComboBox()
        self.lang_combo.addItems(["eng", "rus+eng", "ru", "jpn"])
        self.lang_combo.setToolTip("Язык текста в кадре")
        scan_layout.addWidget(self.lang_combo, 1, 1, 1, 3)

        scan_layout.addWidget(QLabel("Движок:"), 2, 0)
        # Только реально установленные движки: раньше в списке были
        # Tesseract (нет в движках) и EasyOCR (пакет не установлен) —
        # выбор заведомо нерабочих вариантов.
        from ocr_engines import list_available_engines
        self._engine_id_map = {}
        for _d in list_available_engines():
            if _d.engine_type.value == "google_lens":
                self._engine_id_map["Google Lens"] = "google_lens"
            elif _d.engine_type.value == "zen":
                self._engine_id_map["Zen (space-bunny-free)"] = "zen"
            elif _d.engine_type.value == "koma_onnx":
                self._engine_id_map["KOMA ONNX (PP-OCRv4+v5)"] = "koma_onnx"
            elif _d.engine_type.value == "tflite_cyrillic":
                self._engine_id_map["TFLite Cyrillic"] = "tflite_cyrillic"
            elif _d.engine_type.value == "easyocr":
                self._engine_id_map["EasyOCR"] = "easyocr"
        if not self._engine_id_map:
            self._engine_id_map = {"Google Lens": "google_lens"}
        self._engine_name_map = {v: k for k, v in self._engine_id_map.items()}
        self.engine_combo = QComboBox()
        self.engine_combo.addItems(list(self._engine_id_map.keys()))
        self.engine_combo.setToolTip(
            "KOMA ONNX — локальный, PP-OCRv4+v5 (кириллица)\n"
            "Zen — лучшая точность, нужен интернет\n"
            "Google Lens — требует API-ключ"
        )
        scan_layout.addWidget(self.engine_combo, 2, 1, 1, 3)

        self.auto_translate = QCheckBox(f"{ICON['auto']} Автоперевод")
        self.auto_translate.setChecked(True)
        self.auto_translate.setToolTip("Переводить распознанный текст сразу на русский")
        scan_layout.addWidget(self.auto_translate, 3, 0, 1, 4)

        left_layout.addWidget(grp_scan)

        # ── 3. Голос ──
        grp_voice = QGroupBox(f"{ICON['voice']} Голос")
        voice_layout = QGridLayout(grp_voice)
        voice_layout.setHorizontalSpacing(4)
        voice_layout.setVerticalSpacing(3)

        voice_layout.addWidget(QLabel("Движок:"), 0, 0)
        self.tts_engine_combo = QComboBox()
        self.tts_engine_combo.setToolTip("Синтез речи")
        voice_layout.addWidget(self.tts_engine_combo, 0, 1, 1, 2)

        voice_layout.addWidget(QLabel("Голос:"), 1, 0)
        self.voice_combo = QComboBox()
        self.voice_combo.setToolTip("Голос для озвучки перевода")
        voice_layout.addWidget(self.voice_combo, 1, 1, 1, 2)

        voice_layout.addWidget(QLabel(f"{ICON['speed']} Скор."), 2, 0)
        self.speed_slider = QSlider(Qt.Orientation.Horizontal)
        self.speed_slider.setRange(-50, 50)
        self.speed_slider.setValue(15)
        self.speed_slider.setToolTip("Скорость речи")
        voice_layout.addWidget(self.speed_slider, 2, 1)
        self.speed_label = QLabel("15")
        self.speed_slider.valueChanged.connect(lambda v: self.speed_label.setText(str(v)))
        voice_layout.addWidget(self.speed_label, 2, 2)

        voice_layout.addWidget(QLabel(f"{ICON['pitch']} Тон:"), 3, 0)
        self.pitch_slider = QSlider(Qt.Orientation.Horizontal)
        self.pitch_slider.setRange(-50, 50)
        self.pitch_slider.setValue(0)
        self.pitch_slider.setToolTip("Высота тона")
        voice_layout.addWidget(self.pitch_slider, 3, 1)
        self.pitch_label = QLabel("0")
        self.pitch_slider.valueChanged.connect(lambda v: self.pitch_label.setText(str(v)))
        voice_layout.addWidget(self.pitch_label, 3, 2)

        # ── Голос персонажа: кто говорит сейчас (из OCR) + его голос ──
        self._person_speaker = None
        self.person_label = QLabel("Персонаж: —")
        self.person_label.setToolTip("Кто говорит сейчас (имя из OCR)")
        voice_layout.addWidget(self.person_label, 4, 0, 1, 3)
        self.person_voice_combo = QComboBox()
        self.person_voice_combo.setToolTip(
            "Голос этого персонажа. «Основной голос» — как в настройках. "
            "Для неизвестных имён (псевдонимы, латиница) всегда основной голос.")
        voice_layout.addWidget(self.person_voice_combo, 5, 0, 1, 3)
        self.person_voice_combo.currentIndexChanged.connect(self._on_person_voice_changed)

        left_layout.addWidget(grp_voice)

        # ── 4. Вывод: оверлеи + STT ──
        grp_ovl = QGroupBox(f"{ICON['overlay']} Вывод")
        ovl_layout = QVBoxLayout(grp_ovl)
        ovl_layout.setSpacing(3)

        # All overlays hidden by default; only the green region frame shows.
        ovl_row = QHBoxLayout()
        self.ovl_ocr = QPushButton(f"{ICON['ok']} Текст OCR")
        self.ovl_ocr.setCheckable(True)
        self.ovl_ocr.setToolTip("Показывать распознанный текст поверх окна игры")
        self.ovl_stt = QPushButton(f"{ICON['ok']} Текст речи")
        self.ovl_stt.setCheckable(True)
        self.ovl_stt.setToolTip("Показывать распознанную речь поверх окна игры")
        ovl_row.addWidget(self.ovl_ocr)
        ovl_row.addWidget(self.ovl_stt)
        ovl_layout.addLayout(ovl_row)
        left_layout.addWidget(grp_ovl)

        # STT Button (checkable active/passive toggle)
        self.btn_stt = QPushButton(f"{ICON['stt']} Start STT")
        self.btn_stt.setFixedHeight(32)
        self.btn_stt.setCheckable(True)
        self.btn_stt.setChecked(False)
        self.btn_stt.setToolTip("Распознавать речь из системного звука (Ctrl+Shift+F7)")
        self.btn_stt.toggled.connect(self._toggle_stt)
        left_layout.addWidget(self.btn_stt)

        left_layout.addStretch()
        left_scroll.setWidget(left_widget)
        splitter.addWidget(left_scroll)

        # ── RIGHT PANEL: Text ──
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(4, 4, 4, 4)
        right_layout.setSpacing(4)

        # Screenshot Preview
        grp_preview = QGroupBox("Screenshot")
        # ── Preview: рамка + превью кадра ──
        grp_preview = QGroupBox("Превью области")
        preview_layout = QVBoxLayout(grp_preview)
        self.preview_label = QLabel("Нет кадра")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumHeight(60)
        self.preview_label.setMaximumHeight(120)
        self.preview_label.setStyleSheet("background: #1e1e1e; border: 1px solid #3c3c3c; border-radius: 4px; color: #888;")
        preview_layout.addWidget(self.preview_label)
        right_layout.addWidget(grp_preview)

        # ── Клавиатура-симулятор ──
        # Раньше была всегда включена и постоянно занимала ~60 px.
        # Это чисто визуальный эффект «печати» текста, поэтому по
        # умолчанию выключена, а сам виджет появляется только по флажку.
        from keyboard_widget import KeyboardWidget
        kb_row = QHBoxLayout()
        self.kb_check = QCheckBox(f"{ICON['settings']} Показывать набор текста")
        self.kb_check.setChecked(False)
        self.kb_check.setToolTip("Анимировать набор распознанного текста (для отладки OCR)")
        kb_row.addWidget(self.kb_check)
        kb_row.addStretch()
        self.kb_widget = KeyboardWidget()
        self.kb_widget.setMaximumHeight(60)
        self.kb_widget.hide()
        self.kb_check.toggled.connect(lambda on: self.kb_widget.setVisible(bool(on)))
        kb_row.addWidget(self.kb_widget)
        right_layout.addLayout(kb_row)

        # Original + Translation (side by side)
        text_splitter = QSplitter(Qt.Orientation.Horizontal)

        # Original
        grp_orig = QGroupBox("Оригинал (OCR)")
        orig_layout = QVBoxLayout(grp_orig)
        self.result_text = QTextEdit()
        self.result_text.setPlaceholderText("OCR text...")
        self.result_text.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        orig_layout.addWidget(self.result_text)
        btn_row_orig = QHBoxLayout()
        for icon, text, handler in [
            (ICON['tts'], "Speak", self._on_speak),
            (ICON['copy'], "Copy", lambda: QApplication.clipboard().setText(self.result_text.toPlainText())),
            (ICON['clear'], "Clear", lambda: self.result_text.clear()),
            (ICON['save'], ".txt", self._on_save_txt),
        ]:
            b = QPushButton(f"{icon} {text}")
            b.setFixedHeight(24)
            b.clicked.connect(handler)
            btn_row_orig.addWidget(b)
        orig_layout.addLayout(btn_row_orig)
        text_splitter.addWidget(grp_orig)

        # Translation
        grp_tr = QGroupBox("Перевод")
        tr_layout = QVBoxLayout(grp_tr)
        self.translate_text = QTextEdit()
        self.translate_text.setPlaceholderText("Translation...")
        self.translate_text.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.translate_text.setStyleSheet("QTextEdit { background: #1a2a1a; border: 1px solid #2ea043; }")
        tr_layout.addWidget(self.translate_text)
        btn_row_tr = QHBoxLayout()
        for icon, text, handler in [
            (ICON['translate'], "Translate", self._on_translate),
            (ICON['tts'], "Speak RU", lambda: self._speak_text(self.translate_text.toPlainText(), "ru")),
            (ICON['copy'], "Copy", lambda: QApplication.clipboard().setText(self.translate_text.toPlainText())),
            (ICON['clear'], "Clear", lambda: self.translate_text.clear()),
        ]:
            b = QPushButton(f"{icon} {text}")
            b.setFixedHeight(24)
            b.clicked.connect(handler)
            btn_row_tr.addWidget(b)
        tr_layout.addLayout(btn_row_tr)
        text_splitter.addWidget(grp_tr)

        text_splitter.setSizes([400, 400])
        right_layout.addWidget(text_splitter, 1)

        # STT Text
        grp_stt = QGroupBox(f"{ICON['stt']} Речь (STT)")
        stt_layout = QVBoxLayout(grp_stt)
        self.stt_text = QTextEdit()
        self.stt_text.setPlaceholderText("STT results...")
        self.stt_text.setMaximumHeight(80)
        self.stt_text.setStyleSheet("QTextEdit { background: #1a2a1a; border: 1px solid #2ea043; }")
        stt_layout.addWidget(self.stt_text)
        right_layout.addWidget(grp_stt)

        splitter.addWidget(right_widget)
        # 350 = минимум левой панели, иначе подписи снова обрежутся
        splitter.setSizes([350, 620])

        # ═══════════════════════════════════════════════
        # TABS: Main | Browser
        # ═══════════════════════════════════════════════
        self.tabs = QTabWidget()
        self.tabs.addTab(splitter, "🏠 Главная")
        self.tabs.addTab(self._create_browser_tab(), "🌐 Браузер")
        main_layout.addWidget(self.tabs, 1)

        # ═══════════════════════════════════════════════
        # STATUS BAR
        # ═══════════════════════════════════════════════
        self.status_label = QLabel("Ready")
        self.statusBar().addPermanentWidget(self.status_label)
        self.statusBar().showMessage("Screen OCR + TTS")

        # ═══════════════════════════════════════════════
        # INITIALIZATION
        # ═══════════════════════════════════════════════
        self._init_ocr()
        self._init_tts()
        self._init_scanner()
        self._init_tray()
        self._load_voices()
        self._init_game_preview()
        self._init_global_hotkeys()

        # TTS speaking indicator poll (covers Speak button + live mode)
        self._tts_poll = QTimer(self)
        self._tts_poll.timeout.connect(self._update_tts_icon)
        self._tts_poll.start(500)

        logger.info("[COMPACT] Window initialized")

    # ═══════════════════════════════════════════════
    # BROWSER TAB (embedded web page + OCR scan)
    # ═══════════════════════════════════════════════
    def _create_browser_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        nav = QHBoxLayout()
        nav.setSpacing(4)
        self.web_back = QPushButton("◀")
        self.web_back.setFixedWidth(36)
        self.web_forward = QPushButton("▶")
        self.web_forward.setFixedWidth(36)
        self.web_reload = QPushButton(f"{ICON['refresh']}")
        self.web_reload.setFixedWidth(36)
        self.web_stop = QPushButton(f"{ICON['stop']}")
        self.web_stop.setFixedWidth(36)
        self.web_stop.setToolTip("Stop loading")
        self.web_home = QPushButton("🏠")
        self.web_home.setFixedWidth(36)
        self.web_home.setToolTip("Home (search engine)")
        self.web_url = QLineEdit()
        self.web_url.setPlaceholderText("https://...")
        self.web_url.setText(self.settings.get("browser.url", ""))
        self.web_go = QPushButton("Go")
        self.web_go.setFixedWidth(48)
        self.web_select = QPushButton("✂")
        self.web_select.setFixedWidth(36)
        self.web_select.setToolTip("Select page region to scan")
        self.web_select.setCheckable(True)
        self.web_scan = QPushButton(f"{ICON['scan']} Scan")
        self.web_external = QPushButton("↗")
        self.web_external.setFixedWidth(36)
        self.web_external.setToolTip("Open in system browser")
        for w in (self.web_back, self.web_forward, self.web_reload,
                  self.web_stop, self.web_home,
                  self.web_url, self.web_go, self.web_select,
                  self.web_scan, self.web_external):
            nav.addWidget(w)
        self.web_url.setSizePolicy(QSizePolicy.Policy.Expanding,
                                   QSizePolicy.Policy.Fixed)
        layout.addLayout(nav)

        # Search engine opens immediately so the tab is never empty.
        self._browser_home_url = "https://duckduckgo.com"
        try:
            if not _WEBENGINE_AVAILABLE:
                raise ImportError("PyQt6-WebEngine not installed")
            self.webview = QWebEngineView()
            self.web_back.clicked.connect(lambda: self.webview.back())
            self.web_forward.clicked.connect(lambda: self.webview.forward())
            self.web_reload.clicked.connect(lambda: self.webview.reload())
            self.web_stop.clicked.connect(lambda: self.webview.stop())
            self.web_home.clicked.connect(self._browser_home)
            self.web_go.clicked.connect(self._browser_go)
            self.web_url.returnPressed.connect(self._browser_go)
            self.webview.urlChanged.connect(
                lambda q: self.web_url.setText(q.toString()))
            start = self.web_url.text().strip() or self._browser_home_url
            self.web_url.setText(start)
            self.webview.setUrl(self._browser_qurl(start))
            self.web_select.toggled.connect(self._browser_select_toggled)
            self._select_band = None
            self._select_origin = None
            self.webview.installEventFilter(self)
        except Exception as e:
            logger.warning(f"[BROWSER] WebEngine unavailable: {e}")
            self.webview = None
            fallback = QLabel(
                "Embedded browser needs PyQt6-WebEngine:\n"
                "venv311\\Scripts\\pip.exe install PyQt6-WebEngine\n\n"
                "Meanwhile use ↗ to open pages in the system browser,\n"
                "then Scan screen region as usual.")
            fallback.setAlignment(Qt.AlignmentFlag.AlignCenter)
            fallback.setStyleSheet("color: #888;")
            for btn in (self.web_back, self.web_forward, self.web_reload,
                        self.web_stop, self.web_home, self.web_go,
                        self.web_select):
                btn.setEnabled(False)
            layout.addWidget(fallback, 1)
            self.web_scan.clicked.connect(self._on_scan)
            self.web_external.clicked.connect(self._browser_external)
            return tab

        layout.addWidget(self.webview, 1)
        self.web_scan.clicked.connect(self._on_browser_scan)
        self.web_external.clicked.connect(self._browser_external)
        return tab

    @staticmethod
    def _browser_qurl(text):
        text = (text or "").strip()
        if not text:
            return QUrl()
        if "://" not in text:
            text = "https://" + text
        return QUrl(text)

    def _browser_go(self):
        if self.webview is None:
            return
        q = self._browser_qurl(self.web_url.text())
        if not q.isEmpty():
            self.webview.setUrl(q)
            self.settings.set("browser.url", q.toString())

    def _browser_home(self):
        if self.webview is None:
            return
        self.web_url.setText(self._browser_home_url)
        self.webview.setUrl(QUrl(self._browser_home_url))

    def _browser_select_toggled(self, on):
        if self.webview is None:
            return
        if on:
            self.webview.setCursor(Qt.CursorShape.CrossCursor)
            self.status_label.setText("Drag a rectangle on the page...")
        else:
            self.webview.unsetCursor()
            band = getattr(self, "_select_band", None)
            if band is not None:
                band.hide()
            self._select_origin = None

    def eventFilter(self, obj, event):
        try:
            if (getattr(self, "webview", None) is not None
                    and obj is self.webview
                    and self.web_select.isChecked()):
                et = event.type()
                if (et == QEvent.Type.MouseButtonPress
                        and event.button() == Qt.MouseButton.LeftButton):
                    self._select_origin = event.position().toPoint()
                    if getattr(self, "_select_band", None) is None:
                        from PyQt6.QtWidgets import QRubberBand
                        self._select_band = QRubberBand(
                            QRubberBand.Shape.Rectangle, self.webview)
                    self._select_band.setGeometry(
                        QRect(self._select_origin, QSize()))
                    self._select_band.show()
                    return True
                if (et == QEvent.Type.MouseMove
                        and self._select_origin is not None):
                    self._select_band.setGeometry(QRect(
                        self._select_origin,
                        event.position().toPoint()).normalized())
                    return True
                if et == QEvent.Type.MouseButtonRelease:
                    origin = self._select_origin
                    self._select_origin = None
                    if origin is not None and self._select_band is not None:
                        rect = self._select_band.geometry()
                        self._select_band.hide()
                        self.web_select.setChecked(False)
                        self.webview.unsetCursor()
                        self._browser_scan_rect(rect)
                        return True
        except Exception as e:
            logger.error(f"[BROWSER] select error: {e}")
        return super().eventFilter(obj, event)

    def _browser_scan_rect(self, rect):
        if self.webview is None or not self.ocr:
            self.status_label.setText("Browser not ready")
            return
        try:
            dpr = self.webview.devicePixelRatio()
            r = QRect(int(rect.x() * dpr), int(rect.y() * dpr),
                      int(rect.width() * dpr), int(rect.height() * dpr))
            if r.width() < 8 or r.height() < 8:
                self.status_label.setText("Selection too small")
                return
            pix = self.webview.grab().copy(r)
            qimg = pix.toImage().convertToFormat(
                QImage.Format.Format_RGB888)
            w, h = qimg.width(), qimg.height()
            ptr = qimg.constBits()
            ptr.setsize(w * h * 3)
            from PIL import Image
            img = Image.frombytes("RGB", (w, h), bytes(ptr)).copy()
        except Exception as e:
            logger.error(f"Browser select-grab error: {e}")
            self.status_label.setText(f"Grab error: {e}")
            return
        self.status_label.setText("Scanning selection...")
        threading.Thread(target=lambda: self._do_browser_scan(img),
                         daemon=True).start()

    def _browser_external(self):
        q = self._browser_qurl(self.web_url.text())
        if not q.isEmpty():
            QDesktopServices.openUrl(q)

    def _on_browser_scan(self):
        if self.webview is None or not self.ocr:
            self.status_label.setText("Browser not ready")
            return
        try:
            # Grab must run in the GUI thread.
            pix = self.webview.grab()
            qimg = pix.toImage().convertToFormat(
                QImage.Format.Format_RGB888)
            w, h = qimg.width(), qimg.height()
            ptr = qimg.constBits()
            ptr.setsize(w * h * 3)
            from PIL import Image
            img = Image.frombytes("RGB", (w, h), bytes(ptr)).copy()
        except Exception as e:
            logger.error(f"Browser grab error: {e}")
            self.status_label.setText(f"Grab error: {e}")
            return
        self.status_label.setText("Scanning page...")
        self.web_scan.setEnabled(False)
        threading.Thread(target=lambda: self._do_browser_scan(img),
                         daemon=True).start()

    def _do_browser_scan(self, img):
        try:
            self._pending_preview = img
            self._gui(lambda: self._show_preview(self._pending_preview))
            self._gui(lambda: self.status_label.setText("OCR processing..."))
            import time
            t0 = time.perf_counter()
            # Веб-контент — не экран: рамки KOMA поверх игры не рисуем.
            self.ocr.set_capture_origin(None)
            result = self.ocr.recognize(img)
            elapsed = time.perf_counter() - t0
            text = result[0] if isinstance(result, tuple) else str(result)
            self._gui(lambda: self._on_text_detected(text))
            self._gui(lambda: self.status_label.setText(
                f"Done in {elapsed:.1f}s | {len(text)} chars"))
            # Show the result: jump back to the main tab where text lives.
            self._gui(lambda: self.tabs.setCurrentIndex(0))
        except Exception as e:
            logger.error(f"Browser scan error: {e}")
            self._gui(lambda: self.status_label.setText(f"Error: {e}"))
        finally:
            self._gui(lambda: self.web_scan.setEnabled(True))

    def _mort_theme(self):
        fs = self.settings.get("ui.scale", 12)
        return f"""
        QMainWindow, QWidget {{ background-color: #1e1e1e; color: #d4d4d4; font-size: {fs}px; }}
        QGroupBox {{ border: 1px solid #3c3c3c; border-radius: 4px; margin-top: 8px; padding-top: 8px; font-weight: bold; }}
        QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; }}
        QPushButton {{ background: #0e639c; color: #fff; border: none; border-radius: 4px; padding: 4px 8px; font-size: {fs}px; }}
        QPushButton:hover {{ background: #1177bb; }}
        QPushButton:pressed {{ background: #094771; }}
        QPushButton:checked {{ background: #2ea043; }}
        QComboBox {{ background: #2d2d30; color: #d4d4d4; border: 1px solid #3c3c3c; border-radius: 3px; padding: 2px 6px; }}
        QSpinBox {{ background: #2d2d30; color: #d4d4d4; border: 1px solid #3c3c3c; border-radius: 3px; padding: 2px; }}
        QLineEdit {{ background: #2d2d30; color: #d4d4d4; border: 1px solid #3c3c3c; border-radius: 3px; padding: 4px; }}
        QTextEdit {{ background: #2d2d30; color: #d4d4d4; border: 1px solid #3c3c3c; border-radius: 3px; padding: 4px; }}
        QSlider::groove:horizontal {{ background: #3c3c3c; height: 4px; border-radius: 2px; }}
        QSlider::handle:horizontal {{ background: #007acc; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; }}
        QCheckBox {{ color: #d4d4d4; spacing: 4px; }}
        QCheckBox::indicator {{ width: 14px; height: 14px; border-radius: 3px; border: 1px solid #555; background: transparent; }}
        QCheckBox::indicator:checked {{ background-color: #007acc; border-color: #007acc; }}
        QListWidget {{ background: #2d2d30; color: #d4d4d4; border: 1px solid #3c3c3c; border-radius: 3px; }}
        QListWidget::item:selected {{ background: #094771; color: #fff; }}
        QStatusBar {{ background: #007acc; color: #fff; font-size: {max(10, fs-1)}px; }}
        QScrollArea {{ border: none; background: transparent; }}
        QSplitter::handle {{ background: #3c3c3c; }}
        """

    # ═══════════════════════════════════════════════
    # OCR INITIALIZATION
    # ═══════════════════════════════════════════════
    def _init_ocr(self):
        from ocr_wrapper import OCRWrapper
        self.ocr = OCRWrapper(self.settings)
        self._wire_ocr_hooks()
        engine = self.settings.get("ocr.engine", "")
        # Сохранённый движок мог быть удалён/недоступен — тогда берём
        # лучший реально установленный (предпочтение локальному TFLite).
        if engine == "cyrillic_onnx":
            engine = "tflite_cyrillic"
        if engine not in self._engine_id_map.values():
            engine = ("tflite_cyrillic"
                      if "tflite_cyrillic" in self._engine_id_map.values()
                      else next(iter(self._engine_id_map.values())))
            self.settings.set("ocr.engine", engine)
        logger.info(f"[OCR] Engine: {engine}")
        # Sync engine combo with active engine
        try:
            self.engine_combo.blockSignals(True)
            self.engine_combo.setCurrentText(
                self._engine_name_map.get(engine, next(iter(self._engine_id_map))))
            self.engine_combo.blockSignals(False)
        except Exception:
            pass
        try:
            self.engine_combo.currentTextChanged.disconnect()
        except Exception:
            pass
        self.engine_combo.currentTextChanged.connect(self._on_engine_changed)
        self._schedule_ocr_warmup()

    def _schedule_ocr_warmup(self):
        """Прогреть OCR-модели в фоне, чтобы первый Scan не ждал ~13 c.

        ONNX Runtime инициализирует модели лениво: без прогрева первый
        клик «Scan» упирается в 13 c ожидания. Прогрев делаем отдельным
        потоком — GUI при этом полностью отзывчив.
        """
        def _warm():
            try:
                # KOMA ONNX: заранее создаём сессии (детектор+распознаватель),
                # иначе первый Scan ждёт загрузку ~2-4 c.
                if getattr(self.ocr, "engine_type", "") == "koma_onnx":
                    from ocr_engines import create_engine
                    eng = create_engine("koma_onnx", self.settings)
                    if eng is not None:
                        self.ocr._koma_engine = eng
                        logger.info("[OCR] KOMA ONNX warmed up")
                        return
                eng = getattr(self.ocr, "rapidocr_engine", None)
                if eng is None:
                    eng = getattr(self.ocr, "rapid", None)
                if eng is not None and hasattr(eng, "warmup"):
                    eng.warmup()
            except Exception as e:
                logger.debug(f"[OCR] warmup error: {e}")
        threading.Thread(target=_warm, name="ocr-warmup", daemon=True).start()

    def _on_engine_changed(self, name):
        engine_id = (self._engine_id_map.get(name)
                     or next(iter(self._engine_id_map.values())))
        self.settings.set("ocr.engine", engine_id)
        try:
            from ocr_wrapper import OCRWrapper
            self.ocr = OCRWrapper(self.settings)
            self._wire_ocr_hooks()
            if hasattr(self, "scanner") and self.scanner is not None:
                try:
                    self.scanner.ocr_engine = self.ocr
                except Exception:
                    pass
            self.status_label.setText(f"OCR engine: {name}")
            logger.info(f"[OCR] Switched engine to: {engine_id}")
        except Exception as e:
            logger.error(f"[OCR] Engine switch failed: {e}")
            self.status_label.setText(f"Engine error: {e}")

    def _init_tts(self):
        from tts_engine import TTSEngine
        self.tts = TTSEngine(self.settings)
        logger.info("[TTS] Engine initialized")

    def _init_scanner(self):
        from live_scanner import LiveScanner
        self.scanner = LiveScanner(self.ocr, self.settings, self.tts)
        # Callback придёт из asyncio-потока сканера — маршалим в главный
        # поток Qt через сигнал gui_invoke (иначе работа с виджетами
        # из фонового потока = undefined behavior).
        self.scanner.on_text_detected = lambda text: self._gui(
            lambda t=text: self._on_text_detected(t))
        self.scanner.on_screenshot = lambda img: self._gui(lambda: self._show_preview(img))

        # Region overlay (green frame)
        from region_overlay import RegionOverlay
        self.region_overlay = RegionOverlay(settings=self.settings, config_key="overlay")
        self.region_overlay.scan_requested.connect(self._on_scan)
        # Кнопки Live на панели оверлея — те же действия, что в трее/панели:
        # запуск непрерывного сканирования и его остановка.
        self.region_overlay.live_start_requested.connect(
            lambda: self._on_live_toggle(True))
        self.region_overlay.live_stop_requested.connect(self._on_stop_live)
        self.region_overlay.region_moved.connect(self._on_region_moved)
        self.region_overlay.region_deleted.connect(self._on_region_deleted)
        self.region_overlay.region_right_clicked.connect(self._show_region_context_menu)
        self.regions = self._load_regions()
        self.region_overlay.set_regions(self.regions)
        if self.regions:
            self.region_overlay.show_overlay()
            # Первый запуск: regions[0] → ocr.region (и в settings),
            # иначе live схватит весь экран с мусором по краям.
            self._sync_ocr_region()
        logger.info(f"[Scanner] Initialized with overlay, {len(self.regions)} regions")

    def _load_regions(self):
        try:
            if os.path.exists("regions.json"):
                with open("regions.json", "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception as e:
            logger.error(f"Error loading regions: {e}")
        return []

    def _save_regions(self):
        try:
            with open("regions.json", "w", encoding="utf-8") as f:
                json.dump(self.regions, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Error saving regions: {e}")
        self._sync_ocr_region()

    def _sync_ocr_region(self):
        """Держим ocr.region равным regions[0]: live-захват и auto-read идут
        из ocr.capture_region() — без этого live сканирует ВЕСЬ экран и в
        реплику лезет мусор по краям (кнопка «АВТО», «UID: …» и т.п.).
        Кнопочный Scan и так берёт regions[0] — приводим все режимы
        к одному источнику истины."""
        if getattr(self, "ocr", None) is None or not self.regions:
            return
        r = self.regions[0]
        try:
            self.ocr.set_region(r["x"], r["y"], r["width"], r["height"])
        except Exception as e:
            logger.debug(f"[Region] sync ocr.region: {e}")

    def _on_region_moved(self, idx, x, y, w, h):
        if idx < len(self.regions):
            self.regions[idx]["x"] = x
            self.regions[idx]["y"] = y
            self.regions[idx]["width"] = w
            self.regions[idx]["height"] = h
            self._save_regions()

    def _on_region_deleted(self, idx):
        if idx < len(self.regions):
            del self.regions[idx]
            self._save_regions()
            self.region_overlay.set_regions(self.regions)

    def _show_region_context_menu(self, index):
        if index < 0 or index >= len(self.regions):
            return
        region = self.regions[index]
        locked = region.get("locked", False)
        from PyQt6.QtWidgets import QMenu
        from PyQt6.QtGui import QCursor
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu { background-color: #2b2b3b; color: #cdd6f4; border: 1px solid #45475a; }"
            "QMenu::item:selected { background-color: #585b70; }"
        )
        edit_action = menu.addAction("Edit")
        delete_action = menu.addAction("Delete")
        lock_text = "Unlock" if locked else "Lock"
        lock_action = menu.addAction(lock_text)
        menu.addSeparator()
        scan_action = menu.addAction("Scan this region")
        live_start_action = menu.addAction("Live Scan")
        live_stop_action = menu.addAction("Stop Live")

        # Live-скан молча захватывает открытые пункты меню (текст меню
        # попадает в «реплику») — на время меню ставим сканер на паузу.
        scanner = getattr(self, "scanner", None)
        if scanner is not None:
            scanner.pause()
        try:
            action = menu.exec(QCursor.pos())
        finally:
            if scanner is not None:
                scanner.resume()

        if action == edit_action:
            self._edit_region_by_idx(index)
        elif action == delete_action:
            self._delete_region_by_idx(index)
        elif action == lock_action:
            region["locked"] = not locked
            self._save_regions()
            self.region_overlay.set_regions(self.regions)
        elif action == scan_action:
            self._scan_region(region)
        elif action == live_start_action:
            self._on_live_toggle(True)
        elif action == live_stop_action:
            self._on_stop_live()

    def _scan_region(self, region):
        if not self.ocr:
            self.status_label.setText("OCR not ready")
            return
        self.status_label.setText(f"Scanning {region.get('name', '')}...")
        self.region_overlay.set_scanning(True)
        self.region_overlay.update()
        threading.Thread(target=lambda: self._do_scan_region(region), daemon=True).start()

    def _do_scan_region(self, region):
        try:
            import mss, time
            from PIL import Image
            x, y, w, h = region["x"], region["y"], region["width"], region["height"]
            self._gui(lambda: self.status_label.setText(f"Capturing {region.get('name', '')}..."))
            with mss.mss() as sct:
                monitor = {"left": x, "top": y, "width": w, "height": h}
                shot = sct.grab(monitor)
                img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
            self._pending_preview = img
            self._gui(lambda: self._show_preview(self._pending_preview))
            self._gui(lambda: self.status_label.setText("OCR processing..."))
            t0 = time.perf_counter()
            # Регион кроем сами — сообщаем офсет, иначе рамки KOMA
            # рисуются в локальных координатах кропа (выше/левее текста).
            self.ocr.set_capture_origin(x, y)
            result = self.ocr.recognize(img)
            elapsed = time.perf_counter() - t0
            text = result[0] if isinstance(result, tuple) else str(result)
            self._gui(lambda: self._on_text_detected(text))
            self._gui(lambda: self.status_label.setText(f"Done in {elapsed:.1f}s | {len(text)} chars"))
        except Exception as e:
            logger.error(f"Scan error: {e}")
            self._gui(lambda: self.status_label.setText(f"Error: {e}"))
        finally:
            self._gui(lambda: self.region_overlay.set_scanning(False))
            self._gui(lambda: self.region_overlay.update())

    def _init_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(self.style().standardIcon(
            self.style().StandardPixmap.SP_ComputerIcon))
        tray_menu = QMenu()
        tray_menu.addAction(f"{ICON['ok']} Show", self.showNormal)
        tray_menu.addSeparator()
        tray_menu.addAction(f"{ICON['scan']} Scan", self._on_scan)
        tray_menu.addAction(f"{ICON['live_on']} Start Live", lambda: self._on_live_toggle(True))
        tray_menu.addAction(f"{ICON['live_off']} Stop Live", self._on_stop_live)
        tray_menu.addSeparator()
        tray_menu.addAction(f"{ICON['stt']} Toggle STT", lambda: self.btn_stt.toggle())
        tray_menu.addAction(f"{ICON['tts_stop']} Stop TTS", self._on_stop_speak)
        tray_menu.addSeparator()
        tray_menu.addAction(f"{ICON['translate']} Translate", self._on_translate)
        tray_menu.addAction(f"{ICON['copy']} Copy Original", lambda: QApplication.clipboard().setText(self.result_text.toPlainText()))
        tray_menu.addAction(f"{ICON['copy']} Copy Translation", lambda: QApplication.clipboard().setText(self.translate_text.toPlainText()))
        tray_menu.addSeparator()
        tray_menu.addAction(f"{ICON['quit']} Quit", self.close)
        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self._on_tray_activated)
        self.tray_icon.show()

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.showNormal()
            self.activateWindow()

    # ═══════════════════════════════════════════════
    # GAME PREVIEW + STATUS ICONS
    # ═══════════════════════════════════════════════

    def _init_game_preview(self):
        """Инициализация окна предпросмотра игры и панели иконок."""
        logger.info("[GAME-PREVIEW] creating GamePreview...")
        from game_preview import GamePreview, StatusIcons
        region = self.regions[0] if self.regions else {"x": 0, "y": 0, "width": 800, "height": 450}

        # Окно предпросмотра игры
        self.game_preview = GamePreview(region=region)
        logger.info("[GAME-PREVIEW] GamePreview created")

        # Плавающая панель иконок (отдельное окно, маленькое, в углу экрана)
        self.status_icons = StatusIcons()
        logger.info("[GAME-PREVIEW] StatusIcons created")
        self.status_icons.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.status_icons.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.status_icons.resize(150, 28)
        # Разместить в правом нижнем углу
        try:
            geo = QApplication.primaryScreen().availableGeometry()
            self.status_icons.move(geo.right() - 160, geo.bottom() - 40)
        except Exception:
            pass

        logger.info("[GAME-PREVIEW] Initialized")

    def _toggle_game_preview(self):
        """Показать/скрыть окно предпросмотра игры."""
        if not hasattr(self, 'game_preview'):
            return
        if self.game_preview.isVisible():
            self.game_preview.hide()
        else:
            # Обновить регион
            if self.regions:
                self.game_preview.set_region(self.regions[0])
            self.game_preview.show()
            self.game_preview.raise_()

    def _update_game_preview(self, pil_img):
        """Обновить кадр в окне предпросмотра."""
        if hasattr(self, 'game_preview') and self.game_preview.isVisible():
            self.game_preview.update_frame(pil_img)

    def _voice_float(self):
        """Tiny always-on-top speaker shown only while voice is playing."""
        lbl = getattr(self, "_voice_float_lbl", None)
        if lbl is None:
            lbl = QLabel("🔊", None)
            lbl.setWindowFlags(
                Qt.WindowType.WindowStaysOnTopHint
                | Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.Tool)
            lbl.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            lbl.setStyleSheet("font-size: 30px; background: transparent;")
            lbl.adjustSize()
            try:
                geo = QApplication.primaryScreen().availableGeometry()
                lbl.move(geo.right() - 70, geo.bottom() - 90)
            except Exception:
                pass
            self._voice_float_lbl = lbl
        return lbl

    def _update_tts_icon(self):
        """Voice icons: top-bar 🔊 + tiny floating speaker while speaking."""
        try:
            speaking = bool(getattr(self.tts, "is_playing", False))
        except Exception:
            speaking = False
        if speaking:
            self.tts_icon.setStyleSheet("color: #2ea043; font-size: 18px;")
            self.tts_icon.setToolTip("TTS: speaking...")
        else:
            self.tts_icon.setStyleSheet("color: #555555; font-size: 18px;")
            self.tts_icon.setToolTip("TTS: idle")
        try:
            fl = self._voice_float()
            if speaking:
                if not fl.isVisible():
                    fl.show()
            else:
                if fl.isVisible():
                    fl.hide()
        except Exception:
            pass

    # TTS engine split (yomikai-style: engine and voice are separate settings)
    _TTS_ENGINE_NAMES = {
        "edge": "Edge", "rhvoice": "RHVoice", "silero": "Silero",
        "persona": "Persona", "sapi": "SAPI", "profile": "Profiles",
    }

    def _load_voices(self):
        voices = self.tts.get_voices() if hasattr(self.tts, 'get_voices') else []
        self._all_voices = voices
        engines = []
        for v in voices:
            t = v.get("type", "edge")
            if t not in engines:
                engines.append(t)
        if not engines:
            engines = ["edge"]
        self.tts_engine_combo.blockSignals(True)
        self.tts_engine_combo.clear()
        for t in engines:
            self.tts_engine_combo.addItem(self._TTS_ENGINE_NAMES.get(t, t), t)
        # Restore saved engine (else engine of saved voice, else first)
        saved_type = self.settings.get("tts.engine", None)
        saved_voice = self.settings.get("tts.voice", getattr(self.tts, "voice", ""))
        if saved_type in engines:
            self.tts_engine_combo.setCurrentIndex(engines.index(saved_type))
        else:
            match = next((i for i, t in enumerate(engines)
                          if any(v.get("code") == saved_voice and v.get("type", "edge") == t
                                 for v in voices)), 0)
            self.tts_engine_combo.setCurrentIndex(match)
        self.tts_engine_combo.blockSignals(False)
        try:
            self.tts_engine_combo.currentIndexChanged.disconnect()
        except Exception:
            pass
        self.tts_engine_combo.currentIndexChanged.connect(self._on_tts_engine_changed)
        try:
            self.voice_combo.currentIndexChanged.disconnect()
        except Exception:
            pass
        self.voice_combo.currentIndexChanged.connect(self._on_voice_changed)
        self._refill_voice_combo(select_code=saved_voice, apply=True)

    def _refill_voice_combo(self, select_code=None, apply=False):
        eng_type = self.tts_engine_combo.currentData() or "edge"
        candidates = [v for v in getattr(self, "_all_voices", [])
                      if v.get("type", "edge") == eng_type]
        if not candidates:
            candidates = getattr(self, "_all_voices", [])
        self.voice_combo.blockSignals(True)
        self.voice_combo.clear()
        for v in candidates:
            self.voice_combo.addItem(v.get("name", v.get("code", "")), v.get("code", ""))
        if select_code:
            for i in range(self.voice_combo.count()):
                if self.voice_combo.itemData(i) == select_code:
                    self.voice_combo.setCurrentIndex(i)
                    break
        self.voice_combo.blockSignals(False)
        if apply:
            self._apply_tts_voice()

    def _on_tts_engine_changed(self, _idx):
        self._refill_voice_combo(apply=True)

    def _on_voice_changed(self, _idx):
        self._apply_tts_voice()

    def _apply_tts_voice(self):
        code = self.voice_combo.currentData()
        if not code:
            return
        try:
            self.tts.set_voice(code)
            self.settings.set("tts.voice", code)
            self.settings.set("tts.engine",
                              self.tts_engine_combo.currentData() or "edge")
            self.status_label.setText(f"Voice: {self.voice_combo.currentText()}")
            logger.info(f"[TTS] Voice set: {code}")
        except Exception as e:
            logger.error(f"[TTS] set_voice failed: {e}")

    # ── Голос персонажа (по имени говорящего из OCR) ──

    def _update_person_panel(self, speaker):
        """Обновляет панель «Персонаж» при новом имени из OCR."""
        name = (speaker or "").strip() or None
        if name == getattr(self, "_person_speaker", None) and \
                self.person_voice_combo.count() > 0:
            return  # имя не изменилось — combo уже актуален
        self._person_speaker = name
        self.person_label.setText(f"Персонаж: {name}" if name else "Персонаж: —")
        self._fill_person_voice_combo(name)

    def _fill_person_voice_combo(self, name):
        combo = self.person_voice_combo
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("Основной голос", None)
        for v in getattr(self, "_all_voices", []):
            combo.addItem(v.get("name", v.get("code", "")), v.get("code", ""))
        cur = self.tts.get_character_voice(name) if name else None
        idx = 0
        if cur:
            for i in range(combo.count()):
                if combo.itemData(i) == cur:
                    idx = i
                    break
        combo.setCurrentIndex(idx)
        combo.blockSignals(False)
        combo.setEnabled(bool(name))
        combo.setToolTip(f"Голос для «{name}»" if name
                         else "Озвучивается основным голосом")

    def _on_person_voice_changed(self, _idx):
        name = getattr(self, "_person_speaker", None)
        if not name:
            return
        code = self.person_voice_combo.currentData()
        try:
            self.tts.set_character_voice(name, code)
            self.status_label.setText(
                f"Голос «{name}»: {self.person_voice_combo.currentText()}")
        except Exception as e:
            logger.error(f"[TTS] set_character_voice failed: {e}")

    # ═══════════════════════════════════════════════
    # ACTIONS
    # ═══════════════════════════════════════════════
    def _on_scan(self):
        logger.info("[GUI] Scan button pressed")
        self.status_label.setText("Scanning...")
        self.btn_scan.setEnabled(False)
        threading.Thread(target=self._do_scan, daemon=True).start()

    def _do_scan(self):
        logger.info("[GUI] _do_scan thread started")
        try:
            import mss, time
            from PIL import Image
            regions = self.regions if self.regions else [{"x": 0, "y": 0, "width": 800, "height": 200}]
            r = regions[0]
            logger.debug(f"[GUI] Capturing region: {r}")
            with mss.mss() as sct:
                monitor = {"left": r["x"], "top": r["y"], "width": r["width"], "height": r["height"]}
                shot = sct.grab(monitor)
                img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
            logger.debug(f"[GUI] Captured image: {img.size}")
            self._pending_preview = img
            self._gui(lambda: self._show_preview(self._pending_preview))
            self._gui(lambda: self.status_label.setText("OCR processing..."))
            t0 = time.perf_counter()
            # Регион кроем сами — сообщаем офсет, иначе рамки KOMA
            # рисуются в локальных координатах кропа (выше/левее текста).
            self.ocr.set_capture_origin(r["x"], r["y"])
            result = self.ocr.recognize(img)
            elapsed = time.perf_counter() - t0
            text = result[0] if isinstance(result, tuple) else str(result)
            logger.info(f"[GUI] OCR done in {elapsed:.1f}s: {text[:50]}...")
            # force_speak=True: ручной Scan озвучивает ВСЕГДА (даже тот же
            # текст и даже при включённом Live) — кнопка Scan = «скажи сейчас».
            self._gui(lambda: self._on_text_detected(text, force_speak=True))
            self._gui(lambda: self.status_label.setText(f"Done in {elapsed:.1f}s | {len(text)} chars"))
        except Exception as e:
            logger.error(f"Scan error: {e}")
            self._gui(lambda: self.status_label.setText(f"Error: {e}"))
        finally:
            self._gui(lambda: self.btn_scan.setEnabled(True))

    def _ensure_live_loop(self):
        """Выделенный asyncio event loop для LiveScanner.

        start()/stop() и TTS-стриминг — корутины. Без работающего loop'а
        кнопка Live лишь создавала coroutine и ничего не запускала
        («coroutine never awaited») — live-режим в компактном GUI не работал.
        """
        loop = getattr(self, "_live_loop", None)
        if loop is not None and not loop.is_closed():
            return loop
        import asyncio
        loop = asyncio.new_event_loop()
        threading.Thread(target=loop.run_forever, name="LiveScannerLoop",
                         daemon=True).start()
        self._live_loop = loop
        logger.info("[LIVE] Выделенный event loop запущен")
        return loop

    def _live_submit(self, coro):
        """Поставить корутину сканера в его event loop."""
        try:
            import asyncio
            loop = self._ensure_live_loop()
            asyncio.run_coroutine_threadsafe(coro, loop)
        except Exception as e:
            logger.error(f"[LIVE] Не удалось запустить корутину: {e}")

    def _on_live_toggle(self, checked):
        logger.info(f"[GUI] Live toggle: checked={checked}")
        if checked:
            self._sync_ocr_region()   # страховка: regions[0] → ocr.region
            self._live_submit(self.scanner.start())
            self.status_label.setText("Live mode ON")
            self.btn_live.setText(f"{ICON['live_off']} Stop")
        else:
            self._live_submit(self.scanner.stop())
            self.status_label.setText("Live mode OFF")
            self.btn_live.setText(f"{ICON['live_on']} Live")

    def _on_stop_live(self):
        logger.info("[GUI] Stop Live requested")
        self._live_submit(self.scanner.stop())
        self.btn_live.setChecked(False)
        self.btn_live.setText(f"{ICON['live_on']} Live")
        self.status_label.setText("Live stopped")

    # ═══════════════════════════════════════════════
    # РАМКИ OCR (как в читалке KOMA / yomikai)
    # ═══════════════════════════════════════════════

    def _ensure_boxes_overlay(self):
        ov = getattr(self, "_boxes_overlay", None)
        if ov is None:
            from koma_box_overlay import KomaBoxOverlay
            ov = KomaBoxOverlay(ttl_sec=4.0)
            self._boxes_overlay = ov
        return ov

    def _wire_ocr_hooks(self):
        """Хуки OCRWrapper → GUI. Вызывать после КАЖДОГО создания обёртки
        (init и смена движка — там она пересоздаётся)."""
        if getattr(self, "ocr", None) is None:
            return
        # Придёт из фонового потока — маршалим в главный через сигнал.
        self.ocr.on_boxes = lambda boxes: self._gui(
            lambda b=boxes: self._on_ocr_boxes(b))

    def _on_ocr_boxes(self, boxes):
        if not self.settings.get("ocr.show_boxes", True):
            return
        try:
            self._ensure_boxes_overlay().set_boxes(boxes)
        except Exception as e:
            logger.debug(f"[Boxes] overlay: {e}")

    def _toggle_boxes_overlay(self, checked):
        self.settings.set("ocr.show_boxes", bool(checked))
        ov = getattr(self, "_boxes_overlay", None)
        if checked:
            self.status_label.setText("Рамки OCR: ON")
        else:
            if ov is not None:
                ov.clear()
            self.status_label.setText("Рамки OCR: OFF")

    # ═══════════════════════════════════════════════
    # AUTO-READ: реальное время — скан + сразу озвучка
    # ═══════════════════════════════════════════════

    def _toggle_auto_read(self, checked=None):
        """Включение/выключение режима Auto-Read (F7).
        Непрерывно сканирует экран через GlensOCR и сразу озвучивает распознанный текст."""
        if checked is None:
            checked = not self._auto_read_active

        if checked:
            self._start_auto_read()
        else:
            self._stop_auto_read()

    def _start_auto_read(self):
        """Запуск Auto-Read: скан каждые N мс + TTS каждого нового текста."""
        if self._auto_read_active:
            return
        if not hasattr(self, 'ocr') or not self.ocr:
            self.status_label.setText("Auto-Read: OCR not ready")
            return

        self._auto_read_active = True
        self._auto_read_last_text = ""
        # Debounce: текст должен повториться 2 скана подряд, иначе
        # озвучивались бы фрагменты постепенной печати диалога.
        self._auto_read_pending = ""
        self._auto_read_pending_count = 0

        # Визуальная индикация — кнопка "горит"
        self.btn_auto_read.setChecked(True)
        self.btn_auto_read.setText(f"⏹ Stop")
        self.btn_auto_read.setStyleSheet(
            "QPushButton { background: #0d5a0d; color: #4cff4c; border: 2px solid #4cff4c; border-radius: 5px; font-weight: bold; }"
            "QPushButton:hover { background: #0a4a0a; }"
        )
        self.auto_read_led.setStyleSheet("color: #4cff4c; font-size: 14px;")
        self.auto_read_led.setToolTip("Auto-Read: ON — сканирует и озвучивает")

        # Показать панель статус-иконок
        if hasattr(self, 'status_icons'):
            self.status_icons.show_all_off()
            self.status_icons.show()
            self.status_icons.raise_()

        # Показать окно предпросмотра игры
        if hasattr(self, 'game_preview'):
            if self.regions:
                self.game_preview.set_region(self.regions[0])
            self.game_preview.show()
            self.game_preview.raise_()

        # Запуск TTS стриминга (в выделенном loop'е сканера; раньше блок был
        # no-op — get_event_loop() в главном потоке Qt не даёт работающий loop)
        if hasattr(self, 'scanner') and self.scanner:
            try:
                self._live_submit(self.scanner.start_tts_streaming())
            except Exception:
                pass

        # Таймер сканирования (каждые 800 мс)
        interval = self.interval.value() if hasattr(self, 'interval') else 800
        # Для GlensOCR (联网) — не чаще 1 раза в секунду
        interval = max(interval, 1000)
        self._auto_read_timer.start(interval)
        self.status_label.setText(f"Auto-Read: ON — скан каждые {interval}мс")

        # ══════ Запуск Voice Translator (VAD + STT) для лайв-звука ══════
        audio_lang = self.settings.get("game.audio_language", "auto")
        dual = self.settings.get("game.auto_repeat_en", False)
        vad = self.settings.get("game.voice_activity_detection", True)
        if audio_lang != "off":
            try:
                from voice_translator import voice_translator
                self._voice_translator = voice_translator
                voice_translator.tts = self.tts
                if audio_lang in ("en", "auto"):
                    voice_translator.set_languages("en", "ru")
                else:
                    voice_translator.set_languages(audio_lang, "ru")
                voice_translator.set_dual_mode(dual)
                voice_translator.set_vad(vad)
                voice_translator.on_voice_detected = lambda is_v: self._gui(
                    lambda iv=is_v: self.status_icons.pulse("voice", 500) if iv else None)
                voice_translator.start()
                logger.info(f"[AUTO-READ] VoiceTranslator started (audio={audio_lang}, dual={dual}, vad={vad})")
            except Exception as e:
                logger.warning(f"[AUTO-READ] VoiceTranslator start failed: {e}")

        logger.info(f"[AUTO-READ] Запущен (interval={interval}ms)")

    def _stop_auto_read(self):
        """Остановка Auto-Read."""
        if not self._auto_read_active:
            return

        self._auto_read_active = False
        self._auto_read_timer.stop()

        # Визуальная индикация — кнопка погасла
        self.btn_auto_read.setChecked(False)
        self.btn_auto_read.setText(f"▶ Speak")
        self.btn_auto_read.setStyleSheet(
            "QPushButton { background: #1a3a1a; color: #777; border: 2px solid #333; border-radius: 5px; font-weight: bold; }"
            "QPushButton:hover { background: #1e4a1e; }"
            "QPushButton:checked { background: #0d5a0d; color: #4cff4c; border-color: #4cff4c; }"
        )
        self.auto_read_led.setStyleSheet("color: #333; font-size: 14px;")
        self.auto_read_led.setToolTip("Auto-Read: OFF")

        # Скрыть панель иконок
        if hasattr(self, 'status_icons'):
            self.status_icons.show_all_off()
            self.status_icons.hide()

        # Скрыть окно предпросмотра
        if hasattr(self, 'game_preview'):
            self.game_preview.hide()

        # Остановка TTS
        if hasattr(self, 'tts') and self.tts:
            try:
                self.tts.force_stop()
            except Exception:
                pass

        # Остановка стриминга (в выделенном loop'е сканера)
        if hasattr(self, 'scanner') and self.scanner:
            try:
                self._live_submit(self.scanner.stop_tts_streaming())
            except Exception:
                pass

        # ══════ Остановка Voice Translator ══════
        if hasattr(self, '_voice_translator') and self._voice_translator:
            try:
                self._voice_translator.stop()
                logger.info("[AUTO-READ] VoiceTranslator stopped")
            except Exception as e:
                logger.warning(f"[AUTO-READ] VoiceTranslator stop error: {e}")

        self.status_label.setText("Auto-Read: OFF")
        logger.info("[AUTO-READ] Остановлен")

    def _auto_read_tick(self):
        """тик Auto-Read: захват + OCR + перевод + озвучка.

        Защита от накопления потоков: раньше каждый тик (интервал 500 мс)
        порождал новый поток, даже если предыдущий скан ещё шёл. При
        скане 1.5-4 c это давало 3-8 параллельных OCR — интерфейс
        «зависал», а CPU грузился постоянно (и игра теряла FPS).
        """
        if not self._auto_read_active:
            return
        if getattr(self, "_auto_read_busy", False):
            return    # предыдущий скан ещё идёт — не плодим потоки
        # минимальный пауза между сканами, даже если таймер бьёт чаще
        now = time.time()
        if now - getattr(self, "_auto_read_last", 0.0) < self._auto_read_gap:
            return
        self._auto_read_last = now
        self._auto_read_busy = True
        threading.Thread(target=self._auto_read_scan, daemon=True).start()

    def _auto_read_scan(self):
        """Сканирование одного кадра для Auto-Read (в фоновом потоке).

        Иконки мигают на каждом этапе:
          🎬 capture  — захват кадра
          🔍 scan     — OCR running
          ✅ recognize — текст получен
          📦 buffer   — TTS поставлен в очередь
          🎵 voice    — голос звучит
        """
        try:
            import mss, time
            import re
            from scanner import ScreenScanner
            from PIL import Image
            import numpy as np

            # ══════ 🎬 ЗАХВАТ КАДРА ══════
            self._gui(lambda: self.status_icons.pulse("capture", 400))

            img = None
            if hasattr(self.ocr, 'capture_region'):
                img = self.ocr.capture_region()
            if img is None:
                regions = self.regions if self.regions else [{"x": 0, "y": 0, "width": 800, "height": 200}]
                r = regions[0]
                with mss.mss() as sct:
                    monitor = {"left": r["x"], "top": r["y"], "width": r["width"], "height": r["height"]}
                    shot = sct.grab(monitor)
                    img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
                # capture_region() выставил бы сам; для ручного кропа — явно:
                self.ocr.set_capture_origin(r["x"], r["y"])

            if img is None:
                return

            # Обновить окно предпросмотра
            self._gui(lambda i=img: self._update_game_preview(i))

            # ══════ ЗАЩИТА ОТ АНИМАЦИЙ / РАЗМЫТИЯ ══════

            # 1) Детекция размытия (Laplacian variance)
            #    Низкое значение = кадр размыт (анимация, переход)
            gray = np.array(img.convert("L"), dtype=np.float32)
            # Уменьшаем для быстрого расчёта
            small = gray[::4, ::4]  # 1/4 размера
            laplacian = np.array([
                [-1, -1, -1],
                [-1,  8, -1],
                [-1, -1, -1]
            ], dtype=np.float32)
            h, w = small.shape
            if h < 3 or w < 3:
                return
            try:
                from scipy.signal import convolve2d
                lap = convolve2d(small, laplacian, mode='valid')
            except ImportError:
                # Fallback: ручная卷积 без scipy
                lap = np.zeros((h-2, w-2), dtype=np.float32)
                for i in range(1, h-1):
                    for j in range(1, w-1):
                        lap[i-1, j-1] = (
                            -small[i-1,j-1] - small[i-1,j] - small[i-1,j+1]
                            -small[i,j-1]   + 8*small[i,j]  - small[i,j+1]
                            -small[i+1,j-1] - small[i+1,j]  - small[i+1,j+1]
                        )
            blur_score = float(np.var(lap))

            # Порог размытия: если < 500 — кадр размыт (анимация/переход)
            # Нормальный экран: score > 5000, лёгкое размытие: 200-1000, сильное: < 50
            BLUR_THRESHOLD = 500.0
            if blur_score < BLUR_THRESHOLD:
                logger.debug(f"[AUTO-READ] Кадр размыт (blur={blur_score:.1f}), пропускаем")
                return

            # 2) Сравнение с предыдущим кадром (стабильность)
            #    Если кадр сильно отличается — идёт переход/анимация
            if not hasattr(self, '_auto_read_prev_frame'):
                self._auto_read_prev_frame = small.copy()
            else:
                diff = float(np.mean(np.abs(small - self._auto_read_prev_frame)))
                self._auto_read_prev_frame = small.copy()

                # Если разница > 30 — кадр резко изменился (переход)
                TRANSITION_THRESHOLD = 30.0
                if diff > TRANSITION_THRESHOLD:
                    logger.debug(f"[AUTO-READ] Переход detected (diff={diff:.1f}), пропускаем")
                    return

            # ══════ 🔍 OCR ══════
            self._gui(lambda: self.status_icons.pulse("scan", 600))

            t0 = time.perf_counter()
            result = self.ocr.recognize(img)
            elapsed = time.perf_counter() - t0
            text = result[0] if isinstance(result, tuple) else str(result)
            confidence = result[1] if isinstance(result, tuple) and len(result) > 1 else 0.0

            if not text or not text.strip():
                return

            # 3) Фильтрация по confidence: GlensOCR возвращает 95 при успехе,
            #    мусорный текст будет иметь низкий confidence
            if confidence < 30.0:
                logger.debug(f"[AUTO-READ] Низкий confidence ({confidence:.0f}%), пропускаем")
                return

            # 4) Фильтрация мусора: строки < 3 символов или全是 спецсимволы
            lines = text.strip().split('\n')
            clean_lines = []
            for line in lines:
                stripped = line.strip()
                if len(stripped) < 3:
                    continue
                alpha_count = sum(1 for c in stripped if c.isalpha())
                if alpha_count < 2:
                    continue
                clean_lines.append(stripped)
            text = '\n'.join(clean_lines)
            if not text.strip():
                return

            # ══════ АВТО-ПОЧИНКА МУСОРА ══════

            raw_text = text

            # Полный пайплайн очистки OCR (lookalikes, переносы, мусорные токены)
            try:
                from ocr_text_cleaner import (
                    full_clean_pipeline, normalize_alphabets,
                    fix_lookalikes_per_word, filter_garbage_tokens,
                )
                text = full_clean_pipeline(text, engine_type="google_lens")
            except ImportError:
                pass

            # Доп. фиксы из ocr_wrapper: гомоглифы, изолированная латиница, регистр
            try:
                from ocr_wrapper import OCRWrapper
                tmp = OCRWrapper(self.settings)
                text = tmp._normalize_alphabets(text)
                text = tmp._strip_isolated_latin(text)
                text = tmp._filter_by_language(text)
            except Exception:
                pass

            # Русские OCR-ошибки: "тадовати" → "Тадовати", "нагpaды" → "Награды"
            try:
                from ocr_wrapper import _apply_russian_fixes, _apply_known_corrections
                text = _apply_russian_fixes(text)
                text = _apply_known_corrections(text)
            except ImportError:
                pass

            # ══════ ЯЗЫКОВОЙ ФИЛЬТР ══════
            # Если text_language="ru" — оставляем только кириллицу + цифры
            # Если text_language="auto" — всё как есть (лайв-режим, читаем всё из игры)
            game_text_lang = self.settings.get("game.text_language", "ru")
            if game_text_lang == "ru":
                filtered_lines = []
                for line in text.split('\n'):
                    stripped = line.strip()
                    # Оставляем строки где есть кириллица
                    has_cyrillic = any('\u0400' <= c <= '\u04ff' for c in stripped)
                    if has_cyrillic:
                        # Убираем чисто латинские слова (но цифры и знаки оставляем)
                        words = stripped.split()
                        kept = []
                        for w in words:
                            is_latin_only = all(c.isascii() and c.isalpha() for c in w)
                            if not is_latin_only:
                                kept.append(w)
                        if kept:
                            filtered_lines.append(' '.join(kept))
                text = '\n'.join(filtered_lines)
                if not text.strip():
                    return

            # Если после починки текст стал пустым — берём raw
            if not text or not text.strip():
                text = raw_text

            # Убираем пустые строки после починки
            text = '\n'.join(l for l in text.split('\n') if l.strip())
            if not text.strip():
                return

            # ══════ ДЕДУПЛИКАЦИЯ ══════

            normalized = ' '.join(text.strip().lower().split())
            # Не точное сравнение, а similarity без пунктуации: OCR
            # нестабилен (score ~0.85), один кадр даёт slightly другой
            # текст → точное == никогда не совпадало → повторная озвучка.
            dedup_key = ' '.join(re.sub(r'[^\w\s]', ' ', normalized).split())

            # Уже озвучивалось (или это обрезок уже озвученного кадра)?
            if dedup_key and self._auto_read_last_text:
                if ScreenScanner._similarity(dedup_key, self._auto_read_last_text) > 0.75:
                    return
                if len(dedup_key) >= 15 and dedup_key in self._auto_read_last_text:
                    return

            # Debounce: ждём стабилизации текста 2 скана подряд —
            # иначе озвучивались бы фрагменты постепенной печати.
            if dedup_key:
                if (self._auto_read_pending
                        and ScreenScanner._similarity(dedup_key, self._auto_read_pending) > 0.75):
                    self._auto_read_pending_count += 1
                else:
                    self._auto_read_pending = dedup_key
                    self._auto_read_pending_count = 0
                if self._auto_read_pending_count < 1:
                    return  # ждём подтверждения следующим сканом

            self._auto_read_last_text = dedup_key or normalized
            self._auto_read_pending = ""
            self._auto_read_pending_count = 0

            # ✅ Текст распознан и починен
            self._gui(lambda: self.status_icons.pulse("recognize", 500))

            # Показ очищенного текста в GUI
            self._gui(lambda t=text: self._on_text_detected(t))
            self._gui(lambda t=text, c=confidence, e=elapsed:
                      self.status_label.setText(f"Auto-Read: {e:.1f}s | {len(t)} chars | conf={c:.0f}%"))

            # 📦 Буферизация TTS
            if self._auto_read_active:
                self._gui(lambda: self.status_icons.pulse("buffer", 500))
                try:
                    if hasattr(self.tts, 'is_playing') and self.tts.is_playing:
                        self.tts.force_stop()
                    threading.Thread(
                        target=lambda t=text: self._safe_speak(t),
                        daemon=True
                    ).start()
                except Exception as e:
                    logger.error(f"[AUTO-READ] TTS error: {e}")

        except Exception as e:
            logger.error(f"[AUTO-READ] Scan error: {e}")
        finally:
            # освобождаем защиту: следующий тик сможет запустить скан
            self._auto_read_busy = False

    def _safe_speak(self, text):
        """Безопасная озвучка в отдельном потоке. Мигает 🎵 voice пока говорит.

        Замедление берётся из game.tts_rate_auto_read (по умолчанию -30).
        """
        try:
            # 🎵 Voice ON
            self._gui(lambda: self.status_icons.pulse("voice", 3000))

            # Замедление для авто-чтения из конфига
            auto_rate = self.settings.get("game.tts_rate_auto_read", -30)

            # Запоминаем текущую скорость и замедляем для авто-чтения
            old_rate = self.tts.rate
            old_role_rates = {}
            for role in getattr(self.tts, 'role_settings', {}):
                old_role_rates[role] = self.tts.role_settings[role].get("rate", 0)
                self.tts.role_settings[role]["rate"] = auto_rate

            self.tts.rate = auto_rate

            try:
                import asyncio
                loop = asyncio.new_event_loop()
                loop.run_until_complete(self.tts.speak(text))
                loop.close()
            finally:
                # Восстанавливаем скорость
                self.tts.rate = old_rate
                for role, r in old_role_rates.items():
                    if role in self.tts.role_settings:
                        self.tts.role_settings[role]["rate"] = r

        except Exception as e:
            logger.error(f"[AUTO-READ] speak error: {e}")

    # ═══════════════════════════════════════════════

    def _on_speak(self):
        logger.info("[GUI] Speak button pressed")
        # Ручная озвучка — без имени говорящего (префикс только для показа),
        # но speaker передаём для выбора голоса персонажа (tts.characters).
        text = getattr(self, "_last_clean_text", None) or self.result_text.toPlainText()
        speaker = (getattr(self.ocr, "last_speaker", None)
                   if getattr(self, "ocr", None) else None)
        if text:
            logger.info(f"[GUI] Speaking text: {text[:50]}...")
            self._gui(lambda: self._update_tts_icon())
            threading.Thread(target=lambda: self._speak_and_reset(text, speaker),
                             daemon=True).start()
        else:
            logger.warning("[GUI] Speak pressed but no text to speak")

    def _speak_and_reset(self, text, speaker=None):
        logger.info(f"[GUI] _speak_and_reset started for: {text[:50]}...")
        try:
            import asyncio
            loop = asyncio.new_event_loop()
            try:
                # speaker → голос персонажа из tts.characters (если назначен);
                # неизвестное имя → основной голос, ничего не ломается.
                loop.run_until_complete(self.tts.speak(text, speaker=speaker))
                logger.info("[GUI] _speak_and_reset completed")
            finally:
                loop.close()
        except Exception as e:
            logger.error(f"[TTS] speak error: {e}")
        finally:
            self._gui(lambda: self._update_tts_icon())

    def _speak_text(self, text, lang="ru"):
        """Озвучить текст в отдельном потоке (для кнопки Speak RU и т.д.)."""
        if not text or not text.strip():
            return
        threading.Thread(target=lambda: self._speak_and_reset(text), daemon=True).start()

    def _on_stop_speak(self):
        if hasattr(self.tts, 'force_stop'):
            self.tts.force_stop()
        self._update_tts_icon()

    def _set_stt_icon(self, active: bool):
        """Mic icon: red glowing when STT is ON, dim gray when OFF."""
        if active:
            self.stt_icon.setStyleSheet("color: #f44336; font-size: 18px;")
            self.stt_icon.setToolTip("STT: listening")
            self.btn_stt.setText(f"{ICON['stt_stop']} Stop STT")
        else:
            self.stt_icon.setStyleSheet("color: #555555; font-size: 18px;")
            self.stt_icon.setToolTip("STT: off")
            self.btn_stt.setText(f"{ICON['stt']} Start STT")

    def _toggle_stt(self, checked=False):
        """Start/Stop STT: потоковый faster-whisper (чанки + VAD).

        Модель и цикл распознавания живут в фоновых потоках
        (voice_translator), UI никогда не блокируется.
        """
        try:
            self.btn_stt.blockSignals(True)
            self.btn_stt.setChecked(bool(checked))
        finally:
            self.btn_stt.blockSignals(False)
        self._set_stt_icon(bool(checked))
        if checked:
            self.status_label.setText("STT ON — загрузка модели в фоне...")
            threading.Thread(target=self._start_stt, daemon=True).start()
        else:
            vt = getattr(self, "_vt", None)
            if vt is not None:
                try:
                    vt.stop()
                except Exception:
                    logger.exception("[STT] stop failed")
            self.status_label.setText("STT OFF")

    def _start_stt(self):
        """Фоновая инициализация STT (whisper грузится вне UI-потока)."""
        try:
            from voice_translator import VoiceTranslator
            if getattr(self, "_vt", None) is None:
                vt = VoiceTranslator(tts_engine=None, translator=None)
                vt._model_name = "base"  # tiny плохо читает кириллицу
                # Чистый STT: эхо-фильтр «язык речи == язык озвучки» здесь
                # неуместен — озвучки нет, а русскую речь он бы отбрасывал.
                vt._skip_own_lang = False
                # on_heard приходит из STT-потока → прыгаем в UI через _gui
                vt.on_heard = lambda text, lang, spk: self._gui(
                    lambda: self._stt_append(text, lang, spk))
                self._vt = vt
            self._vt.start()
            self._gui(lambda: self.status_label.setText("STT ON — listening..."))
            logger.info("[STT] started (streaming chunks + VAD)")
        except Exception as e:
            logger.error(f"[STT] start failed: {e}", exc_info=True)
            self._gui(lambda: self.status_label.setText(f"STT error: {e}"))

    def _stt_append(self, text, lang, spk):
        """Вывод распознанной реплики в панель STT (вызывается в UI-потоке)."""
        try:
            tag = " ".join(x for x in (lang, spk) if x)
            self.stt_text.append((f"[{tag}] " if tag else "") + text)
        except Exception:
            logger.exception("[STT] append failed")

    def _on_translate(self):
        # Ручной перевод — чистым текстом (имена говорящих не переводим).
        text = getattr(self, "_last_clean_text", None) or self.result_text.toPlainText()
        if text:
            threading.Thread(target=lambda: self._do_translate(text), daemon=True).start()

    def _do_translate(self, text):
        try:
            from translator import Translator
            tr = Translator()
            clean = self._clean_ocr(text)
            if clean:
                ru = tr.translate(clean, src="en", dst="ru")
                if ru and not ru.startswith("[Translation"):
                    self._gui(lambda: self.translate_text.setPlainText(ru))
        except Exception as e:
            logger.error(f"Translation error: {e}")

    def _clean_ocr(self, text):
        import re
        lines = text.split('\n')
        cleaned = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            if re.search(r'[а-яА-ЯёЁ]', line):
                continue
            line = re.sub(r'^\d{1,2}:\d{2}(:\d{2})?[\.,]?\d*\s*', '', line)
            if not re.search(r'[a-zA-Z]{2,}', line):
                continue
            cleaned.append(line.strip())
        return '\n'.join(cleaned)

    def _on_text_detected(self, text, force_speak=False):
        if text:
            # Сохраняем очищенный текст (<имя:) — только для показа; на TTS и
            # на переводы (без префикса переводчика) он не попадает.
            self._last_clean_text = text
            speaker = (getattr(self.ocr, "last_speaker", None)
                       if getattr(self, "ocr", None) else None)
            display = f"{speaker}: {text}" if speaker else text
            live_on = (getattr(self, "scanner", None) is not None
                       and getattr(self.scanner, "running", False))
            if live_on:
                # Live: текст дописывается в поле постепенно —
                # «как будто нейронка печатает в прямом эфире».
                self._set_text_animated(display)
            else:
                self.result_text.setPlainText(display)
            self._update_person_panel(speaker)
            if self.auto_translate.isChecked():
                self._do_translate(text)
            if self.kb_check.isChecked():
                self.kb_widget.animate_text(display, delay_ms=25)
            # Auto Speak: кроме ручного Scan (force_speak=True) звучит всегда -
            # иначе live читает сам, и повтор того же текста молчит впустую.
            # Без force_speak — только если НЕ live/auto-read (там озвучка
            # идёт своим потоком из текста, который уже показан).
            if getattr(self, "auto_speak", None) and self.auto_speak.isChecked():
                auto_read_on = getattr(self, "_auto_read_active", False)
                if force_speak or (not live_on and not auto_read_on):
                    if force_speak and hasattr(self.tts, "force_stop"):
                        try:
                            self.tts.force_stop()
                        except Exception:
                            pass
                    threading.Thread(
                        target=lambda t=text, s=speaker:
                            self._speak_and_reset(t, s),
                        daemon=True).start()

    def _set_text_animated(self, text):
        """Live-показ: текст дописывается в поле постепенно (typing-эффект).

        Дописывание (новый текст начинается со старого) — печатается
        только хвост; полная смена — набор с нуля. Уже напечатанное
        не перерисовывается (кадры live приходят с одним и тем же текстом).
        """
        try:
            cur = self.result_text.toPlainText()
        except Exception:
            cur = ""
        if text == cur:
            return
        if (getattr(self, "_typing_text", None) == text
                and getattr(self, "_typing_timer", None) is not None
                and self._typing_timer.isActive()):
            return  # уже печатается именно этот текст
        if text.startswith(cur) and len(text) > len(cur):
            self._typing_pos = len(cur)          # продолжаем с хвоста
        else:
            self._typing_pos = 0                  # новая реплика — с нуля
        self._typing_text = text
        if getattr(self, "_typing_timer", None) is None:
            from PyQt6.QtCore import QTimer
            self._typing_timer = QTimer(self)
            self._typing_timer.setInterval(15)
            self._typing_timer.timeout.connect(self._typing_step)
        if not self._typing_timer.isActive():
            self._typing_timer.start()

    def _typing_step(self):
        text = getattr(self, "_typing_text", "") or ""
        pos = min(getattr(self, "_typing_pos", 0) + 3, len(text))
        self._typing_pos = pos
        try:
            self.result_text.setPlainText(text[:pos])
            sb = self.result_text.verticalScrollBar()
            sb.setValue(sb.maximum())
        except Exception:
            pass
        if pos >= len(text):
            self._typing_timer.stop()

    def _show_preview(self, pil_img):
        from PyQt6.QtGui import QImage, QPixmap
        try:
            img = pil_img.copy()
            img.thumbnail((600, 280))
            data = img.tobytes("raw", "RGB")
            qimg = QImage(data, img.width, img.height, 3 * img.width, QImage.Format.Format_RGB888)
            pixmap = QPixmap.fromImage(qimg.copy())
            self.preview_label.setPixmap(pixmap)
            self.preview_label.setStyleSheet("border: 1px solid #3c3c3c; border-radius: 4px;")
        except Exception as e:
            logger.error(f"Preview error: {e}")

    def _on_save_txt(self):
        text = self.result_text.toPlainText()
        if text:
            path = os.path.join(os.path.dirname(__file__), "screenshots", "ocr_result.txt")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w', encoding='utf-8') as f:
                f.write(text)
            self.status_label.setText(f"Saved: {path}")

    def _select_process(self):
        pass

    def _add_region(self):
        from PyQt6.QtWidgets import QInputDialog
        x, ok = QInputDialog.getInt(self, "New Region", "X:", 100, 0, 9999)
        if not ok:
            return
        y, _ = QInputDialog.getInt(self, "New Region", "Y:", 100, 0, 9999)
        w, _ = QInputDialog.getInt(self, "New Region", "Width:", 900, 50, 9999)
        h, _ = QInputDialog.getInt(self, "New Region", "Height:", 200, 20, 9999)
        self.regions.append({"x": x, "y": y, "width": w, "height": h, "name": f"Region {len(self.regions) + 1}"})
        self._save_regions()
        self.region_overlay.set_regions(self.regions)
        self.region_overlay.show_overlay()

    def _edit_region(self, index=None):
        if index is None:
            items = self.region_list.selectedItems()
            if items:
                index = self.region_list.row(items[0])
            else:
                return
        if isinstance(index, bool):
            return
        self._edit_region_by_idx(index)

    def _delete_region(self, index=None):
        if index is None:
            items = self.region_list.selectedItems()
            if items:
                index = self.region_list.row(items[0])
            else:
                return
        if isinstance(index, bool):
            return
        self._delete_region_by_idx(index)

    def _edit_region_by_idx(self, index):
        if index < 0 or index >= len(self.regions):
            return
        region = self.regions[index]
        from PyQt6.QtWidgets import QInputDialog
        x, ok = QInputDialog.getInt(self, "Edit Region X", "X:", region["x"], 0, 9999)
        if ok:
            region["x"] = x
        y, ok = QInputDialog.getInt(self, "Edit Region Y", "Y:", region["y"], 0, 9999)
        if ok:
            region["y"] = y
        w, ok = QInputDialog.getInt(self, "Edit Region Width", "Width:", region["width"], 50, 9999)
        if ok:
            region["width"] = w
        h, ok = QInputDialog.getInt(self, "Edit Region Height", "Height:", region["height"], 20, 9999)
        if ok:
            region["height"] = h
        self._save_regions()
        self.region_overlay.set_regions(self.regions)

    def _delete_region_by_idx(self, index):
        if index < 0 or index >= len(self.regions):
            return
        from PyQt6.QtWidgets import QMessageBox
        region = self.regions[index]
        reply = QMessageBox.question(
            self, "Delete Region",
            f"Delete '{region.get('name', f'Region {index + 1}')}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            del self.regions[index]
            self._save_regions()
            self.region_overlay.set_regions(self.regions)

    def _on_gui_invoke(self, fn):
        try:
            fn()
        except Exception as e:
            logger.error(f"[GUI] invoke error: {e}")

    def _gui(self, fn):
        self.gui_invoke.emit(fn)

    # ═══════════════════════════════════════════════
    # ГОРЯЧИЕ КЛАВИШИ
    # ═══════════════════════════════════════════════

    def keyPressEvent(self, event):
        """Обработка горячих клавиш (только когда окно в фокусе):
          F7 — Auto-Read
          F9 — однократное сканирование
          Escape — остановить Auto-Read

        Глобальные (из любого окна):
          Ctrl+Shift+F7 — Auto-Read toggle
          Ctrl+Shift+F8 — Stop
          Ctrl+Shift+F9 — Scan
        """
        key = event.key()
        if key == Qt.Key.Key_F7:
            self._toggle_auto_read()
        elif key == Qt.Key.Key_F9:
            self._on_scan()
        elif key == Qt.Key.Key_Escape:
            if self._auto_read_active:
                self._stop_auto_read()
        else:
            super().keyPressEvent(event)

    def _init_global_hotkeys(self):
        """Глобальные горячие клавиши через `keyboard` (работают из любого окна)."""
        self._global_hotkey_hooks = []
        try:
            import keyboard

            def _cb_toggle():
                # Вызываем из GUI потока
                self._gui(self._toggle_auto_read)

            def _cb_scan():
                self._gui(self._on_scan)

            def _cb_stop():
                if self._auto_read_active:
                    self._gui(self._stop_auto_read)
                if hasattr(self.tts, 'is_playing') and self.tts.is_playing:
                    self._gui(self.tts.force_stop)

            keyboard.add_hotkey('ctrl+shift+f7', _cb_toggle)
            keyboard.add_hotkey('ctrl+shift+f9', _cb_scan)
            keyboard.add_hotkey('ctrl+shift+f8', _cb_stop)
            self._global_hotkey_hooks = True
            logger.info("[HOTKEYS] Global: Ctrl+Shift+F7=Toggle, Ctrl+Shift+F8=Stop, Ctrl+Shift+F9=Scan")
        except ImportError:
            logger.warning("[HOTKEYS] `keyboard` not installed, global hotkeys disabled")
        except Exception as e:
            logger.warning(f"[HOTKEYS] Global hotkey init error: {e}")

    def _cleanup_global_hotkeys(self):
        """Отключение глобальных горячих клавиш."""
        try:
            import keyboard
            keyboard.unhook_all()
        except Exception:
            pass

    def closeEvent(self, event):
        # Останавливаем Auto-Read
        if self._auto_read_active:
            self._stop_auto_read()
        self._cleanup_global_hotkeys()

        # scanner.stop() — корутина. Подаём её в выделенный loop сканера,
        # чтобы цикл сканирования реально остановился до выхода.
        try:
            if hasattr(self, "scanner") and self.scanner is not None:
                import asyncio
                co = self.scanner.stop()
                if asyncio.iscoroutine(co):
                    loop = getattr(self, "_live_loop", None)
                    if loop is not None and loop.is_running():
                        fut = asyncio.run_coroutine_threadsafe(co, loop)
                        try:
                            fut.result(timeout=3)
                        except Exception:
                            pass
                        loop.call_soon_threadsafe(loop.stop)
                    else:
                        asyncio.run(co)
        except Exception as e:
            logger.debug(f"[close] scanner.stop: {e}")

        # Оверлей рамок — отдельное top-level окно: гасим при выходе
        if getattr(self, "_boxes_overlay", None) is not None:
            try:
                self._boxes_overlay.clear()
                self._boxes_overlay.hide()
            except Exception:
                pass

        if hasattr(self, "tray_icon"):
            try:
                self.tray_icon.hide()
            except Exception:
                pass
        try:
            if getattr(self, "_voice_float_lbl", None) is not None:
                self._voice_float_lbl.hide()
        except Exception:
            pass

        # ВАЖНО: оверлей и band — ОТДЕЛЬНЫЕ окна без родителя. Если их не
        # закрыть, Qt считает, что открытое окно ещё есть, и процесс не
        # завершается: окно закрыто, а python.exe продолжает висеть.
        for attr in ("region_overlay", "_select_band", "game_preview"):
            w = getattr(self, attr, None)
            if w is not None:
                try:
                    w.close()
                    w.deleteLater()
                except Exception:
                    pass

        event.accept()
        # подсказываем Qt, что выходить надо именно сейчас
        try:
            from PyQt6.QtWidgets import QApplication
            QApplication.instance().quit()
        except Exception:
            pass


def main():
    from PyQt6.QtWidgets import QApplication
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#1e1e1e"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#d4d4d4"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#1e1e1e"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#d4d4d4"))
    palette.setColor(QPalette.ColorRole.Button, QColor("#0e639c"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#ffffff"))
    app.setPalette(palette)

    window = CompactWindow()
    window.setStyleSheet(window._mort_theme())
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
