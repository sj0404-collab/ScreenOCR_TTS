"""
Тестирование всего нового со старым.
Покрытие: image_preprocessor, voice_changer, tts_engine integration, ocr_wrapper, settings.
"""
import sys
import os
import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PASS = 0
FAIL = 0

def test(name, condition):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  OK  {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}")

def make_test_image(w=200, h=100, bg_color=(255, 255, 255), text_color=(0, 0, 0)):
    img = Image.new("RGB", (w, h), bg_color)
    return img

def make_dark_image():
    return make_test_image(bg_color=(30, 30, 30), text_color=(200, 200, 200))

def make_green_image():
    return make_test_image(bg_color=(0, 150, 0), text_color=(255, 255, 255))

def make_light_image():
    return make_test_image(bg_color=(240, 240, 240), text_color=(20, 20, 20))

def numpy_equal(a, b):
    return np.array_equal(np.array(a), np.array(b))


# ═══════════════════════════════════════════════════════════════
# 1. IMAGE PREPROCESSOR
# ═══════════════════════════════════════════════════════════════
print("\n=== IMAGE PREPROCESSOR ===")

from image_preprocessor import (
    preprocess_for_ocr, rgb_auto_contrast, rgb_channel_boost, rgb_invert,
    hsv_enhance_text, hsv_isolate_color, hsv_grayscale_enhance,
    adaptive_threshold, edge_enhance, PRESETS
)

# Basic functions
img = make_test_image()
test("preprocess_for_ocr returns Image", isinstance(preprocess_for_ocr(img), Image.Image))
test("preprocess_for_ocr RGB mode", preprocess_for_ocr(img).mode == "RGB")

# RGB filters
result = rgb_auto_contrast(img)
test("rgb_auto_contrast returns Image", isinstance(result, Image.Image))
test("rgb_auto_contrast size preserved", result.size == img.size)

result = rgb_channel_boost(img, r=1.5, g=1.0, b=1.0)
test("rgb_channel_boost returns Image", isinstance(result, Image.Image))

result = rgb_invert(img)
test("rgb_invert returns Image", isinstance(result, Image.Image))
# Check that white becomes black
arr = np.array(result)
test("rgb_invert white->black", arr[0, 0, 0] < 10)

# HSV filters
result = hsv_enhance_text(img)
test("hsv_enhance_text returns Image", isinstance(result, Image.Image))

result = hsv_isolate_color(img, hue_center=120, hue_range=30)
test("hsv_isolate_color returns Image", isinstance(result, Image.Image))
test("hsv_isolate_color binary", len(np.unique(np.array(result))) <= 3)

result = hsv_grayscale_enhance(img)
test("hsv_grayscale_enhance returns Image", isinstance(result, Image.Image))

# Adaptive threshold
result = adaptive_threshold(img)
test("adaptive_threshold returns Image", isinstance(result, Image.Image))
arr = np.array(result)
unique = np.unique(arr)
test("adaptive_threshold binary values", all(v in [0, 255] for v in unique))

# Edge enhance
result = edge_enhance(img)
test("edge_enhance returns Image", isinstance(result, Image.Image))

# Presets
test("PRESETS has auto", "auto" in PRESETS)
test("PRESETS has dark_text", "dark_text" in PRESETS)
test("PRESETS has light_text", "light_text" in PRESETS)
test("PRESETS has green_bg", "green_bg" in PRESETS)
test("PRESETS has manga", "manga" in PRESETS)
test("PRESETS has game_ui", "game_ui" in PRESETS)

# Auto preprocess
dark_img = make_dark_image()
result = preprocess_for_ocr(dark_img, preset="auto")
test("auto dark image returns Image", isinstance(result, Image.Image))

light_img = make_light_image()
result = preprocess_for_ocr(light_img, preset="auto")
test("auto light image returns Image", isinstance(result, Image.Image))

green_img = make_green_image()
result = preprocess_for_ocr(green_img, preset="auto")
test("auto green image returns Image", isinstance(result, Image.Image))

# None preset
result = preprocess_for_ocr(img, preset="none")
test("none preset returns original", result.size == img.size)

# Specific presets
result = preprocess_for_ocr(img, preset="dark_text")
test("dark_text preset returns Image", isinstance(result, Image.Image))

result = preprocess_for_ocr(img, preset="light_text")
test("light_text preset returns Image", isinstance(result, Image.Image))

result = preprocess_for_ocr(img, preset="green_bg")
test("green_bg preset returns Image", isinstance(result, Image.Image))

result = preprocess_for_ocr(img, preset="manga")
test("manga preset returns Image", isinstance(result, Image.Image))

result = preprocess_for_ocr(img, preset="game_ui")
test("game_ui preset returns Image", isinstance(result, Image.Image))


# ═══════════════════════════════════════════════════════════════
# 2. VOICE CHANGER
# ═══════════════════════════════════════════════════════════════
print("\n=== VOICE CHANGER ===")

from voice_changer import (
    transform_audio, PRESETS as VC_PRESETS, get_preset, get_all_presets,
    get_presets_by_category, VoicePreset
)

# Generate test WAV
import wave
import io

def make_test_wav(duration=0.5, sr=24000, freq=440):
    t = np.linspace(0, duration, int(sr * duration))
    audio = np.sin(2 * np.pi * freq * t).astype(np.float32)
    audio_int16 = (audio * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(audio_int16.tobytes())
    return buf.getvalue()

test_wav = make_test_wav()
test("test WAV generated", len(test_wav) > 0)

# Transform with each preset
for name in VC_PRESETS:
    result = transform_audio(test_wav, name)
    test(f"transform '{name}' returns bytes", isinstance(result, bytes) and len(result) > 0)

# Original preset returns unchanged
result = transform_audio(test_wav, "original")
test("original preset unchanged", result == test_wav)

# Unknown preset returns unchanged
result = transform_audio(test_wav, "nonexistent")
test("unknown preset returns original", result == test_wav)

# get_preset
preset = get_preset("female")
test("get_preset female", preset is not None and preset.name == "female")

preset = get_preset("nonexistent")
test("get_preset unknown returns None", preset is None)

# get_all_presets
all_presets = get_all_presets()
test("get_all_presets returns dict", isinstance(all_presets, dict))
test("get_all_presets has 16+ presets", len(all_presets) >= 16)

# get_presets_by_category
cats = get_presets_by_category()
test("get_presets_by_category returns dict", isinstance(cats, dict))
test("has category Мужские", "Мужские" in cats)
test("has category Женские", "Женские" in cats)
test("has category Детские" in cats, "Детские" in cats)
test("has category Эффекты" in cats, "Эффекты" in cats)

# VoicePreset attributes
p = VC_PRESETS["robot"]
test("VoicePreset has name", p.name == "robot")
test("VoicePreset has label", p.label == "Робот")


# ═══════════════════════════════════════════════════════════════
# 3. SETTINGS
# ═══════════════════════════════════════════════════════════════
print("\n=== SETTINGS ===")

from settings import Settings

s = Settings()
test("Settings loads", s is not None)
test("Settings has config", isinstance(s.config, dict))
test("ocr.image_preset exists", s.get("ocr.image_preset") is not None)
test("ocr.contrast exists", s.get("ocr.contrast") is not None)
test("ocr.brightness exists", s.get("ocr.brightness") is not None)

# Set/get
s.set("ocr.image_preset", "dark_text")
test("Settings set/get", s.get("ocr.image_preset") == "dark_text")
s.set("ocr.image_preset", "auto")  # restore


# ═══════════════════════════════════════════════════════════════
# 4. OCR WRAPPER (integration)
# ═══════════════════════════════════════════════════════════════
print("\n=== OCR WRAPPER INTEGRATION ===")

from ocr_wrapper import _preprocess_image

img = make_test_image()
result = _preprocess_image(img, s.config)
test("_preprocess_image returns Image", isinstance(result, Image.Image))
test("_preprocess_image RGB mode", result.mode == "RGB")

# With different presets
for preset in ["auto", "none", "dark_text", "light_text", "green_bg", "manga", "game_ui"]:
    s.set("ocr.image_preset", preset)
    result = _preprocess_image(img, s.config)
    test(f"_preprocess_image preset '{preset}' works", isinstance(result, Image.Image))
s.set("ocr.image_preset", "auto")


# ═══════════════════════════════════════════════════════════════
# 5. VOICE PROFILES
# ═══════════════════════════════════════════════════════════════
print("\n=== VOICE PROFILES ===")

from voice_profiles import VoiceDictionary, VoiceRouter, MultiVoiceParser

vd = VoiceDictionary()
test("VoiceDictionary loads", vd is not None)
test("has default profiles", len(vd.get_all_profiles()) >= 6)

# Detect language
parser = MultiVoiceParser(vd)
test("detect Russian", parser.detect_language("Привет мир") == "ru")
test("detect English", parser.detect_language("Hello world") == "en")
test("detect Japanese", parser.detect_language("こんにちは") == "ja")

# VoiceRouter
router = VoiceRouter()
test("VoiceRouter detects ru", router.detect_language("Привет") == "ru")
test("VoiceRouter detects en", router.detect_language("Hello") == "en")
test("VoiceRouter get voice ru", "Dmitry" in router.get_voice_for_lang("ru"))
test("VoiceRouter get voice en", "Guy" in router.get_voice_for_lang("en"))


# ═══════════════════════════════════════════════════════════════
# 6. LOCALIZATION
# ═══════════════════════════════════════════════════════════════
print("\n=== LOCALIZATION ===")

from localization import tr, set_language, get_language

set_language("ru")
test("set_language ru", get_language() == "ru")
test("tr ru works", "Сканировать" in tr("btn_scan_all"))

set_language("en")
test("set_language en", get_language() == "en")
test("tr en works", "Scan" in tr("btn_scan_all"))

# Fallback
test("tr unknown key returns key", tr("nonexistent_key_xyz") == "nonexistent_key_xyz")


# ═══════════════════════════════════════════════════════════════
# 7. CYRILLIC IPA
# ═══════════════════════════════════════════════════════════════
print("\n=== CYRILLIC IPA ===")

from cyrillic_ipa import cyrillic_to_ipa

test("cyrillic_to_ipa exists", callable(cyrillic_to_ipa))
result = cyrillic_to_ipa("привет")
test("cyrillic_to_ipa returns string", isinstance(result, str) and len(result) > 0)
test("cyrillic_to_ipa contains IPA chars", any(c in result for c in "pri"))


# ═══════════════════════════════════════════════════════════════
# SUMMARY
# ═══════════════════════════════════════════════════════════════
print(f"\n{'='*50}")
print(f"RESULTS: {PASS} passed, {FAIL} failed, {PASS+FAIL} total")
print(f"{'='*50}")

sys.exit(0 if FAIL == 0 else 1)
