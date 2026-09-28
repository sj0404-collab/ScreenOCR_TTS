"""
RapidOCR wrapper — PaddleOCR models via ONNX Runtime.
Best for English text recognition with various game fonts.
Lightweight (~80MB), fast, works on CPU.
"""
import logging
import numpy as np
from typing import Tuple, Optional
from PIL import Image

logger = logging.getLogger(__name__)


class RapidOCREngine:
    """RapidOCR engine using PaddleOCR models via ONNX Runtime."""

    def __init__(self, lang: str = "en"):
        self.lang = lang
        self._engine = None
        self._initialized = False

    def initialize(self) -> bool:
        """Initialize RapidOCR engine."""
        try:
            from rapidocr_onnxruntime import RapidOCR

            self._engine = RapidOCR()
            self._initialized = True
            logger.info("[RapidOCR] Engine initialized successfully")
            return True
        except ImportError:
            logger.error("[RapidOCR] rapidocr-onnxruntime not installed")
            return False
        except Exception as e:
            logger.error(f"[RapidOCR] Init failed: {e}")
            return False

    def recognize_fast(self, img: Image.Image) -> Tuple[str, float]:
        """Распознать УЖЕ ВЫРЕЗАННУЮ строку, БЕЗ детектора текста.

        Детектор (PP-OCR DB) — самая тяжёлая часть: он масштабирует кадр до
        ~736 px и прогоняет U-Net. Для субтитров он не нужен: полосу мы уже
        нашли сами. Отключение det+cls даёт ускорение в разы, что и нужно
        для чтения экрана в реальном времени.
        """
        if not self._initialized:
            if not self.initialize():
                return "", 0.0
        try:
            arr = np.array(img)
            result = self._engine(arr, use_det=False, use_cls=False,
                                  use_rec=True)
            if result is None or not result:
                return "", 0.0
            # без детектора движок отдаёт готовые строки
            out = result[0] if isinstance(result, tuple) else result
            texts, confs = [], []
            for item in (out or []):
                if isinstance(item, (list, tuple)) and len(item) == 2:
                    texts.append(str(item[0]))
                    try:
                        confs.append(float(item[1]))
                    except Exception:
                        pass
                elif isinstance(item, str):
                    texts.append(item)
            if not texts:
                return "", 0.0
            conf = (sum(confs) / len(confs)) if confs else 0.0
            return " ".join(t.strip() for t in texts if t.strip()), conf
        except Exception as e:
            logger.debug(f"[RapidOCR] recognize_fast failed: {e}")
            return "", 0.0

    def recognize(self, img: Image.Image) -> Tuple[str, float]:
        """Recognize text from PIL Image. Returns (text, confidence)."""
        if not self._initialized:
            if not self.initialize():
                return "", 0.0

        try:
            # Convert PIL to numpy
            img_array = np.array(img)

            # Run OCR — returns (detections, info) tuple
            result = self._engine(img_array)

            if result is None or not result or not result[0]:
                return "", 0.0

            # result[0] = list of [bbox, text, score]
            detections = result[0]

            # Collect all text blocks with confidence
            texts = []
            confs = []
            for detection in detections:
                if len(detection) >= 3:
                    bbox, text, score = detection[0], detection[1], detection[2]
                    if text and text.strip():
                        texts.append(text.strip())
                        confs.append(float(score))

            if not texts:
                return "", 0.0

            # Assemble in reading order (top to bottom, left to right)
            combined = ' '.join(texts)
            avg_conf = sum(confs) / len(confs) * 100

            logger.info(f"[RapidOCR] Recognized ({len(combined)} chars, {avg_conf:.0f}%): {combined[:120]}")
            return combined, avg_conf

        except Exception as e:
            logger.error(f"[RapidOCR] Recognition failed: {e}")
            return "", 0.0

    def is_ready(self) -> bool:
        return self._initialized
