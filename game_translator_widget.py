# -*- coding: utf-8 -*-
"""Native PyQt6 Game Translator widget — replaces subprocess-based tkinter launcher.

Embeds LivePipeline, OneShotPipeline, OcrPipeline from game_voice_translator
directly into the MORT GUI as a native tab. Dark theme matches gui_mort.py.
"""
import os
import sys
import time
import queue
import logging
import threading
from functools import partial

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QLineEdit, QSpinBox, QDoubleSpinBox, QCheckBox, QRadioButton,
    QTextEdit, QGroupBox, QTabWidget, QScrollArea, QProgressBar,
    QSlider, QFileDialog, QTreeWidget, QTreeWidgetItem, QDialog,
    QGridLayout, QSplitter, QMessageBox, QFrame
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QObject
from PyQt6.QtGui import QFont, QColor, QTextCursor

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
#  Import game_voice_translator modules
# ---------------------------------------------------------------------------
GVT_DIR = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "game_voice_translator"))
if GVT_DIR not in sys.path:
    sys.path.insert(0, GVT_DIR)

try:
    import config as gvt_config
    import audio_capture
    import asr as gvt_asr
    import translator as gvt_translator
    import tts as gvt_tts
    import screen_capture as gvt_screen
    import ocr as gvt_ocr
    import process_utils
    from pipelines import LivePipeline, OneShotPipeline, OcrPipeline
    try:
        import replicate_avatar
        from qt6_avatar import QtAvatar
        EMOTIONS = replicate_avatar.EMOTIONS
        EMOTION_LABELS = replicate_avatar.EMOTION_LABELS
    except Exception:
        import replicate_avatar
        EMOTIONS = replicate_avatar.EMOTIONS
        EMOTION_LABELS = replicate_avatar.EMOTION_LABELS
    _GVT_OK = True
except Exception as e:
    logger.error("game_voice_translator modules not available: %s", e)
    _GVT_OK = False

# ---------------------------------------------------------------------------
#  Dark theme stylesheet (matches gui_mort.py)
# ---------------------------------------------------------------------------
_DARK = """
QGroupBox {
    border: 1px solid #3a3f4b;
    border-radius: 6px;
    margin-top: 12px;
    padding: 10px 8px 8px 8px;
    color: #c9d1d9;
    font-weight: bold;
    font-size: 12px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 6px;
}
QWidget {
    background: #161b22;
    color: #c9d1d9;
    font-family: 'Segoe UI', sans-serif;
    font-size: 12px;
}
QLabel { background: transparent; }
QPushButton {
    background: #21262d;
    color: #c9d1d9;
    border: 1px solid #3a3f4b;
    border-radius: 4px;
    padding: 5px 14px;
    font-weight: bold;
    min-height: 22px;
}
QPushButton:hover { background: #30363d; border-color: #58a6ff; }
QPushButton:pressed { background: #1a1e24; }
QPushButton:disabled { color: #484f58; border-color: #21262d; }
QPushButton#startBtn { background: #238636; border-color: #2ea043; }
QPushButton#startBtn:hover { background: #2ea043; }
QPushButton#stopBtn { background: #da3633; border-color: #f85149; }
QPushButton#stopBtn:hover { background: #f85149; }
QComboBox {
    background: #0d1117;
    color: #c9d1d9;
    border: 1px solid #3a3f4b;
    border-radius: 4px;
    padding: 4px 8px;
    min-height: 22px;
}
QComboBox:hover { border-color: #58a6ff; }
QComboBox::drop-down { border: none; width: 20px; }
QComboBox QAbstractItemView {
    background: #0d1117;
    color: #c9d1d9;
    selection-background-color: #1f6feb;
    border: 1px solid #3a3f4b;
}
QLineEdit, QSpinBox, QDoubleSpinBox {
    background: #0d1117;
    color: #c9d1d9;
    border: 1px solid #3a3f4b;
    border-radius: 4px;
    padding: 4px 6px;
    min-height: 22px;
}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {
    border-color: #58a6ff;
}
QTextEdit {
    background: #0d1117;
    color: #c9d1d9;
    border: 1px solid #3a3f4b;
    border-radius: 4px;
    font-family: 'Consolas', monospace;
    font-size: 11px;
}
QProgressBar {
    background: #0d1117;
    border: 1px solid #3a3f4b;
    border-radius: 4px;
    text-align: center;
    color: #c9d1d9;
    min-height: 16px;
}
QProgressBar::chunk { background: #1f6feb; border-radius: 3px; }
QCheckBox { spacing: 6px; background: transparent; }
QCheckBox::indicator {
    width: 16px; height: 16px;
    border: 1px solid #3a3f4b;
    border-radius: 3px;
    background: #0d1117;
}
QCheckBox::indicator:checked { background: #1f6feb; border-color: #1f6feb; }
QRadioButton { spacing: 6px; background: transparent; }
QRadioButton::indicator {
    width: 14px; height: 14px;
    border: 1px solid #3a3f4b;
    border-radius: 8px;
    background: #0d1117;
}
QRadioButton::indicator:checked { background: #58a6ff; border-color: #58a6ff; }
QSlider::groove:horizontal {
    background: #30363d;
    height: 4px;
    border-radius: 2px;
}
QSlider::handle:horizontal {
    background: #58a6ff;
    width: 14px; height: 14px;
    margin: -5px 0;
    border-radius: 7px;
}
QTabWidget::pane { border: 1px solid #3a3f4b; border-radius: 4px; background: #161b22; }
QTabBar::tab {
    background: #21262d;
    color: #8b949e;
    border: 1px solid #3a3f4b;
    border-bottom: none;
    padding: 6px 14px;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
    margin-right: 2px;
}
QTabBar::tab:selected { background: #161b22; color: #c9d1d9; }
QTabBar::tab:hover { background: #30363d; color: #c9d1d9; }
QScrollArea { border: none; background: transparent; }
QTreeWidget {
    background: #0d1117;
    color: #c9d1d9;
    border: 1px solid #3a3f4b;
    border-radius: 4px;
}
QTreeWidget::item:selected { background: #1f6feb; }
QSplitter::handle { background: #30363d; }
"""


# ===========================================================================
#  Adapter: makes our QWidget look like the tkinter App for pipelines
# ===========================================================================
class _TkAdapter:
    """Bridges the PyQt6 widget to the pipeline interface expected by
    LivePipeline/OneShotPipeline/OcrPipeline (which call app.post_log,
    app.after, app.tts_enabled.get, app.apply_avatar_emotion, etc.)."""

    def __init__(self, widget: 'GameTranslatorWidget'):
        self._w = widget

    def post_log(self, kind, text, level="info"):
        self._w.sig_log.emit(kind, text, level)

    def after(self, ms, func):
        QTimer.singleShot(max(0, int(ms)), func)

    @property
    def tts_enabled(self):
        return self._w._tts_enabled_adapter

    def apply_avatar_emotion(self, text):
        self._w._apply_avatar_emotion(text)

    def do_stop(self):
        QTimer.singleShot(0, self._w._do_stop)

    def on_pipeline_failed(self, reason):
        self._w.sig_pipeline_failed.emit(reason)


class _BoolProxy:
    """Mimics tkinter.BooleanVar for pipeline compatibility."""
    def __init__(self, getter):
        self._getter = getter
    def get(self):
        return self._getter()


# ===========================================================================
#  Process selection dialog (PyQt6)
# ===========================================================================
class ProcessDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Select Game Process")
        self.setMinimumSize(520, 420)
        self.result = None
        self.setStyleSheet(_DARK)

        layout = QVBoxLayout(self)

        # Search
        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("Search:"))
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Type exe name...")
        self.search_edit.textChanged.connect(self._populate)
        search_row.addWidget(self.search_edit)
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self._populate)
        search_row.addWidget(refresh_btn)
        layout.addLayout(search_row)

        # Tree
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["PID", "Process Name"])
        self.tree.setColumnCount(2)
        self.tree.setRootIsDecorated(False)
        self.tree.itemDoubleClicked.connect(self._on_choose)
        layout.addWidget(self.tree)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        choose_btn = QPushButton("Select")
        choose_btn.setObjectName("startBtn")
        choose_btn.clicked.connect(self._on_choose)
        btn_row.addWidget(choose_btn)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

        self._populate()

    def _populate(self):
        self.tree.clear()
        needle = self.search_edit.text().strip().lower()
        for pid, name in process_utils.list_processes():
            if needle and needle not in name.lower():
                continue
            item = QTreeWidgetItem([str(pid), name])
            self.tree.addTopLevelItem(item)
        self.tree.resizeColumnToContents(0)

    def _on_choose(self):
        items = self.tree.selectedItems()
        if not items:
            return
        pid = int(items[0].text(0))
        name = items[0].text(1)
        self.result = (pid, name)
        self.accept()


# ===========================================================================
#  Main widget
# ===========================================================================
class GameTranslatorWidget(QWidget):
    sig_log = pyqtSignal(str, str, str)   # kind, text, level
    sig_pipeline_failed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        if not _GVT_OK:
            self._build_unavailable_ui()
            return

        self.setStyleSheet(_DARK)
        self._adapter = _TkAdapter(self)
        self._tts_enabled_adapter = _BoolProxy(lambda: self.tts_enabled_cb.isChecked())

        self.tts_manager = gvt_tts.TTSManager()
        self.active_pipeline = None
        self.active_mode = None
        self.running = False
        self.oneshot_pipeline = None
        self._devs = []
        self.log_q = queue.Queue()

        self.avatar = None  # lazy init — created on first use

        self._build_ui()
        self._refresh_devices()
        self._start_log_pump()

        self.sig_log.connect(self._on_log_line, Qt.ConnectionType.QueuedConnection)
        self.sig_pipeline_failed.connect(self._on_pipeline_failed_slot,
                                         Qt.ConnectionType.QueuedConnection)

    # ------------------------------------------------------------------
    def _build_unavailable_ui(self):
        layout = QVBoxLayout(self)
        lbl = QLabel(
            "Game Voice Translator modules not found.\n\n"
            "Ensure the game_voice_translator folder exists at:\n"
            f"  {GVT_DIR}\n\n"
            "pip install -r requirements.txt")
        lbl.setStyleSheet("color: #f85149; font-size: 13px;")
        lbl.setWordWrap(True)
        layout.addWidget(lbl)
        layout.addStretch()

    # ------------------------------------------------------------------
    def _build_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(6, 6, 6, 6)
        main_layout.setSpacing(6)

        self.tabs = QTabWidget()
        self.tabs.setMinimumHeight(400)
        main_layout.addWidget(self.tabs, 1)

        # Wrap each sub-tab in a scroll area to prevent content squishing
        for builder, name in [
            (self._build_live_tab, "Live"),
            (self._build_oneshot_tab, "OneShot"),
            (self._build_vtuber_tab, "VTuber (OCR)"),
            (self._build_settings_tab, "Settings"),
        ]:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll.setVerticalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            inner = builder()
            inner.setMinimumHeight(350)
            scroll.setWidget(inner)
            self.tabs.addTab(scroll, name)

        # --- Log as a separate tab ---
        log_tab = QWidget()
        log_layout = QVBoxLayout(log_tab)
        log_layout.setContentsMargins(8, 8, 8, 8)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        log_layout.addWidget(self.log_text, 1)

        log_btn_row = QHBoxLayout()
        log_btn_row.addStretch()
        tts_toggle = QCheckBox("Enable TTS")
        tts_toggle.setChecked(True)
        log_btn_row.addWidget(tts_toggle)
        self.tts_enabled_cb = tts_toggle
        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(self.log_text.clear)
        log_btn_row.addWidget(clear_btn)
        log_layout.addLayout(log_btn_row)

        self.tabs.addTab(log_tab, "Journal")

        # --- Status bar at the very bottom ---
        status_frame = QFrame()
        status_frame.setFixedHeight(36)
        status_frame.setStyleSheet(
            "QFrame { background: #0d1117; border: 1px solid #3a3f4b; "
            "border-radius: 4px; }")
        status_layout = QHBoxLayout(status_frame)
        status_layout.setContentsMargins(8, 2, 8, 2)

        status_layout.addWidget(QLabel("Level:"))
        self.level_bar = QProgressBar()
        self.level_bar.setRange(0, 100)
        self.level_bar.setValue(0)
        self.level_bar.setTextVisible(False)
        self.level_bar.setFixedWidth(200)
        self.level_bar.setFixedHeight(14)
        status_layout.addWidget(self.level_bar)

        self.speak_lbl = QLabel("● idle")
        self.speak_lbl.setStyleSheet("color: #8b949e;")
        status_layout.addWidget(self.speak_lbl)

        status_layout.addStretch()

        test_btn = QPushButton("Test TTS")
        test_btn.setFixedHeight(26)
        test_btn.clicked.connect(self._on_test_tts)
        status_layout.addWidget(test_btn)

        main_layout.addWidget(status_frame)

        # Meter timer
        self._meter_timer = QTimer(self)
        self._meter_timer.timeout.connect(self._update_meter)
        self._meter_timer.start(120)

    # ==============================================================
    #  LIVE TAB
    # ==============================================================
    def _build_live_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # Process group
        proc_grp = QGroupBox("Game Process (PID / exe) — monitoring")
        proc_grp.setMinimumHeight(80)
        proc_layout = QVBoxLayout(proc_grp)

        row0 = QHBoxLayout()
        row0.setSpacing(6)
        row0.addWidget(QLabel("PID:"))
        self.live_pid_edit = QLineEdit()
        self.live_pid_edit.setFixedWidth(80)
        row0.addWidget(self.live_pid_edit)
        row0.addSpacing(8)
        row0.addWidget(QLabel("exe:"))
        self.live_exe_edit = QLineEdit()
        self.live_exe_edit.setMinimumWidth(140)
        row0.addWidget(self.live_exe_edit)
        row0.addSpacing(4)
        find_btn = QPushButton("Find")
        find_btn.setFixedWidth(60)
        find_btn.clicked.connect(self._live_find_exe)
        row0.addWidget(find_btn)
        list_btn = QPushButton("From List...")
        list_btn.setFixedWidth(90)
        list_btn.clicked.connect(self._live_pick_proc)
        row0.addWidget(list_btn)
        row0.addStretch()
        proc_layout.addLayout(row0)

        self.live_status_lbl = QLabel("No game selected.")
        self.live_status_lbl.setStyleSheet("color: #3fb950;")
        proc_layout.addWidget(self.live_status_lbl)
        layout.addWidget(proc_grp)

        # Audio source
        audio_grp = QGroupBox("Audio Source (VB-Cable)")
        audio_grp.setMinimumHeight(80)
        audio_layout = QVBoxLayout(audio_grp)

        row1 = QHBoxLayout()
        row1.setSpacing(6)
        row1.addWidget(QLabel("Device:"))
        self.live_dev_combo = QComboBox()
        self.live_dev_combo.setMinimumWidth(260)
        row1.addWidget(self.live_dev_combo, 1)
        refresh_btn = QPushButton("Refresh")
        refresh_btn.setFixedWidth(70)
        refresh_btn.clicked.connect(self._refresh_devices)
        row1.addWidget(refresh_btn)
        cable_btn = QPushButton("Find CABLE")
        cable_btn.setFixedWidth(90)
        cable_btn.clicked.connect(self._find_cable)
        row1.addWidget(cable_btn)
        audio_layout.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(6)
        row2.addWidget(QLabel("Sensitivity:"))
        self.live_thr_slider = QSlider(Qt.Orientation.Horizontal)
        self.live_thr_slider.setRange(2, 100)
        self.live_thr_slider.setValue(int(gvt_config.VAD_ENERGY_THRESHOLD * 1000))
        self.live_thr_slider.setMinimumWidth(180)
        row2.addWidget(self.live_thr_slider, 1)
        self.live_thr_lbl = QLabel(f"{self.live_thr_slider.value()/1000:.3f}")
        self.live_thr_lbl.setFixedWidth(40)
        row2.addWidget(self.live_thr_lbl)
        self.live_thr_slider.valueChanged.connect(
            lambda v: self.live_thr_lbl.setText(f"{v/1000:.3f}"))
        audio_layout.addLayout(row2)
        layout.addWidget(audio_grp)

        # Buttons row — fixed widths for alignment
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self.live_start_btn = QPushButton("▶ Start Live")
        self.live_start_btn.setFixedWidth(120)
        self.live_start_btn.setFixedHeight(32)
        self.live_start_btn.setObjectName("startBtn")
        self.live_start_btn.clicked.connect(self._start_live)
        btn_row.addWidget(self.live_start_btn)
        self.live_stop_btn = QPushButton("■ Stop")
        self.live_stop_btn.setFixedWidth(120)
        self.live_stop_btn.setFixedHeight(32)
        self.live_stop_btn.setObjectName("stopBtn")
        self.live_stop_btn.setEnabled(False)
        self.live_stop_btn.clicked.connect(self._do_stop)
        btn_row.addWidget(self.live_stop_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        layout.addStretch()
        return w

    # ==============================================================
    #  ONESHOT TAB
    # ==============================================================
    def _build_oneshot_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        grp = QGroupBox("One-Shot Translation")
        grp.setMinimumHeight(160)
        grp_layout = QVBoxLayout(grp)

        # Source radios
        row0 = QHBoxLayout()
        row0.setSpacing(6)
        row0.addWidget(QLabel("Source:"))
        self.oneshot_src_game = QRadioButton("Game (VB-Cable)")
        self.oneshot_src_mic = QRadioButton("Microphone")
        self.oneshot_src_game.setChecked(True)
        row0.addWidget(self.oneshot_src_game)
        row0.addWidget(self.oneshot_src_mic)
        row0.addStretch()
        grp_layout.addLayout(row0)

        # Device
        row1 = QHBoxLayout()
        row1.setSpacing(6)
        row1.addWidget(QLabel("Device:"))
        self.oneshot_dev_combo = QComboBox()
        self.oneshot_dev_combo.setMinimumWidth(260)
        row1.addWidget(self.oneshot_dev_combo, 1)
        refresh_btn2 = QPushButton("Refresh")
        refresh_btn2.setFixedWidth(70)
        refresh_btn2.clicked.connect(self._refresh_devices)
        row1.addWidget(refresh_btn2)
        grp_layout.addLayout(row1)

        # Duration
        row2 = QHBoxLayout()
        row2.setSpacing(6)
        row2.addWidget(QLabel("Duration (s):"))
        self.oneshot_dur_spin = QDoubleSpinBox()
        self.oneshot_dur_spin.setRange(1, 60)
        self.oneshot_dur_spin.setValue(gvt_config.ONESHOT_DURATION_S)
        self.oneshot_dur_spin.setSingleStep(1)
        self.oneshot_dur_spin.setFixedWidth(80)
        row2.addWidget(self.oneshot_dur_spin)
        row2.addStretch()
        grp_layout.addLayout(row2)

        # Buttons — same fixed widths as Live tab
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self.one_record_btn = QPushButton("⏺ Record & Translate")
        self.one_record_btn.setFixedWidth(160)
        self.one_record_btn.setFixedHeight(32)
        self.one_record_btn.setObjectName("startBtn")
        self.one_record_btn.clicked.connect(self._start_oneshot)
        btn_row.addWidget(self.one_record_btn)
        self.one_stop_btn = QPushButton("■ Cancel")
        self.one_stop_btn.setFixedWidth(120)
        self.one_stop_btn.setFixedHeight(32)
        self.one_stop_btn.setObjectName("stopBtn")
        self.one_stop_btn.setEnabled(False)
        self.one_stop_btn.clicked.connect(self._do_stop)
        btn_row.addWidget(self.one_stop_btn)
        btn_row.addStretch()
        grp_layout.addLayout(btn_row)

        self.oneshot_status_lbl = QLabel("Ready to record.")
        self.oneshot_status_lbl.setStyleSheet("color: #3fb950;")
        grp_layout.addWidget(self.oneshot_status_lbl)
        layout.addWidget(grp)

        layout.addStretch()
        return w

    # ==============================================================
    #  VTUBER TAB
    # ==============================================================
    def _build_vtuber_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        grp = QGroupBox("VTuber: OCR from Screen")
        grp.setMinimumHeight(140)
        grp_layout = QVBoxLayout(grp)

        row0 = QHBoxLayout()
        row0.setSpacing(6)
        row0.addWidget(QLabel("Screen region:"))
        self.region_combo = QComboBox()
        self.region_combo.addItems([
            "whole", "bottom25", "bottom33", "top25", "center50", "custom"])
        self.region_combo.setCurrentText(gvt_config.OCR_REGION)
        self.region_combo.setFixedWidth(110)
        row0.addWidget(self.region_combo)
        row0.addWidget(QLabel("(whole = full screen; bottom25 = lower quarter)"))
        row0.addStretch()
        grp_layout.addLayout(row0)

        row1 = QHBoxLayout()
        row1.setSpacing(6)
        row1.addWidget(QLabel("Custom rect (x,y,w,h):"))
        self.rect_edit = QLineEdit(",".join(map(str, gvt_config.OCR_CUSTOM_RECT)))
        self.rect_edit.setFixedWidth(140)
        row1.addWidget(self.rect_edit)
        row1.addStretch()
        grp_layout.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(6)
        row2.addWidget(QLabel("OCR interval (s):"))
        self.ocr_interval_spin = QDoubleSpinBox()
        self.ocr_interval_spin.setRange(0.5, 10)
        self.ocr_interval_spin.setValue(gvt_config.OCR_INTERVAL_S)
        self.ocr_interval_spin.setSingleStep(0.5)
        self.ocr_interval_spin.setFixedWidth(80)
        row2.addWidget(self.ocr_interval_spin)
        row2.addSpacing(12)
        self.avatar_cb = QCheckBox("Show avatar (lip-sync)")
        row2.addWidget(self.avatar_cb)
        row2.addStretch()
        grp_layout.addLayout(row2)
        layout.addWidget(grp)

        # Avatar generation
        av_grp = QGroupBox("Avatar (Pollinations API)")
        av_grp.setMinimumHeight(140)
        av_layout = QVBoxLayout(av_grp)

        av_row0 = QHBoxLayout()
        av_row0.setSpacing(6)
        av_row0.addWidget(QLabel("Character:"))
        self.char_combo = QComboBox()
        self.char_combo.setMinimumWidth(240)
        try:
            for p in replicate_avatar.PRESET_CHARACTERS:
                self.char_combo.addItem(f'{p["slug"]} — {p["name"]}', p["slug"])
        except Exception:
            self.char_combo.addItem("default — Default", "default")
        self.char_combo.currentIndexChanged.connect(self._on_character_select)
        av_row0.addWidget(self.char_combo, 1)
        av_layout.addLayout(av_row0)

        av_row1 = QHBoxLayout()
        av_row1.setSpacing(6)
        av_row1.addWidget(QLabel("Emotion:"))
        self.avatar_emotion_combo = QComboBox()
        self.avatar_emotion_combo.setFixedWidth(180)
        for em in EMOTIONS:
            self.avatar_emotion_combo.addItem(
                f"{em} — {EMOTION_LABELS[em]}", em)
        self.avatar_emotion_combo.currentIndexChanged.connect(
            self._on_avatar_emotion_change)
        av_row1.addWidget(self.avatar_emotion_combo)
        self.auto_emotion_cb = QCheckBox("Auto emotion from text")
        self.auto_emotion_cb.setChecked(True)
        av_row1.addWidget(self.auto_emotion_cb)
        av_row1.addStretch()
        av_layout.addLayout(av_row1)

        av_row2 = QHBoxLayout()
        av_row2.setSpacing(6)
        av_row2.addWidget(QLabel("Seed:"))
        self.seed_spin = QSpinBox()
        self.seed_spin.setRange(0, 999999)
        try:
            self.seed_spin.setValue(replicate_avatar.DEFAULT_SEED)
        except Exception:
            self.seed_spin.setValue(7)
        self.seed_spin.setFixedWidth(90)
        av_row2.addWidget(self.seed_spin)
        av_row2.addSpacing(8)
        gen_btn = QPushButton("Generate (all emotions)")
        gen_btn.setFixedWidth(160)
        gen_btn.clicked.connect(self._on_generate_avatar)
        av_row2.addWidget(gen_btn)
        reload_btn = QPushButton("Reload from cache")
        reload_btn.setFixedWidth(130)
        reload_btn.clicked.connect(self._on_reload_avatar)
        av_row2.addWidget(reload_btn)
        av_row2.addStretch()
        av_layout.addLayout(av_row2)

        self.avatar_status_lbl = QLabel("Avatar: mode 'auto'.")
        self.avatar_status_lbl.setStyleSheet("color: #3fb950;")
        av_layout.addWidget(self.avatar_status_lbl)
        layout.addWidget(av_grp)

        # Buttons — same fixed widths
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self.vtu_start_btn = QPushButton("▶ Start VTuber")
        self.vtu_start_btn.setFixedWidth(120)
        self.vtu_start_btn.setFixedHeight(32)
        self.vtu_start_btn.setObjectName("startBtn")
        self.vtu_start_btn.clicked.connect(self._start_vtuber)
        btn_row.addWidget(self.vtu_start_btn)
        self.vtu_stop_btn = QPushButton("■ Stop")
        self.vtu_stop_btn.setFixedWidth(120)
        self.vtu_stop_btn.setFixedHeight(32)
        self.vtu_stop_btn.setObjectName("stopBtn")
        self.vtu_stop_btn.setEnabled(False)
        self.vtu_stop_btn.clicked.connect(self._do_stop)
        btn_row.addWidget(self.vtu_stop_btn)
        preview_btn = QPushButton("Show Avatar (preview)")
        preview_btn.setFixedWidth(160)
        preview_btn.clicked.connect(lambda: self._avatar_action('show'))
        btn_row.addWidget(preview_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # Info
        info = QGroupBox("How it works")
        info_layout = QVBoxLayout(info)
        info_lbl = QLabel(
            "VTuber periodically captures the selected screen region, "
            "recognizes English text (EasyOCR), skips duplicates, and speaks "
            "the Russian translation. If avatar is enabled, a 2D character "
            "appears over the window with lip-sync. The avatar window is "
            "draggable.")
        info_lbl.setWordWrap(True)
        info_lbl.setStyleSheet("color: #8b949e;")
        info_layout.addWidget(info_lbl)
        layout.addWidget(info)

        layout.addStretch()
        return w

    # ==============================================================
    #  SETTINGS TAB
    # ==============================================================
    def _build_settings_tab(self):
        w = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # ASR
        asr_grp = QGroupBox("Speech Recognition (Whisper)")
        asr_layout = QVBoxLayout(asr_grp)

        asr_row0 = QHBoxLayout()
        asr_row0.setSpacing(6)
        asr_row0.addWidget(QLabel("Model:"))
        self.asr_model_combo = QComboBox()
        self.asr_model_combo.setFixedWidth(120)
        self.asr_model_combo.addItems(
            ["tiny", "base", "small", "medium", "large-v3"])
        self.asr_model_combo.setCurrentText(gvt_config.ASR_MODEL)
        asr_row0.addWidget(self.asr_model_combo)
        asr_row0.addSpacing(12)
        asr_row0.addWidget(QLabel("Device:"))
        self.asr_dev_combo = QComboBox()
        self.asr_dev_combo.setFixedWidth(80)
        self.asr_dev_combo.addItems(["auto", "cpu", "cuda"])
        self.asr_dev_combo.setCurrentText(gvt_config.ASR_DEVICE)
        asr_row0.addWidget(self.asr_dev_combo)
        asr_row0.addWidget(QLabel("cuda = NVIDIA GPU (faster)"))
        asr_row0.addStretch()
        asr_layout.addLayout(asr_row0)
        layout.addWidget(asr_grp)

        # TTS
        tts_grp = QGroupBox("Voice Output (TTS)")
        tts_layout = QVBoxLayout(tts_grp)

        tts_row0 = QHBoxLayout()
        tts_row0.setSpacing(6)
        tts_row0.addWidget(QLabel("Engine:"))
        self.tts_online_rb = QRadioButton("Edge-TTS (online)")
        self.tts_offline_rb = QRadioButton("MMS VITS (offline)")
        self.tts_online_rb.setChecked(gvt_config.TTS_ENGINE == "online")
        self.tts_offline_rb.setChecked(gvt_config.TTS_ENGINE == "offline")
        tts_row0.addWidget(self.tts_online_rb)
        tts_row0.addWidget(self.tts_offline_rb)
        tts_row0.addStretch()
        tts_layout.addLayout(tts_row0)

        tts_row1 = QHBoxLayout()
        tts_row1.setSpacing(6)
        tts_row1.addWidget(QLabel("Voice (Edge):"))
        self.tts_voice_combo = QComboBox()
        self.tts_voice_combo.setMinimumWidth(200)
        for v in gvt_config.TTS_VOICES.values():
            self.tts_voice_combo.addItem(v)
        self.tts_voice_combo.setCurrentText(gvt_config.TTS_VOICE)
        tts_row1.addWidget(self.tts_voice_combo)
        tts_row1.addStretch()
        tts_layout.addLayout(tts_row1)

        tts_row2 = QHBoxLayout()
        tts_row2.setSpacing(6)
        tts_row2.addWidget(QLabel("Rate:"))
        self.tts_rate_edit = QLineEdit(gvt_config.TTS_RATE)
        self.tts_rate_edit.setFixedWidth(80)
        tts_row2.addWidget(self.tts_rate_edit)
        tts_row2.addWidget(QLabel("e.g. -10%, +15%"))
        tts_row2.addStretch()
        tts_layout.addLayout(tts_row2)

        tts_row3 = QHBoxLayout()
        tts_row3.setSpacing(6)
        tts_row3.addWidget(QLabel("Offline model:"))
        self.offline_model_edit = QLineEdit(gvt_config.TTS_OFFLINE_MODEL)
        tts_row3.addWidget(self.offline_model_edit, 1)
        tts_layout.addLayout(tts_row3)
        layout.addWidget(tts_grp)

        # OCR settings
        ocr_grp = QGroupBox("OCR (VTuber)")
        ocr_layout = QVBoxLayout(ocr_grp)

        ocr_row0 = QHBoxLayout()
        ocr_row0.setSpacing(6)
        ocr_row0.addWidget(QLabel("Min confidence:"))
        self.ocr_conf_spin = QDoubleSpinBox()
        self.ocr_conf_spin.setRange(0, 1)
        self.ocr_conf_spin.setValue(gvt_config.OCR_MIN_CONFIDENCE)
        self.ocr_conf_spin.setSingleStep(0.05)
        self.ocr_conf_spin.setFixedWidth(80)
        ocr_row0.addWidget(self.ocr_conf_spin)
        ocr_row0.addSpacing(12)
        ocr_row0.addWidget(QLabel("Dedup (0..1):"))
        self.ocr_dedup_spin = QDoubleSpinBox()
        self.ocr_dedup_spin.setRange(0, 1)
        self.ocr_dedup_spin.setValue(gvt_config.OCR_DEDUP_LEVENSHTEIN)
        self.ocr_dedup_spin.setSingleStep(0.05)
        self.ocr_dedup_spin.setFixedWidth(80)
        ocr_row0.addWidget(self.ocr_dedup_spin)
        ocr_row0.addSpacing(12)
        self.ocr_gpu_cb = QCheckBox("GPU for OCR (NVIDIA)")
        ocr_row0.addWidget(self.ocr_gpu_cb)
        ocr_row0.addStretch()
        ocr_layout.addLayout(ocr_row0)
        layout.addWidget(ocr_grp)

        # VB-Cable note
        note_grp = QGroupBox("VB-Cable Setup")
        note_layout = QVBoxLayout(note_grp)
        note_lbl = QLabel(
            "1) Install VB-CABLE (vb-audio.com/Cable/).\n"
            "2) In Windows, set GAME output = 'CABLE Input'.\n"
            "3) To hear the game: CABLE Output properties → 'Listen to this "
            "device' → your speakers.\n"
            "4) Select recording device 'CABLE Output' here ('Find CABLE').")
        note_lbl.setWordWrap(True)
        note_lbl.setStyleSheet("color: #8b949e;")
        note_layout.addWidget(note_lbl)
        layout.addWidget(note_grp)

        layout.addStretch()
        scroll.setWidget(inner)
        tab_layout = QVBoxLayout(w)
        tab_layout.setContentsMargins(0, 0, 0, 0)
        tab_layout.addWidget(scroll)
        return w

    # ==============================================================
    #  Device management
    # ==============================================================
    def _refresh_devices(self):
        self._devs = audio_capture.list_input_devices()
        labels = [d[1] for d in self._devs]
        self.live_dev_combo.clear()
        self.live_dev_combo.addItems(labels)
        self.oneshot_dev_combo.clear()
        self.oneshot_dev_combo.addItems(labels)

    def _find_cable(self):
        res = audio_capture.find_cable_device()
        if res is None:
            QMessageBox.information(
                self, "CABLE not found",
                "Virtual cable not detected.\n"
                "Check VB-CABLE/Voicemeeter installation.")
            return
        idx, name = res
        for i, d in enumerate(self._devs):
            if d[0] == idx:
                self.live_dev_combo.setCurrentIndex(i)
                self.oneshot_dev_combo.setCurrentIndex(i)
                return

    def _dev_index(self, combo):
        idx = combo.currentIndex()
        if 0 <= idx < len(self._devs):
            return self._devs[idx][0]
        return None

    # ==============================================================
    #  Process selection
    # ==============================================================
    def _live_find_exe(self):
        exe = self.live_exe_edit.text().strip()
        if not exe:
            QMessageBox.information(self, "Hint", "Enter an exe name first.")
            return
        matches = process_utils.find_processes_by_exe(exe)
        if not matches:
            self.live_status_lbl.setText(f"Not found: '{exe}'")
            return
        pid, name = matches[0]
        self.live_pid_edit.setText(str(pid))
        self.live_exe_edit.setText(name)
        self._update_proc_status(pid)

    def _live_pick_proc(self):
        dlg = ProcessDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.result:
            pid, name = dlg.result
            self.live_pid_edit.setText(str(pid))
            self.live_exe_edit.setText(name)
            self._update_proc_status(pid)

    def _update_proc_status(self, pid):
        info = process_utils.process_info(pid)
        if info is None:
            self.live_status_lbl.setText(f"PID {pid}: not found.")
            return
        status = "running" if info["running"] else "stopped"
        self.live_status_lbl.setText(
            f"✓ {info['name']} (PID {pid}, {status})\n  {info.get('exe', '?')}")

    # ==============================================================
    #  TTS settings
    # ==============================================================
    def _apply_tts_settings(self):
        kind = "online" if self.tts_online_rb.isChecked() else "offline"
        self.tts_manager.configure(
            kind=kind,
            voice=self.tts_voice_combo.currentText(),
            rate=self.tts_rate_edit.text(),
            volume=gvt_config.TTS_VOLUME,
            offline_model=self.offline_model_edit.text())

    def _asr_kwargs(self):
        return dict(
            model_size=self.asr_model_combo.currentText(),
            device=self.asr_dev_combo.currentText(),
            download_root=gvt_config.MODELS_DIR,
            language=gvt_config.ASR_LANGUAGE,
            beam_size=gvt_config.ASR_BEAM_SIZE)

    # ==============================================================
    #  Start / Stop
    # ==============================================================
    def _start_any(self, mode, start_btn, stop_btn):
        if self.running:
            self._do_stop()
        self._apply_tts_settings()
        self.running = True
        self.active_mode = mode
        start_btn.setEnabled(False)
        stop_btn.setEnabled(True)

    def _reset_buttons(self):
        self.live_start_btn.setEnabled(True)
        self.live_stop_btn.setEnabled(False)
        self.one_record_btn.setEnabled(True)
        self.one_stop_btn.setEnabled(False)
        self.vtu_start_btn.setEnabled(True)
        self.vtu_stop_btn.setEnabled(False)

    def _start_live(self):
        pid_text = self.live_pid_edit.text().strip()
        pid = None
        if pid_text:
            try:
                pid = int(pid_text)
            except ValueError:
                pass
        if pid is None or not process_utils.is_running(pid):
            ret = QMessageBox.question(
                self, "Game not selected",
                "PID not set or process not running.\n"
                "Continue without process monitoring?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if ret != QMessageBox.StandardButton.Yes:
                return
        self._start_any("live", self.live_start_btn, self.live_stop_btn)
        capture_kwargs = dict(
            device_index=self._dev_index(self.live_dev_combo),
            out_rate=gvt_config.AUDIO_SAMPLE_RATE,
            capture_rate=None,
            frame_ms=gvt_config.AUDIO_FRAME_MS,
            energy_threshold=self.live_thr_slider.value() / 1000.0,
            silence_ms=gvt_config.VAD_SILENCE_MS,
            min_segment_s=gvt_config.VAD_MIN_SEGMENT_S,
            max_segment_s=gvt_config.VAD_MAX_SEGMENT_S,
            pre_roll_s=gvt_config.VAD_PRE_ROLL_S)
        self.active_pipeline = LivePipeline(self._adapter, self.tts_manager)
        self.active_pipeline.start(capture_kwargs, self._asr_kwargs())

    def _start_oneshot(self):
        self._start_any("single", self.one_record_btn, self.one_stop_btn)
        self.oneshot_pipeline = OneShotPipeline(self._adapter, self.tts_manager)
        self.oneshot_pipeline.set_asr_kwargs(self._asr_kwargs())
        dev_idx = self._dev_index(self.oneshot_dev_combo)
        dur = self.oneshot_dur_spin.value()
        src = "game" if self.oneshot_src_game.isChecked() else "mic"
        self.oneshot_pipeline.record_and_translate(
            dev_idx, dur, self._asr_kwargs(), source_label=src)

    def _start_vtuber(self):
        if not gvt_ocr.available():
            QMessageBox.critical(
                self, "OCR unavailable",
                "EasyOCR is not installed.\n"
                "pip install -r requirements-extra.txt")
            return
        if not gvt_screen.available():
            QMessageBox.critical(
                self, "Screen capture unavailable",
                "mss module not installed.\n"
                "pip install -r requirements-extra.txt")
            return
        self._start_any("vtuber", self.vtu_start_btn, self.vtu_stop_btn)

        region = self.region_combo.currentText()
        if region == "custom":
            try:
                vals = [int(x.strip())
                        for x in self.rect_edit.text().split(",")]
                region = tuple(vals)
            except Exception:
                region = "whole"

        gvt_config.OCR_INTERVAL_S = self.ocr_interval_spin.value()
        gvt_config.OCR_MIN_CONFIDENCE = self.ocr_conf_spin.value()
        gvt_config.OCR_DEDUP_LEVENSHTEIN = self.ocr_dedup_spin.value()
        gvt_config.OCR_USE_GPU = self.ocr_gpu_cb.isChecked()
        gvt_config.AVATAR_ENABLED = self.avatar_cb.isChecked()

        avatar = self._ensure_avatar() if self.avatar_cb.isChecked() else None
        self.active_pipeline = OcrPipeline(
            self._adapter, self.tts_manager, avatar=avatar)
        self.active_pipeline.start(
            ocr_kwargs=dict(
                langs=gvt_config.OCR_LANGS,
                gpu=gvt_config.OCR_USE_GPU,
                download_root=gvt_config.MODELS_DIR),
            region=region)

    def _do_stop(self):
        if not self.running and self.active_pipeline is None:
            return
        self.running = False
        if self.active_pipeline is not None:
            try:
                self.active_pipeline.stop()
            except Exception:
                pass
            self.active_pipeline = None
        self.tts_manager.stop()
        self.oneshot_pipeline = None
        self._reset_buttons()
        self.sig_log.emit("system", "Stopped.", "info")

    def _on_pipeline_failed_slot(self, reason):
        self._do_stop()
        QMessageBox.critical(
            self, "Launch Error",
            f"Failed to start:\n{reason}")

    # ==============================================================
    #  Avatar
    # ==============================================================
    def _current_preset(self):
        slug = self.char_combo.currentData()
        try:
            return replicate_avatar.get_preset(slug)
        except Exception:
            return {"slug": "default", "name": "Default", "seed": 7, "prompt": ""}

    def _on_character_select(self):
        p = self._current_preset()
        if p is None:
            return
        self.seed_spin.setValue(p["seed"])
        cached = sum(1 for em in EMOTIONS
                     if replicate_avatar.is_cached(em, p["slug"]))
        if cached >= len(EMOTIONS):
            av = self._ensure_avatar()
            if av:
                av.set_character(p["slug"])
            self.avatar_status_lbl.setText(
                f"Character '{p['name']}' loaded ({cached}/9).")
            self.sig_log.emit(
                "system", f"Avatar: character '{p['name']}'.", "ok")
        else:
            av = self._ensure_avatar()
            if av:
                av.slug = p["slug"]
            self.avatar_status_lbl.setText(
                f"Character '{p['name']}': {cached}/9 cached. "
                f"Press 'Generate'.")
            self.sig_log.emit(
                "system",
                f"Selected '{p['name']}' (seed {p['seed']}). "
                f"{cached}/9 cached — generate.", "info")

    def _on_generate_avatar(self):
        p = self._current_preset()
        if p is None:
            return
        slug, seed, char = p["slug"], p["seed"], p.get("prompt", p.get("character", ""))
        self.seed_spin.setValue(seed)
        # Clear old cache
        for em in EMOTIONS:
            path = replicate_avatar.get_path(em, slug)
            if os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    pass
        cached = sum(1 for em in EMOTIONS
                     if replicate_avatar.is_cached(em, slug))
        self.avatar_status_lbl.setText(
            f"Generating {9 - cached}/9 emotions '{p['name']}'... (seed={seed})")
        self.sig_log.emit(
            "system",
            f"Generating '{p['name']}' (slug={slug}, seed={seed})...", "info")

        def on_done(em, err):
            if err:
                self.sig_log.emit(
                    "system", f"'{p['name']}' / {em}: {err}", "error")
            else:
                self.sig_log.emit(
                    "system", f"'{p['name']}' / {em} ready.", "ok")
            done = sum(1 for e in EMOTIONS
                       if replicate_avatar.is_cached(e, slug))
            QTimer.singleShot(0, lambda d=done: self.avatar_status_lbl.setText(
                f"Generated {d}/9 emotions '{p['name']}'."))
            if done >= len(EMOTIONS):
                QTimer.singleShot(0, lambda: self._apply_character(slug))

        replicate_avatar.ensure_all(
            seed=seed, slug=slug, on_done=on_done)

    def _apply_character(self, slug):
        av = self._ensure_avatar()
        if av:
            av.set_character(slug)
        p = replicate_avatar.get_preset(slug)
        self.sig_log.emit(
            "system",
            f"Avatar: character '{p['name']}' active (image).", "ok")
        self.avatar_status_lbl.setText(f"Active '{p['name']}' (image).")

    def _on_reload_avatar(self):
        p = self._current_preset()
        if p is None:
            return
        av = self._ensure_avatar()
        if av is None:
            return
        av.slug = p["slug"]
        av.reload_images()
        cached = sum(1 for em in EMOTIONS
                     if replicate_avatar.is_cached(em, p["slug"]))
        self.avatar_status_lbl.setText(
            f"Avatar: '{p['name']}', {cached}/9 cached.")
        self.sig_log.emit(
            "system",
            f"Avatar reloaded: '{p['name']}'.", "ok")

    def _on_avatar_emotion_change(self):
        em = self.avatar_emotion_combo.currentData()
        if em:
            av = self._ensure_avatar()
            if av:
                av.set_emotion(em)
            self.sig_log.emit(
                "system",
                f"Avatar emotion: {EMOTION_LABELS.get(em, em)}", "info")

    def _apply_avatar_emotion(self, text):
        if not self.auto_emotion_cb.isChecked():
            return
        try:
            from emotion_rules import detect_emotion
            em = detect_emotion(text)
            av = self._ensure_avatar()
            if av and em and em != av.emotion:
                av.set_emotion(em)
        except Exception:
            pass

    # ==============================================================
    #  TTS test
    # ==============================================================
    def _on_test_tts(self):
        self._apply_tts_settings()
        threading.Thread(
            target=lambda: self.tts_manager.speak(
                "Привет! Это проверка озвучки перевода игры.",
                blocking=True),
            daemon=True).start()
        self.sig_log.emit("system", "TTS test...", "info")

    # ==============================================================
    #  Logging
    # ==============================================================
    def _on_log_line(self, kind, text, level):
        ts = time.strftime("%H:%M:%S")
        if kind == "system":
            color = {"ok": "#3fb950", "error": "#f85149", "info": "#8b949e"
                     }.get(level, "#8b949e")
            prefix = "• "
        elif kind == "en":
            color = "#79c0ff"
            prefix = "[EN] "
        elif kind == "ru":
            color = "#3fb950"
            prefix = "[RU] "
        else:
            color = "#c9d1d9"
            prefix = ""

        self.log_text.append(
            f'<span style="color:#484f58">{ts}</span> '
            f'<span style="color:{color}">{prefix}{text}</span>')
        sb = self.log_text.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _start_log_pump(self):
        self._log_timer = QTimer(self)
        self._log_timer.timeout.connect(self._pump_log)
        self._log_timer.start(120)

    def _pump_log(self):
        try:
            while True:
                kind, text, level, ts = self.log_q.get_nowait()
                self._on_log_line(kind, text, level)
        except queue.Empty:
            pass

    # ==============================================================
    #  Meter
    # ==============================================================
    def _update_meter(self):
        lvl = self.tts_manager.controller.get_level()
        if lvl <= 0.0 and self.active_mode == "live" and \
                self.active_pipeline and hasattr(self.active_pipeline, 'capture') \
                and self.active_pipeline.capture:
            lvl = self.active_pipeline.capture.get_level() * 6.0
        self.level_bar.setValue(int(min(1.0, lvl) * 100))
        speaking = self.tts_manager.controller.is_speaking()
        if not speaking and self.active_mode == "live" and \
                self.active_pipeline and hasattr(self.active_pipeline, 'capture') \
                and self.active_pipeline.capture:
            speaking = self.active_pipeline.capture.is_speaking()
        self.speak_lbl.setText("● speaking!" if speaking else "● idle")
        self.speak_lbl.setStyleSheet(
            "color: #3fb950;" if speaking else "color: #8b949e;")

    # ==============================================================
    #  Avatar lazy init (Qt6 — no tkinter)
    # ==============================================================
    def _ensure_avatar(self):
        if self.avatar is not None:
            return self.avatar
        try:
            from qt6_avatar import QtAvatar
            slug = self.avatar_char_combo.currentData() if hasattr(self, 'avatar_char_combo') else "default"
            gen = self._generator if hasattr(self, '_generator') else "pixel"
            self.avatar = QtAvatar(
                controller=self.tts_manager.controller if self.tts_manager else None,
                slug=slug,
                emotion="neutral",
                generator=gen,
            )
            return self.avatar
        except Exception as e:
            logger.warning("Qt6 avatar init failed: %s", e)
            self.avatar = None
            return None

    def _avatar_action(self, action, *args, **kwargs):
        av = self._ensure_avatar()
        if av is None:
            return None
        fn = getattr(av, action, None)
        if fn:
            return fn(*args, **kwargs)
        return None

    # ==============================================================
    #  Cleanup
    # ==============================================================
    def cleanup(self):
        try:
            self._do_stop()
        except Exception:
            pass
        try:
            if self.avatar:
                self.avatar.hide_avatar()
                self.avatar.close()
        except Exception:
            pass
        try:
            if hasattr(self, '_tk_root') and self._tk_root:
                self._tk_root.quit()
                self._tk_root.destroy()
                self._tk_root = None
        except Exception:
            pass
