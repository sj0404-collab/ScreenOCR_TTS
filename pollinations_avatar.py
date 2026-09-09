# -*- coding: utf-8 -*-
"""Генерация аватара и эмоций через Pollinations API (text-to-image, бесплатно).

Идея консистентности:
  • Один фиксированный CHARACTER_PROMPT (внешность не меняется).
  • Один seed (структура лица/композиция стабильны).
  • От эмоции к эмоции меняется ТОЛЬКО фраза с выражением лица.

Все картинки кэшируются в <app_dir>/models/avatar/<slug>_<emotion>.png — после
первой генерации приложение работает без интернета.
"""
import os
import io
import time
import urllib.request
import urllib.parse
import logging
import threading

from PIL import Image

logger = logging.getLogger(__name__)

POLLINATIONS_URL = "https://image.pollinations.ai/prompt/{prompt}"
MODEL = "sana"
DEFAULT_WIDTH = 520
DEFAULT_HEIGHT = 600
DEFAULT_SEED = 7
TIMEOUT = 180

CHARACTER_PROMPT = (
    "cute young anime vtuber girl, short bob haircut, pastel pink hair, "
    "big turquoise eyes, small star hair clip on side, wearing a white hoodie "
    "with light blue hood, head and shoulders bust portrait, centered, "
    "facing forward, looking at viewer, soft cel-shaded anime illustration, "
    "clean thick lineart, high quality, flat lighting"
)
BG_PROMPT = "solid pastel mint green background, simple flat backdrop"

EMOTION_PROMPTS = {
    "neutral":   "neutral calm gentle expression, soft closed mouth, relaxed",
    "happy":     "very happy cheerful big smile, eyes happily closed as upward arcs, joyful",
    "sad":       "sad crying expression, tears streaming down cheeks, downturned mouth, droopy sad eyes",
    "angry":     "angry annoyed expression, furrowed eyebrows, frowning, puffed cheeks",
    "surprised": "surprised shocked expression, very wide open eyes, open round mouth, raised eyebrows",
    "thinking":  "thinking thoughtful expression, looking up to the side, slight pout, finger near chin",
    "speaking":  "talking expression, mouth open mid speech showing teeth, friendly lively",
    "wink":      "playful wink, one eye closed winking, cheerful smile, tongue out slightly",
    "sleeping":  "sleeping peacefully, eyes closed, relaxed calm face, small zzz floating",
}

EMOTIONS = list(EMOTION_PROMPTS.keys())

PRESET_CHARACTERS = [
    {
        "slug": "default",
        "name": "Мию — розовые волосы, бирюзовые глаза",
        "seed": 7,
        "character": ("cute young anime vtuber girl, short bob haircut, "
                      "pastel pink hair, big turquoise eyes, small star hair "
                      "clip on side, wearing a white hoodie with light blue hood"),
    },
    {
        "slug": "akiko",
        "name": "Акико — чёрные волосы, карие глаза",
        "seed": 13,
        "character": ("cute young anime vtuber girl, long straight black hair, "
                      "warm brown eyes, red ribbon hair accessory, wearing a red "
                      "sailor school uniform"),
    },
    {
        "slug": "sora",
        "name": "Сора — голубые волосы, синие глаза",
        "seed": 21,
        "character": ("cute young anime vtuber girl, short sky blue hair, bright "
                      "blue eyes, white cat-ear headphones, wearing a blue "
                      "techwear jacket"),
    },
    {
        "slug": "yuki",
        "name": "Юки — серебристые волосы, красные глаза",
        "seed": 33,
        "character": ("cute young anime vtuber girl, long silver white hair, "
                      "crimson red eyes, black hairband, wearing a black gothic "
                      "dress with white frills"),
    },
    {
        "slug": "rin",
        "name": "Рин — оранжевые хвостики, зелёные глаза",
        "seed": 42,
        "character": ("cute young anime vtuber girl, messy orange hair in twin "
                      "tails, bright green eyes, wearing a green sporty hoodie"),
    },
    {
        "slug": "haru",
        "name": "Хару — блондинка, фиолетовые глаза",
        "seed": 55,
        "character": ("cute young anime vtuber girl, blonde wavy hair, violet "
                      "purple eyes, wearing a cozy purple sweater"),
    },
]


def get_avatar_dir():
    """Возвращает директорию для кэша аватаров (рядом с app_dir)."""
    app_dir = os.path.dirname(os.path.abspath(__file__))
    avatar_dir = os.path.join(app_dir, "models", "avatar")
    os.makedirs(avatar_dir, exist_ok=True)
    return avatar_dir


def get_preset(slug):
    for p in PRESET_CHARACTERS:
        if p["slug"] == slug:
            return p
    return PRESET_CHARACTERS[0]


def _build_url(prompt, width, height, seed, model=MODEL):
    enc = urllib.parse.quote(prompt, safe="")
    params = urllib.parse.urlencode({
        "width": width, "height": height,
        "seed": seed, "model": model, "nologo": "true",
    })
    return f"{POLLINATIONS_URL.format(prompt=enc)}?{params}"


def _fetch(url, timeout=TIMEOUT):
    logger.debug(f"[AVATAR] Загрузка: {url[:120]}...")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    return urllib.request.urlopen(req, timeout=timeout).read()


def _auto_chroma_alpha(img, tolerance=28):
    """Авто-удаление фона: flood-fill от углов, убирает только 연결ный фон."""
    img = img.convert("RGBA")
    w, h = img.size
    px = img.load()

    bg = px[2, 2][:3]

    from collections import deque
    visited = set()
    queue = deque()
    seed_points = [(2, 2), (w - 3, 2), (2, h - 3), (w - 3, h - 3),
                   (w // 2, 2), (w // 2, h - 3)]
    for sx, sy in seed_points:
        r, g, b, a = px[sx, sy]
        if (abs(r - bg[0]) < tolerance and
                abs(g - bg[1]) < tolerance and
                abs(b - bg[2]) < tolerance):
            queue.append((sx, sy))
            visited.add((sx, sy))

    out = img.copy()
    op = out.load()
    changed = 0
    while queue:
        x, y = queue.popleft()
        r, g, b, a = op[x, y]
        if (abs(r - bg[0]) < tolerance and
                abs(g - bg[1]) < tolerance and
                abs(b - bg[2]) < tolerance):
            op[x, y] = (r, g, b, 0)
            changed += 1
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                nx, ny = x + dx, y + dy
                if 0 <= nx < w and 0 <= ny < h and (nx, ny) not in visited:
                    visited.add((nx, ny))
                    queue.append((nx, ny))

    logger.debug(f"[AVATAR] Flood-fill chroma: {changed} пикселей удалено")
    from PIL import ImageFilter
    return out.filter(ImageFilter.SMOOTH)


def get_path(emotion, slug="default"):
    """Get path using unified asset structure: models/avatar/{slug}/{emotion}.png"""
    import avatar_assets
    return avatar_assets.get_path(emotion, slug)


def is_cached(emotion, slug="default"):
    import avatar_assets
    return avatar_assets.is_cached(emotion, slug)


def generate_emotion(emotion, seed=DEFAULT_SEED, character_prompt=None,
                     slug="default", width=DEFAULT_WIDTH, height=DEFAULT_HEIGHT,
                     chroma_key=True, chroma_tolerance=28, on_progress=None):
    """Генерирует (или берёт из кэша) картинку эмоции. Возвращает путь к PNG."""
    dst = get_path(emotion, slug)
    if os.path.exists(dst):
        logger.info(f"[AVATAR] Кэш {slug}/{emotion}: {dst}")
        return dst
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if on_progress:
        on_progress(f"Генерирую «{emotion}»...")
    char = character_prompt or CHARACTER_PROMPT
    prompt = f"{char}, {EMOTION_PROMPTS.get(emotion, EMOTION_PROMPTS['neutral'])}, {BG_PROMPT}"
    last_err = None
    for attempt in range(3):
        try:
            t0 = time.time()
            url = _build_url(prompt, width, height, seed)
            data = _fetch(url)
            img = Image.open(io.BytesIO(data)).convert("RGBA")
            if chroma_key:
                img = _auto_chroma_alpha(img, chroma_tolerance)
            img.save(dst, "PNG")
            elapsed = time.time() - t0
            logger.info(f"[AVATAR] {slug}/{emotion} сгенерирован за {elapsed:.1f}s → {dst}")
            return dst
        except Exception as e:
            last_err = e
            logger.warning(f"[AVATAR] {slug}/{emotion} попытка {attempt + 1}/3 не удалась: {e}")
            time.sleep(1.5 * (attempt + 1))
    logger.error(f"[AVATAR] Не удалось сгенерировать {slug}/{emotion}: {last_err}")
    raise RuntimeError(f"Не удалось сгенерировать {slug}/{emotion}: {last_err}")


def ensure_all(seed=DEFAULT_SEED, character_prompt=None, slug="default",
               width=DEFAULT_WIDTH, height=DEFAULT_HEIGHT, chroma_key=True,
               on_progress=None, on_done=None):
    """Генерит все недостающие эмоции выбранного персонажа (в фоне)."""
    def _work():
        ok, failed = [], []
        for em in EMOTIONS:
            try:
                generate_emotion(em, seed=seed, character_prompt=character_prompt,
                                 slug=slug, width=width, height=height,
                                 chroma_key=chroma_key, on_progress=on_progress)
                ok.append(em)
                if on_done:
                    on_done(em, None)
            except Exception as e:
                failed.append((em, e))
                if on_done:
                    on_done(em, e)
        logger.info(f"[AVATAR] Генерация {slug}: ok={len(ok)}, failed={len(failed)}")
        if failed:
            for em, err in failed:
                logger.error(f"[AVATAR]   {em}: {err}")

    t = threading.Thread(target=_work, daemon=True, name="avatar-gen")
    t.start()
    logger.info(f"[AVATAR] Запущена генерация персонажа '{slug}' в фоне")
    return t
