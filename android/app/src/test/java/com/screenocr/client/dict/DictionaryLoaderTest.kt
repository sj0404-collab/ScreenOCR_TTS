package com.screenocr.client.dict

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class DictionaryLoaderTest {

    private val dicts = TestDictionaries.dicts

    @Test
    fun `json dictionary is a superset of the literal words`() {
        assertEquals(68155, dicts.words.size)
        val missing = DictData.EXTRA_WORDS.keys.filter { it !in dicts.words }
        assertEquals("в en_rus_full.json нет ключей из words_extra.py: $missing", 0, missing.size)
    }

    @Test
    fun `json dictionaries extend the literal phrases`() {
        val phrasesFromJson = 510 + 686
        assertTrue(
            "ожидалось от 259 до ${258 + phrasesFromJson} фраз, получено ${dicts.phrases.size}",
            dicts.phrases.size in 259..(258 + phrasesFromJson),
        )
        assertTrue(dicts.phrases.size > DictData.PHRASES.size)
    }

    @Test
    fun `literal dictionaries win over json duplicates`() {
        val fromLiteral = DictData.PHRASES["hello"]
        assertEquals("привет", fromLiteral)
        assertEquals("привет", dicts.phrases["hello"])
    }

    @Test
    fun `cities and game names land in the phrase table`() {
        assertTrue(dicts.phrases.containsKey("new york"))
        assertTrue(dicts.phrases.size > DictData.PHRASES.size)
    }

    @Test
    fun `known entries translate end to end`() {
        val translator = OfflineTranslator(dicts.words, dicts.phrases)
        assertEquals("привет мир", translator.translate("hello world"))
        assertTrue(translator.translate("teleport to the arena").isNotEmpty())
    }

    @Test
    fun `missing dictionary file is reported`() {
        try {
            DictionaryLoader.loadFromDir(java.io.File("/nonexistent-dict-dir"))
            throw AssertionError("ожидалось исключение об отсутствии файла")
        } catch (expected: java.io.FileNotFoundException) {
            assertTrue(expected.message!!.contains("en_rus_full.json"))
        }
    }
}
