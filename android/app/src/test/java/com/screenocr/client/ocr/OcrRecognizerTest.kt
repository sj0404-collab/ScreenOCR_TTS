package com.screenocr.client.ocr

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.math.abs

class OcrRecognizerTest {

    /** Тестовый словарь: класс 0 — blank, дальше символы по порядку классов. */
    private val chars = listOf(" ") + listOf(
        "а", "б", "в", "г", "д", "е", "ж", "з", "и", "к", "л", "м", "н", "о", "п", "р", "с",
        "т", "у", "ф", "х", "ц", "ч", "ш", "щ", "ъ", "ы", "ь", "э", "ю", "я",
        "a", "b", "c", "d", "e", "o", "p", "x", "y",
        "0", "1", "2", ".", ",", "?", "!", "§", "中",
    )

    private val alphabet = OcrAlphabet.of(chars, chars.size + 1)

    private fun idx(ch: String): Int = chars.indexOf(ch) + 1

    /** Логиты: по одному шагу на каждый аргумент, максимум у указанных классов. */
    private fun logits(vararg steps: IntArray): FloatArray {
        val stride = chars.size + 1
        val values = FloatArray(steps.size * stride)
        steps.forEachIndexed { step, winners ->
            winners.forEach { values[step * stride + it] = 10f }
        }
        return values
    }

    /** Синтетический кроп: чернила в колонках `ink`, фон со слегка разным тоном. */
    private fun crop(width: Int, ink: BooleanArray): OcrImage = OcrImage.of(width, 12) { x, _ ->
        if (ink[x]) Pixel(8 + x % 5, 8 + x % 5, 8 + x % 5)
        else Pixel(200 + x % 5, 200 + x % 5, 200 + x % 5)
    }

    @Test
    fun `blank class is never a decoded character`() {
        assertTrue(alphabet.isAllowed(idx(" ")))
        assertTrue(!alphabet.isAllowed(0))
    }

    @Test
    fun `chars outside the decode alphabet are dropped`() {
        assertTrue(!alphabet.isAllowed(idx("中")))
        assertTrue(!alphabet.isAllowed(chars.size + 5))
    }

    @Test
    fun `decodes a simple word`() {
        val word = "привет"
        val steps = word.map { intArrayOf(idx(it.toString())) }.toTypedArray()
        val result = CtcDecoder.decode(logits(*steps), alphabet)
        assertEquals(word, result.text)
        assertEquals(6, result.emittedSteps)
        assertTrue(result.confidence > 0.5)
    }

    @Test
    fun `repeated classes collapse until a blank arrives`() {
        val a = idx("а")
        val result = CtcDecoder.decode(
            logits(intArrayOf(a), intArrayOf(a), intArrayOf(a), intArrayOf(0), intArrayOf(a)),
            alphabet,
        )
        assertEquals("аа", result.text)
    }

    @Test
    fun `disallowed winner loses to the allowed one`() {
        val result = CtcDecoder.decode(
            logits(intArrayOf(idx("中")), intArrayOf(idx("б")), intArrayOf(idx("中"))),
            alphabet,
        )
        assertEquals("б", result.text)
    }

    @Test
    fun `script votes follow the winning class`() {
        val cyrillic = logits(intArrayOf(idx("о")), intArrayOf(idx("б")))
        val latin = logits(intArrayOf(idx("o")), intArrayOf(idx("p")))
        assertEquals(2, CtcDecoder.decode(cyrillic, alphabet).cyrillicVotes)
        assertEquals(0, CtcDecoder.decode(cyrillic, alphabet).latinVotes)
        assertEquals(2, CtcDecoder.decode(latin, alphabet).latinVotes)
        assertEquals(0, CtcDecoder.decode(latin, alphabet).cyrillicVotes)
    }

    @Test
    fun `uniform steps do not vote for a script`() {
        val result = CtcDecoder.decode(logits(intArrayOf(0), intArrayOf(0)), alphabet)
        assertEquals(0, result.latinVotes)
        assertEquals(0, result.cyrillicVotes)
        assertEquals("", result.text)
    }

    @Test
    fun `inner blanks raise coverage`() {
        val tight = CtcDecoder.decode(
            logits(intArrayOf(idx("а")), intArrayOf(idx("б")), intArrayOf(idx("в"))),
            alphabet,
        )
        val loose = CtcDecoder.decode(
            logits(
                intArrayOf(idx("а")), intArrayOf(0), intArrayOf(0), intArrayOf(0),
                intArrayOf(idx("б")), intArrayOf(0), intArrayOf(0), intArrayOf(0),
                intArrayOf(idx("в")),
            ),
            alphabet,
        )
        assertEquals(tight.text, loose.text)
        assertTrue("покрытие ${loose.coverage} должно быть выше ${tight.coverage}", loose.coverage > tight.coverage)
    }

    @Test
    fun `truncated logits decode to nothing instead of crashing`() {
        val result = CtcDecoder.decode(FloatArray(3), alphabet)
        assertEquals("", result.text)
        assertEquals(0.0, result.confidence, 0.0)
    }

    @Test
    fun `decoded text is trimmed`() {
        val result = CtcDecoder.decode(
            logits(intArrayOf(0, idx(" ")), intArrayOf(idx("а"))),
            alphabet,
        )
        assertEquals("а", result.text)
    }

    @Test
    fun `window merge keeps one space at the seam`() {
        assertEquals("привет мир всё", WindowMerger.merge(listOf("привет мир", "мир всё")))
    }

    @Test
    fun `window merge drops the shared substring`() {
        assertEquals("приветик дома", WindowMerger.merge(listOf("приветик", "етик дома")))
    }

    @Test
    fun `window merge sees through a latin lookalike at the seam`() {
        assertEquals("приветик дома", WindowMerger.merge(listOf("приветик", "etик дома")))
    }

    @Test
    fun `window merge keeps empty windows out of the result`() {
        assertEquals("текст", WindowMerger.merge(listOf("текст", "")))
        assertEquals("", WindowMerger.merge(emptyList()))
    }

    @Test
    fun `latin crop routing needs two clear latin votes`() {
        assertTrue(Recognition("hi", 0.5, latinVotes = 2, cyrillicVotes = 0).isLatinCrop())
        assertTrue(!Recognition("hi", 0.5, latinVotes = 1, cyrillicVotes = 0).isLatinCrop())
        assertTrue(!Recognition("hi", 0.5, latinVotes = 2, cyrillicVotes = 2).isLatinCrop())
        assertTrue(!Recognition("привет", 0.5, latinVotes = 2, cyrillicVotes = 5).isLatinCrop())
    }

    @Test
    fun `verifier gets a bonus only when its text is acceptable`() {
        val v3 = Recognition("народ", 0.3, model = "v3")
        val v5 = Recognition("народ", 0.3, model = "v5")
        assertTrue(v5.quality(0.5, verifierAccepts = true) > v3.quality(0.5))
        assertTrue(abs(v5.quality(0.5, verifierAccepts = false) - v3.quality(0.5)) < 1e-9)
    }

    @Test
    fun `blank candidate is always the worst`() {
        assertEquals(Double.NEGATIVE_INFINITY, Recognition("   ", 0.9).quality(1.0), 0.0)
    }

    @Test
    fun `short text uses the lower confidence threshold`() {
        assertTrue(ConfidenceGate.accepts(Recognition("дом", 0.2)))
        assertTrue(!ConfidenceGate.accepts(Recognition("слово", 0.2)))
    }

    @Test
    fun `heavy inner blank coverage discounts confidence`() {
        val rec = Recognition("слово", 0.8, coverage = 0.9)
        assertTrue(ConfidenceGate.discounted(0.8, 0.9) < 0.8)
        assertTrue(!ConfidenceGate.accepts(rec))
    }

    @Test
    fun `contrast retry triggers below the peak confidence`() {
        assertTrue(ConfidenceGate.needsContrastRetry(Recognition("текст", 0.4)))
        assertTrue(!ConfidenceGate.needsContrastRetry(Recognition("текст", 0.95)))
    }

    @Test
    fun `word splitter keeps a narrow crop whole`() {
        val ink = BooleanArray(20) { it % 2 == 0 }
        assertEquals(1, WordSplitter.split(crop(20, ink)).size)
    }

    @Test
    fun `word splitter splits on a wide gap`() {
        val ink = BooleanArray(120)
        for (x in 0 until 30) ink[x] = true
        for (x in 70 until 110) ink[x] = true
        val pieces = WordSplitter.split(crop(120, ink))
        assertEquals(2, pieces.size)
        assertTrue("первый кусок должен накрывать свои 30 колонок", pieces[0].right >= 30)
        assertTrue("второй кусок должен начинаться до 70-й колонки", pieces[1].left <= 70)
    }

    @Test
    fun `letter sized gaps do not split a word`() {
        val ink = BooleanArray(120)
        for (x in 0 until 60) ink[x] = true
        for (x in 64 until 120) ink[x] = true
        assertEquals(1, WordSplitter.split(crop(120, ink)).size)
    }

    @Test
    fun `long crops are covered by overlapping windows`() {
        val windows = WindowPlanner.windows(OcrImage.of(2000, 48) { _, _ -> Pixel(0, 0, 0) })
        assertTrue(windows.size > 1)
        assertEquals(0, windows.first().left)
        assertEquals(2000, windows.last().right)
    }

    @Test
    fun `short crop needs a single window`() {
        assertEquals(1, WindowPlanner.windows(OcrImage.of(300, 48) { _, _ -> Pixel(0, 0, 0) }).size)
    }

    @Test
    fun `piece joiner drops a ghost glyph when the rest is a known word`() {
        assertEquals("поделиться", PieceJoiner.join(listOf("подели", "иться"), isKnownWord = { it == "иться" }))
    }

    @Test
    fun `piece joiner keeps legit repeats`() {
        assertEquals("кот торт", PieceJoiner.join(listOf("кот", "торт"), isKnownWord = { it == "торт" }))
    }

    @Test
    fun `piece joiner drops empty pieces`() {
        assertEquals("раз два", PieceJoiner.join(listOf("раз", "  ", "два")))
    }
}
