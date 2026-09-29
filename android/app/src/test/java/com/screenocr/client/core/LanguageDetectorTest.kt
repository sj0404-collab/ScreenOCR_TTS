package com.screenocr.client.core

import org.junit.Test
import org.junit.Assert.assertEquals

class LanguageDetectorTest {

    @Test
    fun `cyrillic text is russian`() {
        assertEquals(LanguageDetector.RU, LanguageDetector.detect("Привет мир"))
    }

    @Test
    fun `latin text is english`() {
        assertEquals(LanguageDetector.EN, LanguageDetector.detect("Hello world"))
    }

    @Test
    fun `empty text falls back to english`() {
        assertEquals(LanguageDetector.EN, LanguageDetector.detect(""))
    }

    @Test
    fun `digits are not a language`() {
        assertEquals(LanguageDetector.EN, LanguageDetector.detect("12345"))
    }

    @Test
    fun `mixed text follows the thirty percent threshold`() {
        assertEquals(LanguageDetector.RU, LanguageDetector.detect("Привет world"))
        assertEquals(LanguageDetector.RU, LanguageDetector.detect("abc Привет"))
    }
}
