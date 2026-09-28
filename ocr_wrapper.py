"""
OCR Wrapper — тонкая обёртка над EasyOCR.
Без кривой предобработки, без обрезания слов.
Использует только EasyOCR напрямую.
"""
import asyncio
import logging
import os
import re
import time
import threading
from typing import Optional, Tuple

import mss
import numpy as np
from PIL import Image, ImageEnhance

from image_preprocessor import preprocess_for_ocr, PRESETS as IMG_PRESETS
from ocr_text_cleaner import (
    full_clean_pipeline, fix_lookalikes_per_word, join_line_hyphens,
    normalize_whitespace, filter_garbage_tokens, cyrillic_fitness,
    looks_like_dictionary_ramp, apply_known_corrections,
)
from ocr_engines import (
    EngineType, get_engine_descriptor, create_engine, fallback_chain,
    get_preset, list_presets, ContentPreset, OcrTuningProfile,
    ENGINE_REGISTRY,
)

logger = logging.getLogger(__name__)


def _preprocess_image(img: Image.Image, settings: dict) -> Image.Image:
    """Предобработка изображения для лучшего распознавания.
    Использует image_preprocessor с настройками из config."""
    if not img:
        return img

    if img.mode != "RGB":
        img = img.convert("RGB")

    # Получаем настройки предобработки
    preset = settings.get("ocr.image_preset", "auto")
    contrast = settings.get("ocr.contrast", 1.5)
    brightness = settings.get("ocr.brightness", 1.0)

    try:
        return preprocess_for_ocr(img, preset=preset, contrast=contrast, brightness=brightness)
    except Exception as e:
        logger.warning(f"[OCR] Image preprocessor error: {e}, falling back to basic")
        # Fallback: базовая обработка
        small = img.resize((50, 50)).convert("L")
        pixels = list(small.getdata())
        avg_brightness = sum(pixels) / len(pixels)
        if avg_brightness < 60:
            img = ImageEnhance.Contrast(img).enhance(1.5)
            img = ImageEnhance.Brightness(img).enhance(1.2)
        else:
            img = ImageEnhance.Contrast(img).enhance(1.8)
            img = ImageEnhance.Brightness(img).enhance(1.1)
        return img

# Ленивый английский спелл-чекер (инициализируется при первом использовании)
_spell_en = None
_spell_en_failed = False


def _get_spell_en():
    """Возвращает английский SpellChecker или None, если недоступен."""
    global _spell_en, _spell_en_failed
    if _spell_en is not None:
        return _spell_en
    if _spell_en_failed:
        return None
    try:
        from spellchecker import SpellChecker
        _spell_en = SpellChecker(language="en")
    except Exception as e:
        logger.warning(f"[OCR] pyspellchecker недоступен, коррекция EN отключена: {e}")
        _spell_en_failed = True
        return None
    return _spell_en


def _edit_distance(a: str, b: str) -> int:
    """Расстояние Левенштейна между строками."""
    if a == b:
        return 0
    la, lb = len(a), len(b)
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        cur = [i] + [0] * lb
        for j in range(1, lb + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
        prev = cur
    return prev[lb]

# ---------------------------------------------------------------------------
# Карты гомоглифов: визуально одинаковые буквы латиницы и кириллицы.
# EasyOCR в режиме ru+en часто подменяет их внутри одного слова
# (например "голосовых" -> "голocoвых" со скрытыми латинскими o/c).
# ---------------------------------------------------------------------------
# Латиница -> кириллица (для слов, которые в основном русские)
_LAT_TO_CYR = {
    "a": "а", "c": "с", "e": "е", "o": "о", "p": "р", "x": "х", "y": "у",
    "A": "А", "B": "В", "C": "С", "E": "Е", "H": "Н", "K": "К", "M": "М",
    "O": "О", "P": "Р", "T": "Т", "X": "Х", "Y": "У", "U": "У", "u": "у",
}
# Кириллица -> латиница (для слов, которые в основном английские)
_CYR_TO_LAT = {
    "а": "a", "с": "c", "е": "e", "о": "o", "р": "p", "х": "x", "у": "y",
    "А": "A", "В": "B", "С": "C", "Е": "E", "Н": "H", "К": "K", "М": "M",
    "О": "O", "Р": "P", "Т": "T", "Х": "X", "У": "Y",
}
# Буквы-гомоглифы (имеют двойника в другом алфавите)
_LAT_HOMOGLYPHS = set(_LAT_TO_CYR.keys())
_CYR_HOMOGLYPHS = set(_CYR_TO_LAT.keys())

# Заглавные буквы-гомоглифы (для коррекции случайного регистра внутри слова)
_LAT_HOMOGLYPH_CAPS = {c for c in _LAT_HOMOGLYPHS if c.isupper()}
_CYR_HOMOGLYPH_CAPS = {c for c in _CYR_HOMOGLYPHS if c.isupper()}

# Украинские буквы → русские (І→И и т.д.)
_UKR_TO_RUS = {"І": "И", "і": "и", "Ї": "Ы", "ї": "ы", "Є": "Е", "є": "е"}

# Путаница букв и цифр: I→1, O→0, l→1 когда окружены цифрами
_LETTER_DIGIT_CONFUSION = re.compile(r"(?<=\d)[IiOo](?=\d)")
_LETTER_DIGIT_CONFUSION_START = re.compile(r"^[IiOo](?=\d)")
_LETTER_DIGIT_CONFUSION_END = re.compile(r"(?<=\d)[IiOo]$")

# Шаблон "слова" из букв обоих алфавитов (+ дефис/апостроф внутри)
_WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё'\-]*")

# Точечные замены частых ошибок OCR для известных терминов (регистронезависимо).
# Ключ — ошибочно распознанное слово (lower), значение — правильное.
# Применяется ДО спелл-чекера, чтобы гарантированно чинить спец-слова,
# которых нет в обычном словаре (горячие клавиши, бренды и т.п.).
_KNOWN_CORRECTIONS = {
    "curl": "Ctrl",
    "ctri": "Ctrl",
    "cirl": "Ctrl",
    "alf": "Alt",
    "shifl": "Shift",
    "shlft": "Shift",
    "enler": "Enter",
    "esc": "Esc",
    "dei": "Del",
}

# Merge split words: "Silent lyCont inue" -> "SilentlyContinue"
_SPLIT_WORD_FIXES = [
    ("Silent lyCont inue", "SilentlyContinue"),
    ("Silent lyContinue", "SilentlyContinue"),
    ("ilen!Continue", "SilentlyContinue"),
    ("ilen Continue", "SilentlyContinue"),
    ("illyContinue", "SilentlyContinue"),
    ("lyCont inue", "lyContinue"),
    ("Silently Continue", "SilentlyContinue"),
    ("Where Object", "Where-Object"),
    ("Where-Object", "Where-Object"),
    ("Idz Ma inVindo", "Id, MainWindow"),
    ("Ma inVindo", "MainWindow"),
    ("inVindo", "Window"),
    ("inVindow", "Window"),
    ("Argu mentList", "ArgumentList"),
    ("Argu ment", "Argument"),
    ("ErrorAction", "ErrorAction"),
    ("Silently Continue", "SilentlyContinue"),
    ("Stop Process", "Stop-Process"),
    ("Get Process", "Get-Process"),
    ("Start Process", "Start-Process"),
    ("Start Sleep", "Start-Sleep"),
    ("Select Object", "Select-Object"),
]

# Clean single-char garbage that appears in split words
_GARBAGE_CHARS = re.compile(r"(?<![A-Za-z])[nz](?![A-Za-z])")


def _merge_split_words(text: str) -> str:
    """Merge words split by OCR into correct compound words."""
    for wrong, right in _SPLIT_WORD_FIXES:
        text = text.replace(wrong, right)
    return text


def _clean_garbage_chars(text: str) -> str:
    """Remove random single characters inserted by OCR (like isolated 'n', 'z')."""
    # Only remove isolated 'n' or 'z' that are clearly garbage (surrounded by spaces)
    # More conservative: only remove if it's a single char between two spaces
    text = re.sub(r"(?<= ) [nz](?= )", "", text)
    return text

_RUSSIAN_OCR_FIXES = {
    "тадовати": "Тадовати",
    "уведомлянив": "Уведомление",
    "уведомляниие": "Уведомление",
    "уведомлениe": "Уведомление",
    "уведомленик": "Уведомление",
    "уведомленикв": "Уведомление",
    "укровой": "Укровый",
    "крговая": "Круговая",
    "кргова": "Круговая",
    "магнит": "Магнит",
    "магнbt": "Магнит",
    "ярмарка": "Ярмарка",
    "ярмapка": "Ярмарка",
    "отрdd": "Отряд",
    "дрyзья": "Друзья",
    "связb": "Связь",
    "систeма": "Система",
    "врeмени": "Времени",
    "собыmие": "Событие",
    "собыmие": "Событие",
    "рamarka": "Ярмарка",
    "проmь": "Промы",
    "врeмя": "Время",
    "нaграды": "Награды",
    "нaгpaды": "Награды",
    "мaccия": "Миссия",
    "мисcия": "Миссия",
    "мисccия": "Миссия",
    "хозяuна": "Хозяина",
    "хозяuн": "Хозяин",
    "охотnика": "Охотника",
    "охотнuка": "Охотника",
}

_KNOWN_RE = re.compile(r"[A-Za-z]+")


def _apply_known_corrections(text: str) -> str:
    """Точечная замена известных ошибочно распознанных терминов."""
    if not text:
        return text

    def repl(m: "re.Match") -> str:
        w = m.group(0)
        fixed = _KNOWN_CORRECTIONS.get(w.lower())
        return fixed if fixed else w

    return _KNOWN_RE.sub(repl, text)


def _apply_russian_fixes(text: str) -> str:
    """Исправление типичных ошибок OCR для русского языка."""
    if not text:
        return text
    lines = text.split("\n")
    result = []
    for line in lines:
        words = line.split()
        fixed_words = []
        for w in words:
            w_lower = w.lower().strip(".,!?;:")
            if w_lower in _RUSSIAN_OCR_FIXES:
                fixed_words.append(_RUSSIAN_OCR_FIXES[w_lower])
            else:
                fixed_words.append(w)
        result.append(" ".join(fixed_words))
    return "\n".join(result)


def _cleanup_punctuation(text: str) -> str:
    """Очистка пробелов вокруг знаков препинания и переносов строк."""
    if not text:
        return text
    # Пробелы вокруг \n
    text = re.sub(r"\s*\n\s*", "\n", text)
    # Убираем пробел перед знаками препинания , . ! ? : ; ) ] »
    text = re.sub(r"\s+([,.!?:;)\]»])", r"\1", text)
    # Убираем пробел после открывающих скобок/кавычек
    text = re.sub(r"([(\[«])\s+", r"\1", text)
    # Схлопываем множественные пробелы
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def _fix_digit_letter_confusion(text: str) -> str:
    """Исправление путаницы букв и цифр: I→1, O→0 когда окружены цифрами.

    EasyOCR часто читает цифру 1 как букву I, а 0 как букву O
    (например "4I50" вместо "4150").
    """
    if not text:
        return text

    def _replace_letter_digit(m: "re.Match") -> str:
        ch = m.group(0)
        if ch in ("I", "i"):
            return "1"
        elif ch in ("O", "o"):
            return "0"
        return ch

    text = _LETTER_DIGIT_CONFUSION.sub(_replace_letter_digit, text)
    text = _LETTER_DIGIT_CONFUSION_START.sub(_replace_letter_digit, text)
    text = _LETTER_DIGIT_CONFUSION_END.sub(_replace_letter_digit, text)
    return text


def _strip_ocr_edge_garbage(text: str) -> str:
    """Убирает мусорные префиксы/суффиксы с краёв OCR текста (артефакты границ скана)."""
    if not text:
        return text
    # Мусорные префиксы: одиночные символы, скобки, кавычки, спецсимволы
    text = re.sub(r'^[!"\'`~@\#\$%\^&\*\(\)\[\]\{\}<>/\\|;:=\+\-,\.]\s*', '', text)
    text = re.sub(r'\s*[!"\'`~@\#\$%\^&\*\(\)\[\]\{\}<>/\\|;:=\+\-,\.]$', '', text)
    # Убираем обрывки слов с нечитаемыми символами в начале/конце
    text = re.sub(r'^\S*[^\w\sа-яёА-ЯЁ]\S*\s+', ' ', text)
    text = re.sub(r'\s+\S*[^\w\sа-яёА-ЯЁ]\S*$', '', text)
    return text.strip()


def _filter_ocr_garbage(text: str) -> str:
    """Удаляет строки, состоящие из мусора OCR (отдельные символы, символы-мусор, артефакты иконок, стикеры/эмодзи)."""
    if not text:
        return text
    lines = text.split("\n")
    filtered = []
    # Мусорные символы которые EasyOCR иногда генерирует
    garbage_chars = set("|~`{}<>[]\\^_")
    # Известные артефакты чтения иконок UI (шестерёнка, кнопки и т.д.)
    # Однобуквенные строки — почти всегда мусор от иконок
    # (кроме "I" и "a" — они бывают в английском тексте)
    icon_artifacts = {
        "d", "c", "e", "x", "v", "r", "n", "i", "l", "k",
        "b", "f", "g", "h", "j", "m", "o", "p", "q", "s",
        "t", "u", "w", "z",
        "D", "C", "E", "X", "V", "R", "N", "L", "K", "B",
        "F", "G", "H", "J", "M", "O", "P", "Q", "S", "T",
        "U", "W", "Z", "Y",
        "d.", "c.", "x.", "v.", "r.", "n.", "i.", "l.", "k.",
        "dd", "cc", "vv", "rr", "nn", "ii", "ll", "kk",
        "tt", "ff", "ss", "mm", "bb", "pp",
    }
    # Unicode диапазоны для эмодзи и стикеров
    _EMOJI_RE = re.compile(
        "["
        "\U0001F600-\U0001F64F"  # emoticons
        "\U0001F300-\U0001F5FF"  # symbols & pictographs
        "\U0001F680-\U0001F6FF"  # transport & map
        "\U0001F1E0-\U0001F1FF"  # flags
        "\U00002702-\U000027B0"  # dingbats
        "\U000024C2-\U0001F251"  # enclosed chars
        "\U0001f926-\U0001f937"  # extra emoticons
        "\U00010000-\U0010ffff"  # supplementary
        "\u200d"                 # ZWJ
        "\u2640-\u2642"          # gender
        "\u2600-\u2B55"          # misc symbols
        "\u23cf"
        "\u23e9-\u23f3"
        "\u23f8-\u23fa"
        "\u2934-\u2935"
        "\u25aa-\u25ab"
        "\u25b6"
        "\u25c0"
        "\u25fb-\u25fe"
        "\u2614-\u2615"
        "\u2648-\u2653"
        "\u267f"
        "\u2693"
        "\u26a1"
        "\u26aa-\u26ab"
        "\u26bd-\u26be"
        "\u26c4-\u26c5"
        "\u26ce"
        "\u26d4"
        "\u26ea"
        "\u26f2-\u26f3"
        "\u26f5"
        "\u26fa"
        "\u26fd"
        "\u2702"
        "\u2705"
        "\u2708-\u270d"
        "\u270f"
        "\u2712"
        "\u2714"
        "\u2716"
        "\u271d"
        "\u2721"
        "\u2728"
        "\u2733-\u2734"
        "\u2744"
        "\u2747"
        "\u274c"
        "\u274e"
        "\u2753-\u2755"
        "\u2757"
        "\u2763-\u2764"
        "\u2795-\u2797"
        "\u27a1"
        "\u27b0"
        "\u27bf"
        "\u2934-\u2935"
        "\u2b05-\u2b07"
        "\u2b1b-\u2b1c"
        "\u2b50"
        "\u2b55"
        "\u3030"
        "\u303d"
        "\u3297"
        "\u3299"
        "]+",
        re.UNICODE,
    )
    for line in lines:
        stripped = line.strip()
        if not stripped:
            filtered.append("")
            continue
        # Убираем строки длиной 1-2 символа (мусор)
        clean_chars = [c for c in stripped if c.isalnum() or c in ".,!?;:-'\"() "]
        if len(clean_chars) <= 2 and not stripped[0].isalnum():
            continue
        # Убираем строки из 1-2 символов — артефакты иконок
        if len(stripped) <= 2:
            continue
        # Убираем однобуквенные строки которые не являются реальным текстом
        # (одна буква + точка или просто одна буква = мусор от иконок)
        if len(stripped) <= 3 and stripped.rstrip(".").isalpha() and stripped.rstrip(".") in icon_artifacts:
            continue
        # Убираем строки из 2-3 небуквенных символов
        alpha_count = sum(1 for c in stripped if c.isalpha())
        if alpha_count < 2 and len(stripped) <= 5:
            continue
        # Убираем строки где больше мусорных символов чем букв
        junk_count = sum(1 for c in stripped if c in garbage_chars)
        if junk_count > alpha_count and alpha_count < 4:
            continue
        # Убираем строки с нечитаемыми символами (японские, китайские, армянские)
        forbidden_blocks = set(
            "぀ぁあぃいぅうぇえぉおかがきぎくぐけげこごたちっつっててとどなにぬぐねのはばひびふぶへべほまみむめもやゆよらりるれろををんァアィイゥウェエォオカガキギクグケゲコゴサザシジスズセゼソゾタダチヂツヅテデトドナニヌネノハバパヒビプフブペホボポマミムメモャヤラリルレロワヲン"
        )
        if any(b in stripped for b in forbidden_blocks):
            continue
        # Убираем однобуквенный префикс от иконки (например "d Настройки" -> "Настройки")
        if len(stripped) > 2:
            first_word = stripped.split()[0] if stripped.split() else ""
            rest = stripped[len(first_word):].strip() if len(stripped) > len(first_word) else ""
            if (len(first_word) <= 2 and first_word.rstrip(".").lower() in icon_artifacts
                    and rest and len(rest) >= 2):
                stripped = rest
        # Убираем строки с смешанным алфавитом (равные части - мусор)
        has_cyr = bool(re.search(r"[а-яёІЇЄіїє]", stripped, re.IGNORECASE))
        has_lat = bool(re.search(r"[a-z]", stripped))
        has_arab = bool(re.search(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]", stripped))  # арабский
        has_chi = bool(re.search(r"[\u4e00-\u9fff\u3400-\u4dbf]", stripped))  # китайский
        if has_arab or has_chi:
            continue  # арабский/китайский часто мусор
        if has_cyr and has_lat and alpha_count >= 3:
            cyr_letters = len(re.findall(r"[а-яё]", stripped, re.IGNORECASE))
            lat_letters = len(re.findall(r"[a-z]", stripped))
            if abs(cyr_letters - lat_letters) <= 2:
                continue
        # Убираем строки с цифрами внутри кириллических слов (Е9е, 51его, 5АР)
        if re.search(r"[а-яё]\d[а-яё]", stripped, re.IGNORECASE):
            continue
        if re.search(r"\d[а-яё]\d", stripped, re.IGNORECASE):
            continue
        # Убираем строки с мусорными символами внутри слов (гпъ_се, ~ек5а)
        words = stripped.split()
        garbage_word_count = 0
        for w in words:
            if re.search(r"[~_{}\[\]\\]", w) or (len(re.findall(r"[^\w\s]", w)) > 1):
                garbage_word_count += 1
        if garbage_word_count > 0 and garbage_word_count >= len(words) * 0.5:
            continue
        # Убираем строки которые состоят преимущественно из эмодзи/стикеров
        emoji_chars = len(_EMOJI_RE.findall(stripped))
        if emoji_chars > 0 and alpha_count < 3:
            continue
        filtered.append(stripped)
    return "\n".join(filtered)


_PUNCTUATION_RE = re.compile(r"[,.!?:;—–\-\"\'»)\]>]")


def _add_punctuation_to_long_words(text: str) -> str:
    """Добавляет точки между длинными словами, если в строке нет знаков препинания.

    Когда OCR распознаёт сплошной текст без пунктуации, TTS читает его
    слитно без пауз. Добавляем точки между длинными словами (>5 букв),
    чтобы TTS делал паузы между ними.
    """
    if not text:
        return text
    lines = text.split("\n")
    result = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            result.append("")
            continue
        # Если в строке уже есть знаки препинания — не трогаем
        if _PUNCTUATION_RE.search(stripped):
            result.append(stripped)
            continue
        words = stripped.split()
        if len(words) < 3:
            result.append(stripped)
            continue
        # Считаем среднюю длину слов
        avg_len = sum(len(w) for w in words) / len(words)
        # Если слова длинные (средняя > 5 букв) — добавляем точки
        if avg_len >= 5:
            new_words = []
            for i, w in enumerate(words):
                new_words.append(w)
                # Добавляем точку после длинного слова (кроме последнего)
                if i < len(words) - 1 and len(w) > 5:
                    new_words[-1] = w + "."
            stripped = " ".join(new_words)
        result.append(stripped)
    return "\n".join(result)


class OCRWrapper:
    """Минимальная обёртка над OCR для распознавания текста с экрана.
    Поддерживает EasyOCR, RapidOCR и онлайн-движок Zen (free, без ключа)."""

    def __init__(self, settings):
        self.settings = settings
        self.language = settings.get("ocr.language", "rus+eng")
        self.confidence_threshold = settings.get("ocr.confidence_threshold", 60)
        self.region = settings.get("ocr.region")
        self.use_gpu = settings.get("ocr.use_gpu", False)

        self.easyocr_reader = None
        self.rapidocr_engine = None
        self.online_ocr_engine = None
        self.engine_type = settings.get("ocr.engine", "google_lens")
        # Backward compat: old id renamed to tflite_cyrillic
        if self.engine_type == "cyrillic_onnx":
            self.engine_type = "tflite_cyrillic"
        logger.info(f"[OCR] Engine type from config: {self.engine_type}")

        # Пресет контента
        self._content_preset = settings.get("ocr.content_preset", "balanced")
        self._tuning = get_preset(self._content_preset)
        logger.info(f"[OCR] Content preset: {self._content_preset}")

        # Цепочка fallback
        self._fallback_engines = fallback_chain(self.engine_type)
        self._active_engine_id = self.engine_type
        self._active_engine = None
        # Кэш TFLite-движка: создание интерпретаторов на каждый скан
        # видно в логах как повторные "[TfliteOCR] Initialized" и тормозит.
        self._tflite_engine = None
        self._tflite_lock = threading.Lock()

        # Онлайн OCR настройки.
        # OrcaRouter удалён (endpoint 404), OpenRouter отклоняет ключ (403).
        # Из бесплатных без ключа остаётся Zen (opencode.ai).
        self._online_engine_name = settings.get("ocr.online_engine", "zen")
        self._online_api_key = settings.get("ocr.online_api_key", "")
        self._online_model = settings.get("ocr.online_model", "")

        # Директория для скриншотов
        self._screenshots_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "screenshots")
        os.makedirs(self._screenshots_dir, exist_ok=True)
        self._screenshot_counter = 0
        self._last_screenshot = None

        # Языковой фильтр
        self._lang_filter = self._parse_lang_filter(self.language)

        # Захват окна приложения
        self._target_hwnd = None
        self._target_title = ""

        self._init_engines()

    def _init_engines(self):
        """Инициализация движков OCR с fallback-цепочкой."""
        # Пытаемся инициализировать основной движок
        engine = create_engine(self.engine_type, self.settings.__dict__ if hasattr(self.settings, '__dict__') else self.settings)
        if engine is not None:
            self._active_engine = engine
            self._active_engine_id = self.engine_type
            logger.info(f"[OCR] Primary engine initialized: {self.engine_type}")
            return

        # Fallback: пробуем другие движки из цепочки
        for eng_id in self._fallback_engines:
            if eng_id == self.engine_type:
                continue
            desc = get_engine_descriptor(eng_id)
            if desc and desc.requires_network and not self._check_network():
                continue
            engine = create_engine(eng_id, self.settings.__dict__ if hasattr(self.settings, '__dict__') else self.settings)
            if engine is not None:
                self._active_engine = engine
                self._active_engine_id = eng_id
                logger.info(f"[OCR] Fallback engine initialized: {eng_id}")
                return

        logger.error("[OCR] No engine could be initialized!")
        self._active_engine = None

    def _check_network(self) -> bool:
        """Проверка доступности сети."""
        try:
            import urllib.request
            urllib.request.urlopen("https://www.google.com", timeout=3)
            return True
        except Exception:
            return False

    def _init_online_ocr(self):
        """Инициализация онлайн OCR движка (legacy)."""
        try:
            from online_ocr import create_online_ocr
            self.online_ocr_engine = create_online_ocr(
                engine=self.engine_type,
                api_key=self._online_api_key,
                model=self._online_model,
            )
            logger.info(f"[OCR] Online OCR инициализирован: {self.engine_type}")
        except ImportError:
            logger.warning("[OCR] online_ocr.py not found, falling back to EasyOCR")
            self.engine_type = "easyocr"
            self._init_easyocr()
        except Exception as e:
            logger.error(f"[OCR] Online OCR init error: {e}, falling back to EasyOCR")
            self.engine_type = "easyocr"
            self._init_easyocr()

    def _init_rapidocr(self):
        """Инициализация RapidOCR (PaddleOCR via ONNX) (legacy)."""
        try:
            from rapid_ocr import RapidOCREngine
            lang_code = "en" if "eng" in self.language.lower() else self.language.split("+")[0][:2]
            self.rapidocr_engine = RapidOCREngine(lang=lang_code)
            if self.rapidocr_engine.initialize():
                logger.info(f"[OCR] RapidOCR инициализирован: lang={lang_code}")
            else:
                logger.warning("[RapidOCR] Init failed, falling back to EasyOCR")
                self.engine_type = "easyocr"
                self._init_easyocr()
        except ImportError:
            logger.warning("[RapidOCR] rapid_ocr.py not found, using EasyOCR")
            self.engine_type = "easyocr"
            self._init_easyocr()
        except Exception as e:
            logger.error(f"[RapidOCR] Init error: {e}, falling back to EasyOCR")
            self.engine_type = "easyocr"
            self._init_easyocr()

    def _init_easyocr(self):
        """Инициализация EasyOCR (legacy)."""
        try:
            import easyocr

            langs = self._resolve_langs(self.language)
            logger.info(f"[OCR] Инициализация EasyOCR: langs={langs}, gpu={self.use_gpu}")
            self.easyocr_reader = easyocr.Reader(langs, gpu=self.use_gpu)
            logger.info("[OCR] EasyOCR инициализирован")
        except ImportError:
            logger.error("[OCR] easyocr не установлен! pip install easyocr")
        except Exception as e:
            logger.error(f"[OCR] Ошибка инициализации EasyOCR: {e}")

    def _resolve_langs(self, language: str) -> list:
        """Преобразование строки языка в список кодов для EasyOCR."""
        langs = []
        low = language.lower()
        if "rus" in low or "ru" in low:
            langs.append("ru")
        if "eng" in low or "en" in low:
            langs.append("en")
        if "jpn" in low or "ja" in low or "яп" in low:
            langs.append("ja")
        if not langs:
            langs = ["en"]
        return langs

    def _parse_lang_filter(self, language: str) -> set:
        """Парсинг строки языка в множество символов для фильтрации."""
        chars = set()
        lang_lower = language.lower()
        if "rus" in lang_lower or "ru" in lang_lower:
            # Кириллица: А-Яа-яёЁ
            for c in range(0x0400, 0x0500):
                chars.add(chr(c))
        if "eng" in lang_lower or "en" in lang_lower:
            # Латиница: A-Za-z
            for c in range(0x0041, 0x005B):
                chars.add(chr(c))
            for c in range(0x0061, 0x007B):
                chars.add(chr(c))
        if "jpn" in lang_lower or "ja" in lang_lower or "яп" in lang_lower:
            # Хирагана
            for c in range(0x3040, 0x309F + 1):
                chars.add(chr(c))
            # Катакана
            for c in range(0x30A0, 0x30FF + 1):
                chars.add(chr(c))
            # Кандзи (CJK Unified Ideographs)
            for c in range(0x4E00, 0x9FFF + 1):
                chars.add(chr(c))
            # Японская пунктуация
            for c in range(0x3000, 0x303F + 1):
                chars.add(chr(c))
        return chars

    def _filter_by_language(self, text: str) -> str:
        """Фильтрация текста: оставляем только символы нужных языков + цифры + пробелы."""
        if not text or not self._lang_filter:
            return text
        filtered = []
        for char in text:
            if char in self._lang_filter or char.isdigit() or char in ' .,!?;:-\'"()—–':
                filtered.append(char)
            elif char == '\n':
                filtered.append(char)
        result = ''.join(filtered).strip()
        # Если после фильтрации пусто — возвращаем исходный текст
        # (чтобы не терять данные, а фильтрация мусора идёт отдельно)
        if not result and text.strip():
            return text.strip()
        return result

    def _save_screenshot(self, img: Image.Image):
        """Сохранение скриншота в папку screenshots/ для анализа."""
        try:
            self._screenshot_counter += 1
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            filename = f"scan_{timestamp}_{self._screenshot_counter:04d}.png"
            filepath = os.path.join(self._screenshots_dir, filename)
            img.save(filepath, "PNG")
            logger.debug(f"[SCREENSHOT] Saved: {filename}")
        except Exception as e:
            logger.warning(f"[SCREENSHOT] Failed to save: {e}")

    def capture_region(self) -> Optional[Image.Image]:
        """Захват области экрана (рамки) или окна приложения."""
        # Если задана область (рамка) — захватываем только её
        # (неважно, выбрано окно или нет — рамка приоритетнее)
        if self.region:
            try:
                with mss.mss() as sct:
                    monitor = {
                        "left": self.region["x"],
                        "top": self.region["y"],
                        "width": self.region["width"],
                        "height": self.region["height"],
                    }
                    screenshot = sct.grab(monitor)
                    img = Image.frombytes(
                        "RGB", screenshot.size, screenshot.bgra, "raw", "BGRX"
                    )
                    return img
            except Exception as e:
                logger.error(f"[OCR] Ошибка захвата области: {e}")
                return None

        # Нет области — захватываем выбранное окно целиком
        if self._target_hwnd:
            return self._capture_window(self._target_hwnd)

        logger.error("[OCR] Область захвата не установлена и окно не выбрано")
        return None

    def select_window(self, hwnd: int):
        """Выбрать окно приложения для захвата."""
        import ctypes
        user32 = ctypes.windll.user32
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        self._target_hwnd = hwnd
        self._target_title = buf.value
        logger.info(f"[OCR] Выбрано окно: '{self._target_title}' (hwnd={hwnd})")

    def clear_window(self):
        """Сбросить выбор окна — вернуться к захвату области."""
        self._target_hwnd = None
        self._target_title = ""
        logger.info("[OCR] Выбор окна сброшен")

    def _capture_window(self, hwnd: int) -> Optional[Image.Image]:
        """Захват содержимого окна по HWND."""
        import ctypes
        import ctypes.wintypes
        try:
            user32 = ctypes.windll.user32
            rect = ctypes.wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            x, y = rect.left, rect.top
            w = rect.right - rect.left
            h = rect.bottom - rect.top
            if w <= 0 or h <= 0:
                logger.warning(f"[OCR] Окно невидимо: {self._target_title}")
                return None
            with mss.mss() as sct:
                monitor = {"left": x, "top": y, "width": w, "height": h}
                screenshot = sct.grab(monitor)
                img = Image.frombytes("RGB", screenshot.size, screenshot.bgra, "raw", "BGRX")
                return img
        except Exception as e:
            logger.error(f"[OCR] Ошибка захвата окна: {e}")
            return None

    @staticmethod
    def list_windows():
        """Получить список видимых окон для выбора."""
        import ctypes
        from ctypes import wintypes as wt
        user32 = ctypes.windll.user32
        results = []
        ENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
        def callback(hwnd, _):
            if user32.IsWindowVisible(hwnd):
                length = user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    buf = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buf, length + 1)
                    title = buf.value
                    if title and "IME" not in title and len(title) > 2:
                        results.append((hwnd, title))
            return True
        user32.EnumWindows(ENUMPROC(callback), 0)
        return results

    def capture_full_screen(self) -> Optional[Image.Image]:
        """Захват всего экрана."""
        try:
            with mss.mss() as sct:
                monitor = sct.monitors[1]
                screenshot = sct.grab(monitor)
                return Image.frombytes(
                    "RGB", screenshot.size, screenshot.bgra, "raw", "BGRX"
                )
        except Exception as e:
            logger.error(f"[OCR] Ошибка захвата экрана: {e}")
            return None

    def _multi_pass_ocr(self, img: Image.Image) -> tuple:
        """Multi-pass OCR: пробует разные preprocessing, выбирает лучший пресет."""
        presets = [
            ("none", {}),
            ("sharpen", {"contrast": 1.5}),
            ("contrast", {"contrast": 2.0}),
            ("threshold", {"contrast": 2.5}),
            ("invert", {}),
        ]

        best_text = ""
        best_conf = 0.0
        best_preset = "none"

        for preset_name, extra_settings in presets:
            try:
                test_img = img.copy()
                settings_copy = dict(self.settings.get_all()) if hasattr(self.settings, 'get_all') else {}
                settings_copy["ocr.image_preset"] = preset_name
                settings_copy.update(extra_settings)

                processed = _preprocess_image(test_img, settings_copy)
                img_array = np.array(processed)

                results = self.easyocr_reader.readtext(
                    img_array, detail=1, paragraph=False,
                    min_size=5, contrast_ths=0.05, text_threshold=0.5,
                    decoder="greedy", beamWidth=1,
                )

                if not results:
                    continue

                # Считаем средний confidence и собираем текст
                texts = []
                confs = []
                for item in results:
                    if len(item) == 3:
                        bbox, text, conf = item
                        if conf * 100 >= self.confidence_threshold:
                            texts.append(text)
                            confs.append(conf)

                if texts:
                    avg_conf = sum(confs) / len(confs)
                    combined = ' '.join(texts)
                    # Выбираем пресет с лучшим confidence
                    if avg_conf > best_conf:
                        best_conf = avg_conf
                        best_text = combined
                        best_preset = preset_name
                    logger.debug(f"[OCR] Preset '{preset_name}': {len(texts)} blocks, conf={avg_conf:.2f}")

            except Exception as e:
                logger.debug(f"[OCR] Preset '{preset_name}' failed: {e}")

        if not best_text:
            return "", 0.0

        # Обрабатываем лучший результат
        text = _merge_split_words(best_text)
        text = _clean_garbage_chars(text)
        text = _cleanup_punctuation(text)
        text = _fix_digit_letter_confusion(text)
        text = _filter_ocr_garbage(text)
        text = self._filter_by_language(text)
        text = self._normalize_alphabets(text)

        avg_conf = best_conf * 100
        logger.info(f"[OCR] Multi-pass best: '{best_preset}' ({len(text)} chars, {avg_conf:.0f}%): {text[:120]}")
        return text, avg_conf

    def recognize(self, img: Image.Image = None) -> tuple:
        """Распознавание текста через OCR. Возвращает (text, confidence).

        Поддерживает fallback-цепочку: если основной движок не справляется,
        пробует следующий из цепочки.
        """
        try:
            if img is None:
                logger.info("[OCR] Захват области экрана...")
                img = self.capture_region()
                if img is None:
                    return "", 0.0

            # Сохранение скриншота для анализа
            self._save_screenshot(img)
            self._last_screenshot = img

            # Применяем пресет контента к изображению
            img = self._apply_content_preset(img)

            # Пробуем основной движок
            text, conf = self._try_recognize(img, self._active_engine_id)

            # Fallback: если результат пустой или мусор — пробуем другие движки
            if not text or len(text.strip()) < 2:
                for eng_id in self._fallback_engines:
                    if eng_id == self._active_engine_id:
                        continue
                    desc = get_engine_descriptor(eng_id)
                    if desc and desc.requires_network and not self._check_network():
                        continue
                    logger.info(f"[OCR] Fallback to: {eng_id}")
                    text, conf = self._try_recognize(img, eng_id)
                    if text and len(text.strip()) >= 2:
                        break

            return text, conf

        except Exception as e:
            logger.error(f"[OCR] Ошибка распознавания: {e}")
            return "", 0.0

    def _apply_content_preset(self, img: Image.Image) -> Image.Image:
        """Применяет пресет контента к изображению (контраст, яркость)."""
        if not self._tuning:
            return img
        if self._tuning.contrast != 1.0:
            img = ImageEnhance.Contrast(img).enhance(self._tuning.contrast)
        if self._tuning.brightness != 1.0:
            img = ImageEnhance.Brightness(img).enhance(self._tuning.brightness)
        return img

    def _try_recognize(self, img: Image.Image, engine_id: str) -> tuple:
        """Распознавание через конкретный движок. Возвращает (text, confidence)."""
        desc = get_engine_descriptor(engine_id)
        if not desc:
            return "", 0.0

        if desc.engine_type == EngineType.TFLITE_CYRILLIC:
            return self._recognize_tflite_cyrillic(img)
        elif desc.engine_type == EngineType.EASYOCR:
            return self._recognize_easyocr(img)
        elif desc.engine_type == EngineType.GOOGLE_LENS:
            return self._recognize_google_lens(img)
        return "", 0.0

    def _recognize_tflite_cyrillic(self, img: Image.Image) -> tuple:
        """Распознавание через TFLite Cyrillic (PP-OCRv3+v5).

        Движок использует тот же пайплайн что и yomihon-custom:
        - PP-OCRv4 детектор (736x736, connected-component BFS, тайлинг)
        - Word splitting (вертикальная проекция + Otsu)
        - Двойной распознаватель: v3 primary + v5 verifier
        - CTC softmax + coverage penalty
        """
        try:
            with self._tflite_lock:
                if self._tflite_engine is None:
                    self._tflite_engine = create_engine("tflite_cyrillic", self.settings.__dict__ if hasattr(self.settings, '__dict__') else self.settings)
                engine = self._tflite_engine
                if engine is None:
                    return "", 0.0
                text, conf = engine.recognize(img)
            if text:
                text = self._filter_by_language(text)
            return text, conf
        except Exception as e:
            logger.error(f"[OCR] TFLite Cyrillic error: {e}")
            return "", 0.0

    def _recognize_easyocr(self, img: Image.Image) -> tuple:
        """Распознавание через EasyOCR."""
        try:
            engine = create_engine("easyocr", self.settings.__dict__ if hasattr(self.settings, '__dict__') else self.settings)
            if engine is None:
                return "", 0.0

            # Апскейл для мелкого текста
            w, h = img.size
            if w < 1000 or h < 400:
                img = img.resize((w * 2, h * 2), Image.LANCZOS)

            img_array = np.array(img)
            results = engine.readtext(
                img_array, detail=1, paragraph=False,
                min_size=self._tuning.min_size if self._tuning else 5,
                contrast_ths=self._tuning.contrast_ths if self._tuning else 0.05,
                text_threshold=self._tuning.text_threshold if self._tuning else 0.5,
                decoder="greedy", beamWidth=1,
            )

            if not results:
                return "", 0.0

            filtered = []
            confidences = []
            for item in results:
                if len(item) == 3:
                    bbox, text, conf = item
                    if conf * 100 >= self.confidence_threshold:
                        filtered.append((bbox, text))
                        confidences.append(conf)

            if not filtered:
                return "", 0.0

            text = self._assemble_reading_order(filtered)
            # Полный пайплайн пост-обработки
            text = full_clean_pipeline(text, engine_type="easyocr")
            text = self._filter_by_language(text)
            text = self._normalize_alphabets(text)
            text = self._strip_isolated_latin(text)
            text = self._correct_english(text)
            text = apply_known_corrections(text)

            avg_conf = sum(confidences) / len(confidences) * 100 if confidences else 0
            return text, avg_conf
        except Exception as e:
            logger.error(f"[OCR] EasyOCR error: {e}")
            return "", 0.0

    def _recognize_google_lens(self, img: Image.Image) -> tuple:
        """Распознавание через Google Lens."""
        try:
            engine = create_engine("google_lens", self.settings.__dict__ if hasattr(self.settings, '__dict__') else self.settings)
            if engine is None:
                return "", 0.0
            text = engine.recognize(img)
            if text:
                text = full_clean_pipeline(text, engine_type="google_lens")
                text = self._filter_by_language(text)
                return text, 95.0
            return "", 0.0
        except Exception as e:
            logger.error(f"[OCR] Google Lens error: {e}")
            return "", 0.0

    def _assemble_reading_order(self, filtered) -> str:
        """Собирает текст в естественном порядке чтения (как на экране).

        Блоки группируются в визуальные строки: два блока относятся к одной
        строке, если их Y-центры близки (в пределах половины высоты строки).
        Строки сортируются сверху вниз, блоки внутри строки — слева направо.
        Между строками вставляется перенос.
        """
        if not filtered:
            return ""

        # Фильтруем мусорные блоки (1-2 небуквенных символа)
        clean_filtered = []
        for bbox, text in filtered:
            stripped = text.strip()
            # Убираем блоки из 1-2 символов (мусор)
            alpha_count = sum(1 for c in stripped if c.isalpha())
            if alpha_count < 2 and len(stripped) <= 3:
                continue
            clean_filtered.append((bbox, text))
        filtered = clean_filtered

        if not filtered:
            return ""

        # Вычисляем метрики каждого блока
        items = []
        heights = []
        for bbox, text in filtered:
            ys = [p[1] for p in bbox]
            xs = [p[0] for p in bbox]
            top = min(ys)
            bottom = max(ys)
            left = min(xs)
            yc = (top + bottom) / 2.0
            h = bottom - top
            heights.append(h)
            items.append({"text": text, "yc": yc, "left": left, "h": h, "top": top})

        median_h = float(np.median(heights)) if heights else 12.0
        # Допуск принадлежности к одной строке (половина высоты строки)
        y_tol = max(8.0, median_h * 0.6)

        # Сортируем по вертикали, затем формируем строки
        items.sort(key=lambda it: it["yc"])
        lines = []  # каждая строка: {"yc": float, "blocks": [item, ...]}
        for it in items:
            placed = False
            for line in lines:
                if abs(it["yc"] - line["yc"]) <= y_tol:
                    line["blocks"].append(it)
                    # обновляем средний Y строки
                    n = len(line["blocks"])
                    line["yc"] = sum(b["yc"] for b in line["blocks"]) / n
                    placed = True
                    break
            if not placed:
                lines.append({"yc": it["yc"], "blocks": [it]})

        # Сортируем строки сверху вниз, блоки внутри — слева направо
        lines.sort(key=lambda ln: ln["yc"])
        line_strs = []
        for line in lines:
            blocks = sorted(line["blocks"], key=lambda b: b["left"])
            line_strs.append(" ".join(b["text"] for b in blocks))

        return "\n".join(line_strs).strip()

    def _refine_small_blocks(self, img, filtered, small_ratio=0.6,
                             factor=2, pad=4):
        """Перераспознаёт мелкие текстовые блоки с увеличением.

        Блоки, у которых высота строки заметно меньше медианной
        (< small_ratio * median), вырезаются из исходного изображения,
        увеличиваются в `factor` раз и распознаются заново. Это повышает
        точность мелкого шрифта, не трогая крупный текст.

        Если мелких блоков нет или медиана не определена — возвращает как есть.
        """
        if not filtered or len(filtered) < 2:
            return filtered

        # Высота каждого блока по bbox
        heights = []
        for bbox, _ in filtered:
            ys = [p[1] for p in bbox]
            heights.append(max(ys) - min(ys))

        median_h = float(np.median(heights))
        if median_h <= 0:
            return filtered
        threshold = median_h * small_ratio

        # Если все блоки примерно одного размера — апскейл не нужен
        small_idx = [i for i, h in enumerate(heights) if h < threshold]
        if not small_idx:
            return filtered

        logger.info(f"[OCR] Мелких блоков для апскейла: {len(small_idx)} "
                    f"(медиана {median_h:.0f}px, порог <{threshold:.0f}px)")

        w_img, h_img = img.size
        refined = list(filtered)
        for i in small_idx:
            bbox, old_text = filtered[i]
            xs = [p[0] for p in bbox]
            ys = [p[1] for p in bbox]
            x0 = max(0, int(min(xs)) - pad)
            y0 = max(0, int(min(ys)) - pad)
            x1 = min(w_img, int(max(xs)) + pad)
            y1 = min(h_img, int(max(ys)) + pad)
            if x1 <= x0 or y1 <= y0:
                continue
            try:
                crop = img.crop((x0, y0, x1, y1))
                up = crop.resize(
                    (crop.size[0] * factor, crop.size[1] * factor),
                    Image.LANCZOS,
                )
                sub = self.easyocr_reader.readtext(
                    np.array(up),
                    detail=1,
                    paragraph=False,
                    min_size=10,
                    contrast_ths=0.15,
                    text_threshold=0.65,
                    decoder="greedy",
                    beamWidth=1,
                )
                new_parts = []
                for it in sub:
                    if len(it) == 3:
                        _, t, c = it
                        if c * 100 >= self.confidence_threshold:
                            new_parts.append(t)
                    elif len(it) == 2:
                        # Без confidence — пропускаем
                        pass
                new_text = " ".join(new_parts).strip()
                # Заменяем только если что-то распозналось
                if new_text:
                    refined[i] = (bbox, new_text)
            except Exception as e:
                logger.debug(f"[OCR] Апскейл блока не удался: {e}")
                continue

        return refined

    def _correct_english(self, text: str, max_dist: int = 2) -> str:
        """Консервативная коррекция явных OCR-ошибок в английских словах.

        Использует pyspellchecker (английский словарь). Безопасные правила,
        чтобы НЕ ломать корректные слова и термины:
        - только чисто латинские слова длиной >= 4;
        - слова с дефисом/апострофом не трогаем (to-dos, it's);
        - корректируем только слова, которых нет в словаре;
        - заменяем только если расстояние правки <= max_dist;
        - сохраняем регистр (Title/UPPER/lower).
        """
        if not text:
            return text
        spell = _get_spell_en()
        if spell is None:
            return text

        def fix(match: "re.Match") -> str:
            word = match.group(0)
            if re.search(r"[А-Яа-яёЁ]", word):
                return word
            if "-" in word or "'" in word:
                return word
            letters = "".join(c for c in word if c.isalpha())
            if len(letters) < 4:
                return word
            low = letters.lower()
            try:
                if low in spell:
                    return word
                corr = spell.correction(low)
            except Exception:
                return word
            if not corr or corr == low:
                return word
            if _edit_distance(low, corr) > max_dist:
                return word

            # Восстанавливаем регистр
            if all(c.isupper() for c in letters):
                corr = corr.upper()
            elif letters[0].isupper():
                corr = corr.capitalize()

            # Собираем обратно с небуквенными символами
            out = []
            ci = 0
            for ch in word:
                if ch.isalpha() and ci < len(corr):
                    out.append(corr[ci])
                    ci += 1
                else:
                    out.append(ch)
            return "".join(out)

        return _WORD_RE.sub(fix, text)

    def _normalize_alphabets(self, text: str) -> str:
        """Исправление смешанных алфавитов и регистра в словах.

        EasyOCR в режиме ru+en часто:
        1) Подменяет визуально одинаковые буквы между алфавитами
           (латинские o/a/c/e/p/x/y ↔ кириллические о/а/с/е/р/х/у).
        2) Ставит заглавную букву внутри строчного слова
           (например "что" -> "чтО", "голосовых" -> "голОсовых").

        Для каждого слова:
        - Определяем доминирующий алфавит по "однозначным" буквам.
        - Приводим буквы-гомоглифы к этому алфавиту.
        - Убираем случайные заглавные внутри строчного слова.
        """
        if not text:
            return text

        def fix_word(match: "re.Match") -> str:
            word = match.group(0)

            cyr_unique = 0  # кириллические буквы без латинского двойника
            lat_unique = 0  # латинские буквы без кириллического двойника
            for ch in word:
                if "a" <= ch.lower() <= "z":
                    if ch not in _LAT_HOMOGLYPHS:
                        lat_unique += 1
                elif ("а" <= ch.lower() <= "я") or ch in ("ё", "Ё", "і", "І", "ї", "Ї", "є", "Є"):
                    if ch not in _CYR_HOMOGLYPHS:
                        cyr_unique += 1

            # Если в слове нет однозначных букв — не трогаем (нельзя определить язык)
            if cyr_unique == 0 and lat_unique == 0:
                return word

            if cyr_unique >= lat_unique:
                # Слово русское -> латинские гомоглифы приводим к кириллице
                fixed = "".join(_LAT_TO_CYR.get(ch, ch) for ch in word)
                # Украинские буквы → русские (І→И)
                fixed = "".join(_UKR_TO_RUS.get(ch, ch) for ch in fixed)
                # Убираем случайные заглавные внутри строчного слова
                fixed = _fix_case(fixed, is_cyr=True)
            else:
                # Слово английское -> кириллические гомоглифы приводим к латинице
                fixed = "".join(_CYR_TO_LAT.get(ch, ch) for ch in word)
                fixed = _fix_case(fixed, is_cyr=False)

            return fixed

        def _fix_case(word: str, is_cyr: bool) -> str:
            """Убираем случайные заглавные внутри строчного слова.

            Для русских слов: убираем ВСЕ заглавные в середине/конце слова,
            потому что русский текст НЕ использует CamelCase.
            Для английских слов: определяем CamelCase по паттерну
            "заглавная после строчной" и не трогаем такие слова.
            """
            letters = [ch for ch in word if ch.isalpha()]
            if len(letters) <= 1:
                return word

            upper_count = sum(1 for ch in letters if ch.isupper())
            lower_count = sum(1 for ch in letters if ch.islower())

            # Если всё заглавное или всё строчное — не трогаем
            if upper_count == 0 or lower_count == 0:
                return word

            if is_cyr:
                # Русский: убираем ВСЕ заглавные除了 первую букву
                # Русский текст не использует CamelCase — любая заглавная внутри = ошибка
                result = []
                for i, ch in enumerate(word):
                    if i > 0 and ch.isupper():
                        ch = ch.lower()
                    result.append(ch)
                return "".join(result)
            else:
                # Английский: определяем CamelCase (заглавная после строчной)
                has_camel = False
                prev_lower = False
                for ch in word:
                    if ch.islower():
                        prev_lower = True
                    elif ch.isupper() and prev_lower:
                        has_camel = True
                        break
                if has_camel:
                    # CamelCase — не трогаем
                    return word
                # Не CamelCase — убираем случайные заглавные除了 первую
                result = []
                for i, ch in enumerate(word):
                    if i > 0 and ch.isupper():
                        ch = ch.lower()
                    result.append(ch)
                return "".join(result)

        return _WORD_RE.sub(fix_word, text)

    def _strip_isolated_latin(self, text: str) -> str:
        """Убирает изолированные латинские буквы из русских слов.

        EasyOCR часто вставляет одну-две латинские буквы в середину
        русских слов (например "Vоводском" вместо "Городском").
        """
        if not text:
            return text
        result = []
        i = 0
        while i < len(text):
            ch = text[i]
            # Проверяем, если это латинская буква среди кириллицы
            if "a" <= ch.lower() <= "z" and ch not in _LAT_HOMOGLYPHS:
                # Ищем контекст - есть ли рядом кириллица
                left_cyr = False
                right_cyr = False
                if i > 0:
                    for c in text[max(0, i-3):i]:
                        if "а" <= c.lower() <= "я" or c in "ёЁ":
                            left_cyr = True
                            break
                if i < len(text) - 1:
                    for c in text[i+1:min(len(text), i+4)]:
                        if "а" <= c.lower() <= "я" or c in "ёЁ":
                            right_cyr = True
                            break
                if left_cyr and right_cyr:
                    # Изолированная латинская буква - убираем её
                    i += 1
                    continue
            result.append(ch)
            i += 1
        return "".join(result)

    def _detect_colors(self, img: Image.Image, x: int, y: int) -> Tuple[Tuple[int, int, int], Tuple[int, int, int]]:
        """Определяет цвет текста и фона по координатам (для пипетки).

        Возвращает (цвет_фона, цвет_текста).
        Определяет по среднему цвету в окрестности.
        """
        try:
            w, h = img.size
            x, y = max(0, min(x, w-1)), max(0, min(y, h-1))
            text_color = img.getpixel((x, y))
            margin = 10
            bg_pixels = []
            for dx in range(-margin, margin+1):
                for dy in range(-margin, margin+1):
                    nx, ny = max(0, min(x+dx, w-1)), max(0, min(y+dy, h-1))
                    if (dx, dy) != (0, 0):
                        bg_pixels.append(img.getpixel((nx, ny)))
            if bg_pixels:
                bg_color = tuple(int(sum(p[i] for p in bg_pixels) / len(bg_pixels)) for i in range(3))
            else:
                bg_color = text_color
            # Сохраняем цвета в настройки
            self.settings.set("ocr.bg_color", bg_color)
            self.settings.set("ocr.text_color", text_color)
            return bg_color, text_color
        except Exception:
            return (0, 0, 0), (255, 255, 255)

    def _calculate_preprocess_settings(self) -> Tuple[float, float, bool]:
        """Рассчитывает оптимальные параметры контраст/яркость/инверсия."""
        bg_color = self.settings.get("ocr.bg_color", (128, 128, 128))
        text_color = self.settings.get("ocr.text_color", (255, 255, 255))
        
        bg_brightness = sum(bg_color) / 3
        txt_brightness = sum(text_color) / 3
        
        invert = False
        if bg_brightness > txt_brightness:
            invert = True
            bg_brightness, txt_brightness = txt_brightness, bg_brightness
        
        # Чем меньше контраст (разница), тем сильнее нужно усиливать
        contrast = min(3.0, 1.0 + (64 - abs(bg_brightness - txt_brightness)) / 32)
        # Яркость - усиливаем светлый текст
        brightness = min(2.0, 1.0 + txt_brightness / 255)
        
        return contrast, brightness, invert

    async def recognize_async(self, img: Image.Image = None) -> tuple:
        """Асинхронное распознавание. Возвращает (text, confidence)."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.recognize, img)

    def set_engine(self, engine_id: str):
        """Переключение OCR-движка."""
        if self.engine_type == engine_id:
            return
        desc = get_engine_descriptor(engine_id)
        if not desc:
            logger.error(f"[OCR] Unknown engine: {engine_id}")
            return
        self.engine_type = engine_id
        self.settings.set("ocr.engine", engine_id)
        self._fallback_engines = fallback_chain(engine_id)
        self._active_engine_id = engine_id
        logger.info(f"[OCR] Engine switched to: {engine_id}")

    def set_content_preset(self, preset_name: str):
        """Переключение пресета контента."""
        self._content_preset = preset_name
        self._tuning = get_preset(preset_name)
        self.settings.set("ocr.content_preset", preset_name)
        logger.info(f"[OCR] Content preset switched to: {preset_name}")

    def set_region(self, x: int, y: int, width: int, height: int):
        """Установка области захвата."""
        self.region = {"x": x, "y": y, "width": width, "height": height}
        self.settings.set("ocr.region", self.region)
        logger.info(f"[OCR] Область захвата: {self.region}")

    def set_language(self, language: str):
        """Установка языка распознавания (перезагрузка модели в фоне)."""
        if self.language == language:
            return
        self.language = language
        self._lang_filter = self._parse_lang_filter(language)
        self.settings.set("ocr.language", language)
        logger.info(f"[OCR] Язык изменён на: {language} — перезагрузка модели...")
        threading.Thread(target=self._init_easyocr, daemon=True).start()
