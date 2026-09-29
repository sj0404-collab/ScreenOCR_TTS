package com.screenocr.client.tts

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class TtsVoiceSelectorTest {

    private val voices = listOf(
        TtsVoice("ru-net", "ru-RU", requiresNetwork = true),
        TtsVoice("ru-local", "ru-RU", requiresNetwork = false),
        TtsVoice("ru-old", "ru", requiresNetwork = false),
        TtsVoice("en-us", "en-US", requiresNetwork = false),
        TtsVoice("en-gb", "en-GB", requiresNetwork = false),
        TtsVoice("de-de", "de-DE", requiresNetwork = false),
    )

    @Test
    fun `local voice wins over network voice`() {
        assertEquals("ru-local", TtsVoiceSelector.select(voices, "ru")?.name)
    }

    @Test
    fun `regional tag wins over bare language`() {
        val candidates = listOf(
            TtsVoice("ru-old", "ru", requiresNetwork = false),
            TtsVoice("ru-local", "ru-RU", requiresNetwork = false),
        )
        assertEquals("ru-local", TtsVoiceSelector.select(candidates, "ru")?.name)
    }

    @Test
    fun `english selection ignores russian voices and breaks ties by name`() {
        assertEquals("en-gb", TtsVoiceSelector.select(voices, "en")?.name)
    }

    @Test
    fun `missing language yields null so the engine can fall back`() {
        assertNull(TtsVoiceSelector.select(voices, "fr"))
    }

    @Test
    fun `selection is case insensitive`() {
        assertEquals("ru-local", TtsVoiceSelector.select(voices, "RU")?.name)
    }

    @Test
    fun `network only language still resolves`() {
        val onlyNetwork = listOf(TtsVoice("ru-net", "ru-RU", requiresNetwork = true))
        assertEquals("ru-net", TtsVoiceSelector.select(onlyNetwork, "ru")?.name)
    }

    @Test
    fun `empty voice list yields null`() {
        assertNull(TtsVoiceSelector.select(emptyList(), "ru"))
    }

    @Test
    fun `locale tags map to engine locales`() {
        assertEquals("ru-RU", TtsVoiceSelector.localeTagFor("ru"))
        assertEquals("en-US", TtsVoiceSelector.localeTagFor("en"))
    }
}
