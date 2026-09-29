package com.screenocr.client.dict

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class OfflineTranslatorTest {

    private val translator = TestDictionaries.fullTranslator

    @Test
    fun `corpus matches python implementation entry by entry`() {
        val mismatches = mutableListOf<String>()
        var checked = 0
        var fixedMode = 0
        forEachCorpusLine { english, expected, mode, _ ->
            checked++
            if (mode == "fixed") fixedMode++
            val actual = translator.translate(english)
            if (actual != expected) {
                mismatches.add("\"$english\": kotlin=\"$actual\" python=\"$expected\"")
            }
        }
        assertTrue("в корпусе должно быть не меньше 1000 строк, было $checked", checked >= 1000)
        assertTrue("ожидались строки с пустым переводом, их $fixedMode", fixedMode > 0)
        assertEquals("расхождений с Python: ${mismatches.size}\n" + mismatches.take(20).joinToString("\n"), 0, mismatches.size)
    }

    @Test
    fun `corpus flags untranslated text like python`() {
        val mismatches = mutableListOf<String>()
        var checked = 0
        forEachCorpusLine { english, _, _, expectedFlag ->
            checked++
            val actual = translator.hasUntranslated(english)
            if (actual != expectedFlag) mismatches.add("\"$english\": kotlin=$actual python=$expectedFlag")
        }
        assertTrue("проверено $checked строк", checked >= 1000)
        assertEquals("расхождений: ${mismatches.size}\n" + mismatches.take(20).joinToString("\n"), 0, mismatches.size)
    }

    @Test
    fun `literal dictionaries match generated data`() {
        assertEquals(47, DictData.CONTRACTIONS.size)
        assertEquals(258, DictData.PHRASES.size)
        assertEquals(512, DictData.EXTRA_WORDS.size)
        assertEquals("let us", DictData.CONTRACTIONS[0].second)
    }

    @Test
    fun `whole text phrase wins over single words`() {
        assertEquals("как дела", translator.translate("How are you?"))
        assertEquals("доброе утро", translator.translate("good morning"))
    }

    @Test
    fun `translation is case insensitive`() {
        assertEquals(translator.translate("hello world"), translator.translate("HELLO WORLD"))
    }

    @Test
    fun `empty translations do not produce double spaces`() {
        val result = translator.translate("of the box")
        assertEquals("ВТУЛКА", result)
        assertTrue(!result.contains("  "))
    }

    @Test
    fun `articles are dropped because russian has none`() {
        assertEquals("БЕГЛО ВОРОНИТЬ ЛИСА", translator.translate("the quick brown fox"))
        assertTrue(translator.translate("a big house").split(" ").none { it in listOf("a", "an", "the") })
    }

    @Test
    fun `contractions expand before lookup`() {
        assertEquals(translator.translate("do not"), translator.translate("don't"))
    }

    @Test
    fun `unknown words pass through unchanged`() {
        assertEquals("квантовая хромодинамика", translator.translate("квантовая хромодинамика"))
    }

    @Test
    fun `untranslated flag catches leftover latin words`() {
        assertTrue(translator.hasUntranslated("Blorp Snarfle"))
        assertTrue(translator.hasUntranslated("Xylophone Quantum Fluctuation Bingo Wandalo"))
        assertFalse(translator.hasUntranslated("hello world"))
        assertFalse(translator.hasUntranslated("Xylophone Quantum Fluctuation"))
    }

    @Test
    fun `empty input yields empty output`() {
        assertEquals("", translator.translate(""))
        assertEquals("", translator.translate("   "))
        assertTrue(translator.hasUntranslated(""))
    }

    @Test
    fun `literal only translator works without assets`() {
        val lite = OfflineTranslator()
        assertEquals("привет", lite.translate("hello"))
        assertEquals("как дела", lite.translate("how are you"))
    }

    private fun forEachCorpusLine(block: (String, String, String, Boolean) -> Unit) {
        val stream = checkNotNull(javaClass.getResourceAsStream("/translation_corpus.tsv")) {
            "translation_corpus.tsv не найден в ресурсах теста"
        }
        stream.bufferedReader(Charsets.UTF_8).useLines { lines ->
            for (line in lines) {
                if (line.isEmpty() || line.startsWith("#")) continue
                val parts = line.split("\t")
                check(parts.size == 4) { "битая строка корпуса: $line" }
                block(parts[0], parts[1], parts[2], parts[3] == "true")
            }
        }
    }
}
