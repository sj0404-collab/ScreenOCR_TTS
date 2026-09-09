"""Test OCR + TTS on NTE game window"""
import sys, os, asyncio, ctypes, time
from ctypes import wintypes

os.chdir(os.path.dirname(os.path.abspath(__file__)))

HWND = 787670

def capture_game_window(hwnd):
    """Capture ONLY the game window"""
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtGui import QImage, QPainter, QGuiApplication, QRegion
    from PyQt6.QtCore import QRect

    app = QApplication.instance() or QApplication(sys.argv)

    # Get window rect
    rect = wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
    x, y = rect.left, rect.top
    w = rect.right - rect.left
    h = rect.bottom - rect.top

    print(f"Game window: {x},{y} {w}x{h}")

    # Bring game window to front first
    ctypes.windll.user32.SetForegroundWindow(hwnd)
    time.sleep(0.3)

    screen = QGuiApplication.primaryScreen()
    if screen:
        # grabWindow(0, x, y, w, h) captures screen area at (x,y) size (w,h)
        pixmap = screen.grabWindow(0, x, y, w, h)
        img = pixmap.toImage()
        path = "screenshots/nte_game_only.png"
        os.makedirs("screenshots", exist_ok=True)
        img.save(path)
        print(f"Screenshot: {path} ({w}x{h})")
        return path, w, h
    return None, 0, 0


async def test_ocr_on_game():
    print("=" * 55)
    print("  TEST: OCR + TTS on NTE (HWND=787670)")
    print("=" * 55)

    # Capture
    img_path, w, h = capture_game_window(HWND)
    if not img_path:
        print("ERROR: Could not capture")
        return

    # OCR
    print("\n--- OCR ---")
    import easyocr
    reader = easyocr.Reader(['ru', 'en'], gpu=False, verbose=False)
    results = reader.readtext(img_path)

    # Filter: keep only game UI text (high confidence, Russian)
    game_texts = []
    for bbox, text, conf in results:
        text = text.strip()
        if conf > 0.5 and len(text) > 1:
            # Skip terminal/code junk
            if any(skip in text.lower() for skip in ['import', 'print', 'save', 'ocr', 'path',
                                                       'png', 'buf', 'img', 'format', 'butes',
                                                       'test_path', 'screenshots', 'directly',
                                                       'captured', 'screenshot', 'engine']):
                continue
            game_texts.append((text, conf))

    print(f"Found {len(game_texts)} game text blocks:")
    for text, conf in game_texts:
        print(f"  [{conf:.0%}] {text}")

    # TTS test
    if game_texts:
        print("\n--- TTS ---")
        from tts_engine import TTSEngine
        from settings import Settings
        settings = Settings()
        tts = TTSEngine(settings)

        # Speak first game text
        first_text = game_texts[0][0]
        print(f"Speaking: \"{first_text}\"")
        await tts.speak(first_text)
        print("TTS done!")
    else:
        print("\nNo game text to speak")

    # Translate test
    if game_texts:
        print("\n--- TRANSLATE ---")
        from translator import Translator
        translator = Translator()
        # Load API key from settings if available
        api_key = settings.get("translation.api_key", "")
        model = settings.get("translation.model", "")
        if api_key:
            translator.configure(api_key, model)
        src_text = game_texts[0][0]
        print(f"Translating: \"{src_text}\"")
        try:
            result = translator.translate(src_text, dst="en")
            print(f"Result: \"{result}\"")
        except Exception as e:
            print(f"Translate error (expected without API key): {e}")

    print("\n" + "=" * 55)
    print("  ALL TESTS COMPLETE")
    print("=" * 55)

if __name__ == "__main__":
    asyncio.run(test_ocr_on_game())
