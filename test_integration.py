# -*- coding: utf-8 -*-
"""Comprehensive test of all OCR/TTS improvements."""
import time
import os
from PIL import Image

def main():
    # Тест 1: Модули
    print('='*60)
    print('ТЕСТ 1: Импорт модулей')
    print('='*60)
    modules = ['ocr_text_cleaner', 'ocr_engines', 'ocr_wrapper', 'tflite_ocr', 'tts_engine', 'settings']
    for m in modules:
        try:
            __import__(m)
            print(f'  [OK] {m}')
        except Exception as e:
            print(f'  [FAIL] {m}: {e}')

    # Тест 2: Реестр движков
    print()
    print('='*60)
    print('ТЕСТ 2: Реестр OCR движков')
    print('='*60)
    from ocr_engines import list_engines, list_presets, fallback_chain
    for e in list_engines():
        net = 'ONLINE' if e.requires_network else 'LOCAL'
        print(f'  [{e.priority:2d}] {e.name:20s} ({e.id:15s}) {net}')
    print()
    for p in list_presets():
        print(f'  {p.preset.value:10s}: contrast={p.contrast}, threshold={p.text_threshold}')
    print()
    for eng in ['tflite_cyrillic', 'easyocr', 'google_lens']:
        chain = fallback_chain(eng)
        print(f'  {eng:15s} -> {chain}')

    # Тест 3: Text Cleaner
    print()
    print('='*60)
    print('ТЕСТ 3: OCR Text Cleaner')
    print('='*60)
    from ocr_text_cleaner import (
        fix_lookalikes_per_word, join_line_hyphens, normalize_whitespace,
        filter_garbage_tokens, looks_like_dictionary_ramp, full_clean_pipeline,
        cyrillic_fitness, is_acceptable_cyrillic_text
    )
    tests = [
        ('Lookalikes', 'голocoвых', fix_lookalikes_per_word),
        ('Hyphens', 'хо-\\nрошо', join_line_hyphens),
        ('Whitespace', '  Hello   world  ', normalize_whitespace),
        ('Garbage', 'мир Hello @@@', filter_garbage_tokens),
    ]
    for name, inp, fn in tests:
        out = fn(inp)
        status = 'OK' if out else 'EMPTY'
        print(f'  [{status}] {name}: {repr(inp)} -> {repr(out)}')

    pipe_tests = ['голocoвых', 'мир', 'Simple test']
    for t in pipe_tests:
        cleaned = full_clean_pipeline(t)
        print(f'  [PIPE] {repr(t)} -> {repr(cleaned)}')

    print(f'  [FIT] cyrillic_fitness("Привет мир") = {cyrillic_fitness("Привет мир"):.2f}')
    print(f'  [FIT] cyrillic_fitness("Hello World") = {cyrillic_fitness("Hello World"):.2f}')
    print(f'  [FIT] is_acceptable("Привет мир") = {is_acceptable_cyrillic_text("Привет мир")}')
    print(f'  [RAMP] looks_like_dictionary_ramp("ABCDEFGHIJKLM") = {looks_like_dictionary_ramp("ABCDEFGHIJKLM")}')

    # Тест 4: Модели
    print()
    print('='*60)
    print('ТЕСТ 4: TFLite модели (pp-ocrv3+v5)')
    print('='*60)
    models_dir = 'models/cyrillic_ocr'
    expected = [
        ('cyrillic_detector.tflite', 4_000_000),
        ('cyrillic_recognizer_v3.tflite', 8_000_000),
        ('cyrillic_recognizer_v5.tflite', 7_000_000),
        ('cyrillic_dict_v3.txt', 100),
        ('cyrillic_dict_v5.txt', 100),
    ]
    for fname, min_size in expected:
        path = os.path.join(models_dir, fname)
        if os.path.exists(path):
            size = os.path.getsize(path)
            status = 'OK' if size > min_size else 'SMALL'
            print(f'  [{status}] {fname}: {size/1024/1024:.2f} MB')
        else:
            print(f'  [MISS] {fname}')

    # Тест 5: OCRWrapper
    print()
    print('='*60)
    print('ТЕСТ 5: OCRWrapper инициализация')
    print('='*60)
    from settings import Settings
    from ocr_wrapper import OCRWrapper
    s = Settings()
    ocr = OCRWrapper(s)
    print(f'  Engine: {ocr.engine_type}')
    print(f'  Active: {ocr._active_engine_id}')
    print(f'  Engine class: {type(ocr._active_engine).__name__}')
    print(f'  Fallback: {ocr._fallback_engines}')
    print(f'  Preset: {ocr._content_preset}')
    print(f'  Tuning: contrast={ocr._tuning.contrast}, threshold={ocr._tuning.text_threshold}')

    # Тест 6: Распознавание
    print()
    print('='*60)
    print('ТЕСТ 6: Распознавание через все движки')
    print('='*60)
    from ocr_engines import create_engine
    img = Image.open('screenshot.png')

    # TFLite Cyrillic
    t0 = time.time()
    eng = create_engine('tflite_cyrillic', s.__dict__ if hasattr(s, '__dict__') else s)
    if eng:
        text, conf = eng.recognize(img)
        t1 = time.time()
        print(f'  [TFLiteCyrillic] {t1-t0:.2f}s, conf={conf:.0f}%, text={text[:80]}...')
    else:
        print('  [TFLiteCyrillic] FAILED')

    # EasyOCR (skip if slow)
    print('  [EasyOCR] SKIP (too slow on first init)')

    # Тест 7: TTS
    print()
    print('='*60)
    print('ТЕСТ 7: TTS разбивка на предложения')
    print('='*60)
    from tts_engine import TTSEngine
    tts = TTSEngine.__new__(TTSEngine)
    tts._detect_text_lang = lambda t: 'ru' if any('\u0400' <= c <= '\u04FF' for c in t) else 'en'
    tts.role_settings = {}
    tts.current_role = None

    chunks = tts._split_by_language('Привет! Как дела? Хорошо. Пока.')
    for c in chunks:
        print(f'  [{c[2]:12s}] {c[0]}')

    # Тест 8: Просодия
    print()
    print('='*60)
    print('ТЕСТ 8: TTS просодия по типу предложения')
    print('='*60)
    tts.volume = 1.0
    prosody_tests = [
        ('Ты где?', 'question'),
        ('Отлично!', 'exclamation'),
        ('Обычный текст.', 'statement'),
    ]
    for text, stype in prosody_tests:
        rate, pitch = tts._get_emotion_adjustments(text, None, stype)
        print(f'  [{stype:12s}] rate={rate:+d}, pitch={pitch:+d}')

    print()
    print('='*60)
    print('ВСЕ ТЕСТЫ ЗАВЕРШЕНЫ')
    print('='*60)

if __name__ == '__main__':
    main()
