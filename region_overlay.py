"""
Оверлей рамок + панели голоса.
Рамки: ЛКМ перетаскивание, ПКМ меню, ▼ toggle панели.
Панели: inline в paintEvent, без отдельных QWidget.
"""
import logging
import time
import ctypes
import urllib.request
from PyQt6.QtWidgets import (QWidget, QApplication, QHBoxLayout, QVBoxLayout,
                              QPushButton, QSlider, QLabel, QLineEdit, QTextEdit,
                              QMenu, QWidgetAction, QComboBox, QSpinBox)
from PyQt6.QtCore import Qt, QRect, QPoint, pyqtSignal, QTimer
from PyQt6.QtGui import (QPainter, QPen, QBrush, QColor, QFont, QMouseEvent,
                          QIcon, QPixmap, QPainterPath, QPolygon)
from icons import icon_male, icon_female, icon_narrator, icon_play, icon_stop

logger = logging.getLogger(__name__)

_ROLE_HOTKEYS = {"male": "1", "female": "2", "narrator": "3"}

# Inline panel layout constants
_PANEL_H = 32
_PANEL_BTN = 24
_PANEL_GAP = 3


def _check_internet():
    """Quick check if internet is available (for Edge-TTS)."""
    try:
        urllib.request.urlopen("https://speech.platform.bing.com", timeout=2)
        return True
    except Exception:
        return False


class VoicePanel:
    """Inline panel state — no QWidget, drawn in RegionOverlay.paintEvent."""

    def __init__(self):
        self.visible = False
        self.role = "male"       # active role highlight
        self.rect = QRect()      # computed in _layout_panels
        # Button rects (computed in _layout_panels)
        self.btn_male = QRect()
        self.btn_female = QRect()
        self.btn_narrator = QRect()
        self.btn_start = QRect()
        self.btn_stop = QRect()
        self.btn_toggle = QRect()  # ▼ in name bar

    def contains(self, pos):
        return self.visible and self.rect.contains(pos)


class RegionOverlay(QWidget):
    """Оверлей: рамки + inline панели голоса."""

    region_moved = pyqtSignal(int, int, int, int, int)
    region_deleted = pyqtSignal(int)
    region_right_clicked = pyqtSignal(int)
    voice_speak = pyqtSignal(str)
    voice_params_changed = pyqtSignal(str, dict)  # role, {pitch, speed, name}
    name_speak = pyqtSignal(str)
    start_clicked = pyqtSignal()
    stop_clicked = pyqtSignal()
    scan_requested = pyqtSignal()
    stt_toggle_requested = pyqtSignal()
    stt_overlay_toggled = pyqtSignal(bool)  # show/hide STT overlays
    stt_tr_overlay_toggled = pyqtSignal(bool)  # show/hide STT translation overlay
    frame_opacity_changed = pyqtSignal(int)
    panel_opacity_changed = pyqtSignal(int)

    def __init__(self, parent=None, settings=None, config_key="overlay"):
        super().__init__(parent)
        self.regions = []
        self.dragging = None
        self.drag_offset = QPoint()
        self.resizing = None
        self.resize_edge = None
        self._edit_mode = True
        self._settings = settings
        self._config_key = config_key
        self._window_opacity = 1.0

        self._voice_panels = {}        # idx -> VoicePanel
        self._frame_opacity = 80
        self._panel_opacity = 80

        # Scan/STT indicators
        self._scanning = False
        self._scan_pulse = 0
        self._stt_active = False
        self._stt_blink = 0
        self._stt_last_text = ""

        self._indicator_timer = QTimer(self)
        self._indicator_timer.timeout.connect(self._tick_indicator)
        self._indicator_timer.start(150)
        self._voice_params = {
            "male": {"ru_voice": "ru-RU-DmitryNeural", "en_voice": "en-US-GuyNeural",
                     "ru_voice_type": "edge", "en_voice_type": "edge",
                     "pitch": 0, "speed": 0, "name": ""},
            "female": {"ru_voice": "ru-RU-SvetlanaNeural", "en_voice": "en-US-JennyNeural",
                        "ru_voice_type": "edge", "en_voice_type": "edge",
                        "pitch": 0, "speed": 0, "name": ""},
            "narrator": {"ru_voice": "ru-RU-DmitryNeural", "en_voice": "en-US-GuyNeural",
                          "ru_voice_type": "edge", "en_voice_type": "edge",
                          "pitch": 0, "speed": 0, "name": ""},
        }

        self._last_ocr_text = ""
        self._last_scan_seconds = 0.0
        self._last_confidence = 0.0

        # Internet check
        self._internet_ok = True
        self._internet_timer = QTimer(self)
        self._internet_timer.timeout.connect(self._check_internet_async)
        self._internet_timer.start(15000)
        self._check_internet_async()

        # Привязка к окну приложения
        self._target_hwnd = None
        self._window_rect = None  # {x, y, width, height} of target window
        self._diag_at = 0.0
        self._warned_no_rect = False
        self._window_track_timer = QTimer(self)
        self._window_track_timer.timeout.connect(self._track_window_position)
        self._window_track_timer.start(200)

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        screen = QApplication.primaryScreen().geometry()
        self.setGeometry(screen)
        self.setFixedSize(screen.width(), screen.height())
        self.hide()

        self._topmost_timer = QTimer(self)
        self._topmost_timer.timeout.connect(self._force_topmost)
        self._topmost_timer.start(1000)
        self._hwnd = None

        # ── In-overlay opacity control (green scan overlay) ──
        self._build_opacity_control()
        self._load_opacity_state()

    def _check_internet_async(self):
        """Non-blocking internet check via background thread."""
        import threading
        def _do():
            try:
                ok = _check_internet()
                if ok != self._internet_ok:
                    self._internet_ok = ok
                    QTimer.singleShot(0, self.update)
            except Exception:
                pass
        threading.Thread(target=_do, daemon=True).start()

    # ── Opacity control (in-overlay) ──────────────────────────
    def _build_opacity_control(self):
        from PyQt6.QtWidgets import (QPushButton, QSlider, QLabel, QVBoxLayout,
                                     QWidget)
        # Toggle button (always visible, top-left)
        self._ctrl_toggle = QPushButton("⚙", self)
        self._ctrl_toggle.setFixedSize(24, 24)
        self._ctrl_toggle.move(8, 8)
        self._ctrl_toggle.setToolTip("Overlay opacity / visibility settings")
        self._ctrl_toggle.setStyleSheet(
            "QPushButton { background: rgba(40,40,50,200); color: #ddd; "
            "border: 1px solid #555; border-radius: 4px; font-size: 13px; }"
            "QPushButton:hover { background: #3d444d; }")
        self._ctrl_toggle.clicked.connect(self._toggle_opacity_panel)

        # Panel
        self._ctrl_panel = QWidget(self)
        self._ctrl_panel.setFixedWidth(190)
        self._ctrl_panel.move(8, 38)
        self._ctrl_panel.setStyleSheet(
            "QWidget { background: rgba(20,22,28,235); border: 1px solid #555; "
            "border-radius: 6px; } QLabel { color: #ccc; font-size: 11px; }")
        self._ctrl_panel.hide()
        cl = QVBoxLayout(self._ctrl_panel)
        cl.setContentsMargins(8, 8, 8, 8)
        cl.setSpacing(6)

        lbl = QLabel("Scan overlay controls")
        lbl.setStyleSheet("font-weight: bold; color: #fff;")
        cl.addWidget(lbl)

        # ── Action buttons: OCR scan + STT start/stop ──
        btn_row = QHBoxLayout()
        self._btn_scan = QPushButton("Scan OCR")
        self._btn_scan.setFixedHeight(24)
        self._btn_scan.setStyleSheet(self._ctrl_btn_style())
        self._btn_scan.clicked.connect(self.scan_requested.emit)
        btn_row.addWidget(self._btn_scan)

        self._btn_stt = QPushButton("Start STT")
        self._btn_stt.setFixedHeight(24)
        self._btn_stt.setStyleSheet(self._ctrl_btn_style())
        self._btn_stt.clicked.connect(self.stt_toggle_requested.emit)
        btn_row.addWidget(self._btn_stt)
        cl.addLayout(btn_row)

        # Toggle buttons for STT overlays
        btn_row2 = QHBoxLayout()
        self._btn_show_stt = QPushButton("STT Text")
        self._btn_show_stt.setCheckable(True)
        self._btn_show_stt.setChecked(False)
        self._btn_show_stt.setFixedHeight(22)
        self._btn_show_stt.setStyleSheet(self._ctrl_btn_style())
        self._btn_show_stt.clicked.connect(lambda checked: self.stt_overlay_toggled.emit(checked))
        btn_row2.addWidget(self._btn_show_stt)

        self._btn_show_stt_tr = QPushButton("STT Translate")
        self._btn_show_stt_tr.setCheckable(True)
        self._btn_show_stt_tr.setChecked(False)
        self._btn_show_stt_tr.setFixedHeight(22)
        self._btn_show_stt_tr.setStyleSheet(self._ctrl_btn_style())
        self._btn_show_stt_tr.clicked.connect(lambda checked: self.stt_tr_overlay_toggled.emit(checked))
        btn_row2.addWidget(self._btn_show_stt_tr)
        cl.addLayout(btn_row2)

        sep = QLabel("")
        sep.setFixedHeight(6)
        cl.addWidget(sep)

        lbl = QLabel("Overlay transparency")
        lbl.setStyleSheet("font-weight: bold; color: #fff;")
        cl.addWidget(lbl)

        # Window opacity
        row = QHBoxLayout()
        row.addWidget(QLabel("Window"))
        self._win_opacity = QSlider(Qt.Orientation.Horizontal)
        self._win_opacity.setRange(10, 100)
        self._win_opacity.setValue(100)
        self._win_opacity.valueChanged.connect(self._on_win_opacity)
        row.addWidget(self._win_opacity, 1)
        cl.addLayout(row)

        # Frame opacity
        row = QHBoxLayout()
        row.addWidget(QLabel("Frame"))
        self._frame_opacity_sl = QSlider(Qt.Orientation.Horizontal)
        self._frame_opacity_sl.setRange(0, 100)
        self._frame_opacity_sl.setValue(self._frame_opacity)
        self._frame_opacity_sl.valueChanged.connect(
            lambda v: self.set_opacity(v, None))
        row.addWidget(self._frame_opacity_sl, 1)
        cl.addLayout(row)

        # Panel opacity
        row = QHBoxLayout()
        row.addWidget(QLabel("Panel"))
        self._panel_opacity_sl = QSlider(Qt.Orientation.Horizontal)
        self._panel_opacity_sl.setRange(0, 100)
        self._panel_opacity_sl.setValue(self._panel_opacity)
        self._panel_opacity_sl.valueChanged.connect(
            lambda v: self.set_opacity(None, v))
        row.addWidget(self._panel_opacity_sl, 1)
        cl.addLayout(row)

    def _ctrl_btn_style(self):
        return ("QPushButton { background: #0e639c; color: #fff; border: none; "
                "border-radius: 4px; font-size: 11px; font-weight: bold; padding: 2px 6px; }"
                "QPushButton:hover { background: #1177bb; }")

    def set_stt_label(self, text):
        if hasattr(self, "_btn_stt"):
            self._btn_stt.setText(text)

    def _toggle_opacity_panel(self):
        self._ctrl_panel.setVisible(not self._ctrl_panel.isVisible())

    def _on_win_opacity(self, v):
        self._window_opacity = v / 100.0
        self.setWindowOpacity(self._window_opacity)
        if not hasattr(self, "_opacity_save_timer"):
            self._opacity_save_timer = QTimer(self)
            self._opacity_save_timer.setSingleShot(True)
            self._opacity_save_timer.timeout.connect(self._save_opacity_state)
        self._opacity_save_timer.start(400)

    def _load_opacity_state(self):
        st = {}
        if self._settings:
            try:
                st = self._settings.get(self._config_key, {}) or {}
            except Exception:
                st = {}
        if "window_opacity" in st:
            self._window_opacity = st["window_opacity"]
            self.setWindowOpacity(self._window_opacity)
            if hasattr(self, "_win_opacity"):
                self._win_opacity.setValue(int(self._window_opacity * 100))

    def _save_opacity_state(self):
        if not self._settings:
            return
        try:
            st = self._settings.get(self._config_key, {}) or {}
            st["window_opacity"] = round(self._window_opacity, 2)
            self._settings.set(self._config_key, st)
        except Exception:
            pass

    def set_voice_params(self, role, params):
        if role in self._voice_params:
            self._voice_params[role].update(params)

    def get_voice_params(self, role):
        return self._voice_params.get(role, {})

    # ── Scan/STT indicator methods ──────────────────────────────
    def set_scanning(self, state: bool):
        """Show pulsing orange indicator on overlay during OCR scan."""
        self._scanning = state
        if state:
            self._scan_pulse = 0
        self.update()

    def set_stt_active(self, state: bool):
        """Show blinking green mic indicator when STT voice translate is on."""
        self._stt_active = state
        if state:
            self._stt_blink = 0
        self.update()

    def set_stt_text(self, text: str):
        """Update last STT recognized text for display on overlay (mic label)."""
        self._stt_last_text = text[:60] if text else ""
        self.update()

    def _tick_indicator(self):
        """Timer tick for pulsing/blinking animations."""
        changed = False
        if self._scanning:
            self._scan_pulse = (self._scan_pulse + 1) % 20
            changed = True
        if self._stt_active:
            self._stt_blink = (self._stt_blink + 1) % 8
            changed = True
        if changed:
            self.update()

    def set_opacity(self, frame_opacity=None, panel_opacity=None):
        if frame_opacity is not None:
            self._frame_opacity = frame_opacity
            self.frame_opacity_changed.emit(frame_opacity)
        if panel_opacity is not None:
            self._panel_opacity = panel_opacity
            self.panel_opacity_changed.emit(panel_opacity)
        self.update()

    def set_ocr_text(self, text: str, scan_seconds: float = 0.0, confidence: float = 0.0):
        self._last_ocr_text = text[:80] if text else ""
        if scan_seconds > 0:
            self._last_scan_seconds = scan_seconds
        if confidence > 0:
            self._last_confidence = confidence
        self.update()

    def set_target_window(self, hwnd):
        """Привязать оверлей к окну приложения. hwnd=None — снять привязку."""
        self._target_hwnd = hwnd
        if hwnd:
            self._track_window_position()
        else:
            self._window_rect = None
            screen = QApplication.primaryScreen().geometry()
            self.setGeometry(screen)
            self.setFixedSize(screen.width(), screen.height())
        self.update()

    def _track_window_position(self):
        """Отслеживать позицию целевого окна и двигать оверлей вслед за ним."""
        if not self._target_hwnd:
            return
        try:
            import ctypes.wintypes
            user32 = ctypes.windll.user32
            rect = ctypes.wintypes.RECT()
            user32.GetWindowRect(self._target_hwnd, ctypes.byref(rect))
            wx, wy = rect.left, rect.top
            ww = rect.right - rect.left
            wh = rect.bottom - rect.top
            if ww <= 0 or wh <= 0:
                # Окно свернуто/невидимо: оверлей оставляем на прежнем
                # месте, но рисуть рамку нечем — сбрасываем прямоугольник,
                # чтобы paintEvent не обрезал её по пустому клипу.
                self._window_rect = None
                self._warned_no_rect = True
                return
            new_rect = {"x": wx, "y": wy, "width": ww, "height": wh}
            if self._window_rect != new_rect:
                self._window_rect = new_rect
                self.setGeometry(wx, wy, ww, wh)
                self.setFixedSize(ww, wh)
                self._warned_no_rect = False
                self.update()
        except Exception:
            self._window_rect = None

    def _get_window_rect(self):
        """Вернуть текущий rect целевого окна или None."""
        return self._window_rect

    def _is_point_in_window(self, x, y):
        """Проверить, находится ли точка внутри целевого окна."""
        if not self._window_rect:
            return True  # Нет привязки — разрешаем везде
        wr = self._window_rect
        return (wr["x"] <= x <= wr["x"] + wr["width"] and
                wr["y"] <= y <= wr["y"] + wr["height"])

    def _clamp_to_window(self, x, y, w, h):
        """Ограничить прямоугольник границами целевого окна."""
        if not self._window_rect:
            return x, y, w, h
        wr = self._window_rect
        x = max(wr["x"], min(x, wr["x"] + wr["width"] - w))
        y = max(wr["y"], min(y, wr["y"] + wr["height"] - h))
        return x, y, w, h

    def _get_or_create_panel(self, idx):
        if idx not in self._voice_panels:
            self._voice_panels[idx] = VoicePanel()
        return self._voice_panels[idx]

    def _layout_panels(self):
        """Compute button rects for all visible panels."""
        for i, region in enumerate(self.regions):
            panel = self._get_or_create_panel(i)
            x, y, w, h = region["x"], region["y"], region["width"], region["height"]
            # Смещение координат если оверлей привязан к окну
            if self._window_rect:
                x -= self._window_rect["x"]
                y -= self._window_rect["y"]
            pw = min(340, max(200, w))
            px = max(0, min(x, self.width() - pw))
            py = y + h + 24
            if py + _PANEL_H > self.height():
                py = y - _PANEL_H - 8
            panel.rect = QRect(px, py, pw, _PANEL_H)

            # Buttons: left to right inside panel
            bx = px + _PANEL_GAP
            by = py + (_PANEL_H - _PANEL_BTN) // 2
            panel.btn_male = QRect(bx, by, _PANEL_BTN, _PANEL_BTN); bx += _PANEL_BTN + _PANEL_GAP
            panel.btn_female = QRect(bx, by, _PANEL_BTN, _PANEL_BTN); bx += _PANEL_BTN + _PANEL_GAP
            panel.btn_narrator = QRect(bx, by, _PANEL_BTN, _PANEL_BTN); bx += _PANEL_BTN + _PANEL_GAP + 4
            panel.btn_start = QRect(bx, by, _PANEL_BTN, _PANEL_BTN); bx += _PANEL_BTN + _PANEL_GAP
            panel.btn_stop = QRect(bx, by, _PANEL_BTN, _PANEL_BTN)

            # ▼ toggle in name bar
            panel.btn_toggle = QRect(x + w - 18, max(0, y - 20), 18, 20)

    def _on_voice_params_changed(self, role, params):
        """Обработка изменения параметров голоса (pitch, speed, name)."""
        self._voice_params[role].update(params)
        self.voice_params_changed.emit(role, params)

    def _on_panel_right_click(self, role, global_pos):
        """Контекстное меню: голоса из профилей, питч, скорость, имя."""
        from PyQt6.QtGui import QCursor

        try:
            params = self._voice_params.get(role, {})

            parent = None
            for w in QApplication.topLevelWidgets():
                if w.isVisible() and w.windowFlags() & Qt.WindowType.Window:
                    parent = w
                    break
            if parent is None:
                parent = self.window()

            menu = QMenu(parent)
            menu.setStyleSheet(
                "QMenu { background-color: #2B2930; color: #E6E1E5; border: 1px solid #49454F; "
                "padding: 4px; }"
                "QMenu::item { padding: 4px 20px; }"
                "QMenu::item:selected { background-color: #0f3460; }"
                "QMenu::item:checkable { padding-left: 6px; }"
                "QMenu::separator { height: 1px; background: #444; margin: 2px 4px; }")

            # === Profiles ===
            try:
                from voice_profiles import VoiceProfileManager
                mgr = VoiceProfileManager()
                profiles = mgr.get_all()
            except Exception:
                profiles = []

            current_ru = params.get("ru_voice", "")
            current_en = params.get("en_voice", "")

            # Group profiles by type
            edge_profiles = [p for p in profiles if p.voice_type == "edge"]
            rhvoice_profiles = [p for p in profiles if p.voice_type == "rhvoice"]
            silero_profiles = [p for p in profiles if p.voice_type == "silero"]
            other_profiles = [p for p in profiles if p.voice_type not in ("edge", "rhvoice", "silero")]

            def _add_profile_group(label, profs):
                if not profs:
                    return
                h = menu.addAction(f"  {label}")
                h.setEnabled(False)
                for p in profs:
                    is_current = (p.voice == current_ru or p.voice == current_en)
                    prefix = "  \u2713 " if is_current else "  "
                    a = menu.addAction(f"{prefix}{p.name}")
                    if is_current:
                        font = a.font()
                        font.setBold(True)
                        a.setFont(font)
                    a.triggered.connect(
                        lambda checked, profile=p, r=role: self._apply_profile(r, profile))
                menu.addSeparator()

            _add_profile_group("\u26a1 Edge-TTS (\u043e\u043d\u043b\u0430\u0439\u043d)", edge_profiles)
            _add_profile_group("\U0001f399 RHVoice (\u043e\u0444\u0444\u043b\u0430\u0439\u043d)", rhvoice_profiles)
            _add_profile_group("\U0001f916 Silero (\u043e\u0444\u0444\u043b\u0430\u0439\u043d)", silero_profiles)
            if other_profiles:
                _add_profile_group("\u2699 \u0414\u0440\u0443\u0433\u0438\u0435", other_profiles)

            # === Pitch slider ===
            pitch_widget = QWidget()
            pitch_layout = QVBoxLayout(pitch_widget)
            pitch_layout.setContentsMargins(8, 4, 8, 4)
            pitch_layout.setSpacing(2)
            pitch_label = QLabel("\u041f\u0438\u0442\u0447")
            pitch_label.setStyleSheet("color: #aaa; font-size: 11px;")
            pitch_layout.addWidget(pitch_label)
            current_pitch = params.get("pitch", 0)
            pitch_slider = QSlider(Qt.Orientation.Horizontal)
            pitch_slider.setRange(-50, 50)
            pitch_slider.setValue(current_pitch)
            pitch_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
            pitch_slider.setTickInterval(10)
            pitch_slider.setStyleSheet(
                "QSlider::groove:horizontal { background: #333; height: 4px; }"
                "QSlider::handle:horizontal { background: #4fc3f7; width: 12px; margin: -4px 0; }")
            pitch_val_label = QLabel(f"{current_pitch:+d}")
            pitch_val_label.setStyleSheet("color: #4fc3f7; font-size: 11px;")
            pitch_val_label.setFixedWidth(30)
            pitch_slider.valueChanged.connect(lambda v: pitch_val_label.setText(f"{v:+d}"))
            pitch_slider.sliderReleased.connect(
                lambda r=role, s=pitch_slider: self._apply_pitch(r, s.value()))
            h1 = QHBoxLayout()
            h1.addWidget(pitch_slider, 1)
            h1.addWidget(pitch_val_label)
            pitch_layout.addLayout(h1)
            pitch_action = QWidgetAction(menu)
            pitch_action.setDefaultWidget(pitch_widget)
            menu.addAction(pitch_action)

            # === Speed slider ===
            speed_widget = QWidget()
            speed_layout = QVBoxLayout(speed_widget)
            speed_layout.setContentsMargins(8, 4, 8, 4)
            speed_layout.setSpacing(2)
            speed_label = QLabel("\u0421\u043a\u043e\u0440\u043e\u0441\u0442\u044c")
            speed_label.setStyleSheet("color: #aaa; font-size: 11px;")
            speed_layout.addWidget(speed_label)
            current_speed = params.get("speed", 0)
            speed_slider = QSlider(Qt.Orientation.Horizontal)
            speed_slider.setRange(-50, 50)
            speed_slider.setValue(current_speed)
            speed_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
            speed_slider.setTickInterval(10)
            speed_slider.setStyleSheet(
                "QSlider::groove:horizontal { background: #333; height: 4px; }"
                "QSlider::handle:horizontal { background: #81c784; width: 12px; margin: -4px 0; }")
            speed_val_label = QLabel(f"{current_speed:+d}")
            speed_val_label.setStyleSheet("color: #81c784; font-size: 11px;")
            speed_val_label.setFixedWidth(30)
            speed_slider.valueChanged.connect(lambda v: speed_val_label.setText(f"{v:+d}"))
            speed_slider.sliderReleased.connect(
                lambda r=role, s=speed_slider: self._apply_speed(r, s.value()))
            h2 = QHBoxLayout()
            h2.addWidget(speed_slider, 1)
            h2.addWidget(speed_val_label)
            speed_layout.addLayout(h2)
            speed_action = QWidgetAction(menu)
            speed_action.setDefaultWidget(speed_widget)
            menu.addAction(speed_action)

            menu.addSeparator()

            # === Name ===
            name_widget = QWidget()
            name_layout = QHBoxLayout(name_widget)
            name_layout.setContentsMargins(8, 4, 8, 4)
            name_layout.setSpacing(4)
            name_lbl = QLabel("\u0418\u043c\u044f:")
            name_lbl.setStyleSheet("color: #aaa; font-size: 11px;")
            name_layout.addWidget(name_lbl)
            name_input = QLineEdit()
            name_input.setPlaceholderText("\u041f\u0435\u0440\u0441\u043e\u043d\u0430\u0436...")
            name_input.setText(params.get("name", ""))
            name_input.setFixedWidth(120)
            name_input.setStyleSheet(
                "QLineEdit { background: #1a1a2e; color: #ddd; border: 1px solid #555; "
                "border-radius: 3px; padding: 2px 4px; font-size: 11px; }")
            name_input.returnPressed.connect(
                lambda r=role, n=name_input: self._apply_name(r, n.text().strip()))
            name_layout.addWidget(name_input)
            name_action = QWidgetAction(menu)
            name_action.setDefaultWidget(name_widget)
            menu.addAction(name_action)

            menu.addSeparator()

            # === Test ===
            test_action = menu.addAction("\U0001f50a \u0422\u0435\u0441\u0442")
            test_action.triggered.connect(lambda: self.voice_speak.emit(role))

            menu.exec(QCursor.pos())

        except Exception as e:
            logger.error(f"[PANEL] \u041e\u0448\u0438\u0431\u043a\u0430 \u041f\u041a\u041c \u043c\u0435\u043d\u044e ({role}): {e}", exc_info=True)

    def _apply_profile(self, role, profile):
        """Apply a voice profile to the role and sync to profile manager."""
        self._voice_params[role]["ru_voice"] = profile.voice
        self._voice_params[role]["en_voice"] = profile.voice
        self._voice_params[role]["ru_voice_type"] = profile.voice_type
        self._voice_params[role]["en_voice_type"] = profile.voice_type
        self._voice_params[role]["pitch"] = profile.pitch
        self._voice_params[role]["speed"] = profile.rate
        self.voice_params_changed.emit(role, {
            "ru_voice": profile.voice, "en_voice": profile.voice,
            "ru_voice_type": profile.voice_type, "en_voice_type": profile.voice_type,
            "pitch": profile.pitch, "speed": profile.rate,
        })
        self.update()

    def _apply_pitch(self, role, pitch):
        """Применить питч."""
        self._voice_params[role]["pitch"] = pitch
        self.voice_params_changed.emit(role, {"pitch": pitch})

    def _apply_speed(self, role, speed):
        """Применить скорость."""
        self._voice_params[role]["speed"] = speed
        self.voice_params_changed.emit(role, {"speed": speed})

    def _apply_name(self, role, name):
        """Apply name for voice and sync to profile."""
        self._voice_params[role]["name"] = name
        self.voice_params_changed.emit(role, {"name": name})
        self._sync_profile_from_role(role)

    def _sync_profile_from_role(self, role):
        """Sync overlay role params back to VoiceProfileManager."""
        try:
            from voice_profiles import VoiceProfileManager
            mgr = VoiceProfileManager()
            params = self._voice_params.get(role, {})
            ru_voice = params.get("ru_voice", "")
            # Find existing profile matching this voice
            for p in mgr.get_all():
                if p.voice == ru_voice:
                    p.pitch = params.get("pitch", 0)
                    p.rate = params.get("speed", 0)
                    mgr.update(p.name, p.to_dict())
                    return
            # No matching profile — create one
            profile_name = f"{role.title()} (overlay)"
            mgr.add(profile_name, {
                "voice": ru_voice, "voice_type": params.get("ru_voice_type", "edge"),
                "pitch": params.get("pitch", 0), "rate": params.get("speed", 0),
                "volume": 1.0, "emotion": "normal", "noise_level": 0,
                "description": f"Auto-created from overlay ({role})", "is_builtin": False,
            })
        except Exception as e:
            logger.debug(f"[PANEL] Profile sync failed: {e}")

    def sync_from_profiles(self):
        """Sync overlay voice params from VoiceProfileManager (call after profile edits)."""
        try:
            from voice_profiles import VoiceProfileManager
            mgr = VoiceProfileManager()
            profiles = mgr.get_all()
            if not profiles:
                return
            # Match profiles to roles by voice code
            for role in ("male", "female", "narrator"):
                params = self._voice_params.get(role, {})
                ru_voice = params.get("ru_voice", "")
                for p in profiles:
                    if p.voice == ru_voice:
                        params["pitch"] = p.pitch
                        params["speed"] = p.rate
                        params["ru_voice_type"] = p.voice_type
                        params["en_voice_type"] = p.voice_type
                        self._voice_params[role] = params
                        break
            self.update()
        except Exception as e:
            logger.debug(f"[PANEL] Profile import failed: {e}")

    def _show_panel(self, idx):
        if idx < 0 or idx >= len(self.regions):
            return
        panel = self._get_or_create_panel(idx)
        panel.visible = True
        self._layout_panels()
        self.update()

    def _hide_panel(self, idx):
        if idx in self._voice_panels:
            self._voice_panels[idx].visible = False
            self.update()

    def set_active_role(self, role):
        for p in self._voice_panels.values():
            p.role = role
        self.update()

    def set_edit_mode(self, enabled):
        self._edit_mode = enabled
        self.update()

    def set_regions(self, regions):
        self.regions = regions
        self.update()

    def show_overlay(self):
        self.sync_from_profiles()
        self.show()
        self.raise_()
        self._update_hwnd()
        self._apply_win32_styles()
        self._layout_panels()
        for i in range(len(self.regions)):
            panel = self._get_or_create_panel(i)
            panel.visible = True
        self.update()

    def _update_hwnd(self):
        """Кэшируем HWND окна оверлея для Win32 API."""
        try:
            import ctypes.wintypes
            hwnd = int(self.winId())
            self._hwnd = hwnd
        except Exception:
            self._hwnd = None

    def _apply_win32_styles(self):
        """Применить Win32 расширенные стили для fullscreen-игр:
        WS_EX_NOACTIVATE  — не отбирать фокус у игры
        WS_EX_TOOLWINDOW  — не показывать в Alt+Tab
        НЕ используем WS_EX_TRANSPARENT — чтобы кнопки панелей работали.
        Клики через прозрачные области пропускаются через Qt WA_TransparentForMouseEvents.
        """
        if self._hwnd is None:
            return
        try:
            GWL_EXSTYLE = -20
            WS_EX_NOACTIVATE = 0x08000000
            WS_EX_TOOLWINDOW = 0x00000080
            flags = WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
            old = ctypes.windll.user32.GetWindowLongW(self._hwnd, GWL_EXSTYLE)
            ctypes.windll.user32.SetWindowLongW(self._hwnd, GWL_EXSTYLE, old | flags)
            logger.debug(f"[OVERLAY] Applied Win32 styles: 0x{flags:08X}")
        except Exception as e:
            logger.warning(f"[OVERLAY] Failed to apply Win32 styles: {e}")

    def _force_topmost(self):
        """Принудительно поднять оверлей поверх fullscreen-приложений через Win32."""
        if not self.isVisible():
            return
        if self._hwnd is None:
            self._update_hwnd()
        if self._hwnd is None:
            return
        try:
            HWND_TOPMOST = -1
            SWP_NOMOVE = 0x0002
            SWP_NOSIZE = 0x0001
            SWP_NOACTIVATE = 0x0010
            SWP_NOSENDCHANGING = 0x0400
            ctypes.windll.user32.SetWindowPos(
                self._hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOSENDCHANGING)
        except Exception:
            pass

    def _diag_overlay(self):
        """Разовая диагностика отрисовки: почему рамка может не быть видна.

        Симптом: в полноэкранном режиме игры кнопка-шестерёнка видна, а
        зелёная рамка — нет. Кнопка это отдельный QPushButton (рисуется
        виджетом), рамка же рисуется вручную в paintEvent и может быть
        срезана clip'ом по окну игры или погашена нулевой прозрачностью.
        """
        now = time.time()
        if now - getattr(self, "_diag_at", 0.0) < 5.0:
            return
        self._diag_at = now
        wr = self._window_rect
        regs = self.regions or []
        logger.info(
            "[Overlay] регионов=%d window_rect=%s frame_opacity=%d "
            "window_opacity=%.2f",
            len(regs), wr, self._frame_opacity, self._window_opacity)
        for r in regs:
            x, y = r["x"], r["y"]
            w, h = r.get("width", 0), r.get("height", 0)
            if not w or not h:
                logger.warning("[Overlay] регион %r имеет нулевой размер", r)
                continue
            if wr:
                inside = (x >= wr["x"] and y >= wr["y"]
                          and x + w <= wr["x"] + wr["width"]
                          and y + h <= wr["y"] + wr["height"])
                if not inside:
                    logger.warning(
                        "[Overlay] регион (%d,%d,%d,%d) ВНЕ окна игры %s — "
                        "будет срезан clip'ом и не виден", x, y, w, h, wr)
            else:
                screen = QApplication.primaryScreen().geometry()
                inside = (x >= 0 and y >= 0
                          and x + w <= screen.width()
                          and y + h <= screen.height())
                if not inside:
                    logger.warning(
                        "[Overlay] регион (%d,%d,%d,%d) выходит за экран %dx%d",
                        x, y, w, h, screen.width(), screen.height())
            break   # одного региона достаточно для диагностики

    def paintEvent(self, event):
        if not self.regions:
            self._diag_overlay()
            return
        try:
            self._diag_overlay()
        except Exception as e:
            logger.debug(f"[Overlay] diag failed: {e}")
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        alpha = int(self._frame_opacity * 2.55)
        panel_alpha = int(self._panel_opacity * 2.55)

        # Клиппинг к целевому окну
        if self._window_rect:
            wr = self._window_rect
            # Раньше clip был строго по окну игры. При переходе в
            # полноэкранный режим окно меняет размер, и рамка (координаты
            # в экранных) уезжала за clip — шестерёнка была видна (это
            # отдельный виджет), а зелёная рамка пропадала.
            # Теперь clip = объединение окна игры и самих регионов.
            # Координаты ВИДЖЕТА = экранные минус позиция окна игры.
            clip = QRect(0, 0, wr["width"], wr["height"])
            for r in self.regions:
                rx = r["x"] - wr["x"]
                ry = r["y"] - wr["y"]
                clip = clip.united(
                    QRect(rx, ry, r.get("width", 0), r.get("height", 0)))
            # QRect.clipRect() в PyQt6 НЕ существует (только intersected) —
            # вызов падал с AttributeError прямо в paintEvent, из-за чего
            # зелёная рамка не рисовалась вообще, а кнопка-шестерёнка
            # (отдельный виджет) оставалась видна.
            p.setClipRect(clip.intersected(self.rect()))

        self._layout_panels()

        for i, region in enumerate(self.regions):
            x, y, w, h = region["x"], region["y"], region["width"], region["height"]
            # Смещение координат если оверлей привязан к окну
            if self._window_rect:
                x -= self._window_rect["x"]
                y -= self._window_rect["y"]
            name = region.get("name", f"R{i+1}")
            locked = region.get("locked", False)

            color = region.get("color")
            if color is None:
                cols = [QColor(76, 175, 80), QColor(33, 150, 243), QColor(255, 152, 0),
                        QColor(156, 39, 176), QColor(244, 67, 54), QColor(0, 188, 212)]
                color = cols[i % len(cols)]
            else:
                color = QColor(color) if isinstance(color, str) else color

            fc = QColor(color.red(), color.green(), color.blue(), alpha)
            pen = QPen(QColor(128, 128, 128, alpha), 2, Qt.PenStyle.DashLine) if locked else QPen(fc, 3)
            p.setPen(pen)
            p.setBrush(QBrush(Qt.GlobalColor.transparent))
            p.drawRoundedRect(QRect(x, y, w, h), 4, 4)

            # Name bar
            ny = max(0, y - 20)
            tr = QRect(x, ny, w, 20)
            bg = QColor(128, 128, 128, 200) if locked else QColor(color.red(), color.green(), color.blue(), 200)
            p.fillRect(tr, bg)

            if locked:
                p.setPen(QColor("yellow"))
                p.setFont(QFont("Arial", 10))
                p.drawText(QRect(x + 2, ny, 18, 20), Qt.AlignmentFlag.AlignCenter, "\U0001f512")

            # ▼ toggle
            if not locked and i in self._voice_panels:
                panel = self._voice_panels[i]
                if panel.visible:
                    p.setPen(QColor("white"))
                    p.setFont(QFont("Arial", 8))
                    p.drawText(QRect(x + w - 16, ny, 16, 20),
                               Qt.AlignmentFlag.AlignCenter, "\u25bc")
                else:
                    p.setPen(QColor(200, 200, 200, 180))
                    p.setFont(QFont("Arial", 8))
                    p.drawText(QRect(x + w - 16, ny, 16, 20),
                               Qt.AlignmentFlag.AlignCenter, "\u25b6")

            # Resize handle
            if not locked:
                p.setBrush(QBrush(color))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawRect(QRect(x + w - 5, y + h - 5, 10, 10))

        # Draw inline panels
        for i, region in enumerate(self.regions):
            if i not in self._voice_panels:
                continue
            panel = self._voice_panels[i]
            if not panel.visible:
                continue

            pr = panel.rect
            # Panel background
            p.setPen(QPen(QColor(80, 80, 80, panel_alpha), 1))
            p.setBrush(QBrush(QColor(25, 25, 35, panel_alpha)))
            p.drawRoundedRect(pr, 6, 6)

            # Internet status dot
            dot_color = QColor(76, 175, 80, 220) if self._internet_ok else QColor(244, 67, 54, 220)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(dot_color))
            dot_x = pr.right() - 12
            dot_y = pr.top() + (pr.height() - 6) // 2
            p.drawEllipse(dot_x, dot_y, 6, 6)

            # Role buttons
            role_colors = {"male": "#4fc3f7", "female": "#f48fb1", "narrator": "#ce93d8"}
            role_labels = {"male": "\u2642", "female": "\u2640", "narrator": "\u2660"}
            for role_key, btn_rect in [("male", panel.btn_male), ("female", panel.btn_female),
                                        ("narrator", panel.btn_narrator)]:
                c = QColor(role_colors[role_key])
                is_active = panel.role == role_key
                if is_active:
                    p.setPen(QPen(c, 2))
                    p.setBrush(QBrush(QColor(c.red(), c.green(), c.blue(), 40)))
                else:
                    p.setPen(QPen(QColor(80, 80, 80), 1))
                    p.setBrush(QBrush(QColor(40, 40, 50, panel_alpha)))
                p.drawRoundedRect(btn_rect, 4, 4)
                # Icon text
                p.setPen(c if is_active else QColor(160, 160, 160))
                p.setFont(QFont("Arial", 12, QFont.Weight.Bold))
                p.drawText(btn_rect, Qt.AlignmentFlag.AlignCenter, role_labels[role_key])

            # Start button (green)
            p.setPen(QPen(QColor(40, 100, 40), 1))
            p.setBrush(QBrush(QColor(30, 80, 30, panel_alpha)))
            p.drawRoundedRect(panel.btn_start, 4, 4)
            p.setPen(QColor(129, 199, 132))
            p.setFont(QFont("Arial", 11, QFont.Weight.Bold))
            p.drawText(panel.btn_start, Qt.AlignmentFlag.AlignCenter, "\u25b6")

            # Stop button (red)
            p.setPen(QPen(QColor(100, 40, 40), 1))
            p.setBrush(QBrush(QColor(80, 30, 30, panel_alpha)))
            p.drawRoundedRect(panel.btn_stop, 4, 4)
            p.setPen(QColor(239, 154, 154))
            p.setFont(QFont("Arial", 11, QFont.Weight.Bold))
            p.drawText(panel.btn_stop, Qt.AlignmentFlag.AlignCenter, "\u25a0")

        # OCR text under first frame
        if self.regions:
            r = self.regions[0]
            tx = r["x"]
            ty = r["y"] + r["height"] + 4
            # Смещение координат если оверлей привязан к окну
            if self._window_rect:
                tx -= self._window_rect["x"]
                ty -= self._window_rect["y"]
            if self._last_ocr_text:
                p.setPen(QColor(200, 200, 200, 90))
                p.setFont(QFont("Arial", 9))
                p.drawText(QRect(tx, ty, min(r["width"], 500), 18),
                           Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                           self._last_ocr_text)
            right_part = ""
            if self._last_scan_seconds > 0:
                right_part = f"{self._last_scan_seconds:.1f}s"
            if self._last_confidence > 0:
                conf_int = int(self._last_confidence)
                if right_part:
                    right_part += f"  {conf_int}%"
                else:
                    right_part = f"{conf_int}%"
            if right_part:
                p.setPen(QColor(76, 175, 80, 120))
                p.setFont(QFont("Arial", 9))
                p.drawText(QRect(tx + min(r["width"], 500) - 80, ty, 80, 18),
                           Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop,
                           right_part)

        # ── Scan indicator: pulsing orange dot on first region ──
        if self._scanning and self.regions:
            r = self.regions[0]
            rx, ry = r["x"], r["y"]
            if self._window_rect:
                rx -= self._window_rect["x"]
                ry -= self._window_rect["y"]
            pulse_alpha = 120 + int(80 * abs(self._scan_pulse - 10) / 10)
            dot_size = 10 + int(4 * abs(self._scan_pulse - 10) / 10)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(QColor(255, 152, 0, pulse_alpha)))
            p.drawEllipse(rx - dot_size // 2, ry - dot_size // 2, dot_size, dot_size)
            p.setPen(QColor(255, 152, 0, 200))
            p.setFont(QFont("Arial", 9, QFont.Weight.Bold))
            p.drawText(QRect(rx + 10, ry - 8, 120, 16),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       "OCR...")

        # ── STT indicator: blinking green mic on first region ──
        if self._stt_active and self.regions:
            r = self.regions[0]
            rx, ry = r["x"], r["y"]
            if self._window_rect:
                rx -= self._window_rect["x"]
                ry -= self._window_rect["y"]
            blink_on = self._stt_blink < 4
            if blink_on:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QBrush(QColor(76, 175, 80, 200)))
                p.drawEllipse(rx - 6, ry - 6, 12, 12)
                p.setPen(QColor(76, 175, 80, 230))
            else:
                p.setPen(QColor(76, 175, 80, 100))
            p.setFont(QFont("Arial", 9, QFont.Weight.Bold))
            p.drawText(QRect(rx + 10, ry - 8, 200, 16),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       "MIC " + (self._stt_last_text[:30] if self._stt_last_text else "..."))

    def mousePressEvent(self, event: QMouseEvent):
        gp = event.globalPosition().toPoint()

        if event.button() == Qt.MouseButton.RightButton:
            # Check if click is on a panel button
            for i, region in enumerate(self.regions):
                if i in self._voice_panels:
                    panel = self._voice_panels[i]
                    if panel.visible:
                        for role_key, btn_rect in [("male", panel.btn_male), ("female", panel.btn_female),
                                                    ("narrator", panel.btn_narrator)]:
                            if btn_rect.contains(gp.x(), gp.y()):
                                self._on_panel_right_click(role_key, event.globalPosition().toPoint())
                                event.accept()
                                return
            # Check border right-click
            for i, region in enumerate(self.regions):
                if self._is_on_border(gp, region):
                    self.region_right_clicked.emit(i)
                    event.accept()
                    return
            event.ignore()
            return

        if event.button() == Qt.MouseButton.LeftButton and self._edit_mode:
            # Panel button clicks
            for i, region in enumerate(self.regions):
                if i in self._voice_panels:
                    panel = self._voice_panels[i]
                    if panel.visible:
                        # Role buttons
                        for role_key, btn_rect in [("male", panel.btn_male), ("female", panel.btn_female),
                                                    ("narrator", panel.btn_narrator)]:
                            if btn_rect.contains(gp.x(), gp.y()):
                                self.voice_speak.emit(role_key)
                                event.accept()
                                return
                        # Start
                        if panel.btn_start.contains(gp.x(), gp.y()):
                            self.start_clicked.emit()
                            event.accept()
                            return
                        # Stop
                        if panel.btn_stop.contains(gp.x(), gp.y()):
                            self.stop_clicked.emit()
                            event.accept()
                            return

            # Resize handle
            for i, region in enumerate(self.regions):
                if not region.get("locked") and self._is_on_handle(gp, region):
                    self.resizing = i
                    self.resize_edge = "br"
                    event.accept()
                    return

            # ▼ toggle
            for i, region in enumerate(self.regions):
                if not region.get("locked") and self._is_on_toggle(gp, region):
                    vis = i in self._voice_panels and self._voice_panels[i].visible
                    if vis:
                        self._hide_panel(i)
                    else:
                        self._show_panel(i)
                    event.accept()
                    return

            # Name bar drag
            for i, region in enumerate(self.regions):
                if not region.get("locked") and self._is_on_name(gp, region):
                    self.dragging = i
                    self.drag_offset = QPoint(gp.x() - region["x"], gp.y() - region["y"])
                    event.accept()
                    return

            # Border drag
            for i, region in enumerate(self.regions):
                if not region.get("locked") and self._is_on_border(gp, region) == "border":
                    self.dragging = i
                    self.drag_offset = QPoint(gp.x() - region["x"], gp.y() - region["y"])
                    event.accept()
                    return

            # Inside frame drag
            for i, region in enumerate(self.regions):
                if not region.get("locked") and self._is_inside(gp, region):
                    self.dragging = i
                    self.drag_offset = QPoint(gp.x() - region["x"], gp.y() - region["y"])
                    event.accept()
                    return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if not self._edit_mode:
            super().mouseMoveEvent(event)
            return
        gp = event.globalPosition().toPoint()

        if self.dragging is not None:
            r = self.regions[self.dragging]
            new_x = gp.x() - self.drag_offset.x()
            new_y = gp.y() - self.drag_offset.y()
            # Ограничение в пределах экрана
            new_x = max(0, min(new_x, self.width() - r["width"]))
            new_y = max(0, min(new_y, self.height() - r["height"]))
            # Ограничение в пределах целевого окна
            if self._window_rect:
                wr = self._window_rect
                new_x = max(wr["x"], min(new_x, wr["x"] + wr["width"] - r["width"]))
                new_y = max(wr["y"], min(new_y, wr["y"] + wr["height"] - r["height"]))
            r["x"] = new_x
            r["y"] = new_y
            self._layout_panels()
            self.update()
            event.accept()
            return

        if self.resizing is not None:
            r = self.regions[self.resizing]
            new_w = max(50, min(gp.x() - r["x"], self.width() - r["x"]))
            new_h = max(50, min(gp.y() - r["y"], self.height() - r["y"]))
            # Ограничение в пределах целевого окна
            if self._window_rect:
                wr = self._window_rect
                max_w = wr["x"] + wr["width"] - r["x"]
                max_h = wr["y"] + wr["height"] - r["y"]
                new_w = max(50, min(new_w, max_w))
                new_h = max(50, min(new_h, max_h))
            r["width"] = new_w
            r["height"] = new_h
            self._layout_panels()
            self.update()
            event.accept()
            return

        hit = any(not r.get("locked") and (self._is_on_handle(gp, r) or self._is_on_border(gp, r))
                  for r in self.regions)
        self.setCursor(Qt.CursorShape.SizeAllCursor if hit else Qt.CursorShape.ArrowCursor)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if self.dragging is not None:
            r = self.regions[self.dragging]
            self.region_moved.emit(self.dragging, r["x"], r["y"], r["width"], r["height"])
            self.dragging = None
        if self.resizing is not None:
            r = self.regions[self.resizing]
            self.region_moved.emit(self.resizing, r["x"], r["y"], r["width"], r["height"])
            self.resizing = None
        event.accept()

    def _is_on_name(self, pos, region):
        x, y, w = region["x"], region["y"], region["width"]
        return QRect(x, max(0, y - 20), w, 20).contains(pos)

    def _is_on_toggle(self, pos, region):
        """Попадание в ▼ справа в name bar."""
        x, y, w = region["x"], region["y"], region["width"]
        return QRect(x + w - 16, max(0, y - 20), 16, 20).contains(pos)

    def _is_on_border(self, pos, region):
        x, y, w, h = region["x"], region["y"], region["width"], region["height"]
        if self._is_on_name(pos, region):
            return "name"
        b = 8
        if QRect(x, y, w, h).contains(pos):
            if abs(pos.x() - x) < b or abs(pos.x() - (x + w)) < b:
                return "border"
            if abs(pos.y() - y) < b or abs(pos.y() - (y + h)) < b:
                return "border"
        return False

    def _is_on_handle(self, pos, region):
        x, y, w, h = region["x"], region["y"], region["width"], region["height"]
        return abs(pos.x() - (x + w)) < 12 and abs(pos.y() - (y + h)) < 12

    def _is_inside(self, pos, region):
        return QRect(region["x"], region["y"], region["width"], region["height"]).contains(pos)
