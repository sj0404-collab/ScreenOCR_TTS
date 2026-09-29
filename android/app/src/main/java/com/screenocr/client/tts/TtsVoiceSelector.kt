package com.screenocr.client.tts

import java.util.Locale

data class TtsVoice(
    val name: String,
    val locale: String,
    val requiresNetwork: Boolean,
)

/**
 * Выбор голоса по языку. Вынесено из Android-класса, потому что сам выбор —
 * чистая функция списка голосов и именно её стоит покрывать тестами.
 *
 * Идентификатор голоса в Android — его `name`: `Voice.getId()` в публичном SDK
 * нет вообще, а `TextToSpeech.setVoice()` принимает сам объект.
 */
object TtsVoiceSelector {

    fun select(voices: List<TtsVoice>, language: String): TtsVoice? {
        val target = language.lowercase(Locale.ROOT)
        return voices
            .filter { it.locale.substringBefore('-').lowercase(Locale.ROOT) == target }
            .sortedWith(
                compareBy<TtsVoice> { it.requiresNetwork }
                    .thenByDescending { it.locale.length }
                    .thenBy { it.name }
            )
            .firstOrNull()
    }

    fun localeTagFor(language: String): String =
        if (language == "ru") "ru-RU" else "en-US"
}
