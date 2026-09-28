# -*- coding: utf-8 -*-
"""
Voice Analyzer — определение характеристик голоса по аудио:
  - Пол: male / female / ambiguous
  - Возраст: child / young / adult / elderly
  - Стиль: shout / whisper / normal / laugh / cry
  - Pitch (F0) в Hz
  - Уверенность (0..1)

Алгоритм:
  1) Pitch detection через autocorrelation (numpy)
  2) Spectral analysis для формант
  3) Energy/RMS для стиля (shout/whisper)
  4) F0 + spectral centroid для пола и возраста

Вход: numpy float32 array (mono, 16kHz)
Выход: VoiceFeatures dataclass
"""
import logging
import dataclasses
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class VoiceFeatures:
    """Результат анализа голоса."""
    gender: str = "unknown"         # "male" / "female" / "child" / "unknown"
    age: str = "unknown"            # "child" / "young" / "adult" / "elderly" / "unknown"
    style: str = "normal"           # "normal" / "shout" / "whisper" / "laugh" / "cry"
    pitch_hz: float = 0.0           # Средний F0 (Hz)
    pitch_confidence: float = 0.0   # Уверенность pitch (0..1)
    energy_db: float = -60.0        # RMS энергия (dB)
    spectral_centroid: float = 0.0  # Спектральный центроид (Hz)
    confidence: float = 0.0         # Общая уверенность (0..1)
    raw_features: dict = dataclasses.field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════
# PITCH DETECTION (Autocorrelation)
# ═══════════════════════════════════════════════════════════════

def _pitch_autocorrelation(audio: np.ndarray, sr: int = 16000,
                           f_min: float = 50.0, f_max: float = 500.0) -> tuple:
    """Pitch detection через autocorrelation.
    
    Возвращает (pitch_hz, confidence).
    """
    if len(audio) < sr * 0.03:  # Меньше 30мс — мало данных
        return 0.0, 0.0

    # Центрируем и нормализуем
    audio = audio - np.mean(audio)
    if np.max(np.abs(audio)) > 0:
        audio = audio / np.max(np.abs(audio))

    # Autocorrelation
    n = len(audio)
    corr = np.correlate(audio, audio, mode='full')[n - 1:]

    # Нормализуем
    if corr[0] > 0:
        corr = corr / corr[0]

    # Поиск пиков в диапазоне [f_min, f_max]
    min_lag = int(sr / f_max)
    max_lag = int(sr / f_min)

    if max_lag >= len(corr):
        return 0.0, 0.0

    search = corr[min_lag:max_lag + 1]
    if len(search) == 0:
        return 0.0, 0.0

    # Находим первый пик (основная частота)
    peak_idx = np.argmax(search)
    peak_val = search[peak_idx]

    if peak_val < 0.3:  # Слишком слабый пик — нет чёткого pitch
        return 0.0, float(peak_val)

    pitch_hz = sr / (peak_idx + min_lag)
    confidence = float(peak_val)

    return pitch_hz, confidence


def _pitch_yin(audio: np.ndarray, sr: int = 16000) -> tuple:
    """Упрощённый YIN-like pitch detection (векторизованный).
    
    Использует кумулятивную разностную функцию для быстрого поиска pitch.
    """
    if len(audio) < sr * 0.05:
        return 0.0, 0.0

    audio = audio - np.mean(audio)
    peak = np.max(np.abs(audio))
    if peak > 0:
        audio = audio / peak

    n = len(audio)
    half = min(n // 2, int(sr / 50))  # Ограничиваем max_tau

    f_min, f_max = 50, 500
    min_tau = int(sr / f_max)
    max_tau = min(int(sr / f_min), half)

    if min_tau >= max_tau or max_tau < 2:
        return 0.0, 0.0

    # Difference function через autocorrelation
    # diff(tau) = sum(x[i]^2) + sum(x[i+tau]^2) - 2*ACF(tau)
    audio_win = audio[:half]
    acf_full = np.correlate(audio, audio, mode='full')
    acf = acf_full[n - 1:]  # non-negative lags [0..n-1]

    sum_sq_total = np.sum(audio_win ** 2)

    # cum_sq[i] = sum(audio[0..i]^2)
    cum_sq = np.cumsum(audio ** 2)

    # sum(audio[i+tau]^2 for i=0..half-1) for each tau
    sum_sq_shifted = np.zeros(max_tau + 1)
    sum_sq_shifted[0] = sum_sq_total
    for tau in range(1, max_tau + 1):
        end_idx = tau + half - 1
        if end_idx < n:
            sum_sq_shifted[tau] = cum_sq[end_idx] - (cum_sq[tau - 1] if tau > 0 else 0)
        else:
            sum_sq_shifted[tau] = cum_sq[-1] - (cum_sq[tau - 1] if tau > 0 else 0)

    acf_slice = acf[:max_tau + 1]
    diff = sum_sq_total + sum_sq_shifted - 2.0 * acf_slice
    diff[0] = 0.0

    # Cumulative mean normalized difference
    cmndf = np.zeros(len(diff))
    cmndf[0] = 1.0
    cum = 0.0
    for tau in range(1, len(diff)):
        cum += diff[tau]
        cmndf[tau] = diff[tau] / (cum / tau) if tau > 0 else 1.0

    # Search range
    search = cmndf[min_tau:max_tau + 1]
    if len(search) == 0:
        return 0.0, 0.0

    # Find first dip below 0.2
    below = np.where(search < 0.2)[0]
    if len(below) == 0:
        idx = np.argmin(search)
        if search[idx] > 0.5:
            return 0.0, 0.0
        best_tau = idx + min_tau
    else:
        best_tau = below[0] + min_tau

    if best_tau >= len(cmndf):
        return 0.0, 0.0

    pitch_hz = sr / best_tau
    confidence = max(0.0, 1.0 - cmndf[best_tau])

    return pitch_hz, confidence


# ═══════════════════════════════════════════════════════════════
# SPECTRAL ANALYSIS
# ═══════════════════════════════════════════════════════════════

def _spectral_features(audio: np.ndarray, sr: int = 16000) -> dict:
    """Спектральные特征: centroid, bandwidth, rolloff.
    
    Берём сегмент из середины аудио для наиболее точного результата.
    """
    if len(audio) < 256:
        return {"centroid": 0.0, "bandwidth": 0.0, "rolloff": 0.0, "flatness": 0.0}

    # Берём сегмент из середины аудио (где обычно речь)
    n_fft = min(1024, len(audio))
    mid = len(audio) // 2
    start = max(0, mid - n_fft // 2)
    end = min(len(audio), start + n_fft)
    segment = audio[start:end]

    if len(segment) < 256:
        return {"centroid": 0.0, "bandwidth": 0.0, "rolloff": 0.0, "flatness": 0.0}

    # FFT
    windowed = segment * np.hanning(len(segment))
    fft = np.fft.rfft(windowed)
    magnitude = np.abs(fft)
    freqs = np.fft.rfftfreq(len(segment), 1.0 / sr)

    if np.sum(magnitude) < 1e-10:
        return {"centroid": 0.0, "bandwidth": 0.0, "rolloff": 0.0, "flatness": 0.0}

    # Spectral centroid
    centroid = np.sum(freqs * magnitude) / np.sum(magnitude)

    # Spectral bandwidth
    bandwidth = np.sqrt(np.sum(((freqs - centroid) ** 2) * magnitude) / np.sum(magnitude))

    # Spectral rolloff (85%)
    cumsum = np.cumsum(magnitude)
    rolloff_idx = np.searchsorted(cumsum, cumsum[-1] * 0.85)
    rolloff = freqs[min(rolloff_idx, len(freqs) - 1)]

    # Spectral flatness (noise vs tonal)
    log_magnitude = np.log(magnitude + 1e-10)
    flatness = np.exp(np.mean(log_magnitude)) / (np.mean(magnitude) + 1e-10)
    flatness = min(1.0, float(flatness))

    return {
        "centroid": float(centroid),
        "bandwidth": float(bandwidth),
        "rolloff": float(rolloff),
        "flatness": flatness,
    }


def _energy_db(audio: np.ndarray) -> float:
    """RMS энергия в dB."""
    if len(audio) == 0:
        return -60.0
    rms = np.sqrt(np.mean(audio ** 2))
    if rms < 1e-10:
        return -60.0
    return float(20 * np.log10(rms + 1e-10))


# ═══════════════════════════════════════════════════════════════
# CLASSIFICATION
# ═══════════════════════════════════════════════════════════════

def _classify_gender(pitch_hz: float, spectral_centroid: float,
                     confidence: float) -> tuple:
    """Определение пола по pitch и спектральному центроиду.
    
    Returns: (gender, confidence)
    """
    if confidence < 0.3 or pitch_hz < 30:
        return "unknown", 0.0

    # Основные диапазоны F0:
    #   Мужской:  85-170 Hz (медиана ~120)
    #   Женский: 165-280 Hz (медиана ~210)
    #   Ребёнок: 280-400 Hz (медиана ~300)

    if pitch_hz < 140:
        # Скорее мужской
        g_conf = min(1.0, (140 - pitch_hz) / 55 + 0.5)
        if spectral_centroid > 2000:
            g_conf *= 0.7
        return "male", g_conf
    elif pitch_hz < 180:
        # Переходная зона (140-180)
        if spectral_centroid < 1800:
            return "male", 0.55
        else:
            return "female", 0.55
    elif pitch_hz < 280:
        # Скорее женский
        g_conf = min(1.0, (pitch_hz - 165) / 80 + 0.4)
        return "female", g_conf
    else:
        # >280 Hz — скорее ребёнок
        g_conf = min(1.0, (pitch_hz - 270) / 100 + 0.4)
        return "child", g_conf


def _classify_age(pitch_hz: float, spectral_centroid: float,
                  bandwidth: float, energy_db: float, confidence: float) -> tuple:
    """Определение возраста по pitch и спектральным特征.
    
    Returns: (age, confidence)
    """
    if confidence < 0.3 or pitch_hz < 30:
        return "unknown", 0.0

    # Дети: высокий pitch (>280 Hz)
    if pitch_hz > 280:
        return "child", min(1.0, (pitch_hz - 280) / 100 + 0.4)

    # Пожилой (60+): низкий centroid, узкий bandwidth, низкая энергия
    # У пожилых людей голос "сдавленный", меньше высоких частот
    if spectral_centroid < 1200 and bandwidth < 500:
        return "elderly", 0.6

    # Молодой (16-30): чистый pitch, высокий centroid, широкий bandwidth
    # Молодые голоса более "яркие" и чистые
    if spectral_centroid > 1800 and bandwidth > 600:
        return "young", 0.6

    # Взрослый (30-60): всё остальное в средних пределах
    if 80 < pitch_hz < 280:
        # Дополнительная проверка: pitch + centroid
        # Мужской centroid >1500 = скорее молодой, <1400 = взрослый/старый
        if pitch_hz > 160:
            # Женский: centroid >1800 = молодой
            if spectral_centroid > 1800:
                return "young", 0.55
            else:
                return "adult", 0.55
        else:
            # Мужской
            if spectral_centroid > 1600:
                return "young", 0.55
            else:
                return "adult", 0.55

    return "adult", 0.5


def _classify_style(energy_db: float, pitch_hz: float,
                    flatness: float, confidence: float) -> str:
    """Определение стиля речи по энергии и спектру."""
    if confidence < 0.3:
        return "normal"

    # Шёпот: очень низкая энергия, высокий flatness (шумовой спектр)
    if energy_db < -40 and flatness > 0.2:
        return "whisper"

    # Крик: очень высокая энергия + высокий pitch
    if energy_db > -10 and pitch_hz > 200:
        return "shout"

    # Смех: высокий pitch + высокий centroid + высокий flatness + переменчивость
    if pitch_hz > 200 and flatness > 0.12:
        return "laugh"

    # Плач: низкий pitch + низкий centroid + модуляция
    if pitch_hz < 150 and flatness < 0.03:
        return "cry"

    return "normal"


# ═══════════════════════════════════════════════════════════════
# MAIN API
# ═══════════════════════════════════════════════════════════════

def _trim_silence(audio: np.ndarray, sr: int, threshold_db: float = -40.0) -> np.ndarray:
    """Вырезать тихие участки в начале и конце. Оставляем речь."""
    if len(audio) == 0:
        return audio
    threshold = 10 ** (threshold_db / 20.0)
    # RMS в окнах 20ms
    win = int(sr * 0.02)
    if win < 1:
        win = 1
    n_windows = len(audio) // win
    if n_windows == 0:
        return audio
    rms_vals = np.array([
        np.sqrt(np.mean(audio[i * win:(i + 1) * win] ** 2))
        for i in range(n_windows)
    ])
    loud = np.where(rms_vals > threshold)[0]
    if len(loud) == 0:
        return audio
    start = loud[0] * win
    end = min(len(audio), (loud[-1] + 1) * win + win)
    return audio[start:end]


def _pick_best_window(audio: np.ndarray, sr: int, win_sec: float = 0.05) -> np.ndarray:
    """Выбрать окно с максимальной энергией для pitch detection."""
    win = int(sr * win_sec)
    if len(audio) <= win:
        return audio
    # RMS в окнах
    n_windows = len(audio) // win
    best_rms = 0
    best_start = 0
    for i in range(n_windows):
        chunk = audio[i * win:(i + 1) * win]
        rms = np.sqrt(np.mean(chunk ** 2))
        if rms > best_rms:
            best_rms = rms
            best_start = i * win
    return audio[best_start:best_start + win]


def analyze_voice(audio: np.ndarray, sr: int = 16000) -> VoiceFeatures:
    """Полный анализ голоса: пол, возраст, стиль, pitch.
    
    Args:
        audio: numpy float32 array (mono)
        sr: sample rate (default 16000)
    
    Returns:
        VoiceFeatures с всеми характеристиками
    """
    if audio is None or len(audio) < sr * 0.03:
        return VoiceFeatures()

    # 1) Обрезаем тишину
    trimmed = _trim_silence(audio, sr)
    if len(trimmed) < sr * 0.03:
        trimmed = audio  # fallback

    # 2) Pitch detection на лучшем окне (самый громкий участок)
    best_window = _pick_best_window(trimmed, sr, win_sec=0.05)

    pitch_yin, conf_yin = _pitch_yin(best_window, sr)
    pitch_ac, conf_ac = _pitch_autocorrelation(best_window, sr)

    # Согласие методов: если оба нашли pitch и он близок — высокая confidence
    # Если разница > 2x — берём меньший (мажоритарный голос = F0, а не октава)
    if pitch_yin > 0 and pitch_ac > 0:
        ratio = max(pitch_yin, pitch_ac) / max(min(pitch_yin, pitch_ac), 1)
        if ratio < 1.3:
            # Согласны — берём среднее и повышаем confidence
            pitch_hz = (pitch_yin + pitch_ac) / 2
            confidence = max(conf_yin, conf_ac) * 1.1
        else:
            # Не согласны — берём меньший (F0, не гармоника)
            pitch_hz = min(pitch_yin, pitch_ac)
            confidence = max(conf_yin, conf_ac) * 0.8
    elif pitch_yin > 0:
        pitch_hz = pitch_yin
        confidence = conf_yin
    elif pitch_ac > 0:
        pitch_hz = pitch_ac
        confidence = conf_ac
    else:
        pitch_hz = 0.0
        confidence = 0.0
    confidence = min(1.0, confidence)

    # 3) Мульти-оконный pitch для усреднения (если confidence низкая)
    if confidence < 0.5 and len(trimmed) > sr * 0.1:
        win = int(sr * 0.05)
        pitches = []
        step = max(1, (len(trimmed) - win) // 8)
        for i in range(0, len(trimmed) - win, step):
            chunk = trimmed[i:i + win]
            p, c = _pitch_autocorrelation(chunk, sr)
            if c > 0.5 and 50 < p < 500:
                pitches.append(p)
        if pitches:
            median_pitch = float(np.median(pitches))
            # Если медиана близка к уже найденному — повышаем confidence
            if pitch_hz > 0 and abs(median_pitch - pitch_hz) / pitch_hz < 0.2:
                confidence = min(1.0, confidence + 0.2)
            else:
                pitch_hz = median_pitch
                confidence = 0.6

    # 4) Спектральный анализ на обрезанном аудио
    spec = _spectral_features(trimmed, sr)
    energy = _energy_db(trimmed)

    # 5) Классификация
    gender, gender_conf = _classify_gender(pitch_hz, spec["centroid"], confidence)
    age, age_conf = _classify_age(pitch_hz, spec["centroid"],
                                  spec["bandwidth"], energy, confidence)
    style = _classify_style(energy, pitch_hz, spec.get("flatness", 0.0), confidence)

    # Общая уверенность
    overall_conf = (gender_conf + age_conf + confidence) / 3.0

    features = VoiceFeatures(
        gender=gender,
        age=age,
        style=style,
        pitch_hz=round(pitch_hz, 1),
        pitch_confidence=round(confidence, 3),
        energy_db=round(energy, 1),
        spectral_centroid=round(spec["centroid"], 1),
        confidence=round(overall_conf, 3),
        raw_features={
            "spectral_bandwidth": round(spec["bandwidth"], 1),
            "spectral_rolloff": round(spec["rolloff"], 1),
            "spectral_flatness": round(spec.get("flatness", 0.0), 4),
            "pitch_ac": round(pitch_ac, 1),
            "pitch_yin": round(pitch_yin, 1),
        }
    )

    logger.debug(
        f"[VoiceAnalyzer] gender={gender}({gender_conf:.2f}) "
        f"age={age}({age_conf:.2f}) style={style} "
        f"pitch={pitch_hz:.1f}Hz energy={energy:.1f}dB"
    )

    return features


def get_voice_label(features: VoiceFeatures) -> str:
    """Человекочитаемая метка: 'мужчина, взрослый' / 'женщина, молодая' и т.д."""
    labels = {
        "gender": {
            "male": "мужчина", "female": "женщина",
            "child": "ребёнок", "unknown": "?"
        },
        "age": {
            "child": "ребёнок", "young": "молодой",
            "adult": "взрослый", "elderly": "пожилой", "unknown": ""
        },
        "style": {
            "normal": "", "shout": "(кричит)", "whisper": "(шепчет)",
            "laugh": "(смеётся)", "cry": "(плачет)",
        },
    }
    parts = []
    g = labels["gender"].get(features.gender, "")
    a = labels["age"].get(features.age, "")
    if g and a and features.gender != "child":
        parts.append(f"{g}, {a}")
    elif g:
        parts.append(g)
    elif a:
        parts.append(a)
    s = labels["style"].get(features.style, "")
    if s:
        parts.append(s)
    return " ".join(parts) if parts else "неизвестный"
