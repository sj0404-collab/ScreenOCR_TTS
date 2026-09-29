package com.screenocr.client.core

object LanguageDetector {

    const val RU = "ru"
    const val EN = "en"

    private const val CYRILLIC_THRESHOLD = 0.3

    fun detect(text: String): String {
        if (text.isEmpty()) return EN
        val cyrillic = text.count { it.code in 0x0400..0x04FF }
        val total = text.count { it.isLetter() }
        if (total == 0) return EN
        return if (cyrillic.toDouble() / total > CYRILLIC_THRESHOLD) RU else EN
    }
}
