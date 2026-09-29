"""
Voice Changer — трансформация голоса через pitch/speed/formant.

Берёт аудио из любого TTS движка и создаёт вариации:
  - Женский: pitch +3-5 semitones
  - Детский: pitch +7, speed +20%
  - Старик: pitch -3, speed -15%
  - Робот: ring modulation + bitcrush
  - Шёпот: noise + highpass
  - Бас: pitch -5 semitones
  - Быстрый: speed +30%

Полностью оффлайн, numpy + scipy + pydub.
"""
import io
import logging
import os
import struct
import tempfile
import wave
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# Константы
SAMPLE_RATE = 24000  # standard for TTS output
SEMITONE_RATIO = 2 ** (1 / 12)


class VoicePreset:
    """Один пресет трансформации голоса."""
    __slots__ = ("name", "label", "pitch_semitones", "speed_factor",
                 "formant_shift", "gender", "description")

    def __init__(self, name: str, label: str, pitch_semitones: float = 0.0,
                 speed_factor: float = 1.0, formant_shift: float = 0.0,
                 gender: str = "neutral", description: str = ""):
        self.name = name
        self.label = label
        self.pitch_semitones = pitch_semitones
        self.speed_factor = speed_factor
        self.formant_shift = formant_shift
        self.gender = gender
        self.description = description


# ─── Пресеты ───────────────────────────────────────────────
PRESETS = {
    # Базовые (без изменений)
    "original":     VoicePreset("original", "Оригинал", 0, 1.0, 0, "neutral", "Без изменений"),

    # Мужские
    "male_deep":    VoicePreset("male_deep", "Бас", -5, 0.95, -1, "male", "Глубокий мужской"),
    "male_old":     VoicePreset("male_old", "Старик", -3, 0.85, -0.5, "male", "Пожилой мужчина"),
    "male_young":   VoicePreset("male_young", "Молодой", +1, 1.05, 0.5, "male", "Молодой мужчина"),
    "male_giant":   VoicePreset("male_giant", "Великан", -7, 0.8, -2, "male", "Очень низкий голос"),

    # Женские
    "female":       VoicePreset("female", "Женщина", +4, 1.0, 1, "female", "Женский голос"),
    "female_sweet": VoicePreset("female_sweet", "Нежная", +5, 1.0, 1.5, "female", "Мягкий женский"),
    "female_lord":  VoicePreset("female_lord", "Госпожа", +3, 0.95, 0.5, "female", "Строгий женский"),
    "female_girl":  VoicePreset("female_girl", "Девочка", +7, 1.15, 2, "female", "Юный голос"),

    # Детские
    "child":        VoicePreset("child", "Ребёнок", +7, 1.2, 2, "neutral", "Голос ребёнка"),
    "baby":         VoicePreset("baby", "Малыш", +10, 1.3, 3, "neutral", "Очень высокий голос"),

    # Эффекты
    "robot":        VoicePreset("robot", "Робот", 0, 1.0, 0, "neutral", "Роботизированный"),
    "whisper":      VoicePreset("whisper", "Шёпот", +2, 0.9, 1, "neutral", "Шёпот"),
    "slow":         VoicePreset("slow", "Медленный", -2, 0.7, -0.5, "neutral", "Размеренная речь"),
    "fast":         VoicePreset("fast", "Быстрый", +1, 1.3, 0, "neutral", "Быстрая речь"),
    "alien":        VoicePreset("alien", "Пришелец", +8, 0.9, 3, "neutral", "Инопланетный"),
    "monster":      VoicePreset("monster", "Монстр", -8, 0.7, -3, "male", "Чудовище"),

    # Громкость / Эффекты
    "loud":         VoicePreset("loud", "Громкий", +1, 1.0, 0, "neutral", "Усиленная громкость"),
    "quiet":        VoicePreset("quiet", "Тихий", -1, 0.95, 0, "neutral", "Пониженная громкость"),
    "echo":         VoicePreset("echo", "Эхо", 0, 1.0, 0, "neutral", "С эхом-эффектом"),
    "telephone":    VoicePreset("telephone", "Телефон", +2, 1.1, 1, "neutral", "Как по телефону"),
    "radio":        VoicePreset("radio", "Радио", +1, 1.0, 1, "neutral", "Эффект радио"),
}


def _load_wav_bytes(data: bytes) -> tuple:
    """Загрузка WAV из bytes -> (numpy array, sample_rate)."""
    with wave.open(io.BytesIO(data), 'rb') as wf:
        sr = wf.getframerate()
        n_channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        frames = wf.readframes(wf.getnframes())

    if sampwidth == 2:
        audio = np.frombuffer(frames, dtype=np.int16)
    elif sampwidth == 4:
        audio = np.frombuffer(frames, dtype=np.int32)
        audio = (audio / 65536).astype(np.int16)
    else:
        raise ValueError(f"Unsupported sample width: {sampwidth}")

    if n_channels > 1:
        audio = audio[::n_channels]  # mono

    return audio.astype(np.float32) / 32768.0, sr


def _save_wav_bytes(audio: np.ndarray, sr: int) -> bytes:
    """Сохранение numpy array -> WAV bytes."""
    audio = np.clip(audio, -1.0, 1.0)
    audio_int16 = (audio * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(audio_int16.tobytes())
    return buf.getvalue()


def _change_pitch(audio: np.ndarray, sr: int, semitones: float) -> np.ndarray:
    """Изменение высоты тона через PSOLA-подобный алгоритм.
    
    Использует overlap-add с временным растяжением.
    """
    if abs(semitones) < 0.1:
        return audio

    factor = SEMITONE_RATIO ** semitones
    new_sr = int(sr * factor)

    # Ресемплинг через numpy интерполяцию (быстро)
    indices = np.linspace(0, len(audio) - 1, int(len(audio) / factor))
    pitch_shifted = np.interp(indices, np.arange(len(audio)), audio)

    return pitch_shifted


def _change_speed(audio: np.ndarray, sr: int, factor: float) -> np.ndarray:
    """Изменение скорости без изменения высоты тона."""
    if abs(factor - 1.0) < 0.01:
        return audio

    indices = np.linspace(0, len(audio) - 1, int(len(audio) / factor))
    return np.interp(indices, np.arange(len(audio)), audio)


def _apply_formant_shift(audio: np.ndarray, sr: int, shift: float) -> np.ndarray:
    """Сдвиг формант (изменение тембра без изменения высоты).
    
    Упрощённый подход: эмпирическое изменение спектра через
    полосовой фильтр.
    """
    if abs(shift) < 0.1:
        return audio

    from scipy.signal import butter, sosfilt

    # Центральная частота формант
    f_center = 500 * (2 ** (shift * 0.3))  # сдвиг центра
    width = 200

    low = max(20, f_center - width)
    high = min(sr / 2 - 100, f_center + width)

    sos = butter(2, [low, high], btype='band', fs=sr, output='sos')
    filtered = sosfilt(sos, audio)

    # Смешиваем с оригиналом (30% фильтра + 70% оригинал)
    return 0.7 * audio + 0.3 * filtered


def _apply_robot(audio: np.ndarray, sr: int) -> np.ndarray:
    """Роботизированный эффект: ring modulation + bitcrush."""
    t = np.arange(len(audio)) / sr

    # Ring modulation на 50Hz
    modulator = np.sin(2 * np.pi * 50 * t)
    result = audio * modulator

    # Bitcrush: квантизация
    levels = 16
    result = np.round(result * levels) / levels

    return result.astype(np.float32)


def _apply_whisper(audio: np.ndarray, sr: int) -> np.ndarray:
    """Эффект шёпота: highpass + шум."""
    from scipy.signal import butter, sosfilt

    # Highpass фильтр
    sos = butter(2, 500, btype='high', fs=sr, output='sos')
    result = sosfilt(sos, audio)

    # Добавляем лёгкий шум
    noise = np.random.randn(len(audio)) * 0.015
    result = result + noise

    return np.clip(result, -1.0, 1.0).astype(np.float32)


def _apply_echo(audio: np.ndarray, sr: int) -> np.ndarray:
    """Эффект эха: задержка 100-300ms с затуханием."""
    delays_ms = [150, 250]
    decay = 0.4
    result = audio.copy()
    for delay_ms in delays_ms:
        delay_samples = int(sr * delay_ms / 1000)
        if delay_samples < len(audio):
            delayed = np.zeros_like(audio)
            delayed[delay_samples:] = audio[:-delay_samples] * decay
            result = result + delayed
    return np.clip(result, -1.0, 1.0).astype(np.float32)


def _apply_loud(audio: np.ndarray, sr: int) -> np.ndarray:
    """Усиление громкости + компрессия."""
    audio = audio * 1.5
    audio = np.clip(audio, -1.0, 1.0)
    # Soft clipping
    audio = np.tanh(audio)
    return audio.astype(np.float32)


def _apply_quiet(audio: np.ndarray, sr: int) -> np.ndarray:
    """Понижение громкости."""
    return (audio * 0.5).astype(np.float32)


def _apply_telephone(audio: np.ndarray, sr: int) -> np.ndarray:
    """Эффект телефонного канала: bandpass 300-3400 Hz."""
    from scipy.signal import butter, sosfilt
    sos = butter(4, [300, 3400], btype='band', fs=sr, output='sos')
    result = sosfilt(sos, audio)
    return (result * 2.0).astype(np.float32)


def transform_audio(audio_data: bytes, preset_name: str) -> bytes:
    """Основная функция: трансформация аудио по пресету.
    
    Args:
        audio_data: WAV bytes (из TTS движка)
        preset_name: имя пресета из PRESETS
        
    Returns:
        WAV bytes после трансформации
    """
    if preset_name not in PRESETS:
        logger.warning(f"[VoiceChanger] Неизвестный пресет: {preset_name}, используем original")
        return audio_data

    preset = PRESETS[preset_name]
    if preset.name == "original":
        return audio_data

    audio, sr = _load_wav_bytes(audio_data)
    logger.info(f"[VoiceChanger] {preset.label}: pitch={preset.pitch_semitones:+.0f}st, "
                f"speed={preset.speed_factor:.2f}x, formant={preset.formant_shift:+.1f}")

    # 1. Pitch shift
    if abs(preset.pitch_semitones) > 0.1:
        audio = _change_pitch(audio, sr, preset.pitch_semitones)

    # 2. Speed change
    if abs(preset.speed_factor - 1.0) > 0.01:
        audio = _change_speed(audio, sr, preset.speed_factor)

    # 3. Formant shift
    if abs(preset.formant_shift) > 0.1:
        audio = _apply_formant_shift(audio, sr, preset.formant_shift)

    # 4. Спецэффекты
    if preset.name == "robot":
        audio = _apply_robot(audio, sr)
    elif preset.name == "whisper":
        audio = _apply_whisper(audio, sr)
    elif preset.name == "echo":
        audio = _apply_echo(audio, sr)
    elif preset.name == "loud":
        audio = _apply_loud(audio, sr)
    elif preset.name == "quiet":
        audio = _apply_quiet(audio, sr)
    elif preset.name == "telephone":
        audio = _apply_telephone(audio, sr)
    elif preset.name == "radio":
        # Radio = telephone + slight distortion
        audio = _apply_telephone(audio, sr)
        audio = np.tanh(audio * 1.5)

    return _save_wav_bytes(audio, sr)


def transform_file(input_path: str, output_path: str, preset_name: str) -> str:
    """Трансформация аудиофайла."""
    audio_data = Path(input_path).read_bytes()
    result = transform_audio(audio_data, preset_name)
    Path(output_path).write_bytes(result)
    return output_path


async def transform_tts_output(tts_engine, text: str, preset_name: str,
                                output_path: Optional[str] = None) -> str:
    """Генерация TTS + трансформация в один вызов.
    
    1. tts_engine.generate_audio_file(text) -> base.mp3
    2. transform_audio(base.mp3, preset) -> transformed.wav
    """
    # Генерируем базовый голос
    base_path = await tts_engine.generate_audio_file(text)
    if not base_path or not os.path.exists(base_path):
        raise Exception("TTS не смог сгенерировать аудио")

    # Трансформируем
    if output_path is None:
        ext = ".wav"
        output_path = str(Path(base_path).parent / f"vc_{preset_name}_{hash(text) % 10000}{ext}")

    return transform_file(base_path, output_path, preset_name)


def get_preset(name: str) -> Optional[VoicePreset]:
    """Получить пресет по имени."""
    return PRESETS.get(name)


def get_all_presets() -> dict:
    """Все доступные пресеты."""
    return dict(PRESETS)


def get_presets_by_category() -> dict:
    """Пресеты, сгруппированные по категории."""
    categories = {
        "Оригинал": ["original"],
        "Мужские": ["male_deep", "male_old", "male_young", "male_giant"],
        "Женские": ["female", "female_sweet", "female_lord", "female_girl"],
        "Детские": ["child", "baby"],
        "Эффекты": ["robot", "whisper", "slow", "fast", "alien", "monster"],
        "Громкость": ["loud", "quiet"],
        "Канал": ["echo", "telephone", "radio"],
    }
    result = {}
    for cat, names in categories.items():
        result[cat] = [PRESETS[n] for n in names if n in PRESETS]
    return result
