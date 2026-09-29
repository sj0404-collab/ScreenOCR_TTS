# -*- coding: utf-8 -*-
"""
Поиск вызовов методов, которых нет в используемой версии Qt.

Мы уже поймали QRect.clipRect() — метода нет в PyQt6, и вызов из
paintEvent ронял процесс (0xC0000409) вместе со всей отрисовкой.
Такие ошибки статически не видны, но ломают GUI полностью.
"""
import ast
import io
import os
import sys

if not (getattr(sys.stdout, "encoding", "") or "").lower().startswith("utf-8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                  errors="replace")

# Известные несовместимости PyQt5 -> PyQt6
PYQT5_ONLY = {
    "QRect.intersect": "PyQt6: QRect.intersected()",
    "QRect.contains": "PyQt6: QRect.contains() есть, ок",
    "QRegExp": "PyQt6: используйте QRegularExpression",
    "QDesktopWidget": "удалён в Qt6, берите QScreen",
    "QFontMetrics.width": "PyQt6: horizontalAdvance()",
    "QApplication.desktop": "удалён в Qt6",
    "exec_": "PyQt6: exec()",
    "print_": "PyQt6: print()",
    "QTest.qWait": "PyQt6: QTest.qWait есть",
    "QPalette.Background": "PyQt6: QPalette.ColorRole.Window",
    "QPalette.Foreground": "PyQt6: QPalette.ColorRole.WindowText",
    "QPalette.Light": "PyQt6: QPalette.ColorRole.Light",
    "QPalette.Midlight": "PyQt6: QPalette.ColorRole.Midlight",
    "QPalette.Highlight": "PyQt6: QPalette.ColorRole.Highlight",
}

FILES = [f for f in os.listdir(".") if f.endswith(".py")]

findings = []
for fn in FILES:
    try:
        src = open(fn, encoding="utf-8", errors="replace").read()
        tree = ast.parse(src, fn)
    except Exception:
        continue
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            # ищем X.clipRect(...) и прочие подозрительные
            name = node.func.attr
            base = ast.unparse(node.func.value)
            if name == "clipRect":
                findings.append((fn, node.lineno,
                                 f"{base}.clipRect() — метода нет в PyQt6"))
            if base in ("QRegExp", "QDesktopWidget"):
                findings.append((fn, node.lineno,
                                 f"{base} — удалён в Qt6"))
            if name in ("exec_", "print_"):
                findings.append((fn, node.lineno,
                                 f"{base}.{name}() — в PyQt6 без подчёркивания"))
            if base == "QFontMetrics" and name == "width":
                findings.append((fn, node.lineno,
                                 "QFontMetrics.width() -> horizontalAdvance()"))

if findings:
    print("НАЙДЕНО несовместимых вызовов:")
    for fn, line, msg in findings:
        print(f"  {fn}:{line}: {msg}")
else:
    print("Несовместимых вызовов Qt не найдено")

sys.exit(1 if findings else 0)
