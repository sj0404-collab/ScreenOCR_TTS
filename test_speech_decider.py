# -*- coding: utf-8 -*-
"""Тест решателя: что озвучивать, а что нет."""
import io
import sys

if not (getattr(sys.stdout, "encoding", "") or "").lower().startswith("utf-8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                  errors="replace")

from speech_decider import SPEAK, REFINE, SKIP, SpeechDecider

d = SpeechDecider()

CASES = [
    # (описание, текст, kwargs, ожидаем_озвучивать)
    # ВАЖНО: мусор может получить решение refine — это «использовать как
    # уточнение, но НЕ озвучивать самому». Поэтому проверяем главное:
    # озвучивать такое нельзя.
    ("реплика голосом, есть точки",
     "We must leave the camp before nightfall.",
     dict(has_voice=True, voice_text="We must leave the camp before nightfall.",
          stability=0, screen_pos=1.0, age=0.1), True),
    ("реплика голосом, уточняем текстом",
     "Benny, take the old bridge.",
     dict(has_voice=True, voice_text="Benny take the old bridge",
          stability=0, screen_pos=0.95, age=0.2), True),
    ("то же, но голоса нет — только экран",
     "We must leave the camp before nightfall.",
     dict(has_voice=False, stability=3, screen_pos=0.95, age=0.1), True),
    ("короткий мелькающий интерфейс",
     "Settings",
     dict(has_voice=False, stability=1, screen_pos=0.9, age=0.1), False),
    ("цифры/иконки",
     "64Y CV- qb",
     dict(has_voice=False, stability=2, screen_pos=0.9, age=0.1), False),
    ("панель задач, одно слово, только что",
     "Поиск",
     dict(has_voice=False, stability=1, screen_pos=1.0, age=0.0), False),
    ("вопрос без голоса, стабильный",
     "Are you bringing the map?",
     dict(has_voice=False, stability=3, screen_pos=0.8, age=0.3), True),
    ("японский/китайский текст (иероглифы)",
     "三名の旅人",
     dict(has_voice=True, voice_text="三名の旅人",
          stability=0, screen_pos=0.9, age=0.1), True),
    ("свой эхо-текст",
     "Перевод: Перевод: Перевод",
     dict(has_voice=True, voice_text="Перевод", stability=0,
          screen_pos=0.9, age=0.1, is_echo=True), False),
    ("пустой текст",
     "   ",
     dict(has_voice=False, stability=2, screen_pos=0.9, age=0.1), False),
]


def main():
    ok = True
    for name, text, kw, want_speak in CASES:
        action, score, reason = d.decide(text, **kw)
        got_speak = (action == SPEAK)
        flag = "OK " if got_speak == want_speak else "ERR"
        if got_speak != want_speak:
            ok = False
        print(f"[{flag}] {name:38} -> {action:6} "
              f"озвучивать={got_speak} (ждали {want_speak})")
        print(f"        {reason}")

    # граница: порог настраивается под игру
    d2 = SpeechDecider(weights={"length": 4.0, "stability": 0.2,
                                "dialog_marks": 0.2, "words": 0.5,
                                "not_gibberish": 0.2, "screen_pos": 0.0,
                                "freshness": 0.0, "has_voice": 0.0,
                                "voice_text": 0.0, "overlap_voice": 0.0,
                                "not_echo": 0.0, "sentence_start": 0.0})
    a1, s1, _ = d2.decide("Benny wait for me",
                          stability=2, screen_pos=0.9)
    print(f"\nпорог настраивается: «Benny wait for me» -> {a1} ({s1:.2f})")
    if a1 != SPEAK:
        print("[ERR] сдвиг весов не изменил решение")
        ok = False
    else:
        print("[OK] веса настраиваются под игру")

    print("\n[OK] ТЕСТ ПРОЙДЕН" if ok else "\n[ERR] ЕСТЬ ПРОБЛЕМЫ")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
