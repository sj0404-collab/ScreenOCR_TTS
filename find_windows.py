"""Find game windows"""
import ctypes
from ctypes import wintypes

EnumWindows = ctypes.windll.user32.EnumWindows
GetWindowTextW = ctypes.windll.user32.GetWindowTextW
GetWindowTextLengthW = ctypes.windll.user32.GetWindowTextLengthW
IsWindowVisible = ctypes.windll.user32.IsWindowVisible
GetClassNameW = ctypes.windll.user32.GetClassNameW

results = []
def callback(hwnd, lparam):
    if IsWindowVisible(hwnd):
        length = GetWindowTextLengthW(hwnd)
        if length > 0:
            buf = ctypes.create_unicode_buffer(length + 1)
            GetWindowTextW(hwnd, buf, length + 1)
            cls = ctypes.create_unicode_buffer(256)
            GetClassNameW(hwnd, cls, 256)
            results.append((hwnd, buf.value, cls.value))
    return True

ENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
EnumWindows(ENUMPROC(callback), 0)

for hwnd, title, cls in results:
    print(f"HWND={hwnd}  Title=\"{title}\"  Class=\"{cls}\"")
