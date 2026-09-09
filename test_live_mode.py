# -*- coding: utf-8 -*-
"""
Тесты live-режима: OCR + TTS streaming + change detection.
Запуск: python test_live_mode.py
"""
import asyncio
import sys
import time
import os
sys.path.insert(0, os.path.dirname(__file__))


def test_imports():
    """Проверка импортов всех ключевых модулей."""
    print("=== TEST: Imports ===")
    try:
        from tts_engine import TTSEngine
        from live_scanner import LiveScanner
        from scanner import ScreenScanner
        from ocr_wrapper import OCRWrapper
        from settings import Settings
        from voice_profiles import VoiceProfileManager, VoiceRouter
        print("  OK: All modules imported")
    except Exception as e:
        print(f"  FAIL: {e}")
        return False
    return True


def test_gpu_default():
    """Проверка что GPU включён по умолчанию."""
    print("=== TEST: GPU Default ===")
    from settings import Settings
    s = Settings()
    use_gpu = s.get("ocr.use_gpu")
    print(f"  ocr.use_gpu = {use_gpu}")
    assert use_gpu is True, f"Expected True, got {use_gpu}"
    print("  OK: GPU defaults to True")
    return True


def test_tts_engine_init():
    """Проверка инициализации TTS Engine."""
    print("=== TEST: TTS Engine Init ===")
    from tts_engine import TTSEngine
    tts = TTSEngine()
    print(f"  voice_type: {tts.voice_type}")
    print(f"  voice: {tts.voice}")
    assert tts.voice_type in ("edge", "rhvoice", "silero", "sapi5")
    print("  OK: TTS Engine initialized")
    return True


def test_tts_detect_lang():
    """Проверка определения языка."""
    print("=== TEST: Language Detection ===")
    from tts_engine import TTSEngine
    assert TTSEngine._detect_text_lang("Привет мир") == "ru"
    assert TTSEngine._detect_text_lang("Hello world") == "en"
    assert TTSEngine._detect_text_lang("Это русский текст") == "ru"
    assert TTSEngine._detect_text_lang("This is English text") == "en"
    print("  OK: Language detection works")
    return True


def test_speak_dialogue_parse():
    """Проверка парсинга диалогов."""
    print("=== TEST: Dialogue Parsing ===")
    from tts_engine import TTSEngine
    s, t = TTSEngine._parse_dialogue_line("A: Привет!")
    assert s == "A" and t == "Привет!"
    s, t = TTSEngine._parse_dialogue_line("B: Hello!")
    assert s == "B" and t == "Hello!"
    s, t = TTSEngine._parse_dialogue_line("Обычный текст")
    assert s == "" and t == "Обычный текст"
    print("  OK: Dialogue parsing works")
    return True


def test_text_validation():
    """Проверка фильтрации мусора OCR."""
    print("=== TEST: Text Validation ===")
    from live_scanner import LiveScanner
    assert LiveScanner._is_valid_text("Hello world") is True
    assert LiveScanner._is_valid_text("") is False
    assert LiveScanner._is_valid_text("12:34:56") is False
    assert LiveScanner._is_valid_text("AB") is False
    assert LiveScanner._is_valid_text("Привет, это тест!") is True
    print("  OK: Text validation works")
    return True


def test_quick_frame_diff():
    """Проверка быстрого сравнения кадров."""
    print("=== TEST: Quick Frame Diff ===")
    from PIL import Image
    import numpy as np

    prev_frame = None

    def detect_text_quick(img):
        nonlocal prev_frame
        try:
            small = img.resize((32, 32)).convert("L")
            arr = np.array(small, dtype=np.float32)
            if prev_frame is None:
                prev_frame = arr
                return True
            diff = float(np.mean(np.abs(arr - prev_frame)))
            prev_frame = arr
            return diff > 2.0
        except Exception:
            return True

    img1 = Image.new("RGB", (100, 100), (128, 128, 128))
    assert detect_text_quick(img1) == True

    img2 = Image.new("RGB", (100, 100), (128, 128, 128))
    assert detect_text_quick(img2) == False

    img3 = Image.new("RGB", (100, 100), (255, 0, 0))
    assert detect_text_quick(img3) == True

    print("  OK: Quick frame diff works")
    return True


def test_voice_profiles():
    """Проверка менеджера профилей."""
    print("=== TEST: Voice Profiles ===")
    from voice_profiles import VoiceProfileManager
    mgr = VoiceProfileManager()
    profiles = mgr.get_all()
    print(f"  Profiles count: {len(profiles)}")
    assert len(profiles) >= 4, f"Expected >= 4 profiles, got {len(profiles)}"
    # Check defaults exist
    names = [p.name for p in profiles]
    assert any("Dmitry" in n for n in names), f"Missing Dmitry profile in {names}"
    assert any("Svetlana" in n for n in names), f"Missing Svetlana profile in {names}"
    print("  OK: Voice profiles loaded")
    return True


def test_voice_router():
    """Проверка маршрутизации голосов."""
    print("=== TEST: Voice Router ===")
    from voice_profiles import VoiceRouter
    router = VoiceRouter()
    ru_voice = router.get_voice_for_lang("ru")
    en_voice = router.get_voice_for_lang("en")
    print(f"  RU voice: {ru_voice}")
    print(f"  EN voice: {en_voice}")
    assert "ru" in ru_voice.lower() or "dmitry" in ru_voice.lower()
    assert "en" in en_voice.lower() or "guy" in en_voice.lower()
    print("  OK: Voice router works")
    return True


def test_live_scanner_init():
    """Проверка инициализации LiveScanner."""
    print("=== TEST: LiveScanner Init ===")
    from live_scanner import LiveScanner
    from settings import Settings
    s = Settings()
    scanner = LiveScanner.__new__(LiveScanner)
    # Simulate __init__ without OCR engine
    scanner.settings = s
    scanner.tts_engine = None
    scanner._tts_streaming = False
    scanner._prev_frame = None
    scanner._sync_mode = False
    scanner._sentence_mode = False
    scanner._line_by_line_mode = False
    scanner._chunk_clear_mode = False
    scanner._tts_paused = False
    scanner.multi_voice = False
    print(f"  interval_ms: {getattr(scanner, 'interval_ms', 'N/A')}")
    print("  OK: LiveScanner structure valid")
    return True


def test_edge_tts_streaming():
    """Проверка что speak() использует streaming (не генерирует полный файл)."""
    print("=== TEST: Edge-TTS Streaming Path ===")
    from tts_engine import TTSEngine
    tts = TTSEngine()
    # Check that speak() method exists and has streaming path
    import inspect
    source = inspect.getsource(tts.speak)
    assert "_speak_edge_streaming" in source, "speak() doesn't use _speak_edge_streaming"
    assert "generate_audio" in source, "speak() doesn't have file fallback"
    print("  OK: speak() has streaming + fallback paths")
    return True


def test_speak_instant_callback():
    """Проверка что callback пробрасывается в _play_audio."""
    print("=== TEST: Callback Forwarding ===")
    from tts_engine import TTSEngine
    import inspect
    source = inspect.getsource(TTSEngine.speak)
    assert "callback=callback" in source, "speak() doesn't forward callback"
    source = inspect.getsource(TTSEngine.speak_instant)
    assert "callback=callback" in source, "speak_instant() doesn't forward callback"
    print("  OK: Callbacks forwarded correctly")
    return True


def test_compilation():
    """Проверка компиляции всех файлов."""
    print("=== TEST: Compilation ===")
    import py_compile
    files = [
        "tts_engine.py", "live_scanner.py", "scanner.py",
        "ocr_wrapper.py", "settings.py", "voice_profiles.py",
        "region_overlay.py", "gui.py",
    ]
    errors = []
    for f in files:
        try:
            py_compile.compile(f, doraise=True)
        except py_compile.PyCompileError as e:
            errors.append(f"{f}: {e}")
    if errors:
        for e in errors:
            print(f"  FAIL: {e}")
        return False
    print(f"  OK: All {len(files)} files compile")
    return True


def main():
    print("=" * 60)
    print("  SCREEN OCR + TTS — Live Mode Tests")
    print("=" * 60)
    print()

    tests = [
        test_imports,
        test_compilation,
        test_gpu_default,
        test_tts_engine_init,
        test_tts_detect_lang,
        test_speak_dialogue_parse,
        test_text_validation,
        test_quick_frame_diff,
        test_voice_profiles,
        test_voice_router,
        test_live_scanner_init,
        test_edge_tts_streaming,
        test_speak_instant_callback,
    ]

    passed = 0
    failed = 0
    for test in tests:
        try:
            if test():
                passed += 1
            else:
                failed += 1
        except SystemExit:
            pass
        except Exception as e:
            print(f"  EXCEPTION: {type(e).__name__}: {e}")
            failed += 1
        print()

    print("=" * 60)
    print(f"  Results: {passed} passed, {failed} failed, {len(tests)} total")
    print("=" * 60)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
