package com.screenocr.client.tts

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class SpeechTextTest {

    @Test
    fun `empty text produces no chunks`() {
        assertTrue(SpeechText.chunks("").isEmpty())
        assertTrue(SpeechText.chunks("   \n\t ").isEmpty())
    }

    @Test
    fun `short text stays in one chunk`() {
        assertEquals(listOf("Привет, как дела?"), SpeechText.chunks("  Привет,   как дела?  "))
    }

    @Test
    fun `newline and tabs collapse into single spaces`() {
        assertEquals(listOf("раз два три"), SpeechText.chunks("раз\nдва\t\tтри"))
    }

    @Test
    fun `long text splits on sentence boundaries`() {
        val sentence = "Это предложение ровно такой длины, чтобы проверить разбиение. "
        val text = sentence.repeat(4)
        val chunks = SpeechText.chunks(text)
        assertTrue("ожидалось больше одного куска, получено ${chunks.size}", chunks.size > 1)
        assertTrue(chunks.all { it.length <= SpeechText.MAX_CHUNK })
        assertTrue(chunks.none { it.isEmpty() })
    }

    @Test
    fun `a single oversized sentence splits on words`() {
        val long = (1..120).joinToString(" ") { "слово$it" }
        val chunks = SpeechText.chunks(long)
        assertTrue(chunks.size > 1)
        assertTrue(chunks.all { it.length <= SpeechText.MAX_CHUNK })
        assertEquals(long.split(" ").joinToString(" "), chunks.joinToString(" "))
    }

    @Test
    fun `no words are lost or duplicated`() {
        val text = "Раз. Два! Три? Четыре… Пять. " + "Шесть ".repeat(80)
        val restored = SpeechText.chunks(text).joinToString(" ")
        assertEquals(text.replace(Regex("\\s+"), " ").trim(), restored)
    }
}
