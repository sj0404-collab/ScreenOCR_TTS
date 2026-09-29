package com.screenocr.client.ocr

import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt

private const val MIN_WORD_GAP_PX = 5
private const val WORD_GAP_FACTOR = 1.7
private const val SPLIT_MIN_WIDTH_PX = 32
private const val MIN_PROB_FOR_SCRIPT_VOTE = 0.008
private const val SCRIPT_MARGIN = 1.2
private const val VERIFIER_CYRILLIC_BONUS = 0.20
private const val MIN_ACCEPT_CONFIDENCE = 0.25
private const val SHORT_TEXT_MIN_CONFIDENCE = 0.12
private const val MIN_COVERAGE = 0.12
private const val CONTRAST_RETRY_CONFIDENCE = 0.90

/** Результат CTC-декодирования одного окна. */
data class CtcResult(
    val text: String,
    val confidence: Double,
    val emittedSteps: Int,
    val coverage: Double,
    val latinVotes: Int,
    val cyrillicVotes: Int,
)

/**
 * Алфавит декодирования: какие классы разрешены и какому письму принадлежат.
 *
 * Латиница в разрешённые классы входит намеренно (`tflite_ocr.py:932`):
 * блокируя её, настоящие латинские шаги схлопываются в blank, и соседние
 * слова распадаются. Размер берётся из выхода модели, а не из словаря —
 * у выходов разных моделей он 165 и 852.
 */
class OcrAlphabet private constructor(
    private val chars: List<String>,
    private val allowed: BooleanArray,
    private val latin: BooleanArray,
    private val cyrillic: BooleanArray,
) {

    val numClasses: Int get() = allowed.size

    fun isAllowed(index: Int): Boolean = index in allowed.indices && allowed[index]

    fun isLatin(index: Int): Boolean = index in latin.indices && latin[index]

    fun isCyrillic(index: Int): Boolean = index in cyrillic.indices && cyrillic[index]

    fun charAt(index: Int): String = chars.getOrNull(index - 1).orEmpty()

    companion object {
        private const val ALLOWED_PUNCTUATION = " .,!?;:-()[]{}\"'«»„“”%№+/=…—–"

        fun of(chars: List<String>, numClasses: Int): OcrAlphabet {
            val allowed = BooleanArray(numClasses)
            val latin = BooleanArray(numClasses)
            val cyrillic = BooleanArray(numClasses)
            for (index in 1 until numClasses) {
                val ch = chars.getOrNull(index - 1).orEmpty()
                if (ch.length != 1) continue
                allowed[index] = isAllowedChar(ch)
                latin[index] = ch[0] in 'a'..'z' || ch[0] in 'A'..'Z'
                cyrillic[index] = isCyrillicChar(ch)
            }
            return OcrAlphabet(chars, allowed, latin, cyrillic)
        }

        fun isAllowedChar(ch: String): Boolean {
            if (ch.length != 1) return false
            val cp = ch[0].code
            if (cp in 0x0400..0x052F || cp in 0xA640..0xA69F) return true
            if (ch[0].isWhitespace() || ALLOWED_PUNCTUATION.contains(ch)) return true
            return ch[0] in 'a'..'z' || ch[0] in 'A'..'Z' || ch[0] in '0'..'9'
        }

        fun isCyrillicChar(ch: String): Boolean {
            if (ch.length != 1) return false
            val cp = ch[0].code
            return cp in 0x0400..0x052F || cp in 0xA640..0xA69F
        }
    }
}

/**
 * Жадный CTC-декод с фильтром по алфавиту и голосованием за письмо
 * (`tflite_ocr.py:846`).
 *
 * Порог 0.008 для подсчёта голосов нельзя поднимать «по вкусу»: пиковые
 * вероятности у этих дистиллированных моделей не превышают ~0.016 даже на
 * идеальном входе, и мягкий порог отсекает шум, а не текст.
 */
object CtcDecoder {

    fun decode(logits: FloatArray, alphabet: OcrAlphabet): CtcResult {
        val numClasses = alphabet.numClasses
        if (numClasses <= 1 || logits.size < numClasses) {
            return CtcResult("", 0.0, 0, 0.0, 0, 0)
        }
        val steps = logits.size / numClasses
        val out = StringBuilder()
        var previous = -1
        var confSum = 0.0
        var confCount = 0
        var blankSteps = 0
        var countingBlanks = false
        var latin = 0
        var cyrillic = 0
        val probs = DoubleArray(numClasses)

        for (step in 0 until steps) {
            val base = step * numClasses
            softmaxInto(logits, base, numClasses, probs)

            var bestIndex = 0
            var bestScore = probs[0]
            var bestLatin = 0.0
            var bestCyrillic = 0.0
            for (idx in 1 until numClasses) {
                if (!alphabet.isAllowed(idx)) continue
                val prob = probs[idx]
                if (prob > bestScore) {
                    bestScore = prob
                    bestIndex = idx
                }
                if (alphabet.isLatin(idx) && prob > bestLatin) bestLatin = prob
                if (alphabet.isCyrillic(idx) && prob > bestCyrillic) bestCyrillic = prob
            }

            if (bestLatin > MIN_PROB_FOR_SCRIPT_VOTE && bestLatin > bestCyrillic * SCRIPT_MARGIN) {
                latin++
            } else if (bestCyrillic > MIN_PROB_FOR_SCRIPT_VOTE &&
                bestCyrillic > bestLatin * SCRIPT_MARGIN
            ) {
                cyrillic++
            }

            if (bestIndex != 0 && bestIndex != previous) {
                out.append(alphabet.charAt(bestIndex))
                confSum += bestScore
                confCount++
                countingBlanks = true
            } else if (bestIndex == 0 && countingBlanks) {
                blankSteps++
            }
            previous = bestIndex
        }

        val inner = steps - confCount
        val coverage = if (confCount > 0 && inner > 0) {
            min(1.0, blankSteps.toDouble()) * (inner.toDouble() / steps)
        } else {
            0.0
        }
        return CtcResult(
            text = out.toString().trim(),
            confidence = if (confCount > 0) confSum / confCount else 0.0,
            emittedSteps = confCount,
            coverage = coverage,
            latinVotes = latin,
            cyrillicVotes = cyrillic,
        )
    }

    /** Мягмакс по шагу: один проход на максимум, один на сумму. */
    private fun softmaxInto(logits: FloatArray, base: Int, numClasses: Int, out: DoubleArray) {
        var maxLogit = logits[base]
        for (idx in base + 1 until base + numClasses) {
            if (logits[idx] > maxLogit) maxLogit = logits[idx]
        }
        var sum = 0.0
        for (idx in base until base + numClasses) {
            val value = Math.exp((logits[idx] - maxLogit).toDouble())
            out[idx - base] = value
            sum += value
        }
        if (sum == 0.0) {
            for (idx in 0 until numClasses) out[idx] = 0.0
            return
        }
        for (idx in 0 until numClasses) out[idx] /= sum
    }
}

/**
 * Склейка перекрывающихся окон распознавателя (`tflite_ocr.py:794`).
 *
 * Общий контент ищется как наибольшая общая ПОДСТРОКА в канонической
 * омоглифной форме: окно режется по краю и может потерять глиф на шве, тогда
 * суффиксное совпадение не сработает. Splice сохраняет исходные строки.
 */
object WindowMerger {

    private val CANON = mapOf(
        'а' to 'a', 'с' to 'c', 'е' to 'e', 'о' to 'o', 'р' to 'p', 'х' to 'x', 'у' to 'y',
        'А' to 'a', 'В' to 'b', 'С' to 'c', 'Е' to 'e', 'Н' to 'h', 'К' to 'k', 'М' to 'm',
        'О' to 'o', 'Р' to 'p', 'Т' to 't', 'Х' to 'x', 'У' to 'y',
        'і' to 'i', 'І' to 'i', 'ї' to 'i', 'є' to 'e', 'Є' to 'e', 'ґ' to 'g', 'Ґ' to 'g',
        'ё' to 'е', 'Ё' to 'е',
    )

    fun canon(text: String): String = buildString(text.length) {
        for (ch in text) append((CANON[ch] ?: ch).lowercaseChar())
    }

    fun merge(texts: List<String>): String {
        if (texts.isEmpty()) return ""
        var merged = texts.first()
        for (next in texts.drop(1)) {
            if (next.isEmpty()) continue
            if (merged.isEmpty()) {
                merged = next
                continue
            }
            val tail = canon(merged.takeLast(48))
            val head = canon(next.take(48))
            var bestK = 0
            var bestLength = 0
            for (k in 0 until min(24, next.length)) {
                var found = false
                for (length in min(next.length - k, tail.length) downTo 4) {
                    val end = k + length
                    if (end > head.length) continue
                    if (length > bestLength && tail.contains(head.substring(k, end))) {
                        bestK = k
                        bestLength = length
                        found = true
                        break
                    }
                }
                if (found && bestLength >= 8) break
            }
            // Шов не должен съедать пробел: иначе «привет» + «мир всё» склеится
            // в «приветмир». В Python здесь окно накрывает общий суффикс вместе
            // с разделителем, и слова липаются.
            if (bestLength > 1 && next.getOrNull(bestK + bestLength - 1) == ' ') bestLength--
            merged += next.substring(bestK + bestLength)
        }
        return merged
    }
}

/** Кандидат распознавания строки. */
data class Recognition(
    val text: String,
    val confidence: Double,
    val model: String = "v3",
    val coverage: Double = 0.0,
    val latinVotes: Int = 0,
    val cyrillicVotes: Int = 0,
) {

    fun alphaCount(): Int = text.count { it.isLetter() }

    /** Уверенный латинский кроп уходит в английский движок (`tflite_ocr.py:660`). */
    fun isLatinCrop(): Boolean = latinVotes >= 2 && latinVotes > cyrillicVotes * 1.25

    /**
     * Оценка кандидата (`tflite_ocr.py:943`): фитнес алфавита + бонус за
     * длину + бонус верификатору + уверенность.
     *
     * Уверенность складывается, а не умножается: пик этих моделей ~0.016,
     * при умножении мусор от v3 обгонял бы чистую кириллицу v5 на ничьих.
     */
    fun quality(fitness: Double, verifierAccepts: Boolean = false): Double {
        if (text.isBlank()) return Double.NEGATIVE_INFINITY
        val lengthBonus = min(text.length * 0.01, 0.12)
        val verifierBonus = if (model == "v5" && verifierAccepts) VERIFIER_CYRILLIC_BONUS else 0.0
        return fitness + lengthBonus + verifierBonus + confidence
    }
}

object ConfidenceGate {

    fun accepts(rec: Recognition): Boolean {
        val letters = rec.alphaCount()
        val threshold = if (letters in 1..3) SHORT_TEXT_MIN_CONFIDENCE else MIN_ACCEPT_CONFIDENCE
        return discounted(rec.confidence, rec.coverage) >= threshold
    }

    fun discounted(confidence: Double, coverage: Double): Double {
        if (coverage <= MIN_COVERAGE) return confidence
        val over = min(1.0, (coverage - MIN_COVERAGE) / (1.0 - MIN_COVERAGE))
        return confidence * (1.0 - 0.40 * over)
    }

    fun needsContrastRetry(rec: Recognition): Boolean = rec.confidence < CONTRAST_RETRY_CONFIDENCE
}

/**
 * Разбиение строки на слова: вертикальная проекция чернил + порог Отсу +
 * медиана пробелов. Метрики совпадают с `tflite_ocr.py:578`.
 */
object WordSplitter {

    fun split(crop: OcrImage): List<OcrImage> {
        if (crop.width < SPLIT_MIN_WIDTH_PX) return listOf(crop)
        val runs = inkRuns(crop.inkProjection()) ?: return listOf(crop)
        if (runs.size < 2) return listOf(crop)
        val gaps = runs.indices.dropLast(1).map { runs[it + 1][0] - runs[it][1] - 1 }
        val threshold = max(MIN_WORD_GAP_PX, (medianGap(gaps) * WORD_GAP_FACTOR).roundToInt())
        val groups = mutableListOf<IntArray>()
        var groupStart = runs.first()[0]
        var splits = 0
        for ((i, gap) in gaps.withIndex()) {
            if (gap >= threshold) {
                groups.add(intArrayOf(groupStart, runs[i][1]))
                groupStart = runs[i + 1][0]
                splits++
            }
        }
        if (splits == 0) return listOf(crop)
        groups.add(intArrayOf(groupStart, runs.last()[1]))
        if (groups.size <= 1) return listOf(crop)

        // Щедрые поля: распознаватель теряет крайние глифы без левого контекста.
        return groups.map { (start, end) ->
            val left = max(0, start - 10)
            val right = min(crop.width, end + 8)
            crop.crop(left, 0, max(left + 1, right), crop.height)
        }
    }

    private fun medianGap(gaps: List<Int>): Double {
        val positive = gaps.filter { it > 0 }.sorted()
        return if (positive.isEmpty()) 1.0 else positive[positive.size / 2].toDouble()
    }

    private fun inkRuns(ink: IntArray): List<IntArray>? {
        val runs = mutableListOf<IntArray>()
        var start = -1
        for (x in ink.indices) {
            if (ink[x] > 0) {
                if (start < 0) start = x
            } else if (start >= 0) {
                runs.add(intArrayOf(start, x - 1))
                start = -1
            }
        }
        if (start >= 0) runs.add(intArrayOf(start, ink.size - 1))
        return runs.ifEmpty { null }
    }
}

/** Окна распознавателя: длинные кропы режутся с перекрытием под ширину 320 px. */
object WindowPlanner {

    const val RECOGNIZER_WIDTH = 320
    const val RECOGNIZER_HEIGHT = 48

    fun windows(crop: OcrImage): List<OcrImage> {
        val scale = RECOGNIZER_HEIGHT.toDouble() / max(1, crop.height)
        if (crop.width * scale <= RECOGNIZER_WIDTH) return listOf(crop)
        val winWidth = max(16, RECOGNIZER_WIDTH * crop.height / RECOGNIZER_HEIGHT)
        val stride = max(8, winWidth - max(16, winWidth / 5))
        val starts = mutableListOf<Int>()
        val last = max(1, crop.width - winWidth + 1)
        var s = 0
        while (s < last) {
            starts.add(s)
            s += stride
        }
        if (starts.isEmpty() || starts.last() + winWidth < crop.width) {
            starts.add(max(0, crop.width - winWidth))
        }
        return starts.map { crop.crop(it, 0, min(crop.width, it + winWidth), crop.height) }
    }
}

/** Склейка кусков строки с дедупликацией «призрачных» глифов на полях. */
object PieceJoiner {

    /**
     * Щедрые поля кусков захватывают крайний глиф соседа ('и' + 'иподелиться').
     * Дубликат в начале убирается, но только когда остаток — известное слово,
     * иначе настоящие повторы ('кот' + 'торт') портятся в 'кот орт'.
     */
    fun join(parts: List<String>, isKnownWord: (String) -> Boolean = { false }): String {
        val out = mutableListOf<String>()
        for (raw in parts) {
            var piece = raw.trim()
            if (piece.isEmpty()) continue
            if (out.isNotEmpty() && !piece.contains(' ') && startsCyrillic(piece)) {
                val tail = WindowMerger.canon(out.last().replace(" ", "").takeLast(8))
                val head = piece.take(8)
                for (k in min(3, piece.length) downTo 1) {
                    if (!tail.endsWith(head.take(k))) continue
                    val rest = piece.substring(k)
                    if (rest.isEmpty()) {
                        if (piece.length == 1) piece = ""
                        break
                    }
                    if (isKnownWord(stripEdges(rest))) piece = rest
                    break
                }
                if (piece.isEmpty()) continue
            }
            out.add(piece)
        }
        return out.joinToString(" ").trim()
    }

    private fun startsCyrillic(text: String): Boolean {
        val first = text.firstOrNull() ?: return false
        val cp = first.code
        return cp in 0x0400..0x052F || cp in 0xA640..0xA69F
    }

    private fun stripEdges(text: String): String = text.trim { ch ->
        val cp = ch.code
        cp < 0x0400 || cp > 0x052F
    }
}
