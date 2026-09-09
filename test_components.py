# -*- coding: utf-8 -*-
"""Comprehensive test of all v1.0 components."""
import asyncio
import logging
import os
import sys
import time

logging.basicConfig(level=logging.INFO, format="%(name)s - %(message)s")
logger = logging.getLogger("test_all")

RESULTS = []

def ok(name, detail=""):
    RESULTS.append(("OK", name, detail))
    print(f"  OK  {name}" + (f" — {detail}" if detail else ""))

def fail(name, detail=""):
    RESULTS.append(("FAIL", name, detail))
    print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))

def section(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


# =====================================================================
#  1. TTS ENGINES
# =====================================================================
section("1. TTS ENGINES")

try:
    from tts_engine import TTSEngine
    from settings import Settings
    s = Settings()
    tts = TTSEngine(s)
    ok("TTSEngine init", f"voice={tts.voice}, type={tts.voice_type}")
except Exception as e:
    fail("TTSEngine init", str(e))
    tts = None

# Edge voices
try:
    edge_voices = [v for v in tts.EDGE_VOICES if v.get("type") == "edge"]
    ok("Edge-TTS voices", f"{len(edge_voices)} loaded")
except Exception as e:
    fail("Edge-TTS voices", str(e))

# RHVoice
try:
    rhv = [v for v in tts.EDGE_VOICES if v.get("type") == "rhvoice"]
    ok("RHVoice voices", f"{len(rhv)} available")
    for v in rhv:
        print(f"       {v['code']} — {v['name']}")
except Exception as e:
    fail("RHVoice voices", str(e))

# Silero
try:
    sil = [v for v in tts.EDGE_VOICES if v.get("type") == "silero"]
    ok("Silero voices", f"{len(sil)} available")
    for v in sil:
        print(f"       {v['code']} — {v['name']}")
except Exception as e:
    fail("Silero voices", str(e))

# Persona
try:
    pers = [v for v in tts.EDGE_VOICES if v.get("type") == "persona"]
    ok("Persona voices", f"{len(pers)} available")
except Exception as e:
    fail("Persona voices", str(e))


# =====================================================================
#  2. EDGE-TTS SYNTHESIS
# =====================================================================
section("2. EDGE-TTS SYNTHESIS")

async def test_edge_synth():
    try:
        t0 = time.time()
        data = await tts.generate_audio("Привет! Это тест озвучки перевода игры.")
        elapsed = time.time() - t0
        if data and len(data) > 100:
            ok("Edge-TTS synthesis", f"{len(data)} bytes, {elapsed:.1f}s")
        else:
            fail("Edge-TTS synthesis", f"Too small: {len(data) if data else 0} bytes")
    except Exception as e:
        fail("Edge-TTS synthesis", str(e))

asyncio.run(test_edge_synth())


# =====================================================================
#  3. RHVOICE SYNTHESIS
# =====================================================================
section("3. RHVOICE SYNTHESIS")

async def test_rhvoice():
    try:
        original_voice = tts.voice
        original_type = tts.voice_type
        tts.set_voice("rhvoice:test")
        t0 = time.time()
        data = await tts.generate_audio("Тест RHVoice озвучки.")
        elapsed = time.time() - t0
        tts.set_voice(original_voice)
        if data and len(data) > 100:
            ok("RHVoice synthesis", f"{len(data)} bytes, {elapsed:.1f}s")
        else:
            fail("RHVoice synthesis", f"Empty result: {data}")
    except Exception as e:
        fail("RHVoice synthesis", str(e))

asyncio.run(test_rhvoice())


# =====================================================================
#  4. MMS OFFLINE TTS (from game_voice_translator)
# =====================================================================
section("4. MMS OFFLINE TTS")

try:
    import torch
    ok("torch import", f"cuda={torch.cuda.is_available()}")
except ImportError:
    fail("torch import", "NOT INSTALLED")

try:
    from transformers import VitsModel, AutoTokenizer
    ok("transformers import", "VitsModel, AutoTokenizer available")
except ImportError:
    fail("transformers import", "NOT INSTALLED")

if "torch" in sys.modules and "transformers" in sys.modules:
    try:
        MODEL_ID = "facebook/mms-tts-rus"
        MODELS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
        os.makedirs(MODELS_DIR, exist_ok=True)

        t0 = time.time()
        tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, cache_dir=MODELS_DIR)
        ok("MMS tokenizer load", f"{time.time()-t0:.1f}s")

        t0 = time.time()
        model = VitsModel.from_pretrained(MODEL_ID, cache_dir=MODELS_DIR)
        ok("MMS model load", f"{time.time()-t0:.1f}s, sr={model.config.sampling_rate}")

        t0 = time.time()
        inputs = tokenizer("Привет, это тест офлайн озвучки.", return_tensors="pt")
        with torch.no_grad():
            wf = model(**inputs).waveform.squeeze().cpu().numpy()
        elapsed = time.time() - t0
        dur = len(wf) / model.config.sampling_rate
        ok("MMS synthesis", f"{dur:.1f}s audio in {elapsed:.1f}s (realtime x{dur/elapsed:.1f})")
    except Exception as e:
        fail("MMS offline TTS", str(e))
else:
    fail("MMS offline TTS", "Dependencies not installed")


# =====================================================================
#  5. TRANSLATOR
# =====================================================================
section("5. TRANSLATOR")

try:
    from translator import Translator
    tr = Translator()
    t0 = time.time()
    result = tr.translate("Hello, how are you?", src="en", dst="ru")
    elapsed = time.time() - t0
    if result and not result.startswith("[Translation"):
        ok("Google Translate", f"'{result}' ({elapsed:.1f}s)")
    else:
        fail("Google Translate", f"Bad result: {result}")
except Exception as e:
    fail("Google Translate", str(e))

# Offline translate
try:
    from offline_dict import OfflineTranslator
    off = OfflineTranslator()
    t0 = time.time()
    result = off.translate("hello")
    elapsed = time.time() - t0
    ok("Offline dict translate", f"'{result}' ({elapsed:.1f}s)")
except Exception as e:
    fail("Offline dict translate", str(e))


# =====================================================================
#  6. OCR
# =====================================================================
section("6. OCR (EasyOCR)")

try:
    from ocr_wrapper import OCRWrapper
    ocr = OCRWrapper(s)
    ok("OCRWrapper init", f"type={ocr.engine_type}")
except Exception as e:
    fail("OCRWrapper init", str(e))


# =====================================================================
#  7. VOICE CHANGER
# =====================================================================
section("7. VOICE CHANGER")

try:
    from voice_changer import VoiceChanger
    vc = VoiceChanger()
    presets = list(vc.PRESETS.keys()) if hasattr(vc, 'PRESETS') else []
    ok("VoiceChanger init", f"{len(presets)} presets: {presets[:5]}...")
except Exception as e:
    fail("VoiceChanger init", str(e))


# =====================================================================
#  8. IMAGE PREPROCESSOR
# =====================================================================
section("8. IMAGE PREPROCESSOR")

try:
    from image_preprocessor import preprocess_for_ocr, PRESETS
    from PIL import Image
    img = Image.new("RGB", (200, 100), (255, 255, 255))
    result = preprocess_for_ocr(img, preset="auto")
    ok("Image preprocessor", f"{len(PRESETS)} presets: {list(PRESETS.keys())}")
except Exception as e:
    fail("Image preprocessor", str(e))


# =====================================================================
#  9. AVATAR + EMOTION RULES
# =====================================================================
section("9. AVATAR + EMOTION RULES")

try:
    from emotion_rules import detect_emotion
    tests = [
        ("Привет! Как дела?", "happy"),
        ("Я злой идиот!", "angry"),
        ("Спокойной ночи", "sleeping"),
        ("Хм, нужно подумать", "thinking"),
        ("Ого, это невозможно!", "surprised"),
        ("Я рад! Супер!", "happy"),
        ("Грустно очень", "sad"),
        ("", "neutral"),
    ]
    passed = 0
    for text, expected in tests:
        result = detect_emotion(text)
        match = result == expected
        if match:
            passed += 1
        else:
            print(f"       WARN: '{text}' → {result} (expected {expected})")
    ok("Emotion rules", f"{passed}/{len(tests)} correct")
except Exception as e:
    fail("Emotion rules", str(e))

try:
    import pollinations_avatar as pa
    ok("Pollinations avatar", f"{len(pa.PRESET_CHARACTERS)} presets, {len(pa.EMOTIONS)} emotions")
except Exception as e:
    fail("Pollinations avatar", str(e))


# =====================================================================
#  10. PROCESS UTILS
# =====================================================================
section("10. PROCESS UTILS")

try:
    import process_utils
    procs = process_utils.list_processes()
    ok("List processes", f"{len(procs)} found")
    info = process_utils.process_info(4)
    ok("Process info", f"PID 4 = {info['name']}")
except Exception as e:
    fail("Process utils", str(e))


# =====================================================================
#  11. GAME AUDIO CAPTURE
# =====================================================================
section("11. GAME AUDIO CAPTURE")

try:
    from game_audio_capture import GameAudioCapture
    ok("GameAudioCapture import", "OK")
except Exception as e:
    fail("GameAudioCapture import", str(e))


# =====================================================================
#  12. VOICE TRANSLATOR
# =====================================================================
section("12. VOICE TRANSLATOR")

try:
    from voice_translator import VoiceTranslator
    ok("VoiceTranslator import", "OK")
except Exception as e:
    fail("VoiceTranslator import", str(e))


# =====================================================================
#  SUMMARY
# =====================================================================
print(f"\n{'='*60}")
ok_count = sum(1 for r in RESULTS if r[0] == "OK")
fail_count = sum(1 for r in RESULTS if r[0] == "FAIL")
print(f"  RESULTS: {ok_count} OK, {fail_count} FAIL / {len(RESULTS)} total")
print(f"{'='*60}")
if fail_count:
    print("\n  FAILED:")
    for status, name, detail in RESULTS:
        if status == "FAIL":
            print(f"    - {name}: {detail}")
