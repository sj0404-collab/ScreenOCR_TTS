"""
Live Dialog Player — потоковое мультиголосовое воспроизведение с субтитрами.

Запуск:
  python live_dialog_player.py                    # диалог по умолчанию
  python live_dialog_player.py --file dialog.txt  # диалог из файла
  python live_dialog_player.py --text "Dmitry: Привет\nSvetlana: Привет"

Управление:
  Space — пауза/продолжить
  Escape / Q — выход
  S — остановить воспроизведение
"""
import asyncio
import sys
import argparse
from pathlib import Path

# Исправляем кодировку консоли Windows
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QLabel,
    QGraphicsOpacityEffect, QFrame
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QObject
from PyQt6.QtGui import QFont, QColor, QPainter, QBrush, QPainterPath, QKeySequence, QShortcut

from tts_engine import TTSEngine
from voice_profiles import VoiceProfile, VoiceDictionary
from settings import Settings

# ─── Цвета для персонажей ───────────────────────────────────────────
CHARACTER_COLORS = {
    "Dmitry":   "#4FC3F7",   # голубой
    "Svetlana": "#F48FB1",   # розовый
    "Guy":      "#81C784",   # зелёный
    "Jenny":    "#FFB74D",   # оранжевый
    "Ryan":     "#B39DDB",   # фиолетовый
    "Sonia":    "#E57373",   # красный
}

# ─── Маппинг персонажей на голоса ──────────────────────────────────
DEFAULT_PROFILES = {
    "Dmitry":   ("ru-RU-DmitryNeural",   "ru", "male"),
    "Svetlana": ("ru-RU-SvetlanaNeural", "ru", "female"),
    "Guy":      ("en-US-GuyNeural",      "en", "male"),
    "Jenny":    ("en-US-JennyNeural",    "en", "female"),
    "Ryan":     ("en-GB-RyanNeural",     "en", "male"),
    "Sonia":    ("en-GB-SoniaNeural",    "en", "female"),
}

# ─── Диалог по умолчанию ────────────────────────────────────────────
DEFAULT_DIALOG = """\
Dmitry: Привет! Я Дмитрий. Как у тебя дела?
Svetlana: Привет, Дмитрий! Всё отлично, спасибо. А у тебя как?
Dmitry: Тоже хорошо. Слушай, я тут встретил наших друзей из Америки.
Guy: Hey everyone! Nice to meet you all. How's it going?
Jenny: Hi there! We just arrived from New York. The weather is great here!
Svetlana: Oh, that's wonderful! Do you like Moscow?
Guy: Absolutely! The city is amazing. We've been to Red Square yesterday.
Jenny: Yes, it was breathtaking. We took so many photos.
Dmitry: Кстати, к нам ещё приехали ребята из Лондона.
Ryan: Hello everyone! I'm Ryan, and this is my sister Sonia.
Sonia: Hi! We're from London. Lovely to meet you all.
Svetlana: Welcome to Russia! Have you tried Russian food yet?
Ryan: Not yet, but we're looking forward to it. Any recommendations?
Jenny: I heard the borscht here is incredible. We should try it together.
Dmitry: Отличная идея! Давайте пойдём в ресторан.
Sonia: That sounds perfect. Let's go!
Guy: Great idea! I'm starving.
Svetlana: Тогда пошли! Я знаю одно замечательное место.
Dmitry: Все готовы? Тогда вперёд!"""


class SubtitleOverlay(QWidget):
    """Оверлей субтитров поверх всех окон."""

    def __init__(self):
        super().__init__()
        self._paused = False
        self._setup_ui()

    def _setup_ui(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 12, 20, 12)

        self.container = QFrame()
        self.container.setStyleSheet(
            "background-color: rgba(0, 0, 0, 180);"
            "border-radius: 12px;"
            "border: 2px solid rgba(255, 255, 255, 80);"
        )
        cl = QVBoxLayout(self.container)
        cl.setContentsMargins(18, 12, 18, 12)
        cl.setSpacing(4)

        # Имя персонажа
        self.name_label = QLabel("")
        self.name_label.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))
        self.name_label.setStyleSheet("color: #AAAAAA; background: transparent;")
        cl.addWidget(self.name_label)

        # Текст реплики
        self.text_label = QLabel("")
        self.text_label.setFont(QFont("Segoe UI", 16))
        self.text_label.setStyleSheet("color: white; background: transparent;")
        self.text_label.setWordWrap(True)
        self.text_label.setMinimumWidth(400)
        self.text_label.setMaximumWidth(700)
        cl.addWidget(self.text_label)

        layout.addWidget(self.container)

        # Прозрачность
        self.opacity_effect = QGraphicsOpacityEffect(self)
        self.opacity_effect.setOpacity(0.92)
        self.setGraphicsEffect(self.opacity_effect)

        # Позиция — нижняя часть экрана
        self._position_at_bottom()

    def _position_at_bottom(self):
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            self.adjustSize()
            w = min(self.width(), 750)
            self.setFixedWidth(w)
            self.adjustSize()
            x = geo.x() + (geo.width() - self.width()) // 2
            y = geo.y() + geo.height() - self.height() - 40
            self.move(x, y)

    def update_subtitle(self, character: str, text: str):
        """Обновить субтитры."""
        if not character:
            self.name_label.setText("")
            self.text_label.setText("")
            return

        color = CHARACTER_COLORS.get(character, "#FFFFFF")
        self.name_label.setText(character)
        self.name_label.setStyleSheet(f"color: {color}; background: transparent;")
        self.text_label.setText(text)
        self.adjustSize()
        self._position_at_bottom()

    def show_overlay(self):
        self.show()
        self.raise_()
        self._apply_win32_styles()

    def _apply_win32_styles(self):
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

    def fade_out(self):
        self.opacity_effect.setOpacity(0.0)


class Bridge(QObject):
    """Мост между async TTS и PyQt6 GUI."""
    subtitle_ready = pyqtSignal(str, str)   # character, text
    finished = pyqtSignal()


class LiveDialogPlayer:
    """Потоковый мультиголосовой плеер."""

    def __init__(self, settings: Settings, profiles: dict = None):
        self.settings = settings
        self.engine = TTSEngine(settings)
        self.profiles = profiles or DEFAULT_PROFILES
        self._paused = False
        self._stopped = False
        self._register_profiles()

    def _register_profiles(self):
        """Регистрирует профили персонажей в voice_dict."""
        for name, (voice_code, lang, gender) in self.profiles.items():
            self.engine.voice_dict.profiles[name.lower()] = VoiceProfile(
                name, voice_code, rate=0.0, volume=1.0, language=lang, gender=gender
            )

    async def play(self, text: str, subtitle_callback=None):
        """
        Потоковое воспроизведение диалога.
        subtitle_callback(character_name, text) вызывается для каждой реплики.
        """
        self._stopped = False
        self._paused = False

        parts = self.engine.multi_voice_parser.get_voice_for_text(text)
        if not parts:
            print("[LIVE] Не удалось распарсить текст")
            return

        print(f"\n{'='*60}")
        print(f"  LIVE DIALOG — {len(parts)} реплик")
        print(f"{'='*60}\n")

        for i, (profile, content) in enumerate(parts):
            if self._stopped:
                print("[LIVE] Остановлен")
                break

            # Пауза
            while self._paused and not self._stopped:
                await asyncio.sleep(0.1)

            if not content.strip():
                continue

            character = profile.name
            voice_code = profile.voice_code

            print(f"  [{i+1}/{len(parts)}] {character} ({voice_code}): {content[:60]}...")

            # Субтитры
            if subtitle_callback:
                subtitle_callback(character, content)

            # Генерация и воспроизведение
            try:
                old_voice = self.engine.voice
                self.engine.voice = voice_code
                audio_data = await self.engine.generate_audio(content)
                self.engine.voice = old_voice

                if audio_data:
                    cache_dir = Path(__file__).parent / "tts_cache"
                    cache_dir.mkdir(exist_ok=True)
                    temp_path = cache_dir / f"live_{i}_{hash(content) % 10000}.mp3"
                    temp_path.write_bytes(audio_data)
                    await self.engine._play_audio(str(temp_path))
                    await asyncio.sleep(0.15)
                else:
                    print(f"    ⚠ Пустое аудио")

            except Exception as e:
                print(f"    ✘ Ошибка: {e}")
                self.engine.voice = old_voice if 'old_voice' in dir() else self.engine.voice

        # Сигнал завершения
        if subtitle_callback:
            subtitle_callback("", "")
        print(f"\n{'='*60}")
        print("  LIVE DIALOG — ЗАВЕРШЁН")
        print(f"{'='*60}\n")


def run_app(text: str, profiles: dict):
    """Запускает PyQt6 приложение с оверлеем и TTS."""
    app = QApplication(sys.argv)

    overlay = SubtitleOverlay()
    overlay.show_overlay()

    player = LiveDialogPlayer(Settings(), profiles)

    bridge = Bridge()
    bridge.subtitle_ready.connect(lambda c, t: overlay.update_subtitle(c, t))
    bridge.finished.connect(lambda: overlay.fade_out())

    def subtitle_cb(character, content):
        bridge.subtitle_ready.emit(character, content)

    async def _run():
        await player.play(text, subtitle_callback=subtitle_cb)
        bridge.finished.emit()
        # Держим оверлей 2 сек после окончания
        await asyncio.sleep(2)
        overlay.close()

    # Запускаем TTS в отдельном потоке
    import threading
    loop = asyncio.new_event_loop()

    def _thread():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_run())

    t = threading.Thread(target=_thread, daemon=True)
    t.start()

    # Горячие клавиши
    def toggle_pause():
        player._paused = not player._paused
        state = "⏸ ПАУЗА" if player._paused else "▶ ПРОДОЛЖЕНО"
        overlay.update_subtitle(state, "Нажмите Space для продолжения")
        print(f"[LIVE] {state}")

    def stop_playback():
        player._stopped = True
        overlay.update_subtitle("", "")
        print("[LIVE] Остановка...")

    QShortcut(QKeySequence("Space"), overlay).activated.connect(toggle_pause)
    QShortcut(QKeySequence("Escape"), overlay).activated.connect(overlay.close)
    QShortcut(QKeySequence("Q"), overlay).activated.connect(overlay.close)
    QShortcut(QKeySequence("S"), overlay).activated.connect(stop_playback)

    # Инструкция
    print("\n╔══════════════════════════════════════════╗")
    print("║      LIVE DIALOG PLAYER                  ║")
    print("╠══════════════════════════════════════════╣")
    print("║  Space — пауза / продолжить              ║")
    print("║  S     — остановить                      ║")
    print("║  Esc/Q — выход                           ║")
    print("╚══════════════════════════════════════════╝\n")

    overlay.update_subtitle("🔴 LIVE", "Загрузка диалога...")

    sys.exit(app.exec())


def main():
    parser = argparse.ArgumentParser(description="Live Dialog Player")
    parser.add_argument("--file", type=str, help="Файл с диалогом")
    parser.add_argument("--text", type=str, help="Текст диалога (с \\n)")
    args = parser.parse_args()

    if args.file:
        text = Path(args.file).read_text(encoding="utf-8")
    elif args.text:
        text = args.text.replace("\\n", "\n")
    else:
        text = DEFAULT_DIALOG

    run_app(text, DEFAULT_PROFILES)


if __name__ == "__main__":
    main()
