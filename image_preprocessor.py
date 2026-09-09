"""
Image Preprocessor — RGB/HSV фильтры для улучшения OCR.

Вдохновлено MORT (Monkeyhead's OCR Realtime Translator):
- RGB фильтры: усиление контраста по каналам
- HSV фильтры: выделение текста по цвету фона
- Adaptive thresholding: бинаризация для чёткого текста
- Edge detection: усиление границ букв

Полностью оффлайн, numpy + PIL.
"""
import logging
import numpy as np
from PIL import Image, ImageEnhance, ImageFilter

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# RGB FILTERS
# ═══════════════════════════════════════════════════════════════

def rgb_auto_contrast(img: Image.Image, strength: float = 1.5) -> Image.Image:
    """Автоматический контраст по каждому RGB каналу растягивает гистограмму."""
    arr = np.array(img, dtype=np.float32)
    for c in range(3):
        channel = arr[:, :, c]
        lo, hi = np.percentile(channel, (1, 99))
        if hi - lo < 10:
            continue
        channel = np.clip((channel - lo) / (hi - lo) * 255, 0, 255)
        arr[:, :, c] = channel
    return Image.fromarray(arr.astype(np.uint8))


def rgb_channel_boost(img: Image.Image, r: float = 1.0, g: float = 1.0, b: float = 1.0) -> Image.Image:
    """Усиление отдельных RGB каналов. r/g/b = 1.0 без изменений."""
    arr = np.array(img, dtype=np.float32)
    arr[:, :, 0] = np.clip(arr[:, :, 0] * r, 0, 255)
    arr[:, :, 1] = np.clip(arr[:, :, 1] * g, 0, 255)
    arr[:, :, 2] = np.clip(arr[:, :, 2] * b, 0, 255)
    return Image.fromarray(arr.astype(np.uint8))


def rgb_invert(img: Image.Image) -> Image.Image:
    """Инверсия цветов — для тёмного текста на светлом фоне."""
    arr = np.array(img)
    return Image.fromarray(255 - arr)


# ═══════════════════════════════════════════════════════════════
# HSV FILTERS
# ═══════════════════════════════════════════════════════════════

def _rgb_to_hsv(arr: np.ndarray) -> np.ndarray:
    """RGB → HSV (H: 0-360, S: 0-1, V: 0-1)."""
    arr = arr.astype(np.float32) / 255.0
    r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
    mx = np.maximum(np.maximum(r, g), b)
    mn = np.minimum(np.minimum(r, g), b)
    diff = mx - mn

    h = np.zeros_like(mx)
    s = np.zeros_like(mx)
    v = mx

    mask = diff > 0
    s[mask] = diff[mask] / mx[mask]

    r_eq = mask & (mx == r)
    g_eq = mask & (mx == g) & ~r_eq
    b_eq = mask & (mx == b) & ~r_eq & ~g_eq

    h[r_eq] = 60 * (((g[r_eq] - b[r_eq]) / diff[r_eq]) % 6)
    h[g_eq] = 60 * (((b[g_eq] - r[g_eq]) / diff[g_eq]) + 2)
    h[b_eq] = 60 * (((r[b_eq] - g[b_eq]) / diff[b_eq]) + 4)

    return np.stack([h, s, v], axis=-1)


def _hsv_to_rgb(hsv: np.ndarray) -> np.ndarray:
    """HSV → RGB."""
    h, s, v = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    h = h / 60.0
    i = np.floor(h).astype(int) % 6
    f = h - np.floor(h)
    p = v * (1 - s)
    q = v * (1 - f * s)
    t = v * (1 - (1 - f) * s)

    rgb = np.zeros_like(hsv)
    for idx, (r, g, b) in enumerate([(v, t, p), (q, v, p), (p, v, t), (p, q, v), (t, p, v), (v, p, q)]):
        mask = i == idx
        rgb[:, :, 0][mask] = r[mask]
        rgb[:, :, 1][mask] = g[mask]
        rgb[:, :, 2][mask] = b[mask]

    return np.clip(rgb * 255, 0, 255).astype(np.uint8)


def hsv_enhance_text(img: Image.Image, saturation: float = 1.2, value: float = 1.1) -> Image.Image:
    """Усиление текста через HSV: увеличение насыщенности и яркости."""
    arr = np.array(img)
    hsv = _rgb_to_hsv(arr)
    hsv[:, :, 1] = np.clip(hsv[:, :, 1] * saturation, 0, 1)
    hsv[:, :, 2] = np.clip(hsv[:, :, 2] * value, 0, 1)
    return Image.fromarray(_hsv_to_rgb(hsv))


def hsv_isolate_color(img: Image.Image, hue_center: float, hue_range: float = 30,
                       sat_min: float = 0.2) -> Image.Image:
    """Выделение текста определённого цвета по HSV.
    
    hue_center: центр оттенка в градусах (0-360)
    hue_range: допуск +/- от центра
    sat_min: минимальная насыщенность (фильтр серого)
    """
    arr = np.array(img)
    hsv = _rgb_to_hsv(arr)
    h, s, v = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]

    # Проверяем попадание оттенка в диапazon (с учётом цикличности)
    h_diff = np.abs(((h - hue_center + 180) % 360) - 180)
    mask = (h_diff <= hue_range) & (s >= sat_min) & (v > 0.1)

    # Маскированные пиксели = белые, остальные = чёрные
    result = np.zeros_like(arr)
    result[mask] = [255, 255, 255]

    return Image.fromarray(result)


def hsv_grayscale_enhance(img: Image.Image) -> Image.Image:
    """Преобразование в оттенки серого с усилением контраста через HSV Value channel."""
    arr = np.array(img)
    hsv = _rgb_to_hsv(arr)
    v = (hsv[:, :, 2] * 255).astype(np.uint8)
    return Image.fromarray(v, mode="L").convert("RGB")


# ═══════════════════════════════════════════════════════════════
# ADAPTIVE THRESHOLD
# ═══════════════════════════════════════════════════════════════

def adaptive_threshold(img: Image.Image, block_size: int = 15, c: int = 8) -> Image.Image:
    """Адаптивная бинаризация — выделяет текст на неоднородном фоне.
    
    block_size: размер окна (нечётное число)
    c: константа вычитания из среднего
    """
    gray = img.convert("L")
    arr = np.array(gray, dtype=np.float32)

    # Вычисляем среднее по окну через uniform filter
    from scipy.ndimage import uniform_filter
    local_mean = uniform_filter(arr, size=block_size)
    binary = np.where(arr > local_mean - c, 255, 0).astype(np.uint8)

    return Image.fromarray(binary, mode="L").convert("RGB")


# ═══════════════════════════════════════════════════════════════
# EDGE DETECTION
# ═══════════════════════════════════════════════════════════════

def edge_enhance(img: Image.Image, strength: float = 1.5) -> Image.Image:
    """Усиление границ текста через фильтр Собеля."""
    gray = img.convert("L")
    arr = np.array(gray, dtype=np.float32)

    # Собель по X и Y
    sobel_x = np.gradient(arr, axis=1)
    sobel_y = np.gradient(arr, axis=0)
    edges = np.sqrt(sobel_x ** 2 + sobel_y ** 2)
    edges = np.clip(edges * strength, 0, 255).astype(np.uint8)

    return Image.fromarray(edges, mode="L").convert("RGB")


# ═══════════════════════════════════════════════════════════════
# COMBINED PREPROCESSING PIPELINE
# ═══════════════════════════════════════════════════════════════

PRESET_NONE = "none"
PRESET_AUTO = "auto"
PRESET_DARK_TEXT = "dark_text"      # тёмный текст на светлом фоне
PRESET_LIGHT_TEXT = "light_text"    # светлый текст на тёмном фоне
PRESET_GREEN_BG = "green_bg"        # зелёный фон (игры)
PRESET_MANGA = "manga"              # манга/комиксы
PRESET_GAME_UI = "game_ui"          # интерфейс игры

PRESETS = {
    PRESET_NONE: "Без обработки",
    PRESET_AUTO: "Авто (определение фона)",
    PRESET_DARK_TEXT: "Тёмный текст на белом",
    PRESET_LIGHT_TEXT: "Светлый текст на тёмном",
    PRESET_GREEN_BG: "Зелёный фон (greenscreen)",
    PRESET_MANGA: "Манга/комиксы",
    PRESET_GAME_UI: "Интерфейс игры",
}


def preprocess_for_ocr(img: Image.Image, preset: str = PRESET_AUTO,
                        contrast: float = 1.5, brightness: float = 1.0) -> Image.Image:
    """Основная функция предобработки изображения перед OCR.
    
    Args:
        img: исходное изображение
        preset: пресет обработки
        contrast: множитель контраста (1.0 = без изменений)
        brightness: множитель яркости (1.0 = без изменений)
    
    Returns:
        обработанное изображение
    """
    if preset == PRESET_NONE:
        return img

    if img.mode != "RGB":
        img = img.convert("RGB")

    if preset == PRESET_AUTO:
        return _auto_preprocess(img, contrast, brightness)

    if preset == PRESET_DARK_TEXT:
        img = rgb_auto_contrast(img, strength=contrast)
        img = ImageEnhance.Contrast(img).enhance(contrast)
        img = ImageEnhance.Brightness(img).enhance(brightness)
        return img

    if preset == PRESET_LIGHT_TEXT:
        img = rgb_invert(img)
        img = rgb_auto_contrast(img, strength=contrast)
        img = ImageEnhance.Contrast(img).enhance(contrast)
        return img

    if preset == PRESET_GREEN_BG:
        # Выделяем текст: всё кроме зелёного → чёрное, зелёное → белое
        arr = np.array(img)
        g = arr[:, :, 1].astype(float)
        r = arr[:, :, 0].astype(float)
        b = arr[:, :, 2].astype(float)
        is_green = (g > 80) & (g > r * 1.3) & (g > b * 1.3)
        result = np.zeros_like(arr)
        result[is_green] = [255, 255, 255]
        result[~is_green] = [0, 0, 0]
        img = Image.fromarray(result)
        img = ImageEnhance.Contrast(img).enhance(2.0)
        return img

    if preset == PRESET_MANGA:
        # Манга: чёрный текст на белом, инвертируем если нужно
        img = hsv_grayscale_enhance(img)
        arr = np.array(img)
        mean_val = arr.mean()
        if mean_val < 128:
            img = rgb_invert(img)
        img = rgb_auto_contrast(img, strength=2.0)
        img = ImageEnhance.Contrast(img).enhance(2.5)
        return img

    if preset == PRESET_GAME_UI:
        # Игровой UI: авто + усиление границ
        img = _auto_preprocess(img, contrast, brightness)
        img = edge_enhance(img, strength=1.2)
        img = ImageEnhance.Contrast(img).enhance(1.5)
        return img

    # Fallback
    return _auto_preprocess(img, contrast, brightness)


def _auto_preprocess(img: Image.Image, contrast: float, brightness: float) -> Image.Image:
    """Авто-определение типа изображения и оптимальная обработка.
    
    Стратегия:
    - Скриншоты (высокий контраст, хорошая яркость) → пропускаем
    - Фото/сканы (низкий контраст, тёмное/светлое) → обрабатываем
    """
    arr = np.array(img, dtype=np.float32)
    h, w = arr.shape[:2]

    gray = np.mean(arr, axis=2)
    contrast_level = np.std(gray)
    mean_brightness = np.mean(gray)

    # Определяем цвет фона по краям
    edge_samples = np.concatenate([
        arr[0, :], arr[h - 1, :], arr[:, 0], arr[:, w - 1]
    ], axis=0)
    r_avg = edge_samples[:, 0].mean()
    g_avg = edge_samples[:, 1].mean()
    b_avg = edge_samples[:, 2].mean()
    is_green = g_avg > 100 and g_avg > r_avg * 1.2 and g_avg > b_avg * 1.2

    # Скриншот: контраст > 40, яркость 40-220, не зелёный
    # Обычно скриншоты IDE/браузера/игр имеют хороший контраст
    is_clean = (
        contrast_level > 40 and
        40 < mean_brightness < 220 and
        not is_green
    )

    if is_clean:
        # Чёткое изображение — пропускаем обработку
        return img

    # Нужна обработка
    if is_green:
        return preprocess_for_ocr(img, PRESET_GREEN_BG)
    elif mean_brightness >= 220:
        # Светлое (белый фон) — усиливаем контраст
        img = rgb_auto_contrast(img, strength=contrast)
        img = ImageEnhance.Contrast(img).enhance(contrast * 1.5)
        img = ImageEnhance.Brightness(img).enhance(0.8)
        return img
    elif mean_brightness <= 40:
        # Тёмное — осветляем + контраст
        img = rgb_auto_contrast(img, strength=contrast)
        img = ImageEnhance.Contrast(img).enhance(contrast)
        img = ImageEnhance.Brightness(img).enhance(brightness * 1.2)
        return img
    else:
        # Слабый контраст — усиливаем
        img = rgb_auto_contrast(img, strength=contrast)
        img = ImageEnhance.Contrast(img).enhance(contrast * 1.2)
        img = ImageEnhance.Brightness(img).enhance(brightness * 1.1)
        return img
