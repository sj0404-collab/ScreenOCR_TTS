"""
Управление настройками приложения
"""
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class Settings:
    """Класс для управления настройками"""
    
    def __init__(self, config_path: str = "config.json"):
        self.config_path = Path(config_path)
        self.config = self._load_config()
    
    def _load_config(self) -> dict:
        """Загрузка конфигурации из файла"""
        if self.config_path.exists():
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    logger.info(f"Настройки загружены из {self.config_path}")
                    return config
            except Exception as e:
                logger.error(f"Ошибка загрузки настроек: {e}")
                return self._get_default_config()
        else:
            logger.info("Файл настроек не найден, создаём дефолтный")
            config = self._get_default_config()
            self._save_config(config)
            return config
    
    def _get_default_config(self) -> dict:
        """Дефолтная конфигурация"""
        return {
            "ocr": {
                "language": "rus+eng",
                "engine": "google_lens",
                "confidence_threshold": 60,
                "scan_interval_ms": 2000,
                "contrast": 1.5,
                "brightness": 1.0,
                "invert": False,
                "bg_color": [128, 128, 128],
                "text_color": [255, 255, 255],
                "region": {
                    "x": 0,
                    "y": 0,
                    "width": 1920,
                    "height": 1080
                },
                "auto_detect_region": False,
                "use_gpu": True,
                "detect_changes": True,
                "change_threshold": 0.05,
                "image_preset": "auto"
            },
            "tts": {
                "voice": "ru-RU-DmitryNeural",
                "male_voice": "ru-RU-DmitryNeural",
                "male_voice_type": "edge",
                "male_pitch": 0,
                "male_rate": 15,
                "female_voice": "ru-RU-SvetlanaNeural",
                "female_voice_type": "edge",
                "female_pitch": 0,
                "female_rate": 15,
                "narrator_voice": "ru-RU-DmitryNeural",
                "narrator_voice_type": "edge",
                "narrator_pitch": 0,
                "narrator_rate": 15,
                "rate": 15,
                "volume": 1.0,
                "output_device": "default",
                "cache_audio": True,
                "characters": {},
            },
            "overlay": {
                "enabled": True,
                "show_text": True,
                "opacity": 0.8,
                "font_size": 14,
                "font_color": "#FFFFFF",
                "background_color": "#000000AA",
                "position": "top-right"
            },
            "hotkeys": {
                "toggle_recognition": "F9",
                "capture_now": "F10",
                "toggle_overlay": "F11",
                "show_settings": "F12",
                "live_mode": "F8",
                "select_region": "F7"
            },
            "general": {
                "start_minimized": False,
                "log_enabled": True,
                "log_level": "INFO"
            },
            "translation": {
                "enabled": False,
                "dst_lang": "ru",
                "api_key": "",
                "model": "google/gemini-2.0-flash-001"
            },
            "game": {
                "text_language": "ru",
                "audio_language": "auto",
                "auto_read_mode": "full",
                "voice_activity_detection": True,
                "auto_repeat_en": True,
                "auto_translate_ru": True,
                "tts_rate_auto_read": -30
            }
        }
    
    def _save_config(self, config: dict):
        """Сохранение конфигурации в файл"""
        try:
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
            logger.info(f"Настройки сохранены в {self.config_path}")
        except Exception as e:
            logger.error(f"Ошибка сохранения настроек: {e}")
    
    def get(self, key: str, default=None):
        """Получение значения по ключу (например: 'ocr.language')"""
        keys = key.split('.')
        value = self.config
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default
        return value
    
    def set(self, key: str, value):
        """Установка значения по ключу"""
        keys = key.split('.')
        config = self.config
        for k in keys[:-1]:
            if k not in config:
                config[k] = {}
            config = config[k]
        config[keys[-1]] = value
        self._save_config(self.config)
    
    def get_all(self) -> dict:
        """Получение всех настроек"""
        return self.config.copy()
    
    def reset(self):
        """Сброс настроек по умолчанию"""
        self.config = self._get_default_config()
        self._save_config(self.config)
        logger.info("Настройки сброшены по умолчанию")