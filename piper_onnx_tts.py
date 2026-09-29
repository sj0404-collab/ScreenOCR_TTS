"""
Piper ONNX TTS — direct inference.
Uses Cyrillic→IPA mapping for Russian, espeak-ng for English.
"""
import json
import numpy as np
import onnxruntime as ort
import wave
import sys
import os
import subprocess

VOICES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "voices")
ESPEAK_PATH = r"C:\Program Files\eSpeak NG\espeak-ng.exe"


def load_config(config_path):
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


# ---- Russian phonemizer (no espeak-ng) ----

_CYRILLIC_MAP = {
    'а': 'a', 'б': 'b', 'в': 'v', 'г': 'ɡ', 'д': 'd',
    'е': 'je', 'ё': 'jo', 'ж': 'ʒ', 'з': 'z', 'и': 'i',
    'й': 'j', 'к': 'k', 'л': 'l', 'м': 'm', 'н': 'n',
    'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't',
    'у': 'u', 'ф': 'f', 'х': 'x', 'ц': 'ts', 'ч': 'tʃ',
    'ш': 'ʃ', 'щ': 'ʃtʃ', 'ъ': '', 'ы': 'ɨ', 'ь': '',
    'э': 'e', 'ю': 'ju', 'я': 'ja',
}
_AFTER_CONSONANT = {'е': 'e', 'ё': 'o', 'ю': 'u', 'я': 'a'}
_CONSONANTS = set('бвгджзклмнпрстфхцчшщ')


def russian_to_ipa(text):
    result = []
    text_lower = text.lower()
    for i, ch in enumerate(text_lower):
        if ch in _CYRILLIC_MAP:
            prev = text_lower[i - 1] if i > 0 else ''
            if ch in _AFTER_CONSONANT and prev in _CONSONANTS:
                result.append(_AFTER_CONSONANT[ch])
            else:
                result.append(_CYRILLIC_MAP[ch])
        elif ch.isalpha():
            result.append(ch)
        elif ch in '.,!?;:-':
            result.append(ch)
        elif ch == ' ':
            result.append(' ')
    return ''.join(result)


# ---- English phonemizer (espeak-ng) ----

def english_to_ipa(text):
    result = subprocess.run(
        [ESPEAK_PATH, "-v", "en-us", "--ipa=0", "-q", text],
        capture_output=True, timeout=10
    )
    return result.stdout.decode("utf-8", errors="replace").strip()


def text_to_ipa(text, lang):
    if lang == "ru":
        return russian_to_ipa(text)
    return english_to_ipa(text)


def ipa_to_piper_ids(ipa_str, phoneme_id_map):
    """Map IPA characters to piper phoneme IDs."""
    ids = []
    if "^" in phoneme_id_map:
        ids.extend(phoneme_id_map["^"])

    i = 0
    while i < len(ipa_str):
        ch = ipa_str[i]
        # Try multi-char matches first (longest match)
        matched = False
        for length in range(min(6, len(ipa_str) - i), 0, -1):
            substr = ipa_str[i:i + length]
            if substr in phoneme_id_map:
                ids.extend(phoneme_id_map[substr])
                i += length
                matched = True
                break
        if not matched:
            i += 1

    if "$" in phoneme_id_map:
        ids.extend(phoneme_id_map["$"])

    return ids


def synthesize(text, model_path, config_path, output_path, lang="ru"):
    config = load_config(config_path)
    sr = config["audio"]["sample_rate"]
    inf = config.get("inference", {})
    noise_scale = inf.get("noise_scale", 0.667)
    length_scale = inf.get("length_scale", 1.0)
    noise_w = inf.get("noise_w", 0.8)
    phoneme_id_map = config.get("phoneme_id_map", {})

    print(f"[TTS] Voice: {config.get('espeak', {}).get('voice', '?')}, sr={sr}")

    ipa_str = text_to_ipa(text, lang)
    phoneme_ids = ipa_to_piper_ids(ipa_str, phoneme_id_map)

    if not phoneme_ids or phoneme_ids == [0]:
        print("[TTS] ERROR: No valid phoneme IDs")
        return
    print(f"[TTS] IPA: {ipa_str[:80]}")
    print(f"[TTS] IDs ({len(phoneme_ids)}): {phoneme_ids[:30]}...")

    phoneme_array = np.array([phoneme_ids], dtype=np.int64)
    phoneme_len = np.array([len(phoneme_ids)], dtype=np.int64)
    scales = np.array([noise_scale, length_scale, noise_w], dtype=np.float32)

    print(f"[TTS] Loading model...")
    session = ort.InferenceSession(model_path)

    outputs = session.run(None, {
        "input": phoneme_array,
        "input_lengths": phoneme_len,
        "scales": scales,
    })
    audio = outputs[0].flatten().astype(np.float32)

    peak = np.abs(audio).max()
    if peak > 0:
        audio = audio / peak * 0.95

    audio_int16 = (audio * 32767).astype(np.int16)
    with wave.open(output_path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(audio_int16.tobytes())

    duration = len(audio) / sr
    print(f"[TTS] Saved: {output_path} ({duration:.1f}s)")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python piper_onnx_tts.py \"text\" [voice] [output.wav]")
        print("  voice: ru-irina, ru-dmitri, en-amy, en-ryan")
        sys.exit(1)

    text = sys.argv[1]
    voice = sys.argv[2] if len(sys.argv) > 2 else "ru-irina"
    output = sys.argv[3] if len(sys.argv) > 3 else r"C:\Temp\opencode\tts_output.wav"

    voice_map = {
        "ru-irina": ("ru_RU-irina-medium.onnx", "ru_RU-irina-medium.onnx.json", "ru"),
        "ru-dmitri": ("ru_RU-dmitri-medium.onnx", "ru_RU-dmitri-medium.onnx.json", "ru"),
        "en-amy": ("en_US-amy-medium.onnx", "en_US-amy-medium.onnx.json", "en"),
        "en-ryan": ("en_US-ryan-medium.onnx", "en_US-ryan-medium.onnx.json", "en"),
    }

    if voice not in voice_map:
        print(f"Unknown voice: {voice}. Available: {', '.join(voice_map.keys())}")
        sys.exit(1)

    model_file, config_file, lang = voice_map[voice]
    model_path = os.path.join(VOICES_DIR, model_file)
    config_path = os.path.join(VOICES_DIR, config_file)

    if not os.path.exists(model_path):
        print(f"Model not found: {model_path}")
        sys.exit(1)

    synthesize(text, model_path, config_path, output, lang)
