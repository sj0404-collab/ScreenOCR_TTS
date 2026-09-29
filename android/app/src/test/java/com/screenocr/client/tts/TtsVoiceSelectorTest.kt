package com.screenocr.client.tts

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class TtsVoiceSelectorTest {

    private val voices = listOf(
        TtsVoice("ru-net", "ru-RU", "Russian (network)", requiresNetwork = true),
        TtsVoice("ru-local", "ru-RU", "Russian local", requiresNetwork = false),
        TtsVoice("ru-old", "ru", "Russian generic", requiresNetwork = false),
        TtsVoice("en-us", "en-US", "English US", requiresNetwork = false),
        TtsVoice("en-gb", "en-GB", "English GB", requiresNetwork = false),
        TtsVoice("de-de", "de-DE", "German", requiresNetwork = false),
    )

    @Test
    fun `local voice wins over network voice`() {
        assertEquals("ru-local", TtsVoiceSelector.select(voices, "ru")?.name)
    }

    @Test
    fun `regional tag wins over bare language`() {
        assertEquals("ru-local", TtsVoiceSelector.select(listOf(TtsVoice("ru-old", "ru", "generic", false), TtsVoice("ru-local", "ru-RU", "regional", false)), "ru")?.name)
    }

    @Test
    fun `english selection is independent of russian voices`() {
        assertEquals("en-us", TtsVoiceSelector.select(voices, "en")?.name)
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
        val onlyNetwork = listOf(TtsVoice("ru-net", "ru-RU", "Russian net", requiresNetwork = true))
        assertEquals("ru-net", TtsVoiceSelector.select(onlyNetwork, "ru")?.name)
    }

    @Test
    fun `locale tags map to engine locales`() {
        assertEquals("ru-RU", TtsVoiceSelector.localeTagFor("ru"))
        assertEquals("en-US", TtsVoiceSelector.localeTagFor("en"))
    }
}
