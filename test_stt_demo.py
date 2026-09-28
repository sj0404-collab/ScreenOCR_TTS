# -*- coding: utf-8 -*-
"""
STT Demo — полный пайплайн:
  Микрофон -> faster-whisper (EN) -> Перевод EN->RU -> Edge-TTS (RU голос)

Также: демо перевода на разных скоростях (от простого слова до сложного предложения).
"""
import asyncio
import os
import sys
import time
import threading
import wave
import struct

sys.path.insert(0, os.path.dirname(__file__))

from tts_engine import TTSEngine
from settings import Settings

# ═══════════════════════════════════════════════════════════════
# КОНСТАНТЫ
# ═══════════════════════════════════════════════════════════════

EN_VOICE = "en-US-GuyNeural"       # Английский мужской (STT повтор)
RU_VOICE = "ru-RU-SvetlanaNeural"  # Русский женский (перевод)

# Прогрессия от простого к сложному (en -> ru)
DEMO_PHRASES = [
    # Уровень 1: Одно слово
    ("Hello", "Привет"),
    ("Water", "Вода"),
    ("Fire", "Огонь"),
    ("Sword", "Меч"),
    ("Enemy", "Враг"),

    # Уровень 2: Два-три слова
    ("I am ready", "Я готов"),
    ("Be careful", "Будь осторожен"),
    ("No time", "Нет времени"),
    ("Help me", "Помоги мне"),
    ("Good luck", "Удачи"),

    # Уровень 3: Короткое предложение
    ("The dragon is coming", "Дракон приближается"),
    ("We need to run", "Нам нужно бежать"),
    ("I found a treasure", "Я нашёл сокровище"),
    ("Be quiet, they can hear us", "Тише, они могут нас услышать"),

    # Уровень 4: Среднее предложение
    ("The ancient temple hides a powerful artifact", "Древний храм скрывает могущественный артефакт"),
    ("We must defend the village before sunset", "Мы должны защитить деревню до заката"),

    # Уровень 5: Сложное предложение
    ("The legendary sword of the fallen king can defeat the darkness that threatens our kingdom",
     "Легендарный меч павшего короля способен победить тьму, которая угрожает нашему королевству"),
]


# ═══════════════════════════════════════════════════════════════
# 1. ДЕМО ПЕРЕВОДА НА РАЗНЫХ СКОРОСТЯХ
# ═══════════════════════════════════════════════════════════════

async def demo_speed_progression():
    """Показывает перевод на разных скоростях: от простого слова до сложного предложения.
    
    Каждая фраза:
      1) Английский голос повторяет EN текст (быстро)
      2) Русский голос озвучивает перевод (медленнее для сложных фраз)
    """
    settings = Settings()
    engine = TTSEngine(settings)

    print()
    print("=" * 70)
    print("  STT DEMO: EN речь -> Перевод -> RU голос")
    print("  Скорость: от простого слова до сложного предложения")
    print("=" * 70)

    for i, (en_text, ru_text) in enumerate(DEMO_PHRASES):
        level = 1 if i < 5 else (2 if i < 10 else (3 if i < 14 else (4 if i < 16 else 5)))
        print(f"\n  [{level}] EN: {en_text}")
        print(f"       RU: {ru_text}")

        # Английский голос (STT повтор) — быстрый
        engine.set_voice(EN_VOICE)
        engine.rate = 0
        engine.pitch = 0

        t0 = time.perf_counter()
        print(f"       ... EN voice ({EN_VOICE})")
        await engine.speak(en_text)
        t_en = (time.perf_counter() - t0) * 1000

        # Русский голос (перевод) — медленнее для длинных фраз
        engine.set_voice(RU_VOICE)
        # Чем длиннее фраза — тем медленнее (для лучшего восприятия)
        ru_rate = max(-30, -5 * level)
        engine.rate = ru_rate

        t1 = time.perf_counter()
        print(f"       ... RU voice ({RU_VOICE}, rate={ru_rate}%)")
        await engine.speak(ru_text)
        t_ru = (time.perf_counter() - t1) * 1000

        total = t_en + t_ru
        print(f"       [OK] EN={t_en:.0f}ms  RU={t_ru:.0f}ms  total={total:.0f}ms")

    # Восстановить дефолт
    engine.set_voice("ru-RU-DmitryNeural")
    engine.rate = 15

    print()
    print("=" * 70)
    print("  ДЕМО ЗАВЕРШЕНО")
    print("=" * 70)


# ═══════════════════════════════════════════════════════════════
# 2. STT: Запись с микрофона -> Whisper -> Перевод -> TTS
# ═══════════════════════════════════════════════════════════════

def record_from_mic(duration_sec=5, sample_rate=16000):
    """Записать аудио с микрофона через wave (без pyaudio)."""
    try:
        import pyaudiowpatch as pyaudio
        pa = pyaudio.PyAudio()
        stream = pa.open(format=pyaudio.paInt16, channels=1,
                         rate=sample_rate, input=True,
                         frames_per_buffer=4096)
        print(f"  [REC] Запись {duration_sec} сек с микрофона...")
        frames = []
        for _ in range(0, sample_rate // 4096 * duration_sec):
            data = stream.read(4096, exception_on_overflow=False)
            frames.append(data)
        stream.stop_stream()
        stream.close()
        pa.terminate()

        # Конвертируем в numpy float32
        audio_bytes = b''.join(frames)
        samples = struct.unpack(f'<{len(audio_bytes)//2}h', audio_bytes)
        audio = np.array(samples, dtype=np.float32) / 32768.0
        print(f"  [REC] Записано: {len(audio)/sample_rate:.1f} сек, {len(audio)} сэмплов")
        return audio, sample_rate
    except Exception as e:
        print(f"  [REC] Ошибка микрофона: {e}")
        print("  [REC] Попробуйте: pip install pyaudiowpatch")
        return None, None


def transcribe_whisper(audio, sr, model_name="tiny", language="en"):
    """Распознать речь через faster-whisper."""
    import numpy as np
    print(f"  [STT] Загрузка whisper model '{model_name}'...")

    from faster_whisper import WhisperModel
    import torch
    device = "cuda" if torch.cuda.is_available() else "cpu"
    compute_type = "float16" if device == "cuda" else "int8"

    model = WhisperModel(model_name, device=device, compute_type=compute_type)
    print(f"  [STT] Модель загружена ({device}/{compute_type})")

    # Ресэмплинг если нужно
    if sr != 16000:
        import scipy.signal
        audio = scipy.signal.resample(audio, int(len(audio) * 16000 / sr))
        sr = 16000

    # Нормализация
    peak = np.abs(audio).max()
    if peak > 0:
        audio = audio / peak * 0.95

    t0 = time.perf_counter()
    segments, info = model.transcribe(
        audio, language=language, beam_size=5,
        word_timestamps=True, vad_filter=True,
    )
    text_parts = []
    for seg in segments:
        text_parts.append(seg.text.strip())
    text = " ".join(text_parts)
    elapsed = (time.perf_counter() - t0) * 1000

    print(f"  [STT] Распознано за {elapsed:.0f}мс (язык={info.language}, вероятность={info.language_probability:.2f})")
    print(f"  [STT] Текст: \"{text}\"")
    return text


def translate_en_ru(text):
    """Перевод EN->RU: сначала offline dict, потом Google Translate fallback."""
    from offline_dict import OfflineTranslator
    offline = OfflineTranslator()

    # Попробовать offline
    ru = offline.translate(text)
    if ru and not offline.has_untranslated(text):
        print(f"  [TR] Offline: \"{ru}\"")
        return ru

    # Fallback: Google Translate
    try:
        import requests
        url = "https://translate.googleapis.com/translate_a/single"
        params = {"client": "gtx", "sl": "en", "tl": "ru", "dt": "t", "q": text}
        r = requests.get(url, params=params, timeout=10)
        if r.status_code == 200:
            data = r.json()
            ru = "".join(part[0] for part in data[0] if part[0])
            print(f"  [TR] Google: \"{ru}\"")
            return ru
    except Exception as e:
        print(f"  [TR] Google failed: {e}")

    return ru or text


async def speak_with_voice(engine, text, voice, rate=0, pitch=0):
    """Озвучить текст заданным голосом."""
    old_voice = engine.voice
    old_type = engine.voice_type
    old_rate = engine.rate

    engine.set_voice(voice)
    engine.rate = rate
    engine.pitch = pitch
    await engine.speak(text)

    engine.voice = old_voice
    engine.voice_type = old_type
    engine.rate = old_rate


async def demo_stt_live():
    """Живое демо: микрофон -> STT -> перевод -> RU голос."""
    settings = Settings()
    engine = TTSEngine(settings)

    print()
    print("=" * 70)
    print("  LIVE STT DEMO: Микрофон -> EN -> Перевод -> RU голос")
    print("=" * 70)
    print(f"  Голос EN: {EN_VOICE}")
    print(f"  Голос RU: {RU_VOICE}")
    print(f"  Whisper model: tiny (CPU)")
    print()

    import numpy as np

    while True:
        print("-" * 50)
        print("  Команды: [Enter] записать 5 сек | [q] выход")
        cmd = input("  > ").strip().lower()
        if cmd == 'q':
            break

        # Запись с микрофона
        audio, sr = record_from_mic(duration_sec=5)
        if audio is None:
            print("  [ERR] Не удалось записать. Проверьте микрофон.")
            continue

        # STT: распознавание
        en_text = transcribe_whisper(audio, sr, model_name="tiny", language="en")
        if not en_text or len(en_text.strip()) < 2:
            print("  [STT] Речь не распознана или слишком короткая.")
            continue

        # Перевод EN -> RU
        ru_text = translate_en_ru(en_text)

        # Озвучка: EN повтор (мужской голос)
        print(f"  [TTS] EN: \"{en_text}\"")
        await speak_with_voice(engine, en_text, EN_VOICE, rate=0)

        # Озвучка: RU перевод (женский голос, медленнее)
        print(f"  [TTS] RU: \"{ru_text}\"")
        await speak_with_voice(engine, ru_text, RU_VOICE, rate=-20)

        print("  [OK] Готово!\n")

    # Восстановить
    engine.set_voice("ru-RU-DmitryNeural")
    engine.rate = 15
    print("\n  Демо завершено.")


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

async def main():
    print("\n  STT + TTS DEMO")
    print("  1) Демо перевода (простые -> сложные фразы, без микрофона)")
    print("  2) Live STT (микрофон -> whisper -> перевод -> голос)")
    print("  3) Оба")

    choice = input("\n  Выбор (1/2/3): ").strip()

    if choice in ("1", "3"):
        await demo_speed_progression()

    if choice in ("2", "3"):
        await demo_stt_live()


if __name__ == "__main__":
    asyncio.run(main())
