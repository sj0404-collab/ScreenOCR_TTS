# -*- coding: utf-8 -*-
"""
KOMA ONNX OCR Engine — дословная реплика KomaOnnxOcrEngine.kt из yomikai
(в свою очередь — data/OcrEngine.kt из читалки KOMA).

Пайплайн (ровно как в KOMA):
  1. Плитки: страница выше 2800 px режется на плитки 1800 px с перекрытием
     160 px, дубли на стыке отбрасываются по тексту и близости верха (<0.015).
  2. Детектор PP-OCRv4 (detection.onnx): масштаб ≤960 и кратен 32,
     нормализация ImageNet (mean .485/.456/.406, std .229/.224/.225),
     каналы BGR (как в PaddleOCR: pixels shr c*8 = B,G,R).
  3. Компоненты связности: BFS по порогу 0.3 (4-связность), отбраковка:
     <6 пикселей, средний балл <0.5, ширина/высота <2 px. Бокс
     расширяется на max(2, w*h*1.5/(2*(w+h))).
  4. Кроп и поворот: если кроп выше, чем в 1.8 раза шире — поворот 90° CW.
  5. Распознаватель PP-OCRv5 East Slavic (cyrillic.onnx): вход 3x48xW,
     W = clamp(48*w/h, 8..960), добит нулями до max(320, W);
     нормализация (x/255−0.5)/0.5, каналы BGR.
  6. CTC-жадный декодер: словарь [""] + строки cyrillic.txt + [" "]
     (0 — blank, последний — пробел). Средний сырой логит; строки
     со средним ≤0.25 отбрасываются.
  7. Порядок: сортировка по top.

Честные ограничения KOMA (сохранены намеренно):
  - пробелы между словами не восстанавливаются — их «склеивает» CTC;
  - порядок блоков — эвристика (top), не анализ панелей;
  - модели только кириллица (+ латиница в словаре PP-OCRv5).
"""
import logging
import os
import time
from collections import deque
from typing import List, Optional, Tuple

import numpy as np
import onnxruntime as ort
from PIL import Image

logger = logging.getLogger(__name__)

# Пороги — дословно из KomaOnnxOcrEngine.kt
DET_THRESHOLD = 0.3
MIN_COMPONENT_PIXELS = 6
MIN_COMPONENT_SCORE = 0.5
MIN_BOX_DIM = 2
MAX_BOXES = 400
MIN_REC_SCORE = 0.25
VERTICAL_ROTATE_RATIO = 1.8
TILE_HEIGHT = 1800
TILE_THRESHOLD = 2800
TILE_OVERLAP = 160
DEDUP_TOP_EPS = 0.015
DET_MAX_SIDE = 960
DET_MULTIPLE_OF_32 = 32
REC_HEIGHT = 48
REC_MIN_WIDTH = 320
REC_MAX_WIDTH = 960
REC_MIN_WIDTH_CLAMP = 8

# ImageNet нормализация детектора (BGR-порядок, как в PaddleOCR)
DET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
DET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


class KomaRegion:
    """Строка, найденная детектором: текст и рамка в долях картинки."""

    __slots__ = ("text", "left", "top", "right", "bottom", "confidence")

    def __init__(self, text: str, left: float, top: float,
                 right: float, bottom: float, confidence: float):
        self.text = text
        self.left = left
        self.top = top
        self.right = right
        self.bottom = bottom
        self.confidence = confidence

    def __repr__(self):
        return (f"KomaRegion({self.text!r}, L={self.left:.3f} T={self.top:.3f} "
                f"R={self.right:.3f} B={self.bottom:.3f}, conf={self.confidence:.2f})")


# ── имя говорящего (эвристика по позиции) ─────────────────────
# Символы, которых не бывает в имени, но бывают в коде/UI-подписях.
_SPEAKER_CODE_CHARS = set("=()[]{}<>\\/|_#&@%^$`~\"'0123456789-")
_SPEAKER_END_PUNCT = set(".!?…:;,")

# CTC часто отдаёт имя смесью алфавитов («Heфep» вместо «Нефер»).
# Для имён — только визуальные двойники-латиница → кириллица.
_SPEAKER_LAT2CYR = str.maketrans({
    "A": "А", "B": "В", "C": "С", "E": "Е", "H": "Н", "K": "К",
    "M": "М", "O": "О", "P": "Р", "T": "Т", "X": "Х", "Y": "У",
    "a": "а", "b": "в", "c": "с", "e": "е", "h": "н", "k": "к",
    "m": "м", "o": "о", "p": "р", "t": "т", "x": "х", "y": "у",
})


def _normalize_speaker(name: str) -> str:
    """Кириллизация визуальных латинских двойников в имени: Heфep → Нефер."""
    return name.translate(_SPEAKER_LAT2CYR)


def strip_speaker_line(regions: List[KomaRegion],
                       speaker: Optional[str]) -> List[KomaRegion]:
    """Убирает блок-имя из списка строк, чтобы имя не дублировалось.

    Имя входит в общий text (показ/TTS/перевод) первой строкой — без
    вырезания получалось «Нефер: Нефер Большая часть…» и TTS читал
    имя, хотя по заданию имя только ПОКАЗЫВАЕТСЯ, не озвучивается.
    Блок ищется как ПЕРВЫЙ (сверху) с равным нормализованным текстом.
    """
    if not speaker or not regions:
        return regions
    for i, r in enumerate(regions):
        if _normalize_speaker(r.text.strip()) == speaker:
            return regions[:i] + regions[i + 1:]
    return regions


def detect_speaker(regions: List[KomaRegion]) -> Optional[str]:
    """Имя говорящего из блоков кадра («Демьян», «Паймон»…).

    Эвристика по позиции (как в живых диалогах Genshin): имя —
    отдельная короткая строка НАД длинной репликой. Кандидат:
      - 1-3 слова, ≤20 символов, каждое слово с заглавной буквы;
      - без цифр и символов кода, не оканчивается на знак препинания.
    Ниже по кадру — ближайший блок-реплика: не менее 3 слов и 12
    символов, длиннее кандидата, вертикальный зазор небольшой
    (не более ~2 высот строки имени). Из нескольких коротких строк
    подряд берётся та, что стоит непосредственно над репликой.
    Результат нормализуется (латиница→кириллица): «Heфep» → «Нефер».
    """
    if not regions:
        return None
    regs = sorted(regions, key=lambda r: (r.top, r.left))
    for i, r in enumerate(regs):
        t = r.text.strip()
        words = t.split()
        if not (1 <= len(words) <= 3) or len(t) > 20:
            continue
        if any(not w[0].isupper() for w in words):
            continue
        if any(c in _SPEAKER_CODE_CHARS for c in t):
            continue
        if t[-1] in _SPEAKER_END_PUNCT:
            continue
        # Ближайший блок строго ниже кандидата — это «реплика».
        nxt = None
        for b in regs[i + 1:]:
            if b.top >= r.bottom - 0.01:
                nxt = b
                break
        if nxt is None:
            continue
        nt = nxt.text.strip()
        # Реплика: содержательнее имени — не менее 3 слов И 12 символов
        # («Что это значит» — минимальная реальная реплика Genshin).
        if (len(nt.split()) < 3 or len(nt) < 12) or len(nt) <= len(t):
            continue
        h = r.bottom - r.top
        gap = nxt.top - r.bottom
        if not (-0.01 <= gap <= max(0.04, h * 2.0)):
            continue
        return _normalize_speaker(t)
    return None


class KomaOnnxOCR:
    """ONNX OCR engine: PP-OCRv4 detector + CTC PP-OCRv5 East Slavic."""

    def __init__(self, models_dir: str = None):
        if models_dir is None:
            models_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                      "models", "koma_ocr")
        self._models_dir = models_dir
        self._det: "ort.InferenceSession | None" = None
        self._rec: "ort.InferenceSession | None" = None
        self._dictionary: List[str] = []
        self._initialized = False
        # Рамки последнего распознавания (KomaRegion, доли кадра) —
        # их же рисует GUI-оверлей как в читалке KOMA/yomikai.
        self.last_regions: List[KomaRegion] = []
        # Имя говорящего последнего кадра (detect_speaker) или None —
        # GUI добавляет его префиксом к ПОКАЗУ (не к TTS).
        self.last_speaker: Optional[str] = None

    # ── инициализация ──────────────────────────────────────────

    def initialize(self) -> bool:
        if self._initialized:
            return True
        det_path = os.path.join(self._models_dir, "detection.onnx")
        rec_path = os.path.join(self._models_dir, "cyrillic.onnx")
        dict_path = os.path.join(self._models_dir, "cyrillic.txt")
        for p in (det_path, rec_path, dict_path):
            if not os.path.exists(p):
                logger.error(f"[KomaOCR] model file missing: {p}")
                return False
        try:
            opts = ort.SessionOptions()
            # 2 потока — эмпирический оптимум для этого CPU. A/B на
            # Ryzen 3 3200G (4 физ. ядра, без SMT, игра на том же CPU):
            #   intra_op=4 → 2.34-4.58s/кадр
            #   intra_op=2 → 0.97-1.98s/кадр  (в ~2 раза быстрее)
            # 4 потока перегружают память/конкурируют с игрой — не читерим.
            opts.intra_op_num_threads = 2
            self._det = ort.InferenceSession(det_path, opts,
                                             providers=["CPUExecutionProvider"])
            self._rec = ort.InferenceSession(rec_path, opts,
                                             providers=["CPUExecutionProvider"])
            # Словарь: [""] + строки файла + [" "] — индекс 0 blank,
            # последний пробел. BOM от GitHub raw срезаем.
            raw = open(dict_path, "rb").read()
            if raw.startswith(b"\xef\xbb\xbf"):
                raw = raw[3:]
            lines = raw.decode("utf-8").splitlines()
            while lines and not lines[-1]:
                lines.pop()
            self._dictionary = [""] + lines + [" "]
            self._initialized = True
            logger.info(f"[KomaOCR] initialized: {len(self._dictionary)} classes, "
                        f"det={self._det.get_inputs()[0].shape}, "
                        f"rec={self._rec.get_inputs()[0].shape}")
            return True
        except Exception as e:
            logger.error(f"[KomaOCR] init failed: {e}")
            return False

    # ── публичный API (как у tflite_ocr) ──────────────────────

    def recognize(self, img: Image.Image) -> Tuple[str, float]:
        if not self._initialized:
            if not self.initialize():
                return "", 0.0
        start = time.time()
        try:
            self.last_speaker = None
            regions = self._page(img)
            self.last_regions = regions
            self.last_speaker = detect_speaker(regions)
            # Блок-имя убираем из ТЕКСТА: имя показывается GUI префиксом,
            # а в тексте оно не должно дублироваться («Нефер: Нефер…»)
            # и не должно попадать в TTS/перевод.
            text_regions = strip_speaker_line(regions, self.last_speaker)
            # Строгий порядок чтения сверху вниз: если детектор вернул
            # строки не по порядку — live читает «прыжками» между
            # абзацами (читал одно, потом резко другое). Сортируем по
            # (top, left); top округляем — плавающие доли пикселей
            # между кадрами не должны менять порядок.
            text_regions = sorted(text_regions,
                                  key=lambda r: (round(r.top, 3), r.left))
            text = "\n".join(r.text for r in text_regions)
            elapsed = time.time() - start
            if regions:
                avg = sum(r.confidence for r in regions) / len(regions)
                logger.info(f"[KomaOCR] {len(text)} chars, {len(regions)} lines "
                            f"in {elapsed:.2f}s (avg score {avg:.2f})")
                return text, max(50.0, min(99.0, avg * 100.0))
            logger.info(f"[KomaOCR] no text in {elapsed:.2f}s")
            return "", 0.0
        except Exception as e:
            logger.error(f"[KomaOCR] error: {e}")
            import traceback
            traceback.print_exc()
            return "", 0.0

    def recognize_regions(self, img: Image.Image) -> List[KomaRegion]:
        """Строки страницы с рамками в долях картинки."""
        if not self._initialized:
            if not self.initialize():
                return []
        return self._page(img)

    # ── плитки страницы ────────────────────────────────────────

    def _page(self, image: Image.Image) -> List[KomaRegion]:
        regions: List[KomaRegion] = []
        tile_h = TILE_HEIGHT if image.height > TILE_THRESHOLD else image.height
        y = 0
        while True:
            h = min(tile_h, image.height - y)
            tile = image.crop((0, y, image.width, y + h))
            for r in self._paddle(tile):
                mapped = KomaRegion(
                    r.text, r.left,
                    (y + r.top * h) / image.height,
                    r.right,
                    (y + r.bottom * h) / image.height,
                    r.confidence,
                )
                if not any(prev.text == mapped.text
                           and abs(prev.top - mapped.top) < DEDUP_TOP_EPS
                           for prev in regions):
                    regions.append(mapped)
            if y + h >= image.height:
                break
            y += tile_h - TILE_OVERLAP
        return regions

    # ── детектор + распознавание кропов ────────────────────────

    def _paddle(self, image: Image.Image) -> List[KomaRegion]:
        boxes = self._detect_boxes(image)
        out: List[KomaRegion] = []
        for (x1, y1, x2, y2) in boxes[:MAX_BOXES]:
            crop = image.crop((x1, y1, x2, y2))
            # Вертикальное облачко: поворот 90° по часовой (postRotate(90f))
            if crop.height > crop.width * VERTICAL_ROTATE_RATIO:
                crop = crop.transpose(Image.ROTATE_270)
            text, score = self._decode(crop)
            if text.strip() and score > MIN_REC_SCORE:
                out.append(KomaRegion(
                    text,
                    x1 / image.width, y1 / image.height,
                    x2 / image.width, y2 / image.height,
                    score,
                ))
        return out

    def _detect_boxes(self, image: Image.Image) -> List[Tuple[int, int, int, int]]:
        """Детектор PP-OCRv4: картинка → карта вероятностей → компоненты."""
        src_w, src_h = image.size
        ratio = min(1.0, DET_MAX_SIDE / max(src_w, src_h))
        w = max(DET_MULTIPLE_OF_32, int(round(src_w * ratio / DET_MULTIPLE_OF_32))
                * DET_MULTIPLE_OF_32)
        h = max(DET_MULTIPLE_OF_32, int(round(src_h * ratio / DET_MULTIPLE_OF_32))
                * DET_MULTIPLE_OF_32)
        scaled = image.resize((w, h), Image.BILINEAR).convert("RGB")
        rgb = np.asarray(scaled, dtype=np.float32)          # (h, w, 3) RGB
        bgr = rgb[:, :, ::-1]                                # PaddleOCR = BGR
        data = (bgr / 255.0 - DET_MEAN) / DET_STD
        tensor = data.transpose(2, 0, 1)[np.newaxis]         # (1, 3, h, w)

        input_name = self._det.get_inputs()[0].name
        result = self._det.run(None, {input_name: tensor})
        prob = result[0][0, 0]                               # (h, w)

        boxes = self._components(prob)
        # Масштаб боксов из координат scaled обратно к оригиналу
        out = []
        for (min_x, min_y, max_x, max_y) in boxes:
            x1 = int(min_x / w * src_w)
            x2 = int(max_x / w * src_w)
            y1 = int(min_y / h * src_h)
            y2 = int(max_y / h * src_h)
            x1 = max(0, min(x1, src_w - 1))
            y1 = max(0, min(y1, src_h - 1))
            x2 = max(x1 + 1, min(x2, src_w))
            y2 = max(y1 + 1, min(y2, src_h))
            out.append((x1, y1, x2, y2))
        return out

    @staticmethod
    def _components(prob: np.ndarray) -> List[Tuple[int, int, int, int]]:
        """Заливка по порогу 0.3: BFS (4-связность) + отбраковка мелочи.

        Дословно из KomaOnnxOcrEngine.components().
        """
        h, w = prob.shape
        visited = np.zeros((h, w), dtype=bool)
        boxes: List[Tuple[int, int, int, int]] = []
        above = prob >= DET_THRESHOLD
        ys, xs = np.nonzero(above)
        for y0, x0 in zip(ys, xs):
            if visited[y0, x0]:
                continue
            # BFS
            q = deque()
            q.append((y0, x0))
            visited[y0, x0] = True
            min_x = max_x = x0
            min_y = max_y = y0
            score = 0.0
            count = 0
            while q:
                yy, xx = q.popleft()
                score += float(prob[yy, xx])
                count += 1
                if xx < min_x:
                    min_x = xx
                elif xx > max_x:
                    max_x = xx
                if yy < min_y:
                    min_y = yy
                elif yy > max_y:
                    max_y = yy
                for ny, nx in ((yy - 1, xx), (yy + 1, xx), (yy, xx - 1), (yy, xx + 1)):
                    if 0 <= ny < h and 0 <= nx < w and not visited[ny, nx] \
                            and prob[ny, nx] > DET_THRESHOLD:
                        visited[ny, nx] = True
                        q.append((ny, nx))
            if count < MIN_COMPONENT_PIXELS:
                continue
            if score / count < MIN_COMPONENT_SCORE:
                continue
            if max_x - min_x < MIN_BOX_DIM or max_y - min_y < MIN_BOX_DIM:
                continue
            bw = max_x - min_x
            bh = max_y - min_y
            expand = max(2, int(round(bw * bh * 1.5 / (2 * (bw + bh)))))
            boxes.append((
                max(0, min_x - expand), max(0, min_y - expand),
                min(w, max_x + expand + 1), min(h, max_y + expand + 1),
            ))
        boxes.sort(key=lambda b: b[1])
        return boxes

    # ── CTC-распознаватель ─────────────────────────────────────

    def _decode(self, image: Image.Image) -> Tuple[str, float]:
        """CTC-жадный декодер: вход 3x48xW, нормализация (x/255−0.5)/0.5."""
        src_w, src_h = image.size
        if src_w < 1 or src_h < 1:
            return "", 0.0
        width = int(round(REC_HEIGHT * src_w / src_h))
        width = max(REC_MIN_WIDTH_CLAMP, min(REC_MAX_WIDTH, width))
        padded = max(REC_MIN_WIDTH, width)
        scaled = image.resize((width, REC_HEIGHT), Image.BILINEAR).convert("RGB")
        rgb = np.asarray(scaled, dtype=np.float32)
        bgr = rgb[:, :, ::-1]
        norm = (bgr / 255.0 - 0.5) / 0.5                   # (48, width, 3)
        data = np.zeros((1, 3, REC_HEIGHT, padded), dtype=np.float32)
        data[0, :, :, :width] = norm.transpose(2, 0, 1)

        input_name = self._rec.get_inputs()[0].name
        result = self._rec.run(None, {input_name: data})
        values = result[0][0]                               # (steps, classes)

        text_parts: List[str] = []
        prev = 0
        score = 0.0
        count = 0
        dic = self._dictionary
        n_classes = len(dic)
        for logits in values:
            best = int(np.argmax(logits))
            if best != 0 and best != prev:
                text_parts.append(dic[best] if best < n_classes else "")
                score += float(logits[best])
                count += 1
            prev = best
        text = "".join(text_parts)
        return text, (score / count if count else 0.0)


# ── convenience ────────────────────────────────────────────────

_default_engine = None


def get_koma_engine() -> KomaOnnxOCR:
    """Лениво созданный общий экземпляр (сессии ONNX дорогие)."""
    global _default_engine
    if _default_engine is None:
        _default_engine = KomaOnnxOCR()
    return _default_engine


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    eng = KomaOnnxOCR()
    if not eng.initialize():
        sys.exit(1)
    path = sys.argv[1] if len(sys.argv) > 1 else None
    if path:
        img = Image.open(path)
        t0 = time.time()
        regions = eng.recognize_regions(img)
        print(f"\n=== {path} ({img.width}x{img.height}) — "
              f"{len(regions)} строк за {time.time()-t0:.2f}s ===")
        for r in regions:
            print(f"  [{r.confidence:.2f}] {r.text}")
