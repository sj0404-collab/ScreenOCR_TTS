# -*- coding: utf-8 -*-
"""Генерирует Kotlin-словари для Android-порта из Python-источников.

Запуск (из корня репозитория):
    python3 gen_android_dicts.py

Результат:
    android/app/src/main/java/com/screenocr/client/dict/DictData.kt
    android/app/src/test/resources/translation_corpus.tsv

Правило: Android-копия словарей генерируется, а не переписывается руками —
иначе `phrases_dict.py` и Kotlin-версия разойдутся при первом же правке
оригинала. Файл помечен как сгенерированный, руками не правится.

Корпус — контрольные пары «английский -> русский», снятые с Python-реализации.
На нём Kotlin-тест сверяется с оригиналом по всему словарю, а не на пяти
примерах. Строки с пустым переводом помечены `fixed`: там Python даёт двойной
пробел, а Kotlin-версия чинит это, и колонка expected содержит уже исправленное
значение.
"""
import json
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_PATH = os.path.join(
    BASE_DIR,
    "android/app/src/main/java/com/screenocr/client/dict/DictData.kt",
)
CORPUS_PATH = os.path.join(
    BASE_DIR,
    "android/app/src/test/resources/translation_corpus.tsv",
)

sys.path.insert(0, BASE_DIR)

from contractions import CONTRACTIONS  # noqa: E402
from phrases_dict import PHRASES  # noqa: E402
from words_extra import EXTRA_WORDS  # noqa: E402

HEADER = """package com.screenocr.client.dict

// СГЕНЕРИРОВАНО: python3 gen_android_dicts.py
// Источники: phrases_dict.py, words_extra.py, contractions.py
// Правь источники и перегенерируй — руками этот файл не меняем.

object DictData {
"""


def kt_string(value: str) -> str:
    out = ['"']
    for ch in value:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "$":
            out.append("\\$")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def render_map(name: str, data: dict) -> str:
    lines = [f"    val {name}: Map<String, String> = mapOf("]
    for key, value in data.items():
        lines.append(f"        {kt_string(key)} to {kt_string(value)},")
    lines.append("    )")
    return "\n".join(lines)


def render_contractions(name: str, data: dict) -> str:
    lines = [f"    val {name}: List<Pair<String, String>> = listOf("]
    for key, value in data.items():
        lines.append(f"        {kt_string(key)} to {kt_string(value)},")
    lines.append("    )")
    return "\n".join(lines)


SENTENCES = [
    "Hello world",
    "How are you?",
    "Thank you!",
    "Press the button",
    "Please open the door and wait here",
    "the quick brown fox",
    "I saw a big fish in the sea",
    "one two three four five six",
    "3 apples, 12 oranges",
    "HELLO WORLD",
    "New York City",
    "  leading and trailing  ",
    "punctuation, everywhere!",
    "...ellipsis...",
    "hyphen-ated-word",
    "under_score and dash-separated",
    "I don't understand",
    "It's raining, isn't it?",
    "Let's go now",
    "you're welcome",
    "i'm fine",
    "of the box",
    "a an the of",
    "The End",
    "SUPER MARIO",
    "the legend of zelda",
    "final fantasy",
    "",
    " ",
    "!!!",
    "12345",
    "emoji only \U0001f600 text",
    "Zelda",
    "no such word at all here",
]


def _every_nth(items, step):
    return list(items)[::step]


def build_corpus():
    """Снимает контрольные пары с Python-реализации."""
    from offline_dict import OfflineTranslator

    translator = OfflineTranslator()

    inputs = []
    inputs.extend(SENTENCES)
    inputs.extend(PHRASES.keys())
    inputs.extend(_every_nth(sorted(EXTRA_WORDS), 3))
    inputs.extend(_every_nth(sorted(EXTRA_WORDS), 7))
    inputs.extend(json.load(open(os.path.join(BASE_DIR, "cities_dict.json"), encoding="utf-8")))
    inputs.extend(_every_nth(sorted(json.load(open(os.path.join(BASE_DIR, "game_names.json"), encoding="utf-8"))), 2))
    inputs.extend(f"{k.upper()}" for k in _every_nth(sorted(EXTRA_WORDS), 11))
    inputs.extend(f"{k}." for k in _every_nth(sorted(EXTRA_WORDS), 13))
    inputs.extend(f"go to {k} now" for k in _every_nth(sorted(EXTRA_WORDS), 17))

    seen = set()
    rows = []
    for text in inputs:
        if "\t" in text or "\n" in text or "\r" in text:
            continue
        if text in seen:
            continue
        seen.add(text)
        result = translator.translate(text)
        fixed = " ".join(result.split())
        mode = "same" if fixed == result else "fixed"
        rows.append((text, fixed, mode, translator.has_untranslated(text)))

    return rows


def write_corpus() -> int:
    rows = build_corpus()
    os.makedirs(os.path.dirname(CORPUS_PATH), exist_ok=True)
    with open(CORPUS_PATH, "w", encoding="utf-8", newline="\n") as f:
        f.write("# СГЕНЕРИРОВАНО: python3 gen_android_dicts.py\n")
        f.write("# Колонки: english<TAB>expected_russian<TAB>mode<TAB>hasUntranslated\n")
        f.write("# mode=same — Kotlin обязан совпасть с Python; fixed — Python даёт двойной пробел\n")
        for text, expected, mode, untranslated in rows:
            f.write(f"{text}\t{expected}\t{mode}\t{'true' if untranslated else 'false'}\n")
    fixed_count = sum(1 for row in rows if row[2] == "fixed")
    return len(rows), fixed_count


def main() -> int:
    parts = [HEADER]
    parts.append("    // Порядок важен: замены идут подряд, как str.replace в Python.")
    parts.append(render_contractions("CONTRACTIONS", CONTRACTIONS))
    parts.append("")
    parts.append(render_map("PHRASES", PHRASES))
    parts.append("")
    parts.append(render_map("EXTRA_WORDS", EXTRA_WORDS))
    parts.append("}")
    parts.append("")

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(parts))

    print(f"OK {OUT_PATH}")
    print(f"   CONTRACTIONS={len(CONTRACTIONS)} PHRASES={len(PHRASES)} EXTRA_WORDS={len(EXTRA_WORDS)}")
    total, fixed = write_corpus()
    print(f"OK {CORPUS_PATH}")
    print(f"   cases={total} fixed={fixed} same={total - fixed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
