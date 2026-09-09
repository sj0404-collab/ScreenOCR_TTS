"""Test OCR on NTE game window"""
import sys
import os
import asyncio
import ctypes
from ctypes import wintypes
import time

os.chdir(os.path.dirname(os.path.abspath(__file__)))

from settings import Settings
from ocr_wrapper import OCRWrapper

HWND = 787670

def capture_window(hwnd):
    """Capture screenshot of window using Win32 API"""
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtGui import QImage, QPainter, QGuiApplication
    from PyQt6.QtCore import QRect

    app = QApplication.instance() or QApplication(sys.argv)

    # Get window rect
    rect = wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
    x, y = rect.left, rect.top
    w = rect.right - rect.left
    h = rect.bottom - rect.top

    print(f"Window rect: {x},{y} {w}x{h}")

    # Capture using Qt
    screen = QGuiApplication.primaryScreen()
    if screen:
        pixmap = screen.grabWindow(0, x, y, w, h)
        img = pixmap.toImage()
        path = "screenshots/nte_test.png"
        os.makedirs("screenshots", exist_ok=True)
        img.save(path)
        print(f"Screenshot saved: {path}")
        return img
    return None

async def test_ocr():
    print("=" * 50)
    print("OCR TEST ON NTE (HWND=787670)")
    print("=" * 50)

    settings = Settings()
    # Force region to full window
    rect = wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(HWND, ctypes.byref(rect))
    w = rect.right - rect.left
    h = rect.bottom - rect.top
    print(f"Game window size: {w}x{h}")

    # Capture screenshot first
    img = capture_window(HWND)

    # Init OCR
    print("\nInitializing OCR...")
    ocr = OCRWrapper(settings)
    print(f"OCR engine: {ocr.engine_type}")

    # Try to recognize from captured image
    if img:
        # Run OCR directly on the captured screenshot
        test_path = "screenshots/nte_test.png"
        print(f"Running OCR on {test_path}...")

        # Use easyocr directly on the image
        import easyocr
        reader = easyocr.Reader(['ru', 'en'], gpu=False, verbose=False)
        results = reader.readtext(test_path)

        print(f"\nOCR Results ({len(results)} blocks):")
        for i, (bbox, text, conf) in enumerate(results):
            print(f"  [{i+1}] conf={conf:.2f}  text=\"{text}\"")

        if not results:
            print("  (no text detected — game region may be empty or text is stylized)")
    else:
        print("Could not capture screenshot")

    print("\nDone!")

if __name__ == "__main__":
    asyncio.run(test_ocr())
