"""
themes.py — Тёмная/светлая тема для Tkinter GUI.

Использование:
    from themes import ThemeManager
    tm = ThemeManager(root)
    tm.apply_theme("dark")
"""
import tkinter as tk
from tkinter import ttk
import json
import os


THEMES = {
    "light": {
        "name": "Светлая",
        "bg": "#f0f0f0",
        "fg": "#000000",
        "bg_entry": "#ffffff",
        "bg_button": "#e0e0e0",
        "fg_button": "#000000",
        "bg_hover": "#d0d0d0",
        "bg_select": "#0078d4",
        "fg_select": "#ffffff",
        "bg_text": "#ffffff",
        "fg_text": "#000000",
        "bg_label": "#f0f0f0",
        "fg_label": "#000000",
        "bg_frame": "#e8e8e8",
        "border": "#c0c0c0",
        "accent": "#0078d4",
        "success": "#28a745",
        "warning": "#ffc107",
        "error": "#dc3545",
        "link": "#0066cc",
    },
    "dark": {
        "name": "Тёмная",
        "bg": "#1e1e1e",
        "fg": "#d4d4d4",
        "bg_entry": "#2d2d2d",
        "bg_button": "#333333",
        "fg_button": "#d4d4d4",
        "bg_hover": "#404040",
        "bg_select": "#264f78",
        "fg_select": "#ffffff",
        "bg_text": "#1e1e1e",
        "fg_text": "#d4d4d4",
        "bg_label": "#1e1e1e",
        "fg_label": "#d4d4d4",
        "bg_frame": "#252525",
        "border": "#3c3c3c",
        "accent": "#569cd6",
        "success": "#4ec9b0",
        "warning": "#ce9178",
        "error": "#f44747",
        "link": "#569cd6",
    },
    "manga": {
        "name": "Манга",
        "bg": "#f5f0e8",
        "fg": "#2c2c2c",
        "bg_entry": "#fffdf7",
        "bg_button": "#e8e0d0",
        "fg_button": "#2c2c2c",
        "bg_hover": "#d8d0c0",
        "bg_select": "#c0392b",
        "fg_select": "#ffffff",
        "bg_text": "#fffdf7",
        "fg_text": "#2c2c2c",
        "bg_label": "#f5f0e8",
        "fg_label": "#2c2c2c",
        "bg_frame": "#ebe5d8",
        "border": "#c8c0b0",
        "accent": "#c0392b",
        "success": "#27ae60",
        "warning": "#f39c12",
        "error": "#c0392b",
        "link": "#2980b9",
    },
}


class ThemeManager:
    """Менеджер тем для Tkinter."""
    def __init__(self, root: tk.Tk = None, config_path: str = None):
        self.root = root
        self.current_theme = "dark"
        self.themes = THEMES

        if config_path is None:
            config_path = os.path.join(os.path.dirname(__file__), "theme.json")
        self.config_path = config_path
        self.load()

    def load(self):
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.current_theme = data.get("theme", "dark")
            except Exception:
                pass

    def save(self):
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump({"theme": self.current_theme}, f)

    def set_theme(self, name: str):
        if name in self.themes:
            self.current_theme = name
            self.save()
            if self.root:
                self.apply()

    def get_theme(self) -> dict:
        return self.themes.get(self.current_theme, self.themes["dark"])

    def get_color(self, key: str) -> str:
        return self.get_theme().get(key, "#000000")

    def apply(self):
        """Применить тему ко всем виджетам."""
        if not self.root:
            return
        theme = self.get_theme()
        self.root.configure(bg=theme["bg"])
        self._apply_widget(self.root, theme)

    def _apply_widget(self, widget, theme):
        try:
            wtype = widget.winfo_class()
            if wtype in ("Frame", "Labelframe"):
                widget.configure(bg=theme["bg_frame"])
            elif wtype == "Label":
                widget.configure(bg=theme["bg_label"], fg=theme["fg_label"])
            elif wtype == "Entry":
                widget.configure(bg=theme["bg_entry"], fg=theme["fg"], insertbackground=theme["fg"])
            elif wtype == "Text":
                widget.configure(bg=theme["bg_text"], fg=theme["fg_text"],
                                 insertbackground=theme["fg_text"])
            elif wtype == "Button":
                widget.configure(bg=theme["bg_button"], fg=theme["fg_button"],
                                 activebackground=theme["bg_hover"])
            elif wtype == "Checkbutton" or wtype == "Radiobutton":
                widget.configure(bg=theme["bg"], fg=theme["fg"],
                                 selectcolor=theme["bg_button"],
                                 activebackground=theme["bg"])
            elif wtype == "Combobox":
                pass  # ttk widget
        except Exception:
            pass

        for child in widget.winfo_children():
            self._apply_widget(child, theme)

    def apply_to_ttk(self, style: ttk.Style = None):
        """Применить тему к ttk виджетам."""
        if style is None:
            style = ttk.Style()
        theme = self.get_theme()
        style.theme_use("clam")

        style.configure(".", background=theme["bg"], foreground=theme["fg"])
        style.configure("TFrame", background=theme["bg_frame"])
        style.configure("TLabel", background=theme["bg_label"], foreground=theme["fg_label"])
        style.configure("TButton", background=theme["bg_button"], foreground=theme["fg_button"])
        style.configure("TEntry", fieldbackground=theme["bg_entry"], foreground=theme["fg"])
        style.configure("TCheckbutton", background=theme["bg"], foreground=theme["fg"])
        style.configure("TRadiobutton", background=theme["bg"], foreground=theme["fg"])
        style.configure("TCombobox", fieldbackground=theme["bg_entry"], foreground=theme["fg"])
        style.configure("Treeview", background=theme["bg_entry"], foreground=theme["fg"],
                        fieldbackground=theme["bg_entry"])
        style.configure("Treeview.Heading", background=theme["bg_button"], foreground=theme["fg_button"])
        style.configure("TNotebook", background=theme["bg"])
        style.configure("TNotebook.Tab", background=theme["bg_button"], foreground=theme["fg_button"])
        style.configure("Accent.TButton", background=theme["accent"], foreground="#ffffff")

    def get_available_themes(self) -> list[str]:
        return list(self.themes.keys())


if __name__ == "__main__":
    root = tk.Tk()
    root.title("Theme Preview")
    root.geometry("300x200")

    tm = ThemeManager(root)

    for name in tm.get_available_themes():
        btn = tk.Button(root, text=name, command=lambda n=name: tm.set_theme(n))
        btn.pack(pady=5)

    tm.apply()
    root.mainloop()
