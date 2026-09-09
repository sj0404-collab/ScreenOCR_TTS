# -*- coding: utf-8 -*-
"""Авто-выбор эмоции аватара по содержанию переведённого (русского) текста.

Правила на основе ключевых слов + знаков препинания.
Возвращает одну из эмоций аватара (neutral, happy, sad, angry, surprised,
thinking, speaking, wink, sleeping) или 'neutral' по умолчанию.
"""
import logging

logger = logging.getLogger(__name__)

# Категории ключевых слов (русские корни/слова)
KEYWORDS = {
    "happy": [
        "спасиб", "благодар", "привет", "здравств", "доброе утро", "добрый",
        "отлич", "супер", "круто", "люблю", "рад", "весел", " ура ", "счаст",
        "поздравл", "молодец", "хорошо", "класс", "прекрасн", "замечательн",
        "нрав", "согласен", "согласна", "договорил",
    ],
    "sad": [
        "грустн", "прощай", "прости", " жаль", "печал", "умер", "смерть",
        "плач", "слёз", "слез", "одинок", "тоск", "разочарован", "больн",
        "несчаст", "погиб", "потерял", "скучаю",
    ],
    "angry": [
        "ненавиж", "дурак", "идиот", "придурок", "заткнис", "проклят",
        "прочь", "пошёл", "пошел", "убью", "бесит", "ненавист", "кретин",
        "тупой", "немедлен", "хватит", "прекрат", "вон", "подл",
    ],
    "surprised": [
        "о боже", "ого", "вау", "невозможн", "шок", "удив", "серьёзн",
        "серьезн", "не может быть", "неужел", "как так", "поразительн",
        "осторожн", "опасн", "беги", "смотри", "вниман",
    ],
    "thinking": [
        "хм", "посмотрим", "подожд", "думаю", "возможн", "наверн", "интересн",
        "может быть", "допустим", " стоп ", "ладно", "итак", "значит",
        "предполож", "вероятн",
    ],
    "wink": [
        "шучу", "подмиг", "не переживай", "всё ок", "всё хорошо", "расслаб",
    ],
    "sleeping": [
        "спокойной ночи", "уснул", "спать", "устал", "сонн", "зев",
    ],
}

# Грубый фильтр «мата» → angry
PROFANITY = ["блин ", "чёрт", "черт ", "задниц", "придур"]


def detect_emotion(text):
    """Возвращает имя эмоции или 'neutral' для пустого/нейтрального текста.

    Если нет сильных сигналов — 'neutral'.
    """
    text = (text or "").lower().strip()
    if not text:
        logger.debug("[EMOTION] Пустой текст → neutral")
        return "neutral"

    t = " " + text + " "
    t = t.replace("ё", "е")

    scores = {em: 0 for em in KEYWORDS}
    for em, words in KEYWORDS.items():
        for w in words:
            wt = w.replace("ё", "е")
            if wt in t:
                scores[em] += 1

    has_q = "?" in text or "¿" in text
    has_excl = "!" in text
    letters = [c for c in text if c.isalpha()]
    caps_ratio = (sum(1 for c in letters if c.isupper()) /
                  max(1, len(letters))) if letters else 0

    if any(p in t for p in PROFANITY):
        scores["angry"] += 2
        logger.debug(f"[EMOTION] Обнаруцен мат → angry +2")
    if has_q:
        scores["thinking"] += 1
        scores["surprised"] += 0.5
    if has_excl:
        best = max(scores, key=scores.get)
        if scores[best] > 0:
            scores[best] += 1
        else:
            scores["surprised"] += 0.5
    if caps_ratio > 0.55 and len(letters) > 4:
        if scores["happy"] > 0:
            scores["happy"] += 1
        else:
            scores["angry"] += 1

    best = max(scores, key=scores.get)
    if scores[best] >= 1:
        logger.info(f"[EMOTION] Текст: '{text[:60]}...' → {best} (score={scores[best]})")
        return best
    logger.debug(f"[EMOTION] Нет сигналов → neutral (scores={scores})")
    return "neutral"
