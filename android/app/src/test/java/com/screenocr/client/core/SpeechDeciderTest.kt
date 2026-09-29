package com.screenocr.client.core

import org.junit.Assert.assertEquals
import org.junit.Test

class SpeechDeciderTest {

    private val decider = SpeechDecider()

    @Test
    fun `own voice is never spoken`() {
        val d = decider.decide("Привет как дела", isEcho = true)
        assertEquals(SpeechDecider.SKIP, d.action)
        assertEquals(0.0, d.score, 1e-9)
        assertEquals("жёсткое правило: это наш собственный голос", d.reason)
    }

    @Test
    fun `empty candidate is skipped`() {
        val d = decider.decide("   ")
        assertEquals(SpeechDecider.SKIP, d.action)
        assertEquals(0.0, d.score, 1e-9)
        assertEquals("пустой текст", d.reason)
    }

    @Test
    fun `stable subtitle in the lower screen is spoken`() {
        val d = decider.decide("Привет, как дела?", stability = 3, screenPos = 0.9, age = 0.2)
        assertEquals(SpeechDecider.SPEAK, d.action)
        assertEquals(0.6421, d.score, 1e-4)
        assertEquals(
            "score=0.64 [порог 0.55/0.30] за: words+1.00 length+0.50 dialog_marks+1.20 sentence_start+0.50 " +
                "против: has_voice-2.00 voice_text-1.20 overlap_voice-1.50",
            d.reason,
        )
    }

    @Test
    fun `gibberish is only refined`() {
        val d = decider.decide("OMCK", stability = 3, screenPos = 0.9, age = 0.2)
        assertEquals(SpeechDecider.REFINE, d.action)
        assertEquals(0.4319, d.score, 1e-4)
    }

    @Test
    fun `single letter is not gibberish quality but too short`() {
        val d = decider.decide("X", stability = 3, screenPos = 0.9, age = 0.2)
        assertEquals(SpeechDecider.REFINE, d.action)
        assertEquals(0.4319, d.score, 1e-4)
    }

    @Test
    fun `cjk text is treated as real speech`() {
        val d = decider.decide("你好世界", stability = 3, screenPos = 0.9, age = 0.2)
        assertEquals(SpeechDecider.SPEAK, d.action)
        assertEquals(0.5616, d.score, 1e-4)
    }

    @Test
    fun `text confirmed by voice is spoken`() {
        val d = decider.decide(
            "иди к выходу",
            hasVoice = true,
            voiceText = "иди к выходу",
            stability = 3,
            screenPos = 0.9,
            age = 0.2,
        )
        assertEquals(SpeechDecider.SPEAK, d.action)
        assertEquals(0.8435, d.score, 1e-4)
    }

    @Test
    fun `flickering text drops below speak threshold`() {
        val d = decider.decide("Привет, как дела?", stability = 0, screenPos = 0.9, age = 0.2)
        assertEquals(SpeechDecider.REFINE, d.action)
        assertEquals(0.5348, d.score, 1e-4)
    }

    @Test
    fun `weights are tunable per game`() {
        val tuned = SpeechDecider(weights = mapOf("stability" to 2.0))
        val d = tuned.decide("Привет, как дела?", stability = 0, screenPos = 0.9, age = 0.2)
        assertEquals(SpeechDecider.REFINE, d.action)
        assertEquals(0.5208, d.score, 1e-4)
    }

    @Test
    fun `overlap is a jaccard-like share of the longer side`() {
        assertEquals(0.5, SpeechDecider.overlap("Привет мир", "мир и привет всем"), 1e-9)
        assertEquals(0.0, SpeechDecider.overlap("", "мир"), 1e-9)
    }

    @Test
    fun `features expose every weighted signal`() {
        val keys = decider.features("Привет, как дела?", stability = 3).keys
        assertEquals(SpeechDecider.DEFAULTS.keys, keys)
    }
}
