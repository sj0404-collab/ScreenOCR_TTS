package com.screenocr.client.tts

import android.content.Context
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.speech.tts.Voice
import java.util.Locale
import java.util.concurrent.atomic.AtomicLong

class AndroidTtsEngine(
    context: Context,
    private val listener: SpeechListener,
) : SpeechEngine {

    private val appContext = context.applicationContext
    private var tts: TextToSpeech? = null
    private val utteranceCounter = AtomicLong()

    @Volatile
    private var ready = false

    @Volatile
    private var lastUtteranceId: String? = null

    override val isReady: Boolean get() = ready

    init {
        tts = TextToSpeech(appContext) { status ->
            val engine = tts
            if (status != TextToSpeech.SUCCESS || engine == null) {
                ready = false
                listener.onReady(false)
                return@TextToSpeech
            }
            ready = true
            engine.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
                override fun onStart(utteranceId: String?) = listener.onStarted()

                override fun onDone(utteranceId: String?) {
                    if (utteranceId == lastUtteranceId) listener.onFinished()
                }

                @Deprecated("Обязательный член API до API 33")
                override fun onError(utteranceId: String?) = listener.onFailed("Ошибка озвучки")

                override fun onError(utteranceId: String?, errorCode: Int) = listener.onFailed(errorCodeMessage(errorCode))
            })
            listener.onReady(true)
        }
    }

    override fun speak(text: String, language: String) {
        val engine = tts
        if (engine == null || !ready) {
            listener.onFailed("Синтезатор речи не готов")
            return
        }
        val parts = SpeechText.chunks(text)
        if (parts.isEmpty()) {
            listener.onFailed("Нет текста для озвучки")
            return
        }
        val available = tts?.voices.orEmpty()
        val selected = TtsVoiceSelector.select(available.map { it.toCandidate() }, language)
        val chosen = selected?.let { pick -> available.firstOrNull { it.name == pick.name } }
        if (chosen == null || engine.setVoice(chosen) != TextToSpeech.SUCCESS) {
            val tag = TtsVoiceSelector.localeTagFor(language)
            if (engine.setLanguage(Locale.forLanguageTag(tag)) != TextToSpeech.SUCCESS) {
                listener.onFailed("Синтезатор не поддерживает язык $tag")
                return
            }
        }
        parts.forEachIndexed { index, part ->
            val id = "screenocr-${utteranceCounter.incrementAndGet()}"
            if (index == parts.lastIndex) lastUtteranceId = id
            val mode = if (index == 0) TextToSpeech.QUEUE_FLUSH else TextToSpeech.QUEUE_ADD
            engine.speak(part, mode, null, id)
        }
    }

    override fun stop() {
        tts?.stop()
    }

    override fun shutdown() {
        ready = false
        tts?.stop()
        tts?.shutdown()
        tts = null
    }

    private fun Voice.toCandidate() = TtsVoice(
        name = name,
        locale = locale.toLanguageTag(),
        requiresNetwork = isNetworkConnectionRequired,
    )

    private fun errorCodeMessage(code: Int): String = when (code) {
        TextToSpeech.ERROR_NETWORK, TextToSpeech.ERROR_NETWORK_TIMEOUT -> "Голос недоступен по сети"
        TextToSpeech.ERROR_SERVICE -> "Служба синтеза речи недоступна"
        TextToSpeech.ERROR_NOT_INSTALLED_YET -> "Данные синтезатора не установлены"
        TextToSpeech.ERROR_SYNTHESIS, TextToSpeech.ERROR_OUTPUT -> "Синтезатор не смог произнести текст"
        TextToSpeech.ERROR_INVALID_REQUEST -> "Недопустимый запрос к синтезатору"
        else -> "Ошибка синтеза речи: $code"
    }
}
