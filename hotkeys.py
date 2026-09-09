"""
hotkeys.py — Горячие клавиши для быстрого доступа.

Для Termux:.volumebuttons + custom keybindings
Для Windows: keyboard (опционально) или msvcrt

По умолчанию:
  Ctrl+Shift+S — старт/стоп сканирования
  Ctrl+Shift+T — старт/стоп Voice Translate
  Ctrl+Shift+C — копировать последний результат
  Ctrl+Shift+H — показать историю
  Ctrl+Shift+Q — выход
  Ctrl+1..9    — выбрать пресет голоса
"""
import platform
import json
import os
from typing import Callable, Optional


DEFAULT_HOTKEYS = {
    "scan_toggle":     {"keys": "ctrl+shift+s", "description": "Старт/стоп сканирования"},
    "translate_toggle": {"keys": "ctrl+shift+t", "description": "Старт/стоп Voice Translate"},
    "copy_result":     {"keys": "ctrl+shift+c", "description": "Копировать последний результат"},
    "show_history":    {"keys": "ctrl+shift+h", "description": "Показать историю"},
    "quit":            {"keys": "ctrl+shift+q", "description": "Выход"},
    "preset_1":        {"keys": "ctrl+1", "description": "Пресет 1 (Оригинал)"},
    "preset_2":        {"keys": "ctrl+2", "description": "Пресет 2 (Бас)"},
    "preset_3":        {"keys": "ctrl+3", "description": "Пресет 3 (Женщина)"},
    "preset_4":        {"keys": "ctrl+4", "description": "Пресет 4 (Ребёнок)"},
    "preset_5":        {"keys": "ctrl+5", "description": "Пресет 5 (Робот)"},
    "preset_6":        {"keys": "ctrl+6", "description": "Пресет 6 (Шёпот)"},
    "preset_7":        {"keys": "ctrl+7", "description": "Пресет 7 (Монстр)"},
    "preset_8":        {"keys": "ctrl+8", "description": "Пресет 8 (Быстрый)"},
    "preset_9":        {"keys": "ctrl+9", "description": "Пресет 9 (Медленный)"},
}


class HotkeyManager:
    """Менеджер горячих клавиш."""
    def __init__(self, config_path: str = None):
        if config_path is None:
            config_path = os.path.join(os.path.dirname(__file__), "hotkeys.json")
        self.config_path = config_path
        self.hotkeys = dict(DEFAULT_HOTKEYS)
        self.callbacks: dict[str, Callable] = {}
        self._listener = None
        self.load()

    def load(self):
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for k, v in data.items():
                    if k in self.hotkeys:
                        self.hotkeys[k].update(v)
            except Exception:
                pass

    def save(self):
        data = {}
        for k, v in self.hotkeys.items():
            data[k] = {"keys": v["keys"], "description": v.get("description", "")}
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def register(self, action: str, callback: Callable):
        self.callbacks[action] = callback

    def get_key_display(self, action: str) -> str:
        if action in self.hotkeys:
            return self.hotkeys[action]["keys"]
        return ""

    def get_all_actions(self) -> dict:
        result = {}
        for action, info in self.hotkeys.items():
            result[action] = {
                "keys": info["keys"],
                "description": info.get("description", ""),
                "has_callback": action in self.callbacks,
            }
        return result

    def set_hotkey(self, action: str, keys: str):
        if action in self.hotkeys:
            self.hotkeys[action]["keys"] = keys
            self.save()

    def _trigger(self, action: str):
        if action in self.callbacks:
            self.callbacks[action]()

    def start_listening(self):
        """Запуск прослушивания (зависит от платформы)."""
        system = platform.system()
        if system == "Windows":
            self._start_windows()
        elif system == "Linux":
            self._start_linux()

    def _start_windows(self):
        try:
            import keyboard
            for action, info in self.hotkeys.items():
                keys = info["keys"]
                try:
                    keyboard.add_hotkey(keys, lambda a=action: self._trigger(a))
                except Exception:
                    pass
            self._listener = True
        except ImportError:
            pass

    def _start_linux(self):
        """Termux: используем volume buttons или termux-keyevent."""
        pass

    def stop_listening(self):
        if self._listener:
            try:
                import keyboard
                keyboard.unhook_all()
            except ImportError:
                pass
            self._listener = None


if __name__ == "__main__":
    hm = HotkeyManager()
    print("=== Hotkeys ===")
    for action, info in hm.get_all_actions().items():
        print(f"  {info['keys']:20s} — {info['description']}")
