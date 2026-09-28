# -*- coding: utf-8 -*-
"""
Решатель: что именно озвучивать.

Зачем он нужен. Жёсткие правила («минимум 2 слова», «держится 2 опроса»)
ломаются в реальных играх: субтитры могут быть в один слово, в верхней
трети экрана, а интерфейс — наоборот длинными фразами. Поэтому вместо
списка запретов здесь СКОРИНГ: набор признаков, веса и порог. Решение
считается из признаков, а не из совпадений с шаблоном.

Что это даёт:
  * решение объяснимо — рядом пишется, какие признаки «за» и «против»;
  * веса можно подкрутить под конкретную игру, не ломая логику;
  * одинаково работает для реплики голоса, для текста на экране и для
    их слияния.
"""
import logging
import re

logger = logging.getLogger(__name__)

# Действия
SPEAK = "speak"      # озвучить
REFINE = "refine"    # только уточнить текст голоса (не озвучивать сам)
SKIP = "skip"        # промолчать

_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)
_CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]")


class SpeechDecider:
    """Считает, стоит ли озвучивать очередной кандидат.

    weights — доли вклада признаков. Правится под игру:
        decider = SpeechDecider(weights={"stability": 2.0})
    """

    DEFAULTS = {
        # текст на экране
        "words": 1.0,          # есть слова (в т.ч. CJK)
        "length": 1.0,         # разумная длина реплики
        "dialog_marks": 1.2,   # знаки конца фразы: . ? ! …
        "sentence_start": 0.5, # с заглавной буквы
        "screen_pos": 1.0,     # строка низко на экране
        "stability": 1.6,      # держится между опросами, а не мелькает
        "freshness": 0.8,      # только что изменилась на экране
        "not_gibberish": 1.6,  # не «X», не «OMCK», не одни цифры
        # голос
        "has_voice": 2.0,      # STT услышал речь
        "voice_text": 1.2,     # распознанный текст осмысленный
        "overlap_voice": 1.5,  # текст совпадает с голосом -> это он же
        "not_echo": 1.5,       # не похоже на наш собственный голос
    }

    def __init__(self, weights=None, speak_at: float = 0.55,
                 refine_at: float = 0.30, min_score: float = 0.0):
        self.w = dict(self.DEFAULTS)
        if weights:
            self.w.update(weights)
        self.speak_at = speak_at
        self.refine_at = refine_at
        self.min_score = min_score

    # ── разбор кандидата ──
    @staticmethod
    def features(text: str, *, has_voice: bool = False,
                 voice_text: str = "", stability: int = 0,
                 screen_pos: float = 1.0, age: float = 0.0,
                 is_echo: bool = False) -> dict:
        t = (text or "").strip()
        words = _WORD_RE.findall(t)
        n = len(words)
        # CJK: иероглифы/кана не считаются словами регексом — считаем сами,
        # иначе японский/китайский текст выглядел бы как «мусор»
        cjk_chars = len(_CJK_RE.findall(t))
        eff_words = n + (cjk_chars // 2 if cjk_chars else 0)
        letters = sum(1 for c in t if c.isalpha())
        digits = sum(1 for c in t if c.isdigit())
        marks = sum(t.count(c) for c in ".?!…")
        # «живой» текст: слова нормальной длины, буквы есть, цифр мало
        short = sum(1 for w in words if len(w) <= 2)
        short_ratio = (short / n) if n else 1.0
        mixed = sum(1 for w in words if any(ch.isdigit() for ch in w))
        good_words = 1.0 if eff_words >= 1 else 0.0
        gibberish = 0.0
        if cjk_chars >= 2:
            gibberish = 1.0                    # CJK — законный текст
        elif eff_words >= 2 and letters >= 3 and short_ratio <= 0.5 \
                and mixed == 0 and digits <= max(1, letters // 4):
            gibberish = 1.0
        return {
            "words": good_words,
            "length": min(1.0, eff_words / 6.0),
            "dialog_marks": 1.0 if marks else 0.0,
            "sentence_start": 1.0 if (t[:1].isupper() or cjk_chars) else 0.0,
            # 0 — верх экрана, 1 — низ; субтитры любят низ
            "screen_pos": max(0.0, min(1.0, screen_pos)),
            # 1 опрос — слабо, 3+ — уверенно
            "stability": min(1.0, max(0, stability) / 3.0),
            # свежесть: 0 c — только что, 5 c и старше — 0
            "freshness": max(0.0, 1.0 - age / 5.0),
            "not_gibberish": gibberish,
            "has_voice": 1.0 if has_voice else 0.0,
            "voice_text": 1.0 if (len(_WORD_RE.findall(voice_text or "")) >= 2
                                  or len(_CJK_RE.findall(voice_text or "")) >= 2)
                          else 0.0,
            "overlap_voice": SpeechDecider.overlap(voice_text, t),
            "not_echo": 0.0 if is_echo else 1.0,
        }

    @staticmethod
    def overlap(a: str, b: str) -> float:
        wa = set(w.lower() for w in _WORD_RE.findall(a or ""))
        wb = set(w.lower() for w in _WORD_RE.findall(b or ""))
        if not wa or not wb:
            return 0.0
        return len(wa & wb) / max(len(wa), len(wb))

    # ── решение ──
    def decide(self, text: str, **kw) -> tuple:
        """-> (action, score, обоснование)"""
        t = (text or "").strip()
        # Два жёстких правила, где скоринг бессмысленен:
        #  - эхо собственного голоса озвучивать нельзя НИКОГДА;
        #  - пустого кандидата озвучивать нельзя.
        # Всё остальное решается весами — их можно крутить под игру.
        if kw.get("is_echo"):
            return SKIP, 0.0, "жёсткое правило: это наш собственный голос"
        if not t:
            return SKIP, 0.0, "пустой текст"

        f = self.features(t, **kw)
        total = 0.0
        weight = 0.0
        pos, neg = [], []
        for k, v in f.items():
            w = self.w.get(k, 0.0)
            if w <= 0:
                continue
            weight += w
            total += w * v
            if v >= 0.5:
                pos.append(f"{k}+{w * v:.2f}")
            else:
                # показываем, сколько потеряли, а не 0.00
                neg.append(f"{k}-{w * (1.0 - v):.2f}")

        score = (total / weight) if weight else 0.0
        score = max(0.0, min(1.0, score))
        if score >= self.speak_at:
            action = SPEAK
        elif score >= self.refine_at:
            action = REFINE
        else:
            action = SKIP

        reason = (f"score={score:.2f} [порог {self.speak_at}/{self.refine_at}] "
                  f"за: {' '.join(pos[:4]) or '-'} "
                  f"против: {' '.join(neg[:4]) or '-'}")
        return action, score, reason

    def log(self, action: str, score: float, reason: str, text: str):
        """Лог с обоснованием: по нему видно, ПОЧЕМУ озвучено или нет."""
        tag = {SPEAK: "ОЗВУЧИТЬ", REFINE: "УТОЧНИТЬ", SKIP: "ПРОПУСТИТЬ"}[action]
        if action == SPEAK:
            logger.info("[Decider] %s %s | %r", tag, reason, (text or "")[:60])
        else:
            logger.debug("[Decider] %s %s | %r", tag, reason, (text or "")[:60])
