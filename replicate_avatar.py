# -*- coding: utf-8 -*-
"""Replicate API — генерация аватара с эмоциями через AI-модели.

Использует официальный replicate Python пакет.
Модель: black-forest-labs/flux-schnell (быстрая, ~1с).
Кэширование в models/avatar/.
"""
import os
import io
import json
import logging
import threading

from PIL import Image

logger = logging.getLogger(__name__)

AVATAR_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models", "avatar")
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

EMOTIONS = ["neutral", "happy", "sad", "angry", "surprised", "thinking", "speaking", "wink", "sleeping"]

EMOTION_PROMPTS = {
    "neutral":   "neutral calm gentle expression, soft closed mouth, relaxed face",
    "happy":     "very happy cheerful big smile, eyes happily closed as upward arcs, joyful expression",
    "sad":       "sad crying expression, tears streaming down cheeks, downturned mouth, droopy sad eyes",
    "angry":     "angry annoyed expression, furrowed eyebrows, frowning, puffed cheeks, tense face",
    "surprised": "surprised shocked expression, very wide open eyes, open round mouth, raised eyebrows",
    "thinking":  "thinking thoughtful expression, looking up to the side, slight pout, finger near chin",
    "speaking":  "talking expression, mouth open mid speech showing teeth, friendly lively animated face",
    "wink":      "playful wink, one eye closed winking, cheerful smile, tongue out slightly",
    "sleeping":  "sleeping peacefully, eyes closed, relaxed calm face, small zzz floating nearby",
}

EMOTION_LABELS = {
    "neutral": "Нейтрально", "happy": "Радость", "sad": "Грусть",
    "angry": "Злость", "surprised": "Удивление", "thinking": "Задумчивость",
    "speaking": "Речь", "wink": "Подмигивание", "sleeping": "Сон",
}

PRESET_CHARACTERS = [
    {
        "name": "Мию — розовые волосы",
        "slug": "default",
        "prompt": "cute young anime girl, short bob haircut, pastel pink hair, big turquoise eyes, small star hair clip on side, wearing a white hoodie with light blue hood, head and shoulders bust portrait, centered, facing forward, looking at viewer, soft cel-shaded anime illustration, clean thick lineart, high quality",
        "seed": 7,
    },
    {
        "name": "Реалистичный парень",
        "slug": "realistic_man",
        "prompt": "portrait of a young man, short brown hair, green eyes, casual jacket, confident smile, photorealistic, studio lighting, head and shoulders, centered, facing camera, high detail skin texture, professional photography",
        "seed": 42,
    },
    {
        "name": "Реалистичная девушка",
        "slug": "realistic_woman",
        "prompt": "portrait of a young woman, long auburn hair, blue eyes, natural makeup, warm smile, photorealistic, soft lighting, head and shoulders, centered, facing camera, high detail, professional photography",
        "seed": 13,
    },
    {
        "name": "Кот-волшебник",
        "slug": "cat_wizard",
        "prompt": "portrait of an anthropomorphic cat wizard, fluffy orange fur, wise golden eyes, wearing a dark blue pointed wizard hat with gold stars, mystical expression, detailed fur texture, fantasy illustration style, head and shoulders, centered",
        "seed": 99,
    },
    {
        "name": "Дракон",
        "slug": "dragon",
        "prompt": "portrait of a cute baby dragon, emerald green scales, large golden eyes, small horns, wisps of smoke from nostrils, friendly expression, fantasy illustration, detailed scales, head and shoulders, centered, facing viewer",
        "seed": 77,
    },
    {
        "name": "Рыцарь",
        "slug": "knight",
        "prompt": "portrait of a medieval knight, silver plate armor, blue plume on helmet visor, stern noble expression, battle-scarred face visible, oil painting style, dramatic lighting, head and shoulders, centered",
        "seed": 55,
    },
]

_replicate_client = None
_replicate_lock = threading.Lock()


def _get_client():
    global _replicate_client
    if _replicate_client is not None:
        return _replicate_client
    with _replicate_lock:
        if _replicate_client is not None:
            return _replicate_client
        try:
            import replicate as rpl
            key = get_api_key()
            if key:
                os.environ["REPLICATE_API_TOKEN"] = key
            _replicate_client = rpl
            logger.info("[REPLICATE] replicate package loaded")
            return _replicate_client
        except ImportError:
            logger.error("[REPLICATE] 'replicate' package not installed. Run: pip install replicate")
            raise


def get_api_key():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        return cfg.get("replicate_api_key", "")
    except Exception:
        return ""


def set_api_key(key):
    try:
        cfg = {}
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        cfg["replicate_api_key"] = key
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        os.environ["REPLICATE_API_TOKEN"] = key
        global _replicate_client
        _replicate_client = None
        logger.info("[REPLICATE] API key saved")
    except Exception as e:
        logger.error(f"[REPLICATE] Failed to save API key: {e}")


def get_avatar_dir():
    os.makedirs(AVATAR_DIR, exist_ok=True)
    return AVATAR_DIR


def get_preset(slug):
    for p in PRESET_CHARACTERS:
        if p["slug"] == slug:
            return p
    return PRESET_CHARACTERS[0]


def get_path(emotion, slug="default"):
    """Get path using unified asset structure: models/avatar/{slug}/{emotion}.png"""
    import avatar_assets
    return avatar_assets.get_path(emotion, slug)


def is_cached(emotion, slug="default"):
    import avatar_assets
    return avatar_assets.is_cached(emotion, slug)


def _auto_chroma_alpha(img, tolerance=28):
    """Flood-fill удаление фона от углов."""
    img = img.convert("RGBA")
    w, h = img.size
    px = img.load()
    bg = px[2, 2][:3]

    from collections import deque
    visited = set()
    queue = deque()
    seeds = [(2, 2), (w - 3, 2), (2, h - 3), (w - 3, h - 3),
             (w // 2, 2), (w // 2, h - 3)]
    for sx, sy in seeds:
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

    from PIL import ImageFilter
    return out.filter(ImageFilter.SMOOTH)


def _run_flux(prompt, seed=None, aspect_ratio="3:4"):
    """Запуск Flux Schnell через replicate пакет. Возвращает URL картинки.
    Retry с задержкой при 429."""
    rpl = _get_client()
    key = get_api_key()
    if key:
        os.environ["REPLICATE_API_TOKEN"] = key

    input_data = {
        "prompt": prompt,
        "num_outputs": 1,
        "aspect_ratio": aspect_ratio,
        "go_fast": True,
    }
    if seed is not None:
        input_data["seed"] = seed

    logger.debug(f"[REPLICATE] Running flux-schnell, prompt={prompt[:80]}...")

    import time
    for attempt in range(5):
        try:
            output = rpl.run("black-forest-labs/flux-schnell", input=input_data)
            if isinstance(output, list) and output:
                return output[0]
            return output
        except Exception as e:
            err_str = str(e)
            if "429" in err_str or "throttl" in err_str.lower():
                wait = 12 * (attempt + 1)
                logger.warning(f"[REPLICATE] Rate limited, waiting {wait}s (attempt {attempt+1}/5)")
                time.sleep(wait)
            else:
                raise
    raise RuntimeError("Replicate rate limit exceeded after 5 retries")


def generate_base(slug="default", api_key=None, seed=None):
    """Генерирует базовое изображение персонажа (нейтральная эмоция)."""
    preset = get_preset(slug)
    if seed is None:
        seed = preset["seed"]

    dst = get_path("neutral", slug)
    if os.path.exists(dst):
        return dst
    os.makedirs(os.path.dirname(dst), exist_ok=True)

    key = api_key or get_api_key()
    if not key:
        raise RuntimeError("Replicate API key not set. Go to Settings → Replicate API Key.")
    os.environ["REPLICATE_API_TOKEN"] = key

    prompt = preset["prompt"]
    logger.info(f"[REPLICATE] Generating base for '{preset['name']}'...")

    try:
        image_url = _run_flux(
            prompt + ", solid pastel gray background, bust portrait, high quality",
            seed=seed,
        )
        import urllib.request
        data = urllib.request.urlopen(image_url, timeout=60).read()
        img = Image.open(io.BytesIO(data)).convert("RGBA")
        img = _auto_chroma_alpha(img)
        img.save(dst, "PNG")
        logger.info(f"[REPLICATE] Base saved: {dst}")
        return dst
    except Exception as e:
        logger.error(f"[REPLICATE] Base generation failed: {e}")
        raise


def generate_emotion(emotion, slug="default", api_key=None, seed=None):
    """Генерирует изображение эмоции."""
    dst = get_path(emotion, slug)
    if os.path.exists(dst):
        return dst
    os.makedirs(os.path.dirname(dst), exist_ok=True)

    base_path = get_path("neutral", slug)
    if not os.path.exists(base_path):
        generate_base(slug, api_key, seed)

    key = api_key or get_api_key()
    if not key:
        raise RuntimeError("Replicate API key not set.")
    os.environ["REPLICATE_API_TOKEN"] = key

    preset = get_preset(slug)
    if seed is None:
        seed = preset["seed"]

    em_prompt = EMOTION_PROMPTS.get(emotion, EMOTION_PROMPTS["neutral"])
    full_prompt = f"{preset['prompt']}, {em_prompt}, consistent character, same art style"

    logger.info(f"[REPLICATE] Generating emotion '{emotion}' for '{slug}'...")

    try:
        image_url = _run_flux(
            full_prompt + ", solid pastel gray background, bust portrait, high quality",
            seed=seed + hash(emotion) % 1000,
        )
        import urllib.request
        data = urllib.request.urlopen(image_url, timeout=60).read()
        img = Image.open(io.BytesIO(data)).convert("RGBA")
        img = _auto_chroma_alpha(img)
        img.save(dst, "PNG")
        logger.info(f"[REPLICATE] Emotion '{emotion}' saved: {dst}")
        return dst
    except Exception as e:
        logger.error(f"[REPLICATE] Emotion '{emotion}' failed: {e}")
        raise


def ensure_all(slug="default", api_key=None, seed=None,
               on_done=None, on_progress=None):
    """Генерирует все 9 эмоций. on_done(emotion, error) вызывается для каждой.
    Задержка 12с между запросами для соблюдения rate limit 6 req/min."""
    import time
    preset = get_preset(slug)
    if seed is None:
        seed = preset["seed"]

    first = True
    for em in EMOTIONS:
        if is_cached(em, slug):
            logger.info(f"[REPLICATE] {em} cached, skipping")
            if on_done:
                on_done(em, None)
            continue
        if not first:
            logger.info(f"[REPLICATE] Waiting 12s for rate limit...")
            time.sleep(12)
        first = False
        try:
            if on_progress:
                on_progress(f"Generating {em}...")
            generate_emotion(em, slug, api_key, seed)
            if on_done:
                on_done(em, None)
        except Exception as e:
            logger.error(f"[REPLICATE] {em} failed: {e}")
            if on_done:
                on_done(em, e)
