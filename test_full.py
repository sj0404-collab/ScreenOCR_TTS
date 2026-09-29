"""Full test suite for ScreenOCR_TTS"""
import os, sys, traceback
os.chdir(os.path.dirname(os.path.abspath(__file__)))

passed = 0
failed = 0
errs = []

def test(name, func):
    global passed, failed
    try:
        func()
        passed += 1
        print(f"  [OK] {name}")
    except Exception as e:
        failed += 1
        errs.append((name, e))
        print(f"  [FAIL] {name}: {e}")

# === 1. IMPORTS ===
print("=" * 60)
print("1. IMPORTS")
print("=" * 60)
for m in ['settings','voice_profiles','ocr_wrapper','live_scanner','tts_engine',
          'overlay','region_selector','region_overlay','ocr_indicator',
          'localization','translator','voice_context_manager','builtin_player']:
    test(f"import {m}", lambda m=m: __import__(m))

# === 2. VOICE ROUTER ===
print("\n" + "=" * 60)
print("2. VOICE ROUTER")
print("=" * 60)
from voice_profiles import VoiceRouter, SUPPORTED_LANGUAGES, VoiceDictionary, VoiceProfile, MultiVoiceParser

def t_router_defaults():
    r = VoiceRouter()
    for lang in SUPPORTED_LANGUAGES:
        v = r.get_voice_for_language(lang)
        assert v, f"No voice for {lang}"
test("defaults", t_router_defaults)

def t_detect_ru():
    assert VoiceRouter().detect_language("Привет мир") == "ru"
test("detect ru", t_detect_ru)

def t_detect_en():
    assert VoiceRouter().detect_language("Hello world") == "en"
test("detect en", t_detect_en)

def t_detect_uk():
    assert VoiceRouter().detect_language("Привіт світ") == "ru"
test("detect uk (now ru)", t_detect_uk)

def t_set_voice():
    r = VoiceRouter()
    r.set_voice_for_language("en", "en-GB-SoniaNeural")
    assert r.get_voice_for_language("en") == "en-GB-SoniaNeural"
    r.set_voice_for_language("en", "en-US-GuyNeural")
test("set/get voice", t_set_voice)

def t_fallback():
    r = VoiceRouter()
    r.set_fallback_voice("ru-RU-SvetlanaNeural")
    assert r.get_fallback_voice() == "ru-RU-SvetlanaNeural"
    r.set_fallback_voice("ru-RU-DmitryNeural")
test("fallback", t_fallback)

def t_voice_for_text():
    r = VoiceRouter()
    v = r.get_voice_for_text("Привет")
    assert v.startswith("ru-RU-")
    v = r.get_voice_for_text("Hello")
    assert v.startswith("en-")
test("get_voice_for_text", t_voice_for_text)

# === 3. VOICE PROFILES ===
print("\n" + "=" * 60)
print("3. VOICE PROFILES")
print("=" * 60)

def t_vd_defaults():
    d = VoiceDictionary()
    assert len(d.profiles) > 0
test("VoiceDictionary defaults", t_vd_defaults)

def t_vd_get():
    d = VoiceDictionary()
    p = d.get_profile("Мужской")
    assert p.voice_code == "ru-RU-DmitryNeural"
test("VoiceDictionary get", t_vd_get)

def t_vd_set_remove():
    d = VoiceDictionary()
    d.set_profile("Test", "test-voice", rate=0.1, volume=0.9, language="ru")
    p = d.get_profile("Test")
    assert p.voice_code == "test-voice"
    assert p.rate == 0.1
    d.remove_profile("Test")
    assert d.get_profile("Test").name == "default"
test("VoiceDictionary set/remove", t_vd_set_remove)

def t_mvp():
    d = VoiceDictionary()
    p = MultiVoiceParser(d)
    r = p.get_voice_for_text("Dmitry: Привет!\nSvetlana: Привет!")
    assert len(r) == 2
test("MultiVoiceParser", t_mvp)

# === 4. OCR WRAPPER ===
print("\n" + "=" * 60)
print("4. OCR WRAPPER")
print("=" * 60)
from ocr_wrapper import (_cleanup_punctuation, _filter_ocr_garbage,
    _apply_known_corrections, _edit_distance, _LAT_TO_CYR, _CYR_TO_LAT)

def t_cleanup():
    r = _cleanup_punctuation("  Hello   world  ")
    assert r == "Hello world"
test("cleanup_punctuation", t_cleanup)

def t_garbage():
    r = _filter_ocr_garbage("Hello world")
    assert "Hello" in r
    r = _filter_ocr_garbage("|~")
    assert r == ""
test("filter_garbage", t_garbage)

def t_corrections():
    assert _apply_known_corrections("curl") == "Ctrl"
    assert _apply_known_corrections("shifl") == "Shift"
    assert _apply_known_corrections("alf") == "Alt"
    assert _apply_known_corrections("hello") == "hello"
test("known_corrections", t_corrections)

def t_levenshtein():
    assert _edit_distance("abc", "abc") == 0
    assert _edit_distance("abc", "ab") == 1
    assert _edit_distance("abc", "xyz") == 3
test("edit_distance", t_levenshtein)

def t_homoglyphs():
    assert _LAT_TO_CYR["a"] == chr(0x430)  # а
    assert _CYR_TO_LAT[chr(0x430)] == "a"
test("homoglyphs", t_homoglyphs)

# === 5. LIVE SCANNER ===
print("\n" + "=" * 60)
print("5. LIVE SCANNER")
print("=" * 60)
from live_scanner import LiveScanner
from settings import Settings
from ocr_wrapper import OCRWrapper

def _mk_scanner():
    s = Settings()
    o = OCRWrapper(s)
    return LiveScanner(o, s)

def t_scanner_init():
    sc = _mk_scanner()
    assert sc.running == False
    assert sc._line_by_line_mode == False
    assert len(sc._spoken_lines) == 0
test("init", t_scanner_init)

def t_line_by_line():
    sc = _mk_scanner()
    sc.set_line_by_line_mode(True)
    assert sc._line_by_line_mode == True
    sc.set_line_by_line_mode(False)
    assert sc._line_by_line_mode == False
test("line_by_line mode", t_line_by_line)

def t_normalize():
    sc = _mk_scanner()
    n = sc._normalize_line("  Hello, World!  ")
    assert n == "hello, world"
test("normalize_line", t_normalize)

def t_text_changed():
    sc = _mk_scanner()
    sc.clear_cache()
    assert sc._text_changed("Hello World") == True
    assert sc._text_changed("Hello World") == False
    assert sc._text_changed("Goodbye Universe") == True
test("text_changed", t_text_changed)

def t_similarity():
    assert LiveScanner._similarity("hello", "hello") == 1.0
    assert LiveScanner._similarity("hello", "world") < 0.5
    assert LiveScanner._similarity("hello", "helo") == 0.6
test("similarity", t_similarity)

def t_clear_spoken():
    sc = _mk_scanner()
    sc._spoken_lines.add("test")
    sc.clear_spoken_lines()
    assert len(sc._spoken_lines) == 0
test("clear_spoken_lines", t_clear_spoken)

def t_status():
    sc = _mk_scanner()
    st = sc.get_status()
    assert "running" in st
    assert "line_by_line" in st
    assert "spoken_lines_count" in st
test("get_status", t_status)

# === 6. TTS ENGINE ===
print("\n" + "=" * 60)
print("6. TTS ENGINE")
print("=" * 60)
from tts_engine import TTSEngine

def t_tts_init():
    s = Settings()
    t = TTSEngine(s)
    assert t.voice == "ru-RU-DmitryNeural" or t.voice
    assert t.voice_type in ("edge", "sapi", "silero", "rhvoice", "persona")
test("TTS init", t_tts_init)

def t_tts_voices():
    s = Settings()
    t = TTSEngine(s)
    v = t.get_voices()
    assert len(v) > 7  # at least original edge voices
    codes = [x["code"] for x in v]
    assert "ru-RU-DmitryNeural" in codes
    assert "en-US-GuyNeural" in codes
test("TTS voices count", t_tts_voices)

def t_tts_has_router():
    s = Settings()
    t = TTSEngine(s)
    assert hasattr(t, "voice_router")
    assert hasattr(t.voice_router, "get_voice_for_language")
test("TTS has voice_router", t_tts_has_router)

def t_tts_detect_lang():
    assert TTSEngine._detect_text_lang("Привет") == "ru"
    assert TTSEngine._detect_text_lang("Hello") == "en"
test("TTS detect lang", t_tts_detect_lang)

def t_expand_abbrev():
    s = Settings()
    t = TTSEngine(s)
    r = t._expand_abbreviations("нажми вкл и т.д.")
    assert "включить" in r
    assert "так далее" in r
test("expand abbreviations", t_expand_abbrev)

# === 7. SETTINGS ===
print("\n" + "=" * 60)
print("7. SETTINGS")
print("=" * 60)
from settings import Settings

def t_settings_defaults():
    s = Settings()
    d = s._get_default_config()
    assert d["ocr"]["language"] == "rus+eng"
    assert d["ocr"]["confidence_threshold"] == 60
    assert d["tts"]["voice"] == "ru-RU-DmitryNeural"
test("settings defaults", t_settings_defaults)

def t_settings_set_get():
    s = Settings()
    s.set("test.key", "hello")
    assert s.get("test.key") == "hello"
    del s.config["test"]
test("settings set/get", t_settings_set_get)

# === 8. LOCALIZATION ===
print("\n" + "=" * 60)
print("8. LOCALIZATION")
print("=" * 60)
from localization import tr, set_language

def t_localization():
    set_language("en")
    assert tr("app_title") != "app_title"
    set_language("ru")
    assert tr("app_title") != "app_title"
    set_language("en")
test("localization", t_localization)

# === SUMMARY ===
print("\n" + "=" * 60)
print(f"RESULTS: {passed} passed, {failed} failed")
print("=" * 60)
if errs:
    print("\nFailed tests:")
    for name, e in errs:
        print(f"  - {name}: {e}")
sys.exit(1 if failed else 0)
