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
