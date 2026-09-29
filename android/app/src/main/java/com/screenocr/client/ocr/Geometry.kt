package com.screenocr.client.ocr

import kotlin.math.max
import kotlin.math.min

/** Пиксель RGB. Своя структура вместо `Bitmap`, чтобы ядро тестировалось на JVM. */
data class Pixel(val r: Int, val g: Int, val b: Int) {

    fun gray(): Int = (r * 77 + g * 150 + b * 29) shr 8

    fun highContrast(): Pixel {
        val l = gray()
        val v = (l * 3 / 2 - 45).coerceIn(0, 255)
        return Pixel(v, v, v)
    }
}

/** Неизменяемое изображение: массив пикселей по строкам, `pixels[y][x]`. */
class OcrImage(val width: Int, val height: Int, val pixels: Array<IntArray>) {

    init {
        require(width > 0 && height > 0) { "пустое изображение ${width}x$height" }
        require(pixels.size == height) { "ожидалось $height строк, получено ${pixels.size}" }
        pixels.forEachIndexed { y, row ->
            require(row.size == width) { "строка $y: ожидалось $width, получено ${row.size}" }
        }
    }

    fun at(x: Int, y: Int): Pixel {
        val p = pixels[y][x]
        return Pixel((p shr 16) and 0xFF, (p shr 8) and 0xFF, p and 0xFF)
    }

    fun grayAt(x: Int, y: Int): Int = at(x, y).gray()

    /** Кадр [left, right) x [top, bottom) с обрезкой по границам. */
    fun crop(left: Int, top: Int, right: Int, bottom: Int): OcrImage {
        val l = left.coerceIn(0, width)
        val r = max(l + 1, right.coerceIn(l, width))
        val t = top.coerceIn(0, height)
        val b = max(t + 1, bottom.coerceIn(t, height))
        val out = Array(b - t) { Array(r - l) { 0 } }
        for (y in t until b) {
            val src = pixels[y]
            val dst = out[y - t]
            for (x in l until r) dst[x - l] = src[x]
        }
        return OcrImage(r - l, b - t, out)
    }

    fun highContrast(): OcrImage {
        val out = Array(height) { y -> IntArray(width) { x -> at(x, y).highContrast().packed() } }
        return OcrImage(width, height, out)
    }

    companion object {
        fun of(width: Int, height: Int, block: (x: Int, y: Int) -> Pixel): OcrImage {
            val pixels = Array(height) { y -> IntArray(width) { x -> block(x, y).packed() } }
            return OcrImage(width, height, pixels)
        }
    }
}

private fun Pixel.packed(): Int = (r shl 16) or (g shl 8) or b

/**
 * Порог Отсу по серому каналу. Python считает `between = w_b * w_f * (m_b - m_f)^2`
 * (см. `tflite_ocr.py:594`), множители не меняем — иначе порог уедет на входах,
 * где распределение бимодальное, и разбиение на слова станет другим.
 */
object Otsu {

    fun threshold(gray: IntArray): Int {
        require(gray.isNotEmpty()) { "пустое изображение" }
        val hist = IntArray(256)
        for (v in gray) hist[v.coerceIn(0, 255)]++
        val total = gray.size
        val sumAll = DoubleArray(256)
        for (v in 1..255) sumAll[v] = sumAll[v - 1] + v.toDouble() * hist[v]

        var wB = 0
        var sumB = 0.0
        var maxBetween = -1.0
        var otsu = 127
        for (v in 0..255) {
            wB += hist[v]
            if (wB == 0) continue
            val wF = total - wB
            if (wF == 0) break
            sumB += v.toDouble() * hist[v]
            val mB = sumB / wB
            val mF = (sumAll[v] - sumB) / wF
            val between = wB.toDouble() * wF * (mB - mF) * (mB - mF)
            if (between > maxBetween) {
                maxBetween = between
                otsu = v
            }
        }
        return otsu
    }
}

/** Проекция тёмных пикселей на ось X: число чернил в каждом столбце. */
fun OcrImage.inkProjection(otsu: Int = Otsu.threshold(grayArray())): IntArray {
    val ink = IntArray(width)
    for (y in 0 until height) {
        for (x in 0 until width) {
            if (grayAt(x, y) < otsu) ink[x]++
        }
    }
    return ink
}

fun OcrImage.grayArray(): IntArray = IntArray(width * height) { grayAt(it % width, it / width) }

/**
 * Компоненты связности 8-связностью по карте вероятностей детектора.
 * Порог, минимальная площадь и минимальный размер — как в `tflite_ocr.py:390`.
 */
object ConnectedComponents {

    private const val DETECTOR_THRESHOLD = 0.20
    private const val MIN_COMPONENT_AREA = 16
    private const val MIN_SPAN = 3

    fun find(probability: FloatArray, size: Int): List<IntArray> {
        require(probability.size == size * size) { "карта ${probability.size} != $size x $size" }
        val visited = BooleanArray(size * size)
        val queue = IntArray(size * size)
        val boxes = mutableListOf<IntArray>()

        for (start in probability.indices) {
            if (visited[start] || probability[start] < DETECTOR_THRESHOLD) continue
            var head = 0
            var tail = 0
            queue[tail++] = start
            visited[start] = true
            var minX = size
            var minY = size
            var maxX = 0
            var maxY = 0
            var area = 0

            while (head < tail) {
                val idx = queue[head++]
                val x = idx % size
                val y = idx / size
                minX = min(minX, x)
                minY = min(minY, y)
                maxX = max(maxX, x)
                maxY = max(maxY, y)
                area++
                for (dy in -1..1) {
                    for (dx in -1..1) {
                        if (dx == 0 && dy == 0) continue
                        val nx = x + dx
                        val ny = y + dy
                        if (nx < 0 || ny < 0 || nx >= size || ny >= size) continue
                        val next = ny * size + nx
                        if (!visited[next] && probability[next] >= DETECTOR_THRESHOLD) {
                            visited[next] = true
                            queue[tail++] = next
                        }
                    }
                }
            }

            if (area < MIN_COMPONENT_AREA) continue
            if (maxX - minX < MIN_SPAN || maxY - minY < MIN_SPAN) continue
            boxes.add(intArrayOf(minX, minY, maxX, maxY))
        }
        return boxes
    }
}

/** Бокс текста в координатах исходного изображения. */
data class TextBox(val left: Int, val top: Int, val right: Int, val bottom: Int) {

    val width: Int get() = right - left
    val height: Int get() = bottom - top
    val centerY: Double get() = (top + bottom) / 2.0
}

object TextBoxes {

    private const val MERGE_OVERLAP_Y_FACTOR = 0.55
    private const val MERGE_GAP_X_FACTOR = 0.55
    private const val MAX_TEXT_BOXES = 128

    /**
     * Слияние перекрывающихся боксов (`tflite_ocr.py:991`). Итерация до
     * стабилизации: одна пара за проход, как в Python.
     */
    fun merge(boxes: List<TextBox>): List<TextBox> {
        val merged = boxes.sortedWith(compareBy({ it.top }, { it.left })).toMutableList()
        var changed = true
        while (changed) {
            changed = false
            outer@ for (i in merged.indices) {
                for (j in i + 1 until merged.size) {
                    val a = merged[i]
                    val b = merged[j]
                    val overlapY = max(0, min(a.bottom, b.bottom) - max(a.top, b.top))
                    val minH = max(1, min(a.height, b.height))
                    val gapX = max(0, max(a.left, b.left) - min(a.right, b.right))
                    if (overlapY >= minH * MERGE_OVERLAP_Y_FACTOR &&
                        gapX <= max(a.height, b.height) * MERGE_GAP_X_FACTOR
                    ) {
                        merged[i] = TextBox(
                            min(a.left, b.left), min(a.top, b.top),
                            max(a.right, b.right), max(a.bottom, b.bottom),
                        )
                        merged.removeAt(j)
                        changed = true
                        break@outer
                    }
                }
            }
        }
        return merged.sortedWith(compareBy({ it.top }, { it.left }))
    }

    fun cap(boxes: List<TextBox>): List<TextBox> = boxes.take(MAX_TEXT_BOXES)

    /**
     * Поля вокруг бокса: 4% по X, 12% по Y, минимум 2 px
     * (`tflite_ocr.py:1019`). Без распознаватель теряет крайние глифы.
     */
    fun pad(box: TextBox, imageWidth: Int, imageHeight: Int): TextBox {
        val px = max(2, (box.width * 0.04).toInt())
        val py = max(2, (box.height * 0.12).toInt())
        return TextBox(
            max(0, box.left - px), max(0, box.top - py),
            min(imageWidth, box.right + px), min(imageHeight, box.bottom + py),
        )
    }

    /** Промежуточное приведение координат детектора к пикселям исходника. */
    fun fromDetector(
        minX: Int, minY: Int, maxX: Int, maxY: Int,
        scale: Double, offsetX: Int, offsetY: Int,
        offsetInImageX: Int, offsetInImageY: Int,
        sourceWidth: Int, sourceHeight: Int,
    ): TextBox {
        val expandX = max(3, ((maxX - minX) * 0.16).toInt())
        val expandY = max(2, ((maxY - minY) * 0.20).toInt())
        val left = offsetInImageX + max(0, ((minX - expandX - offsetX) / scale).toInt())
        val top = offsetInImageY + max(0, ((minY - expandY - offsetY) / scale).toInt())
        val right = offsetInImageX + min(sourceWidth, Math.ceil((maxX + expandX - offsetX) / scale).toInt())
        val bottom = offsetInImageY + min(sourceHeight, Math.ceil((maxY + expandY - offsetY) / scale).toInt())
        val safeRight = max(right, left + 1)
        val safeBottom = max(bottom, top + 1)
        if (safeRight - left < 6 || safeBottom - top < 6) return TextBox(0, 0, 0, 0)
        return TextBox(left, top, safeRight, safeBottom)
    }

    /**
     * Порядок чтения: строки по Y-центру, внутри строки по X, строки склеены
     * переводом строки (`tflite_ocr.py:1027`).
     */
    fun assembleReadingOrder(items: List<Pair<TextBox, String>>): String {
        if (items.isEmpty()) return ""
        val rows = mutableListOf<MutableList<Pair<TextBox, String>>>()
        for ((box, text) in items) {
            val row = rows.firstOrNull { r ->
                val center = r.sumOf { it.first.centerY } / r.size
                val height = r.sumOf { it.first.height } / r.size
                Math.abs(box.centerY - center) <= max(box.height, height) * 0.60
            }
            if (row != null) row.add(box to text) else rows.add(mutableListOf(box to text))
        }
        return rows
            .sortedBy { r -> r.minOf { it.first.top } }
            .joinToString("\n") { row ->
                row.sortedBy { it.first.left }.joinToString(" ") { it.second.trim() }
            }
    }
}
