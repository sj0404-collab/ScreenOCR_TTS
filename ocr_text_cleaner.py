# -*- coding: utf-8 -*-
"""
OCR Text Cleaner — пост-обработка текста после распознавания.

Алгоритмы адаптированы из yomihon-custom (CyrillicOcrEngine, OcrTextCleaner):
- Исправление Lookalike букв (латиница ↔ кириллица)
- Склейка переносов строк (дефис + \n)
- DP-сегментация слипшегося текста через словарь
- Фильтр мусорных токенов (поочерёдное удаление)
- Нормализация пробелов и пунктуации
- Определение «словарного рэмпа» (артефакт CTC-декодера)
"""
import logging
import re
import unicodedata
from typing import List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# LOOKALIKE МАППИНГ: латиница → кириллица
# ═══════════════════════════════════════════════════════════════

# Латиница → кириллица (для слов, которые в основном русские)
LAT_TO_CYR = {
    "a": "а", "c": "с", "e": "е", "o": "о", "p": "р", "x": "х", "y": "у",
    "A": "А", "B": "В", "C": "С", "E": "Е", "H": "Н", "K": "К", "M": "М",
    "O": "О", "P": "Р", "T": "Т", "X": "Х", "Y": "У", "U": "У", "u": "у",
    "i": "і", "I": "І",  # украинская І
}

# Кириллица → латиница (для слов, которые в основном английские)
CYR_TO_LAT = {
    "а": "a", "с": "c", "е": "e", "о": "o", "р": "p", "х": "x", "у": "y",
    "А": "A", "В": "B", "С": "C", "Е": "E", "Н": "H", "К": "K", "М": "M",
    "О": "O", "Р": "P", "Т": "T", "Х": "X", "У": "Y",
    "і": "i", "І": "I", "ї": "i", "Ї": "I", "є": "e", "Є": "E",
    "ґ": "g", "Ґ": "G", "ё": "e", "Ё": "E",
}

# Украинские буквы → русские
UKR_TO_RUS = {"І": "И", "і": "и", "Ї": "Ы", "ї": "ы", "Є": "Е", "є": "е"}

# Многосимвольная транслитерация (shch → щ и т.д.)
TRANSLIT_TABLE = {
    "shch": "щ", "Shch": "Щ", "SHCH": "Щ",
    "ch": "ч", "Ch": "Ч", "CH": "Ч",
    "sh": "ш", "Sh": "Ш", "SH": "Ш",
    "zh": "ж", "Zh": "Ж", "ZH": "Ж",
    "yu": "ю", "Yu": "Ю", "YU": "Ю",
    "ya": "я", "Ya": "Я", "YA": "Я",
    "yo": "ё", "Yo": "Ё", "YO": "Ё",
    "kh": "х", "Kh": "Х", "KH": "Х",
    "ts": "ц", "Ts": "Ц", "TS": "Ц",
    "ye": "е", "Ye": "Е", "YE": "Е",
}

LAT_HOMOGLYPHS = set(LAT_TO_CYR.keys())
CYR_HOMOGLYPHS = set(CYR_TO_LAT.keys())

# Буквы без двойников в другом алфавите (для определения доминирующего)
_CYR_UNIQUE = set("абвгдежзийклмнопстуфчшщъыьэюяёЁ")
_LAT_UNIQUE = set("BbDdFfGgJjLlNnQqRrSsWwZz")

# Мусорные однобуквенные артефакты иконок
ICON_ARTIFACTS = {
    "d", "c", "e", "x", "v", "r", "n", "i", "l", "k",
    "b", "f", "g", "h", "j", "m", "o", "p", "q", "s",
    "t", "u", "w", "z",
    "D", "C", "E", "X", "V", "R", "N", "L", "K", "B",
    "F", "G", "H", "J", "M", "O", "P", "Q", "S", "T",
    "U", "W", "Z", "Y",
}

# ═══════════════════════════════════════════════════════════════
# СЛОВАРЬ ИЗВЕСТНЫХ СЛОВ (для DP-сегментации)
# ═══════════════════════════════════════════════════════════════

_KNOWN_WORDS = {
    # Русские
    "и", "в", "на", "не", "что", "это", "как", "он", "она", "оно", "они",
    "поиск", "клонирование",
    "мы", "вы", "я", "ты", "его", "её", "их", "нас", "вас", "мне", "тебе",
    "ему", "ей", "нам", "вам", "им", "меня", "тебя", "него", "неё", "них",
    "был", "была", "было", "были", "будет", "будут", "быть", "бы",
    "мог", "могла", "могло", "могли", "можно", "нужно", "надо",
    "есть", "нет", "да", "или", "а", "но", "если", "то", "тоже", "так",
    "уже", "ещё", "еще", "вот", "тут", "там", "где", "когда", "пока",
    "всё", "все", "кто", "чем", "чём", "всего", "всем", "этот", "эта",
    "эти", "тот", "та", "те", "такой", "такая", "такие", "какой", "какая",
    "один", "два", "три", "четыре", "пять", "шесть", "семь", "восемь",
    "девять", "десять", "после", "перед", "между", "через", "без", "для",
    "из", "от", "до", "под", "над", "при", "по", "за", "про", "ante",
    "большой", "маленький", "новый", "старый", "хороший", "плохой",
    "говорит", "сказал", "сказала", "думает", "знает", "видит", "идёт",
    "идет", "стоит", "лежит", "сидит", "летит", "бежит", "работает",
    "время", "день", "ночь", "утро", "вечер", "год", "месяц", "неделя",
    "рука", "нога", "глаз", "голова", "лицо", "тело", "сердце",
    "дом", "квартира", "комната", "окно", "дверь", "стена", "пол",
    "вода", "огонь", "земля", "небо", "ветер", "солнце", "луна",
    "жизнь", "смерть", "любовь", "ненависть", "радость", "горе",
    "сила", "слабость", "ум", "душа", "разум", "волшебство", "магия",
    "战争", "мир", "победа", "поражение", "битва", "бой", "сражение",
    # Английские
    "the", "and", "for", "are", "but", "not", "you", "all", "can", "had",
    "her", "was", "one", "our", "out", "day", "get", "has", "him", "his",
    "how", "its", "may", "new", "now", "old", "see", "way", "who", "did",
    "got", "let", "say", "too", "use", "that", "with", "have", "this",
    "will", "your", "from", "they", "been", "said", "each", "make",
    "like", "long", "look", "many", "most", "over", "such", "take",
    "than", "them", "then", "what", "when", "more", "some", "time",
    "very", "just", "know", "also", "back", "only", "come", "good",
    "give", "first", "life", "world", "still", "find", "here", "thing",
    "many", "well", "tell", "than", "that", "this", "with", "have",
    "about", "could", "would", "should", "there", "their", "which",
    "people", "think", "really", "something", "nothing", "everything",
}

# Известные ошибки OCR для русских терминов
_KNOWN_CORRECTIONS = {
    "curl": "Ctrl", "ctri": "Ctrl", "cirl": "Ctrl",
    "alf": "Alt", "shifl": "Shift", "shlft": "Shift",
    "enler": "Enter", "esc": "Esc", "dei": "Del",
}


# ═══════════════════════════════════════════════════════════════
# ОСНОВНЫЕ ФУНКЦИИ
# ═══════════════════════════════════════════════════════════════

def fix_lookalikes_per_word(text: str) -> str:
    """Поочерёдная замена латинских lookalike на кириллицу в словах,
    которые в основном кириллические. Чисто латинские слова (SOS, Wi-Fi)
    сохраняются из whitelist.

    Алгоритм (из OcrTextCleaner.fixLookalikesPerWord):
    1. Для каждого слова считаем количество «однозначных» букв каждого алфавита.
    2. Если кириллицы больше — латинские lookalike заменяем на кириллицу.
    3. Если латиницы больше — кириллические lookalike заменяем на латиницу.
    4. Если равны — не трогаем.
    """
    if not text:
        return text

    def _is_cyr(c: str) -> bool:
        o = ord(c)
        return 0x0400 <= o <= 0x052F or 0xA640 <= o <= 0xA69F

    _run_re = re.compile(
        r"[A-Za-z]+|[\u0400-\u052F\uA640-\uA69F]+|[^A-Za-z\u0400-\u052F\uA640-\uA69F]+"
    )

    def _is_lat_run(s: str) -> bool:
        return len(s) >= 1 and all("a" <= c <= "z" or "A" <= c <= "Z" for c in s)

    def _is_cyr_run(s: str) -> bool:
        return len(s) >= 1 and all(_is_cyr(c) for c in s)

    def _fix_word(match: re.Match) -> str:
        word = match.group(0)
        # Склеенные разными алфавитами куски (окна CTC уронили пробел):
        # «Поискиклонированиеyomihon» → два слова, иначе голосование
        # за единый алфавит портит латинский хвост (y→у, o→о).
        runs = _run_re.findall(word)
        letter_runs = [r for r in runs if _is_lat_run(r) or _is_cyr_run(r)]
        if len(letter_runs) >= 2 and any(
            len(a) >= 2 and len(b) >= 2
            and ((_is_cyr_run(a) and _is_lat_run(b)) or (_is_lat_run(a) and _is_cyr_run(b)))
            for a, b in zip(letter_runs, letter_runs[1:])
        ):
            out: List[str] = []
            for r in runs:
                if _is_cyr_run(r) and len(r) >= 2:
                    out.append("".join(UKR_TO_RUS.get(c, c) for c in r))
                else:
                    out.append(r)
            # Пробел только на границе длинные буквенные прогоны разных алфавитов
            res: List[str] = []
            prev_letters = ""
            for chunk in out:
                if (
                    res and _is_lat_run(chunk) and len(chunk) >= 2
                    and _is_cyr_run(prev_letters) and len(prev_letters) >= 2
                ) or (
                    res and _is_cyr_run(chunk) and len(chunk) >= 2
                    and _is_lat_run(prev_letters) and len(prev_letters) >= 2
                ):
                    res.append(" ")
                res.append(chunk)
                if _is_lat_run(chunk) or _is_cyr_run(chunk):
                    prev_letters = chunk
                elif chunk.strip() == "":
                    prev_letters = ""
            return "".join(res)

        cyr_unique = sum(1 for c in word if c in _CYR_UNIQUE)
        lat_unique = sum(1 for c in word if c in _LAT_UNIQUE)

        if cyr_unique == 0 and lat_unique == 0:
            # Нет однозначных букв (одни омоглифы): решает большинство
            # по полному составу алфавитов, иначе не трогаем.
            cyr_all = sum(1 for c in word if _is_cyr(c))
            lat_all = sum(1 for c in word if "a" <= c <= "z" or "A" <= c <= "Z")
            if cyr_all > lat_all:
                mapped = "".join(LAT_TO_CYR.get(c, c) for c in word)
                return "".join(UKR_TO_RUS.get(c, c) for c in mapped)
            elif lat_all > cyr_all:
                return "".join(CYR_TO_LAT.get(c, c) for c in word)
            return word

        if cyr_unique > lat_unique:
            # Русское слово → латинские lookalike → кириллица,
            # украинские → русские
            mapped = "".join(LAT_TO_CYR.get(c, c) for c in word)
            return "".join(UKR_TO_RUS.get(c, c) for c in mapped)
        elif lat_unique > cyr_unique:
            # Английское слово → кириллические lookalike → латиница
            return "".join(CYR_TO_LAT.get(c, c) for c in word)
        return word

    return re.sub(r"[A-Za-z\u0400-\u052F\uA640-\uA69F]{2,}", _fix_word, text)


def join_line_hyphens(text: str) -> str:
    """Склейка переносов строк с дефисом.

    Из OcrTextCleaner.joinLineHyphens:
    'пере-\\nносится' → 'переносится'
    'хо-\\nрошо' → 'хорошо'
    """
    if not text:
        return text
    # Дефис в конце строки + начало следующей — склеиваем
    text = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", text)
    # Дефис посередине строки с переносом
    text = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", text)
    return text


# ═══════════════════════════════════════════════════════════════
# КОРРЕКЦИЯ ВИЗУАЛЬНЫХ ПУТАНИЦ (г↔р, и↔й, ...)
# ═══════════════════════════════════════════════════════════════
# Строго ограниченная правка одиночных ошибок модели: слово правится
# ТОЛЬКО если оно само НЕ в словаре, а ровно ОДИН кандидат на расстоянии
# 1 правки (замена внутри пар-омоглифов, удаление/вставка буквы из пар)
# ЕСТЬ в словаре. Правильное редкое слово не трогаем никогда.

_CONFUSION_PAIRS = {
    "гр": ("г", "р"), "гп": ("г", "п"), "рп": ("р", "п"),
    "ий": ("и", "й"), "её": ("е", "ё"), "шщ": ("ш", "щ"),
    "вб": ("в", "б"), "ьъ": ("ь", "ъ"), "дл": ("д", "л"),
    "зэ": ("з", "э"), "лп": ("л", "п"),
}
_CONFUSION_NEIGHBORS = {}
for _a, _b in _CONFUSION_PAIRS.values():
    _CONFUSION_NEIGHBORS.setdefault(_a, set()).add(_b)
    _CONFUSION_NEIGHBORS.setdefault(_b, set()).add(_a)
_CONFUSION_LETTERS = frozenset(_CONFUSION_NEIGHBORS.keys())

# Явные частые слова (страховка, если их нет в harvest-словарях)
_CONFUSION_SEEDS = frozenset({
    "рана", "ране", "рану", "раной", "раны", "ран",
    "объект", "объекта", "объекту", "объектом", "объекте", "объекты",
    "поделиться", "содержимым", "содержимое",
})

_RU_VALID = None
_RU_FREQ = None


def _ru_valid_words() -> frozenset:
    """Словарь валидации: _KNOWN_WORDS + кириллические токены из словарей."""
    global _RU_VALID, _RU_FREQ
    if _RU_VALID is not None:
        return _RU_VALID
    from collections import Counter
    freq = Counter()
    for w in _CONFUSION_SEEDS:
        freq[w] += 5
    for w in _KNOWN_WORDS:
        freq[w.lower()] += 5
    try:
        import json
        import os
        base = os.path.dirname(os.path.abspath(__file__))
        p = os.path.join(base, "eng_rus_dict.json")
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            vals = data.values() if isinstance(data, dict) else data
            for v in vals:
                if isinstance(v, str):
                    seq = (v,)
                elif isinstance(v, (list, tuple)):
                    seq = v
                else:
                    continue
                for s in seq:
                    if not isinstance(s, str):
                        continue
                    for tok in re.findall(r"[а-яё]{2,}", s.lower()):
                        freq[tok] += 1
    except Exception as e:
        logger.warning(f"[Cleaner] RU validation dict not loaded: {e}")
    _RU_VALID = frozenset(freq.keys())
    _RU_FREQ = dict(freq)
    logger.info(f"[Cleaner] RU validation dict: {len(_RU_VALID)} words")
    return _RU_VALID


def _ru_freq(word: str) -> int:
    _ru_valid_words()
    return (_RU_FREQ or {}).get(word, 0)


_EN_VALID = None

# Технические аббревиатуры и UI-слова, которых может не быть в ключах
_EN_TECH = frozenset({
    "ai", "wifi", "wi-fi", "sos", "usb", "pc", "tv", "dvd", "3d", "hp",
    "sim", "sd", "ok", "sms", "mms", "gps", "gsm", "cd", "hd", "4k", "8k",
    "it", "app", "web", "http", "www", "exe", "zip", "mp3", "kb", "mb",
    "workspace", "browser", "search", "settings", "file", "files", "folder",
    "folders", "download", "downloads", "window", "windows", "button",
    "buttons", "menu", "menus", "toolbar", "icon", "icons", "page", "pages",
    "tab", "tabs", "link", "links", "image", "images", "video", "player",
    "volume", "network", "scan", "stop", "start", "close", "open", "save",
    "copy", "paste", "cut", "edit", "view", "help", "home", "back",
    "forward", "reload", "go", "text", "voice", "language", "region",
})


def _en_valid_words() -> frozenset:
    """Английский лексикон из ключей eng_rus_dict + тех. аббревиатуры."""
    global _EN_VALID
    if _EN_VALID is not None:
        return _EN_VALID
    words = set(_EN_TECH)
    try:
        import json
        import os
        base = os.path.dirname(os.path.abspath(__file__))
        p = os.path.join(base, "eng_rus_dict.json")
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            keys = data.keys() if isinstance(data, dict) else []
            for k in keys:
                if not isinstance(k, str):
                    continue
                for tok in re.findall(r"[a-z]{2,}", k.lower()):
                    words.add(tok)
    except Exception as e:
        logger.warning(f"[Cleaner] EN validation dict not loaded: {e}")
    _EN_VALID = frozenset(words)
    logger.info(f"[Cleaner] EN validation dict: {len(_EN_VALID)} words")
    return _EN_VALID


def _confusion_candidates(word: str):
    """Кандидаты на расстоянии 1 визуальной правки: строка -> (вес, операция).

    Вес отражает механизм ошибки модели: замена внутри пары-омоглифа
    или удаление вставленной буквы рядом с её парным соседом (расщеплённый
    глиф: 'грана' -> 'рана', где лишний 'г' стоит рядом с 'р') — 2;
    вставка рядом с парным соседом — 1; прочее — 0.
    Операция нужна гейту: нулевые вставки/замены запрещены (выдумывают
    буквы: 'теат' -> 'театр'), нулевые удаления разрешены (заикание модели).
    """
    out = {}
    n = len(word)

    def _bump(key, score, op):
        if key != word and (key not in out or score > out[key][0]):
            out[key] = (score, op)

    # Замены внутри пар-омоглифов
    for i, ch in enumerate(word):
        for alt in _CONFUSION_NEIGHBORS.get(ch, ()):
            _bump(word[:i] + alt + word[i + 1:], 2, "sub")
    # Удаление одного символа
    for i in range(n):
        ch = word[i]
        mates = _CONFUSION_NEIGHBORS.get(ch, set())
        near = set(word[max(0, i - 1):i + 2]) - {ch}
        score = 2 if mates & near else 0
        _bump(word[:i] + word[i + 1:], score, "del")
    # Вставка буквы из пар
    for i in range(n + 1):
        near = set(word[max(0, i - 1):i + 1])
        for ch in _CONFUSION_LETTERS:
            score = 1 if (_CONFUSION_NEIGHBORS.get(ch, set()) & near) else 0
            _bump(word[:i] + ch + word[i:], score, "ins")
    return out


def correct_visual_confusions(text: str) -> str:
    """Правит одиночные визуальные ошибки модели по словарю.

    Безопасно: трогает только чисто кириллические токены длиной 4+,
    которых НЕТ в словаре, и только если ровно один кандидат ЕСТЬ.
    """
    if not text:
        return text
    valid = _ru_valid_words()
    result = []
    for tok in text.split(" "):
        # Отделяем пунктуацию по краям
        m = re.match(r"^([^а-яА-ЯёЁa-zA-Z]*)([а-яА-ЯёЁa-zA-Z]+)([^а-яА-ЯёЁa-zA-Z]*)$", tok)
        if not m:
            result.append(tok)
            continue
        pre, core, post = m.groups()
        if len(core) < 4 or not re.fullmatch(r"[а-яё]+", core.lower()):
            result.append(tok)
            continue
        low = core.lower()
        if low in valid:
            result.append(tok)
            continue
        scored = [(c, s, o) for c, (s, o) in _confusion_candidates(low).items()
                  if c in valid]
        if not scored:
            result.append(tok)
            continue
        best = max(s for _, s, _ in scored)
        winners = [(c, o) for c, s, o in scored if s == best]
        if len(winners) == 1:
            cand, op = winners[0]
            # Нулевые вставки/замены запрещены: они выдумывают буквы
            # ('теат' -> 'театр' из ничего). Нулевые удаления разрешены.
            if best == 0 and op != "del":
                result.append(tok)
                continue
            fixed = cand
        else:
            # Ничья по механизму ошибки — решает частотность (редкое
            # собственное имя вроде 'гана' проигрывает обычному 'рана').
            # Строго больше, иначе не трогаем.
            ranked = sorted(
                winners, key=lambda co: _ru_freq(co[0]), reverse=True)
            if len(ranked) < 2 or _ru_freq(ranked[0][0]) <= _ru_freq(ranked[1][0]):
                result.append(tok)
                continue
            if best == 0 and ranked[0][1] != "del":
                result.append(tok)
                continue
            fixed = ranked[0][0]
        if core.isupper():
            fixed = fixed.upper()
        elif core[0].isupper():
            fixed = fixed.capitalize()
        result.append(pre + fixed + post)
    return " ".join(result)


def restore_known_words(text: str) -> str:
    """DP-сегментация слипшегося текста через словарь известных слов.

    Из OcrTextCleaner.restoreKnownCaptionWords:
    Динамическое программирование находит оптимальное разбиение
    кириллического текста на известные слова. Вставляет границы
    ТОЛЬКО если ВСЕ фрагменты — известные слова.
    """
    if not text:
        return text

    words = text.split()
    result = []
    for word in words:
        # Только для слов из подозрительно длинных кириллических блоков
        if len(word) < 6 or not re.match(r"^[а-яА-ЯёЁ]+$", word):
            result.append(word)
            continue

        low = word.lower()
        if low in _KNOWN_WORDS:
            result.append(word)
            continue

        # DP-поиск разбиения на известные слова
        split = _dp_split_on_known(word.lower())
        if split and len(split) > 1 and all(s in _KNOWN_WORDS for s in split):
            # Восстанавливаем регистр
            restored = []
            idx = 0
            for part in split:
                orig_slice = word[idx:idx + len(part)]
                # Сохраняем регистр исходного сегмента
                if orig_slice.isupper():
                    restored.append(part.upper())
                elif orig_slice[0].isupper():
                    restored.append(part.capitalize())
                else:
                    restored.append(part)
                idx += len(part)
            result.append(" ".join(restored))
        else:
            result.append(word)

    return " ".join(result)


def _dp_split_on_known(word: str, max_word_len: int = 20) -> Optional[List[str]]:
    """DP-алгоритм: разбивает слово на список известных подслов.

    Возвращает None, если разбиение невозможно или все фрагменты
    не являются известными словами.
    """
    n = len(word)
    if n == 0:
        return []
    if n > 200:
        return None

    # dp[i] = список слов для word[:i] или None если невозможно
    dp = [None] * (n + 1)
    dp[0] = []

    for i in range(1, n + 1):
        for length in range(1, min(max_word_len, i) + 1):
            start = i - length
            candidate = word[start:i]
            if dp[start] is not None and candidate in _KNOWN_WORDS:
                new_list = dp[start] + [candidate]
                if dp[i] is None or len(new_list) < len(dp[i]):
                    dp[i] = new_list

    return dp[n]


def filter_garbage_tokens(text: str) -> str:
    """Поочерёдное удаление мусорных токенов, сохраняя нормальные.

    Из OcrTextCleaner.filterGarbageTokens:
    Удаляет токены, содержащие не-кириллические и не-латинские символы,
    при этом сохраняя остальные токены строки.
    """
    if not text:
        return text

    CYRILLIC_RE = re.compile(r"[а-яА-ЯёЁ]")
    LATIN_RE = re.compile(r"[a-zA-Z]")
    WHITELIST = set(".,!?;:-'\"()—–…/ \n")

    lines = text.split("\n")
    result = []
    for line in lines:
        words = line.split()
        cleaned = []
        for w in words:
            # Токен нормальный если содержит кириллицу
            if CYRILLIC_RE.search(w):
                cleaned.append(w)
            # Или латиницу (английские слова)
            elif LATIN_RE.search(w):
                cleaned.append(w)
            # Или если это чистая пунктуация/пробелы/цифры
            elif all(c in WHITELIST or c.isdigit() for c in w):
                cleaned.append(w)
            # Иначе — мусорный токен, пропускаем
            else:
                logger.debug(f"[Cleaner] Garbage token removed: '{w}'")
        if cleaned:
            result.append(" ".join(cleaned))

    return "\n".join(result)


def looks_like_dictionary_ramp(text: str) -> bool:
    """Определяет артефакт CTC-декодера: длинные последовательные восходящие Unicode codepoints.

    Из OcrTextCleaner.looksLikeDictionaryRamp:
    Это.signature ошибки, когда декодер «скатывается» по таблице символов.
    Ключевой критерий: 10+ последовательных восходящих codepoints БЕЗ пробелов и пунктуации.
    """
    if not text or len(text) < 10:
        return False
    # Разбиваем на токены (слова без пробелов/пунктуации)
    for word in re.split(r'[\s,.\-!?;:\"\'()\[\]{}<>+/=…—–%№]+', text):
        if len(word) < 8:
            continue
        ascending = 1
        for i in range(1, len(word)):
            if ord(word[i]) > ord(word[i - 1]):
                ascending += 1
                if ascending >= 10:
                    return True
            else:
                ascending = 1
    return False


def normalize_whitespace(text: str) -> str:
    """Нормализация пробелов и пунктуации.

    Из TextPostprocessor: сжатие пробелов, нормализация многоточий,
    нормализация знаков препинания.
    """
    if not text:
        return text
    # Множественные пробелы → один
    text = re.sub(r"[ \t]+", " ", text)
    # Пробелы вокруг \n
    text = re.sub(r" *\n *", "\n", text)
    # Многоточия → …
    text = re.sub(r"\.{4,}", "…", text)
    # Три точки → …
    text = re.sub(r"\.\.\.", "…", text)
    # Запятые/точки с пробелами
    text = re.sub(r"\s+([,.!?;:])", r"\1", text)
    text = re.sub(r"([(\[«])\s+", r"\1", text)
    # Убираем пробелы в начале/конце строк
    lines = [l.strip() for l in text.split("\n")]
    return "\n".join(lines)


def normalize_numbers(text: str) -> str:
    """Чистка чисел: 1.5000000 → 1.50, 100.00000 → 100.

    Убирает хвостовые нули после десятичной точки (артефакт float-форматирования OCR).
    Целые числа не трогает. Числа вида 1.00 → 1.
    """
    if not text:
        return text

    def _clean_number(m: re.Match) -> str:
        s = m.group(0)
        if '.' not in s and ',' not in s:
            return s
        # Заменяем запятую на точку для обработки, потом восстанавливаем
        sep = ',' if ',' in s else '.'
        integer, frac = s.split(sep, 1)
        # Убираем хвостовые нули
        frac = frac.rstrip('0')
        if not frac:
            return integer  # 1.00 → 1
        return f"{integer}{sep}{frac}"

    # Числа с десятичной точкой/запятой и хвостовыми нулями
    text = re.sub(r'\b\d+[.,]\d*0+\b', _clean_number, text)
    return text


def cyrillic_fitness(text: str) -> float:
    """Доля кириллических символов после исправления lookalike.

    Из CyrillicOcrEngine.cyrillicFitness:
    Возвращает 0.0..1.0 — чем выше, тем больше кириллицы в тексте.
    """
    if not text:
        return 0.0
    alpha = [c for c in text if c.isalpha()]
    if not alpha:
        return 0.0
    cyr_count = sum(1 for c in alpha if "а" <= c.lower() <= "я" or c in "ёЁ")
    return cyr_count / len(alpha)


def latin_fitness(text: str) -> float:
    """Доля ASCII-латиницы среди букв. Пара к cyrillic_fitness для
    ранжирования кандидатов английского движка."""
    if not text:
        return 0.0
    alpha = [c for c in text if c.isalpha()]
    if not alpha:
        return 0.0
    lat_count = sum(
        1 for c in alpha if "a" <= c <= "z" or "A" <= c <= "Z")
    return lat_count / len(alpha)


def reattach_leading_punct(text: str) -> str:
    """Возвращает оторванную ведущую пунктуацию предыдущему слову.

    Артефакт сегментации/CTC: запятая предыдущего слова оказывается
    в начале следующего токена ('файл' ',чтобы' -> 'файл,' 'чтобы').
    """
    _PUNCT = set(".,!?:;»”’'\"…—–-")
    toks = text.split(" ")
    out = []
    for tok in toks:
        i = 0
        while i < len(tok) and tok[i] in _PUNCT:
            i += 1
        if i > 0 and i < len(tok) and out and out[-1] \
                and out[-1][-1] not in _PUNCT and out[-1][-1].isalnum():
            out[-1] = out[-1] + tok[:i]
            rest = tok[i:]
            if rest:
                out.append(rest)
        else:
            out.append(tok)
    return " ".join(t for t in out if t)


def is_acceptable_cyrillic_text(text: str) -> bool:
    """Проверяет, что все слова либо чисто кириллические, либо из whitelist.

    Из OcrTextCleaner.isAcceptableCyrillicOcrText.
    """
    if not text:
        return False
    words = text.split()
    for w in words:
        clean = w.strip(".,!?;:—–-")
        if not clean:
            continue
        # Кириллическое слово — ок
        if re.match(r"^[а-яА-ЯёЁ]+$", clean):
            continue
        # Чисто латинское — допускаем (SOS, Wi-Fi, OK)
        if re.match(r"^[a-zA-Z]+$", clean):
            continue
        # Смешанное — проверяем
        has_cyr = bool(re.search(r"[а-яА-ЯёЁ]", clean))
        has_lat = bool(re.search(r"[a-zA-Z]", clean))
        if has_cyr and has_lat:
            # Смешанное слово — допускаем если есть хотя бы 1 кириллическая
            continue
        # Непонятное — отклоняем
        return False
    return True


def apply_known_corrections(text: str) -> str:
    """Точечная замена известных ошибочно распознанных терминов."""
    if not text:
        return text

    def repl(m: re.Match) -> str:
        w = m.group(0)
        return _KNOWN_CORRECTIONS.get(w.lower(), w)

    return re.sub(r"[A-Za-z]+", repl, text)


def normalize_local_cyrillic_caption(text: str) -> str:
    """Полный пайплайн пост-обработки кириллического текста.

    Из OcrTextCleaner.normalizeLocalCyrillicCaption:
    joinLineHyphens → fixLookalikesPerWord → restoreKnownCaptionWords
    """
    if not text:
        return text
    text = join_line_hyphens(text)
    text = fix_lookalikes_per_word(text)
    text = restore_known_words(text)
    text = correct_visual_confusions(text)
    text = reattach_leading_punct(text)
    return text


def full_clean_pipeline(text: str, engine_type: str = "tflite_cyrillic") -> str:
    """Полный пайплайн очистки OCR текста.

    Порядок шагов (вдохновлён yomihon-custom + существующий ocr_wrapper):
    1. Нормализация пробелов
    2. Исправление Lookalike букв
    3. Склейка переносов
    4. DP-сегментация слипшегося текста
    5. Фильтр мусорных токенов
    6. Нормализация пробелов (финальная)
    7. Проверка на «словарный рэмп»
    """
    if not text:
        return text

    # Шаг 1: Нормализация пробелов
    text = normalize_whitespace(text)

    # Шаг 2: Исправление Lookalike
    text = fix_lookalikes_per_word(text)

    # Шаг 3: Склейка переносов
    text = join_line_hyphens(text)

    # Шаг 4: DP-сегментация (только для кириллицы)
    if engine_type in ("tflite_cyrillic", "cyrillic_onnx", "easyocr"):
        text = restore_known_words(text)

    # Шаг 5: Фильтр мусора
    text = filter_garbage_tokens(text)

    # Шаг 6: Исправление known errors
    text = apply_known_corrections(text)

    # Шаг 7: Нормализация чисел (1.5000000 → 1.50)
    text = normalize_numbers(text)

    # Шаг 8: Финальная нормализация
    text = normalize_whitespace(text)

    # Шаг 9: Проверка на CTC-рэмп
    if looks_like_dictionary_ramp(text):
        logger.warning(f"[Cleaner] Dictionary ramp detected, text may be garbage: {text[:80]}")

    return text
