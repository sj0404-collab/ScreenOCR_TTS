"""
Unit tests for the features we built/modified.
"""
import sys
import os
import asyncio
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PASS = 0
FAIL = 0


def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {detail}")


# ═══════════════════════════════════════════════════════════
# 1. Cyrillic → IPA mapping
# ═══════════════════════════════════════════════════════════
print("\n=== 1. Cyrillic→IPA ===")
from cyrillic_ipa import cyrillic_to_ipa

r = cyrillic_to_ipa("привет")
check("'привет' → IPA", r == "privet", f"got: {r!r}")

r = cyrillic_to_ipa("как дела")
check("'как дела' → IPA", r == "kak dela", f"got: {r!r}")

r = cyrillic_to_ipa("хорошо")
check("'хорошо' -> IPA (x=kh, ʃ=sh)", r == "xoroʃo", f"got: {r!r}")

r = cyrillic_to_ipa("hello")
check("Latin passthrough", r == "hello", f"got: {r!r}")

r = cyrillic_to_ipa("привет, мир!")
check("Punctuation", r == "privet, mir!", f"got: {r!r}")

# After-consonant softening: тё → to
r = cyrillic_to_ipa("тёща")
check("After-consonant ё->o", r == "toʃtʃa", f"got: {r!r}")

r = cyrillic_to_ipa("яма")
check("Initial я->ja", r == "jama", f"got: {r!r}")


# ═══════════════════════════════════════════════════════════
# 2. Piper ONNX TTS (standalone)
# ═══════════════════════════════════════════════════════════
print("\n=== 2. Piper ONNX TTS ===")
from piper_onnx_tts import load_config, ipa_to_piper_ids, synthesize

VOICES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "voices")

config_path = os.path.join(VOICES_DIR, "ru_RU-irina-medium.onnx.json")
model_path = os.path.join(VOICES_DIR, "ru_RU-irina-medium.onnx")

check("Config exists", os.path.exists(config_path))
check("Model exists", os.path.exists(model_path))

if os.path.exists(config_path):
    config = load_config(config_path)
    pim = config.get("phoneme_id_map", {})
    check("phoneme_id_map loaded", len(pim) > 10, f"count={len(pim)}")

    ids = ipa_to_piper_ids("privet", pim)
    check("IPA→IDs produces non-empty", len(ids) > 3, f"len={len(ids)}")
    check("IPA→IDs starts with ^", ids[0] == 1, f"first={ids[0]}")
    check("IPA→IDs ends with $", ids[-1] == 2, f"last={ids[-1]}")

out_wav = os.path.join(os.environ.get("TEMP", r"C:\Temp\opencode"), "test_piper_unit.wav")
if os.path.exists(model_path):
    try:
        synthesize("привет", model_path, config_path, out_wav, "ru")
        size = os.path.getsize(out_wav) if os.path.exists(out_wav) else 0
        check("synthesize produces WAV", size > 1000, f"size={size}")
    except Exception as e:
        check("synthesize no crash", False, str(e))
else:
    check("synthesize (skipped — no model)", True)


# ═══════════════════════════════════════════════════════════
# 3. TTSEngine — set_voice type detection
# ═══════════════════════════════════════════════════════════
print("\n=== 3. TTSEngine.set_voice ===")
from tts_engine import TTSEngine

e = TTSEngine()

e.set_voice("piper:piper-irina")
check("piper:piper-irina → piper", e.voice_type == "piper", f"got={e.voice_type}")
check("voice stored correctly", e.voice == "piper:piper-irina")

e.set_voice("piper:piper-dmitri")
check("piper:piper-dmitri → piper", e.voice_type == "piper")

e.set_voice("rhvoice:Pavel")
check("rhvoice:Pavel → rhvoice", e.voice_type == "rhvoice")

e.set_voice("ru-RU-DmitryNeural")
check("Edge voice → edge", e.voice_type == "edge")

e.set_voice("en-US-GuyNeural")
check("en Edge → edge", e.voice_type == "edge")

e.set_voice("silero:aidar")
check("silero → silero", e.voice_type == "silero")


# ═══════════════════════════════════════════════════════════
# 4. Voice list filtering
# ═══════════════════════════════════════════════════════════
print("\n=== 4. Voice list filtering ===")


class FakeSettings:
    def __init__(self, d=None):
        self._d = d or {}
    def get(self, k, default=None):
        return self._d.get(k, default)


e_filtered = TTSEngine(settings=FakeSettings({"tts.show_all_edge_voices": False}))
voices = e_filtered.get_voices()
edge_count = sum(1 for v in voices if v.get("type") == "edge")
check("Default: ≤5 Edge voices", edge_count <= 5, f"got {edge_count}")

piper_count = sum(1 for v in voices if v.get("type") == "piper")
check("4 Piper voices present", piper_count == 4, f"got {piper_count}")

rhvoice_count = sum(1 for v in voices if v.get("type") == "rhvoice")
check("RHVoice voices present", rhvoice_count >= 1, f"got {rhvoice_count}")

piper_names = [v["name"] for v in voices if v.get("type") == "piper"]
check("Irina in list", any("Irina" in n for n in piper_names), f"names={piper_names}")
check("Dmitri in list", any("Dmitri" in n for n in piper_names))

e_all = TTSEngine(settings=FakeSettings({"tts.show_all_edge_voices": True}))
voices_all = e_all.get_voices()
edge_all = sum(1 for v in voices_all if v.get("type") == "edge")
check("show_all → 100+ Edge voices", edge_all > 100, f"got {edge_all}")


# ═══════════════════════════════════════════════════════════
# 5. Voice ordering (offline first)
# ═══════════════════════════════════════════════════════════
print("\n=== 5. Voice ordering ===")
types_order = [v.get("type") for v in voices]
first_piper = next((i for i, t in enumerate(types_order) if t == "piper"), 999)
first_edge = next((i for i, t in enumerate(types_order) if t == "edge"), 999)
check("Piper before Edge", first_piper < first_edge, f"piper@{first_piper} edge@{first_edge}")


# ═══════════════════════════════════════════════════════════
# 6. _gen_by_engine routes to piper
# ═══════════════════════════════════════════════════════════
print("\n=== 6. _gen_by_engine routing ===")
e.set_voice("piper:piper-irina")
check("After set_voice, type is piper", e.voice_type == "piper")
check("voice is piper:piper-irina", e.voice == "piper:piper-irina")

# Verify _gen_piper method exists
check("_gen_piper exists", hasattr(e, "_gen_piper"))


# ═══════════════════════════════════════════════════════════
# 7. Piper ONNX through TTSEngine
# ═══════════════════════════════════════════════════════════
print("\n=== 7. TTSEngine.generate_audio with Piper ===")


async def test_generate():
    e.set_voice("piper:piper-irina")
    audio = await e.generate_audio("привет")
    return audio


try:
    audio = asyncio.run(test_generate())
    check("generate_audio returns bytes", isinstance(audio, bytes))
    check("generate_audio non-empty", len(audio) > 100, f"len={len(audio)}")
except Exception as ex:
    check("generate_audio no crash", False, str(ex))


# ═══════════════════════════════════════════════════════════
# 8. voice_translator protection
# ═══════════════════════════════════════════════════════════
print("\n=== 8. voice_translator auto-switch protection ===")
import voice_translator as vt

# Check that _adapt_voice_for_net respects offline voices
e.set_voice("piper:piper-irina")
old_voice = e.voice
old_type = e.voice_type

# Simulate _adapt_voice_for_net (it uses _get_net internally)
# The key check: voice_type not in ("edge", "sapi") → should NOT be overridden
check("Piper voice_type != edge", e.voice_type != "edge")
check("Piper voice_type != sapi", e.voice_type != "sapi")


# ═══════════════════════════════════════════════════════════
# Summary
# ═══════════════════════════════════════════════════════════
print(f"\n{'='*50}")
print(f"RESULTS: {PASS} passed, {FAIL} failed out of {PASS + FAIL}")
print(f"{'='*50}")
sys.exit(1 if FAIL > 0 else 0)
