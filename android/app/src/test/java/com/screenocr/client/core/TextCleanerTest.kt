package com.screenocr.client.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class TextCleanerTest {

    @Test
    fun `mixed alphabet word keeps latin tail and gains a space`() {
        assertEquals(
            "Поискиклонирование yomihon",
            TextCleaner.fixLookalikesPerWord("Поискиклонированиеyomihon"),
        )
    }

    @Test
    fun `cyrillic majority maps latin lookalikes`() {
        assertEquals("Привет саkсаlо", TextCleaner.fixLookalikesPerWord("Привет cаkcаlо"))
    }

    @Test
    fun `pure latin words are preserved`() {
        assertEquals(
            "Мир hello саkсаlо SOS Wi-Fi",
            TextCleaner.fixLookalikesPerWord("Мир hello cаkcаlо SOS Wi-Fi"),
        )
    }

    @Test
    fun `ukrainian letters are folded to russian`() {
        assertEquals("Ыжак испит", TextCleaner.fixLookalikesPerWord("Їжак іспит"))
    }

    @Test
    fun `empty text is returned as is`() {
        assertEquals("", TextCleaner.fixLookalikesPerWord(""))
    }

    @Test
    fun `line hyphenation is joined`() {
        assertEquals(
            "переносится хорошо",
            TextCleaner.joinLineHyphens("пере-\nносится хо-\nрошо"),
        )
    }

    @Test
    fun `garbage tokens are dropped but cyrillic survives`() {
        assertEquals(
            "Привет мир 123 ok",
            TextCleaner.filterGarbageTokens("Привет ★你好 мир 123 ok ★★"),
        )
    }

    @Test
    fun `whitespace and punctuation are normalized`() {
        assertEquals(
            "Привет мир. Как дела?",
            TextCleaner.normalizeWhitespace("  Привет   мир .  Как дела?  "),
        )
    }

    @Test
    fun `ellipsis collapses and space after opening bracket is removed`() {
        assertEquals("(заметка ) и … всё", TextCleaner.normalizeWhitespace("( заметка ) и ... всё"))
    }

    @Test
    fun `trailing zeros are stripped from numbers`() {
        assertEquals(
            "1.5 100 1 12 3,14 2.1 7",
            TextCleaner.normalizeNumbers("1.5000000 100.00000 1.00 12 3,1400 2.10 7"),
        )
    }

    @Test
    fun `known ocr confusions of hotkeys are corrected`() {
        assertEquals(
            "нажми Ctrl и Alt, потом Shift и Del",
            TextCleaner.applyKnownCorrections("нажми curl и alf, потом shlft и dei"),
        )
    }

    @Test
    fun `glued cyrillic words are split by dictionary`() {
        assertEquals("поиск клонирование", TextCleaner.restoreKnownWords("поискклонирование"))
    }

    @Test
    fun `case is restored after split`() {
        assertEquals("ПОИСК КЛОНИРОВАНИЕ", TextCleaner.restoreKnownWords("ПОИСККЛОНИРОВАНИЕ"))
    }

    @Test
    fun `unknown long word is left intact`() {
        assertEquals(
            "неизвестноесловосочетание",
            TextCleaner.restoreKnownWords("неизвестноесловосочетание"),
        )
    }

    @Test
    fun `dictionary ramp is detected`() {
        assertTrue(TextCleaner.looksLikeDictionaryRamp("абвгдеёжзийклмноп"))
        assertFalse(TextCleaner.looksLikeDictionaryRamp("Привет мир как дела"))
    }

    @Test
    fun `leading punctuation is reattached to previous word`() {
        assertEquals("файл, чтобы открыть", TextCleaner.reattachLeadingPunct("файл ,чтобы открыть"))
    }

    @Test
    fun `acceptability check allows cyrillic and latin`() {
        assertTrue(TextCleaner.isAcceptableCyrillicText("Привет мир OK"))
        assertFalse(TextCleaner.isAcceptableCyrillicText("Привет 12abc"))
    }

    @Test
    fun `fitness ratios split by script`() {
        assertEquals(1.0, TextCleaner.cyrillicFitness("Привет мир"), 1e-9)
        assertEquals(0.0, TextCleaner.cyrillicFitness("Hello world"), 1e-9)
        assertEquals(1.0, TextCleaner.latinFitness("Hello world"), 1e-9)
    }

    @Test
    fun `full pipeline cleans a messy ocr line`() {
        assertEquals(
            "Саkсаlо порвдите,что делать? нажми Ctrl 1.5 ★мусор★",
            TextCleaner.fullCleanPipeline("  Cаkcаlо  по-\nрвдите  ,что делать?  нажми curl   1.5000000  ★мусор★  "),
        )
    }

    @Test
    fun `english engine skips cyrillic dictionary split`() {
        assertEquals(
            "Press ctrl and Alt now",
            TextCleaner.fullCleanPipeline("  Press  ctrl   and  alf  now", engineType = "easyocr_en"),
        )
    }

    @Test
    fun `full pipeline reports dictionary ramp`() {
        var reported: String? = null
        val result = TextCleaner.fullCleanPipeline("абвгдеёжзийклмноп") { reported = it }
        assertTrue(reported != null)
        assertEquals(result, reported)
    }
}
