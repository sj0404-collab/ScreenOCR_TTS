# -*- coding: utf-8 -*-
"""
Тест работы с субтитрами на экране: фолбэк и слияние с голосом.

Что проверяем:
  1) ФОЛБЭК. Голоса нет (тишина), но субтитры на экране есть -> реплика
     озвучена. Именно это пользователь просил: «если голосов нету, автоматом
     читает OCR, вдруг есть только субтитры».
  2) НЕ СПЕШИТ. Если голос только что был, субтитры НЕ озвучиваются вторым
     разом — иначе каждая реплика говорится дважды.
  3) СТАБИЛЬНОСТЬ. Текст, мелькающий на один кадр (элемент интерфейса),
     не озвучивается: нужен текст, продержавшийся в двух опросах.
  4) СЛИЯНИЕ. Когда голос и субтитры идут одновременно, берётся текст
     субтитров (он точнее), но только если он про ту же реплику.

Запуск: venv311\Scripts\python.exe test_subtitle_fallback.py
"""
import io
import sys
import threading
import time

if not (getattr(sys.stdout, "encoding", "") or "").lower().startswith("utf-8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                  errors="replace")

import subtitle_reader
from test_async_pipeline import FakeTTS, SlowTranslator
from voice_translator import VoiceTranslator

# Настоящий класс ДО подмены: FakeSubReader использует его статические
# методы, иначе после monkeypatch он бы смотрел на заглушку.
_REAL_READER = subtitle_reader.SubtitleReader


class FakeSubReader:
    """Подмена OCR с ТОЧНОЙ семантикой настоящего SubtitleReader:
    read() отдаёт текст только когда он ИЗМЕНИЛСЯ, current() — последний
    свежий текст. Это важно: стабильность проверяется через current().
    """

    def __init__(self, settings=None, region_ratio=None, **kw):
        self.script = []            # список текстов; None = субтитров нет
        self.i = 0
        self.lock = threading.Lock()
        self.last_text = ""
        self.last_change_at = 0.0
        self.active = kw.get("require_foreground", True) and True

    def is_active(self):
        return self.active

    def current_age(self):
        if not self.last_change_at:
            return 999.0
        return max(0.0, time.time() - self.last_change_at)

    def read(self, standalone: bool = False):
        with self.lock:
            t = self.script[self.i] if self.i < len(self.script) else None
            self.i += 1
        if not t:
            return ""
        if not _REAL_READER._looks_like_text(
                t, 0.9, min_words=2 if standalone else 1):
            return ""
        if t == self.last_text:
            return ""
        self.last_text = t
        self.last_change_at = time.time()
        return t

    def current(self, max_age: float = 3.0):
        if not self.last_text:
            return ""
        if time.time() - self.last_change_at > max_age:
            return ""
        return self.last_text


def make_vt():
    vt = VoiceTranslator()
    vt.set_languages("en", "ru")
    vt.tts = FakeTTS()
    vt.translator = SlowTranslator(delay=0.05)
    vt._sub_poll_sec = 0.05
    vt._sub_fallback_after = 0.3
    vt._running = True
    threading.Thread(target=vt._translate_worker, daemon=True).start()
    vt._tts_worker_alive = True
    threading.Thread(target=vt._tts_worker, daemon=True).start()
    return vt


def spoken_of(vt, n=1, timeout=6.0):
    end = time.time() + timeout
    while time.time() < end:
        with vt.tts.lock:
            if len(vt.tts.spoken) >= n:
                return list(vt.tts.spoken)
        time.sleep(0.05)
    with vt.tts.lock:
        return list(vt.tts.spoken)


def main():
    ok = True

    # ── 1) ФОЛБЭК: голоса нет, субтитры есть ──
    print("1) Голоса нет, на экране субтитры...")
    reader = FakeSubReader()
    reader.script = ["We must leave the camp before nightfall.",
                     "We must leave the camp before nightfall."]
    orig = subtitle_reader.SubtitleReader
    subtitle_reader.SubtitleReader = lambda s=None, r=None, **kw: reader
    try:
        vt = make_vt()
        vt._last_voice_at = time.time()      # стартуем «молчащим»
        vt.set_subtitles(True)
        got = spoken_of(vt, 1)
        vt._running = False
        if not got:
            print("[ERR] субтитры не озвучены, хотя голоса не было")
            ok = False
        else:
            print(f"[OK] фолбэк сработал: «{got[0]}»")
    finally:
        subtitle_reader.SubtitleReader = orig

    # ── 2) НЕ СПЕШИТ: голос только что был ──
    print("\n2) Голос только что прозвучал, субтитры те же...")
    reader = FakeSubReader()
    reader.script = ["We must leave the camp before nightfall."] * 8
    subtitle_reader.SubtitleReader = lambda s=None, r=None, **kw: reader
    try:
        vt = make_vt()
        vt._last_voice_at = time.time() + 30     # свежий голос
        vt.set_subtitles(True)
        time.sleep(1.2)
        vt._running = False
        got = list(vt.tts.spoken)
        if got:
            print(f"[ERR] субтитры озвучены поверх голоса: {got}")
            ok = False
        else:
            print("[OK] повторного озвучивания нет — субтитры ушли в уточнение")
    finally:
        subtitle_reader.SubtitleReader = orig

    # ── 3) СТАБИЛЬНОСТЬ: мелькающий текст интерфейса ──
    print("\n3) Текст меняется каждый кадр (интерфейс)...")
    reader = FakeSubReader()
    reader.script = ["Settings", "Inventory", "Quests", "Map", "Party",
                     "Level 42", "HP", "MP", "Quests updated", "New item!",
                     "Achievement", "Save game?"] * 3
    subtitle_reader.SubtitleReader = lambda s=None, r=None, **kw: reader
    try:
        vt = make_vt()
        vt._last_voice_at = time.time() + 30
        vt.set_subtitles(True)
        time.sleep(1.5)
        vt._running = False
        got = list(vt.tts.spoken)
        if got:
            print(f"[ERR] мигающий интерфейс озвучен: {got}")
            ok = False
        else:
            print("[OK] нестабильный текст не озвучен")
    finally:
        subtitle_reader.SubtitleReader = orig

    # ── 4) СЛИЯНИЕ: субтитры точнее, но только про ту же реплику ──
    print("\n4) Слияние голоса и субтитров...")
    vt = make_vt()

    # 4a) субтитры про ту же реплику -> берём их (точные имена/кавычки)
    vt._sub_pending = "Benny, take the old bridge."
    vt._sub_pending_at = time.time()
    txt, fused = vt._resolve_text("Benny take the old bridge")
    if txt == "Benny, take the old bridge." and fused:
        print("[OK] субтитры уточнили распознавание (запятая, имя)")
    else:
        print(f"[ERR] слияние не сработало: {txt!r} fused={fused}")
        ok = False

    # 4b) субтитры ПРО ДРУГУЮ реплику -> не подменяем голос
    vt._sub_pending = "The shop closes at midnight."
    vt._sub_pending_at = time.time()
    txt2, fused2 = vt._resolve_text("Benny take the old bridge")
    if txt2 == "Benny take the old bridge" and not fused2:
        print("[OK] чужие субтитры не подменили реплику голоса")
    else:
        print(f"[ERR] подмена чужой репликой: {txt2!r} fused={fused2}")
        ok = False

    # 4c) протухшие субтитры игнорируются
    vt._sub_pending = "Benny take the old bridge"
    vt._sub_pending_at = time.time() - 30
    txt3, fused3 = vt._resolve_text("Benny take the old bridge")
    if not fused3:
        print("[OK] протухшие субтитры (старше 3 c) проигнорированы")
    else:
        print("[ERR] протухшие субтитры подмешаны")
        ok = False

    # ── 5) фильтр мусора OCR ──
    print("\n5) Фильтр мусорных кадров OCR...")

    def looks(raw, **kw):
        return _REAL_READER._looks_like_text(raw, 0.9, **kw)

    cases = [
        # (текст, ожидаем, min_words, описание)
        ("We should meet at the old bridge tomorrow.", True, 1, "реплика"),
        ("Benny, wait!", True, 1, "короткая реплика"),
        ("Wait!", True, 1, "одно слово, но при слиянии с голосом"),
        ("Settings", True, 1, "одно слово при слиянии — допустим"),
        ("Settings", False, 2, "одно слово для фолбэка — это меню"),
        ("Inventory", False, 2, "пункт меню для фолбэка"),
        ("", False, 1, "пусто"),
        ("X", False, 1, "слишком коротко"),
        ("L V I 3 7 K 9 2", False, 1, "цифры/иконки"),
    ]
    for raw, want, mw, why in cases:
        got_ok = looks(raw, min_words=mw)
        flag = "OK " if got_ok == want else "ERR"
        if got_ok != want:
            ok = False
        print(f"  [{flag}] min_words={mw} {raw!r:44} -> {got_ok} "
              f"({why})")

    # ── 6) БЫСТРАЯ СВЕРКА: озвучка ждёт субтитры, но недолго ──
    print("\n6) Сверка голоса с субтитрами перед озвучкой...")

    # 6a) субтитры ОПОЗДАЮТ (OCR медленнее голоса) — ждём их и сверяем
    reader = FakeSubReader()
    reader.script = ["Benny, take the old bridge."] * 6
    subtitle_reader.SubtitleReader = lambda s=None, r=None, **kw: reader
    try:
        vt = make_vt()
        vt._sub_reader = reader
        vt._sub_enabled = True
        vt._sub_pending = ""
        vt._sub_pending_at = 0.0

        def late_subs():
            time.sleep(0.25)
            vt._sub_pending = "Benny, take the old bridge."
            vt._sub_pending_at = time.time()

        threading.Thread(target=late_subs, daemon=True).start()
        t0 = time.time()
        vt._await_subtitles(timeout=0.6)
        waited = time.time() - t0
        merged, fused = vt._resolve_text("Benny take the old bridge")
        if fused and merged == "Benny, take the old bridge.":
            print(f"[OK] дождались опоздавших субтитров за {waited:.2f}c "
                  f"и сверили реплику")
        else:
            print(f"[ERR] сверка не сработала: {merged!r} fused={fused}")
            ok = False
    finally:
        subtitle_reader.SubtitleReader = orig

    # 6b) субтитров нет — ждём НЕ дольше таймаута (задержка не растёт)
    reader = FakeSubReader()
    reader.script = []
    subtitle_reader.SubtitleReader = lambda s=None, r=None, **kw: reader
    try:
        vt = make_vt()
        vt._sub_reader = reader
        vt._sub_enabled = True
        vt._sub_pending = ""
        vt._sub_pending_at = 0.0
        t0 = time.time()
        got = vt._await_subtitles(timeout=0.4)
        waited = time.time() - t0
        if got:
            print(f"[ERR] ждали субтитры, которых нет: {got!r}")
            ok = False
        elif waited > 0.75:
            print(f"[ERR] ожидание субтитров затянулось: {waited:.2f}c")
            ok = False
        else:
            print(f"[OK] без субтитров ждём {waited:.2f}c и озвучиваем по голосу")
    finally:
        subtitle_reader.SubtitleReader = orig

    # 6c) окно игры НЕ в фокусе — не ждём (на экране чужое окно)
    reader = FakeSubReader()
    reader.active = False
    reader.script = ["Esc interrupt"] * 6
    subtitle_reader.SubtitleReader = lambda s=None, r=None, **kw: reader
    try:
        vt = make_vt()
        vt._sub_reader = reader
        vt._sub_enabled = True
        vt._sub_pending = "Esc interrupt"
        vt._sub_pending_at = time.time()
        t0 = time.time()
        vt._await_subtitles(timeout=2.0)
        waited = time.time() - t0
        if waited > 0.3:
            print(f"[ERR] ждём субтитры, хотя игра не в фокусе: {waited:.2f}c")
            ok = False
        else:
            print(f"[OK] игра не в фокусе — сверка пропущена за {waited:.2f}c")
    finally:
        subtitle_reader.SubtitleReader = orig

    # 6d) голос + субтитры одновременно: НЕ должно быть второй озвучки
    print("\n7) Сценарий «голос + субтитры»: одна озвучка, не две...")
    reader = FakeSubReader()
    reader.script = ["Benny, take the old bridge."] * 12
    subtitle_reader.SubtitleReader = lambda s=None, r=None, **kw: reader
    try:
        vt = make_vt()
        vt._sub_reader = reader
        vt._sub_enabled = True
        vt._sub_pending = "Benny, take the old bridge."
        vt._sub_pending_at = time.time()
        vt.set_subtitles(True)

        # Имитируем непрерывную речь: STT обновляет _last_voice_at, пока
        # персонаж говорит. Пока он говорит, фолбэк по субтитрам молчит.
        stop_talk = threading.Event()

        def talking():
            while not stop_talk.is_set():
                vt._last_voice_at = time.time()
                time.sleep(0.1)

        th = threading.Thread(target=talking, daemon=True)
        th.start()
        time.sleep(1.5)
        stop_talk.set()
        th.join(timeout=1)
        got = list(vt.tts.spoken)
        if got:
            print(f"[ERR] реплика озвучена дважды (голос + OCR): {got}")
            ok = False
        else:
            print("[OK] двойного озвучивания нет: субтитры лишь уточняют "
                  "текст, голос уже озвучен")
    finally:
        subtitle_reader.SubtitleReader = orig

    # ── 8) ЗЕЛЁНАЯ РАМКА: сканируем внутри неё, а не окна целиком ──
    print("\n8) Область сканирования из зелёной рамки...")
    r = _REAL_READER(target_pid=1176, require_foreground=False)

    # рамка из regions.json лежит в координатах экрана
    n = r.set_regions([{"name": "Screen OCR + TTS", "x": 0, "y": 9,
                        "width": 671, "height": 647, "locked": False}])
    boxes = r._boxes()
    if n != 1 or len(boxes) != 1:
        print(f"[ERR] рамка не применилась: n={n}, boxes={boxes}")
        ok = False
    elif boxes[0] != {"left": 0, "top": 9, "width": 671, "height": 647}:
        print(f"[ERR] неверные координаты рамки: {boxes[0]}")
        ok = False
    else:
        print(f"[OK] сканируем только рамку: {boxes[0]}")

    # мусорные рамки (слишком маленькие / без размеров) отбрасываются
    r.set_regions([{"x": 0, "y": 0, "width": 10, "height": 5},
                   {"x": 5, "y": 5}])
    if r._boxes():
        print(f"[ERR] мусорные рамки приняты: {r._boxes()}")
        ok = False
    else:
        print("[OK] вырожденные рамки отброшены")

    # без рамок — работаем по окну игры
    r.set_regions(None)
    r._window_rect = lambda: {"left": 0, "top": 0, "width": 800, "height": 600}
    if r._boxes() != [{"left": 0, "top": 0, "width": 800, "height": 600}]:
        print(f"[ERR] без рамки должно быть окно игры: {r._boxes()}")
        ok = False
    else:
        print("[OK] без рамки сканируется окно игры")

    # несколько рамок: выбирается самая «репличная»
    class _FakeOcr:
        def __init__(self, mapping):
            self.mapping = mapping

        def recognize(self, img):
            return self.mapping.get(img.size, ""), 90.0

    r2 = _REAL_READER(target_pid=1176, require_foreground=False)
    r2.set_regions([{"x": 0, "y": 0, "width": 671, "height": 647},
                    {"x": 700, "y": 0, "width": 600, "height": 647}])
    # разные размеры кадров -> разные «области»
    sizes = [(590, 109), (520, 109)]

    def fake_grab(box=None, wide=False, **kw):
        class Img:
            def __init__(self, size):
                self.size = size
        return Img(sizes[0] if (box or {}).get("left") == 0 else sizes[1])

    r2._grab = fake_grab
    r2._ensure_ocr = lambda: _FakeOcr({sizes[0]: "Settings",
                                       sizes[1]: "We must leave the camp!"})
    got = r2.read(standalone=True)
    if "camp" in got.lower() or "camp" in (got or "").lower():
        print(f"[OK] из двух рамок выбрана репличная: {got!r}")
    else:
        print(f"[ERR] выбрана не та рамка: {got!r}")
        ok = False

    vt._running = False
    print("\n[OK] ТЕСТ ПРОЙДЕН" if ok else "\n[ERR] ЕСТЬ ПРОБЛЕМЫ")
    return 0 if ok else 1

if __name__ == "__main__":
    sys.exit(main())
