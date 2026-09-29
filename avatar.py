# -*- coding: utf-8 -*-
"""VTuber аватар — 2D-персонаж поверх окна игры.

Два режима отрисовки:
  • image  — персонаж и эмоции сгенерированы через Pollinations API.
    Между эмоциями — плавный кроссфейд; во время речи — липсинк.
  • canvas (fallback) — если картинки ещё не сгенерированы, аватар рисуется
    на tkinter Canvas.

Окно: frameless, всегда поверх других окон, перетаскивается мышью, фон
заливается «ключевым» цветом, который на Windows делается прозрачным через
wm_attributes('-transparentcolor', ...).
"""
import math
import random
import tkinter as tk
import os
import logging

from PIL import Image, ImageTk

logger = logging.getLogger(__name__)

# Палитра для canvas-fallback
C_HAIR = "#4a3a38"
C_HAIR_D = "#3a2c2a"
C_SKIN = "#ffe0c2"
C_BLUSH = "#ffb7c0"
C_BROW = "#4a3a38"
C_MOUTH = "#7a2230"
C_MOUTH_O = "#5a1a26"
C_TONGUE = "#d96577"
C_SMILE = "#a94555"
C_TEAR = "#5fb7ff"
C_VEIN = "#d23b3b"

# Ключевой цвет фона (Windows transparentcolor).
# Используем редкий цвет, который не встречается в аниме-арт.
KEY_COLOR = "#020305"


class AvatarWindow:
    def __init__(self, controller=None, parent_root=None, on_close=None,
                 emotion="neutral", mode="auto", size=(400, 500)):
        self.controller = controller
        self.on_close = on_close
        self.emotion = emotion
        self.mode = self._resolve_mode(mode)
        self._alive = False
        self._t = 0.0
        self._blink_t = 0.0
        self._next_blink = 2.0 + random.random() * 3.0
        self._closed = False
        self._drag = None

        # image-mode state
        self.slug = "default"
        self._imgs = {}
        self._photos = {}
        self._blend_cache = {}
        self._fade_from = None
        self._fade_to = None
        self._fade_t = 0.0
        self._fade_dur = 0.35
        self._cur_photo = None
        self._img_x = 0
        self._img_y = 0
        self._dw, self._dh = 0, 0
        self._img_item = None
        self._dot_item = None

        w, h = size
        self.w, self.h = w, h

        self.top = tk.Toplevel(parent_root) if parent_root else tk.Toplevel()
        self.top.overrideredirect(True)
        self.top.geometry(f"{w}x{h}+80+80")
        self.top.attributes("-topmost", True)
        self.top.configure(bg=KEY_COLOR)
        try:
            self.top.wm_attributes("-transparentcolor", KEY_COLOR)
            logger.debug("[AVATAR] Windows transparentcolor включён")
        except Exception as e:
            logger.warning(f"[AVATAR] transparentcolor недоступен: {e}")

        self.canvas = tk.Canvas(self.top, width=w, height=h,
                                bg=KEY_COLOR, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_motion)
        self.top.protocol("WM_DELETE_WINDOW", self.hide)

        if self.mode == "image":
            self._load_images()

        logger.info(f"[AVATAR] Создан: mode={self.mode}, size={w}x{h}")

    def _resolve_mode(self, mode):
        if mode in ("image", "canvas"):
            return mode
        try:
            import pollinations_avatar as pa
            from pollinations_avatar import EMOTIONS
            if any(pa.is_cached(em) for em in EMOTIONS):
                return "image"
        except Exception:
            pass
        return "canvas"

    def _sanitize_key_color(self, im):
        """Заменяет пиксели KEY_COLOR (и старый #ff00ff) в картинке,
        чтобы они не стали прозрачными через Windows transparentcolor."""
        px = im.load()
        w, h = im.size
        kc = tuple(int(KEY_COLOR.lstrip('#')[i:i+2], 16) for i in (0, 2, 4))
        old_kc = (0xff, 0x00, 0xff)  # старый KEY_COLOR
        replaced = 0
        for y in range(h):
            for x in range(w):
                r, g, b, a = px[x, y]
                if a > 0 and ((r, g, b) == kc or (r, g, b) == old_kc):
                    nr = r + 1 if r < 255 else r - 1
                    px[x, y] = (nr, g, b, a)
                    replaced += 1
        if replaced:
            logger.debug(f"[AVATAR] Заменено {replaced} пикселей KEY_COLOR")
        return im

    def _load_images(self):
        import pollinations_avatar as pa
        maxw = self.w - 10
        maxh = self.h - 10
        loaded = {}
        for em in pa.EMOTIONS:
            path = pa.get_path(em, self.slug)
            if os.path.exists(path):
                im = Image.open(path).convert("RGBA")
                ratio = min(maxw / im.width, maxh / im.height)
                self._dw, self._dh = int(im.width * ratio), int(im.height * ratio)
                loaded[em] = self._sanitize_key_color(im.resize((self._dw, self._dh), Image.LANCZOS))
        if not loaded:
            logger.warning("[AVATAR] Картинки не найдены, fallback на canvas")
            self.mode = "canvas"
            return
        self._imgs = loaded
        for em in pa.EMOTIONS:
            if em not in self._imgs:
                self._imgs[em] = loaded[list(loaded)[0]]
        self._build_base_photos()
        self._build_blend_cache()
        self._img_item = None
        self._dot_item = None
        logger.info(f"[AVATAR] Загружено {len(self._imgs)} эмоций (image mode)")

    def _build_base_photos(self):
        for em, im in self._imgs.items():
            self._photos[em] = ImageTk.PhotoImage(im)

    def reload_images(self):
        """Перечитать картинки из кэша."""
        try:
            self._imgs.clear()
            self._photos.clear()
            self._blend_cache.clear()
            self.mode = "image"
            self._load_images()
            logger.info(f"[AVATAR] Картинки перезагружены: mode={self.mode}, imgs={len(self._imgs)}")
            return self.mode == "image"
        except Exception as e:
            logger.error(f"[AVATAR] Ошибка перезагрузки: {e}")
            return False

    def set_character(self, slug):
        self.slug = slug
        self.emotion = "neutral"
        self._fade_from = None
        self._fade_to = None
        return self.reload_images()

    def _build_blend_cache(self):
        if "speaking" not in self._imgs:
            return
        speak = self._imgs["speaking"]
        levels = [0.0, 0.35, 0.65, 1.0]
        for em, base in self._imgs.items():
            for i, a in enumerate(levels):
                blended = Image.blend(base, speak, a)
                self._blend_cache[(em, i)] = ImageTk.PhotoImage(blended)
        logger.debug(f"[AVATAR] Blend-кэш: {len(self._blend_cache)} кадров")

    def _level_idx(self, level):
        if level <= 0.05:
            return 0
        if level <= 0.3:
            return 1
        if level <= 0.6:
            return 2
        return 3

    def set_emotion(self, name):
        from pollinations_avatar import EMOTIONS
        if name not in EMOTIONS:
            return
        if name == self.emotion:
            return
        if self.mode == "image" and self._imgs:
            self._fade_from = self.emotion
            self._fade_to = name
            self._fade_t = 0.0
        old = self.emotion
        self.emotion = name
        logger.debug(f"[AVATAR] Эмоция: {old} → {name}")

    def show(self):
        if self._alive:
            return
        self._alive = True
        self.top.deiconify()
        self._tick()
        logger.info("[AVATAR] Показан")

    def hide(self):
        self._alive = False
        try:
            self.top.withdraw()
        except Exception:
            pass
        if self.on_close:
            try:
                self.on_close()
            except Exception:
                pass
        logger.info("[AVATAR] Скрыт")

    def is_shown(self):
        return self._alive

    def _on_press(self, e):
        self._drag = (e.x_root, e.y_root)

    def _on_motion(self, e):
        if not self._drag:
            return
        dx = e.x_root - self._drag[0]
        dy = e.y_root - self._drag[1]
        x = self.top.winfo_x() + dx
        y = self.top.winfo_y() + dy
        self.top.geometry(f"+{x}+{y}")
        self._drag = (e.x_root, e.y_root)

    def _tick(self):
        if not self._alive:
            return
        self._t += 0.033
        self._animate_blink()
        if self.mode == "image" and self._imgs:
            self._draw_image()
        else:
            self._draw_canvas()
        self.top.after(33, self._tick)

    def _animate_blink(self):
        self._blink_t += 0.033
        if self._closed:
            if self._blink_t > 0.12:
                self._closed = False
                self._blink_t = 0
                self._next_blink = 2.0 + random.random() * 3.5
        else:
            if self._blink_t > self._next_blink:
                self._closed = True
                self._blink_t = 0

    def _level(self):
        lvl = 0.0
        speaking = False
        if self.controller is not None:
            lvl = self.controller.get_level()
            speaking = self.controller.is_speaking()
        if lvl <= 0.0 and speaking:
            lvl = 0.35 + 0.3 * abs(math.sin(self._t * 14))
        return lvl, speaking

    # ---- IMAGE MODE ----
    def _draw_image(self):
        c = self.canvas
        lvl, speaking = self._level()

        photo = None
        if self._fade_to is not None and self._fade_from in self._imgs \
                and self._fade_to in self._imgs:
            self._fade_t += 0.033
            a = min(1.0, self._fade_t / self._fade_dur)
            blended = Image.blend(self._imgs[self._fade_from],
                                  self._imgs[self._fade_to], a)
            self._cur_photo = ImageTk.PhotoImage(blended)
            photo = self._cur_photo
            if a >= 1.0:
                self._fade_from = None
                self._fade_to = None
        else:
            if speaking and self._blend_cache:
                idx = self._level_idx(lvl)
                photo = self._blend_cache.get((self.emotion, idx)) \
                    or self._photos.get(self.emotion)
            else:
                photo = self._photos.get(self.emotion)
            self._cur_photo = photo

        bob = math.sin(self._t * 1.6) * 2.0
        if speaking:
            bob += math.sin(self._t * 9) * 1.0 * lvl
        x = (self.w - self._dw) // 2
        y = (self.h - self._dh) // 2 + int(bob)

        if photo is not None:
            if self._img_item is None:
                self._img_item = c.create_image(x, y, anchor="nw", image=photo, tags="avatar_img")
            else:
                c.coords(self._img_item, x, y)
                c.itemconfig(self._img_item, image=photo)

        color = "#36c45b" if speaking else "#aaa"
        if self._dot_item is None:
            self._dot_item = c.create_oval(self.w - 26, 10, self.w - 10, 26, fill=color, outline="", tags="dot")
        else:
            c.itemconfig(self._dot_item, fill=color)

    # ---- CANVAS FALLBACK ----
    def _draw_canvas(self, canvas=None):
        c = canvas or self.canvas
        if canvas is None:
            c.delete("all")
        w, h = self.w, self.h
        cx = w / 2
        bob = math.sin(self._t * 1.6) * 4.0
        cy = h * 0.42 + bob

        em = self.emotion
        lvl, speaking = self._level()
        em_used = "speaking" if (em == "neutral" and speaking) else em

        body_top = cy + 70
        c.create_polygon(cx - 95, h, cx - 70, body_top, cx - 28, body_top - 8,
                         cx + 28, body_top - 8, cx + 70, body_top, cx + 95, h,
                         fill="#5b6ee0", outline="")
        c.create_polygon(cx - 26, body_top - 6, cx, body_top + 24,
                         cx + 26, body_top - 6, fill="#e8ecff", outline="")
        c.create_oval(cx - 86, cy - 96, cx + 86, cy + 70, fill=C_HAIR_D, outline="")
        face_r = 76
        skin = "#ffd0b8" if em_used == "angry" else C_SKIN
        c.create_oval(cx - face_r, cy - face_r, cx + face_r, cy + face_r,
                      fill=skin, outline="")
        c.create_arc(cx - 86, cy - 96, cx + 86, cy + 30, start=0, extent=180,
                     style="chord", fill=C_HAIR, outline="")
        c.create_polygon(cx - 70, cy - 50, cx - 30, cy - 86, cx + 10, cy - 58,
                         cx + 50, cy - 88, cx + 74, cy - 50, cx + 60, cy - 24,
                         cx + 20, cy - 48, cx - 18, cy - 24, fill=C_HAIR, outline="")
        c.create_oval(cx - 78, cy - 12, cx - 66, cy + 18, fill=skin, outline="")
        c.create_oval(cx + 66, cy - 12, cx + 78, cy + 18, fill=skin, outline="")

        blush_a = {"happy": 1.0, "wink": 1.0, "sad": 0.5, "angry": 0.85}.get(
            em_used, 0.75)
        bw = 28 * (0.6 + 0.4 * blush_a)
        for sgn in (-1, 1):
            c.create_oval(cx + sgn * 38 - bw / 2, cy + 6,
                          cx + sgn * 38 + bw / 2, cy + 6 + 24 * blush_a,
                          fill=C_BLUSH, outline="")

        self._draw_brows(c, cx, cy, em_used)
        self._draw_eyes(c, cx, cy, em_used)
        self._draw_mouth(c, cx, cy, em_used, lvl, speaking)
        self._draw_extras(c, cx, cy, em_used, w, h)

        if canvas is None:
            color = "#36c45b" if speaking else "#aaa"
            c.create_oval(w - 26, 10, w - 10, 26, fill=color, outline="")

    def _draw_brows(self, c, cx, cy, em):
        y = cy - 28
        lx, rx = cx - 32, cx + 32
        w = 22
        if em == "angry":
            c.create_line(lx - w / 2, y - 8, lx + w / 2, y + 6, width=5, fill=C_BROW, capstyle="round")
            c.create_line(rx - w / 2, y + 6, rx + w / 2, y - 8, width=5, fill=C_BROW, capstyle="round")
        elif em == "sad":
            c.create_line(lx - w / 2, y + 6, lx + w / 2, y - 8, width=5, fill=C_BROW, capstyle="round")
            c.create_line(rx - w / 2, y - 8, rx + w / 2, y + 6, width=5, fill=C_BROW, capstyle="round")
        elif em == "surprised":
            c.create_line(lx - w / 2, y - 12, lx + w / 2, y - 14, width=5, fill=C_BROW, capstyle="round")
            c.create_line(rx - w / 2, y - 14, rx + w / 2, y - 12, width=5, fill=C_BROW, capstyle="round")
        elif em == "thinking":
            c.create_line(lx - w / 2, y, lx + w / 2, y - 2, width=5, fill=C_BROW, capstyle="round")
            c.create_line(rx - w / 2, y - 12, rx + w / 2, y - 14, width=5, fill=C_BROW, capstyle="round")
        elif em in ("happy", "wink", "speaking"):
            c.create_arc(lx - w / 2, y - 6, lx + w / 2, y + 8, start=200, extent=140, style="arc", width=4, outline=C_BROW)
            c.create_arc(rx - w / 2, y - 6, rx + w / 2, y + 8, start=200, extent=140, style="arc", width=4, outline=C_BROW)
        elif em == "sleeping":
            c.create_line(lx - w / 2, y + 4, lx + w / 2, y + 6, width=4, fill=C_BROW, capstyle="round")
            c.create_line(rx - w / 2, y + 6, rx + w / 2, y + 4, width=4, fill=C_BROW, capstyle="round")
        else:
            c.create_line(lx - w / 2, y, lx + w / 2, y - 2, width=4, fill=C_BROW, capstyle="round")
            c.create_line(rx - w / 2, y - 2, rx + w / 2, y, width=4, fill=C_BROW, capstyle="round")

    def _draw_eyes(self, c, cx, cy, em):
        lx, rx = cx - 32, cx + 32
        ey = cy

        def eye_open(ex, ey, scale=1.0, pupil_dx=0, pupil_dy=0):
            r = 13 * scale
            c.create_oval(ex - r, ey - r, ex + r, ey + r, fill="#fff", outline="")
            ir = 6 * scale
            c.create_oval(ex - ir + pupil_dx, ey - ir + pupil_dy, ex + ir + pupil_dx, ey + ir + pupil_dy, fill="#2a6dd6", outline="")
            pr = 3 * scale
            c.create_oval(ex - pr + pupil_dx, ey - pr + pupil_dy, ex + pr + pupil_dx, ey + pr + pupil_dy, fill="#0a1a33", outline="")
            c.create_oval(ex - 2 + pupil_dx, ey - 5 + pupil_dy, ex + 2 + pupil_dx, ey - 1 + pupil_dy, fill="#fff", outline="")

        def eye_closed_arc(ex, ey, happy=False):
            if happy:
                c.create_arc(ex - 14, ey - 8, ex + 14, ey + 12, start=20, extent=140, style="arc", width=3, outline=C_BROW)
            else:
                c.create_line(ex - 12, ey, ex + 12, ey, width=3, fill=C_BROW, capstyle="round")

        def eye_droopy(ex, ey):
            eye_open(ex, ey, scale=0.9, pupil_dy=1)
            c.create_arc(ex - 14, ey - 16, ex + 14, ey + 8, start=0, extent=180, style="chord", fill=C_SKIN, outline="")

        if em == "sleeping" or (self._closed and em not in ("happy", "wink", "surprised")):
            eye_closed_arc(lx, ey); eye_closed_arc(rx, ey)
        elif em == "happy":
            eye_closed_arc(lx, ey, True); eye_closed_arc(rx, ey, True)
        elif em == "wink":
            eye_closed_arc(lx, ey, True); eye_open(rx, ey)
        elif em == "surprised":
            eye_open(lx, ey, 1.25); eye_open(rx, ey, 1.25)
        elif em == "angry":
            eye_open(lx, ey, 0.9); eye_open(rx, ey, 0.9)
            c.create_polygon(lx - 14, ey - 12, lx + 12, ey - 10, lx + 12, ey - 4, lx - 14, ey - 8, fill=C_HAIR, outline="")
            c.create_polygon(rx - 12, ey - 10, rx + 14, ey - 12, rx + 14, ey - 8, rx - 12, ey - 4, fill=C_HAIR, outline="")
        elif em == "sad":
            eye_droopy(lx, ey); eye_droopy(rx, ey)
        elif em == "thinking":
            eye_open(lx, ey, 0.95, -2, -2); eye_open(rx, ey, 0.95, 2, -2)
        else:
            eye_open(lx, ey); eye_open(rx, ey)

    def _draw_mouth(self, c, cx, cy, em, lvl=0.0, speaking=False):
        y = cy + 28
        if em == "speaking":
            mouth_w, mouth_h = 26, 4 + lvl * 22
            c.create_oval(cx - mouth_w / 2, y, cx + mouth_w / 2, y + mouth_h, fill=C_MOUTH, outline=C_MOUTH_O)
            c.create_oval(cx - 8, y + mouth_h * 0.4, cx + 8, y + mouth_h, fill=C_TONGUE, outline="")
            c.create_rectangle(cx - 10, y, cx + 10, y + 4, fill="#fff", outline="")
        elif em == "happy":
            c.create_arc(cx - 30, y - 6, cx + 30, y + 26, start=0, extent=180, style="chord", fill=C_MOUTH, outline=C_MOUTH_O)
            c.create_arc(cx - 22, y + 2, cx + 22, y + 20, start=0, extent=180, style="chord", fill=C_TONGUE, outline="")
        elif em == "sad":
            c.create_arc(cx - 22, y + 2, cx + 22, y + 26, start=180, extent=180, style="arc", width=4, outline=C_MOUTH)
        elif em == "angry":
            c.create_line(cx - 22, y + 6, cx - 6, y, cx + 6, y + 6, cx + 22, y, width=4, fill=C_MOUTH, capstyle="round", smooth=True)
        elif em == "surprised":
            c.create_oval(cx - 12, y - 4, cx + 12, y + 20, fill=C_MOUTH, outline=C_MOUTH_O)
        elif em == "thinking":
            c.create_oval(cx + 4, y, cx + 20, y + 8, fill=C_MOUTH, outline=C_MOUTH_O)
        elif em == "wink":
            c.create_arc(cx - 22, y - 4, cx + 22, y + 18, start=10, extent=160, style="chord", fill=C_MOUTH, outline=C_MOUTH_O)
        elif em == "sleeping":
            c.create_oval(cx - 8, y, cx + 8, y + 6, fill=C_MOUTH, outline=C_MOUTH_O)
        else:
            c.create_arc(cx - 18, y - 8, cx + 18, y + 14, start=200, extent=140, style="arc", width=3, outline=C_SMILE)

    def _draw_extras(self, c, cx, cy, em, w, h):
        if em == "sad":
            for sgn in (-1, 1):
                ex = cx + sgn * 32
                c.create_oval(ex - 4, cy - 2, ex + 4, cy + 12, fill=C_TEAR, outline="")
                c.create_polygon(ex - 3, cy + 10, ex + 3, cy + 10, ex, cy + 22, fill=C_TEAR, outline="")
        elif em == "angry":
            vx, vy = cx + 56, cy - 56
            for k in range(4):
                ang = k * math.pi / 2 + math.pi / 4
                c.create_line(vx, vy, vx + 14 * math.cos(ang), vy + 14 * math.sin(ang), width=3, fill=C_VEIN, capstyle="round")
            c.create_oval(vx - 6, vy - 6, vx + 6, vy + 6, outline=C_VEIN, width=3)
        elif em == "thinking":
            bx, by = cx + 70, cy - 70
            c.create_oval(bx - 10, by - 8, bx + 10, by + 8, fill="#fff", outline=C_BROW)
            c.create_oval(bx + 6, by + 6, bx + 18, by + 16, fill="#fff", outline=C_BROW)
            for dx, r in ((-4, 2.5), (3, 2.5), (10, 2.5)):
                c.create_oval(bx + dx - r, by - 1, bx + dx + r, by - 1 + 2 * r, fill=C_BROW, outline="")
        elif em == "sleeping":
            zx, zy = cx + 60, cy - 60
            c.create_text(zx, zy, text="Z", font=("Segoe UI", 18, "bold"), fill="#88a")
            c.create_text(zx + 16, zy - 14, text="z", font=("Segoe UI", 12, "bold"), fill="#aac")
