# -*- coding: utf-8 -*-
"""
Screen OCR + TTS — MORT-style interface.
Bottom tab bar, dark theme, compact layout.
Auto-scaling font, proper scroll, DPI-aware.
"""
import sys
import os
import logging
import asyncio
import threading
import json
import time
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QLineEdit, QComboBox, QSlider, QTabWidget,
    QGroupBox, QSpinBox, QCheckBox, QMessageBox, QFileDialog,
    QFrame, QScrollArea, QSystemTrayIcon, QMenu, QStyle, QTextEdit,
    QListWidget, QDialog, QDialogButtonBox, QGridLayout, QListWidgetItem,
    QTreeWidget, QTreeWidgetItem
)
from PyQt6.QtGui import QFont, QColor, QAction, QCursor, QTextOption
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal, QSize
from PyQt6.QtWidgets import QInputDialog

from settings import Settings
from ocr_wrapper import OCRWrapper as OCREngine
from tts_engine import TTSEngine
from overlay import OverlayWidget
from live_scanner import LiveScanner
from region_selector import RegionSelector
from region_overlay import RegionOverlay
from text_overlay import TextOverlay
from ocr_indicator import OCRIndicator
from localization import tr, set_language
from translator import Translator

logger = logging.getLogger(__name__)


class RegionDialog(QDialog):
    COLORS = [
        ("Green", "#4caf50"), ("Blue", "#2196f3"), ("Orange", "#ff9800"),
        ("Purple", "#9c27b0"), ("Red", "#f44336"), ("Cyan", "#00bcd4"),
        ("Yellow", "#ffeb3b"), ("White", "#ffffff"),
    ]

    def __init__(self, region=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Region Properties")
        self.setMinimumSize(350, 280)
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("Name:"))
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
        size.addWidget(QLabel("Width:"))
        self.w_spin = QSpinBox()
        self.w_spin.setRange(50, 10000)
        self.w_spin.setValue(region.get("width", 200) if region else 200)
        size.addWidget(self.w_spin)
        size.addWidget(QLabel("Height:"))
        self.h_spin = QSpinBox()
        self.h_spin.setRange(50, 10000)
        self.h_spin.setValue(region.get("height", 100) if region else 100)
        size.addWidget(self.h_spin)
        layout.addLayout(size)

        layout.addWidget(QLabel("Color:"))
        self.color_combo = QComboBox()
        for name_key, hex_color in self.COLORS:
            self.color_combo.addItem(name_key, hex_color)
        if region and region.get("color"):
            for i, (name_key, hex_color) in enumerate(self.COLORS):
                if hex_color == region["color"]:
                    self.color_combo.setCurrentIndex(i)
                    break
        layout.addWidget(self.color_combo)

        self.click_through_check = QCheckBox("Click-through")
        self.click_through_check.setChecked(region.get("click_through", False) if region else False)
        layout.addWidget(self.click_through_check)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_region(self):
        return {
            "name": self.name_edit.text() or f"Region",
            "x": self.x_spin.value(), "y": self.y_spin.value(),
            "width": self.w_spin.value(), "height": self.h_spin.value(),
            "color": self.color_combo.currentData(),
            "click_through": self.click_through_check.isChecked(),
        }


class OCRWorker(QThread):
    result_ready = pyqtSignal(str)
    error_occurred = pyqtSignal(str)

    def __init__(self, ocr_engine):
        super().__init__()
        self.ocr_engine = ocr_engine

    def run(self):
        loop = None
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            result = loop.run_until_complete(self.ocr_engine.recognize_async())
            if isinstance(result, tuple):
                text = result[0] if result else ""
            else:
                text = result or ""
            self.result_ready.emit(str(text))
        except Exception as e:
            self.error_occurred.emit(str(e))
        finally:
            if loop:
                loop.close()


class MainWindow(QMainWindow):
    text_detected_signal = pyqtSignal(str)
    gui_invoke = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self.gui_invoke.connect(self._on_gui_invoke)
        self.settings = Settings()

        ui_lang = self.settings.get("ui.language", "ru")
        set_language(ui_lang)

        self.ocr = None
        self.ocr_ready = False
        self._selected_pid = None
        self._selected_process_name = None
        self._scan_mode = "OCR"
        self.tts = TTSEngine(self.settings)
        self.regions = []
        self._load_regions()

        self.live_scanner = LiveScanner(None, self.settings, self.tts)
        self.live_scanner.enable_tts_streaming(self.tts)

        threading.Thread(target=self._init_ocr_background, daemon=True).start()

        self._active_voice = self.settings.get("tts.voice", "ru-RU-DmitryNeural")

        def _safe_text_detected(text):
            self.text_detected_signal.emit(text)
        self.live_scanner.on_text_detected = _safe_text_detected
        self.text_detected_signal.connect(self._on_text_detected_gui)

        # Punctuation stream callback: translate chunk for live punct mode
        def _punct_translate_chunk(text):
            return self._punct_translate_sync(text)
        self.live_scanner.on_translate_chunk = _punct_translate_chunk

        self.region_overlay = RegionOverlay(settings=self.settings, config_key="overlay")
        self.region_overlay.region_moved.connect(self._on_region_moved)
        self.region_overlay.region_deleted.connect(self._on_region_deleted)
        # Debounced region save so dragging doesn't rewrite regions.json on
        # every single move event
        self._region_save_timer = QTimer(self)
        self._region_save_timer.setSingleShot(True)
        self._region_save_timer.timeout.connect(self._save_regions)
        self.region_overlay.region_right_clicked.connect(self._show_region_context_menu)
        self.region_overlay.voice_speak.connect(self._on_overlay_voice_speak)
        self.region_overlay.start_clicked.connect(self._on_overlay_start)
        self.region_overlay.stop_clicked.connect(self._on_overlay_stop)
        self.region_overlay.voice_params_changed.connect(self._on_overlay_voice_params_changed)
        self.region_overlay.scan_requested.connect(self._scan_all_regions)
        self.region_overlay.stt_toggle_requested.connect(self._on_overlay_stt_toggle)
        self.region_overlay.stt_overlay_toggled.connect(self._on_stt_overlay_toggled)
        self.region_overlay.stt_tr_overlay_toggled.connect(self._on_stt_tr_overlay_toggled)
        self.region_overlay.set_regions(self.regions)
        if self.regions:
            self.region_overlay.show_overlay()

        # ── Dedicated text overlays (OCR + STT heard + STT translation) ──
        self.ocr_text_overlay = self._create_text_overlay(
            "OCR Text", "overlays.ocr", action_label="Scan OCR")
        self.stt_heard_overlay = self._create_text_overlay(
            "STT Heard", "overlays.stt_heard", action_label="Start STT")
        self.stt_translate_overlay = self._create_text_overlay(
            "STT Translation", "overlays.stt_translate")
        # OCR overlay shown by default; STT overlays hidden until needed
        self.ocr_text_overlay.show_overlay()
        # Wire toggle buttons in OCR overlay settings
        self.ocr_text_overlay.set_toggle_callbacks(
            ocr_cb=lambda checked: self.ocr_text_overlay.toggle_visible() if checked else self.ocr_text_overlay.hide_overlay(),
            stt_cb=lambda checked: self._toggle_stt_overlays(checked)
        )

        # ── VTuber Avatar (tkinter, lazy init) ──
        self._avatar = None
        self._avatar_auto_emotion = self.settings.get("avatar.auto_emotion", True)
        logger.info(f"[AVATAR] Auto-emotion: {self._avatar_auto_emotion}")

        # ── Auto-start STT on voice activity in the selected app ──
        self._stt_auto_started = False
        self._audio_activity = False
        self._silence_ticks = 0
        self._auto_stt_enabled = True

        # Per-process (isolated) capture is opt-in — the COM code is
        # fragile and can hard-crash; disabled by default for stability.
        try:
            from game_audio_capture import game_audio
            game_audio.per_process_enabled = bool(
                self.settings.get("stt.per_process_capture", False))
            # Optional: force a startup output volume. Leave unset (default
            # None) to keep the user's current system volume untouched.
            vol = self.settings.get("general.set_output_volume_on_start", None)
            if vol is not None:
                try:
                    game_audio.set_default_output_volume(float(vol))
                except Exception:
                    pass
            # Register an audio monitor (starts shared capture if needed)
            game_audio.add_audio_callback(self._on_audio_monitor)
        except Exception as e:
            logger.warning(f"[AUTO-STT] Init failed: {e}")

        self._auto_monitor_timer = QTimer(self)
        self._auto_monitor_timer.timeout.connect(self._auto_monitor_tick)
        self._auto_monitor_timer.start(500)

        self.tray_icon = None

        self._setup_ui()
        self._setup_tray()
        self._load_settings()

        self.raise_()
        self.activateWindow()
        self.setFocus()

        self._hotkey_listener = None
        self._init_global_hotkeys()

        logger.info("[START] Screen OCR + TTS (MORT style)")

    # ==================== GLOBAL HOTKEYS ====================

    def _init_global_hotkeys(self):
        # DISABLED — F-keys conflict with games
        logger.info("Global hotkeys DISABLED (F-keys conflict with games)")

    def _cleanup_global_hotkeys(self):
        try:
            if self._hotkey_listener:
                self._hotkey_listener.stop()
                self._hotkey_listener = None
        except Exception as e:
            logger.error(f"Error cleaning up hotkeys: {e}")

    def _toggle_live_mode_btn(self):
        if self.start_live_btn.isEnabled():
            self._on_live_toggle(True)
        else:
            self._on_live_toggle(False)

    def _toggle_overlay(self):
        if self.region_overlay.isVisible():
            self.region_overlay.hide()
        else:
            self.region_overlay.show_overlay()

    # ── Cross-thread GUI marshalling ─────────────────────────
    def _on_gui_invoke(self, fn):
        """Slot (runs on GUI thread) that executes a callable marshalled
        from a worker thread via the gui_invoke signal."""
        try:
            fn()
        except Exception as e:
            logger.error(f"[GUI] invoke error: {e}")

    def _gui(self, fn):
        """Schedule fn to run on the GUI thread, safe from any thread."""
        self.gui_invoke.emit(fn)

    # ── Dedicated text overlays (OCR / STT) ──────────────────
    def _create_text_overlay(self, title, config_key, action_label=None):
        """Create (or recreate) a floating text overlay and wire its signals."""
        ov = TextOverlay(title=title, settings=self.settings,
                         config_key=config_key, action_label=action_label)
        ov.deleted.connect(self._on_text_overlay_deleted)
        ov.action_clicked.connect(self._on_overlay_action)
        return ov

    def _on_text_overlay_deleted(self, ov):
        """Clear our reference when an overlay is deleted via its × button."""
        if ov is self.ocr_text_overlay:
            self.ocr_text_overlay = None
        elif ov is self.stt_heard_overlay:
            self.stt_heard_overlay = None
        elif ov is self.stt_translate_overlay:
            self.stt_translate_overlay = None

    def _on_overlay_action(self):
        """Action button inside an overlay: OCR scan or STT toggle (by sender)."""
        from voice_translator import voice_translator
        ov = self.sender()
        if ov is self.ocr_text_overlay:
            self._scan_all_regions()
        elif ov is self.stt_heard_overlay:
            self._toggle_stt()
            running = voice_translator._running
            self.stt_heard_overlay.set_action_label("Stop STT" if running else "Start STT")

    def _stt_callbacks(self):
        """Build on_heard / on_text callbacks feeding the two STT overlays.

        on_heard  -> STT Heard overlay (raw recognised text, real-time)
        on_text   -> STT Translation overlay (translated RU text, per sentence)
        """
        gui = self

        def on_heard(heard, lang_tag="", speaker=""):
            def _upd():
                sp = f" [{speaker}]" if speaker else ""
                if gui.stt_heard_overlay:
                    gui.stt_heard_overlay.replace_last_line(f"[{lang_tag}]{sp} {heard}")
                # Show pending translation immediately so the overlay
                # never looks dead while we wait for the network call.
                if gui.stt_translate_overlay:
                    gui.stt_translate_overlay.replace_last_line(
                        f"[{lang_tag}]{sp} {heard} ...")
            gui._gui(_upd)

        def on_text(en, ru, lang_tag="", speaker=""):
            def _upd():
                sp = f" [{speaker}]" if speaker else ""
                gui.status_label.setText(f"Voice [{lang_tag}]{sp}: {en[:50]}")
                gui.result_text.setPlainText(f"[{lang_tag}]{sp}\nEN: {en}\n\nRU: {ru}")
                # VTuber: auto-emotion by translated text
                gui._set_avatar_emotion(ru)
                # Mirror into the main-window STT box too (debugging)
                st = getattr(gui, "stt_text", None)
                if st is not None:
                    try:
                        import time as _t
                        ts = _t.strftime("%H:%M:%S")
                        st.append(f"[{ts}] [{lang_tag}] EN: {en}")
                        st.append(f"              RU: {ru}")
                        st.append("")
                    except Exception:
                        pass
                if gui.stt_translate_overlay:
                    gui.stt_translate_overlay.replace_last_line(
                        f"[{lang_tag}] {ru}")
            gui._gui(_upd)

        def on_flush():
            """After TTS speaks, clear both STT buffers."""
            def _upd():
                if gui.stt_heard_overlay:
                    gui.stt_heard_overlay.clear_text()
                if gui.stt_translate_overlay:
                    gui.stt_translate_overlay.clear_text()
            gui._gui(_upd)

        return on_heard, on_text, on_flush

    def _toggle_text_overlay(self, which):
        if which == "ocr":
            ov = self.ocr_text_overlay
        elif which == "stt":
            ov = self.stt_heard_overlay
        elif which == "stt_tr":
            ov = self.stt_translate_overlay
        else:
            ov = None
        if ov is None:
            # Recreate if previously deleted
            if which == "ocr":
                self.ocr_text_overlay = self._create_text_overlay(
                    "OCR Text", "overlays.ocr", action_label="Scan OCR")
                ov = self.ocr_text_overlay
            elif which == "stt":
                self.stt_heard_overlay = self._create_text_overlay(
                    "STT Heard", "overlays.stt_heard", action_label="Start STT")
                ov = self.stt_heard_overlay
            elif which == "stt_tr":
                self.stt_translate_overlay = self._create_text_overlay(
                    "STT Translation", "overlays.stt_translate")
                ov = self.stt_translate_overlay
        ov.toggle_visible()
        labels = {"ocr": "OCR", "stt": "STT", "stt_tr": "STT Tr"}
        btns = {"ocr": self.ocr_ovl_btn, "stt": self.stt_ovl_btn,
                "stt_tr": self.stt_tr_ovl_btn}
        btn = btns[which]
        btn.setText(f"{labels[which]} overlay: {'Hide' if ov.isVisible() else 'Show'}")

    def _toggle_stt_overlays(self, show: bool):
        """Show/hide both STT overlays (heard + translate)."""
        if show:
            if self.stt_heard_overlay:
                self.stt_heard_overlay.show_overlay()
            if self.stt_translate_overlay:
                self.stt_translate_overlay.show_overlay()
        else:
            if self.stt_heard_overlay:
                self.stt_heard_overlay.hide_overlay()
            if self.stt_translate_overlay:
                self.stt_translate_overlay.hide_overlay()

    def _on_stt_overlay_toggled(self, show: bool):
        """Toggle STT heard overlay from region panel button."""
        if self.stt_heard_overlay:
            if show:
                self.stt_heard_overlay.show_overlay()
            else:
                self.stt_heard_overlay.hide_overlay()

    def _on_stt_tr_overlay_toggled(self, show: bool):
        """Toggle STT translation overlay from region panel button."""
        if self.stt_translate_overlay:
            if show:
                self.stt_translate_overlay.show_overlay()
            else:
                self.stt_translate_overlay.hide_overlay()

    # ==================== AVATAR (Qt6) ====================

    def _ensure_avatar(self):
        """Лениво создаёт Qt6 аватар."""
        if self._avatar is not None:
            return self._avatar
        try:
            from qt6_avatar import QtAvatar
            slug = self.avatar_char_combo.currentData() if hasattr(self, 'avatar_char_combo') else "default"
            gen = self.avatar_gen_combo.currentData() if hasattr(self, 'avatar_gen_combo') else "pixel"
            self._avatar = QtAvatar(
                controller=None,
                slug=slug,
                emotion="neutral",
                generator=gen,
            )
            # Синхронизация масштаба с GUI
            if hasattr(self, 'avatar_scale_slider'):
                self._avatar.scale_changed.connect(self._on_avatar_scale_from_handle)
            logger.info("[AVATAR] Qt6 avatar created")
            return self._avatar
        except Exception as e:
            logger.error(f"[AVATAR] Qt6 creation failed: {e}")
            return None

    def _show_avatar(self):
        av = self._ensure_avatar()
        if av:
            slug = self.avatar_char_combo.currentData() if hasattr(self, 'avatar_char_combo') else "default"
            if slug and av.slug != slug:
                av.set_character(slug)
            av.show_avatar()
            if hasattr(self, 'avatar_status_label'):
                self.avatar_status_label.setText(f"Avatar: shown ({av.emotion})")
                self.avatar_status_label.setStyleSheet("color: #3fb950;")

    def _hide_avatar(self):
        if self._avatar:
            self._avatar.hide_avatar()
            if hasattr(self, 'avatar_status_label'):
                self.avatar_status_label.setText("Avatar: hidden")
                self.avatar_status_label.setStyleSheet("color: #969696;")

    def _set_avatar_emotion(self, text):
        """Авто-выбор эмоции по тексту перевода."""
        if not self._avatar_auto_emotion or not self._avatar:
            return
        try:
            from emotion_rules import detect_emotion
            em = detect_emotion(text)
            if em and self._avatar.emotion != em:
                self._avatar.set_emotion(em)
        except Exception as e:
            logger.debug(f"[AVATAR] detect_emotion error: {e}")

    def _on_avatar_generate(self):
        """Маршрутизация генерации по выбранному генератору."""
        # Clear preview thumbnails before generation
        for em, lbl in self._avatar_preview_labels.items():
            lbl.clear()
            lbl.setText("...")
            lbl.setStyleSheet("border: 1px solid #444; background: #1a1a2e; color: #f0883e; font-size: 10px;")
        gen = self.avatar_gen_combo.currentData() if hasattr(self, 'avatar_gen_combo') else "pixel"
        if gen == "replicate":
            self._on_avatar_generate_repl()
        elif gen == "pollinations":
            self._on_avatar_generate_poll()
        else:
            self._on_avatar_generate_px()

    def _update_avatar_gen_status(self, name, done, total):
        if hasattr(self, 'avatar_status_label'):
            if done >= total:
                self.avatar_status_label.setText(f"'{name}' ready ({done}/{total})")
                self.avatar_status_label.setStyleSheet("color: #3fb950;")
            else:
                self.avatar_status_label.setText(f"Generating '{name}': {done}/{total}...")
                self.avatar_status_label.setStyleSheet("color: #f0883e;")

    def _update_preview_thumbnail(self, emotion, slug):
        """Update a single emotion thumbnail in the preview grid."""
        try:
            import avatar_assets
            path = avatar_assets.get_path(emotion, slug)
            if os.path.exists(path) and emotion in self._avatar_preview_labels:
                from PyQt6.QtGui import QPixmap
                from PyQt6.QtCore import Qt as Qtc
                pm = QPixmap(path)
                if not pm.isNull():
                    pm = pm.scaled(78, 78, Qtc.AspectRatioMode.KeepAspectRatio,
                                   Qtc.TransformationMode.SmoothTransformation)
                    self._avatar_preview_labels[emotion].setPixmap(pm)
        except Exception as e:
            logger.debug(f"[PREVIEW] Update failed for {emotion}: {e}")

    def _refresh_all_previews(self, slug):
        """Refresh all 9 emotion thumbnails."""
        try:
            import avatar_assets
            for em in avatar_assets.EMOTIONS:
                path = avatar_assets.get_path(em, slug)
                if os.path.exists(path) and em in self._avatar_preview_labels:
                    from PyQt6.QtGui import QPixmap
                    from PyQt6.QtCore import Qt as Qtc
                    pm = QPixmap(path)
                    if not pm.isNull():
                        pm = pm.scaled(78, 78, Qtc.AspectRatioMode.KeepAspectRatio,
                                       Qtc.TransformationMode.SmoothTransformation)
                        self._avatar_preview_labels[emotion].setPixmap(pm) if False else None
                        self._avatar_preview_labels[em].setPixmap(pm)
                elif em in self._avatar_preview_labels:
                    self._avatar_preview_labels[em].clear()
                    self._avatar_preview_labels[em].setText("--")
        except Exception as e:
            logger.debug(f"[PREVIEW] Refresh failed: {e}")

    def _on_pick_reference(self):
        """Pick a reference image for custom avatar creation."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Select reference image", "",
            "Images (*.png *.jpg *.jpeg *.webp *.bmp);;All files (*)")
        if path:
            self._avatar_ref_path = path
            name = os.path.basename(path)
            self.avatar_ref_label.setText(name)
            self.avatar_ref_label.setStyleSheet("color: #3fb950; font-size: 11px;")
            logger.info(f"[AVATAR] Reference image selected: {path}")

    def _on_clear_reference(self):
        self._avatar_ref_path = None
        self.avatar_ref_label.setText("No reference (uses preset)")
        self.avatar_ref_label.setStyleSheet("color: #969696; font-size: 11px;")

    def _scan_all_regions(self):
        if not self.ocr:
            self.status_label.setText("OCR not ready")
            return
        if not self.regions:
            self.status_label.setText("No regions defined")
            return
        self.status_label.setText(f"Scanning {len(self.regions)} regions...")
        self.region_overlay.set_scanning(True)
        if self.ocr_text_overlay:
            self.ocr_text_overlay.set_scanning(True)
        threading.Thread(target=self._scan_all_regions_thread, daemon=True).start()

    def _scan_all_regions_thread(self):
        all_text = []
        for i, region in enumerate(self.regions):
            try:
                x, y, w, h = region["x"], region["y"], region["width"], region["height"]
                logger.info(f"[SCAN] Region {i}: ({x},{y}) {w}x{h}")
                from PIL import Image
                import mss
                with mss.mss() as sct:
                    monitor = {"left": x, "top": y, "width": w, "height": h}
                    shot = sct.grab(monitor)
                    img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
                result = self.ocr.recognize(img)
                text = result[0] if isinstance(result, tuple) else str(result)
                logger.info(f"[SCAN] OCR result: {repr(text[:200] if text else '')}")
                if text.strip():
                    name = region.get("name", f"Region {i+1}")
                    all_text.append(f"[{name}]\n{text.strip()}")
            except Exception as e:
                logger.error(f"Scan region {i} error: {e}")
        combined = "\n\n".join(all_text)
        logger.info(f"[SCAN] Combined text: {repr(combined[:200] if combined else '')}")
        self._pending_preview = None
        self._pending_text = combined
        self._gui(self._apply_scan_result)

    def _init_ocr_background(self):
        try:
            ocr = OCREngine(self.settings)
            self.ocr = ocr
            self.ocr_ready = True
            self.live_scanner.ocr_engine = ocr
            logger.info(f"[OCR] Ready: {ocr.engine_type}")
        except Exception as e:
            logger.error(f"[OCR] Init failed: {e}")

    def _load_regions(self):
        try:
            if os.path.exists("regions.json"):
                with open("regions.json", "r", encoding="utf-8") as f:
                    self.regions = json.load(f)
            else:
                self.regions = []
        except Exception as e:
            logger.error(f"Error loading regions: {e}")
            self.regions = []

    def _save_regions(self):
        try:
            with open("regions.json", "w", encoding="utf-8") as f:
                json.dump(self.regions, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Error saving regions: {e}")

    def _on_text_detected_gui(self, text):
        if text:
            logger.info(f"[OCR] Text detected: {repr(text[:100])}")
            self.result_text.setPlainText(text)
            if self.ocr_text_overlay:
                self.ocr_text_overlay.set_text(text)
            # Keyboard simulation
            if self.keyboard_check.isChecked():
                self.keyboard_widget.animate_text(text, delay_ms=25)
            # Auto-translate
            auto = self.auto_translate_check.isChecked() if hasattr(self, 'auto_translate_check') else True
            if auto:
                threading.Thread(
                    target=self._translate_ocr_bg, args=(text,), daemon=True).start()

    @staticmethod
    def _clean_ocr_for_translation(text: str) -> str:
        """Clean OCR text before translation — remove timestamps, logs, Russian text."""
        import re
        lines = text.split('\n')
        cleaned = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            # Skip lines with Cyrillic (Russian text) — don't translate
            if re.search(r'[а-яА-ЯёЁ]', line):
                continue
            # Remove timestamps: "19:01:27,926" or "08-06 19:01:27,946"
            line = re.sub(r'^\d{1,2}[-/]\d{1,2}\s+\d{1,2}:\d{2}(:\d{2})?[\.,]?\d*\s*', '', line)
            line = re.sub(r'^\d{1,2}:\d{2}(:\d{2})?[\.,]?\d*\s*', '', line)
            # Remove log level tags: "[INFO]", "[DEBUG]", "[ERROR]", "INFO", "DEBUG", "ERROR"
            line = re.sub(r'^\[?(INFO|DEBUG|ERROR|WARNING|TRACE)\]?\s*', '', line, flags=re.IGNORECASE)
            # Remove module prefixes: "gui_mort -", "tts_engine -", "translator -"
            line = re.sub(r'^[a-z_]+\s*-\s*', '', line, flags=re.IGNORECASE)
            # Remove leading dash/number patterns from log output
            line = re.sub(r'^[-\d]+\s*', '', line)
            # Skip lines with no Latin characters (garbage)
            if not re.search(r'[a-zA-Z]{2,}', line):
                continue
            if line.strip():
                cleaned.append(line.strip())
        return '\n'.join(cleaned)

    def _translate_ocr_bg(self, text):
        try:
            from translator import Translator
            tr = Translator()
            api_key = self.settings.get("translation.api_key", "")
            model = self.settings.get("translation.model", "")
            if api_key:
                tr.configure(api_key, model)
            ocr_lang = self.settings.get("ocr.language", "eng")
            src = "en" if "eng" in ocr_lang.lower() or "en" in ocr_lang.lower() else "auto"
            # Clean text before translation
            clean_text = self._clean_ocr_for_translation(text)
            if not clean_text.strip():
                logger.info("[OCR] Nothing to translate after cleaning")
                return
            logger.info(f"[OCR] Translating '{clean_text[:50]}...' src={src} -> ru")
            ru = tr.translate(clean_text, src=src, dst="ru")
            if ru and not ru.startswith("[Translation") and ru.strip():
                logger.info(f"[OCR] Translation result: '{ru[:80]}...'")
                self._gui(lambda: self._show_translation(ru))
            else:
                logger.warning(f"[OCR] Translation empty or failed: {repr(ru[:100] if ru else '')}")
        except Exception as e:
            logger.error(f"[OCR] translate failed: {e}")

    def _start_typewriter(self, text: str, target: "QTextEdit", delay: int = 30):
        """Начать typewriter эффект — посимвольное отображение текста."""
        self._typewriter_text = text
        self._typewriter_pos = 0
        self._typewriter_target = target
        self._typewriter_delay = delay
        target.clear()
        self._typewriter_timer.start(delay)

    def _typewriter_tick(self):
        """Один шаг typewriter — добавить один символ."""
        if self._typewriter_pos >= len(self._typewriter_text):
            self._typewriter_timer.stop()
            return

        char = self._typewriter_text[self._typewriter_pos]
        self._typewriter_pos += 1

        # Добавляем символ в текстовое поле
        if self._typewriter_target:
            self._typewriter_target.insertPlainText(char)

        # Подсвечиваем клавишу на клавиатуре
        if hasattr(self, 'keyboard_widget') and char.strip():
            self.keyboard_widget.key_press(char)

    def _stop_typewriter(self):
        """Остановить typewriter."""
        self._typewriter_timer.stop()
        if self._typewriter_target and self._typewriter_pos < len(self._typewriter_text):
            # Дописать остаток мгновенно
            remaining = self._typewriter_text[self._typewriter_pos:]
            self._typewriter_target.insertPlainText(remaining)
            self._typewriter_pos = len(self._typewriter_text)

    def _show_translation(self, ru):
        """Show translated text in the right panel."""
        self.translate_text.setPlainText(ru)
        if self.ocr_text_overlay:
            self.ocr_text_overlay.set_text(ru)
        self._set_avatar_emotion(ru)
        # Keyboard simulation for translation
        if self.keyboard_check.isChecked():
            self.keyboard_widget.animate_text(ru, delay_ms=20)

    def _punct_translate_sync(self, text: str) -> str:
        """Synchronous translation for punctuation stream mode (called from LiveScanner)."""
        try:
            from translator import Translator
            tr = Translator()
            api_key = self.settings.get("translation.api_key", "")
            model = self.settings.get("translation.model", "")
            if api_key:
                tr.configure(api_key, model)
            ocr_lang = self.settings.get("ocr.language", "eng")
            src = "en" if "eng" in ocr_lang.lower() or "en" in ocr_lang.lower() else "auto"
            clean_text = self._clean_ocr_for_translation(text)
            if not clean_text.strip():
                return ""
            ru = tr.translate(clean_text, src=src, dst="ru")
            if ru and not ru.startswith("[Translation") and ru.strip():
                self._gui(lambda: self._show_translation(ru))
                return ru
        except Exception as e:
            logger.error(f"[PUNCT] translate sync error: {e}")
        return ""

    def _on_translate_new(self):
        """Translate button in the right panel."""
        text = self.result_text.toPlainText().strip()
        if not text:
            return
        engine = self.translate_engine_combo.currentText()
        threading.Thread(target=self._do_translate_new, args=(text, engine), daemon=True).start()

    def _do_translate_new(self, text, engine):
        try:
            if "Zen" in engine:
                from translator import Translator
                tr = Translator()
                model = (self.settings.get("translation.zen_model", "") or "").strip()
                tr.configure(zen_model=model)
                result = tr.translate(text, src="en", dst="ru", engine="zen")
            elif "Google" in engine:
                from translator import Translator
                tr = Translator()
                result = tr.translate(text, src="en", dst="ru", engine="google")
            elif "DeepL" in engine:
                from translator import Translator
                tr = Translator()
                result = tr._translate_deepl_free(text, "en", "ru")
            elif "Offline" in engine:
                from en_ru_dict import translate_text
                result = translate_text(text)
            else:
                result = "[Unknown engine]"

            if result and not result.startswith("[Translation"):
                self._gui(lambda: self._show_translation(result))
            else:
                self._gui(lambda: self.translate_text.setPlainText("[Translation failed]"))
        except Exception as e:
            logger.error(f"Translate error: {e}")
            self._gui(lambda: self.translate_text.setPlainText(f"[Error: {e}]"))

    def _speak_text(self, text, lang="ru"):
        """Speak text using TTS."""
        if text:
            self.status_label.setText(f"Speaking {lang}...")
            threading.Thread(target=self._do_speak, args=(text,), daemon=True).start()

    # ════════════════════════════════════════════════════════════
    # UI
    # ════════════════════════════════════════════════════════════

    def _setup_ui(self):
        self.setWindowTitle("Screen OCR + TTS")
        self.setMinimumSize(720, 440)
        self.resize(820, 540)

        screen = QApplication.primaryScreen().geometry()
        dpi = QApplication.primaryScreen().logicalDotsPerInch()
        scale = dpi / 96.0
        self._font_size = max(10, int(13 * scale))
        self._btn_h = max(24, int(30 * scale))

        cx = (screen.width() - 820) // 2
        cy = (screen.height() - 540) // 2
        self.move(max(0, cx), max(0, cy))
        self.setWindowFlags(Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Window)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # ── Top bar ──────────────────────────────────────────
        top_bar = QFrame()
        top_bar.setFrameShape(QFrame.Shape.StyledPanel)
        top_bar.setStyleSheet("QFrame { background: #2d2d30; border-radius: 4px; }")
        top_layout = QHBoxLayout(top_bar)
        top_layout.setContentsMargins(8, 4, 8, 4)
        top_layout.setSpacing(6)

        self.src_lang_combo = QComboBox()
        self.src_lang_combo.addItems(["Auto", "RU", "EN", "JP", "ZH", "KO", "DE", "FR", "ES", "IT", "PT", "UK"])
        self.src_lang_combo.setFixedWidth(70)
        top_layout.addWidget(self.src_lang_combo)

        top_layout.addWidget(QLabel("→"))

        self.dst_lang_combo = QComboBox()
        self.dst_lang_combo.addItems(["RU", "EN", "JP", "ZH", "KO", "DE", "FR", "ES", "IT", "PT", "UK", "Auto"])
        self.dst_lang_combo.setFixedWidth(70)
        top_layout.addWidget(self.dst_lang_combo)

        top_layout.addSpacing(12)

        top_layout.addStretch()
        layout.addWidget(top_bar)

        # ── Tabs ─────────────────────────────────────────────
        self.tabs = QTabWidget()
        self.tabs.setTabPosition(QTabWidget.TabPosition.South)

        self.tabs.addTab(self._create_scan_tab(), "OCR")
        self.tabs.addTab(self._create_result_tab(), "Result")
        self.tabs.addTab(self._create_voice_tab(), "Voice")
        try:
            from game_translator_widget import GameTranslatorWidget
            self._gvt_widget = GameTranslatorWidget()
            self.tabs.addTab(self._gvt_widget, "Game Translator")
        except Exception as e:
            logger.error(f"Failed to load GameTranslatorWidget: {e}")
            self._gvt_widget = None
            self.tabs.addTab(self._create_game_translator_tab_fallback(),
                             "Game Translator")
        self.tabs.addTab(self._create_settings_tab(), "Settings")
        self.tabs.addTab(self._create_avatar_tab(), "Avatar")
        self.tabs.addTab(self._create_animation_tab(), "Animation")
        self.tabs.addTab(self._create_storage_tab(), "Storage")
        layout.addWidget(self.tabs)

        # ── Status bar ───────────────────────────────────────
        self.status_label = QLabel("Ready")
        self.statusBar().addPermanentWidget(self.status_label)

        self._refresh_voices()

    # ── Tabs ──────────────────────────────────────────────────

    def _create_scan_tab(self):
        w = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # ── Target Process ─────────────────────────────────
        grp_proc = QGroupBox("Target Process")
        proc_layout = QGridLayout(grp_proc)
        proc_layout.addWidget(QLabel("Process:"), 0, 0)
        self.proc_label = QLabel("Auto-detect (no selection)")
        self.proc_label.setStyleSheet("color: #969696;")
        self.proc_label.setMinimumWidth(200)
        proc_layout.addWidget(self.proc_label, 0, 1)

        proc_btn_row = QHBoxLayout()
        self.proc_select_btn = QPushButton("Select from running")
        self.proc_select_btn.setFixedHeight(self._btn_h)
        self.proc_select_btn.clicked.connect(self._select_process_running)
        proc_btn_row.addWidget(self.proc_select_btn)

        self.proc_browse_btn = QPushButton("Browse .exe")
        self.proc_browse_btn.setFixedHeight(self._btn_h)
        self.proc_browse_btn.clicked.connect(self._select_process_exe)
        proc_btn_row.addWidget(self.proc_browse_btn)

        self.proc_clear_btn = QPushButton("Clear")
        self.proc_clear_btn.setFixedHeight(self._btn_h)
        self.proc_clear_btn.clicked.connect(self._clear_process)
        proc_btn_row.addWidget(self.proc_clear_btn)
        proc_layout.addLayout(proc_btn_row, 1, 0, 1, 2)
        layout.addWidget(grp_proc)

        # ── OCR Regions ────────────────────────────────────
        grp_regions = QGroupBox("OCR Regions")
        reg_layout = QVBoxLayout(grp_regions)
        self.region_list = QListWidget()
        self.region_list.setMinimumHeight(120)
        reg_layout.addWidget(self.region_list)

        btn_row = QHBoxLayout()
        for text, handler in [("+ Add", self._add_region),
                              ("Edit", self._edit_region),
                              ("- Del", self._delete_region),
                              ("Window", self._capture_focused_window)]:
            b = QPushButton(text)
            b.setFixedHeight(self._btn_h)
            b.clicked.connect(handler)
            btn_row.addWidget(b)
        reg_layout.addLayout(btn_row)
        layout.addWidget(grp_regions)

        grp_scan = QGroupBox("Scan")
        scan_layout = QGridLayout(grp_scan)

        scan_layout.addWidget(QLabel("Mode:"), 0, 0)
        self.scan_mode_combo = QComboBox()
        self.scan_mode_combo.addItems(["OCR (text recognition)", "STT (voice translate)", "Both"])
        self.scan_mode_combo.setFixedWidth(200)
        self.scan_mode_combo.currentIndexChanged.connect(self._on_scan_mode_changed)
        scan_layout.addWidget(self.scan_mode_combo, 0, 1)

        self.live_mode_combo = QComboBox()
        self.live_mode_combo.addItems(["Manual (Start/Stop)", "Continuous (Auto)"])
        self.live_mode_combo.setFixedWidth(180)
        self.live_mode_combo.currentIndexChanged.connect(self._on_live_mode_changed)
        scan_layout.addWidget(self.live_mode_combo, 0, 2)

        self.punct_stream_check = QCheckBox("Punct Stream")
        self.punct_stream_check.setToolTip("Live: accumulate text until comma/period -> translate -> speak -> clear.\nIdle detection: pause if text unchanged for 5 sec.")
        self.punct_stream_check.toggled.connect(self._on_punct_stream_toggled)
        scan_layout.addWidget(self.punct_stream_check, 0, 3)

        self.scan_btn = QPushButton("Scan")
        self.scan_btn.setFixedHeight(self._btn_h)
        self.scan_btn.clicked.connect(self._on_scan)
        scan_layout.addWidget(self.scan_btn, 0, 3)

        self.start_live_btn = QPushButton("Start Live")
        self.start_live_btn.setFixedHeight(self._btn_h)
        self.start_live_btn.setStyleSheet("QPushButton { background: #2ea043; } QPushButton:hover { background: #3fb950; }")
        self.start_live_btn.clicked.connect(lambda: self._on_live_toggle(True))
        scan_layout.addWidget(self.start_live_btn, 1, 0)

        self.stop_live_btn = QPushButton("Stop Live")
        self.stop_live_btn.setFixedHeight(self._btn_h)
        self.stop_live_btn.setStyleSheet("QPushButton { background: #da3633; } QPushButton:hover { background: #f85149; }")
        self.stop_live_btn.clicked.connect(lambda: self._on_live_toggle(False))
        scan_layout.addWidget(self.stop_live_btn, 1, 1)

        self.live_status_label = QLabel("Mode: Manual")
        self.live_status_label.setStyleSheet("color: #969696; font-size: 11px;")
        scan_layout.addWidget(self.live_status_label, 1, 2, 1, 2)

        scan_layout.addWidget(QLabel("Interval:"), 2, 0)
        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(100, 10000)
        self.interval_spin.setValue(400)
        self.interval_spin.setSuffix(" ms")
        scan_layout.addWidget(self.interval_spin, 2, 1, 1, 2)
        layout.addWidget(grp_scan)

        grp_voice = QGroupBox("Voice")
        v_layout = QGridLayout(grp_voice)
        v_layout.addWidget(QLabel("Voice:"), 0, 0)
        self.voice_combo = QComboBox()
        self.voice_combo.setFixedWidth(220)
        self.voice_combo.currentIndexChanged.connect(self._on_voice_changed)
        v_layout.addWidget(self.voice_combo, 0, 1)

        v_layout.addWidget(QLabel("Speed:"), 1, 0)
        self.speed_slider = QSlider(Qt.Orientation.Horizontal)
        self.speed_slider.setRange(-50, 50)
        self.speed_slider.setValue(0)
        v_layout.addWidget(self.speed_slider, 1, 1)
        self.speed_label = QLabel("0")
        self.speed_slider.valueChanged.connect(lambda v: self.speed_label.setText(str(v)))
        v_layout.addWidget(self.speed_label, 1, 2)

        v_layout.addWidget(QLabel("Pitch:"), 2, 0)
        self.pitch_slider = QSlider(Qt.Orientation.Horizontal)
        self.pitch_slider.setRange(-50, 50)
        self.pitch_slider.setValue(0)
        v_layout.addWidget(self.pitch_slider, 2, 1)
        self.pitch_label = QLabel("0")
        self.pitch_slider.valueChanged.connect(lambda v: self.pitch_label.setText(str(v)))
        v_layout.addWidget(self.pitch_label, 2, 2)

        voice_btn_row = QHBoxLayout()
        self.start_voice_btn = QPushButton("Start TTS")
        self.start_voice_btn.setFixedHeight(self._btn_h)
        self.start_voice_btn.setStyleSheet("QPushButton { background: #2ea043; } QPushButton:hover { background: #3fb950; }")
        self.start_voice_btn.clicked.connect(self._on_speak)
        voice_btn_row.addWidget(self.start_voice_btn)

        self.stop_voice_btn = QPushButton("Stop TTS")
        self.stop_voice_btn.setFixedHeight(self._btn_h)
        self.stop_voice_btn.setStyleSheet("QPushButton { background: #da3633; } QPushButton:hover { background: #f85149; }")
        self.stop_voice_btn.clicked.connect(self._on_stop_speak)
        voice_btn_row.addWidget(self.stop_voice_btn)

        v_layout.addLayout(voice_btn_row, 3, 0, 1, 3)
        layout.addWidget(grp_voice)

        grp_ocr = QGroupBox("OCR Settings")
        ocr_layout = QGridLayout(grp_ocr)
        ocr_layout.addWidget(QLabel("Language:"), 0, 0)
        self.lang_combo = QComboBox()
        self.lang_combo.addItems(["eng", "rus+eng", "ru", "jpn", "jpn+eng", "chi_sim", "kor", "deu", "fra", "spa", "ita", "por", "ukr"])
        ocr_layout.addWidget(self.lang_combo, 0, 1)
        ocr_layout.addWidget(QLabel("Engine:"), 0, 2)
        self.engine_combo = QComboBox()
        # OrcaRouter/OpenRouter удалены: переводить ими больше нечем.
        self.engine_combo.addItems([
            "EasyOCR", "RapidOCR (PaddleOCR)",
            "Zen (free, no key)", "Google Lens",
            "Tesseract", "Windows OCR"
        ])
        self.engine_combo.currentIndexChanged.connect(self._on_engine_changed)
        ocr_layout.addWidget(self.engine_combo, 0, 3)
        self.gpu_check = QCheckBox("GPU (CUDA)")
        self.gpu_check.setChecked(True)
        ocr_layout.addWidget(self.gpu_check, 1, 0, 1, 2)
        self.detect_changes_check = QCheckBox("Detect changes")
        self.detect_changes_check.setChecked(True)
        ocr_layout.addWidget(self.detect_changes_check, 1, 2, 1, 2)
        self.multi_pass_check = QCheckBox("Multi-pass OCR")
        self.multi_pass_check.setChecked(False)
        self.multi_pass_check.setToolTip("Try multiple preprocessing presets for different game fonts\n(sharpen, contrast, threshold, invert)\nSlower but handles unusual fonts better.")
        ocr_layout.addWidget(self.multi_pass_check, 2, 0, 1, 2)
        self.auto_translate_check = QCheckBox("Auto-translate EN→RU")
        self.auto_translate_check.setChecked(True)
        self.auto_translate_check.setToolTip("Automatically translate English OCR text to Russian\n(Google Translate offline dict fallback)")
        ocr_layout.addWidget(self.auto_translate_check, 2, 0, 1, 4)
        layout.addWidget(grp_ocr)

        grp_vt = QGroupBox("Voice Translate -> RU")
        vt_layout = QGridLayout(grp_vt)
        vt_layout.addWidget(QLabel("From:"), 0, 0)
        self.vt_src_combo = QComboBox()
        self.vt_src_combo.addItems(["en", "ja", "zh", "ko", "de", "fr", "es", "it", "pt", "uk"])
        self.vt_src_combo.setFixedWidth(70)
        vt_layout.addWidget(self.vt_src_combo, 0, 1)
        vt_layout.addWidget(QLabel("-> RU"), 0, 2)

        # Target app for STT — capture ONLY this process's audio
        vt_layout.addWidget(QLabel("Target app:"), 3, 0)
        self.stt_target_combo = QComboBox()
        self.stt_target_combo.setMinimumWidth(150)
        self.stt_target_combo.setToolTip("STT listens ONLY to this app's audio")
        vt_layout.addWidget(self.stt_target_combo, 3, 1)
        self.stt_target_refresh = QPushButton("⟳")
        self.stt_target_refresh.setFixedWidth(28)
        self.stt_target_refresh.setToolTip("Refresh app list")
        self.stt_target_refresh.clicked.connect(self._refresh_target_apps)
        vt_layout.addWidget(self.stt_target_refresh, 3, 2)
        self.stt_target_pick = QPushButton("...")
        self.stt_target_pick.setFixedWidth(28)
        self.stt_target_pick.setToolTip("Choose an .exe file")
        self.stt_target_pick.clicked.connect(self._pick_target_exe)
        vt_layout.addWidget(self.stt_target_pick, 3, 3)

        vt_btn_row = QHBoxLayout()
        self.vt_start_btn = QPushButton("Start Voice")
        self.vt_start_btn.setFixedHeight(self._btn_h)
        self.vt_start_btn.setStyleSheet("QPushButton { background: #2ea043; } QPushButton:hover { background: #3fb950; }")
        self.vt_start_btn.clicked.connect(self._on_vt_start)
        vt_btn_row.addWidget(self.vt_start_btn)

        self.vt_stop_btn = QPushButton("Stop Voice")
        self.vt_stop_btn.setFixedHeight(self._btn_h)
        self.vt_stop_btn.setStyleSheet("QPushButton { background: #da3633; } QPushButton:hover { background: #f85149; }")
        self.vt_stop_btn.clicked.connect(self._on_vt_stop)
        vt_btn_row.addWidget(self.vt_stop_btn)
        vt_layout.addLayout(vt_btn_row, 1, 0, 1, 4)

        self.vt_status = QLabel("STT captures ONLY the selected Target app's audio")
        vt_layout.addWidget(self.vt_status, 2, 0, 1, 4)
        self._refresh_target_apps()
        layout.addWidget(grp_vt)

        layout.addStretch()
        scroll.setWidget(inner)
        main_layout = QVBoxLayout(w)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(scroll)
        return w

    def _create_result_tab(self):
        w = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(8, 8, 8, 8)

        grp_preview = QGroupBox("Screenshot preview")
        preview_layout = QVBoxLayout(grp_preview)
        self.preview_label = QLabel("No screenshot yet")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumHeight(120)
        self.preview_label.setMaximumHeight(300)
        self.preview_label.setStyleSheet("background-color: #1e1e1e; border: 1px solid #3c3c3c; border-radius: 4px; color: #888;")
        preview_layout.addWidget(self.preview_label)
        layout.addWidget(grp_preview)

        # ── Keyboard simulation (compact, bottom) ──
        from keyboard_widget import KeyboardWidget
        kb_row = QHBoxLayout()
        self.keyboard_check = QCheckBox("Keyboard Sim")
        self.keyboard_check.setChecked(self.settings.get("ui.keyboard_sim", True))
        self.keyboard_check.setToolTip("Show key press animation when text appears")
        self.keyboard_check.stateChanged.connect(lambda s: self.settings.set("ui.keyboard_sim", s == 2))
        kb_row.addWidget(self.keyboard_check)
        self.keyboard_widget = KeyboardWidget()
        self.keyboard_widget.setMaximumHeight(80)
        kb_row.addWidget(self.keyboard_widget)
        kb_row.addStretch()
        layout.addLayout(kb_row)

        # Typewriter timer
        self._typewriter_timer = QTimer(self)
        self._typewriter_timer.setSingleShot(False)
        self._typewriter_timer.timeout.connect(self._typewriter_tick)
        self._typewriter_text = ""
        self._typewriter_pos = 0
        self._typewriter_delay = 30  # ms per character
        self._typewriter_target = None  # QTextEdit to type into

        # ── Two panels: Original (left) + Translation (right) ──
        panels_split = QHBoxLayout()
        panels_split.setSpacing(6)

        # Left panel: Original OCR text
        grp_left = QGroupBox("Original (OCR)")
        left_layout = QVBoxLayout(grp_left)
        self.result_text = QTextEdit()
        self.result_text.setPlaceholderText("OCR text appears here...")
        self.result_text.setMinimumHeight(100)
        self.result_text.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.result_text.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        left_layout.addWidget(self.result_text)

        btn_row_left = QHBoxLayout()
        for text, handler in [("Speak", self._on_speak),
                              ("Copy", lambda: QApplication.clipboard().setText(self.result_text.toPlainText())),
                              ("Clear", lambda: self.result_text.clear()),
                              ("Save .txt", self._on_save_txt)]:
            b = QPushButton(text)
            b.setFixedHeight(self._btn_h)
            b.clicked.connect(handler)
            btn_row_left.addWidget(b)
        left_layout.addLayout(btn_row_left)
        panels_split.addWidget(grp_left, 1)

        # Right panel: Translation
        grp_right = QGroupBox("Translation (EN->RU)")
        right_layout = QVBoxLayout(grp_right)

        # Translation engine selector
        eng_row = QHBoxLayout()
        eng_row.addWidget(QLabel("Engine:"))
        self.translate_engine_combo = QComboBox()
        # OrcaRouter (404) и OpenRouter (403) удалены 26.09.2026 —
        # их endpoints не отвечают, вариантов в списке больше нет.
        self.translate_engine_combo.addItems([
            "Zen (free, no key)", "Google (free)", "DeepL (free)",
            "Offline dict"
        ])
        self.translate_engine_combo.setFixedWidth(180)
        eng_row.addWidget(self.translate_engine_combo)
        eng_row.addStretch()
        right_layout.addLayout(eng_row)

        self.translate_text = QTextEdit()
        self.translate_text.setPlaceholderText("Translation appears here...\nClick 'Translate' or enable auto-translate")
        self.translate_text.setMinimumHeight(100)
        self.translate_text.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.translate_text.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.translate_text.setStyleSheet("QTextEdit { background-color: #1a2a1a; border: 1px solid #2ea043; }")
        right_layout.addWidget(self.translate_text)

        btn_row_right = QHBoxLayout()
        self.translate_btn = QPushButton("Translate")
        self.translate_btn.setFixedHeight(self._btn_h)
        self.translate_btn.setStyleSheet("QPushButton { background: #1f6feb; } QPushButton:hover { background: #388bfd; }")
        self.translate_btn.clicked.connect(self._on_translate_new)
        btn_row_right.addWidget(self.translate_btn)
        for text, handler in [("Speak RU", lambda: self._speak_text(self.translate_text.toPlainText(), "ru")),
                              ("Copy", lambda: QApplication.clipboard().setText(self.translate_text.toPlainText())),
                              ("Clear", lambda: self.translate_text.clear())]:
            b = QPushButton(text)
            b.setFixedHeight(self._btn_h)
            b.clicked.connect(handler)
            btn_row_right.addWidget(b)
        right_layout.addLayout(btn_row_right)
        panels_split.addWidget(grp_right, 1)

        layout.addLayout(panels_split)

        # ── STT Voice Translation results ──
        grp_stt = QGroupBox("STT Voice Translate (EN -> RU)")
        stt_layout = QVBoxLayout(grp_stt)
        self.stt_text = QTextEdit()
        self.stt_text.setPlaceholderText("STT results appear here... (F6 to toggle)")
        self.stt_text.setMinimumHeight(80)
        self.stt_text.setMaximumHeight(160)
        self.stt_text.setStyleSheet("QTextEdit { background-color: #1a2a1a; border: 1px solid #2ea043; }")
        stt_layout.addWidget(self.stt_text)
        stt_btn_row = QHBoxLayout()
        self.stt_toggle_btn = QPushButton("Start STT")
        self.stt_toggle_btn.setFixedHeight(self._btn_h)
        self.stt_toggle_btn.setStyleSheet("QPushButton { background: #2ea043; } QPushButton:hover { background: #3fb950; }")
        self.stt_toggle_btn.clicked.connect(self._toggle_stt)
        stt_btn_row.addWidget(self.stt_toggle_btn)
        stt_clear_btn = QPushButton("Clear")
        stt_clear_btn.setFixedHeight(self._btn_h)
        stt_clear_btn.clicked.connect(lambda: self.stt_text.clear())
        stt_btn_row.addWidget(stt_clear_btn)
        stt_layout.addLayout(stt_btn_row)

        self.stt_auto_chk = QCheckBox("Auto-start on voice in selected app")
        self.stt_auto_chk.setChecked(True)
        self.stt_auto_chk.setStyleSheet("color: #8b949e; font-size: 11px;")
        self.stt_auto_chk.setToolTip(
            "Automatically start STT when the selected app produces voice/audio")
        stt_layout.addWidget(self.stt_auto_chk)

        layout.addWidget(grp_stt)

        # ── Text overlays control ──
        grp_ovl = QGroupBox("Text overlays")
        ovl_layout = QVBoxLayout(grp_ovl)
        ovl_layout.setSpacing(4)

        self.ocr_ovl_btn = QPushButton("OCR overlay: Show")
        self.ocr_ovl_btn.setFixedHeight(self._btn_h)
        self.ocr_ovl_btn.clicked.connect(lambda: self._toggle_text_overlay("ocr"))
        ovl_layout.addWidget(self.ocr_ovl_btn)

        self.stt_ovl_btn = QPushButton("STT Heard overlay: Show")
        self.stt_ovl_btn.setFixedHeight(self._btn_h)
        self.stt_ovl_btn.clicked.connect(lambda: self._toggle_text_overlay("stt"))
        ovl_layout.addWidget(self.stt_ovl_btn)

        self.stt_tr_ovl_btn = QPushButton("STT Translation overlay: Show")
        self.stt_tr_ovl_btn.setFixedHeight(self._btn_h)
        self.stt_tr_ovl_btn.clicked.connect(lambda: self._toggle_text_overlay("stt_tr"))
        ovl_layout.addWidget(self.stt_tr_ovl_btn)

        tip = QLabel("Each overlay has its own controls: drag title to move, "
                     "⚙ for opacity/font/colors, – to hide, × to delete.")
        tip.setWordWrap(True)
        tip.setStyleSheet("color: #6e7681; font-size: 10px;")
        ovl_layout.addWidget(tip)

        layout.addWidget(grp_ovl)

        layout.addStretch()

        scroll.setWidget(inner)
        main_layout = QVBoxLayout(w)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(scroll)
        return w

    def _create_voice_tab(self):
        w = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        grp = QGroupBox("Voice")
        v_layout = QGridLayout(grp)
        v_layout.addWidget(QLabel("Voice:"), 0, 0)
        self.voice_tab_combo = QComboBox()
        self.voice_tab_combo.currentIndexChanged.connect(self._on_voice_tab_changed)
        v_layout.addWidget(self.voice_tab_combo, 0, 1, 1, 2)

        self.show_all_edge_check = QCheckBox("All Edge voices")
        self.show_all_edge_check.setToolTip("Show all 300+ Edge-TTS voices (default: 4)")
        self.show_all_edge_check.setChecked(self.settings.get("tts.show_all_edge_voices", False))
        self.show_all_edge_check.toggled.connect(self._on_toggle_all_edge_voices)
        v_layout.addWidget(self.show_all_edge_check, 0, 3)

        v_layout.addWidget(QLabel("Speed:"), 1, 0)
        self.speed_slider = QSlider(Qt.Orientation.Horizontal)
        self.speed_slider.setRange(-50, 50)
        self.speed_slider.setValue(0)
        v_layout.addWidget(self.speed_slider, 1, 1)
        self.speed_label = QLabel("0")
        self.speed_slider.valueChanged.connect(lambda v: self.speed_label.setText(str(v)))
        v_layout.addWidget(self.speed_label, 1, 2)

        v_layout.addWidget(QLabel("Pitch:"), 2, 0)
        self.pitch_slider = QSlider(Qt.Orientation.Horizontal)
        self.pitch_slider.setRange(-50, 50)
        self.pitch_slider.setValue(0)
        v_layout.addWidget(self.pitch_slider, 2, 1)
        self.pitch_label = QLabel("0")
        self.pitch_slider.valueChanged.connect(lambda v: self.pitch_label.setText(str(v)))
        v_layout.addWidget(self.pitch_label, 2, 2)

        test_btn = QPushButton("Test voice")
        test_btn.clicked.connect(self._on_test_voice)
        v_layout.addWidget(test_btn, 3, 0, 1, 4)
        layout.addWidget(grp)

        grp2 = QGroupBox("Dual Voice")
        d_layout = QGridLayout(grp2)
        d_layout.addWidget(QLabel("RU voice:"), 0, 0)
        self.ru_voice_combo = QComboBox()
        d_layout.addWidget(self.ru_voice_combo, 0, 1)
        d_layout.addWidget(QLabel("EN voice:"), 1, 0)
        self.en_voice_combo = QComboBox()
        d_layout.addWidget(self.en_voice_combo, 1, 1)
        self.dual_voice_check = QCheckBox("Enable dual voice")
        d_layout.addWidget(self.dual_voice_check, 2, 0, 1, 2)
        layout.addWidget(grp2)

        grp3 = QGroupBox("Roles")
        r_layout = QGridLayout(grp3)
        for i, (label, attr) in enumerate([("Male:", "male_voice_combo"),
                                            ("Female:", "female_voice_combo"),
                                            ("Narrator:", "narrator_voice_combo")]):
            r_layout.addWidget(QLabel(label), i, 0)
            combo = QComboBox()
            setattr(self, attr, combo)
            r_layout.addWidget(combo, i, 1)
        layout.addWidget(grp3)
        layout.addStretch()

        scroll.setWidget(inner)
        main_layout = QVBoxLayout(w)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(scroll)
        return w

    def _create_game_translator_tab_fallback(self):
        """Fallback if native widget fails to load."""
        w = QWidget()
        layout = QVBoxLayout(w)
        lbl = QLabel(
            "Game Translator widget failed to load.\n"
            "Check game_voice_translator folder exists.\n"
            "Falling back to subprocess mode.")
        lbl.setStyleSheet("color: #f85149; padding: 12px;")
        lbl.setWordWrap(True)
        layout.addWidget(lbl)
        layout.addStretch()
        return w

    def _create_settings_tab(self):
        w = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        grp = QGroupBox("Overlay")
        o_layout = QGridLayout(grp)
        o_layout.setSpacing(4)
        self.overlay_check = QCheckBox("Enable overlay")
        o_layout.addWidget(self.overlay_check, 0, 0, 1, 2)
        o_layout.addWidget(QLabel("Opacity:"), 1, 0)
        self.opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.opacity_slider.setRange(0, 100)
        self.opacity_slider.setValue(80)
        o_layout.addWidget(self.opacity_slider, 1, 1)
        o_layout.addWidget(QLabel("Font size:"), 2, 0)
        self.font_spin = QSpinBox()
        self.font_spin.setRange(8, 36)
        self.font_spin.setValue(14)
        o_layout.addWidget(self.font_spin, 2, 1)

        o_layout.addWidget(QLabel("Font:"), 3, 0)
        self.font_family_combo = QComboBox()
        self.font_family_combo.setFixedWidth(200)
        self._load_game_fonts()
        o_layout.addWidget(self.font_family_combo, 3, 1)

        layout.addWidget(grp)

        grp2 = QGroupBox("Hotkeys")
        h_layout = QVBoxLayout(grp2)
        h_layout.setSpacing(4)
        h_layout.addWidget(QLabel("  Hotkeys disabled (F-keys conflict with games)"))
        layout.addWidget(grp2)

        grp3 = QGroupBox("General")
        g_layout = QVBoxLayout(grp3)
        g_layout.setSpacing(4)
        self.minimize_check = QCheckBox("Start minimized to tray")
        g_layout.addWidget(self.minimize_check)
        self.log_check = QCheckBox("Enable logging")
        self.log_check.setChecked(True)
        g_layout.addWidget(self.log_check)
        layout.addWidget(grp3)

        grp4 = QGroupBox("Interface Language")
        l_layout = QHBoxLayout(grp4)
        self.ui_lang_combo = QComboBox()
        self.ui_lang_combo.addItems(["ru", "en"])
        self.ui_lang_combo.currentTextChanged.connect(self._on_ui_lang_changed)
        l_layout.addWidget(self.ui_lang_combo)
        l_layout.addStretch()
        layout.addWidget(grp4)

        layout.addStretch()
        scroll.setWidget(inner)
        main_layout = QVBoxLayout(w)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(scroll)
        return w

    # ══════════════════════════════════════════════════════════════
    # AVATAR TAB — VTuber model creation, selection, generation
    # ══════════════════════════════════════════════════════════════

    def _create_avatar_tab(self):
        """VTuber avatar creation and management."""
        w = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # ── Generator + Character ──────────────────────────
        grp_av = QGroupBox("Avatar")
        av_layout = QGridLayout(grp_av)
        av_layout.setSpacing(4)

        av_layout.addWidget(QLabel("Generator:"), 0, 0)
        self.avatar_gen_combo = QComboBox()
        self.avatar_gen_combo.setFixedWidth(180)
        self.avatar_gen_combo.addItem("Pixel Art (free, offline)", "pixel")
        self.avatar_gen_combo.addItem("Pollinations (free, online)", "pollinations")
        self.avatar_gen_combo.addItem("Replicate (paid, high quality)", "replicate")
        self.avatar_gen_combo.currentIndexChanged.connect(self._on_generator_changed)
        av_layout.addWidget(self.avatar_gen_combo, 0, 1)

        av_layout.addWidget(QLabel("Character:"), 1, 0)
        self.avatar_char_combo = QComboBox()
        self._populate_characters()
        av_layout.addWidget(self.avatar_char_combo, 1, 1, 1, 2)

        self.avatar_auto_emotion_check = QCheckBox("Auto emotion by text")
        self.avatar_auto_emotion_check.setChecked(self._avatar_auto_emotion)
        self.avatar_auto_emotion_check.stateChanged.connect(
            lambda s: setattr(self, '_avatar_auto_emotion', s == 2))
        av_layout.addWidget(self.avatar_auto_emotion_check, 2, 0, 1, 2)

        av_layout.addWidget(QLabel("Reference image:"), 3, 0)
        ref_row = QHBoxLayout()
        self.avatar_ref_label = QLabel("No reference (uses preset)")
        self.avatar_ref_label.setStyleSheet("color: #969696; font-size: 11px;")
        ref_row.addWidget(self.avatar_ref_label, 1)
        self.avatar_ref_btn = QPushButton("Upload PNG/JPG")
        self.avatar_ref_btn.setFixedHeight(self._btn_h)
        self.avatar_ref_btn.clicked.connect(self._on_pick_reference)
        ref_row.addWidget(self.avatar_ref_btn)
        self.avatar_ref_clear_btn = QPushButton("X")
        self.avatar_ref_clear_btn.setFixedSize(self._btn_h, self._btn_h)
        self.avatar_ref_clear_btn.clicked.connect(self._on_clear_reference)
        ref_row.addWidget(self.avatar_ref_clear_btn)
        av_layout.addLayout(ref_row, 3, 1, 1, 2)
        self._avatar_ref_path = None

        av_btn_row = QHBoxLayout()
        self.avatar_show_btn = QPushButton("Show Avatar")
        self.avatar_show_btn.setFixedHeight(self._btn_h)
        self.avatar_show_btn.setStyleSheet("QPushButton { background: #6e40c9; } QPushButton:hover { background: #8957e5; }")
        self.avatar_show_btn.clicked.connect(self._show_avatar)
        av_btn_row.addWidget(self.avatar_show_btn)
        self.avatar_hide_btn = QPushButton("Hide Avatar")
        self.avatar_hide_btn.setFixedHeight(self._btn_h)
        self.avatar_hide_btn.clicked.connect(self._hide_avatar)
        av_btn_row.addWidget(self.avatar_hide_btn)
        self.avatar_gen_btn = QPushButton("Generate (all emotions)")
        self.avatar_gen_btn.setFixedHeight(self._btn_h)
        self.avatar_gen_btn.setStyleSheet("QPushButton { background: #1f6feb; } QPushButton:hover { background: #388bfd; }")
        self.avatar_gen_btn.clicked.connect(self._on_avatar_generate)
        av_btn_row.addWidget(self.avatar_gen_btn)
        av_layout.addLayout(av_btn_row, 4, 0, 1, 3)

        self._avatar_preview_labels = {}
        preview_group = QGroupBox("Emotions preview (real-time)")
        pg_layout = QGridLayout(preview_group)
        pg_layout.setSpacing(2)
        emotions_list = ["neutral", "happy", "sad", "angry", "surprised", "thinking", "speaking", "wink", "sleeping"]
        emotion_labels_ru = {"neutral": "neutral", "happy": "happy", "sad": "sad", "angry": "angry",
                            "surprised": "wow", "thinking": "...", "speaking": "talk", "wink": "wink", "sleeping": "zzz"}
        for i, em in enumerate(emotions_list):
            row, col = divmod(i, 3)
            cell = QVBoxLayout()
            cell.setSpacing(1)
            lbl = QLabel("--")
            lbl.setFixedSize(80, 80)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet("border: 1px solid #444; background: #1a1a2e; color: #666; font-size: 10px;")
            cell.addWidget(lbl)
            name_lbl = QLabel(emotion_labels_ru.get(em, em))
            name_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            name_lbl.setStyleSheet("font-size: 9px; color: #888;")
            cell.addWidget(name_lbl)
            pg_layout.addLayout(cell, row, col)
            self._avatar_preview_labels[em] = lbl
        av_layout.addWidget(preview_group, 5, 0, 1, 3)

        # ── Avatar Settings (scale, background) ──
        grp_av_settings = QGroupBox("Avatar Settings")
        avs_layout = QGridLayout(grp_av_settings)
        avs_layout.setSpacing(4)

        avs_layout.addWidget(QLabel("Scale:"), 0, 0)
        self.avatar_scale_slider = QSlider(Qt.Orientation.Horizontal)
        self.avatar_scale_slider.setRange(50, 300)
        self.avatar_scale_slider.setValue(100)
        self.avatar_scale_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.avatar_scale_slider.setTickInterval(10)
        self.avatar_scale_slider.valueChanged.connect(self._on_avatar_scale_changed)
        avs_layout.addWidget(self.avatar_scale_slider, 0, 1)
        self.avatar_scale_label = QLabel("100%")
        self.avatar_scale_label.setFixedWidth(40)
        avs_layout.addWidget(self.avatar_scale_label, 0, 2)

        avs_layout.addWidget(QLabel("Background:"), 1, 0)
        self.avatar_bg_combo = QComboBox()
        self.avatar_bg_combo.setFixedWidth(120)
        self.avatar_bg_combo.addItems(["None (transparent)", "Circle", "Rectangle"])
        self.avatar_bg_combo.currentIndexChanged.connect(self._on_avatar_bg_shape_changed)
        avs_layout.addWidget(self.avatar_bg_combo, 1, 1)

        self.avatar_bg_color_btn = QPushButton("Color")
        self.avatar_bg_color_btn.setFixedHeight(self._btn_h)
        self.avatar_bg_color_btn.clicked.connect(self._on_avatar_bg_color_changed)
        avs_layout.addWidget(self.avatar_bg_color_btn, 1, 2)
        self._avatar_bg_color = QColor(30, 30, 40, 200)

        av_layout.addWidget(grp_av_settings, 7, 0, 1, 3)

        self.avatar_status_label = QLabel("Avatar: not created yet")
        self.avatar_status_label.setStyleSheet("color: #969696;")
        av_layout.addWidget(self.avatar_status_label, 8, 0, 1, 3)
        layout.addWidget(grp_av)

        # ── Replicate API Key ──────────────────────────────
        grp_repl = QGroupBox("Replicate API")
        r_layout = QHBoxLayout(grp_repl)
        r_layout.setSpacing(4)
        r_layout.addWidget(QLabel("API Key:"))
        self.replicate_key_edit = QLineEdit()
        self.replicate_key_edit.setPlaceholderText("r8_... (get from replicate.com/account/api-tokens)")
        self.replicate_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        try:
            import replicate_avatar as ra
            self.replicate_key_edit.setText(ra.get_api_key())
        except Exception:
            pass
        r_layout.addWidget(self.replicate_key_edit, 1)
        self.replicate_key_save_btn = QPushButton("Save")
        self.replicate_key_save_btn.setFixedWidth(60)
        self.replicate_key_save_btn.clicked.connect(self._save_replicate_key)
        r_layout.addWidget(self.replicate_key_save_btn)
        layout.addWidget(grp_repl)

        layout.addStretch()
        scroll.setWidget(inner)
        main_layout = QVBoxLayout(w)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(scroll)
        return w

    # ══════════════════════════════════════════════════════════════
    # ANIMATION TAB — Animation Builder
    # ══════════════════════════════════════════════════════════════

    def _create_animation_tab(self):
        """Animation Builder — create/store/preview GIF from frames."""
        w = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        grp_anim = QGroupBox("Animation Builder")
        al = QVBoxLayout(grp_anim)
        al.setSpacing(4)

        anim_row = QHBoxLayout()
        anim_row.addWidget(QLabel("Animation:"))
        self.anim_combo = QComboBox()
        self.anim_combo.setFixedWidth(200)
        self.anim_combo.currentIndexChanged.connect(self._on_anim_selected)
        anim_row.addWidget(self.anim_combo, 1)
        self.anim_refresh_btn = QPushButton("Refresh")
        self.anim_refresh_btn.setFixedWidth(70)
        self.anim_refresh_btn.clicked.connect(self._refresh_anim_list)
        anim_row.addWidget(self.anim_refresh_btn)
        al.addLayout(anim_row)

        new_row = QHBoxLayout()
        self.anim_name_edit = QLineEdit()
        self.anim_name_edit.setPlaceholderText("New animation name...")
        new_row.addWidget(self.anim_name_edit, 1)
        self.anim_create_btn = QPushButton("Create")
        self.anim_create_btn.setFixedWidth(70)
        self.anim_create_btn.clicked.connect(self._on_anim_create)
        new_row.addWidget(self.anim_create_btn)
        al.addLayout(new_row)

        opts_row = QHBoxLayout()
        opts_row.addWidget(QLabel("FPS:"))
        self.anim_fps_spin = QSpinBox()
        self.anim_fps_spin.setRange(1, 30)
        self.anim_fps_spin.setValue(4)
        self.anim_fps_spin.valueChanged.connect(self._on_anim_fps_changed)
        opts_row.addWidget(self.anim_fps_spin)
        self.anim_loop_check = QCheckBox("Loop")
        self.anim_loop_check.setChecked(True)
        self.anim_loop_check.stateChanged.connect(self._on_anim_loop_changed)
        opts_row.addWidget(self.anim_loop_check)
        opts_row.addStretch()
        al.addLayout(opts_row)

        import_row = QHBoxLayout()
        import_row.addWidget(QLabel("Import from:"))
        self.anim_import_slug_combo = QComboBox()
        self.anim_import_slug_combo.setFixedWidth(160)
        import_row.addWidget(self.anim_import_slug_combo)
        self.anim_import_btn = QPushButton("Import Emotions")
        self.anim_import_btn.clicked.connect(self._on_anim_import_emotions)
        import_row.addWidget(self.anim_import_btn)
        al.addLayout(import_row)

        add_row = QHBoxLayout()
        self.anim_add_file_btn = QPushButton("+ Add Image")
        self.anim_add_file_btn.clicked.connect(self._on_anim_add_image)
        add_row.addWidget(self.anim_add_file_btn)
        self.anim_add_gif_btn = QPushButton("+ Extract GIF")
        self.anim_add_gif_btn.clicked.connect(self._on_anim_extract_gif)
        add_row.addWidget(self.anim_add_gif_btn)
        self.anim_add_url_btn = QPushButton("+ URL")
        self.anim_add_url_btn.clicked.connect(self._on_anim_add_url)
        add_row.addWidget(self.anim_add_url_btn)
        al.addLayout(add_row)

        export_row = QHBoxLayout()
        self.anim_export_btn = QPushButton("Export GIF")
        self.anim_export_btn.setStyleSheet("QPushButton { background: #1f6feb; } QPushButton:hover { background: #388bfd; }")
        self.anim_export_btn.clicked.connect(self._on_anim_export_gif)
        export_row.addWidget(self.anim_export_btn)
        self.anim_delete_btn = QPushButton("Delete Animation")
        self.anim_delete_btn.setStyleSheet("QPushButton { background: #da3633; } QPushButton:hover { background: #f85149; }")
        self.anim_delete_btn.clicked.connect(self._on_anim_delete)
        export_row.addWidget(self.anim_delete_btn)
        al.addLayout(export_row)

        self.anim_status = QLabel("Select or create an animation")
        self.anim_status.setStyleSheet("color: #969696; font-size: 11px;")
        al.addWidget(self.anim_status)

        # Frame list
        frame_row = QHBoxLayout()
        self.anim_frame_list = QListWidget()
        self.anim_frame_list.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.anim_frame_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.anim_frame_list.model().rowsMoved.connect(self._on_anim_frames_reordered)
        self.anim_frame_list.itemDoubleClicked.connect(self._on_anim_frame_replace)
        self.anim_frame_list.setIconSize(QSize(48, 48))
        frame_row.addWidget(self.anim_frame_list, 1)

        preview_col = QVBoxLayout()
        preview_col.addWidget(QLabel("Preview:"))
        self.anim_preview_label = QLabel("--")
        self.anim_preview_label.setFixedSize(160, 160)
        self.anim_preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.anim_preview_label.setStyleSheet("border: 2px solid #444; background: #1a1a2e; color: #666;")
        preview_col.addWidget(self.anim_preview_label)
        self.anim_preview_btn = QPushButton("Play")
        self.anim_preview_btn.clicked.connect(self._on_anim_preview)
        preview_col.addWidget(self.anim_preview_btn)
        preview_col.addStretch()
        frame_row.addLayout(preview_col, 0)
        al.addLayout(frame_row)

        fc_row = QHBoxLayout()
        for text, slot in [("Up", self._on_anim_frame_up), ("Down", self._on_anim_frame_down),
                           ("Duplicate", self._on_anim_frame_duplicate)]:
            btn = QPushButton(text)
            btn.setFixedWidth(70)
            btn.clicked.connect(slot)
            fc_row.addWidget(btn)
        self.anim_frame_del_btn = QPushButton("Delete Frame")
        self.anim_frame_del_btn.setStyleSheet("QPushButton { background: #da3633; }")
        self.anim_frame_del_btn.clicked.connect(self._on_anim_frame_delete)
        fc_row.addWidget(self.anim_frame_del_btn)
        fc_row.addStretch()
        al.addLayout(fc_row)

        layout.addWidget(grp_anim)
        layout.addStretch()
        scroll.setWidget(inner)
        main_layout = QVBoxLayout(w)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(scroll)
        self._refresh_anim_list()
        return w

    # ══════════════════════════════════════════════════════════════
    # STORAGE TAB — Manage all saved avatars and animations
    # ══════════════════════════════════════════════════════════════

    def _create_storage_tab(self):
        """Storage management: list, rename, delete avatars & animations."""
        w = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # ── Avatars Section ─────────────────────────────────
        grp_av = QGroupBox("Avatars")
        av_layout = QVBoxLayout(grp_av)

        self.storage_av_list = QTreeWidget()
        self.storage_av_list.setHeaderLabels(["Name", "Slug", "Emotions", "Size"])
        self.storage_av_list.setColumnCount(4)
        self.storage_av_list.setAlternatingRowColors(True)
        self.storage_av_list.setSortingEnabled(True)
        av_layout.addWidget(self.storage_av_list)

        av_btn_row = QHBoxLayout()
        self.storage_av_refresh_btn = QPushButton("Refresh")
        self.storage_av_refresh_btn.clicked.connect(self._refresh_storage_avatars)
        av_btn_row.addWidget(self.storage_av_refresh_btn)
        self.storage_av_rename_btn = QPushButton("Rename")
        self.storage_av_rename_btn.clicked.connect(self._on_storage_avatar_rename)
        av_btn_row.addWidget(self.storage_av_rename_btn)
        self.storage_av_del_btn = QPushButton("Delete")
        self.storage_av_del_btn.setStyleSheet("QPushButton { background: #da3633; } QPushButton:hover { background: #f85149; }")
        self.storage_av_del_btn.clicked.connect(self._on_storage_avatar_delete)
        av_btn_row.addWidget(self.storage_av_del_btn)
        av_btn_row.addStretch()
        av_layout.addLayout(av_btn_row)

        self.storage_av_status = QLabel("0 avatars")
        self.storage_av_status.setStyleSheet("color: #969696; font-size: 11px;")
        av_layout.addWidget(self.storage_av_status)
        layout.addWidget(grp_av)

        # ── Animations Section ──────────────────────────────
        grp_an = QGroupBox("Animations")
        an_layout = QVBoxLayout(grp_an)

        self.storage_an_list = QTreeWidget()
        self.storage_an_list.setHeaderLabels(["Name", "Slug", "Frames", "FPS", "Size"])
        self.storage_an_list.setColumnCount(5)
        self.storage_an_list.setAlternatingRowColors(True)
        self.storage_an_list.setSortingEnabled(True)
        an_layout.addWidget(self.storage_an_list)

        an_btn_row = QHBoxLayout()
        self.storage_an_refresh_btn = QPushButton("Refresh")
        self.storage_an_refresh_btn.clicked.connect(self._refresh_storage_animations)
        an_btn_row.addWidget(self.storage_an_refresh_btn)
        self.storage_an_rename_btn = QPushButton("Rename")
        self.storage_an_rename_btn.clicked.connect(self._on_storage_anim_rename)
        an_btn_row.addWidget(self.storage_an_rename_btn)
        self.storage_an_del_btn = QPushButton("Delete")
        self.storage_an_del_btn.setStyleSheet("QPushButton { background: #da3633; } QPushButton:hover { background: #f85149; }")
        self.storage_an_del_btn.clicked.connect(self._on_storage_anim_delete)
        an_btn_row.addWidget(self.storage_an_del_btn)
        an_btn_row.addStretch()
        an_layout.addLayout(an_btn_row)

        self.storage_an_status = QLabel("0 animations")
        self.storage_an_status.setStyleSheet("color: #969696; font-size: 11px;")
        an_layout.addWidget(self.storage_an_status)
        layout.addWidget(grp_an)

        layout.addStretch()
        scroll.setWidget(inner)
        main_layout = QVBoxLayout(w)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(scroll)

        self._refresh_storage_avatars()
        self._refresh_storage_animations()
        return w

    def _refresh_storage_avatars(self):
        """Refresh the avatar tree list."""
        if not hasattr(self, 'storage_av_list'):
            return
        self.storage_av_list.clear()
        try:
            import avatar_assets
            import os
            total_size = 0
            for av in avatar_assets.list_all_avatars():
                slug = av['slug']
                name = av['name']
                count = av['count']
                # Calculate size
                av_dir = os.path.join(avatar_assets.ASSETS_DIR, slug)
                av_size = 0
                for f in os.listdir(av_dir):
                    fpath = os.path.join(av_dir, f)
                    if os.path.isfile(fpath):
                        av_size += os.path.getsize(fpath)
                total_size += av_size
                size_str = f"{av_size / 1024:.1f} KB" if av_size > 0 else "0 KB"
                item = QTreeWidgetItem([name, slug, f"{count}/9", size_str])
                item.setData(0, Qt.ItemDataRole.UserRole, slug)
                self.storage_av_list.addTopLevelItem(item)
            self.storage_av_status.setText(f"{len(avatar_assets.list_all_avatars())} avatars, {total_size / 1024:.1f} KB total")
        except Exception as e:
            self.storage_av_status.setText(f"Error: {e}")

    def _refresh_storage_animations(self):
        """Refresh the animation tree list."""
        if not hasattr(self, 'storage_an_list'):
            return
        self.storage_an_list.clear()
        try:
            import avatar_animation as aa
            import os
            total_size = 0
            for anim in aa.list_animations():
                slug = anim['slug']
                name = anim['name']
                frames = anim['frame_count']
                fps = anim['fps']
                # Calculate size
                anim_dir = os.path.join(aa.ANIMATIONS_DIR, slug)
                an_size = 0
                if os.path.isdir(anim_dir):
                    for f in os.listdir(anim_dir):
                        fpath = os.path.join(anim_dir, f)
                        if os.path.isfile(fpath):
                            an_size += os.path.getsize(fpath)
                total_size += an_size
                size_str = f"{an_size / 1024:.1f} KB" if an_size > 0 else "0 KB"
                item = QTreeWidgetItem([name, slug, str(frames), str(fps), size_str])
                item.setData(0, Qt.ItemDataRole.UserRole, slug)
                self.storage_an_list.addTopLevelItem(item)
            self.storage_an_status.setText(f"{len(aa.list_animations())} animations, {total_size / 1024:.1f} KB total")
        except Exception as e:
            self.storage_an_status.setText(f"Error: {e}")

    def _on_storage_avatar_delete(self):
        """Delete selected avatar."""
        item = self.storage_av_list.currentItem()
        if not item:
            QMessageBox.warning(self, "Warning", "Select an avatar to delete")
            return
        slug = item.data(0, Qt.ItemDataRole.UserRole)
        name = item.text(0)
        reply = QMessageBox.question(self, "Delete Avatar",
                                     f"Delete '{name}' and all emotions?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            import avatar_assets
            avatar_assets.delete_avatar(slug)
            self._refresh_storage_avatars()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _on_storage_avatar_rename(self):
        """Rename selected avatar."""
        item = self.storage_av_list.currentItem()
        if not item:
            QMessageBox.warning(self, "Warning", "Select an avatar to rename")
            return
        old_slug = item.data(0, Qt.ItemDataRole.UserRole)
        old_name = item.text(0)
        new_name, ok = QInputDialog.getText(self, "Rename Avatar", "New name:", text=old_name)
        if not ok or not new_name.strip():
            return
        new_name = new_name.strip()
        try:
            import avatar_assets
            avatar_assets.rename_avatar(old_slug, new_name)
            self._refresh_storage_avatars()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _on_storage_anim_delete(self):
        """Delete selected animation."""
        item = self.storage_an_list.currentItem()
        if not item:
            QMessageBox.warning(self, "Warning", "Select an animation to delete")
            return
        slug = item.data(0, Qt.ItemDataRole.UserRole)
        name = item.text(0)
        reply = QMessageBox.question(self, "Delete Animation",
                                     f"Delete '{name}' and all frames?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            import avatar_animation as aa
            aa.delete_animation(slug)
            self._refresh_storage_animations()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _on_storage_anim_rename(self):
        """Rename selected animation."""
        item = self.storage_an_list.currentItem()
        if not item:
            QMessageBox.warning(self, "Warning", "Select an animation to rename")
            return
        old_slug = item.data(0, Qt.ItemDataRole.UserRole)
        old_name = item.text(0)
        new_name, ok = QInputDialog.getText(self, "Rename Animation", "New name:", text=old_name)
        if not ok or not new_name.strip():
            return
        new_name = new_name.strip()
        try:
            import avatar_animation as aa
            aa.rename_animation(old_slug, new_name)
            self._refresh_storage_animations()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    # ── Animation methods ────────────────────────────────────

    def _refresh_anim_list(self):
        """Refresh the animation combo from disk."""
        if not hasattr(self, 'anim_combo'):
            return
        self.anim_combo.blockSignals(True)
        self.anim_combo.clear()
        try:
            import avatar_animation as aa
            for anim in aa.list_animations():
                self.anim_combo.addItem(
                    f"{anim['name']} ({anim['frame_count']}f, {anim['fps']}fps)",
                    anim['slug'])
        except Exception:
            pass
        self.anim_combo.blockSignals(False)
        self._refresh_import_slugs()

    def _refresh_import_slugs(self):
        """Refresh the import-from slug combo."""
        if not hasattr(self, 'anim_import_slug_combo'):
            return
        self.anim_import_slug_combo.clear()
        try:
            import avatar_assets
            for av in avatar_assets.list_all_avatars():
                self.anim_import_slug_combo.addItem(
                    f"{av['name']} ({av['count']}/9)", av['slug'])
        except Exception:
            pass

    def _on_anim_selected(self):
        """Load selected animation frames into the list."""
        slug = self.anim_combo.currentData() if hasattr(self, 'anim_combo') else None
        if not slug:
            return
        self._load_anim_frames(slug)

    def _load_anim_frames(self, slug):
        """Load frames of an animation into the frame list widget."""
        self.anim_frame_list.clear()
        try:
            import avatar_animation as aa
            frames = aa._list_frames(slug)
            meta = aa._load_meta(slug)
            self.anim_fps_spin.setValue(meta.get("fps", 4))
            self.anim_loop_check.setChecked(meta.get("loop", True))
            for fp in frames:
                from PyQt6.QtGui import QPixmap, QIcon
                pm = QPixmap(fp)
                if not pm.isNull():
                    icon = QIcon(pm.scaled(48, 48, Qt.AspectRatioMode.KeepAspectRatio,
                                          Qt.TransformationMode.SmoothTransformation))
                    item = QListWidgetItem(icon, os.path.basename(fp))
                else:
                    item = QListWidgetItem(os.path.basename(fp))
                item.setData(Qt.ItemDataRole.UserRole, fp)
                self.anim_frame_list.addItem(item)
            self.anim_status.setText(f"Loaded {len(frames)} frames from '{slug}'")
            self.anim_status.setStyleSheet("color: #3fb950; font-size: 11px;")
            # Show first frame as preview
            if frames:
                self._show_frame_preview(frames[0])
        except Exception as e:
            self.anim_status.setText(f"Error: {e}")
            self.anim_status.setStyleSheet("color: #f85149; font-size: 11px;")

    def _show_frame_preview(self, frame_path):
        """Show a frame in the preview label."""
        from PyQt6.QtGui import QPixmap
        if os.path.exists(frame_path):
            pm = QPixmap(frame_path)
            if not pm.isNull():
                pm = pm.scaled(196, 196, Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
                self.anim_preview_label.setPixmap(pm)

    def _on_anim_create(self):
        """Create a new animation project."""
        name = self.anim_name_edit.text().strip()
        if not name:
            self.anim_status.setText("Enter animation name first")
            self.anim_status.setStyleSheet("color: #f85149; font-size: 11px;")
            return
        try:
            import avatar_animation as aa
            aa.create_animation(name, fps=self.anim_fps_spin.value(),
                                loop=self.anim_loop_check.isChecked())
            self._refresh_anim_list()
            # Select the new one
            for i in range(self.anim_combo.count()):
                if self.anim_combo.itemData(i) == aa._sanitize(name):
                    self.anim_combo.setCurrentIndex(i)
                    break
            self.anim_status.setText(f"Created '{name}'")
            self.anim_status.setStyleSheet("color: #3fb950; font-size: 11px;")
        except Exception as e:
            self.anim_status.setText(f"Error: {e}")
            self.anim_status.setStyleSheet("color: #f85149; font-size: 11px;")

    def _on_anim_fps_changed(self, val):
        slug = self.anim_combo.currentData() if hasattr(self, 'anim_combo') else None
        if slug:
            try:
                import avatar_animation as aa
                aa.set_fps(slug, val)
            except Exception:
                pass

    def _on_anim_loop_changed(self, state):
        slug = self.anim_combo.currentData() if hasattr(self, 'anim_combo') else None
        if slug:
            try:
                import avatar_animation as aa
                aa.set_loop(slug, state == 2)
            except Exception:
                pass

    def _on_anim_import_emotions(self):
        """Import emotion frames from an avatar."""
        slug = self.anim_combo.currentData() if hasattr(self, 'anim_combo') else None
        import_slug = self.anim_import_slug_combo.currentData() if hasattr(self, 'anim_import_slug_combo') else None
        if not slug or not import_slug:
            self.anim_status.setText("Select animation and source avatar first")
            self.anim_status.setStyleSheet("color: #f85149; font-size: 11px;")
            return
        try:
            import avatar_animation as aa
            count = aa.add_frames_from_emotions(slug, import_slug)
            self.anim_status.setText(f"Imported {count} frames from '{import_slug}'")
            self.anim_status.setStyleSheet("color: #3fb950; font-size: 11px;")
            self._load_anim_frames(slug)
        except Exception as e:
            self.anim_status.setText(f"Error: {e}")
            self.anim_status.setStyleSheet("color: #f85149; font-size: 11px;")

    def _on_anim_add_image(self):
        """Add a single image as frame."""
        slug = self.anim_combo.currentData() if hasattr(self, 'anim_combo') else None
        if not slug:
            self.anim_status.setText("Create or select an animation first")
            self.anim_status.setStyleSheet("color: #f85149; font-size: 11px;")
            return
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Select images", "",
            "Images (*.png *.jpg *.jpeg *.webp *.bmp);;All files (*)")
        if paths:
            try:
                import avatar_animation as aa
                for p in paths:
                    aa.add_frame(slug, p)
                self.anim_status.setText(f"Added {len(paths)} frames")
                self.anim_status.setStyleSheet("color: #3fb950; font-size: 11px;")
                self._load_anim_frames(slug)
            except Exception as e:
                self.anim_status.setText(f"Error: {e}")
                self.anim_status.setStyleSheet("color: #f85149; font-size: 11px;")

    def _on_anim_extract_gif(self):
        """Extract frames from a GIF file."""
        slug = self.anim_combo.currentData() if hasattr(self, 'anim_combo') else None
        if not slug:
            self.anim_status.setText("Create or select an animation first")
            self.anim_status.setStyleSheet("color: #f85149; font-size: 11px;")
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Select GIF", "",
            "GIF files (*.gif);;All files (*)")
        if path:
            try:
                import avatar_animation as aa
                count = aa.add_frames_from_gif(slug, path)
                self.anim_status.setText(f"Extracted {count} frames from GIF")
                self.anim_status.setStyleSheet("color: #3fb950; font-size: 11px;")
                self._load_anim_frames(slug)
            except Exception as e:
                self.anim_status.setText(f"Error: {e}")
                self.anim_status.setStyleSheet("color: #f85149; font-size: 11px;")

    def _on_anim_add_url(self):
        """Add an image from URL as frame."""
        slug = self.anim_combo.currentData() if hasattr(self, 'anim_combo') else None
        if not slug:
            self.anim_status.setText("Create or select an animation first")
            self.anim_status.setStyleSheet("color: #f85149; font-size: 11px;")
            return
        url, ok = QInputDialog.getText(self, "Add URL frame", "Image URL:")
        if ok and url.strip():
            try:
                import avatar_animation as aa
                aa.add_frames_from_url(slug, url.strip())
                self.anim_status.setText("Added URL frame")
                self.anim_status.setStyleSheet("color: #3fb950; font-size: 11px;")
                self._load_anim_frames(slug)
            except Exception as e:
                self.anim_status.setText(f"Error: {e}")
                self.anim_status.setStyleSheet("color: #f85149; font-size: 11px;")

    def _on_anim_export_gif(self):
        """Export animation as GIF."""
        slug = self.anim_combo.currentData() if hasattr(self, 'anim_combo') else None
        if not slug:
            self.anim_status.setText("Select an animation first")
            self.anim_status.setStyleSheet("color: #f85149; font-size: 11px;")
            return
        save_path, _ = QFileDialog.getSaveFileName(
            self, "Save GIF", f"{slug}.gif", "GIF files (*.gif)")
        if save_path:
            try:
                import avatar_animation as aa
                result = aa.export_gif(slug, output_path=save_path,
                                       fps=self.anim_fps_spin.value(),
                                       loop=self.anim_loop_check.isChecked())
                self.anim_status.setText(f"Exported: {result}")
                self.anim_status.setStyleSheet("color: #3fb950; font-size: 11px;")
            except Exception as e:
                self.anim_status.setText(f"Error: {e}")
                self.anim_status.setStyleSheet("color: #f85149; font-size: 11px;")

    def _on_anim_delete(self):
        """Delete current animation."""
        slug = self.anim_combo.currentData() if hasattr(self, 'anim_combo') else None
        if not slug:
            return
        reply = QMessageBox.question(self, "Delete Animation",
                                     f"Delete '{slug}' and all frames?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            try:
                import avatar_animation as aa
                aa.delete_animation(slug)
                self._refresh_anim_list()
                self.anim_frame_list.clear()
                self.anim_status.setText("Deleted")
                self.anim_status.setStyleSheet("color: #f85149; font-size: 11px;")
            except Exception as e:
                self.anim_status.setText(f"Error: {e}")
                self.anim_status.setStyleSheet("color: #f85149; font-size: 11px;")

    def _on_anim_frame_up(self):
        """Move selected frame up."""
        row = self.anim_frame_list.currentRow()
        if row > 0:
            item = self.anim_frame_list.takeItem(row)
            self.anim_frame_list.insertItem(row - 1, item)
            self.anim_frame_list.setCurrentRow(row - 1)
            self._reindex_frames()

    def _on_anim_frame_down(self):
        """Move selected frame down."""
        row = self.anim_frame_list.currentRow()
        if row < self.anim_frame_list.count() - 1:
            item = self.anim_frame_list.takeItem(row)
            self.anim_frame_list.insertItem(row + 1, item)
            self.anim_frame_list.setCurrentRow(row + 1)
            self._reindex_frames()

    def _on_anim_frame_duplicate(self):
        """Duplicate selected frame."""
        item = self.anim_frame_list.currentItem()
        if not item:
            return
        fp = item.data(Qt.ItemDataRole.UserRole)
        if fp:
            try:
                import avatar_animation as aa
                aa.duplicate_frame(fp)
                slug = self.anim_combo.currentData()
                if slug:
                    self._load_anim_frames(slug)
            except Exception as e:
                self.anim_status.setText(f"Error: {e}")
                self.anim_status.setStyleSheet("color: #f85149; font-size: 11px;")

    def _on_anim_frame_delete(self):
        """Delete selected frame."""
        item = self.anim_frame_list.currentItem()
        if not item:
            return
        fp = item.data(Qt.ItemDataRole.UserRole)
        if fp:
            try:
                import avatar_animation as aa
                aa.remove_frame(fp)
                slug = self.anim_combo.currentData()
                if slug:
                    self._load_anim_frames(slug)
            except Exception as e:
                self.anim_status.setText(f"Error: {e}")
                self.anim_status.setStyleSheet("color: #f85149; font-size: 11px;")

    def _on_anim_frame_replace(self, item):
        """Double-click a frame to replace it with another image."""
        fp = item.data(Qt.ItemDataRole.UserRole)
        if not fp:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Replace frame", "",
            "Images (*.png *.jpg *.jpeg *.webp *.bmp);;All files (*)")
        if path:
            try:
                import avatar_animation as aa
                aa.replace_frame(fp, path)
                slug = self.anim_combo.currentData()
                if slug:
                    self._load_anim_frames(slug)
                    self._show_frame_preview(fp)
            except Exception as e:
                self.anim_status.setText(f"Error: {e}")
                self.anim_status.setStyleSheet("color: #f85149; font-size: 11px;")

    def _on_anim_frames_reordered(self):
        """Reindex frames after drag-and-drop reorder."""
        self._reindex_frames()

    def _reindex_frames(self):
        """Rename all frame files to match current order in the list."""
        slug = self.anim_combo.currentData() if hasattr(self, 'anim_combo') else None
        if not slug:
            return
        try:
            import avatar_animation as aa
            d = aa.get_anim_dir(slug)
            # Collect current files and their order from list widget
            new_order = []
            for i in range(self.anim_frame_list.count()):
                item = self.anim_frame_list.item(i)
                fp = item.data(Qt.ItemDataRole.UserRole)
                if fp and os.path.exists(fp):
                    new_order.append(fp)
            # Rename to sequential
            for i, fp in enumerate(new_order):
                ext = Path(fp).suffix or ".png"
                new_name = f"frame_{i:04d}{ext}"
                new_path = os.path.join(d, new_name)
                if fp != new_path and os.path.exists(fp):
                    os.rename(fp, new_path)
                    item = self.anim_frame_list.item(i)
                    item.setData(Qt.ItemDataRole.UserRole, new_path)
        except Exception as e:
            logger.debug(f"[ANIM] Reindex error: {e}")

    def _on_anim_preview(self):
        """Play a simple preview of the animation (cycle through frames)."""
        slug = self.anim_combo.currentData() if hasattr(self, 'anim_combo') else None
        if not slug:
            return
        try:
            import avatar_animation as aa
            frames = aa._list_frames(slug)
            if not frames:
                return
            meta = aa._load_meta(slug)
            fps = meta.get("fps", 4)
            interval = max(20, int(1000 / fps))

            self._anim_preview_idx = 0
            self._anim_preview_frames = frames

            if not hasattr(self, '_anim_preview_timer'):
                self._anim_preview_timer = QTimer(self)
                self._anim_preview_timer.timeout.connect(self._anim_preview_tick)

            self._anim_preview_timer.start(interval)
            self.anim_preview_btn.setText("Stop")
            self.anim_preview_btn.clicked.disconnect()
            self.anim_preview_btn.clicked.connect(self._anim_preview_stop)
        except Exception as e:
            self.anim_status.setText(f"Preview error: {e}")
            self.anim_status.setStyleSheet("color: #f85149; font-size: 11px;")

    def _anim_preview_tick(self):
        """Show next frame in preview."""
        if not hasattr(self, '_anim_preview_frames') or not self._anim_preview_frames:
            return
        idx = self._anim_preview_idx % len(self._anim_preview_frames)
        self._show_frame_preview(self._anim_preview_frames[idx])
        self._anim_preview_idx += 1

    def _anim_preview_stop(self):
        if hasattr(self, '_anim_preview_timer'):
            self._anim_preview_timer.stop()
        self.anim_preview_btn.setText("Play Preview")
        self.anim_preview_btn.clicked.disconnect()
        self.anim_preview_btn.clicked.connect(self._on_anim_preview)

    def _save_replicate_key(self):
        """Сохраняет Replicate API key."""
        try:
            import replicate_avatar as ra
            key = self.replicate_key_edit.text().strip()
            ra.set_api_key(key)
            self.avatar_status_label.setText("Replicate API key saved")
            self.avatar_status_label.setStyleSheet("color: #3fb950;")
        except Exception as e:
            logger.error(f"[REPLICATE] Failed to save key: {e}")
            self.avatar_status_label.setText(f"Error saving key: {e}")
            self.avatar_status_label.setStyleSheet("color: #f85149;")

    def _populate_characters(self):
        """Заполняет combo персонажей в зависимости от выбранного генератора."""
        self.avatar_char_combo.clear()
        gen = self.avatar_gen_combo.currentData() if hasattr(self, 'avatar_gen_combo') else "pixel"

        # Always add existing custom avatars from assets
        try:
            import avatar_assets
            for av in avatar_assets.list_all_avatars():
                if av["slug"].startswith("custom_"):
                    self.avatar_char_combo.addItem(f"[Custom] {av['name']} ({av['count']}/9)", av["slug"])
        except Exception:
            pass

        if gen == "replicate":
            try:
                import replicate_avatar as ra
                for p in ra.PRESET_CHARACTERS:
                    self.avatar_char_combo.addItem(f"{p['name']}", p["slug"])
            except Exception:
                self.avatar_char_combo.addItem("Default", "default")
        elif gen == "pollinations":
            try:
                import importlib.util as _iu
                _pa_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pollinations_avatar.py")
                _spec = _iu.spec_from_file_location("pollinations_avatar_local", _pa_path)
                pa = _iu.module_from_spec(_spec)
                _spec.loader.exec_module(pa)
                for p in pa.PRESET_CHARACTERS:
                    self.avatar_char_combo.addItem(f"{p['name']}", p["slug"])
            except Exception:
                self.avatar_char_combo.addItem("Default", "default")
        else:
            try:
                import pixel_avatar as px
                for p in px.PRESET_CHARACTERS:
                    self.avatar_char_combo.addItem(f"{p['name']}", p["slug"])
            except Exception:
                self.avatar_char_combo.addItem("Рыжий", "pixel_orange")

    def _on_generator_changed(self):
        """При смене генератора — обновить список персонажей и аватар."""
        gen = self.avatar_gen_combo.currentData() if hasattr(self, 'avatar_gen_combo') else "pixel"
        if self._avatar is not None:
            self._avatar.set_generator(gen)
        self._populate_characters()

    def _on_avatar_scale_changed(self, value):
        """Изменение масштаба аватара через слайдер."""
        scale = value / 100.0
        self.avatar_scale_label.setText(f"{value}%")
        if self._avatar is not None:
            self._avatar.scale_changed.disconnect(self._on_avatar_scale_from_handle)
            self._avatar.set_scale(scale)
            self._avatar.scale_changed.connect(self._on_avatar_scale_from_handle)

    def _on_avatar_scale_from_handle(self, scale):
        """Обновление слайдера при масштабировании через кружок-зажатие."""
        if hasattr(self, 'avatar_scale_slider'):
            self.avatar_scale_slider.blockSignals(True)
            self.avatar_scale_slider.setValue(int(scale * 100))
            self.avatar_scale_slider.blockSignals(False)
            self.avatar_scale_label.setText(f"{int(scale * 100)}%")

    def _on_avatar_bg_shape_changed(self, idx):
        """Изменение формы фона аватара."""
        shapes = ["none", "circle", "rect"]
        shape = shapes[idx] if idx < len(shapes) else "none"
        if self._avatar is not None:
            self._avatar.set_bg_shape(shape)

    def _on_avatar_bg_color_changed(self):
        """Изменение цвета фона аватара."""
        from PyQt6.QtWidgets import QColorDialog
        color = QColorDialog.getColor(self._avatar_bg_color, self, "Avatar Background Color")
        if color.isValid():
            self._avatar_bg_color = color
            if self._avatar is not None:
                self._avatar.set_bg_color(color)

    def _on_avatar_generate_px(self):
        """Генерация 9 эмоций (Pixel Art — в фоновом потоке)."""
        try:
            import pixel_avatar as px
            import avatar_assets
            slug = self.avatar_char_combo.currentData() if hasattr(self, 'avatar_char_combo') else "pixel_orange"
            
            # If reference image provided, create custom avatar from it
            ref_path = self._avatar_ref_path
            if ref_path:
                slug, colors = avatar_assets.create_custom_from_image(ref_path, name=slug)
                # Save extracted colors to meta
                meta_path = os.path.join(avatar_assets.get_slug_dir(slug), "_meta.json")
                meta = {}
                if os.path.exists(meta_path):
                    with open(meta_path, "r", encoding="utf-8") as f:
                        meta = json.load(f)
                meta["extracted_colors"] = {k: list(v) for k, v in colors.items()}
                meta["generator"] = "pixel_custom"
                with open(meta_path, "w", encoding="utf-8") as f:
                    json.dump(meta, f, indent=2, ensure_ascii=False)
                # Update combo to show new slug
                self._gui(lambda: self._populate_characters())
            
            logger.info(f"[AVATAR] Pixel art generation: {slug}")
            self.avatar_status_label.setText("Generating pixel art...")
            self.avatar_status_label.setStyleSheet("color: #f0883e;")

            def on_done(em, err):
                if err:
                    logger.error(f"[AVATAR] {em}: {err}")
                done = sum(1 for e in px.EMOTIONS if avatar_assets.is_cached(e, slug))
                self._gui(lambda: (
                    self._update_avatar_gen_status(slug, done, len(px.EMOTIONS)),
                    self._update_preview_thumbnail(em, slug)
                ))

            def _work():
                try:
                    px.ensure_all(slug=slug, on_done=on_done)
                    self._gui(lambda: (
                        self.avatar_status_label.setText(f"Pixel art '{slug}' generated!"),
                        self.avatar_status_label.setStyleSheet("color: #3fb950;"),
                        self._avatar.reload_images() if self._avatar else None,
                        self._refresh_all_previews(slug)
                    ))
                except Exception as e:
                    logger.error(f"[AVATAR] Pixel generation failed: {e}")
                    self._gui(lambda: (
                        self.avatar_status_label.setText(f"Error: {e}"),
                        self.avatar_status_label.setStyleSheet("color: #f85149;")
                    ))

            threading.Thread(target=_work, daemon=True).start()
        except Exception as e:
            logger.error(f"[AVATAR] Pixel generation failed: {e}")
            self.avatar_status_label.setText(f"Error: {e}")
            self.avatar_status_label.setStyleSheet("color: #f85149;")

    def _on_avatar_generate_poll(self):
        """Генерация 9 эмоций через Pollinations API (в фоновом потоке)."""
        try:
            import importlib.util as _iu
            _pa_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pollinations_avatar.py")
            _spec = _iu.spec_from_file_location("pollinations_avatar_local", _pa_path)
            pa = _iu.module_from_spec(_spec)
            _spec.loader.exec_module(pa)
            slug = self.avatar_char_combo.currentData() if hasattr(self, 'avatar_char_combo') else "default"
            preset = pa.get_preset(slug)
            seed = preset["seed"]
            char = preset["character"]
            logger.info(f"[AVATAR] Pollinations generation: '{preset['name']}' (seed={seed})")
            self.avatar_status_label.setText(f"Generating '{preset['name']}' via Pollinations...")
            self.avatar_status_label.setStyleSheet("color: #f0883e;")

            def on_done(em, err):
                if err:
                    logger.error(f"[AVATAR] {em}: {err}")
                else:
                    logger.info(f"[AVATAR] {em} done")
                import avatar_assets
                done = sum(1 for e in pa.EMOTIONS if avatar_assets.is_cached(e, slug))
                self._gui(lambda: (
                    self._update_avatar_gen_status(preset['name'], done, len(pa.EMOTIONS)),
                    self._update_preview_thumbnail(em, slug)
                ))

            def _work():
                try:
                    pa.ensure_all(seed=seed, character_prompt=char, slug=slug, on_done=on_done)
                    self._gui(lambda: (
                        self._refresh_all_previews(slug),
                        self._avatar.reload_images() if self._avatar else None
                    ))
                except Exception as e:
                    logger.error(f"[AVATAR] Pollinations generation failed: {e}")

            threading.Thread(target=_work, daemon=True).start()
        except Exception as e:
            logger.error(f"[AVATAR] Pollinations generation failed: {e}")
            self.avatar_status_label.setText(f"Error: {e}")
            self.avatar_status_label.setStyleSheet("color: #f85149;")

    def _on_avatar_generate_repl(self):
        """Генерация 9 эмоций через Replicate API (в фоновом потоке)."""
        try:
            import replicate_avatar as ra
            import avatar_assets
            slug = self.avatar_char_combo.currentData() if hasattr(self, 'avatar_char_combo') else "default"
            preset = ra.get_preset(slug)
            seed = preset["seed"]
            logger.info(f"[AVATAR] Replicate generation: '{preset['name']}' (seed={seed})")
            self.avatar_status_label.setText(f"Generating '{preset['name']}' via Replicate...")
            self.avatar_status_label.setStyleSheet("color: #f0883e;")

            def on_done(em, err):
                if err:
                    logger.error(f"[AVATAR] {em}: {err}")
                else:
                    logger.info(f"[AVATAR] {em} done")
                done = sum(1 for e in ra.EMOTIONS if avatar_assets.is_cached(e, slug))
                self._gui(lambda: (
                    self._update_avatar_gen_status(preset['name'], done, len(ra.EMOTIONS)),
                    self._update_preview_thumbnail(em, slug)
                ))

            def _work():
                try:
                    ra.ensure_all(slug=slug, seed=seed, on_done=on_done)
                    self._gui(lambda: (
                        self._refresh_all_previews(slug),
                        self._avatar.reload_images() if self._avatar else None
                    ))
                except Exception as e:
                    logger.error(f"[AVATAR] Replicate generation failed: {e}")

            threading.Thread(target=_work, daemon=True).start()
        except Exception as e:
            logger.error(f"[AVATAR] Replicate generation failed: {e}")
            self.avatar_status_label.setText(f"Error: {e}")
            self.avatar_status_label.setStyleSheet("color: #f85149;")

    # ════════════════════════════════════════════════════════════
    # Actions
    # ════════════════════════════════════════════════════════════

    def _refresh_voices(self):
        voices = self.tts.get_voices()
        all_combos = [self.voice_combo, self.voice_tab_combo,
                      self.ru_voice_combo, self.en_voice_combo,
                      self.male_voice_combo, self.female_voice_combo,
                      self.narrator_voice_combo]
        for combo in all_combos:
            combo.blockSignals(True)
            combo.clear()
            for v in voices:
                combo.addItem(v.get("name", v["code"]), v["code"])
            combo.blockSignals(False)
        saved = self.settings.get("tts.voice", "ru-RU-DmitryNeural")
        for combo in [self.voice_combo, self.voice_tab_combo]:
            idx = combo.findData(saved)
            if idx >= 0:
                combo.setCurrentIndex(idx)

    def _on_voice_changed(self, idx):
        code = self.voice_combo.currentData()
        logger.info(f"[VOICE] _on_voice_changed idx={idx} code={code}")
        if code:
            self.tts.set_voice(code)
            self.settings.set("tts.voice", code)
            # Sync tab combo
            self.voice_tab_combo.blockSignals(True)
            self.voice_tab_combo.setCurrentIndex(self.voice_tab_combo.findData(code))
            self.voice_tab_combo.blockSignals(False)

    def _on_voice_tab_changed(self, idx):
        code = self.voice_tab_combo.currentData()
        logger.info(f"[VOICE] _on_voice_tab_changed idx={idx} code={code}")
        if code:
            self.tts.set_voice(code)
            self.settings.set("tts.voice", code)
            # Sync main combo
            self.voice_combo.blockSignals(True)
            self.voice_combo.setCurrentIndex(self.voice_combo.findData(code))
            self.voice_combo.blockSignals(False)

    def _on_toggle_all_edge_voices(self, checked):
        self.settings.set("tts.show_all_edge_voices", checked)
        self._refresh_voices()

    def _on_speak(self):
        text = self.result_text.toPlainText().strip()
        if not text:
            return
        self.status_label.setText("Speaking...")
        threading.Thread(target=self._do_speak, args=(text,), daemon=True).start()

    def _do_speak(self, text):
        try:
            import asyncio
            loop = asyncio.new_event_loop()
            loop.run_until_complete(self.tts.speak(text))
            loop.close()
        except Exception as e:
            logger.error(f"Speak error: {e}")
            self._gui(lambda: self.status_label.setText(f"Speak error: {e}"))

    def _on_stop_speak(self):
        try:
            self.tts.force_stop()
        except Exception:
            pass
        self.status_label.setText("Stopped")

    def _on_stop_live(self):
        self.live_scanner.stop()
        self.start_live_btn.setEnabled(True)
        self.stop_live_btn.setEnabled(False)
        self.status_label.setText("Live: OFF")

    # ── STT target-app selection (per-process capture) ──────
    def _refresh_target_apps(self):
        """Populate the Target-app combo with running processes (+ psutil)."""
        self.stt_target_combo.blockSignals(True)
        self.stt_target_combo.clear()
        self.stt_target_combo.addItem("— focused window —", None)
        try:
            import psutil
            seen = set()
            for p in psutil.process_iter(["pid", "name", "exe"]):
                try:
                    pid = p.info["pid"]
                    name = p.info["name"] or ""
                    if pid in seen or not name:
                        continue
                    seen.add(pid)
                    label = f"{name} (PID {pid})"
                    exe = p.info.get("exe") or ""
                    self.stt_target_combo.addItem(label, {"pid": pid, "name": name, "exe": exe})
                except Exception:
                    continue
        except Exception as e:
            logger.warning(f"[STT] psutil list failed: {e}")
        self.stt_target_combo.blockSignals(False)

    def _pick_target_exe(self):
        """Let the user pick an .exe file to use as the STT target."""
        from PyQt6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(
            self, "Select application (.exe)", "", "Executable (*.exe)")
        if not path:
            return
        try:
            import psutil
            pid = None
            for p in psutil.process_iter(["pid", "name", "exe"]):
                try:
                    if (p.info.get("exe") or "").lower() == path.lower():
                        pid = p.info["pid"]
                        break
                except Exception:
                    continue
        except Exception:
            pid = None
        if pid is None:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.information(
                self, "Target app",
                "This .exe is not running right now.\nStart it first, then "
                "pick it from the Target-app list (⟳ to refresh).")
            return
        name = path.split("\\")[-1]
        self.stt_target_combo.blockSignals(True)
        self.stt_target_combo.insertItem(1, f"{name} (PID {pid})",
                                          {"pid": pid, "name": name, "exe": path})
        self.stt_target_combo.setCurrentIndex(1)
        self.stt_target_combo.blockSignals(False)

    def _selected_target_pid(self):
        """Return the chosen target PID, or None for 'focused window'."""
        data = self.stt_target_combo.currentData()
        if isinstance(data, dict) and data.get("pid"):
            return int(data["pid"])
        return None

    def _on_vt_start(self):
        from voice_translator import voice_translator
        from translator import Translator

        src = self.vt_src_combo.currentText()
        dst = "ru"
        voice_translator.set_languages(src, dst)
        voice_translator.set_target_pid(self._selected_target_pid())
        voice_translator.set_model(self.settings.get("stt.whisper_model", "tiny"))
        voice_translator.set_chunk_duration(self.settings.get("stt.chunk_duration", 1.0))
        voice_translator.set_stt_tts_rate(self.settings.get("stt.tts_rate", 0))

        voice_translator.tts = self.tts
        tr = Translator()
        api_key = self.settings.get("translation.api_key", "")
        model = self.settings.get("translation.model", "")
        if api_key:
            tr.configure(api_key, model)
        voice_translator.translator = tr

        on_heard, on_text, on_flush = self._stt_callbacks()
        voice_translator.on_heard = on_heard
        voice_translator.on_text = on_text
        voice_translator.on_flush = on_flush
        voice_translator.start()
        self.vt_status.setText(f"Listening... ({src} -> {dst})")
        self.status_label.setText("Voice translate: ON")

    def _on_vt_stop(self):
        from voice_translator import voice_translator
        voice_translator.stop()
        self.vt_status.setText("Stopped")
        self.status_label.setText("Voice translate: OFF")

    def _start_stt(self):
        """Start STT voice translate (used by F6 toggle and auto-monitor)."""
        from voice_translator import voice_translator
        from translator import Translator

        if voice_translator._running:
            return

        src = self.vt_src_combo.currentText()
        dst = "ru"
        voice_translator.set_languages(src, dst)
        voice_translator.set_target_pid(self._selected_target_pid())
        voice_translator.set_model(self.settings.get("stt.whisper_model", "tiny"))
        voice_translator.set_chunk_duration(self.settings.get("stt.chunk_duration", 1.0))
        voice_translator.set_stt_tts_rate(self.settings.get("stt.tts_rate", 0))
        voice_translator.tts = self.tts
        tr = Translator()
        api_key = self.settings.get("translation.api_key", "")
        model = self.settings.get("translation.model", "")
        if api_key:
            tr.configure(api_key, model)
        voice_translator.translator = tr

        on_heard, on_text, on_flush = self._stt_callbacks()
        voice_translator.on_heard = on_heard
        voice_translator.on_text = on_text
        voice_translator.on_flush = on_flush
        voice_translator.start()
        self.region_overlay.set_stt_active(True)
        self.stt_toggle_btn.setText("Stop STT")
        self.stt_toggle_btn.setStyleSheet(
            "QPushButton { background: #da3633; } QPushButton:hover { background: #f85149; }")
        self.status_label.setText(f"STT: ON ({src} -> {dst})")

    def _stop_stt(self):
        """Stop STT voice translate (used by F6 toggle and auto-monitor)."""
        from voice_translator import voice_translator
        if not voice_translator._running:
            return
        voice_translator.stop()
        self.region_overlay.set_stt_active(False)
        self.region_overlay.set_stt_text("")
        if self.stt_heard_overlay:
            self.stt_heard_overlay.clear_text()
        if self.stt_translate_overlay:
            self.stt_translate_overlay.clear_text()
        self.stt_toggle_btn.setText("Start STT")
        self.stt_toggle_btn.setStyleSheet(
            "QPushButton { background: #2ea043; } QPushButton:hover { background: #3fb950; }")
        self.status_label.setText("STT: OFF")

    def _toggle_stt(self):
        """Manual toggle (F6 / button). Disables auto-management of this session."""
        from voice_translator import voice_translator
        if voice_translator._running:
            self._stop_stt()
        else:
            self._start_stt()
        self._stt_auto_started = False
        self._silence_ticks = 0
        self._sync_stt_overlay_label()

    def _sync_stt_overlay_label(self):
        from voice_translator import voice_translator
        running = voice_translator._running
        if self.stt_heard_overlay:
            self.stt_heard_overlay.set_action_label("Stop STT" if running else "Start STT")
        if self.region_overlay:
            self.region_overlay.set_stt_label("Stop STT" if running else "Start STT")

    def _on_overlay_stt_toggle(self):
        self._toggle_stt()

    def _on_audio_monitor(self, audio, sr):
        """Called from capture thread on non-silent audio — flag activity."""
        try:
            import numpy as np
            peak = float(np.abs(audio).max())
        except Exception:
            peak = 0.0
        if peak > 0.01:
            self._audio_activity = True

    def _auto_monitor_tick(self):
        """GUI-thread tick: auto start/stop STT based on voice activity."""
        from voice_translator import voice_translator
        if not self.stt_auto_chk.isChecked():
            self._audio_activity = False
            self._silence_ticks = 0
            return
        if self._audio_activity:
            self._audio_activity = False
            self._silence_ticks = 0
            if not voice_translator._running:
                self._start_stt()
                self._stt_auto_started = True
                logger.info("[AUTO-STT] Voice detected — STT auto-started")
        else:
            if self._stt_auto_started and voice_translator._running:
                self._silence_ticks += 1
                # ~20s of silence (40 ticks * 500ms) -> auto-stop
                if self._silence_ticks > 40:
                    self._stop_stt()
                    self._stt_auto_started = False
                    logger.info("[AUTO-STT] Silence timeout — STT auto-stopped")
            else:
                self._silence_ticks = 0

    # ── Process selector ──────────────────────────────────

    def _select_process_running(self):
        """Show dialog with running processes that have audio sessions."""
        try:
            from pycaw.pycaw import AudioUtilities
            sessions = AudioUtilities.GetAllSessions()
        except Exception:
            sessions = []

        processes = []
        seen_pids = set()
        for s in sessions:
            if s.Process and s.Process.pid not in seen_pids:
                name = s.Process.name()
                pid = s.Process.pid
                vol = s.SimpleAudioVolume.GetMasterVolume()
                processes.append((pid, name, vol))
                seen_pids.add(pid)

        processes.sort(key=lambda x: x[1].lower())

        if not processes:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.information(self, "Processes", "No processes with audio found.")
            return

        items = [f"{name} (PID={pid}, vol={vol:.0%})" for pid, name, vol in processes]
        from PyQt6.QtWidgets import QInputDialog
        item, ok = QInputDialog.getItem(self, "Select Process", "Running processes:", items, 0, False)
        if ok and item:
            idx = items.index(item)
            pid, name, vol = processes[idx]
            self._selected_pid = pid
            self._selected_process_name = name
            self.proc_label.setText(f"{name} (PID={pid})")
            self.proc_label.setStyleSheet("color: #4ec9b0;")
            self.status_label.setText(f"Process: {name} (PID={pid})")
            # Update game_audio PID for STT ducking
            from game_audio_capture import game_audio
            game_audio._game_pid = pid
            game_audio._game_name = name

    def _select_process_exe(self):
        """Browse for an .exe file and find its PID, or launch it."""
        from PyQt6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(
            self, "Select .exe", "", "Executables (*.exe);;All files (*)")
        if not path:
            return

        exe_name = os.path.basename(path)
        # Try to find existing process with this name
        import psutil
        found_pid = None
        for proc in psutil.process_iter(['pid', 'name']):
            try:
                if proc.info['name'].lower() == exe_name.lower():
                    found_pid = proc.info['pid']
                    break
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        if found_pid:
            self._selected_pid = found_pid
            self._selected_process_name = exe_name
            self.proc_label.setText(f"{exe_name} (PID={found_pid})")
            self.proc_label.setStyleSheet("color: #4ec9b0;")
            self.status_label.setText(f"Process: {exe_name} (PID={found_pid})")
        else:
            # Launch the exe
            import subprocess
            try:
                proc = subprocess.Popen([path])
                self._selected_pid = proc.pid
                self._selected_process_name = exe_name
                self.proc_label.setText(f"{exe_name} (PID={proc.pid}, launched)")
                self.proc_label.setStyleSheet("color: #dcdcaa;")
                self.status_label.setText(f"Launched: {exe_name} (PID={proc.pid})")
            except Exception as e:
                self.status_label.setText(f"Failed to launch: {e}")
                return

        # Update game_audio PID for STT ducking
        from game_audio_capture import game_audio
        game_audio._game_pid = self._selected_pid
        game_audio._game_name = self._selected_process_name

    def _clear_process(self):
        """Clear selected process — revert to auto-detect."""
        self._selected_pid = None
        self._selected_process_name = None
        self.proc_label.setText("Auto-detect (no selection)")
        self.proc_label.setStyleSheet("color: #969696;")
        from game_audio_capture import game_audio
        game_audio._game_pid = None
        game_audio._game_name = None
        self.status_label.setText("Process cleared — auto-detect")

    def _on_engine_changed(self, idx):
        """Handle OCR engine change."""
        engine_names = [
            "easyocr", "rapidocr",
            "zen", "google_lens",
            "tesseract", "windows_ocr"
        ]
        engine = engine_names[idx] if idx < len(engine_names) else "easyocr"
        self.settings.set("ocr.engine", engine)
        logger.info(f"[OCR] Engine changed to: {engine}")
        # Reinitialize OCR engine
        threading.Thread(target=self._init_ocr_engine, args=(engine,), daemon=True).start()

    def _init_ocr_engine(self, engine: str):
        """Initialize OCR engine in background."""
        try:
            from ocr_wrapper import OCRWrapper
            self.ocr = OCRWrapper(self.settings)
            if hasattr(self, 'live_scanner'):
                self.live_scanner.ocr_engine = self.ocr
            logger.info(f"[OCR] Engine {engine} ready")
        except Exception as e:
            logger.error(f"[OCR] Engine init failed: {e}")

    def _on_scan_mode_changed(self, idx):
        """Handle scan mode change (OCR / STT / Both)."""
        mode_names = ["OCR", "STT", "Both"]
        mode = mode_names[idx] if idx < len(mode_names) else "OCR"
        self._scan_mode = mode
        is_ocr = mode in ("OCR", "Both")
        is_stt = mode in ("STT", "Both")
        self.region_list.setVisible(is_ocr)
        self.start_live_btn.setVisible(is_ocr)
        self.stop_live_btn.setVisible(is_ocr)
        self.vt_start_btn.setVisible(is_stt)
        self.vt_stop_btn.setVisible(is_stt)
        self.stt_toggle_btn.setVisible(is_stt)
        label = {"OCR": "Text recognition only", "STT": "Voice translate only", "Both": "OCR + Voice translate"}[mode]
        self.status_label.setText(f"Mode: {label}")

    def _on_test_voice(self):
        logger.info(f"[TEST-VOICE] Current voice={self.tts.voice}, type={self.tts.voice_type}")
        self.status_label.setText("Testing...")
        threading.Thread(
            target=lambda: asyncio.run(self.tts.speak("Привет! Это тест голоса.")),
            daemon=True
        ).start()

    def _on_translate(self):
        """Old translate button - now uses new panel."""
        self._on_translate_new()

    def _do_translate(self, text, dst):
        """Old translate - now delegates to new."""
        self._do_translate_new(text, self.translate_engine_combo.currentText())

    def _on_scan(self):
        mode = getattr(self, '_scan_mode', 'OCR')
        if mode in ('OCR', 'Both'):
            if not self.ocr:
                self.status_label.setText("OCR not ready")
                return
            if not self.regions:
                self.status_label.setText("No regions defined")
                return
            self._scan_region(self.regions[0])
        if mode in ('STT', 'Both'):
            self._toggle_stt()

    def _on_live_mode_changed(self, idx):
        mode = "continuous" if idx == 1 else "manual"
        self.live_scanner.continuous_mode = (mode == "continuous")
        self.settings.set("live.mode", mode)
        self.live_status_label.setText(f"Mode: {'Continuous' if mode == 'continuous' else 'Manual'}")
        if mode == "continuous":
            self.live_status_label.setStyleSheet("color: #569cd6; font-size: 11px;")
        else:
            self.live_status_label.setStyleSheet("color: #969696; font-size: 11px;")

    def _on_punct_stream_toggled(self, checked):
        self.live_scanner.set_punct_mode(checked)
        if checked:
            self.live_status_label.setText("Mode: Punct Stream")
            self.live_status_label.setStyleSheet("color: #da3633; font-size: 11px;")
        else:
            mode = "continuous" if self.live_mode_combo.currentIndex() == 1 else "manual"
            self.live_status_label.setText(f"Mode: {'Continuous' if mode == 'continuous' else 'Manual'}")
            self.live_status_label.setStyleSheet("color: #969696; font-size: 11px;")
        self.settings.set("live.punct_stream", checked)

    def _on_live_toggle(self, state):
        if state == True or state == Qt.CheckState.Checked.value:
            self.live_scanner.start()
            self.start_live_btn.setEnabled(False)
            self.stop_live_btn.setEnabled(True)
            self.status_label.setText("Live: ON")
        else:
            self.live_scanner.stop()
            self.start_live_btn.setEnabled(True)
            self.stop_live_btn.setEnabled(False)
            self.status_label.setText("Live: OFF")

    def _add_region(self):
        screen = QApplication.primaryScreen().geometry()
        w, h = screen.width(), screen.height()
        name = self._get_active_window_title()
        if not name:
            name = f"Region {len(self.regions) + 1}"
        region = {
            "name": name,
            "x": w // 4, "y": h // 4,
            "width": w // 2, "height": h // 4,
        }
        self.regions.append(region)
        self._refresh_region_list()
        self._save_regions()
        self.status_label.setText(f"Added: {name}")

    def _edit_region(self):
        item = self.region_list.currentItem()
        if not item:
            return
        idx = self.region_list.row(item)
        self._edit_region_by_index(idx)

    def _delete_region(self):
        item = self.region_list.currentItem()
        if not item:
            return
        idx = self.region_list.row(item)
        self._delete_region_by_index(idx)

    def _select_area(self):
        sel = RegionSelector()
        result = sel.get_region()
        if result:
            result["name"] = result.get("name", f"Region {len(self.regions) + 1}")
            self.regions.append(result)
            self._refresh_region_list()
            self._save_regions()
            self.region_overlay.set_regions(self.regions)

    def _capture_focused_window(self):
        info = self._get_active_window_rect()
        if not info:
            self.status_label.setText("No active window detected")
            return
        title, x, y, w, h = info
        if not title:
            title = f"Region {len(self.regions) + 1}"
        region = {"name": title, "x": x, "y": y, "width": w, "height": h, "locked": True}
        self.regions.append(region)
        self._refresh_region_list()
        self._save_regions()
        self.region_overlay.set_regions(self.regions)
        self.status_label.setText(f"Captured: {title}")

    def _get_active_window_title(self):
        try:
            import ctypes
            hwnd = ctypes.windll.user32.GetForegroundWindow()
            if not ctypes.windll.user32.IsWindowVisible(hwnd):
                return ""
            length = ctypes.windll.user32.GetWindowTextLengthW(hwnd) + 1
            buf = ctypes.create_unicode_buffer(length)
            ctypes.windll.user32.GetWindowTextW(hwnd, buf, length)
            return buf.value.strip() if buf.value else ""
        except Exception:
            return ""

    def _get_active_window_rect(self):
        try:
            import ctypes
            import ctypes.wintypes
            hwnd = ctypes.windll.user32.GetForegroundWindow()
            if not hwnd or not ctypes.windll.user32.IsWindowVisible(hwnd):
                return None
            length = ctypes.windll.user32.GetWindowTextLengthW(hwnd) + 1
            buf = ctypes.create_unicode_buffer(length)
            ctypes.windll.user32.GetWindowTextW(hwnd, buf, length)
            title = buf.value.strip() if buf.value else ""
            rect = ctypes.wintypes.RECT()
            ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
            w, h = rect.right - rect.left, rect.bottom - rect.top
            if w <= 0 or h <= 0:
                return None
            return (title, rect.left, rect.top, w, h)
        except Exception:
            return None

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
        name = self.regions[index].get("name", "")
        reply = QMessageBox.question(
            self, "Delete Region",
            f"Delete '{name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            del self.regions[index]
            self._refresh_region_list()
            self._save_regions()
            self.region_overlay.set_regions(self.regions)

    def _load_game_fonts(self):
        """Load game fonts into the font family combo."""
        from font_manager import get_font_manager
        fm = get_font_manager()
        fonts = fm.get_font_names()
        self.font_family_combo.addItem("System Default")
        for f in fonts:
            self.font_family_combo.addItem(f)
        logger.info(f"[FONTS] Loaded {len(fonts)} game fonts")

    def _on_region_moved(self, idx, x, y, w, h):
        if idx < len(self.regions):
            self.regions[idx]["x"] = x
            self.regions[idx]["y"] = y
            self.regions[idx]["width"] = w
            self.regions[idx]["height"] = h
            self._refresh_region_list()
            self._region_save_timer.start(400)

    def _on_region_deleted(self, idx):
        if idx < len(self.regions):
            del self.regions[idx]
            self._refresh_region_list()
            self._save_regions()

    def _show_region_context_menu(self, index):
        if index < 0 or index >= len(self.regions):
            return
        region = self.regions[index]
        locked = region.get("locked", False)
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
        action = menu.exec(QCursor.pos())
        if action == edit_action:
            self._edit_region_by_index(index)
        elif action == delete_action:
            self._delete_region_by_index(index)
        elif action == lock_action:
            region["locked"] = not locked
            self._save_regions()
            self.region_overlay.set_regions(self.regions)
        elif action == scan_action:
            self._scan_region(self.regions[index])

    def _on_overlay_voice_speak(self, role):
        voice_params = self.region_overlay.get_voice_params(role)
        voice = voice_params.get("ru_voice", self.settings.get("tts.voice", "ru-RU-DmitryNeural"))
        self.tts.set_voice(voice)
        text = self.result_text.toPlainText().strip()
        if not text:
            self.status_label.setText("No text to speak")
            return
        self.status_label.setText(f"Speaking ({role})...")
        threading.Thread(target=lambda: asyncio.run(self.tts.speak(text)), daemon=True).start()

    def _on_overlay_start(self):
        self._on_live_toggle(True)

    def _on_overlay_stop(self):
        self._on_live_toggle(False)

    def _on_overlay_voice_params_changed(self, role, params):
        if "pitch" in params:
            self.tts.set_role_pitch(role, params["pitch"])
        if "speed" in params:
            self.tts.set_role_rate(role, params["speed"])

    def _scan_region(self, region):
        if not self.ocr:
            self.status_label.setText("OCR not ready")
            return
        self.status_label.setText(f"Scanning {region.get('name', '')}...")
        x, y, w, h = region["x"], region["y"], region["width"], region["height"]
        # Show scan indicator on overlays
        self.region_overlay.set_scanning(True)
        self.region_overlay.update()
        if self.ocr_text_overlay:
            self.ocr_text_overlay.set_scanning(True)

        def _do():
            try:
                from PIL import Image
                import mss
                with mss.mss() as sct:
                    monitor = {"left": x, "top": y, "width": w, "height": h}
                    shot = sct.grab(monitor)
                    img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
                # Show screenshot IMMEDIATELY
                self._pending_preview = img
                self._gui(lambda: self._show_preview(self._pending_preview))
                # Then OCR
                result = self.ocr.recognize(img)
                text = result[0] if isinstance(result, tuple) else str(result)
                self._pending_text = text
                self._gui(self._apply_scan_result)
            except Exception as e:
                logger.error(f"Scan error: {e}", exc_info=True)
                self._gui(lambda: self.status_label.setText(f"Error: {e}"))
                self._gui(lambda: self.region_overlay.set_scanning(False))
        threading.Thread(target=_do, daemon=True).start()

    def _apply_scan_result(self):
        logger.info(f"[SCAN] _apply_scan_result called, pending_text={repr(self._pending_text[:80] if hasattr(self, '_pending_text') and self._pending_text else '(empty)')}")
        self.region_overlay.set_scanning(False)
        if self.ocr_text_overlay:
            self.ocr_text_overlay.set_scanning(False)
        if hasattr(self, '_pending_preview') and self._pending_preview:
            self._show_preview(self._pending_preview)
            self._pending_preview = None
        if hasattr(self, '_pending_text') and self._pending_text:
            logger.info(f"[SCAN] Setting text ({len(self._pending_text)} chars)")
            # _on_text_detected_gui уже использует typewriter эффект
            self._on_text_detected_gui(self._pending_text)
            self._pending_text = ""
        else:
            logger.warning(f"[SCAN] _pending_text is empty or missing")
        self.status_label.setText("Done")
        self.tabs.setCurrentIndex(1)
        if self.regions:
            self.region_overlay.show_overlay()

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

    def _refresh_region_list(self):
        self.region_list.clear()
        for i, region in enumerate(self.regions):
            name = region.get("name", f"R{i+1}")
            x, y = region.get("x", 0), region.get("y", 0)
            w, h = region.get("width", 0), region.get("height", 0)
            item = QListWidgetItem(f"[{w}x{h}] @ {x},{y}  {name}")
            item.setData(Qt.ItemDataRole.UserRole, i)
            self.region_list.addItem(item)
        self.region_overlay.set_regions(self.regions)
        if self.regions:
            self.region_overlay.show_overlay()
        else:
            self.region_overlay.hide()

    def _on_save_txt(self):
        text = self.result_text.toPlainText()
        if not text:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save", "", "Text (*.txt)")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            self.status_label.setText(f"Saved: {path}")

    def _on_ui_lang_changed(self, lang):
        set_language(lang)
        self.settings.set("ui.language", lang)

    def _setup_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            logger.warning("System tray not available")
            return
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon))
        tray_menu = QMenu()
        tray_menu.addAction("Show", self.showNormal)
        tray_menu.addSeparator()
        tray_menu.addAction("Scan", self._on_scan)
        tray_menu.addAction("Start Live", lambda: self._on_live_toggle(True))
        tray_menu.addAction("Stop Live", self._on_stop_live)
        tray_menu.addSeparator()
        tray_menu.addAction("Start STT", self._toggle_stt)
        tray_menu.addAction("Stop TTS", self._on_stop_speak)
        tray_menu.addSeparator()
        tray_menu.addAction("Translate EN->RU", self._on_translate_new)
        tray_menu.addAction("Copy Original", lambda: QApplication.clipboard().setText(self.result_text.toPlainText()))
        tray_menu.addAction("Copy Translation", lambda: QApplication.clipboard().setText(self.translate_text.toPlainText()))
        tray_menu.addSeparator()
        tray_menu.addAction("Quit", self.close)
        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self._on_tray_activated)
        self.tray_icon.show()

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.showNormal()
            self.activateWindow()

    def _load_settings(self):
        try:
            self.overlay_check.setChecked(self.settings.get("overlay.enabled", True))
            self.opacity_slider.setValue(int(self.settings.get("overlay.opacity", 0.8) * 100))
            self.font_spin.setValue(self.settings.get("overlay.font_size", 14))
            font_family = self.settings.get("overlay.font_family", "System Default")
            idx = self.font_family_combo.findText(font_family)
            if idx >= 0:
                self.font_family_combo.setCurrentIndex(idx)
            self.minimize_check.setChecked(self.settings.get("general.start_minimized", False))
            self.log_check.setChecked(self.settings.get("general.log_enabled", True))

            live_mode = self.settings.get("live.mode", "manual")
            idx = 1 if live_mode == "continuous" else 0
            self.live_mode_combo.setCurrentIndex(idx)
            self.live_scanner.continuous_mode = (live_mode == "continuous")
            self.live_status_label.setText(f"Mode: {'Continuous' if live_mode == 'continuous' else 'Manual'}")
            if live_mode == "continuous":
                self.live_status_label.setStyleSheet("color: #569cd6; font-size: 11px;")
            else:
                self.live_status_label.setStyleSheet("color: #969696; font-size: 11px;")

            # Punct stream restore
            punct = self.settings.get("live.punct_stream", False)
            self.punct_stream_check.setChecked(punct)
            self.live_scanner.set_punct_mode(punct)
            if punct:
                self.live_status_label.setText("Mode: Punct Stream")
                self.live_status_label.setStyleSheet("color: #da3633; font-size: 11px;")

            self.stop_live_btn.setEnabled(False)

            # OCR auto-translate
            if hasattr(self, 'auto_translate_check'):
                self.auto_translate_check.setChecked(
                    self.settings.get("ocr.auto_translate", True))
            # Multi-pass OCR
            if hasattr(self, 'multi_pass_check'):
                self.multi_pass_check.setChecked(
                    self.settings.get("ocr.multi_pass", False))
            # OCR engine
            if hasattr(self, 'engine_combo'):
                engine = self.settings.get("ocr.engine", "google_lens")
                engine_map = {
                    "easyocr": 0, "rapidocr": 1,
                    "zen": 2, "google_lens": 3,
                    "tesseract": 4, "windows_ocr": 5
                }
                idx = engine_map.get(engine, 3)  # google_lens index
                self.engine_combo.setCurrentIndex(idx)
        except Exception as e:
            logger.warning(f"Load settings error: {e}")

    def _save_settings(self):
        self.settings.set("overlay.enabled", self.overlay_check.isChecked())
        self.settings.set("overlay.opacity", self.opacity_slider.value() / 100.0)
        self.settings.set("overlay.font_size", self.font_spin.value())
        self.settings.set("overlay.font_family", self.font_family_combo.currentText())
        self.settings.set("general.start_minimized", self.minimize_check.isChecked())
        self.settings.set("general.log_enabled", self.log_check.isChecked())
        if hasattr(self, 'auto_translate_check'):
            self.settings.set("ocr.auto_translate", self.auto_translate_check.isChecked())
        if hasattr(self, 'multi_pass_check'):
            self.settings.set("ocr.multi_pass", self.multi_pass_check.isChecked())
        if hasattr(self, 'engine_combo'):
            engine_names = [
                "easyocr", "rapidocr",
                "zen", "google_lens",
                "tesseract", "windows_ocr"
            ]
            idx = self.engine_combo.currentIndex()
            self.settings.set("ocr.engine", engine_names[idx] if idx < len(engine_names) else "easyocr")

    def closeEvent(self, event):
        self._save_settings()
        self._cleanup_global_hotkeys()
        self.region_overlay.hide()
        for ov in (self.ocr_text_overlay, self.stt_heard_overlay,
                   self.stt_translate_overlay):
            if ov:
                try:
                    ov.close()
                except Exception:
                    pass
        # Cleanup Qt6 avatar
        if self._avatar:
            try:
                self._avatar.hide_avatar()
                self._avatar.close()
            except Exception:
                pass
        # Cleanup game translator (native widget)
        if hasattr(self, '_gvt_widget') and self._gvt_widget:
            try:
                self._gvt_widget.cleanup()
            except Exception:
                pass
        if self.tray_icon:
            self.tray_icon.hide()
        event.accept()

    # ════════════════════════════════════════════════════════════
    # MORT dark theme
    # ════════════════════════════════════════════════════════════

    def showEvent(self, event):
        super().showEvent(event)
        self.raise_()
        self.activateWindow()
        self.setFocus()

    def _mort_theme(self):
        fs = self._font_size
        return f"""
        QMainWindow, QWidget {{
            background-color: #1e1e1e;
            color: #d4d4d4;
            font-family: "Segoe UI", "Tahoma", sans-serif;
            font-size: {fs}px;
        }}
        QTabWidget::pane {{
            border: 1px solid #3c3c3c;
            background: #252526;
        }}
        QTabBar::tab {{
            background: #2d2d30;
            color: #969696;
            border: 1px solid #3c3c3c;
            border-bottom: none;
            padding: 6px 18px;
            margin-right: 1px;
            border-top-left-radius: 4px;
            border-top-right-radius: 4px;
            font-size: {fs}px;
        }}
        QTabBar::tab:selected {{
            background: #252526;
            color: #ffffff;
            font-weight: bold;
        }}
        QTabBar::tab:hover:!selected {{
            background: #383838;
        }}
        QGroupBox {{
            color: #569cd6;
            font-weight: bold;
            border: 1px solid #3c3c3c;
            border-radius: 4px;
            margin-top: 10px;
            padding: 12px 8px 8px 8px;
            font-size: {fs}px;
        }}
        QGroupBox::title {{
            subcontrol-origin: margin;
            left: 10px;
            padding: 0 4px;
        }}
        QPushButton {{
            background-color: #0e639c;
            color: #ffffff;
            border: none;
            border-radius: 4px;
            padding: 6px 14px;
            font-weight: bold;
            font-size: {fs}px;
        }}
        QPushButton:hover {{
            background-color: #1177bb;
        }}
        QPushButton:pressed {{
            background-color: #094771;
        }}
        QPushButton:disabled {{
            background-color: #3c3c3c;
            color: #6c6c6c;
        }}
        QComboBox {{
            background-color: #3c3c3c;
            color: #d4d4d4;
            border: 1px solid #555;
            border-radius: 3px;
            padding: 4px 8px;
            min-height: 22px;
            font-size: {fs}px;
        }}
        QComboBox::drop-down {{ border: none; width: 20px; }}
        QComboBox QAbstractItemView {{
            background-color: #2d2d30;
            color: #d4d4d4;
            selection-background-color: #094771;
            border: 1px solid #555;
            font-size: {fs}px;
        }}
        QLineEdit, QTextEdit {{
            background-color: #3c3c3c;
            color: #d4d4d4;
            border: 1px solid #555;
            border-radius: 3px;
            padding: 4px;
            font-size: {fs}px;
        }}
        QLineEdit:focus, QTextEdit:focus {{ border-color: #007acc; }}
        QSpinBox {{
            background-color: #3c3c3c;
            color: #d4d4d4;
            border: 1px solid #555;
            border-radius: 3px;
            padding: 3px;
            font-size: {fs}px;
        }}
        QSlider::groove:horizontal {{
            background: #3c3c3c; height: 4px; border-radius: 2px;
        }}
        QSlider::handle:horizontal {{
            background: #007acc; width: 14px; height: 14px;
            margin: -5px 0; border-radius: 7px;
        }}
        QCheckBox {{ color: #d4d4d4; spacing: 6px; font-size: {fs}px; }}
        QCheckBox::indicator {{
            width: 16px; height: 16px; border-radius: 3px;
            border: 1px solid #555; background: transparent;
        }}
        QCheckBox::indicator:checked {{
            background-color: #007acc; border-color: #007acc;
        }}
        QListWidget {{
            background-color: #2d2d30; color: #d4d4d4;
            border: 1px solid #3c3c3c; border-radius: 3px;
            font-size: {fs}px;
        }}
        QListWidget::item:selected {{ background-color: #094771; color: #ffffff; }}
        QListWidget::item:hover {{ background-color: #2a2d2e; }}
        QScrollArea {{ border: none; background: transparent; }}
        QScrollBar:vertical {{
            background: #1e1e1e; width: 12px; border: none;
        }}
        QScrollBar::handle:vertical {{
            background: #424242; border-radius: 5px; min-height: 30px;
        }}
        QScrollBar::handle:vertical:hover {{ background: #555; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
        QStatusBar {{
            background-color: #007acc; color: #ffffff; font-size: {max(10, fs - 1)}px;
        }}
        QLabel {{ font-size: {fs}px; }}
        """
