package com.screenocr.client.core

import com.screenocr.client.dict.OfflineTranslator

data class TranslationResult(
    val input: String,
    val cleaned: String,
    val spoken: String,
    val language: String,
    val sourceLanguage: String,
    val needsBetterTranslation: Boolean,
)

/**
 * Сквозной путь без Android: сырой текст → чистка → перевод → язык озвучки.
 *
 * Русский текст не переводится: `OfflineTranslator` словарь EN→RU, и пропуская
 * кириллицу через него, получаем мусор из пословного разбора. Python делает
 * то же самое через `_detect_text_language` в `live_scanner.py:664`.
 */
class TranslationPipeline(private val translator: OfflineTranslator) {

    fun process(raw: String): TranslationResult {
        val cleaned = TextCleaner.fullCleanPipeline(raw)
        val sourceLanguage = LanguageDetector.detect(cleaned)
        val spoken = if (sourceLanguage == LanguageDetector.RU) cleaned else translator.translate(cleaned)
        return TranslationResult(
            input = raw,
            cleaned = cleaned,
            spoken = spoken,
            language = LanguageDetector.detect(spoken.ifEmpty { cleaned }),
            sourceLanguage = sourceLanguage,
            needsBetterTranslation = sourceLanguage != LanguageDetector.RU &&
                cleaned.isNotEmpty() && translator.hasUntranslated(cleaned),
        )
    }
}
