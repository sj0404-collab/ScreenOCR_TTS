"""
Voice Profiles + Voice Router + MultiVoiceParser.

Contains:
- VoiceProfile / VoiceDictionary / MultiVoiceParser (original, backward-compatible)
- VoiceProfileManager / UserVoiceProfile (new user-customizable profiles)
"""
import json
import logging
import re
from pathlib import Path
from typing import Dict, Optional, List

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# ORIGINAL CLASSES (backward-compatible)
# ═══════════════════════════════════════════════════════════════

class VoiceProfile:
    """Voice profile for a character/source."""

    def __init__(self, name: str, voice_code: str, rate: float = 0.0,
                 volume: float = 1.0, language: str = "auto",
                 gender: str = "any", number: str = "singular"):
        self.name = name
        self.voice_code = voice_code
        self.rate = rate
        self.volume = volume
        self.language = language
        self.gender = gender
        self.number = number


class VoiceDictionary:
    """Dictionary of voice profiles for characters."""

    def __init__(self):
        self.profiles: Dict[str, VoiceProfile] = {}
        self.default_profile = VoiceProfile("default", "ru-RU-DmitryNeural")
        self._load_defaults()

    def _load_defaults(self):
        defaults = [
            VoiceProfile("Рассказчик", "ru-RU-DmitryNeural", rate=0.0, volume=1.0, language="ru", gender="male"),
            VoiceProfile("Героиня", "ru-RU-SvetlanaNeural", rate=0.0, volume=0.9, language="ru", gender="female"),
            VoiceProfile("Герой", "ru-RU-DmitryNeural", rate=-0.1, volume=1.0, language="ru", gender="male"),
            VoiceProfile("Narrator", "en-US-GuyNeural", rate=0.0, volume=1.0, language="en", gender="male"),
            VoiceProfile("Heroine", "en-US-JennyNeural", rate=0.0, volume=0.9, language="en", gender="female"),
            VoiceProfile("Hero", "en-US-GuyNeural", rate=-0.1, volume=1.0, language="en", gender="male"),
        ]
        for p in defaults:
            self.profiles[p.name.lower()] = p

    def get_profile(self, name: str) -> VoiceProfile:
        key = name.lower().strip()
        if key in self.profiles:
            return self.profiles[key]
        return self.default_profile

    def get_profile_by_language(self, lang: str) -> VoiceProfile:
        for key, profile in self.profiles.items():
            if profile.language == lang:
                return profile
        return self.default_profile

    def set_profile(self, name: str, voice_code: str, rate: float = 0.0,
                    volume: float = 1.0, pitch: float = 0.0, language: str = "auto"):
        self.profiles[name.lower().strip()] = VoiceProfile(
            name, voice_code, rate, volume, language
        )

    def remove_profile(self, name: str):
        key = name.lower().strip()
        if key in self.profiles and key != "default":
            del self.profiles[key]

    def get_all_profiles(self) -> Dict[str, VoiceProfile]:
        return self.profiles.copy()


class MultiVoiceParser:
    """Parser for multi-character text with auto language detection."""

    PATTERNS = [
        re.compile(r'^([^:]+):\s*(.+)$'),
        re.compile(r'^([^>]+)>\s*(.+)$'),
        re.compile(r'^\[([^\]]+)\]\s*(.+)$'),
        re.compile(r'^\(([^)]+)\)\s*(.+)$'),
    ]

    RUSSIAN_WORDS = {
        'и', 'в', 'не', 'на', 'я', 'что', 'он', 'с', 'это', 'а', 'как', 'но', 'да',
        'мы', 'за', 'то', 'она', 'они', 'вы', 'от', 'по', 'его', 'её', 'их', 'бы',
        'же', 'уже', 'для', 'был', 'была', 'были', 'быть', 'есть', 'или', 'если',
        'когда', 'где', 'чем', 'кто', 'этот', 'эта', 'эти', 'тот', 'та', 'те',
    }

    ENGLISH_WORDS = {
        'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of', 'with',
        'by', 'from', 'as', 'is', 'was', 'are', 'were', 'be', 'been', 'being', 'have',
        'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could', 'should', 'may',
        'i', 'you', 'he', 'she', 'it', 'we', 'they', 'me', 'him', 'her', 'my', 'your',
        'what', 'which', 'who', 'where', 'when', 'why', 'how', 'all', 'each', 'every',
        'this', 'that', 'these', 'those', 'not', 'only', 'same', 'so', 'than', 'too',
    }

    def __init__(self, voice_dict: VoiceDictionary = None):
        self.voice_dict = voice_dict or VoiceDictionary()

    def split_by_language(self, text: str) -> list:
        if not text:
            return [("auto", "")]
        paragraphs = re.split(r'\n\s*\n', text.strip())
        if len(paragraphs) <= 1:
            paragraphs = re.split(r'(?<=[.!?])\s+', text.strip())
        results = []
        for para in paragraphs:
            para = para.strip()
            if not para or len(para) < 2:
                continue
            lang = self.detect_language(para)
            results.append((lang, para))
        return results if results else [("auto", text)]

    def split_by_alphabet(self, text: str) -> list:
        if not text or not text.strip():
            return []
        tokens = re.findall(r"[A-Za-zА-Яа-яёЁ]+|[^A-Za-zА-Яа-яёЁ]+", text)
        segments = []
        for tok in tokens:
            if re.search(r"[А-Яа-яёЁ]", tok):
                lang = "ru"
            elif re.search(r"[A-Za-z]", tok):
                lang = "en"
            else:
                lang = None
            if lang is None:
                if segments:
                    segments[-1][1] += tok
                else:
                    segments.append([None, tok])
                continue
            if segments and segments[-1][0] == lang:
                segments[-1][1] += tok
            elif segments and segments[-1][0] is None:
                segments[-1][0] = lang
                segments[-1][1] += tok
            else:
                segments.append([lang, tok])
        result = []
        for lang, buf in segments:
            buf = buf.strip()
            if not buf:
                continue
            if lang is None:
                lang = self.detect_language(buf)
            if not re.search(r"[A-Za-zА-Яа-яёЁ]", buf):
                if result:
                    result[-1] = (result[-1][0], (result[-1][1] + " " + buf).strip())
                continue
            result.append((lang, buf))
        return result

    def detect_language(self, text: str) -> str:
        if not text:
            return "auto"
        cyrillic = len(re.findall(r'[А-Яа-яёЁ]', text))
        if cyrillic > 0:
            return "ru"
        japanese = len(re.findall(r'[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF]', text))
        if japanese > 0:
            return "ja"
        german = len(re.findall(r'[äöüßÄÖÜ]', text))
        if german > 0:
            return "de"
        french = len(re.findall(r'[àâçéèêëïîôùûüÿœæ]', text))
        if french > 0:
            return "fr"
        words = re.findall(r'[A-Za-z]+', text.lower())
        if not words:
            return "auto"
        ru_count = sum(1 for w in words if w in self.RUSSIAN_WORDS)
        en_count = sum(1 for w in words if w in self.ENGLISH_WORDS)
        if ru_count > en_count and ru_count > 0:
            return "ru"
        if en_count > ru_count and en_count > 0:
            return "en"
        return "en"

    def parse_line(self, text: str) -> tuple:
        text = text.strip()
        if not text:
            return None, ""
        for pattern in self.PATTERNS:
            match = pattern.match(text)
            if match:
                return match.group(1).strip(), match.group(2).strip()
        return None, text

    def parse_text(self, text: str) -> list:
        results = []
        for line in text.split("\n"):
            line = line.strip()
            if not line:
                continue
            character, content = self.parse_line(line)
            if content:
                results.append((character, content))
        return results

    def get_voice_for_text(self, text: str) -> list:
        parsed = self.parse_text(text)
        results = []
        for character, content in parsed:
            if character:
                profile = self.voice_dict.get_profile(character)
                if profile.name == "default":
                    lang = self.detect_language(content)
                    profile = self.voice_dict.get_profile_by_language(lang)
            else:
                lang = self.detect_language(content)
                profile = self.voice_dict.get_profile_by_language(lang)
            results.append((profile, content))
        return results


class VoiceRouter:
    """Routes text to appropriate voice based on language."""

    def __init__(self, settings=None):
        self.settings = settings or {}
        self._voice_dict = VoiceDictionary()
        self._parser = MultiVoiceParser(self._voice_dict)

    def detect_language(self, text: str) -> str:
        return self._parser.detect_language(text)

    def get_voice_for_lang(self, lang: str) -> str:
        routing = self.settings.get("tts.voice_router", {}).get("routing", {})
        if lang in routing:
            return routing[lang]
        # Defaults
        if lang == "ru":
            return "ru-RU-DmitryNeural"
        if lang == "en":
            return "en-US-GuyNeural"
        return "ru-RU-DmitryNeural"

    def get_voice_for_language(self, lang: str) -> str:
        return self.get_voice_for_lang(lang)

    def set_voice_for_language(self, lang: str, voice_code: str):
        if "tts.voice_router" not in self.settings:
            self.settings["tts.voice_router"] = {"routing": {}}
        self.settings["tts.voice_router"]["routing"][lang] = voice_code

    def set_fallback_voice(self, voice_code: str):
        if "tts.voice_router" not in self.settings:
            self.settings["tts.voice_router"] = {}
        self.settings["tts.voice_router"]["fallback"] = voice_code

    def get_fallback_voice(self) -> str:
        return self.settings.get("tts.voice_router", {}).get("fallback", "ru-RU-DmitryNeural")

    def get_voice_for_text(self, text: str) -> str:
        """Return voice code for text based on language detection."""
        lang = self.detect_language(text)
        return self.get_voice_for_lang(lang)


SUPPORTED_LANGUAGES = {
    "ru": "Russian", "en": "English", "ja": "Japanese",
    "de": "German", "fr": "French", "es": "Spanish",
    "it": "Italian", "uk": "Ukrainian", "pl": "Polish",
    "pt": "Portuguese", "tr": "Turkish", "ar": "Arabic",
    "hi": "Hindi", "th": "Thai", "zh": "Chinese", "ko": "Korean",
}


# ═══════════════════════════════════════════════════════════════
# NEW: User-customizable voice profiles
# ═══════════════════════════════════════════════════════════════

class UserVoiceProfile:
    """User-created voice profile with full customization."""

    def __init__(self, name: str, data: dict = None):
        self.name = name
        self.voice = data.get("voice", "ru-RU-DmitryNeural") if data else "ru-RU-DmitryNeural"
        self.voice_type = data.get("voice_type", "edge") if data else "edge"
        self.pitch = data.get("pitch", 0) if data else 0
        self.rate = data.get("rate", 0) if data else 0
        self.volume = data.get("volume", 1.0) if data else 1.0
        self.emotion = data.get("emotion", "normal") if data else "normal"
        self.noise_level = data.get("noise_level", 0) if data else 0
        self.description = data.get("description", "") if data else ""
        self.is_builtin = data.get("is_builtin", False) if data else False
        # Per-role pitch/rate
        self.role_pitch = data.get("role_pitch", {}) if data else {}
        self.role_rate = data.get("role_rate", {}) if data else {}

    def to_dict(self) -> dict:
        return {
            "voice": self.voice, "voice_type": self.voice_type,
            "pitch": self.pitch, "rate": self.rate, "volume": self.volume,
            "emotion": self.emotion, "noise_level": self.noise_level,
            "description": self.description, "is_builtin": self.is_builtin,
            "role_pitch": self.role_pitch, "role_rate": self.role_rate,
        }

    @classmethod
    def from_dict(cls, name: str, data: dict) -> "UserVoiceProfile":
        return cls(name, data)


class VoiceProfileManager:
    """Manages user-created voice profiles."""

    def __init__(self, profiles_path: str = "voice_profiles.json"):
        self.profiles_path = Path(profiles_path)
        self.profiles: Dict[str, UserVoiceProfile] = {}
        self._load()

    def _load(self):
        if self.profiles_path.exists():
            try:
                with open(self.profiles_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                for name, pdata in data.items():
                    self.profiles[name] = UserVoiceProfile(name, pdata)
            except Exception:
                pass
        if not self.profiles:
            self._create_defaults()
            self._save()

    def _save(self):
        data = {name: p.to_dict() for name, p in self.profiles.items()}
        with open(self.profiles_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _create_defaults(self):
        defaults = {
            "Dmitry (default)": {
                "voice": "ru-RU-DmitryNeural", "voice_type": "edge",
                "pitch": 0, "rate": 0, "volume": 1.0,
                "emotion": "normal", "noise_level": 0,
                "description": "Default Russian male", "is_builtin": True,
            },
            "Svetlana (default)": {
                "voice": "ru-RU-SvetlanaNeural", "voice_type": "edge",
                "pitch": 0, "rate": 0, "volume": 1.0,
                "emotion": "normal", "noise_level": 0,
                "description": "Default Russian female", "is_builtin": True,
            },
            "Aleksandr (RHVoice)": {
                "voice": "rhvoice:Aleksandr", "voice_type": "rhvoice",
                "pitch": 0, "rate": 0, "volume": 1.0,
                "emotion": "normal", "noise_level": 0,
                "description": "RHVoice Russian male", "is_builtin": True,
            },
            "Elena (RHVoice)": {
                "voice": "rhvoice:Elena", "voice_type": "rhvoice",
                "pitch": 0, "rate": 0, "volume": 1.0,
                "emotion": "normal", "noise_level": 0,
                "description": "RHVoice Russian female", "is_builtin": True,
            },
        }
        for name, pdata in defaults.items():
            self.profiles[name] = UserVoiceProfile(name, pdata)

    def get(self, name: str) -> Optional[UserVoiceProfile]:
        return self.profiles.get(name)

    def get_all(self) -> List[UserVoiceProfile]:
        return list(self.profiles.values())

    def add(self, name: str, data: dict):
        self.profiles[name] = UserVoiceProfile(name, data)
        self._save()

    def remove(self, name: str):
        if name in self.profiles and not self.profiles[name].is_builtin:
            del self.profiles[name]
            self._save()

    def update(self, name: str, data: dict):
        if name in self.profiles:
            self.profiles[name] = UserVoiceProfile(name, data)
            self._save()
