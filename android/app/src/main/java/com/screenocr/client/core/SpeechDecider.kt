package com.screenocr.client.core

import java.util.Locale
import kotlin.math.min

data class SpeechDecision(val action: String, val score: Double, val reason: String)

class SpeechDecider(
    weights: Map<String, Double> = emptyMap(),
    private val speakAt: Double = 0.55,
    private val refineAt: Double = 0.30,
) {

    private val w: Map<String, Double> = DEFAULTS + weights

    fun features(
        text: String,
        hasVoice: Boolean = false,
        voiceText: String = "",
        stability: Int = 0,
        screenPos: Double = 1.0,
        age: Double = 0.0,
        isEcho: Boolean = false,
    ): Map<String, Double> {
        val t = text.trim()
        val words = WORD_RE.findAll(t).map { it.value }.toList()
        val n = words.size
        val cjkChars = CJK_RE.findAll(t).count()
        val effWords = n + if (cjkChars > 0) cjkChars / 2 else 0
        val letters = t.count { it.isLetter() }
        val digits = t.count { it.isDigit() }
        val marks = t.count { it in ".?!…" }
        val short = words.count { it.length <= 2 }
        val shortRatio = if (n > 0) short.toDouble() / n else 1.0
        val mixed = words.count { w -> w.any { it.isDigit() } }
        val gibberish = when {
            cjkChars >= 2 -> 1.0
            effWords >= 2 && letters >= 3 && shortRatio <= 0.5 && mixed == 0 &&
                digits <= maxOf(1, letters / 4) -> 1.0
            else -> 0.0
        }
        return linkedMapOf(
            "words" to if (effWords >= 1) 1.0 else 0.0,
            "length" to min(1.0, effWords / 6.0),
            "dialog_marks" to if (marks > 0) 1.0 else 0.0,
            "sentence_start" to if (t.firstOrNull()?.isUpperCase() == true || cjkChars > 0) 1.0 else 0.0,
            "screen_pos" to screenPos.coerceIn(0.0, 1.0),
            "stability" to (maxOf(0, stability) / 3.0).coerceAtMost(1.0),
            "freshness" to (1.0 - age / 5.0).coerceAtLeast(0.0),
            "not_gibberish" to gibberish,
            "has_voice" to if (hasVoice) 1.0 else 0.0,
            "voice_text" to if (isMeaningful(voiceText)) 1.0 else 0.0,
            "overlap_voice" to overlap(voiceText, t),
            "not_echo" to if (isEcho) 0.0 else 1.0,
        )
    }

    fun decide(
        text: String,
        hasVoice: Boolean = false,
        voiceText: String = "",
        stability: Int = 0,
        screenPos: Double = 1.0,
        age: Double = 0.0,
        isEcho: Boolean = false,
    ): SpeechDecision {
        val t = text.trim()
        if (isEcho) return SpeechDecision(SKIP, 0.0, "жёсткое правило: это наш собственный голос")
        if (t.isEmpty()) return SpeechDecision(SKIP, 0.0, "пустой текст")

        val f = features(t, hasVoice, voiceText, stability, screenPos, age, isEcho)
        var total = 0.0
        var weight = 0.0
        val pos = mutableListOf<String>()
        val neg = mutableListOf<String>()
        for ((k, v) in f) {
            val weightOf = w[k] ?: 0.0
            if (weightOf <= 0.0) continue
            weight += weightOf
            total += weightOf * v
            if (v >= 0.5) pos.add("$k+${fmt(weightOf * v)}") else neg.add("$k-${fmt(weightOf * (1.0 - v))}")
        }

        val score = (if (weight > 0.0) total / weight else 0.0).coerceIn(0.0, 1.0)
        val action = when {
            score >= speakAt -> SPEAK
            score >= refineAt -> REFINE
            else -> SKIP
        }
        val reason = "score=${fmt(score)} [порог ${fmt(speakAt)}/${fmt(refineAt)}] " +
            "за: ${pos.take(4).joinToString(" ").ifEmpty { "-" }} " +
            "против: ${neg.take(4).joinToString(" ").ifEmpty { "-" }}"
        return SpeechDecision(action, score, reason)
    }

    private fun isMeaningful(voiceText: String): Boolean =
        WORD_RE.findAll(voiceText).count() >= 2 || CJK_RE.findAll(voiceText).count() >= 2

    companion object {
        const val SPEAK = "speak"
        const val REFINE = "refine"
        const val SKIP = "skip"

        private val WORD_RE = Regex("(?U)[^\\W\\d_]+")
        private val CJK_RE = Regex("[\\u3040-\\u30ff\\u3400-\\u9fff\\uac00-\\ud7af]")

        val DEFAULTS: Map<String, Double> = mapOf(
            "words" to 1.0,
            "length" to 1.0,
            "dialog_marks" to 1.2,
            "sentence_start" to 0.5,
            "screen_pos" to 1.0,
            "stability" to 1.6,
            "freshness" to 0.8,
            "not_gibberish" to 1.6,
            "has_voice" to 2.0,
            "voice_text" to 1.2,
            "overlap_voice" to 1.5,
            "not_echo" to 1.5,
        )

        fun overlap(a: String, b: String): Double {
            val wa = WORD_RE.findAll(a).map { it.value.lowercase() }.toSet()
            val wb = WORD_RE.findAll(b).map { it.value.lowercase() }.toSet()
            if (wa.isEmpty() || wb.isEmpty()) return 0.0
            return wa.intersect(wb).size.toDouble() / maxOf(wa.size, wb.size)
        }

        private fun fmt(v: Double): String = String.format(Locale.US, "%.2f", v)
    }
}
