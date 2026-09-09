# -*- coding: utf-8 -*-
"""PyQt6 VTuber avatar — frame-cycle animation (GIF-style).

Each emotion generates N frames from one source image via subtle
scale/offset transforms, creating a breathing/living loop.
Transitions between emotions crossfade the full frame sequences.
"""
import os
import math
import logging
from pathlib import Path

from PyQt6.QtWidgets import QWidget, QLabel
from PyQt6.QtCore import (Qt, QTimer, QPropertyAnimation, QEasingCurve,
                          QRect, pyqtProperty, pyqtSignal)
from PyQt6.QtGui import QPixmap, QPainter, QColor, QTransform, QImage

logger = logging.getLogger(__name__)

try:
    import replicate_avatar as ra
except ImportError:
    ra = None
try:
    import importlib.util as _iu
    _pa_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pollinations_avatar.py")
    _spec = _iu.spec_from_file_location("pollinations_avatar_local", _pa_path)
    pa_poll = _iu.module_from_spec(_spec)
    _spec.loader.exec_module(pa_poll)
except Exception:
    pa_poll = None
try:
    import pixel_avatar as pa_pix
except ImportError:
    pa_pix = None

FRAME_COUNT = 8
FRAME_INTERVAL_MS = 60
FADE_DURATION_MS = 400


def _load_frames_from_disk(emotion, target_w=250, target_h=290):
    """Load pre-generated animation frames from models/animations/{slug}_{emotion}/*.

    Returns a list of QPixmap scaled to target size.
    """
    import avatar_animation as aa
    frames = []
    # Try both "emotion" and "slug_emotion" naming conventions
    for dir_name in [f"default_{emotion}", emotion]:
        anim_dir = aa.get_anim_dir(dir_name)
        if os.path.isdir(anim_dir):
            for f in sorted(os.listdir(anim_dir)):
                if f.endswith((".png", ".jpg", ".webp")):
                    pm = QPixmap(os.path.join(anim_dir, f))
                    if not pm.isNull():
                        pm = pm.scaled(target_w, target_h,
                                       Qt.AspectRatioMode.KeepAspectRatio,
                                       Qt.TransformationMode.SmoothTransformation)
                        frames.append(pm)
            if frames:
                break
    return frames

def _generate_frames(base_pixmap, emotion="neutral", n_frames=FRAME_COUNT):
    """Generate n_frames from one pixmap with per-emotion animation style.
    
    Uses whole-image transforms (no face cropping) to preserve background.
    """
    w = base_pixmap.width()
    h = base_pixmap.height()
    
    # Try loading pre-generated animation frames from disk
    frames = _load_frames_from_disk(emotion, target_w=w, target_h=h)
    if frames:
        logger.debug(f"[QT6-AVATAR] Loaded {len(frames)} frames from disk for '{emotion}'")
        return frames
    
    # Fallback: generate n_frames with whole-image transforms
    from PIL import Image, ImageDraw
    qimg = base_pixmap.toImage()
    buf = qimg.bits().asstring(qimg.sizeInBytes())
    pil_img = Image.frombuffer("RGBA", (qimg.width(), qimg.height()), buf, "raw", "BGRA", qimg.bytesPerLine(), 1)
    
    # Add padding to prevent edge clipping during transforms
    pad = 20
    padded = Image.new("RGBA", (w + pad*2, h + pad*2), (0, 0, 0, 0))
    padded.paste(pil_img, (pad, pad))
    pw, ph = padded.size
    
    frames = []
    
    for i in range(n_frames):
        t = i / max(1, n_frames)
        angle = 0.0
        sx, sy = 1.0, 1.0
        ox, oy = 0.0, 0.0
        
        if emotion == "neutral":
            # Gentle breathing: slow vertical bob
            oy = 3.0 * math.sin(t * 2 * math.pi)
            sy = 1.0 + 0.008 * math.sin(t * 2 * math.pi)
            
        elif emotion == "thinking":
            # Slow tilt
            angle = 2.0 * math.sin(t * 2 * math.pi)
            ox = 1.0 * math.cos(t * 2 * math.pi)
            
        elif emotion == "happy":
            # Bounce
            oy = -5.0 * abs(math.sin(t * 2 * math.pi))
            sx = sy = 1.0 + 0.01 * math.sin(t * 4 * math.pi)
            
        elif emotion == "sad":
            # Droop
            oy = 3.0 * abs(math.sin(t * 2 * math.pi))
            angle = -1.5 * math.sin(t * 2 * math.pi)
            
        elif emotion == "speaking":
            # Gentle bob
            oy = 2.0 * math.sin(t * 4 * math.pi)
            sy = 1.0 + 0.005 * math.sin(t * 6 * math.pi)
            
        elif emotion == "angry":
            # Shake
            ox = 3.0 * math.sin(t * 8 * math.pi)
            
        elif emotion == "surprised":
            # Pop zoom
            pop = 1.0 + 0.03 * abs(math.sin(t * 3 * math.pi))
            sx = sy = pop
            oy = -3.0 * math.sin(t * 3 * math.pi)
            
        elif emotion == "sleeping":
            # Slow sway
            angle = 3.0 * math.sin(t * 2 * math.pi)
            oy = 2.0 * math.sin(t * 2 * math.pi)
            
        elif emotion == "wink":
            # Gentle bounce
            oy = 2.0 * math.sin(t * 2 * math.pi)
            sx = 1.0 + 0.005 * math.sin(t * 4 * math.pi)
            
        else:
            # Generic breathing
            oy = 2.0 * math.sin(t * 2 * math.pi)
            sy = 1.0 + 0.006 * math.sin(t * 2 * math.pi)
        
        # Apply transforms to whole padded image
        frame = padded.copy()
        
        # Scale
        if sx != 1.0 or sy != 1.0:
            new_w = int(pw * sx)
            new_h = int(ph * sy)
            frame = frame.resize((new_w, new_h), Image.Resampling.LANCZOS)
            # Paste centered on canvas
            result = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
            px = (pw - new_w) // 2
            py = (ph - new_h) // 2
            result.paste(frame, (px, py), frame)
            frame = result
        
        # Rotate
        if angle != 0:
            frame = frame.rotate(-angle, expand=False, fillcolor=(0, 0, 0, 0))
        
        # Offset
        if ox != 0 or oy != 0:
            shifted = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
            shifted.paste(frame, (int(ox), int(oy)), frame)
            frame = shifted
        
        # Crop back to original size (center)
        cx = (pw - w) // 2
        cy = (ph - h) // 2
        frame = frame.crop((cx, cy, cx + w, cy + h))
        
        # Convert to QPixmap
        data = frame.tobytes("raw", "RGBA")
        qimg_out = QImage(data, w, h, 4 * w, QImage.Format.Format_RGBA8888)
        pm = QPixmap.fromImage(qimg_out.copy())
        frames.append(pm)
    
    return frames


class QtAvatar(QWidget):
    """VTuber avatar with GIF-style frame cycling and smooth crossfade."""

    emotion_changed = pyqtSignal(str)
    scale_changed = pyqtSignal(float)

    def __init__(self, parent=None, controller=None, slug="default",
                 emotion="neutral", size=(400, 500), generator="pixel"):
        super().__init__(parent)
        self.controller = controller
        self.slug = slug
        self.emotion = emotion
        self.generator = generator
        self._alive = False
        self._t = 0.0
        self._frame_t = 0.0
        self._frame_idx = 0

        self._imgs = {}
        self._frame_cycles = {}

        self._cur_emotion = emotion
        self._next_emotion = None
        self._fade_progress = 0.0
        self._fading = False

        w, h = size
        self._w, self._h = w, h

        # Настройки фона и масштаба
        self._bg_color = QColor(0, 0, 0, 0)  # Прозрачный по умолчанию
        self._bg_shape = "none"  # "none", "circle", "rect"
        self._scale = 1.0

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(w, h)
        self.move(80, 80)

        self._label = QLabel(self)
        self._label.setGeometry(0, 0, w, h)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Кружок-зажатие для масштабирования (правый нижний угол)
        self._resize_handle = QLabel(self)
        self._resize_handle.setFixedSize(20, 20)
        self._resize_handle.move(w - 24, h - 24)
        self._resize_handle.setStyleSheet(
            "background-color: rgba(100, 100, 100, 150); "
            "border-radius: 10px; border: 2px solid rgba(200, 200, 200, 180);"
        )
        self._resize_handle.setCursor(Qt.CursorShape.SizeFDiagCursor)
        self._resize_handle.setVisible(True)
        self._resize_dragging = False
        self._resize_start_pos = None
        self._resize_start_scale = 1.0

        self._drag_pos = None

        self._bob_y = 0

        self._fade_anim = QPropertyAnimation(self, b"fade_alpha")
        self._fade_anim.setDuration(FADE_DURATION_MS)
        self._fade_anim.setEasingCurve(QEasingCurve.Type.InOutQuad)

        self._lip_timer = QTimer(self)
        self._lip_timer.timeout.connect(self._lip_tick)
        self._lip_level = 0.0
        self._lip_open = 0.0

        self._render_timer = QTimer(self)
        self._render_timer.timeout.connect(self._render_tick)
        self._render_timer.setInterval(FRAME_INTERVAL_MS)

        if ra:
            self._load_images()

        logger.info(f"[QT6-AVATAR] Created: {w}x{h}, slug={slug}")

    def _get_fade_alpha(self):
        return self._fade_progress

    def _set_fade_alpha(self, val):
        self._fade_progress = val
        self._render()

    fade_alpha = pyqtProperty(float, _get_fade_alpha, _set_fade_alpha)

    def _load_images(self):
        """Load emotion images and generate frame cycles."""
        import avatar_assets
        loaded_from = None
        maxw = self._w - 10
        maxh = self._h - 10

        if avatar_assets.is_all_cached(self.slug):
            for em in avatar_assets.EMOTIONS:
                path = avatar_assets.get_path(em, self.slug)
                pm = QPixmap(path)
                if not pm.isNull():
                    is_pixel = pm.width() <= 64 or pm.height() <= 64
                    mode = (Qt.TransformationMode.FastTransformation
                            if is_pixel else Qt.TransformationMode.SmoothTransformation)
                    pm = pm.scaled(maxw, maxh,
                                   Qt.AspectRatioMode.KeepAspectRatio, mode)
                    self._imgs[em] = pm
                    self._frame_cycles[em] = _generate_frames(pm, emotion=em, n_frames=FRAME_COUNT)
            if self._imgs:
                loaded_from = f"assets/{self.slug}"

        if not self._imgs and pa_pix:
            for em in pa_pix.EMOTIONS:
                try:
                    path = pa_pix.generate_emotion(em, self.slug)
                    pm = QPixmap(path)
                    if not pm.isNull():
                        maxw = self._w - 10
                        maxh = self._h - 10
                        pm = pm.scaled(maxw, maxh,
                                       Qt.AspectRatioMode.KeepAspectRatio,
                                       Qt.TransformationMode.FastTransformation)
                        self._imgs[em] = pm
                        self._frame_cycles[em] = _generate_frames(pm, emotion=em, n_frames=FRAME_COUNT)
                except Exception as e:
                    logger.warning(f"[QT6-AVATAR] Pixel gen failed for {em}: {e}")
            if self._imgs:
                loaded_from = f"pixel/{self.slug}"

        if not self._imgs:
            logger.warning("[QT6-AVATAR] No images found, will show placeholder")
        else:
            logger.info(f"[QT6-AVATAR] Loaded {len(self._imgs)} emotions, "
                        f"{FRAME_COUNT} frames each from {loaded_from}")

    def reload_images(self):
        self._imgs.clear()
        self._frame_cycles.clear()
        self._load_images()

    def set_character(self, slug):
        self.slug = slug
        self.emotion = "neutral"
        self._cur_emotion = "neutral"
        self._fading = False
        self.reload_images()

    def set_generator(self, gen):
        if gen != self.generator:
            self.generator = gen
            self.reload_images()

    def set_emotion(self, name):
        if name == self._cur_emotion and not self._fading:
            return
        if name not in self._imgs:
            return
        self._next_emotion = name
        self._fade_progress = 0.0
        self._fading = True
        self._fade_anim.setStartValue(0.0)
        self._fade_anim.setEndValue(1.0)
        self._fade_anim.start()
        old = self._cur_emotion
        self.emotion = name
        self.emotion_changed.emit(name)
        logger.debug(f"[QT6-AVATAR] Emotion: {old} -> {name}")

    def set_audio_level(self, level):
        self._lip_level = max(0.0, min(1.0, level))

    def set_bg_color(self, color: QColor):
        """Устанавливает цвет фона (QColor). Прозрачный = без фона."""
        self._bg_color = color
        self._render()

    def set_bg_shape(self, shape: str):
        """Форма фона: 'none', 'circle', 'rect'."""
        self._bg_shape = shape
        self._render()

    def set_scale(self, scale: float):
        """Масштаб аватара (0.5 = 50%, 1.0 = 100%, 2.0 = 200%)."""
        self._scale = max(0.1, min(3.0, scale))
        # Обновляем позицию кружка-зажатия
        self._resize_handle.move(self._w - 24, self._h - 24)
        self.scale_changed.emit(self._scale)
        self._render()

    def set_size(self, w: int, h: int):
        """Изменяет размер окна аватара."""
        self._w, self._h = w, h
        self.setFixedSize(w, h)
        self._label.setGeometry(0, 0, w, h)
        self._render()

    def show_avatar(self):
        if self._alive:
            return
        self._alive = True
        self.show()
        self._render_timer.start()
        self._lip_timer.start(50)
        logger.info("[QT6-AVATAR] Shown")

    def hide_avatar(self):
        self._alive = False
        self._render_timer.stop()
        self._lip_timer.stop()
        self.hide()
        logger.info("[QT6-AVATAR] Hidden")

    def _lip_tick(self):
        target = self._lip_level
        speed = 0.15 if target > self._lip_open else 0.25
        self._lip_open += (target - self._lip_open) * speed
        if self._lip_open < 0.01:
            self._lip_open = 0.0

    def _render_tick(self):
        self._t += FRAME_INTERVAL_MS / 1000.0
        self._frame_idx = int(self._t * 1000 / FRAME_INTERVAL_MS) % FRAME_COUNT
        self._render()

    def _get_frame(self, emotion, idx=None):
        """Get a specific frame from an emotion's cycle."""
        cycle = self._frame_cycles.get(emotion)
        if not cycle:
            return self._imgs.get(emotion)
        i = (idx if idx is not None else self._frame_idx) % len(cycle)
        return cycle[i]

    def _render(self):
        if not self._alive:
            return
        pm = self._get_current_pixmap()
        if pm is None:
            self._label.clear()
            return

        result = QPixmap(self._w, self._h)
        result.fill(Qt.GlobalColor.transparent)

        painter = QPainter(result)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        # Рисуем фон (если не прозрачный)
        if self._bg_color.alpha() > 0:
            painter.setBrush(self._bg_color)
            painter.setPen(Qt.PenStyle.NoPen)
            if self._bg_shape == "circle":
                painter.drawEllipse(0, 0, self._w, self._h)
            elif self._bg_shape == "rect":
                painter.drawRoundedRect(0, 0, self._w, self._h, 16, 16)
            else:
                painter.drawRect(0, 0, self._w, self._h)

        # Масштабируем аватар
        scaled_w = int(pm.width() * self._scale)
        scaled_h = int(pm.height() * self._scale)
        if self._scale != 1.0:
            pm = pm.scaled(scaled_w, scaled_h,
                           Qt.AspectRatioMode.KeepAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)

        x = (self._w - pm.width()) // 2
        y = (self._h - pm.height()) // 2
        painter.drawPixmap(x, y, pm)
        painter.end()
        self._label.setPixmap(result)

    def _get_current_pixmap(self):
        if not self._imgs:
            return self._placeholder_pixmap()

        cur_cycle = self._frame_cycles.get(self._cur_emotion)
        cur_frame = self._get_frame(self._cur_emotion) if cur_cycle else self._imgs.get(self._cur_emotion)

        if self._fading and self._next_emotion:
            next_cycle = self._frame_cycles.get(self._next_emotion)
            next_frame = self._get_frame(self._next_emotion) if next_cycle else self._imgs.get(self._next_emotion)

            if cur_frame is None:
                cur_frame = list(self._imgs.values())[0]
            if next_frame is None:
                next_frame = list(self._imgs.values())[0]

            a = self._fade_progress
            w, h = cur_frame.width(), cur_frame.height()
            result = QPixmap(w, h)
            result.fill(Qt.GlobalColor.transparent)
            p = QPainter(result)
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            p.setOpacity(1.0 - a)
            p.drawPixmap(0, 0, cur_frame)
            p.setOpacity(a)
            p.drawPixmap(0, 0, next_frame)
            p.end()

            if a >= 1.0:
                self._cur_emotion = self._next_emotion
                self._next_emotion = None
                self._fading = False
                self._fade_progress = 0.0
            return result

        return cur_frame

    def _placeholder_pixmap(self):
        pm = QPixmap(self._w, self._h)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setBrush(QColor(60, 60, 80))
        p.setPen(QColor(100, 100, 120))
        p.drawRoundedRect(10, 10, self._w - 20, self._h - 20, 16, 16)
        p.setPen(QColor(200, 200, 200))
        p.drawText(QRect(0, 0, self._w, self._h),
                   Qt.AlignmentFlag.AlignCenter, "No avatar\nimages")
        p.end()
        return pm

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint()
            # Проверяем, попали ли в кружок-зажатие
            handle_rect = self._resize_handle.geometry()
            if handle_rect.contains(pos):
                self._resize_dragging = True
                self._resize_start_pos = pos
                self._resize_start_scale = self._scale
                event.accept()
                return
            # Иначе — перетаскивание окна
            self._drag_pos = event.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, event):
        pos = event.position().toPoint()
        # Масштабирование через кружок-зажатие
        if self._resize_dragging and self._resize_start_pos is not None:
            delta = pos.x() - self._resize_start_pos.x()
            new_scale = self._resize_start_scale + delta * 0.005
            new_scale = max(0.2, min(3.0, new_scale))
            self.set_scale(new_scale)
            event.accept()
            return
        # Перетаскивание окна
        if self._drag_pos is not None:
            self.move(event.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, event):
        self._resize_dragging = False
        self._resize_start_pos = None
        self._drag_pos = None

    def closeEvent(self, event):
        self.hide_avatar()
        super().closeEvent(event)


class QtAvatarManager:
    """Manager for QtAvatar — creates, shows, hides, cleans up."""

    def __init__(self):
        self._avatar = None

    def get_or_create(self, slug="default", controller=None):
        if self._avatar is None:
            self._avatar = QtAvatar(controller=controller, slug=slug)
        return self._avatar

    def show(self):
        if self._avatar:
            self._avatar.show_avatar()

    def hide(self):
        if self._avatar:
            self._avatar.hide_avatar()

    def set_emotion(self, emotion):
        if self._avatar:
            self._avatar.set_emotion(emotion)

    def set_audio_level(self, level):
        if self._avatar:
            self._avatar.set_audio_level(level)

    def set_bg_color(self, color):
        if self._avatar:
            self._avatar.set_bg_color(color)

    def set_bg_shape(self, shape):
        if self._avatar:
            self._avatar.set_bg_shape(shape)

    def set_scale(self, scale):
        if self._avatar:
            self._avatar.set_scale(scale)

    def set_size(self, w, h):
        if self._avatar:
            self._avatar.set_size(w, h)

    @property
    def widget(self):
        return self._avatar

    def cleanup(self):
        if self._avatar:
            self._avatar.hide_avatar()
            self._avatar.close()
            self._avatar = None
