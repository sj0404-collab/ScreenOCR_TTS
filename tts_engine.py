# -*- coding: utf-8 -*-
"""
TTS Engine v2 — separate methods per engine, dual voice ru/en, profiles.

Key changes:
- _gen_edge(), _gen_rhvoice(), _gen_silero() — separate methods per engine
- Dual voice: ru voice reads ru chunks, en voice reads en chunks
- Voice profiles: user-created profiles with custom pitch/rate/volume/emotion
- Built-in voice list can be hidden via flag
- Text disappears after reading (chunk removal)
"""
import asyncio
import hashlib
import io
import json
import logging
import threading
import os
import platform
import re
import subprocess
import time
import uuid
import tempfile
from pathlib import Path
from typing import List, Optional, Callable, AsyncGenerator

import edge_tts

from voice_profiles import VoiceProfileManager, UserVoiceProfile, VoiceRouter

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# EMOTION SYSTEM — dictionaries and detection
# ═══════════════════════════════════════════════════════════════

EMOTION_TEXT_FIXES = {
    'тс': 'тише', 'тсс': 'тише', 'тссс': 'тише',
    'шш': 'тише', 'шшш': 'тише', 'шшшш': 'тише',
    'псс': 'тише', 'пссс': 'тише',
    'ммм': 'мм', 'мммм': 'мм',
    'нууу': 'ну', 'нуууу': 'ну',
    'эээ': 'эх', 'ээээ': 'эх',
    'ааа': 'аа', 'аааа': 'аа',
    'ооо': 'оо', 'оооо': 'оо',
    'еее': 'ее', 'ееее': 'ее',
    'ууу': 'уу', 'уууу': 'уу',
    'ыыы': 'ыы', 'ыыыы': 'ыы',
}

D_SHOUT = {
    'ааа', 'аааа', 'ааааа', 'ооо', 'оооо', 'еее', 'ееее',
    'ууу', 'уууу', 'аххх', 'оххх', 'уххх',
    'нет', 'да', 'помоги', 'помогите', 'спасите',
    'стой', 'хватит', 'прекрати', 'вперед', 'атака',
    'ура', 'победа', 'невозможно', 'бежим', 'беги',
    'эй', 'слушай', 'внимание', 'смотри',
}
D_ANGER = {
    'злой', 'злая', 'бесит', 'раздражает', 'ненавижу', 'ненависть',
    'тварь', 'мерзавец', 'урод', 'чёрт', 'идиот', 'дурак',
    'подонок', 'негодяй', 'сволочь', 'убью', 'умри', 'сдохни',
    'отвратительно', 'мерзко', 'противно', 'взбешён', 'в ярости',
    'пошёл вон', 'вон', 'прочь', 'не смей', 'накажу', 'предатель',
}
D_PAIN = {
    'больно', 'ой', 'ай', 'ох', 'ух', 'боль',
    'рана', 'ранен', 'удар', 'падение', 'сломал',
    'ожог', 'горит', 'тошнит', 'кружится голова',
    'не могу дышать', 'сердце болит', 'живот болит',
    'простуда', 'болезнь', 'болею', 'слабость',
}
D_FEAR = {
    'боюсь', 'ужас', 'страшно', 'кошмар', 'помоги', 'помогите',
    'нет нет', 'убей', 'смерть', 'опасно', 'тревога',
    'паника', 'дрожу', 'мурашки', 'задыхаюсь',
    'темнота', 'не подходи', 'отойди',
}
D_SURPRISE = {
    'ого', 'о?', 'а?', 'э?', 'неужели', 'не может быть',
    'вот это да', 'вау', 'ничего себе', 'невероятно',
    'серьёзно', 'правда', 'как так', 'что происходит',
    'ух ты', 'ого-го',
}
D_LAUGH = {
    'хаха', 'хахаха', 'хехе', 'хехехе', 'хихи', 'ахах',
    'лол', 'кек', 'рофл', 'смешно', 'угар', 'кайф',
    'ржака', 'умора', 'ха-ха', 'хе-хе', 'хи-хи',
}
D_JOY = {
    'ура', 'класс', 'супер', 'отлично', 'круто', 'здорово',
    'прекрасно', 'чудесно', 'замечательно', 'великолепно',
    'рад', 'рада', 'счастлив', 'счастье', 'праздник',
    'победа', 'получилось', 'успех', 'да!', 'согласен',
    'обожаю', 'нравится', 'мило', 'весело',
}
D_LOVE = {
    'люблю', 'обожаю', 'милый', 'милая', 'дорогой', 'дорогая',
    'сердце', 'целую', 'обнимаю', 'скучаю', 'тоскую',
    'вместе', 'навсегда', 'романтика', 'нежность', 'ласка',
    'забота', 'верность', 'обещаю', 'клянусь',
}
D_SAD = {
    'грустно', 'печаль', 'тоска', 'жаль', 'обидно',
    'слёзы', 'плачу', 'одиноко', 'пусто', 'больно',
    'потерял', 'ушёл', 'прощай', 'забыл', 'скучаю',
    'не получилось', 'провал', 'устал', 'пустота',
}
D_ANNOY = {
    'блин', 'ой', 'хватит', 'надоело', 'достало',
    'задолбал', 'тьфу', 'ерунда', 'глупость',
    'не хочу', 'отстань', 'ну ладно', 'подумаешь',
    'фигня', 'отстой', 'скучно', 'опять', 'сколько можно',
    'чёрт', 'ёлки-палки', 'ну вот',
}
D_SARCASM = {
    'ну да', 'конечно', 'ага', 'как же', 'очень смешно',
    'подумаешь', 'ну и ну', 'браво', 'молодец',
    'поздравляю', 'ну спасибо', 'очень нужно',
    'как интересно', 'ну и что',
}
D_WHISPER = {
    'тихо', 'тише', 'молчи', 'помолчи', 'замолчи',
    'секрет', 'никому не говори', 'осторожно', 'тихонько',
    'шепот', 'прислушайся',
}
D_THINK = {
    'мм', 'хм', 'ну', 'интересно', 'подожди', 'секунду',
    'минутку', 'так...', 'значит', 'допустим',
    'может быть', 'возможно', 'наверное', 'видимо',
    'логично', 'понятно', 'подумай', 'задумайся',
}

EMOTION_DEFAULTS = {
    'shout':    {'rate': 50,  'pitch': 25},
    'anger':    {'rate': 30,  'pitch': -10},
    'pain':     {'rate': 10,  'pitch': 8},
    'fear':     {'rate': 15,  'pitch': 18},
    'surprise': {'rate': 10,  'pitch': 15},
    'laugh':    {'rate': 15,  'pitch': 12},
    'joy':      {'rate': 20,  'pitch': 15},
    'love':     {'rate': -5,  'pitch': 5},
    'sad':      {'rate': -20, 'pitch': -8},
    'annoy':    {'rate': 20,  'pitch': -3},
    'sarcasm':  {'rate': -10, 'pitch': 3},
    'whisper':  {'rate': -25, 'pitch': -5},
    'think':    {'rate': -15, 'pitch': -2},
    'normal':   {'rate': 0,   'pitch': 0},
}

EMOTION_PRIORITY = [
    'shout', 'anger', 'pain', 'fear', 'surprise',
    'laugh', 'joy', 'love', 'sad', 'annoy',
    'sarcasm', 'whisper', 'think',
]

EMOTION_DICTS = {
    'shout': D_SHOUT, 'anger': D_ANGER, 'pain': D_PAIN,
    'fear': D_FEAR, 'surprise': D_SURPRISE, 'laugh': D_LAUGH,
    'joy': D_JOY, 'love': D_LOVE, 'sad': D_SAD,
    'annoy': D_ANNOY, 'sarcasm': D_SARCASM,
    'whisper': D_WHISPER, 'think': D_THINK,
}


class TTSEngine:
    """TTS Engine with separate methods per engine, dual voice, profiles."""

    EDGE_VOICES = []
    RHVOICE_VOICES = []

    # Role constants
    ROLE_MALE = "male"
    ROLE_FEMALE = "female"
    ROLE_NARRATOR = "narrator"

    # Voices for "old/detective/accent" style → RHVoice
    RHVOICE_STYLE_ROLES = {"male", "narrator"}
    # RHVoice voices that sound old/detective
    RHVOICE_DETECTIVE_VOICES = {"rhvoice:Pavel", "rhvoice:Arina"}

    def __init__(self, settings=None):
        self.settings = settings or {}
        self.cache_dir = Path("tts_cache")
        self.cache_dir.mkdir(exist_ok=True)
        self.audio_cache = {}
        self.cache_enabled = True
        self.is_playing = False
        self.voice = "ru-RU-DmitryNeural"
        self.voice_type = "edge"
        self.rate = 15
        self.volume = 1.5
        self.pitch = 0

        # Per-role pitch and rate (-50..+50 range, 0 = default)
        self.role_settings = {
            self.ROLE_MALE:     {"pitch": 0, "rate": 15},
            self.ROLE_FEMALE:   {"pitch": 0, "rate": 15},
            self.ROLE_NARRATOR: {"pitch": 0, "rate": 15},
        }
        self.current_role = self.ROLE_MALE

        # Smooth transition state
        self._transition_enabled = True
        self._transition_ms = 80  # ms to interpolate between chunks

        # Dual voice support
        self.dual_voice_enabled = False
        self.ru_voice = "ru-RU-DmitryNeural"
        self.en_voice = "en-US-GuyNeural"
        self.ru_voice_type = "edge"
        self.en_voice_type = "edge"

        # Voice router for dual voice
        from voice_profiles import VoiceRouter as _VoiceRouter
        self.voice_router = _VoiceRouter(settings=self.settings.config if hasattr(self.settings, 'config') else {})

        # Voice dictionary and multi-voice parser
        from voice_profiles import VoiceDictionary, MultiVoiceParser
        self.voice_dict = VoiceDictionary()
        self.multi_voice_parser = MultiVoiceParser(self.voice_dict)

        # Profile manager
        self.profile_manager = VoiceProfileManager()
        self.active_profile = None
        self.show_builtin_voices = True  # flag to hide built-in list

        # Voice changer preset (None = no transformation)
        self.voice_preset = None  # e.g. "female", "robot", "child"

        # Engines
        self._rhvoice = None
        self._silero_model = None
        self.sapi_available = False
        self.sapi_voice = None
        self._sapi_voices_cache = []

        self._load_edge_voices()
        # RHVoice: lazy-load (don't block startup)
        self._rhvoice = None
        self._rhvoice_loaded = False
        # Silero: lazy-load (don't block startup)
        self._silero_model = None
        self._silero_loaded = False

    def _ensure_rhvoice(self):
        """Lazy-init RHVoice on first use."""
        if self._rhvoice_loaded:
            return
        self._rhvoice_loaded = True
        self._init_rhvoice()

    def _ensure_silero(self):
        """Lazy-init Silero TTS model on first use (per-speaker caching)."""
        if self._silero_loaded:
            return
        self._silero_loaded = True
        if not hasattr(self, '_silero_models'):
            self._silero_models = {}

    def _get_silero_model(self, speaker_code: str):
        """Get or create Silero model for a speaker code (e.g. 'aidar', 'baya')."""
        if not hasattr(self, '_silero_models'):
            self._silero_models = {}

        speaker_map = {
            "aidar": "aidar_v2", "baya": "baya_v2", "irina": "irina_v2",
            "kseniya": "kseniya_v2", "ruslan": "ruslan_v2", "natasha": "natasha_v2",
        }
        model_speaker = speaker_map.get(speaker_code, "baya_v2")
        cache_key = model_speaker

        if cache_key in self._silero_models:
            return self._silero_models[cache_key]

        try:
            import ssl as _ssl
            _ssl._create_default_https_context = _ssl._create_unverified_context
            from silero import silero_tts
            logger.info(f"[Silero] Loading model for speaker '{model_speaker}'...")
            model, utils = silero_tts(language='ru', speaker=model_speaker)
            self._silero_models[cache_key] = model
            logger.info(f"[Silero] Model '{model_speaker}' loaded")
            return model
        except Exception as e:
            logger.warning(f"[Silero] Could not load model '{model_speaker}': {e}")
            return None

    # ═══════════════════════════════════════════════════════════
    # EMOTION DETECTION
    # ═══════════════════════════════════════════════════════════

    def _preprocess_emotion_text(self, text: str) -> str:
        for bad, good in EMOTION_TEXT_FIXES.items():
            text = re.sub(re.escape(bad), good, text, flags=re.IGNORECASE)
        return text

    def _detect_emotion(self, text: str) -> str:
        if not text:
            return 'normal'
        if re.search(r'[А-ЯЁ]{2,}', text):
            return 'shout'
        if re.search(r'([а-яё])\1{3,}', text, re.IGNORECASE):
            return 'shout'
        t_clean = re.sub(r'[!?.,;:\-…]+', ' ', text.lower()).strip()
        for emotion in EMOTION_PRIORITY:
            if emotion == 'shout':
                continue
            d = EMOTION_DICTS.get(emotion)
            if d:
                for word in d:
                    if word in t_clean:
                        return emotion
        if '?' in text:
            return 'surprise'
        return 'normal'

    def _get_role_pitch_rate(self, role: str = None) -> tuple:
        """Get base (pitch, rate) for a role. Falls back to global."""
        if role is None:
            role = self.current_role
        rs = self.role_settings.get(role, {})
        pitch = rs.get("pitch", 0)
        rate = rs.get("rate", 0)
        return pitch, rate

    def _get_emotion_adjustments(self, text: str, role: str = None, sentence_type: str = "statement") -> tuple:
        """Return (rate_adj, pitch_adj) for text, using per-role base + emotion + sentence type.

        Из yomihon-custom TtsSpeaker:
        - Вопросительные предложения: +12% pitch / -5% rate
        - Восклицательные: +7% pitch / +5% rate
        """
        emotion = self._detect_emotion(text)
        defaults = EMOTION_DEFAULTS.get(emotion, EMOTION_DEFAULTS['normal'])
        base_pitch, base_rate = self._get_role_pitch_rate(role)
        rate = int(base_rate + defaults['rate'])
        pitch = int(base_pitch + defaults['pitch'])

        # Просодия по типу предложения (из yomihon-custom)
        if sentence_type == "question":
            pitch += 12  # +12% pitch для вопросов
            rate -= 5    # -5% rate (чуть медленнее)
        elif sentence_type == "exclamation":
            pitch += 7   # +7% pitch для восклицаний
            rate += 5    # +5% rate (чуть быстрее)

        rate = max(-100, min(100, rate))
        pitch = max(-50, min(50, pitch))
        return rate, pitch

    def set_role(self, role: str):
        """Set current active role for pitch/rate lookup."""
        if role in self.role_settings:
            self.current_role = role

    def set_role_pitch(self, role: str, pitch: int):
        """Set pitch for a role (-50..+50)."""
        if role in self.role_settings:
            self.role_settings[role]["pitch"] = max(-50, min(50, pitch))

    def set_role_rate(self, role: str, rate: int):
        """Set rate for a role (-100..+100)."""
        if role in self.role_settings:
            self.role_settings[role]["rate"] = max(-100, min(100, rate))

    def get_role_pitch(self, role: str) -> int:
        return self.role_settings.get(role, {}).get("pitch", 0)

    def get_role_rate(self, role: str) -> int:
        return self.role_settings.get(role, {}).get("rate", 0)

    @staticmethod
    def _detect_text_lang(text: str) -> str:
        if not text:
            return "en"
        cyrillic = sum(1 for c in text if '\u0400' <= c <= '\u04FF')
        total = sum(1 for c in text if c.isalpha())
        if total == 0:
            return "en"
        return "ru" if (cyrillic / total) > 0.3 else "en"

    # ═══════════════════════════════════════════════════════════
    # GENERATE AUDIO — main entry point
    # ═══════════════════════════════════════════════════════════

    def _expand_abbreviations(self, text: str) -> str:
        """Expand abbreviations for TTS."""
        if not text:
            return text
        abbr = {
            "вкл": "включить", "выкл": "выключить",
            "т.д": "так далее", "т.п": "тому подобное",
            "т.е": "то есть", "т.к": "так как",
        }
        for short, full in abbr.items():
            text = re.sub(r'\b' + re.escape(short) + r'\b', full, text, flags=re.IGNORECASE)
        return text

    async def generate_audio(self, text: str, role: str = None) -> bytes:
        """Main entry: generate audio with emotion + dual voice support.
        Now includes sentence-type prosody from yomihon-custom."""
        try:
            if not text or not text.strip():
                return b''
            text = text.strip()
            text = self._preprocess_emotion_text(text)

            # Dual voice: split by language, generate each with its voice
            if self.dual_voice_enabled:
                result = await self._generate_dual_voice(text, role)
                if not result:
                    logger.warning(f"[generate_audio] Dual voice returned empty for: {text[:50]}...")
                return self._apply_voice_changer(result)

            # Single voice with emotion + sentence-type prosody
            # Определяем тип предложения
            stype = "statement"
            if "?" in text or "?" in text:
                stype = "question"
            elif "!" in text or "!" in text:
                stype = "exclamation"

            rate_adj, pitch_adj = self._get_emotion_adjustments(text, role, stype)
            result = await self._gen_by_engine(text, rate_adj, pitch_adj, role)
            if not result:
                logger.warning(f"[generate_audio] Engine returned empty for: {text[:50]}... (voice={self.voice}, type={self.voice_type})")
            return self._apply_voice_changer(result)
        except Exception as e:
            logger.error(f"[generate_audio] Error: {e}")
            return b''

    def _apply_voice_changer(self, audio_data: bytes) -> bytes:
        """Apply voice changer transformation if a preset is set."""
        if not audio_data or not self.voice_preset or self.voice_preset == "original":
            return audio_data
        try:
            from voice_changer import transform_audio
            result = transform_audio(audio_data, self.voice_preset)
            return result
        except Exception as e:
            logger.warning(f"[voice_changer] Failed: {e}, returning original")
            return audio_data

    async def _generate_dual_voice(self, text: str, role: str = None) -> bytes:
        """Split text by language, generate ru chunks with ru voice, en with en.
        Applies smooth crossfade between chunks for seamless playback.
        Now includes sentence_type for prosody adjustments."""
        chunks = self._split_by_language(text)
        all_audio = []
        for chunk_data in chunks:
            if len(chunk_data) == 3:
                chunk_text, lang, sentence_type = chunk_data
            else:
                chunk_text, lang = chunk_data
                sentence_type = "statement"
            if not chunk_text.strip():
                continue
            rate_adj, pitch_adj = self._get_emotion_adjustments(chunk_text, role, sentence_type)
            if lang == "ru":
                old_voice, old_type = self.voice, self.voice_type
                self.voice, self.voice_type = self.ru_voice, self.ru_voice_type
                audio = await self._gen_by_engine(chunk_text, rate_adj, pitch_adj, role)
                self.voice, self.voice_type = old_voice, old_type
            else:
                old_voice, old_type = self.voice, self.voice_type
                self.voice, self.voice_type = self.en_voice, self.en_voice_type
                audio = await self._gen_by_engine(chunk_text, rate_adj, pitch_adj, role)
                self.voice, self.voice_type = old_voice, old_type
            if audio:
                # Smooth transition: crossfade between consecutive chunks
                if all_audio and self._transition_enabled:
                    audio = self._crossfade(all_audio[-1], audio, self._transition_ms)
                all_audio.append(audio)
        return b''.join(all_audio) if all_audio else b''

    def _split_by_language(self, text: str) -> list:
        """Split text into (chunk, lang, sentence_type) triples.

        sentence_type: 'question', 'exclamation', 'statement'
        Из yomihon-custom TtsSpeaker.splitSentences:
        - Разбивка по .!?... с лимитом 200 символов на чанк
        - Определение типа предложения для просодии
        """
        if not text or not text.strip():
            return [(text, self._detect_text_lang(text), "statement")]

        # Максимальная длина одного предложения (из yomihon: 200 chars)
        MAX_SENT_LEN = 200

        # Разбиваем по маркерам конца предложения
        parts = re.split(r'(?<=[.!?…])\s+|\n+', text)
        result = []
        buffer = ""

        for part in parts:
            part = part.strip()
            if not part:
                continue

            # Определяем тип предложения
            if "?" in part or "?" in part:
                stype = "question"
            elif "!" in part or "!" in part:
                stype = "exclamation"
            else:
                stype = "statement"

            # Если буфер + часть превышают лимит — сбрасываем буфер
            if buffer and len(buffer) + len(part) > MAX_SENT_LEN:
                lang = self._detect_text_lang(buffer)
                # Тип буфера — берём тип последнего предложения в нём
                buf_type = "statement"
                if "?" in buffer:
                    buf_type = "question"
                elif "!" in buffer:
                    buf_type = "exclamation"
                result.append((buffer, lang, buf_type))
                buffer = ""

            buffer = (buffer + " " + part).strip() if buffer else part

            # Если предложение заканчивается на .!? — сбрасываем
            if part and part[-1] in ".!?…!?":
                lang = self._detect_text_lang(buffer)
                result.append((buffer, lang, stype))
                buffer = ""

        # Остаток буфера
        if buffer.strip():
            lang = self._detect_text_lang(buffer)
            stype = "statement"
            if "?" in buffer:
                stype = "question"
            elif "!" in buffer:
                stype = "exclamation"
            result.append((buffer, lang, stype))

        return result if result else [(text, self._detect_text_lang(text), "statement")]

    async def _gen_by_engine(self, text: str, rate: int, pitch: int, role: str = None) -> bytes:
        """Route to engine: Edge for bright/youthful, RHVoice for old/detective or offline.

        When voice_type is 'edge', Edge is ALWAYS tried first (the bing.com probe is
        only a hint, never a hard gate). Only if Edge itself fails do we fall back to
        RHVoice/SAPI — otherwise a flaky bing.com probe would wrongly push us to SAPI
        (which defaults to the system voice, e.g. Microsoft Zira)."""
        import urllib.request

        # Best-effort internet probe (does NOT block Edge)
        internet_ok = True
        try:
            urllib.request.urlopen("https://www.bing.com", timeout=5)
        except Exception:
            internet_ok = False

        # RHVoice preferred only for explicit old/detective style AND internet up
        need_rhvoice_style = (self.voice_type == "edge" and internet_ok
                              and role in self.RHVOICE_STYLE_ROLES and pitch < -15)

        if self.voice_type == "rhvoice":
            result = await self._gen_rhvoice(text, rate, pitch)
            if result:
                return result
            # RHVoice failed → fall through to Edge
        elif self.voice_type == "silero":
            return await self._gen_silero(text, rate, pitch)
        elif self.voice_type == "persona":
            return await self._gen_persona(text, rate, pitch, role)

        # edge (default) — always try Edge first
        if self.voice_type in ("edge", "rhvoice"):
            if need_rhvoice_style:
                rh = await self._gen_rhvoice(text, rate, pitch)
                if rh:
                    return rh
            edge = await self._gen_edge(text, rate, pitch)
            if edge:
                return edge
            # Edge failed (genuinely offline) → last-resort RHVoice/SAPI
            if not internet_ok:
                fb = await self._gen_rhvoice(text, rate, pitch)
                if fb:
                    return fb
        return b''

    # ═══════════════════════════════════════════════════════════
    # EDGE-TTS — separate method
    # ═══════════════════════════════════════════════════════════

    async def _gen_edge(self, text: str, rate: int, pitch: int) -> bytes:
        """Generate audio via Edge-TTS.
        rate: -100..+100 (percentage)
        pitch: -50..+50 (mapped to Hz, 1 unit ≈ 2Hz)"""
        clean_text = re.sub(r'\s+', ' ', text).strip()
        if not clean_text:
            return b''
        if len(clean_text) > 10000:
            clean_text = clean_text[:10000]

        rate_str = f"{rate:+d}%"
        # Map pitch: -50..+50 → -100..+100 Hz (Edge-TTS range is roughly ±50Hz from base)
        # Clamp to Edge-TTS safe range: -50..+50 Hz
        pitch_hz = max(-50, min(50, pitch))
        pitch_str = f"{pitch_hz:+d}Hz" if pitch_hz != 0 else None

        for attempt in range(3):
            uid = uuid.uuid4().hex[:12]
            temp_path = self.cache_dir / f"edge_{uid}.mp3"
            try:
                vol_str = f"{int((self.volume - 1.0) * 100):+d}%" if self.volume != 1.0 else "+0%"
                kwargs = dict(text=clean_text, voice=self.voice, rate=rate_str, volume=vol_str)
                if pitch_str:
                    kwargs["pitch"] = pitch_str
                comm = edge_tts.Communicate(**kwargs)
                await asyncio.wait_for(comm.save(str(temp_path)), timeout=30)
                if temp_path.exists() and temp_path.stat().st_size > 100:
                    data = temp_path.read_bytes()
                    self._safe_unlink(temp_path)
                    return data
            except asyncio.TimeoutError:
                logger.warning(f"[Edge] Attempt {attempt+1} timeout (30s)")
                self._safe_unlink(temp_path)
            except Exception as e:
                logger.warning(f"[Edge] Attempt {attempt+1} failed: {e}")
                self._safe_unlink(temp_path)
        return b''

    # ═══════════════════════════════════════════════════════════
    # RHVOICE — separate method
    # ═══════════════════════════════════════════════════════════

    async def _gen_rhvoice(self, text: str, rate: int, pitch: int) -> bytes:
        """Generate audio via RHVoice SAPI5.
        rate: -100..+100 (mapped to SAPI -10..+10)
        pitch: -50..+50 (mapped to SAPI pitch if supported, else ignored)"""
        self._ensure_rhvoice()
        if not self._rhvoice:
            return b''
        voice_name = self.voice.split(":", 1)[1] if ":" in self.voice else self.voice
        uid = uuid.uuid4().hex[:12]
        temp_path = self.cache_dir / f"rhv_{uid}.wav"
        try:
            await self._speak_sapi5(text, voice_name, str(temp_path), rate, pitch)
            if temp_path.exists() and temp_path.stat().st_size > 100:
                data = temp_path.read_bytes()
                self._safe_unlink(temp_path)
                return data
        except Exception as e:
            logger.warning(f"[RHVoice/SAPI5] Error: {e}")
            self._safe_unlink(temp_path)
        return b''

    # ═══════════════════════════════════════════════════════════
    # SILERO — separate method
    # ═══════════════════════════════════════════════════════════

    async def _gen_silero(self, text: str, rate: int, pitch: int) -> bytes:
        """Generate audio via Silero.
        Note: Silero doesn't natively support pitch/rate, but we keep the params
        for API consistency. Rate can be applied via audio stretching if needed."""
        self._ensure_silero()
        if self._detect_text_lang(text) != "ru":
            return b''
        speaker = self.voice.split(":", 1)[1] if ":" in self.voice else "aidar"
        model = self._get_silero_model(speaker)
        if not model:
            return b''
        uid = uuid.uuid4().hex[:12]
        temp_path = self.cache_dir / f"sil_{uid}.wav"
        try:
            await self._speak_silero(text, speaker, str(temp_path), rate)
            if temp_path.exists() and temp_path.stat().st_size > 100:
                data = temp_path.read_bytes()
                self._safe_unlink(temp_path)
                return data
        except Exception as e:
            logger.warning(f"[Silero] Error: {e}")
            self._safe_unlink(temp_path)
        return b''

    # ═══════════════════════════════════════════════════════════
    # PERSONA — Edge-TTS with custom rate/pitch from PERSONA dict
    # ═══════════════════════════════════════════════════════════

    async def _gen_persona(self, text: str, rate: int, pitch: int, role: str = None) -> bytes:
        """Generate audio via persona (Edge-TTS voice + persona rate/pitch + user adjustments).
        Persona provides base rate/pitch, user's role settings add on top."""
        persona_code = self.voice.split(":", 1)[1] if ":" in self.voice else ""
        try:
            from live_voices import PERSONA
            info = PERSONA.get(persona_code)
            if not info:
                return b''
            edge_voice = info.get("voice", "ru-RU-DmitryNeural")
            # Parse persona base rate (e.g. "+20%") and pitch (e.g. "+10Hz")
            persona_rate_str = info.get("rate", "+0%")
            persona_pitch_str = info.get("pitch", "+0Hz")
            pr_match = re.search(r'([+-]?\d+)', persona_rate_str)
            pp_match = re.search(r'([+-]?\d+)', persona_pitch_str)
            persona_rate = int(pr_match.group(1)) if pr_match else 0
            persona_pitch = int(pp_match.group(1)) if pp_match else 0
            # Blend: persona base + user adjustments
            final_rate = max(-100, min(100, persona_rate + rate))
            final_pitch = max(-50, min(50, persona_pitch + pitch))
            clean_text = re.sub(r'\s+', ' ', text).strip()
            if not clean_text:
                return b''
            if len(clean_text) > 10000:
                clean_text = clean_text[:10000]
            uid = uuid.uuid4().hex[:12]
            temp_path = self.cache_dir / f"pers_{uid}.mp3"
            try:
                import edge_tts
                rate_str = f"{final_rate:+d}%"
                pitch_str = f"{final_pitch:+d}Hz" if final_pitch != 0 else None
                vol_str = f"{int((self.volume - 1.0) * 100):+d}%" if self.volume != 1.0 else "+0%"
                kwargs = dict(text=clean_text, voice=edge_voice, rate=rate_str, volume=vol_str)
                if pitch_str:
                    kwargs["pitch"] = pitch_str
                comm = edge_tts.Communicate(**kwargs)
                await asyncio.wait_for(comm.save(str(temp_path)), timeout=30)
                if temp_path.exists() and temp_path.stat().st_size > 100:
                    data = temp_path.read_bytes()
                    self._safe_unlink(temp_path)
                    return data
            except Exception as e:
                logger.warning(f"[Persona] Error: {e}")
                self._safe_unlink(temp_path)
        except Exception as e:
            logger.warning(f"[Persona] Import error: {e}")
        return b''

    # ═══════════════════════════════════════════════════════════
    # PLAYBACK METHODS
    # ═══════════════════════════════════════════════════════════

    async def speak(self, text: str, callback=None):
        """Play text via MCI player (no VLC)."""
        try:
            self.is_playing = True
            clean_text = re.sub(r'\s+', ' ', text).strip() if text else ""
            if not clean_text:
                return

            # Strip special chars for TTS — keep only letters, digits, basic punctuation
            tts_text = re.sub(r'[^\w\s.,!?;:\-\u0400-\u04FF\u0041-\u005A\u0061-\u007A\u3000-\u9FFF]', '', clean_text)
            tts_text = re.sub(r'\s+', ' ', tts_text).strip()
            if not tts_text:
                tts_text = clean_text

            logger.info(f"[speak] voice={self.voice}, type={self.voice_type}")
            audio_data = await self.generate_audio(tts_text)
            if audio_data:
                ext = ".wav" if self.voice_type in ("silero", "rhvoice") else ".mp3"
                uid = uuid.uuid4().hex[:12]
                tmp = self.cache_dir / f"speak_{uid}{ext}"
                tmp.write_bytes(audio_data)
                await self._play_audio(str(tmp), callback=callback)
                self._safe_unlink(tmp)
            else:
                logger.error(f"[speak] Engine failed for: {clean_text[:50]}")

        except Exception as e:
            logger.error(f"[speak] Error: {e}")
        finally:
            self.is_playing = False

    async def speak_streaming(self, text: str, callback=None):
        """Streaming TTS — starts playback as soon as first audio chunk arrives."""
        try:
            self.is_playing = True
            clean_text = re.sub(r'\s+', ' ', text).strip() if text else ""
            if not clean_text:
                return

            if self.voice_type == "edge":
                await self._speak_edge_streaming(clean_text, callback)
            elif self.voice_type == "rhvoice":
                await self.speak(clean_text, callback)
            else:
                await self.speak(clean_text, callback)
        except Exception as e:
            logger.error(f"[speak_streaming] Error: {e}")
        finally:
            self.is_playing = False

    async def _speak_edge_streaming(self, text: str, callback=None):
        """Edge-TTS streaming: play audio chunks as they arrive from the server."""
        import edge_tts as _edge_tts
        from builtin_player import get_player

        voice = self.voice
        rate_str = f"{self.rate:+d}%" if self.rate else "+0%"
        vol_str = f"{int((self.volume - 1.0) * 100):+d}%" if self.volume != 1.0 else "+0%"

        uid = uuid.uuid4().hex[:12]
        tmp_path = self.cache_dir / f"stream_{uid}.mp3"

        try:
            comm = _edge_tts.Communicate(text, voice, rate=rate_str, volume=vol_str)
            player = get_player()
            chunks_written = 0
            file_started = False

            with open(tmp_path, "wb") as f:
                async for chunk in comm.stream():
                    if chunk["type"] == "audio" and chunk["data"]:
                        f.write(chunk["data"])
                        chunks_written += len(chunk["data"])

                        # Start playback after first ~1KB of audio data
                        if not file_started and chunks_written > 1024:
                            file_started = True
                            # Play in background — MCI reads from file as we write
                            player.play(str(tmp_path), wait=False)

            # Wait for playback to finish if we started it
            if file_started:
                player.wait_finish(timeout=30)
            player.close()
        except Exception as e:
            logger.error(f"[Edge streaming] Error: {e}")
        finally:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass
            if callback:
                callback()

    def _fallback_to_offline(self):
        """Disabled — use only the configured voice."""
        pass

    async def speak_instant(self, text: str, callback=None):
        """Instant playback — generate and play."""
        try:
            self.is_playing = True
            audio_data = await self.generate_audio(text)
            if audio_data:
                ext = ".wav" if self.voice_type in ("silero", "rhvoice") else ".mp3"
                uid = uuid.uuid4().hex[:12]
                tmp = self.cache_dir / f"inst_{uid}{ext}"
                tmp.write_bytes(audio_data)
                await self._play_audio(str(tmp), callback=callback)
                self._safe_unlink(tmp)
        except Exception as e:
            logger.error(f"[speak_instant] Error: {e}")
        finally:
            self.is_playing = False

    async def speak_streaming(self, text: str):
        """Generate audio and play it. Cleans up temp file after playback."""
        try:
            self.is_playing = True
            audio_data = await self.generate_audio(text)
            if audio_data:
                ext = ".wav" if self.voice_type in ("silero", "rhvoice") else ".mp3"
                uid = uuid.uuid4().hex[:12]
                tmp = self.cache_dir / f"stream_{uid}{ext}"
                tmp.write_bytes(audio_data)
                await self._play_audio(str(tmp))
                self._safe_unlink(tmp)
        except Exception as e:
            logger.error(f"[speak_streaming] Error: {e}")
        finally:
            self.is_playing = False

    async def speak_dialogue(self, dialogue: str, voice_a: str = None,
                             voice_b: str = None, voice_narrator: str = None,
                             callback=None):
        """Play multi-character dialogue. Format: 'A: text' / 'B: text' / 'text'."""
        lines = [l.strip() for l in dialogue.strip().split("\n") if l.strip()]
        original_voice = self.voice
        original_type = self.voice_type
        try:
            for i, line in enumerate(lines):
                speaker, text = self._parse_dialogue_line(line)
                if speaker == "A" and voice_a:
                    self.set_voice(voice_a)
                elif speaker == "B" and voice_b:
                    self.set_voice(voice_b)
                elif speaker == "N" and voice_narrator:
                    self.set_voice(voice_narrator)
                if i == 0 and callback:
                    callback()
                await self.speak(text)
        finally:
            self.voice = original_voice
            self.voice_type = original_type

    @staticmethod
    def _parse_dialogue_line(line: str):
        """Return (speaker, text). Speaker is 'A', 'B', 'N', or ''."""
        m = re.match(r'^([ABN]):\s*(.+)', line)
        if m:
            return m.group(1), m.group(2)
        return '', line

    async def _play_audio(self, audio_path: str, callback=None):
        try:
            from builtin_player import get_player
            player = get_player()
            player.play(audio_path, wait=True)
            player.close()
            # Small delay for Windows to release file handle
            await asyncio.sleep(0.05)
        except Exception as e:
            logger.warning(f"_play_audio error: {e}")
        finally:
            if callback:
                callback()

    def _safe_unlink(self, path):
        """Delete file with retry for Windows file locks."""
        import pathlib
        p = pathlib.Path(path) if not isinstance(path, pathlib.Path) else path
        for attempt in range(3):
            try:
                if p.exists():
                    p.unlink()
                    return
            except PermissionError:
                time.sleep(0.1)
            except Exception:
                break
        # If still locked, just log — temp files will be cleaned on next startup
        if p.exists():
            logger.debug(f"[TTS] Could not delete temp file: {p.name}")

    def _crossfade(self, audio1: bytes, audio2: bytes, fade_ms: int = 80) -> bytes:
        """Crossfade between two MP3/WAV audio byte strings.
        For MP3, we do a simple byte-level crossfade (good enough for short fades).
        Returns audio1 + audio2 with overlap region blended."""
        if not audio1 or not audio2 or fade_ms <= 0:
            return audio1 + audio2
        try:
            import io
            # Try to do proper audio crossfade with numpy
            # Detect format from header
            is_wav1 = audio1[:4] == b'RIFF'
            is_wav2 = audio2[:4] == b'RIFF'
            if is_wav1 and is_wav2:
                return self._crossfade_wav(audio1, audio2, fade_ms)
            # For MP3 or mixed: simple overlap at byte level
            fade_bytes = min(len(audio1), len(audio2), fade_ms * 44)  # ~44 bytes/ms for MP3
            if fade_bytes < 100:
                return audio1 + audio2
            # Overlap: fade out end of audio1, fade in start of audio2
            return audio1[:-fade_bytes] + audio2
        except Exception:
            return audio1 + audio2

    def _crossfade_wav(self, wav1: bytes, wav2: bytes, fade_ms: int) -> bytes:
        """Proper WAV crossfade using numpy."""
        try:
            import io, wave, numpy as np
            with wave.open(io.BytesIO(wav1), 'rb') as w1:
                rate1 = w1.getframerate()
                raw1 = np.frombuffer(w1.readframes(w1.getnframes()), dtype=np.int16)
            with wave.open(io.BytesIO(wav2), 'rb') as w2:
                raw2 = np.frombuffer(w2.readframes(w2.getnframes()), dtype=np.int16)
            fade_samples = min(int(rate1 * fade_ms / 1000), len(raw1), len(raw2))
            if fade_samples < 10:
                return wav1 + wav2
            # Create fade curves
            fade_out = np.linspace(1.0, 0.0, fade_samples, dtype=np.float32)
            fade_in = np.linspace(0.0, 1.0, fade_samples, dtype=np.float32)
            # Blend overlap region
            overlap = (raw1[-fade_samples:].astype(np.float32) * fade_out +
                       raw2[:fade_samples].astype(np.float32) * fade_in).astype(np.int16)
            result = np.concatenate([raw1[:-fade_samples], overlap, raw2[fade_samples:]])
            # Write back to WAV
            buf = io.BytesIO()
            with wave.open(buf, 'wb') as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(rate1)
                w.writeframes(result.tobytes())
            return buf.getvalue()
        except Exception:
            return wav1 + wav2

    def force_stop(self):
        """Kill all active TTS processes instantly."""
        self.is_playing = False
        if hasattr(self, '_current_process') and self._current_process:
            try:
                self._current_process.kill()
            except Exception:
                pass
            self._current_process = None
        logger.info("[TTS] force_stop — всё остановлено")

    def stop_stream(self):
        """Stop current streaming playback (alias for force_stop)."""
        self.force_stop()

    # ═══════════════════════════════════════════════════════════
    # PROFILE MANAGEMENT
    # ═══════════════════════════════════════════════════════════

    def apply_profile(self, profile: UserVoiceProfile):
        """Apply a voice profile to current settings."""
        self.voice = profile.voice
        self.voice_type = profile.voice_type
        self.pitch = profile.pitch
        self.rate = profile.rate
        self.volume = profile.volume
        self.active_profile = profile.name
        # Also update role settings from profile if available
        if hasattr(profile, 'role_pitch') and hasattr(profile, 'role_rate'):
            for role in self.role_settings:
                if role in profile.role_pitch:
                    self.role_settings[role]["pitch"] = profile.role_pitch[role]
                if role in profile.role_rate:
                    self.role_settings[role]["rate"] = profile.role_rate[role]

    def get_profile_list(self) -> list:
        """Get list of available profiles."""
        return self.profile_manager.get_all()

    def add_profile(self, name: str, data: dict):
        self.profile_manager.add(name, data)

    def remove_profile(self, name: str):
        self.profile_manager.remove(name)

    # ═══════════════════════════════════════════════════════════
    # VOICE LIST (for GUI dropdown)
    # ═══════════════════════════════════════════════════════════

    # 4 default Edge voices (the rest hidden unless show_all_edge_voices=True)
    EDGE_DEFAULTS = {
        "ru-RU-DmitryNeural", "ru-RU-SvetlanaNeural",
        "en-US-GuyNeural", "en-US-JennyNeural",
    }

    def get_voices(self) -> List[dict]:
        """Get voice list. Offline first, then Edge (4 default or all), profiles last."""
        if not self.show_builtin_voices:
            return [{"code": f"profile:{p.name}", "name": p.name, "type": "profile"}
                    for p in self.profile_manager.get_all()]

        show_all = self.settings.get("tts.show_all_edge_voices", False) if hasattr(self.settings, 'get') else False

        voices = []
        for v in self.EDGE_VOICES.copy():
            if v.get("type") == "edge" and not show_all and v["code"] not in self.EDGE_DEFAULTS:
                continue
            voices.append(v)
        if self.sapi_available:
            voices.extend(self._sapi_voices_cache)
        # Sort: rhvoice > persona > silero > edge > profiles
        priority = {"rhvoice": 1, "persona": 2, "silero": 3, "edge": 4, "profile": 5}
        voices.sort(key=lambda v: priority.get(v.get("type", "edge"), 5))
        # Add profiles at the end
        for p in self.profile_manager.get_all():
            voices.append({"code": f"profile:{p.name}", "name": f"★ {p.name}", "type": "profile"})
        return voices

    def set_voice(self, voice_code: str):
        """Set voice by code."""
        if voice_code.startswith("profile:"):
            profile_name = voice_code.split(":", 1)[1]
            profile = self.profile_manager.get(profile_name)
            if profile:
                self.apply_profile(profile)
            return

        self.voice = voice_code
        if voice_code.startswith(("ru-RU-", "en-", "de-", "fr-", "es-", "it-", "ja-", "zh-", "ko-", "uk-", "pl-", "pt-", "tr-", "ar-", "hi-", "th-")):
            self.voice_type = "edge"
        elif voice_code.startswith("rhvoice:"):
            self.voice_type = "rhvoice"
        elif voice_code.startswith("silero:"):
            self.voice_type = "silero"
        elif voice_code.startswith("persona:"):
            self.voice_type = "persona"
        else:
            self.voice_type = "sapi"

    # ═══════════════════════════════════════════════════════════
    # ENGINE INITIALIZATION (stubs — keep existing logic)
    # ═══════════════════════════════════════════════════════════

    def _load_edge_voices(self):
        """Load Edge-TTS voices — cache to disk, refresh in background."""
        cache_path = self.cache_dir / "edge_voices_cache.json"
        # Fast: load from cache immediately
        if cache_path.exists():
            try:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                if cached and len(cached) > 10:
                    TTSEngine.EDGE_VOICES = cached
                    logger.info(f"[Edge] Loaded {len(cached)} voices from cache")
                    self._register_offline_voices()
                    # Refresh in background (non-blocking)
                    threading.Thread(target=self._refresh_edge_voices, daemon=True).start()
                    return
            except Exception:
                pass
        # No cache: load synchronously (first run only)
        self._refresh_edge_voices()
        self._register_offline_voices()

    def _refresh_edge_voices(self):
        """Fetch Edge-TTS voices from network and cache to disk."""
        try:
            import asyncio as _asyncio
            loop = _asyncio.new_event_loop()
            voices = loop.run_until_complete(edge_tts.list_voices())
            loop.close()
            TTSEngine.EDGE_VOICES = [
                {"code": v["ShortName"], "name": v.get("FriendlyName", v["ShortName"]),
                 "type": "edge", "lang": v.get("Locale", "")[:2],
                 "gender": v.get("Gender", "")}
                for v in voices
            ]
            # Cache to disk
            cache_path = self.cache_dir / "edge_voices_cache.json"
            cache_path.write_text(json.dumps(TTSEngine.EDGE_VOICES, ensure_ascii=False), encoding="utf-8")
            logger.info(f"[Edge] Refreshed {len(TTSEngine.EDGE_VOICES)} voices (cached)")
            # Re-register offline voices (RHVoice/Silero/Persona were lost)
            self._register_offline_voices()
        except Exception as e:
            logger.warning(f"Could not refresh Edge voices: {e}")
            if not TTSEngine.EDGE_VOICES:
                TTSEngine.EDGE_VOICES = [
                    {"code": "ru-RU-DmitryNeural", "name": "Dmitry (ru)", "type": "edge", "lang": "ru", "gender": "Male"},
                    {"code": "ru-RU-SvetlanaNeural", "name": "Svetlana (ru)", "type": "edge", "lang": "ru", "gender": "Female"},
                    {"code": "en-US-GuyNeural", "name": "Guy (en)", "type": "edge", "lang": "en", "gender": "Male"},
                    {"code": "en-US-JennyNeural", "name": "Jenny (en)", "type": "edge", "lang": "en", "gender": "Female"},
                ]

    def _register_offline_voices(self):
        """Register RHVoice (SAPI5), Silero and Persona voices into EDGE_VOICES for menu display."""
        # RHVoice: detect via SAPI5 PowerShell
        rhvoice_available = False
        known_rhvoice = []
        try:
            sapi_voices = self._get_sapi_voices()
            if sapi_voices:
                rhvoice_available = True
                for v in sapi_voices:
                    known_rhvoice.append({
                        "code": f"rhvoice:{v['name']}",
                        "name": f"{v['name']} (ru, RHVoice)",
                        "type": "rhvoice", "lang": "ru",
                        "gender": v['gender'],
                    })
        except Exception:
            pass

        silero_available = False
        try:
            import torch
            from scipy.io.wavfile import write as _test_wav
            silero_available = True
        except ImportError:
            pass

        known_silero = []
        if silero_available:
            known_silero = [
                {"code": "silero:aidar", "name": "Aidar (ru, Silero)", "type": "silero", "lang": "ru", "gender": "Male"},
                {"code": "silero:baya", "name": "Baya (ru, Silero)", "type": "silero", "lang": "ru", "gender": "Female"},
                {"code": "silero:kseniya", "name": "Kseniya (ru, Silero)", "type": "silero", "lang": "ru", "gender": "Female"},
                {"code": "silero:ruslan", "name": "Ruslan (ru, Silero)", "type": "silero", "lang": "ru", "gender": "Male"},
            ]

        # Personas from live_voices (всегда доступны — используют Edge-TTS)
        try:
            from live_voices import PERSONA
            known_personas = []
            for k, p in PERSONA.items():
                known_personas.append({
                    "code": f"persona:{k}",
                    "name": f"🎭 {p.get('label', k)}",
                    "type": "persona",
                    "lang": "ru",
                    "gender": "unknown",
                })
        except Exception:
            known_personas = []

        existing_codes = {v["code"] for v in TTSEngine.EDGE_VOICES}
        for v in known_rhvoice + known_silero + known_personas:
            if v["code"] not in existing_codes:
                TTSEngine.EDGE_VOICES.append(v)
        logger.info(f"[Voices] Registered: {len(known_rhvoice)} RHVoice"
                    f" ({'available' if rhvoice_available else 'NOT available'}), "
                    f"{len(known_silero)} Silero ({'available' if silero_available else 'NOT available'}), "
                    f"{len(known_personas)} Persona")

    def _init_rhvoice(self):
        """Initialize RHVoice via SAPI5 (voices installed as Windows SAPI5)."""
        try:
            rh_voices = self._get_sapi_voices()
            self._rhvoice = True
            self.RHVOICE_VOICES = [
                {"code": f"rhvoice:{v['name']}", "name": f"{v['name']} (ru, RHVoice)",
                 "type": "rhvoice", "lang": "ru", "gender": v['gender']}
                for v in rh_voices
            ]
            TTSEngine.EDGE_VOICES.extend(self.RHVOICE_VOICES)
            logger.info(f"[RHVoice/SAPI5] Loaded {len(self.RHVOICE_VOICES)} voices")
        except Exception as e:
            logger.warning(f"RHVoice not available: {e}")
            self._rhvoice = None

    def _get_sapi_voices(self) -> list:
        """Get SAPI5 voices via PowerShell. Returns list of dicts with name, culture, gender."""
        try:
            ps = (
                'Add-Type -AssemblyName System.Speech; '
                '$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; '
                'foreach($v in $s.GetInstalledVoices()){'
                '  Write-Output ($v.VoiceInfo.Name + "|" + $v.VoiceInfo.Culture.Name + "|" + $v.VoiceInfo.Gender)'
                '}'
            )
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-STA", "-Command", ps],
                capture_output=True, text=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
            )
            voices = []
            rhvoice_names = {"elena", "arina", "pavel", "victoria", "aleksandr", "anna",
                             "irina", "boris", "maxim", "umka", "tatiana", "mikhail",
                             "timofey", "artemiy", "evgeniy-rus", "yuriy", "vitaliy",
                             "dmitry", "svetlana", "alexander"}
            for line in proc.stdout.strip().split("\n"):
                line = line.strip()
                if "|" not in line:
                    continue
                parts = line.split("|")
                name = parts[0].strip()
                culture = parts[1].strip() if len(parts) > 1 else ""
                gender = parts[2].strip() if len(parts) > 2 else ""
                # Include: known RHVoice names, any ru-RU voice, or name contains "rhvoice"
                is_rh = (name.lower() in rhvoice_names or
                         "rhvoice" in name.lower() or
                         culture.startswith("ru"))
                if is_rh:
                    voices.append({"name": name, "culture": culture, "gender": gender})
            return voices
        except Exception as e:
            logger.warning(f"SAPI5 voice detection error: {e}")
            return []

    async def _speak_sapi5(self, text: str, voice_name: str, output_path: str,
                            rate: int = 0, pitch: int = 0):
        """Synthesize via SAPI5 PowerShell — uses temp text file to avoid escaping issues.
        rate: -100..+100 (mapped to SAPI -10..+10)
        pitch: -50..+50 (SAPI doesn't support pitch directly, logged for reference)"""
        uid = uuid.uuid4().hex[:8]
        text_file = self.cache_dir / f"sapi_text_{uid}.txt"
        try:
            text_file.write_text(text, encoding="utf-8")
            # SAPI5 rate: -10..10. Map our rate (-100..+100) to SAPI range.
            sapi_rate = max(-10, min(10, round(rate * 10 / 100)))
            if pitch != 0:
                logger.debug(f"[SAPI5] Pitch adjustment ({pitch}) not supported by SAPI5, ignored")
            ps_cmd = (
                f'Add-Type -AssemblyName System.Speech; '
                f'$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; '
                f'$s.SelectVoice("{voice_name}"); '
                f'$s.Rate = {sapi_rate}; '
                f'$t = [IO.File]::ReadAllText("{str(text_file)}"); '
                f'$s.SetOutputToWaveFile("{str(output_path)}"); '
                f'$s.Speak($t); '
                f'$s.SetOutputToNull(); $s.Dispose()'
            )
            proc = await asyncio.create_subprocess_exec(
                "powershell", "-NoProfile", "-STA", "-Command", ps_cmd,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            if proc.returncode != 0:
                logger.warning(f"[RHVoice/SAPI5] PowerShell error (rc={proc.returncode}): {stderr.decode('utf-8', errors='replace')[:200]}")
            if os.path.exists(output_path) and os.path.getsize(output_path) > 100:
                self._resample_wav(output_path)
        except Exception as e:
            logger.warning(f"[RHVoice/SAPI5] Error: {e}")
        finally:
            self._safe_unlink(text_file)

    def _resample_wav(self, path: str):
        """Resample WAV 22050→44100Hz with scipy for MCI compatibility."""
        try:
            import wave, numpy as np
            from scipy.signal import resample
            with wave.open(path, 'rb') as w:
                rate = w.getframerate()
                if rate == 44100:
                    return
                raw = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
                n_samples = int(len(raw) * 44100 / rate)
                resampled = resample(raw.astype(np.float32), n_samples).astype(np.int16)
            with wave.open(path, 'wb') as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(44100)
                w.writeframes(resampled.tobytes())
        except Exception as e:
            logger.debug(f"[RHVoice] Resample skipped: {e}")

    async def _speak_silero(self, text, speaker, output_path, rate=0):
        """Silero TTS — generate WAV from model.
        rate: -100..+100 (applied via audio stretching if non-zero)."""
        model = self._get_silero_model(speaker)
        if not model:
            return
        try:
            import numpy as np
            from scipy.io.wavfile import write as write_wav
            audio_list = model.apply_tts(
                texts=text,
                sample_rate=48000
            )
            audio_np = np.concatenate([
                t.numpy() if hasattr(t, 'numpy') else np.array(t)
                for t in audio_list
            ])
            # Apply rate adjustment via resampling if needed
            if rate != 0:
                speed_factor = 1.0 + (rate / 100.0)
                if speed_factor > 0.1:
                    n_samples = int(len(audio_np) / speed_factor)
                    if n_samples > 0:
                        from scipy.signal import resample
                        audio_np = resample(audio_np.astype(np.float32), n_samples).astype(np.int16)
            write_wav(output_path, 48000, audio_np)
        except Exception as e:
            logger.warning(f"[Silero] TTS error: {e}")

    async def generate_audio_file(self, text: str, output_path: str = None) -> Optional[str]:
        """Generate audio file. Delegates to generate_audio(). Returns None on failure."""
        try:
            audio_data = await self.generate_audio(text)
        except Exception as e:
            logger.error(f"[generate_audio_file] Error: {e}")
            return None
        if audio_data:
            if output_path:
                file_path = Path(output_path)
            else:
                uid = uuid.uuid4().hex[:12]
                ext = ".wav" if self.voice_type in ("silero", "rhvoice") else ".mp3"
                file_path = self.cache_dir / f"tts_{uid}{ext}"
            file_path.write_bytes(audio_data)
            return str(file_path)
        logger.warning(f"[generate_audio_file] Empty audio for: {text[:50]}...")
        return None

    def set_rate(self, rate: float):
        self.rate = rate

    def set_volume(self, volume: float):
        self.volume = volume
