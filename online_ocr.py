"""
Online OCR модули — OCR через облачные API.

Поддерживаемые сервисы:
  1. Zen (opencode.ai) — бесплатный, без ключа
  2. Google Lens — бесплатный OCR через Google Lens API

Все движки используют vision-модели для распознавания текста с изображений.

Удалено 26.09.2026:
  * OrcaRouterOCR — api.orcarouter.ai отдаёт 404;
  * OpenRouterOCR — ключ из настроек отклоняется с 403.
"""
import base64
import io
import logging
import os
import requests
from PIL import Image

logger = logging.getLogger(__name__)

_TIMEOUT = 30


class OnlineOCREngine:
    """Базовый класс для онлайн OCR движков."""

    def __init__(self, api_key: str = "", model: str = ""):
        self.api_key = api_key
        self.model = model

    def recognize(self, image: Image.Image, prompt: str = "") -> str:
        """Распознаёт текст с изображения."""
        raise NotImplementedError

    def _image_to_base64(self, image: Image.Image, max_side: int = 1024) -> str:
        """Конвертирует PIL Image в base64 JPEG."""
        # Масштабируем если нужно
        m = max(image.width, image.height)
        if m > max_side:
            s = max_side / m
            image = image.resize(
                (int(image.width * s), int(image.height * s)),
                Image.Resampling.LANCZOS
            )
        # Конвертируем в RGB если нужно
        if image.mode != "RGB":
            image = image.convert("RGB")
        # Кодируем в JPEG
        buf = io.BytesIO()
        image.save(buf, format="JPEG", quality=85)
        return base64.b64encode(buf.getvalue()).decode("utf-8")


class ZenOCR(OnlineOCREngine):
    """OCR через Zen API (бесплатный, без ключа).

    ВНИМАНИЕ: почти все free-модели Zen отдают 403 FreeTierError
    («can only be used from within OpenCode»). Проверено 26.09.2026 —
    из внешнего приложения работает только space-bunny-free.
    """

    ENDPOINT = "https://opencode.ai/zen/v1/chat/completions"
    DEFAULT_MODEL = "space-bunny-free"

    def recognize(self, image: Image.Image, prompt: str = "") -> str:
        b64 = self._image_to_base64(image)
        ocr_prompt = prompt or (
            "Perform STRICT OPTICAL CHARACTER RECOGNITION (OCR) ONLY. "
            "Transcribe the exact text from this image verbatim. "
            "Do not translate, explain, or add any commentary. "
            "Output ONLY the raw text."
        )

        resp = requests.post(
            self.ENDPOINT,
            headers={
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/sj0404-collab/overlay-translator",
                "X-Title": "ScreenOCR_TTS",
            },
            json={
                "model": self.model or self.DEFAULT_MODEL,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": ocr_prompt},
                        {"type": "image_url", "image_url": {
                            "url": f"data:image/jpeg;base64,{b64}"
                        }}
                    ]
                }],
                "temperature": 0.0,
                "max_tokens": 2048,
            },
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()

        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        return content.strip() if content else ""



class GoogleLensOCR(OnlineOCREngine):
    """OCR через Google Lens API (protobuf endpoint)."""

    ENDPOINT = "https://lensfrontend-pa.googleapis.com/v1/crupload"
    # Ключ НЕ хранится в коде: он берётся из настроек (ocr.online_api_key)
    # или из переменной окружения. Раньше здесь был захардкоженный ключ —
    # он попадал в репозиторий вместе с исходниками.
    DEFAULT_KEY = os.environ.get("GOOGLE_LENS_API_KEY", "")

    def recognize(self, image: Image.Image, prompt: str = "") -> str:
        """Распознавание через Google Lens protobuf API."""
        try:
            from glens_ocr import GlensOCR
            glens = GlensOCR(api_key=self.api_key or self.DEFAULT_KEY)
            return glens.recognize(image)
        except ImportError:
            logger.warning("[GoogleLensOCR] glens_ocr.py not found")
            return ""
        except Exception as e:
            logger.error(f"[GoogleLensOCR] Error: {e}")
            return ""


# ── Фабрика ──────────────────────────────────────────────────────────────

def create_online_ocr(engine: str, api_key: str = "", model: str = "") -> OnlineOCREngine:
    """Создаёт онлайн OCR движок по имени."""
    engines = {
        "zen": ZenOCR,
        "google_lens": GoogleLensOCR,
    }
    cls = engines.get(engine)
    if not cls:
        raise ValueError(f"Unknown online OCR engine: {engine}")
    return cls(api_key=api_key, model=model)
