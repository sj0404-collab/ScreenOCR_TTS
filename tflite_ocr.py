# -*- coding: utf-8 -*-
"""
Cyrillic TFLite OCR Engine — точная реплика CyrillicOcrEngine.kt из yomihon-custom.

Пайплайн:
  1. PP-OCRv4 детектор: 736x736, BGR ImageNet norm, probability map
  2. Connected-component BFS → text boxes
  3. Word splitting: vertical ink projection + Otsu threshold
  4. Двойной распознаватель: PP-OCRv3 (primary) + PP-OCRv5 (verifier)
  5. CTC greedy decode + softmax
  6. High-contrast retry при low confidence
  7. Candidate quality ranking (cyrillic fitness + coverage penalty)
  8. Reading order assembly
"""
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import List, Tuple, Optional

import numpy as np
from PIL import Image, ImageEnhance

logger = logging.getLogger(__name__)

# Constants from CyrillicOcrEngine.kt
DETECTOR_SIZE = 736
RECOGNIZER_WIDTH = 320
RECOGNIZER_HEIGHT = 48
RECOGNIZER_STEPS = 40

# Thresholds from OcrTuning.kt (BALANCED preset, yomikai)
DETECTOR_THRESHOLD = 0.20
MIN_COMPONENT_AREA = 16
MIN_ACCEPT_CONFIDENCE = 0.25
SHORT_TEXT_MIN_CONFIDENCE = 0.12
CONTRAST_RETRY_CONFIDENCE = 0.90
MIN_COVERAGE = 0.12
VERIFIER_CYRILLIC_BONUS = 0.20
WHOLE_LINE_BOUNDARY_BONUS = 0.08
MAX_TEXT_BOXES = 128
RESCUE_MAX_LINES = 6
SPLIT_MIN_WIDTH_PX = 32
MIN_WORD_GAP_PX = 5
WORD_GAP_FACTOR = 1.7
MERGE_OVERLAP_Y_FACTOR = 0.55
MERGE_GAP_X_FACTOR = 0.55
LINE_MERGE_OVERLAP_FACTOR = 0.30
LINE_MERGE_GAP_X_FACTOR = 0.15

CONTRAST_MATRIX = [1.5, 0, 0, 0, -45,
                    0, 1.5, 0, 0, -45,
                    0, 0, 1.5, 0, -45,
                    0, 0, 0, 1, 0]

ALLOWED_PUNCTUATION = set(" .,!?;:-()[]{}\"'«»„""%№+/=…—–")


@dataclass
class TextBox:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def center_y(self) -> float:
        return (self.top + self.bottom) / 2.0

    @property
    def height(self) -> int:
        return self.bottom - self.top


@dataclass
class Recognition:
    text: str
    confidence: float
    model: str = "v3"
    coverage: float = 0.0
    # Script evidence from the v3 pass (steps where Latin/Cyrillic classes
    # clearly win). Used for EN/RU routing. Padding/uniform steps excluded.
    n_lat: int = 0
    n_cyr: int = 0


class CyrillicTfliteOCR:
    """TFLite OCR engine for Cyrillic using PaddleOCR PP-OCRv3 + PP-OCRv5."""

    def __init__(self, models_dir: str = None):
        if models_dir is None:
            models_dir = os.path.join(os.path.dirname(__file__), "models", "cyrillic_ocr")
        self._models_dir = models_dir
        self._detector = None
        self._primary = None
        self._verifier = None
        self._primary_chars: List[str] = []
        self._verifier_chars: List[str] = []
        self._initialized = False
        self._interpreter_cls = None

        self._visited = np.zeros(DETECTOR_SIZE * DETECTOR_SIZE, dtype=bool)
        self._component_queue = np.zeros(DETECTOR_SIZE * DETECTOR_SIZE, dtype=np.int32)
        self._rapid = None  # lazy RapidOCR (English route)

    def initialize(self) -> bool:
        if self._initialized:
            return True

        try:
            import tensorflow as tf
            self._interpreter_cls = tf.lite.Interpreter
        except ImportError:
            try:
                import tflite_runtime.interpreter as interp
                self._interpreter_cls = interp.Interpreter
            except ImportError:
                logger.error("[TfliteOCR] No TFLite runtime (install tensorflow or tflite-runtime)")
                return False

        detector_path = os.path.join(self._models_dir, "cyrillic_detector.tflite")
        primary_path = os.path.join(self._models_dir, "cyrillic_recognizer_v3.tflite")
        verifier_path = os.path.join(self._models_dir, "cyrillic_recognizer_v5.tflite")
        primary_dict = os.path.join(self._models_dir, "cyrillic_dict_v3.txt")
        verifier_dict = os.path.join(self._models_dir, "cyrillic_dict_v5.txt")

        for p in [detector_path, primary_path, primary_dict]:
            if not os.path.exists(p):
                logger.error(f"[TfliteOCR] Missing: {p}")
                return False

        def _new_interpreter(path: str):
            # Single thread: XNNPACK parallel reductions flip argmax between
            # runs when logit margins are tiny (flat-softmax regime).
            try:
                return self._interpreter_cls(model_path=path, num_threads=1)
            except TypeError:
                return self._interpreter_cls(model_path=path)

        try:
            self._detector = _new_interpreter(detector_path)
            self._detector.allocate_tensors()
            self._primary = _new_interpreter(primary_path)
            self._primary.allocate_tensors()
            if os.path.exists(verifier_path) and os.path.exists(verifier_dict):
                self._verifier = _new_interpreter(verifier_path)
                self._verifier.allocate_tensors()
                self._verifier_chars = self._read_dictionary(verifier_dict)
            self._primary_chars = self._read_dictionary(primary_dict)
            self._initialized = True
            logger.info(f"[TfliteOCR] Initialized: v3 dict={len(self._primary_chars)}, "
                        f"v5={'yes' if self._verifier else 'no'}")
            return True
        except Exception as e:
            logger.error(f"[TfliteOCR] Init failed: {e}")
            return False

    def _read_dictionary(self, path: str) -> List[str]:
        # Match Kotlin: strip UTF-8 BOM, strict UTF-8, drop trailing empties only
        # (leading ' ' entry must be preserved — do NOT filter empty lines).
        raw = open(path, 'rb').read()
        if raw[:3] == b'\xef\xbb\xbf':
            raw = raw[3:]
        text = raw.decode('utf-8')  # strict: raises on malformed, like REPORT in Kotlin
        lines = text.splitlines()
        while lines and lines[-1] == '':
            lines.pop()
        return lines

    def recognize(self, img: Image.Image) -> Tuple[str, float]:
        # Match Kotlin recognizeText: recognize EACH padded box individually,
        # then assemble rows. Merging boxes before recognition destroys text
        # (whole desktop strip scaled to 320x48 becomes unreadable).
        if not self._initialized:
            if not self.initialize():
                return "", 0.0

        start = time.time()
        try:
            boxes = self._detect_text_boxes(img)
            if boxes:
                recognized: List[Tuple[TextBox, Recognition]] = []
                rejected: List[Tuple[TextBox, Recognition]] = []
                for box in boxes:
                    padded = self._pad(box, img.width, img.height)
                    if padded.right - padded.left < 4 or padded.bottom - padded.top < 4:
                        continue
                    crop = img.crop((padded.left, padded.top, padded.right, padded.bottom))
                    result = self._recognize_line_bitmap(crop)
                    if not result.text.strip():
                        continue
                    # Gate on text quality, not absolute softmax confidence:
                    # these distilled models cap peak prob at ~0.016 even on
                    # perfect inputs, so 0.25/0.12 thresholds reject everything.
                    # _clean_recognition (ramp + Cyrillic accept + garbage
                    # salvage) is the real filter.
                    cleaned = self._clean_recognition(result.text)
                    if cleaned:
                        recognized.append((
                            box,
                            Recognition(
                                text=cleaned,
                                confidence=result.confidence,
                                model=result.model,
                                coverage=result.coverage,
                            ),
                        ))
                    else:
                        rejected.append((box, result))

                if recognized:
                    text = self._assemble_reading_order(
                        [(b, r.text) for b, r in recognized]
                    )
                    text = self._clean_recognition(text)
                    elapsed = time.time() - start
                    logger.info(f"[TfliteOCR] {len(text)} chars, {len(recognized)} boxes in {elapsed:.2f}s")
                    return text, 85.0 if text else 0.0

                if rejected:
                    rescued = self._rescue_rejected_lines(rejected)
                    if rescued:
                        elapsed = time.time() - start
                        logger.info(f"[TfliteOCR] rescued {len(rescued)} chars in {elapsed:.2f}s")
                        return rescued, 50.0

            # Fallback: whole image via recognizer with vertical slicing
            text = self._fallback_sliced(img)
            elapsed = time.time() - start
            logger.info(f"[TfliteOCR] Sliced fallback: {len(text)} chars in {elapsed:.2f}s")
            return text, 85.0 if text else 0.0
        except Exception as e:
            logger.error(f"[TfliteOCR] Error: {e}")
            import traceback
            traceback.print_exc()
            return "", 0.0

    def _rescue_rejected_lines(
        self, rejected: List[Tuple[TextBox, Recognition]]
    ) -> str:
        # Second rescue echelon (Kotlin rescueRejectedLines): best rejected
        # crops by letter count, assembled in reading order.
        cands = [(b, r.text.strip()) for b, r in rejected if r.text.strip()]
        if not cands:
            return ""
        cands.sort(key=lambda x: sum(1 for c in x[1] if c.isalpha()), reverse=True)
        cands = cands[:RESCUE_MAX_LINES]
        text = self._assemble_reading_order(cands)
        return self._clean_recognition(text)

    def _merge_into_lines(self, boxes: List[TextBox], img_w: int, img_h: int) -> List[TextBox]:
        """Merge nearby boxes into horizontal text lines."""
        if not boxes:
            return []
        # Sort by Y
        sorted_b = sorted(boxes, key=lambda b: b.top)
        lines = []
        current_line = [sorted_b[0]]
        for b in sorted_b[1:]:
            prev = current_line[-1]
            # Same line if Y overlap is significant
            overlap = min(prev.bottom, b.bottom) - max(prev.top, b.top)
            min_h = min(prev.height, b.height)
            gap_x = max(0, b.left - prev.right)
            same_line = (overlap >= min_h * LINE_MERGE_OVERLAP_FACTOR or gap_x < img_w * LINE_MERGE_GAP_X_FACTOR)
            if same_line and abs(b.center_y - np.mean([x.center_y for x in current_line])) < img_h * 0.08:
                current_line.append(b)
            else:
                lines.append(current_line)
                current_line = [b]
        lines.append(current_line)

        result = []
        for line in lines:
            left = min(b.left for b in line)
            top = min(b.top for b in line)
            right = max(b.right for b in line)
            bottom = max(b.bottom for b in line)
            result.append(TextBox(left, top, right, bottom))
        return result

    def _fallback_sliced(self, img: Image.Image) -> str:
        """Slice image into horizontal strips and recognize each."""
        w, h = img.size
        strip_height = RECOGNIZER_HEIGHT * 4  # ~192px per strip
        all_parts = []

        for y in range(0, h, strip_height):
            strip_h = min(strip_height, h - y)
            strip = img.crop((0, y, w, y + strip_h))
            result = self._recognize_line_bitmap(strip)
            if result.text.strip():
                all_parts.append(result.text.strip())

        text = " ".join(all_parts)
        return self._clean_recognition(text) if text else ""

    # ── DETECTION ──────────────────────────────────────────

    def _detect_text_boxes(self, img: Image.Image) -> List[TextBox]:
        boxes = self._run_detection(img, 0, 0)

        if max(img.width, img.height) > DETECTOR_SIZE:
            over_x = int(img.width * 0.12)
            over_y = int(img.height * 0.12)
            half_w = img.width // 2
            half_h = img.height // 2
            tiles = [
                (0, 0, half_w + over_x, half_h + over_y),
                (half_w - over_x, 0, img.width, half_h + over_y),
                (0, half_h - over_y, half_w + over_x, img.height),
                (half_w - over_x, half_h - over_y, img.width, img.height),
            ]
            for x1, y1, x2, y2 in tiles:
                x1, y1 = max(0, x1), max(0, y1)
                x2, y2 = min(img.width, x2), min(img.height, y2)
                if x2 - x1 < 96 or y2 - y1 < 96:
                    continue
                crop = img.crop((x1, y1, x2, y2))
                boxes.extend(self._run_detection(crop, x1, y1))

        merged = self._merge_boxes(boxes)
        if not merged:
            return self._projection_boxes(img)
        return merged[:MAX_TEXT_BOXES]

    def _run_detection(self, source: Image.Image, off_x: int, off_y: int) -> List[TextBox]:
        scale = min(DETECTOR_SIZE / source.width, DETECTOR_SIZE / source.height)
        scaled_w = max(1, int(source.width * scale))
        scaled_h = max(1, int(source.height * scale))
        offset_x = (DETECTOR_SIZE - scaled_w) // 2
        offset_y = (DETECTOR_SIZE - scaled_h) // 2

        det_img = Image.new("RGB", (DETECTOR_SIZE, DETECTOR_SIZE), (255, 255, 255))
        resized = source.resize((scaled_w, scaled_h), Image.Resampling.LANCZOS)
        det_img.paste(resized, (offset_x, offset_y))

        arr = np.array(det_img, dtype=np.float32)
        arr = arr[:, :, ::-1]  # RGB -> BGR
        mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
        std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
        arr = (arr / 255.0 - mean) / std
        arr = arr.reshape(1, DETECTOR_SIZE, DETECTOR_SIZE, 3)

        inp = self._detector.get_input_details()
        out = self._detector.get_output_details()
        self._detector.set_tensor(inp[0]['index'], arr)
        self._detector.invoke()
        probability = self._detector.get_tensor(out[0]['index']).reshape(DETECTOR_SIZE, DETECTOR_SIZE)

        # Connected-component BFS
        boxes = []
        self._visited[:] = False
        for start in range(DETECTOR_SIZE * DETECTOR_SIZE):
            if self._visited[start] or probability.flat[start] < DETECTOR_THRESHOLD:
                continue
            head = 0
            tail = 0
            self._component_queue[tail] = start
            tail += 1
            self._visited[start] = True
            min_x = DETECTOR_SIZE
            min_y = DETECTOR_SIZE
            max_x = 0
            max_y = 0
            area = 0
            while head < tail:
                idx = self._component_queue[head]
                head += 1
                x = idx % DETECTOR_SIZE
                y = idx // DETECTOR_SIZE
                min_x = min(min_x, x)
                min_y = min(min_y, y)
                max_x = max(max_x, x)
                max_y = max(max_y, y)
                area += 1
                for dy in range(-1, 2):
                    for dx in range(-1, 2):
                        if dx == 0 and dy == 0:
                            continue
                        nx, ny = x + dx, y + dy
                        if 0 <= nx < DETECTOR_SIZE and 0 <= ny < DETECTOR_SIZE:
                            next_idx = ny * DETECTOR_SIZE + nx
                            if not self._visited[next_idx] and probability.flat[next_idx] >= DETECTOR_THRESHOLD:
                                self._visited[next_idx] = True
                                self._component_queue[tail] = next_idx
                                tail += 1

            if area < MIN_COMPONENT_AREA or max_x - min_x < 3 or max_y - min_y < 3:
                continue

            expand_x = max(3, int((max_x - min_x) * 0.16))
            expand_y = max(2, int((max_y - min_y) * 0.20))
            left = off_x + max(0, int((min_x - expand_x - offset_x) / scale))
            top = off_y + max(0, int((min_y - expand_y - offset_y) / scale))
            right = off_x + min(source.width, int(np.ceil((max_x + expand_x - offset_x) / scale)))
            bottom = off_y + min(source.height, int(np.ceil((max_y + expand_y - offset_y) / scale)))
            right = max(right, left + 1)
            bottom = max(bottom, top + 1)
            if right - left >= 6 and bottom - top >= 6:
                boxes.append(TextBox(left, top, right, bottom))

        return boxes

    def _projection_boxes(self, img: Image.Image) -> List[TextBox]:
        gray = np.array(img.convert("L"), dtype=np.float32)
        binary = (gray < 180).astype(np.uint8)
        row_sums = binary.sum(axis=1)

        boxes = []
        in_region = False
        y_start = 0
        for y in range(len(row_sums)):
            if row_sums[y] > 5 and not in_region:
                in_region = True
                y_start = y
            elif row_sums[y] <= 5 and in_region:
                in_region = False
                col_sums = binary[y_start:y, :].sum(axis=0)
                xs = np.where(col_sums > 0)[0]
                if len(xs) > 0:
                    boxes.append(TextBox(int(xs[0]), y_start, int(xs[-1]) + 1, y))
        if in_region:
            col_sums = binary[y_start:, :].sum(axis=0)
            xs = np.where(col_sums > 0)[0]
            if len(xs) > 0:
                boxes.append(TextBox(int(xs[0]), y_start, int(xs[-1]) + 1, len(row_sums)))
        return boxes

    # ── RECOGNITION ────────────────────────────────────────

    def _recognize_line_bitmap(self, crop: Image.Image) -> Recognition:
        words = self._split_words(crop)
        whole_line = self._recognize_crop(crop)
        if len(words) == 1:
            rescued = self._rescue_narrow_gap(words[0], whole_line)
            return rescued if rescued is not None else whole_line
        # Whole-crop English win: RapidOCR reads the full line with native
        # word spacing and context; per-piece splitting only fragments it.
        if whole_line.model == "en" and whole_line.text.strip():
            return whole_line

        parts = []
        conf_sum = 0.0
        count = 0
        for word_img in words:
            r = self._recognize_crop(word_img)
            if r.text.strip():
                conf_sum += r.confidence
                count += 1
                parts.append(r.text)

        segmented_text = self._join_pieces(parts)
        segmented = Recognition(
            text=segmented_text,
            confidence=conf_sum / count if count > 0 else 0.0,
            model=whole_line.model,
            coverage=whole_line.coverage,
        )
        return self._select_line_recognition(whole_line, segmented)

    def _rescue_narrow_gap(self, piece: Image.Image,
                           whole: Recognition) -> Optional[Recognition]:
        # Splitter found no >=5px gaps, but small UI text can hide a real
        # word gap at 3-4px ('Алису AI' glued to 'АлисуАИ'). Only for short
        # crops (h<20: letter gaps are 1-2px there, so 3-4px is a word gap;
        # at manga sizes letter gaps reach 3-4px and must NOT split).
        # Accept only if both halves decode non-empty and differ.
        w, h = piece.size
        if h >= 20:
            return None
        # English route already spaces natively; nothing to rescue.
        if whole.model == "en":
            return None
        toks = whole.text.split()
        if len(toks) != 1:
            return None
        # Rescue only unknown words of real length: correct ones
        # ('Workspace', 'народ') must never split, whatever gaps look like.
        # Short tokens ('т', 'B', 'Cm') are icon fragments, not glue.
        if len(toks[0]) < 6:
            return None
        from ocr_text_cleaner import _ru_valid_words, _en_valid_words
        low = toks[0].lower()
        if low in _ru_valid_words() or low in _en_valid_words():
            return None
        gray = np.array(piece.convert("L"), dtype=np.int32)
        col = (gray < 128).sum(axis=0)
        gaps = []  # (gap_start_x, gap_width)
        x = 0
        in_ink = col[0] > 0 if len(col) else False
        while x < len(col):
            if col[x] > 0:
                x += 1
                continue
            x0 = x
            while x < len(col) and col[x] == 0:
                x += 1
            gw = x - x0
            if 3 <= gw <= 4 and x0 > w * 0.15 and x < w * 0.85:
                gaps.append((x0, gw))
        if not gaps:
            return None
        # Widest qualifying gap first
        gaps.sort(key=lambda g: -g[1])
        whole_alpha = sum(1 for c in whole.text if c.isalpha())
        for gx, _gw in gaps:
            left = piece.crop((0, 0, gx, h))
            right = piece.crop((gx, 0, w, h))
            rl = self._recognize_crop(left)
            rr = self._recognize_crop(right)
            if not rl.text.strip() or not rr.text.strip():
                continue
            joined = (rl.text.strip() + " " + rr.text.strip()).strip()
            if joined == whole.text:
                continue
            # No lost letters (cut glyphs vanish): tolerates one.
            if sum(1 for c in joined if c.isalpha()) < whole_alpha - 1:
                continue
            return Recognition(
                text=joined, confidence=(rl.confidence + rr.confidence) / 2,
                model=whole.model, coverage=max(rl.coverage, rr.coverage))
        return None

    def _join_pieces(self, parts: List[str]) -> str:
        # Pieces have generous margins that can include a neighbor's edge
        # glyph ('и' + 'иподелиться'). Strip leading chars of a spaceless
        # part that duplicate the previous tail in canonical homoglyph
        # form, but ONLY when the remainder is a known word — otherwise
        # legit repeats ('кот' + 'торт') would corrupt to 'кот орт'.
        from ocr_text_cleaner import _ru_valid_words
        valid = _ru_valid_words()
        out: List[str] = []
        for part in parts:
            p = part.strip()
            if not p:
                continue
            # Latin pieces (RapidOCR route) re-detect text internally, so
            # they carry no margin ghosts; dedupe Cyrillic pieces only.
            if out and " " not in p and re.match(r"^[^A-Za-z]*[а-яА-ЯёЁ]", p):
                tail = self._canon(out[-1].replace(" ", "")[-8:])
                head = self._canon(p[:8])
                for k in range(min(3, len(p)), 0, -1):
                    if not tail.endswith(head[:k]):
                        continue
                    rest = p[k:]
                    if not rest:
                        # Whole piece duplicates tail (single ghost glyph)
                        if len(p) == 1:
                            p = ""
                        break
                    core = re.sub(r"^[^а-яА-ЯёЁ]+|[^а-яА-ЯёЁ]+$", "", rest)
                    if core.lower() in valid:
                        p = rest
                    break
                if not p:
                    continue
            out.append(p)
        return " ".join(out).strip()

    def _select_line_recognition(self, whole: Recognition, segmented: Recognition) -> Recognition:
        if not segmented.text.strip():
            return whole
        if not whole.text.strip():
            return segmented
        from ocr_text_cleaner import normalize_local_cyrillic_caption
        restored = normalize_local_cyrillic_caption(whole.text)
        restores_boundaries = len(restored) > len(whole.text)
        whole_quality = self._candidate_quality(whole)
        if restores_boundaries:
            whole_quality += WHOLE_LINE_BOUNDARY_BONUS
        segmented_quality = self._candidate_quality(segmented)
        if restores_boundaries and whole_quality >= segmented_quality:
            return whole
        return segmented

    def _split_words(self, crop: Image.Image) -> List[Image.Image]:
        w, h = crop.size
        if w < SPLIT_MIN_WIDTH_PX:
            return [crop]

        gray = np.array(crop.convert("L"), dtype=np.int32)
        hist, _ = np.histogram(gray.flatten(), bins=256, range=(0, 256))

        total = w * h
        sum_all = np.sum(np.arange(256) * hist)
        sum_b = 0.0
        w_b = 0
        max_between = -1.0
        otsu = 127
        for v in range(256):
            w_b += hist[v]
            if w_b == 0:
                continue
            w_f = total - w_b
            if w_f == 0:
                break
            sum_b += v * hist[v]
            m_b = sum_b / w_b
            m_f = (sum_all - sum_b) / w_f
            between = w_b * w_f * (m_b - m_f) ** 2
            if between > max_between:
                max_between = between
                otsu = v

        ink = np.zeros(w, dtype=np.int32)
        for y in range(h):
            for x in range(w):
                if gray[y, x] < otsu:
                    ink[x] += 1

        runs = []
        start = -1
        for x in range(w):
            if ink[x] > 0:
                if start < 0:
                    start = x
            elif start >= 0:
                runs.append((start, x - 1))
                start = -1
        if start >= 0:
            runs.append((start, w - 1))

        if len(runs) < 2:
            return [crop]

        gaps = [runs[i + 1][0] - runs[i][1] - 1 for i in range(len(runs) - 1)]
        positive = sorted([g for g in gaps if g > 0])
        median = positive[len(positive) // 2] if positive else 1.0
        threshold = max(MIN_WORD_GAP_PX, round(median * WORD_GAP_FACTOR))

        groups = []
        group_start = runs[0][0]
        split_after = 0
        for i, gap in enumerate(gaps):
            if gap >= threshold:
                groups.append((group_start, runs[i][1]))
                group_start = runs[i + 1][0]
                split_after += 1
        if split_after == 0:
            return [crop]
        groups.append((group_start, runs[-1][1]))

        if len(groups) <= 1:
            return [crop]

        result = []
        for g_start, g_end in groups:
            # Generous margins: recognizer drops edge glyphs without left
            # context (title-bar П lost with -4px).
            left = max(0, g_start - 10)
            right = min(w, g_end + 8)
            result.append(crop.crop((left, 0, max(left + 1, right), h)))
        return result

    def _is_latin_crop(self, rec: Recognition) -> bool:
        # Script routing: clearly Latin evidence -> English engine.
        # Digits/punct-only crops (n_lat == n_cyr == 0) stay Cyrillic.
        return rec.n_lat >= 2 and rec.n_lat > rec.n_cyr * 1.25

    def _rapid_engine(self):
        if self._rapid is None:
            try:
                from rapid_ocr import RapidOCREngine
                eng = RapidOCREngine(lang="en")
                self._rapid = eng if eng.initialize() else False
            except Exception as e:
                logger.warning(f"[TfliteOCR] RapidOCR unavailable: {e}")
                self._rapid = False
        return self._rapid or None

    @staticmethod
    def _clean_en_text(text: str) -> str:
        # Keep Latin/digits/basic punct/spaces; drop CJK and other scripts
        # the EN model hallucinates (e.g. 山白 on icon glyphs).
        kept = []
        for ch in text:
            o = ord(ch)
            if ("a" <= ch <= "z" or "A" <= ch <= "Z" or ch.isdigit()
                    or ch in " .,!?;:'\"()[]{}-_/\\@#%+*=" or ch in "\n\t"):
                kept.append(ch)
            elif ch.isspace():
                kept.append(" ")
        return re.sub(r"[ \t]+", " ", "".join(kept)).strip(" \n")

    def _recognize_en(self, crop: Image.Image) -> Optional[Recognition]:
        """English route via RapidOCR (PP-OCR EN). Returns None when unusable
        so the caller falls back to the Cyrillic pipeline."""
        from ocr_text_cleaner import latin_fitness
        engine = self._rapid_engine()
        if engine is None:
            return None
        try:
            # RapidOCR's detector fails on tight word crops (reads CJK junk
            # like '二'): retry with a gray margin. Skip the retry when the
            # raw read is already confident.
            text, conf = engine.recognize(crop)
            if conf < 85.0:
                canvas = Image.new("RGB", (crop.width + 24, crop.height + 24),
                                   (128, 128, 128))
                canvas.paste(crop, (12, 12))
                try:
                    t2, c2 = engine.recognize(canvas)
                except Exception:
                    t2, c2 = "", -1.0
                if c2 > conf:
                    text, conf = t2, c2
        except Exception as e:
            logger.warning(f"[TfliteOCR] RapidOCR error: {e}")
            return None
        clean = self._clean_en_text(text or "")
        if not clean or latin_fitness(clean) < 0.5:
            return None
        return Recognition(text=clean, confidence=max(0.0, min(1.0, conf / 100.0)),
                           model="en", coverage=0.0)

    def _recognize_crop(self, crop: Image.Image) -> Recognition:
        r3 = self._run_recognizer(crop, self._primary, self._primary_chars, "v3")
        # English text -> dedicated Latin engine (native word spacing);
        # Russian text -> Cyrillic pipeline below.
        if self._is_latin_crop(r3):
            en = self._recognize_en(crop)
            if en is not None and en.text.strip():
                return en

        candidates = [r3]

        if candidates[-1].confidence < CONTRAST_RETRY_CONFIDENCE:
            contrast = self._create_high_contrast(crop)
            candidates.append(self._run_recognizer(contrast, self._primary, self._primary_chars, "v3"))

        if self._verifier is not None:
            v5_result = self._run_recognizer(crop, self._verifier, self._verifier_chars, "v5")
            candidates.append(v5_result)
            if v5_result.confidence < CONTRAST_RETRY_CONFIDENCE:
                contrast = self._create_high_contrast(crop)
                candidates.append(self._run_recognizer(contrast, self._verifier, self._verifier_chars, "v5"))

        return max(candidates, key=self._candidate_quality) if candidates else Recognition("", 0.0)

    def _run_recognizer(self, crop: Image.Image, model, chars: List[str], model_name: str) -> Recognition:
        # Long crops would squash horizontally into 320px (glyphs become
        # tall/narrow and decode with char errors). Instead cover them with
        # overlapping windows that each fit the 320 budget, then merge.
        scale = RECOGNIZER_HEIGHT / max(1, crop.height)
        scaled_w = crop.width * scale
        if scaled_w <= RECOGNIZER_WIDTH:
            windows = [crop]
        else:
            win_w = max(16, int(RECOGNIZER_WIDTH * crop.height / RECOGNIZER_HEIGHT))
            stride = max(8, win_w - max(16, win_w // 5))
            starts = list(range(0, max(1, crop.width - win_w + 1), stride))
            if not starts or starts[-1] + win_w < crop.width:
                starts.append(max(0, crop.width - win_w))
            windows = [crop.crop((s, 0, min(crop.width, s + win_w), crop.height))
                       for s in starts]

        texts: List[str] = []
        conf_sum, conf_count = 0.0, 0
        coverage = 0.0
        n_lat_total, n_cyr_total = 0, 0
        for win in windows:
            text, conf, _emitted, cov, n_lat, n_cyr = self._infer_window(
                win, model, chars)
            texts.append(text)
            conf_sum += conf
            conf_count += 1
            coverage = max(coverage, cov)
            n_lat_total += n_lat
            n_cyr_total += n_cyr
        merged = self._merge_window_texts(texts)
        conf = conf_sum / conf_count if conf_count else 0.0
        return Recognition(text=merged, confidence=conf, model=model_name,
                           coverage=coverage, n_lat=n_lat_total,
                           n_cyr=n_cyr_total)

    # Canonical homoglyph map for overlap comparison only: unifies
    # Cyrillic/Ukrainian lookalikes to one base so a 1-char script
    # confusion (і/и, о/o, е/e) between windows does not break merging.
    _MERGE_CANON = {
        'а': 'a', 'с': 'c', 'е': 'e', 'о': 'o', 'р': 'p', 'х': 'x', 'у': 'y',
        'А': 'a', 'В': 'b', 'С': 'c', 'Е': 'e', 'Н': 'h', 'К': 'k', 'М': 'm',
        'О': 'o', 'Р': 'p', 'Т': 't', 'Х': 'x', 'У': 'y',
        'і': 'i', 'І': 'i', 'ї': 'i', 'є': 'e', 'Є': 'e', 'ґ': 'g', 'Ґ': 'g',
        'ё': 'е', 'Ё': 'е',
    }

    @classmethod
    def _canon(cls, s: str) -> str:
        return "".join(cls._MERGE_CANON.get(c, c).lower() for c in s)

    @classmethod
    def _merge_window_texts(cls, texts: List[str]) -> str:
        # Overlapping windows share content: append only the non-overlapping
        # tail. Window cuts can drop edge glyphs, so the shared content is
        # found as a longest common SUBSTRING between the merged tail and
        # the next window head (not just affix), compared in canonical
        # homoglyph form; splicing keeps the original strings.
        merged = texts[0] if texts else ""
        for nxt in texts[1:]:
            if not nxt:
                continue
            if not merged:
                merged = nxt
                continue
            tail = cls._canon(merged[-48:])
            head = cls._canon(nxt[:48])
            best_k, best_L = 0, 0
            for k in range(0, min(24, len(nxt))):
                maxL = min(len(nxt) - k, len(tail))
                for L in range(maxL, 3, -1):
                    if L > best_L and head[k:k + L] in tail:
                        best_k, best_L = k, L
                        break
                if best_L >= 8:
                    break
            merged += nxt[best_k + best_L:]
        return merged

    def _infer_window(self, crop: Image.Image, model, chars: List[str]):
        rec_img = Image.new("RGB", (RECOGNIZER_WIDTH, RECOGNIZER_HEIGHT), (128, 128, 128))
        scale = RECOGNIZER_HEIGHT / max(1, crop.height)
        target_w = min(RECOGNIZER_WIDTH, max(1, int(crop.width * scale)))
        resized = crop.resize((target_w, RECOGNIZER_HEIGHT), Image.Resampling.LANCZOS)
        rec_img.paste(resized, (0, 0))

        arr = np.array(rec_img, dtype=np.float32)
        arr = arr[:, :, ::-1]  # RGB -> BGR
        arr = (arr / 255.0 - 0.5) / 0.5

        inp = model.get_input_details()
        out = model.get_output_details()
        model.set_tensor(inp[0]['index'], arr.reshape(1, RECOGNIZER_HEIGHT, RECOGNIZER_WIDTH, 3))
        model.invoke()
        values = model.get_tensor(out[0]['index'])

        # Output shape: (1, steps, num_classes) - raw logits (see CtcScoring.softmax).
        # Classes come from actual output size, not dict length (165 vs 163+1).
        if values.ndim == 3:
            seq = values[0]  # (steps, num_classes)
            _steps, num_classes = seq.shape
            return self._decode_ctc(seq.reshape(-1), chars, int(num_classes))
        return ("", 0.0, 0, 0.0, 0, 0)

    def _decode_ctc(self, values: np.ndarray, chars: List[str], num_classes: int):
        if num_classes <= 1 or len(values) < num_classes:
            return ("", 0.0, 0, 0.0, 0, 0)

        # Per-class script flags for EN/RU routing (digits/punct neutral).
        is_lat = [False] * num_classes
        is_cyr = [False] * num_classes
        for idx in range(1, num_classes):
            if idx - 1 >= len(chars):
                continue
            ch = chars[idx - 1]
            if len(ch) == 1:
                if "a" <= ch <= "z" or "A" <= ch <= "Z":
                    is_lat[idx] = True
                elif 0x0400 <= ord(ch) <= 0x052F or 0xA640 <= ord(ch) <= 0xA69F:
                    is_cyr[idx] = True

        steps = len(values) // num_classes
        text_parts = []
        prev = -1
        conf_sum = 0.0
        conf_count = 0
        blank_steps = 0
        counting_blanks = False
        n_lat = 0
        n_cyr = 0

        for step in range(steps):
            base = step * num_classes
            logits = values[base:base + num_classes]
            max_logit = np.max(logits)
            exp_logits = np.exp(logits - max_logit)
            probs = exp_logits / np.sum(exp_logits)

            best_idx = 0
            best_score = probs[0]
            best_lat = 0.0
            best_cyr = 0.0
            for idx in range(1, num_classes):
                # Match Kotlin: chars.getOrNull(idx-1).orEmpty() + allowed('')
                # Out-of-range classes (164/165, 851/852) are skipped.
                char = chars[idx - 1] if idx - 1 < len(chars) else ''
                if not self._allowed(char):
                    continue
                if probs[idx] > best_score:
                    best_score = probs[idx]
                    best_idx = idx
                if is_lat[idx] and probs[idx] > best_lat:
                    best_lat = probs[idx]
                elif is_cyr[idx] and probs[idx] > best_cyr:
                    best_cyr = probs[idx]

            # Script evidence: winning side needs a clear margin AND to beat
            # uniform noise, so padding steps (blank/uniform) don't vote.
            if best_lat > 0.008 and best_lat > best_cyr * 1.2:
                n_lat += 1
            elif best_cyr > 0.008 and best_cyr > best_lat * 1.2:
                n_cyr += 1

            if best_idx != 0 and best_idx != prev:
                if best_idx - 1 < len(chars):
                    text_parts.append(chars[best_idx - 1])
                conf_sum += best_score
                conf_count += 1
                counting_blanks = True
            elif best_idx == 0 and counting_blanks:
                blank_steps += 1

            prev = best_idx

        text = "".join(text_parts).strip()
        conf = conf_sum / conf_count if conf_count > 0 else 0.0
        inner_blank_cov = 0.0
        if conf_count > 0 and steps > 0:
            inner = steps - conf_count
            if inner > 0:
                inner_blank_cov = min(1.0, blank_steps) * (inner / steps)
        return (text, conf, conf_count, inner_blank_cov, n_lat, n_cyr)

    def _allowed(self, char: str) -> bool:
        # Decode alphabet = dict coverage: Cyrillic + Latin + digits +
        # whitespace + punctuation. Script normalization happens later,
        # per word, in fix_lookalikes_per_word (Latin lookalikes -> Cyrillic
        # inside Cyrillic words, pure Latin kept as-is). Blocking Latin here
        # forces true-Latin steps into blank/Cyrillic crumbs and breaks
        # neighboring words (e.g. window title "... yomihon-custom ...").
        if len(char) != 1:
            return False
        cp = ord(char)
        if 0x0400 <= cp <= 0x052F or 0xA640 <= cp <= 0xA69F:
            return True
        if char.isspace() or char in ALLOWED_PUNCTUATION:
            return True
        if 'a' <= char <= 'z' or 'A' <= char <= 'Z' or '0' <= char <= '9':
            return True
        return False

    def _candidate_quality(self, rec: Recognition) -> float:
        if not rec.text.strip():
            return float('-inf')
        from ocr_text_cleaner import cyrillic_fitness, latin_fitness, is_acceptable_cyrillic_text, fix_lookalikes_per_word
        if rec.model == "en":
            fitness = latin_fitness(rec.text)
        else:
            fitness = cyrillic_fitness(rec.text)
        length_bonus = min(len(rec.text) * 0.01, 0.12)
        verifier_bonus = 0.0
        if rec.model == "v5" and is_acceptable_cyrillic_text(fix_lookalikes_per_word(rec.text)):
            verifier_bonus = VERIFIER_CYRILLIC_BONUS
        # Additive confidence: these distilled models cap peak prob at
        # ~0.016 even on perfect inputs, so multiplicative conf would let
        # v3's Latin garbage outrank v5's cleaner Cyrillic on ties.
        return fitness + length_bonus + verifier_bonus + rec.confidence

    def _accepts_confidence(self, rec: Recognition) -> bool:
        letters = sum(1 for c in rec.text if c.isalpha())
        threshold = SHORT_TEXT_MIN_CONFIDENCE if letters in (1, 2, 3) else MIN_ACCEPT_CONFIDENCE
        discounted = self._coverage_penalty(rec.confidence, rec.coverage)
        return discounted >= threshold

    def _coverage_penalty(self, confidence: float, coverage: float) -> float:
        if coverage <= MIN_COVERAGE:
            return confidence
        over = min(1.0, (coverage - MIN_COVERAGE) / (1.0 - MIN_COVERAGE))
        return confidence * (1.0 - 0.40 * over)

    def _create_high_contrast(self, source: Image.Image) -> Image.Image:
        # Match Kotlin createHighContrast: desaturate, then 1.5x + (-45) per channel
        # ColorMatrix: setSaturation(0) then postConcat(1.5, bias -45)
        arr = np.array(source.convert("RGB"), dtype=np.float32)
        # Luma desaturation (same weights as splitWords: 77/150/29)
        lum = (arr[:, :, 0] * 0.299 + arr[:, :, 1] * 0.587 + arr[:, :, 2] * 0.114)
        gray = np.stack([lum, lum, lum], axis=-1)
        out = gray * 1.5 - 45.0
        out = np.clip(out, 0, 255).astype(np.uint8)
        return Image.fromarray(out, mode="RGB")

    def _fallback_whole_image(self, img: Image.Image) -> str:
        result = self._recognize_crop(img)
        if not result.text.strip() or not self._accepts_confidence(result):
            return ""
        return self._clean_recognition(result.text)

    # ── POST-PROCESSING ────────────────────────────────────

    def _merge_boxes(self, boxes: List[TextBox]) -> List[TextBox]:
        if not boxes:
            return []
        sorted_boxes = sorted(boxes, key=lambda b: (b.top, b.left))
        merged = list(sorted_boxes)
        changed = True
        while changed:
            changed = False
            for i in range(len(merged)):
                for j in range(i + 1, len(merged)):
                    a, b = merged[i], merged[j]
                    overlap_y = max(0, min(a.bottom, b.bottom) - max(a.top, b.top))
                    min_h = max(1, min(a.height, b.height))
                    gap_x = max(0, max(a.left, b.left) - min(a.right, b.right))
                    if (overlap_y >= min_h * MERGE_OVERLAP_Y_FACTOR and
                            gap_x <= max(a.height, b.height) * MERGE_GAP_X_FACTOR):
                        new_box = TextBox(
                            min(a.left, b.left), min(a.top, b.top),
                            max(a.right, b.right), max(a.bottom, b.bottom),
                        )
                        merged[i] = new_box
                        merged.pop(j)
                        changed = True
                        break
                if changed:
                    break
        return sorted(merged, key=lambda b: (b.top, b.left))

    def _pad(self, box: TextBox, width: int, height: int) -> TextBox:
        px = max(2, int(box.right - box.left) * 0.04)
        py = max(2, int(box.height) * 0.12)
        return TextBox(
            max(0, box.left - px), max(0, box.top - py),
            min(width, box.right + px), min(height, box.bottom + py),
        )

    def _assemble_reading_order(self, recognized: List[Tuple[TextBox, str]]) -> str:
        if not recognized:
            return ""
        rows = []
        for box, text in recognized:
            placed = False
            for row in rows:
                center = np.mean([b.center_y for b, _ in row])
                height = np.mean([b.height for b, _ in row])
                if abs(box.center_y - center) <= max(box.height, height) * 0.60:
                    row.append((box, text))
                    placed = True
                    break
            if not placed:
                rows.append([(box, text)])
        rows.sort(key=lambda r: min(b.top for b, _ in r))
        lines = []
        for row in rows:
            row.sort(key=lambda x: x[0].left)
            lines.append(" ".join(t.strip() for _, t in row))
        return "\n".join(lines)

    def _clean_recognition(self, text: str) -> str:
        from ocr_text_cleaner import (
            normalize_local_cyrillic_caption, looks_like_dictionary_ramp,
            is_acceptable_cyrillic_text, filter_garbage_tokens,
        )
        cleaned = normalize_local_cyrillic_caption(text).strip()
        if not cleaned or looks_like_dictionary_ramp(cleaned):
            return ""
        if is_acceptable_cyrillic_text(cleaned):
            return cleaned
        return filter_garbage_tokens(cleaned)

    def close(self):
        for m in [self._detector, self._primary, self._verifier]:
            if m is not None:
                try:
                    m.close()
                except Exception:
                    pass
        self._detector = self._primary = self._verifier = None
        self._initialized = False
