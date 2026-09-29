# -*- coding: utf-8 -*-
"""
OCR Engines — реестр движков и пресеты для типа контента.

Движки:
  1. CyrillicONNX — локальный ONNX (PaddleOCR/RapidOCR), оптимизирован для кириллицы
  2. EasyOCR — локальный EasyOCR (многоязычный)
  3. GoogleLens — онлайн Google Lens API

Пресеты контента (из OcrTuning.kt):
  - BALANCED — сбалансированный (по умолчанию)
  - MANGA — манга (мелкий текст, вертикальные реплики)
  - COMIC — комикс (крупный текст, горизонтальный)
  - MANHWA — манхва/вебтун (вертикальное чтение)
"""
import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# ДВИЖКИ OCR
# ═══════════════════════════════════════════════════════════════

class EngineType(str, Enum):
    TFLITE_CYRILLIC = "tflite_cyrillic"
    EASYOCR = "easyocr"
    GOOGLE_LENS = "google_lens"
    RAPIDOCR = "rapidocr"
    ZEN = "zen"


@dataclass
class OcrEngineDescriptor:
    """Декларативное описание OCR-движка (из OcrPlugins.kt)."""
    id: str
    name: str
    engine_type: EngineType
    priority: int  # чем меньше — тем выше приоритет
    requires_network: bool = False
    requires_gpu: bool = False
    description: str = ""
    languages: List[str] = field(default_factory=lambda: ["ru", "en"])


# Реестр движков (порядок = приоритет по умолчанию)
ENGINE_REGISTRY: Dict[str, OcrEngineDescriptor] = {
    EngineType.GOOGLE_LENS.value: OcrEngineDescriptor(
        id=EngineType.GOOGLE_LENS.value,
        name="Google Lens (Glens OCR)",
        engine_type=EngineType.GOOGLE_LENS,
        priority=0,
        requires_network=True,
        description="Google Lens OCR через protobuf API (Glens OCR) — основной движок",
        languages=["ru", "en", "ja", "ko", "zh", "de", "fr", "es"],
    ),
    EngineType.TFLITE_CYRILLIC.value: OcrEngineDescriptor(
        id=EngineType.TFLITE_CYRILLIC.value,
        name="TFLite Cyrillic",
        engine_type=EngineType.TFLITE_CYRILLIC,
        priority=10,
        requires_network=False,
        description="Локальный TFLite (PP-OCRv3+v5 + детектор v4), оптимизирован для кириллицы",
        languages=["ru", "en"],
    ),
    EngineType.EASYOCR.value: OcrEngineDescriptor(
        id=EngineType.EASYOCR.value,
        name="EasyOCR",
        engine_type=EngineType.EASYOCR,
        priority=20,
        requires_network=False,
        requires_gpu=False,
        description="Локальный EasyOCR (многоязычный)",
        languages=["ru", "en", "ja", "ko", "zh"],
    ),
    EngineType.RAPIDOCR.value: OcrEngineDescriptor(
        id=EngineType.RAPIDOCR.value,
        name="RapidOCR (быстрый)",
        engine_type=EngineType.RAPIDOCR,
        priority=5,
        requires_network=False,
        description="Локальный RapidOCR на ONNX — самый быстрый: ~0.3 c на кадр. "
                    "Хорошо для латиницы",
        languages=["ru", "en"],
    ),
    EngineType.ZEN.value: OcrEngineDescriptor(
        id=EngineType.ZEN.value,
        name="Zen (space-bunny-free)",
        engine_type=EngineType.ZEN,
        priority=8,
        requires_network=True,
        description="Облачный OCR через Zen: читает ЛЮБЫЕ языки включая "
                    "иероглифы, ~3-4 c на кадр, ключ не нужен",
        languages=["ru", "en", "ja", "ko", "zh", "any"],
    ),
}


def list_available_engines() -> List[OcrEngineDescriptor]:
    """Список движков, которые РЕАЛЬНО установлены в этой системе.

    В списке настроек не должно быть движков, которые падают при выборе:
    раньше там стояли Tesseract (его нет в движках вообще) и EasyOCR
    (пакет не установлен) — то есть выбор заведомо нерабочих вариантов.
    """
    out = []
    for desc in sorted(ENGINE_REGISTRY.values(), key=lambda d: d.priority):
        if not _engine_installed(desc.engine_type):
            logger.debug(f"[Engines] {desc.id} пропущен: не установлен")
            continue
        out.append(desc)
    return out


def _engine_installed(engine_type: "EngineType") -> bool:
    """Есть ли зависимости движка в этой системе.

    Проверка идёт через find_spec, а НЕ через import: импорт
    tensorflow/tflite_runtime занимает ~15 c и тормозит запуск GUI.
    """
    import importlib.util

    def have(mod):
        try:
            return importlib.util.find_spec(mod) is not None
        except Exception:
            return False

    if engine_type == EngineType.RAPIDOCR:
        return have("rapidocr_onnxruntime")
    if engine_type == EngineType.EASYOCR:
        return have("easyocr")
    if engine_type == EngineType.TFLITE_CYRILLIC:
        # движок умеет работать и на tensorflow.lite, и на tflite_runtime
        if not (have("tensorflow") or have("tflite_runtime")):
            return False
        # плюс сами модели должны быть на месте (models/cyrillic_ocr/)
        models = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "models", "cyrillic_ocr")
        if not os.path.isdir(models):
            models = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "models")
        return (os.path.exists(os.path.join(models, "cyrillic_detector.tflite"))
                and os.path.exists(os.path.join(models,
                                                "cyrillic_recognizer_v3.tflite")))
    if engine_type in (EngineType.GOOGLE_LENS, EngineType.ZEN):
        # это наш собственный код, он всегда на месте
        return os.path.exists(os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "online_ocr.py"))
    return True


def get_engine_descriptor(engine_id: str) -> Optional[OcrEngineDescriptor]:
    """Получить описание движка по ID."""
    return ENGINE_REGISTRY.get(engine_id)


def list_engines() -> List[OcrEngineDescriptor]:
    """Список всех доступных движков, отсортированный по приоритету."""
    return sorted(ENGINE_REGISTRY.values(), key=lambda e: e.priority)


def fallback_chain(primary: str, available: Optional[List[str]] = None) -> List[str]:
    """Построить цепочку fallback (из OcrPlugins.fallbackChain).

    Возвращает有序 список engine_id, начиная с primary.
    Если available задан — фильтрует только доступные.
    """
    engines = list_engines()
    chain = []
    for eng in engines:
        if available and eng.id not in available:
            continue
        if eng.id == primary:
            chain.insert(0, eng.id)
        else:
            chain.append(eng.id)
    return chain


# ═══════════════════════════════════════════════════════════════
# ПРЕСЕТЫ КОНТЕНТА
# ═══════════════════════════════════════════════════════════════

class ContentPreset(str, Enum):
    BALANCED = "balanced"
    MANGA = "manga"
    COMIC = "comic"
    MANHWA = "manhwa"
    VISUAL_NOVEL = "visual_novel"
    RPG_GAMES = "rpg_games"
    SUBTITLES = "subtitles"
    LIGHT_NOVEL = "light_novel"
    HANDWRITTEN = "handwritten"
    WEBNOVEL = "webnovel"


@dataclass
class OcrTuningProfile:
    """Профиль параметров OCR для типа контента (из OcrTuning.kt)."""
    name: str
    preset: ContentPreset
    # Предобработка
    contrast: float = 1.5
    brightness: float = 1.0
    invert: bool = False
    upscale_factor: float = 2.0
    # EasyOCR параметры
    min_size: int = 5
    contrast_ths: float = 0.05
    text_threshold: float = 0.5
    # Фильтрация
    confidence_threshold: int = 40
    min_word_length: int = 2
    # Регион
    reading_direction: str = "ltr"  # ltr / rtl
    vertical_text: bool = False


# Пресеты (адаптированы из OcrTuning.kt + новые для приложения)
PRESETS: Dict[str, OcrTuningProfile] = {
    ContentPreset.BALANCED.value: OcrTuningProfile(
        name="Сбалансированный",
        preset=ContentPreset.BALANCED,
        contrast=1.5,
        brightness=1.0,
        upscale_factor=2.0,
        min_size=5,
        contrast_ths=0.05,
        text_threshold=0.5,
        confidence_threshold=40,
        reading_direction="ltr",
    ),
    ContentPreset.MANGA.value: OcrTuningProfile(
        name="Манга",
        preset=ContentPreset.MANGA,
        contrast=2.0,
        brightness=1.2,
        upscale_factor=2.5,
        min_size=3,
        contrast_ths=0.03,
        text_threshold=0.4,
        confidence_threshold=35,
        vertical_text=True,
    ),
    ContentPreset.COMIC.value: OcrTuningProfile(
        name="Комикс",
        preset=ContentPreset.COMIC,
        contrast=1.8,
        brightness=1.1,
        upscale_factor=2.0,
        min_size=8,
        contrast_ths=0.08,
        text_threshold=0.6,
        confidence_threshold=45,
        reading_direction="ltr",
    ),
    ContentPreset.MANHWA.value: OcrTuningProfile(
        name="Манхва / Вебтун",
        preset=ContentPreset.MANHWA,
        contrast=1.7,
        brightness=1.15,
        upscale_factor=2.5,
        min_size=4,
        contrast_ths=0.04,
        text_threshold=0.45,
        confidence_threshold=38,
        vertical_text=True,
        reading_direction="rtl",
    ),
    # ── Визуальные новеллы ──────────────────────────────────────
    # Шрифты стилизованные, полупрозрачные пузыри, много диалогов.
    # Нужен высокий контраст для чёткого чтения размытых шрифтов.
    ContentPreset.VISUAL_NOVEL.value: OcrTuningProfile(
        name="Визуальная новелла",
        preset=ContentPreset.VISUAL_NOVEL,
        contrast=2.2,
        brightness=1.2,
        upscale_factor=3.0,  # мелкий шрифт диалогов
        min_size=3,
        contrast_ths=0.03,
        text_threshold=0.35,  # мягче — полупрозрачные пузыри
        confidence_threshold=30,
        reading_direction="ltr",
    ),
    # ── RPG / Игры ──────────────────────────────────────────────
    # Крупный UI-текст, меню, квесты, диалоги. Разные шрифты,
    # от яркого UI до тёмных подвалов. Мягкие пороги.
    ContentPreset.RPG_GAMES.value: OcrTuningProfile(
        name="Игры (RPG)",
        preset=ContentPreset.RPG_GAMES,
        contrast=1.8,
        brightness=1.1,
        upscale_factor=2.0,
        min_size=4,
        contrast_ths=0.04,
        text_threshold=0.4,
        confidence_threshold=35,
        reading_direction="ltr",
    ),
    # ── Субтитры ────────────────────────────────────────────────
    # Светлый текст на тёмном фоне. Инверсия выключена (уже светлый).
    # Высокий контраст для чёткости, крупный upscale.
    ContentPreset.SUBTITLES.value: OcrTuningProfile(
        name="Субтитры",
        preset=ContentPreset.SUBTITLES,
        contrast=2.5,
        brightness=1.3,
        invert=False,  # текст уже светлый
        upscale_factor=3.0,  # мелкие субтитры
        min_size=3,
        contrast_ths=0.02,  # очень мягко — полупрозрачные буквы
        text_threshold=0.35,
        confidence_threshold=30,
        reading_direction="ltr",
    ),
    # ── Лайт-новелла ────────────────────────────────────────────
    # Тяжёлые текстовые страницы. Много текста, мелкий шрифт.
    # Баланс между скоростью и качеством.
    ContentPreset.LIGHT_NOVEL.value: OcrTuningProfile(
        name="Лайт-новелла",
        preset=ContentPreset.LIGHT_NOVEL,
        contrast=1.6,
        brightness=1.0,
        upscale_factor=2.5,
        min_size=3,
        contrast_ths=0.04,
        text_threshold=0.45,
        confidence_threshold=38,
        reading_direction="ltr",
    ),
    # ── Рукописный текст ────────────────────────────────────────
    # Почерк, заметки, дневники. Очень высокий контраст, мягкие пороги.
    ContentPreset.HANDWRITTEN.value: OcrTuningProfile(
        name="Рукописный текст",
        preset=ContentPreset.HANDWRITTEN,
        contrast=3.0,
        brightness=1.4,
        upscale_factor=3.5,  # максимум для мелкого почерка
        min_size=2,
        contrast_ths=0.02,
        text_threshold=0.3,  # очень мягко — неровные буквы
        confidence_threshold=25,  # рукопись = низкий confidence
        reading_direction="ltr",
    ),
    # ── Веб-новелла / Онлайн-текст ──────────────────────────────
    # Онлайн-манга, вебтун с текстом на фоне, фанатские переводы.
    # Стандартные веб-шрифты, но могут быть артефакты сжатия.
    ContentPreset.WEBNOVEL.value: OcrTuningProfile(
        name="Веб-новелла",
        preset=ContentPreset.WEBNOVEL,
        contrast=1.7,
        brightness=1.1,
        upscale_factor=2.0,
        min_size=4,
        contrast_ths=0.05,
        text_threshold=0.45,
        confidence_threshold=38,
        reading_direction="ltr",
    ),
}


def get_preset(preset_name: str) -> OcrTuningProfile:
    """Получить пресет по имени. Если не найден — BALANCED."""
    return PRESETS.get(preset_name, PRESETS[ContentPreset.BALANCED.value])


def list_presets() -> List[OcrTuningProfile]:
    """Список всех пресетов."""
    return list(PRESETS.values())


# ═══════════════════════════════════════════════════════════════
# ИНИЦИАЛИЗАТОР ДВИЖКА
# ═══════════════════════════════════════════════════════════════

def create_engine(engine_id: str, settings: dict) -> Any:
    """Фабрика: создаёт экземпляр OCR-движка по ID.

    Возвращает объект с методом recognize(img) -> (text, confidence).
    """
    descriptor = get_engine_descriptor(engine_id)
    if not descriptor:
        logger.error(f"[Engines] Unknown engine: {engine_id}")
        return None

    if descriptor.engine_type == EngineType.TFLITE_CYRILLIC:
        return _create_cyrillic_onnx(settings)
    elif descriptor.engine_type == EngineType.EASYOCR:
        return _create_easyocr(settings)
    elif descriptor.engine_type == EngineType.GOOGLE_LENS:
        return _create_google_lens(settings)
    elif descriptor.engine_type == EngineType.RAPIDOCR:
        return _create_rapidocr(settings)
    elif descriptor.engine_type == EngineType.ZEN:
        return _create_zen(settings)
    else:
        logger.error(f"[Engines] No factory for engine: {engine_id}")
        return None


def _create_rapidocr(settings: dict):
    """Создать RapidOCR (локальный, самый быстрый).

    Движок работает в «умном» режиме: на узкой полосе идёт быстрый проход
    без детектора (~0.3 c), детектор подключается только если результат
    плохой. Число потоков ограничено, чтобы игра не теряла FPS.
    """
    try:
        from rapid_ocr import RapidOCREngine
        engine = RapidOCREngine(
            threads=settings.get("ocr.ocr_threads", 2),
            limit_side_len=settings.get("ocr.det_limit_side", 480),
            use_cls=False,
        )
        # recognize_auto: быстрый проход с добивкой детектором при необходимости
        engine.recognize = engine.recognize_auto
        logger.info("[Engines] RapidOCR initialized (быстрый режим)")
        return engine
    except ImportError:
        logger.error("[Engines] rapidocr_onnxruntime not installed")
        return None
    except Exception as e:
        logger.error(f"[Engines] RapidOCR error: {e}")
        return None


def _create_zen(settings: dict):
    """Создать Zen OCR (облачный, любые языки)."""
    try:
        from online_ocr import ZenOCR
        engine = ZenOCR(model=settings.get("ocr.zen_model", ""))
        logger.info(f"[Engines] Zen OCR initialized "
                    f"(model={engine.model or engine.DEFAULT_MODEL})")
        return engine
    except ImportError:
        logger.error("[Engines] online_ocr.py not found")
        return None
    except Exception as e:
        logger.error(f"[Engines] Zen OCR error: {e}")
        return None


def _create_cyrillic_onnx(settings: dict):
    """Создать CyrillicONNX движок.

    Приоритет:
    1. TFLite с кириллическими моделями PP-OCRv3+v5 (из yomihon-custom)
    2. RapidOCR (PaddleOCR ONNX) с пост-обработкой из ocr_text_cleaner

    Оба варианта используют одинаковый пайплайн пост-обработки.
    """
    # Попытка 1: TFLite движок (из yomihon-custom)
    try:
        from tflite_ocr import CyrillicTfliteOCR
        engine = CyrillicTfliteOCR()
        if engine.initialize():
            logger.info("[Engines] TFLite Cyrillic (PP-OCRv3+v5) initialized")
            return engine
        else:
            logger.warning("[Engines] TFLite init failed, trying RapidOCR")
    except ImportError:
        logger.info("[Engines] TFLite not available, trying RapidOCR")
    except Exception as e:
        logger.warning(f"[Engines] TFLite error: {e}, trying RapidOCR")

    # Попытка 2: RapidOCR (PaddleOCR ONNX)
    try:
        from rapid_ocr import RapidOCREngine
        engine = RapidOCREngine(lang="ru")
        if engine.initialize():
            logger.info("[Engines] TFLite Cyrillic (RapidOCR fallback) initialized")
            return engine
        else:
            logger.warning("[Engines] RapidOCR init failed")
    except ImportError:
        logger.error("[Engines] Neither TFLite nor RapidOCR available")
    except Exception as e:
        logger.error(f"[Engines] RapidOCR error: {e}")

    return None


def _create_easyocr(settings: dict):
    """Создать EasyOCR движок."""
    try:
        import easyocr
        lang_str = settings.get("ocr.language", "rus+eng")
        langs = []
        low = lang_str.lower()
        if "rus" in low or "ru" in low:
            langs.append("ru")
        if "eng" in low or "en" in low:
            langs.append("en")
        if "jpn" in low or "ja" in low:
            langs.append("ja")
        if not langs:
            langs = ["en"]
        use_gpu = settings.get("ocr.use_gpu", False)
        reader = easyocr.Reader(langs, gpu=use_gpu)
        logger.info(f"[Engines] EasyOCR initialized: langs={langs}, gpu={use_gpu}")
        return reader
    except ImportError:
        logger.error("[Engines] easyocr not installed")
        return None
    except Exception as e:
        logger.error(f"[Engines] EasyOCR error: {e}")
        return None


def _create_google_lens(settings: dict):
    """Создать Google Lens движок."""
    try:
        from online_ocr import GoogleLensOCR
        api_key = settings.get("ocr.online_api_key", "")
        engine = GoogleLensOCR(api_key=api_key)
        logger.info("[Engines] Google Lens initialized")
        return engine
    except ImportError:
        logger.error("[Engines] online_ocr.py not found")
        return None
    except Exception as e:
        logger.error(f"[Engines] Google Lens error: {e}")
        return None
