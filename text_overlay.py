# -*- coding: utf-8 -*-
"""
Floating text overlay window for OCR / STT results.

Features: drag, resize, hide, delete, enable/disable updates,
adjustable opacity, font size, text & background color, always-on-top,
word-wrap, clear, line limit. State persists to config.
"""
import logging

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QSlider,
    QTextEdit, QCheckBox, QColorDialog, QSizeGrip, QApplication, QFrame,
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer
from PyQt6.QtGui import QColor, QFont

logger = logging.getLogger(__name__)


class TextOverlay(QWidget):
    """A standalone, always-on-top, frameless text HUD window."""

    deleted = pyqtSignal(object)  # emits self
    action_clicked = pyqtSignal()  # action button pressed

    def __init__(self, title="Text", settings=None, config_key="overlays.text",
                 action_label=None, parent=None):
        super().__init__(parent)
        self._title = title
        self._settings = settings
        self._config_key = config_key
        self._action_label = action_label

        self._enabled = True        # accept updates?
        self._user_hidden = False   # hidden by user (don't auto-show)
        self._auto_show = True      # show on first update if not hidden
        self._drag_pos = None
        self._max_lines = 200
        self._text_color = "#c9d1d9"
        self._bg_color = "#0d1117"
        self._loading = True        # suppress saves during init
        self._scanning = False      # OCR scan indicator
        self._scan_pulse = 0

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool |
            Qt.WindowType.WindowDoesNotAcceptFocus
        )

        # Debounced save so dragging/resizing doesn't rewrite config.json on
        # every single move/resize event
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self._save_state)

        self._build_ui()
        self._load_state()

    # ── UI ──────────────────────────────────────────────────────
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Title bar
        bar = QWidget()
        bar.setObjectName("ovl_bar")
        bar.setFixedHeight(26)
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(6, 2, 4, 2)
        bl.setSpacing(4)

        self._title_lbl = QLabel(self._title)
        self._title_lbl.setStyleSheet("color:#fff; font-weight:bold; font-size:11px;")
        bl.addWidget(self._title_lbl, 1)

        if self._action_label:
            self._btn_action = QPushButton(self._action_label)
            self._btn_action.setFixedHeight(20)
            self._btn_action.setFixedWidth(64)
            self._btn_action.setToolTip("Trigger action")
            self._btn_action.clicked.connect(self.action_clicked.emit)
            bl.addWidget(self._btn_action)

        self._btn_hide = QPushButton("–")
        self._btn_hide.setFixedSize(20, 20)
        self._btn_hide.setToolTip("Hide")
        self._btn_hide.clicked.connect(self.hide_overlay)
        bl.addWidget(self._btn_hide)

        self._btn_settings = QPushButton("⚙")
        self._btn_settings.setFixedSize(20, 20)
        self._btn_settings.setToolTip("Settings")
        self._btn_settings.clicked.connect(self._toggle_settings)
        bl.addWidget(self._btn_settings)

        self._btn_close = QPushButton("×")
        self._btn_close.setFixedSize(20, 20)
        self._btn_close.setToolTip("Delete overlay")
        self._btn_close.clicked.connect(self.delete_overlay)
        bl.addWidget(self._btn_close)

        root.addWidget(bar)

        # Settings panel
        self._settings_panel = QWidget()
        self._settings_panel.setObjectName("ovl_sett")
        self._settings_panel.setVisible(False)
        sp = QVBoxLayout(self._settings_panel)
        sp.setContentsMargins(8, 6, 8, 6)
        sp.setSpacing(4)

        row = QHBoxLayout()
        row.addWidget(QLabel("Opacity"))
        self._opacity = QSlider(Qt.Orientation.Horizontal)
        self._opacity.setRange(10, 100)
        self._opacity.setValue(90)
        self._opacity.valueChanged.connect(self._on_opacity)
        row.addWidget(self._opacity, 1)
        sp.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel("Font"))
        self._font = QSlider(Qt.Orientation.Horizontal)
        self._font.setRange(8, 32)
        self._font.setValue(13)
        self._font.valueChanged.connect(self._on_font)
        row.addWidget(self._font, 1)
        sp.addLayout(row)

        row = QHBoxLayout()
        self._btn_text_color = QPushButton("Text color")
        self._btn_text_color.clicked.connect(lambda: self._pick_color("text"))
        self._btn_bg_color = QPushButton("BG color")
        self._btn_bg_color.clicked.connect(lambda: self._pick_color("bg"))
        row.addWidget(self._btn_text_color)
        row.addWidget(self._btn_bg_color)
        sp.addLayout(row)

        row = QHBoxLayout()
        self._chk_enabled = QCheckBox("Enable updates")
        self._chk_enabled.setChecked(True)
        self._chk_enabled.toggled.connect(self._on_enabled)
        row.addWidget(self._chk_enabled)
        self._chk_wrap = QCheckBox("Wrap")
        self._chk_wrap.setChecked(True)
        self._chk_wrap.toggled.connect(self._on_wrap)
        row.addWidget(self._chk_wrap)
        sp.addLayout(row)

        row = QHBoxLayout()
        self._btn_clear = QPushButton("Clear")
        self._btn_clear.clicked.connect(self.clear_text)
        self._btn_reset = QPushButton("Reset pos")
        self._btn_reset.clicked.connect(self._reset_pos)
        row.addWidget(self._btn_clear)
        row.addWidget(self._btn_reset)
        sp.addLayout(row)

        # Toggle buttons for OCR/STT overlays (only in OCR overlay)
        if self._action_label == "Scan OCR":
            row = QHBoxLayout()
            self._btn_toggle_ocr = QPushButton("Toggle OCR overlay")
            self._btn_toggle_ocr.setCheckable(True)
            self._btn_toggle_ocr.setChecked(True)
            self._btn_toggle_ocr.clicked.connect(self._on_toggle_ocr)
            row.addWidget(self._btn_toggle_ocr)
            self._btn_toggle_stt = QPushButton("Toggle STT overlay")
            self._btn_toggle_stt.setCheckable(True)
            self._btn_toggle_stt.setChecked(False)
            self._btn_toggle_stt.clicked.connect(self._on_toggle_stt)
            row.addWidget(self._btn_toggle_stt)
            sp.addLayout(row)

        root.addWidget(self._settings_panel)

        # Content
        self._text = QTextEdit()
        self._text.setReadOnly(True)
        self._text.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._text.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._text.setFrameShape(QFrame.Shape.NoFrame)
        root.addWidget(self._text, 1)

        # Resize grip
        self._grip = QSizeGrip(self)
        self._grip.setFixedSize(16, 16)

        self.setStyleSheet(self._base_style())

    def _base_style(self):
        return """
        TextOverlay { background: #161b22; border: 1px solid #30363d; border-radius: 6px; }
        QWidget#ovl_bar { background: #21262d; border-top-left-radius: 6px; border-top-right-radius: 6px; }
        QWidget#ovl_sett { background: #0d1117; }
        QPushButton { background: #30363d; color: #c9d1d9; border: none; border-radius: 3px;
                      font-size: 11px; padding: 2px; }
        QPushButton:hover { background: #3d444d; }
        QLabel { color: #c9d1d9; font-size: 11px; }
        QTextEdit { background: #0d1117; color: #c9d1d9; border: none; padding: 6px; }
        QSlider::groove:horizontal { background: #30363d; height: 4px; border-radius: 2px; }
        QSlider::handle:horizontal { background: #58a6ff; width: 12px; margin: -4px 0; border-radius: 6px; }
        QCheckBox { color: #c9d1d9; font-size: 11px; }
        QSizeGrip { background: transparent; }
        """

    # ── Public API ─────────────────────────────────────────────
    def set_title(self, t):
        self._title = t
        self._title_lbl.setText(t)

    def set_action_label(self, t):
        if hasattr(self, "_btn_action"):
            self._btn_action.setText(t)

    def set_text(self, text):
        """Replace content (OCR-style)."""
        if not self._enabled:
            return
        self._text.setPlainText(text or "")
        self._maybe_auto_show()

    def append_line(self, text, prefix=""):
        """Append a line (STT-style)."""
        if not self._enabled:
            return
        line = f"{prefix}{text}" if prefix else text
        self._text.append(line)
        self._trim_lines()
        self._maybe_auto_show()

    def replace_last_line(self, text):
        """Replace the last line in-place (YouTube-style live subtitle)."""
        if not self._enabled:
            return
        cur = self._text.toPlainText()
        lines = cur.split("\n")
        if lines and lines[-1].strip():
            lines[-1] = text
        else:
            lines.append(text)
        self._text.setPlainText("\n".join(lines))
        self._maybe_auto_show()

    def clear_text(self):
        self._text.clear()

    def set_scanning(self, state: bool):
        """Show pulsing scan indicator on title bar."""
        self._scanning = state
        if state:
            self._scan_pulse = 0
            self._scan_timer = QTimer(self)
            self._scan_timer.timeout.connect(self._tick_scan)
            self._scan_timer.start(80)
        elif hasattr(self, '_scan_timer'):
            self._scan_timer.stop()
        self._title_lbl.setText("OCR..." if state else self._title)

    def _tick_scan(self):
        self._scan_pulse = (self._scan_pulse + 1) % 20
        if self._scanning:
            alpha = 150 + int(100 * abs(self._scan_pulse - 10) / 10)
            self._title_lbl.setStyleSheet(f"color:rgb(255,{152 + self._scan_pulse * 5},0); font-weight:bold; font-size:11px;")

    def set_enabled(self, state):
        self._enabled = state
        self._chk_enabled.setChecked(state)

    def hide_overlay(self):
        self._user_hidden = True
        self.hide()
        self._save_state()

    def show_overlay(self):
        self._user_hidden = False
        self.show()
        self.raise_()
        self._save_state()

    def toggle_visible(self):
        if self.isVisible() and not self._user_hidden:
            self.hide_overlay()
        else:
            self.show_overlay()

    def delete_overlay(self):
        self._save_state()
        self.deleted.emit(self)
        self.deleteLater()

    # ── Internals ──────────────────────────────────────────────
    def _maybe_auto_show(self):
        if self._auto_show and not self._user_hidden and not self.isVisible():
            self.show()
            self.raise_()

    def _toggle_settings(self):
        self._settings_panel.setVisible(not self._settings_panel.isVisible())

    def _on_opacity(self, v):
        self.setWindowOpacity(v / 100.0)
        self._save_state()

    def _on_font(self, v):
        self._text.setFont(QFont("Consolas", v))
        self._save_state()

    def _on_enabled(self, state):
        self._enabled = state
        self._save_state()

    def _on_wrap(self, state):
        self._text.setLineWrapMode(
            QTextEdit.LineWrapMode.WidgetWidth if state else QTextEdit.LineWrapMode.NoWrap)
        self._save_state()

    def _on_toggle_ocr(self, checked):
        """Toggle OCR text overlay visibility."""
        if hasattr(self, '_toggle_ocr_callback'):
            self._toggle_ocr_callback(checked)

    def _on_toggle_stt(self, checked):
        """Toggle STT overlays visibility."""
        if hasattr(self, '_toggle_stt_callback'):
            self._toggle_stt_callback(checked)

    def set_toggle_callbacks(self, ocr_cb=None, stt_cb=None):
        """Set callbacks for overlay toggle buttons."""
        self._toggle_ocr_callback = ocr_cb
        self._toggle_stt_callback = stt_cb

    def _pick_color(self, which):
        cur = QColor(self._text_color if which == "text" else self._bg_color)
        c = QColorDialog.getColor(cur, self, f"Select {which} color")
        if c.isValid():
            if which == "text":
                self._text_color = c.name()
            else:
                self._bg_color = c.name()
            self._apply_colors()
            self._save_state()

    def _apply_colors(self):
        self._text.setStyleSheet(
            f"QTextEdit {{ background: {self._bg_color}; color: {self._text_color}; "
            f"border: none; padding: 6px; }}")

    def _trim_lines(self):
        doc = self._text.document()
        if doc.blockCount() > self._max_lines:
            cur = self._text.textCursor()
            cur.movePosition(cur.MoveOperation.Start)
            cur.movePosition(cur.MoveOperation.Down, cur.MoveMode.KeepAnchor,
                             doc.blockCount() - self._max_lines)
            cur.removeSelectedText()

    def _reset_pos(self):
        screen = QApplication.primaryScreen().geometry()
        self.setGeometry(screen.width() // 2 - 200, screen.height() // 2 - 150, 400, 300)
        self._save_state()

    # ── Drag ───────────────────────────────────────────────────
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and e.position().y() <= 26:
            self._drag_pos = e.globalPosition().toPoint() - self.pos()
            e.accept()
        else:
            super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag_pos is not None and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_pos)
            e.accept()
        else:
            super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._drag_pos is not None:
            self._drag_pos = None
            self._save_state()
        super().mouseReleaseEvent(e)

    def moveEvent(self, e):
        super().moveEvent(e)
        self._save_timer.start(400)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._save_timer.start(400)

    # ── Persistence ────────────────────────────────────────────
    def _load_state(self):
        st = {}
        if self._settings:
            try:
                st = self._settings.get(self._config_key, {}) or {}
            except Exception:
                st = {}
        x = st.get("x"); y = st.get("y"); w = st.get("w", 400); h = st.get("h", 300)
        if x is not None and y is not None:
            self.setGeometry(int(x), int(y), int(w), int(h))
        else:
            self._reset_pos()
        self._text_color = st.get("text_color", "#c9d1d9")
        self._bg_color = st.get("bg_color", "#0d1117")
        opacity = st.get("opacity", 90)
        font = st.get("font", 13)
        enabled = st.get("enabled", True)
        wrap = st.get("wrap", True)

        self.setWindowOpacity(opacity / 100.0)
        self._opacity.setValue(int(opacity))
        self._font.setValue(int(font))
        self._text.setFont(QFont("Consolas", int(font)))
        self._chk_enabled.setChecked(enabled)
        self._enabled = enabled
        self._chk_wrap.setChecked(wrap)
        self._text.setLineWrapMode(
            QTextEdit.LineWrapMode.WidgetWidth if wrap else QTextEdit.LineWrapMode.NoWrap)
        self._apply_colors()

        if st.get("visible", True) and not st.get("user_hidden", False):
            self.show()
        else:
            self._user_hidden = True
            self.hide()
        self._loading = False

    def _save_state(self):
        if not self._settings or self._loading:
            return
        try:
            geo = self.geometry()
            st = {
                "x": geo.x(), "y": geo.y(), "w": geo.width(), "h": geo.height(),
                "opacity": self._opacity.value(),
                "font": self._font.value(),
                "text_color": self._text_color,
                "bg_color": self._bg_color,
                "enabled": self._enabled,
                "wrap": self._chk_wrap.isChecked(),
                "visible": self.isVisible(),
                "user_hidden": self._user_hidden,
            }
            self._settings.set(self._config_key, st)
        except Exception as e:
            logger.debug(f"[OVERLAY] save failed: {e}")
