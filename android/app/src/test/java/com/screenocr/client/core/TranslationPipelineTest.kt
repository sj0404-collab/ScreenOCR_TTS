package com.screenocr.client.core

import com.screenocr.client.dict.DictionaryLoader
import com.screenocr.client.dict.OfflineTranslator
import com.screenocr.client.dict.TestDictionaries
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class TranslationPipelineTest {

    private val pipeline = TranslationPipeline(
        OfflineTranslator(TestDictionaries.dicts.words, TestDictionaries.dicts.phrases),
    )

    @Test
    fun `english input is translated`() {
        val result = pipeline.process("Hello world")
        assertEquals("en", result.sourceLanguage)
        assertEquals("привет мир", result.spoken)
        assertEquals("ru", result.language)
        assertFalse(result.needsBetterTranslation)
    }

    @Test
    fun `russian input is spoken as is`() {
        val result = pipeline.process("Привет мир")
        assertEquals("ru", result.sourceLanguage)
        assertEquals("Привет мир", result.spoken)
        assertEquals("ru", result.language)
        assertFalse(result.needsBetterTranslation)
    }

    @Test
    fun `cleaning runs before translation`() {
        val result = pipeline.process("  Cаkcаlо  нажми curl  ")
        assertTrue(result.cleaned.contains("Ctrl"))
        assertTrue(result.spoken.isNotEmpty())
    }

    @Test
    fun `uncovered latin text raises the warning flag`() {
        val result = pipeline.process("Blorp Snarfle")
        assertEquals("en", result.sourceLanguage)
        assertTrue(result.needsBetterTranslation)
    }

    @Test
    fun `empty input yields empty speech`() {
        val result = pipeline.process("   ")
        assertEquals("", result.spoken)
    }

    @Test
    fun `input is preserved for display`() {
        val raw = "  Hello   world  "
        assertEquals(raw, pipeline.process(raw).input)
    }
}
