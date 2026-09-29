package com.screenocr.client.ui

import android.content.Context
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import com.screenocr.client.core.TranslationPipeline
import com.screenocr.client.core.TranslationResult
import com.screenocr.client.dict.DictionaryLoader
import com.screenocr.client.dict.OfflineTranslator
import com.screenocr.client.tts.AndroidTtsEngine
import com.screenocr.client.tts.SpeechEngine
import com.screenocr.client.tts.SpeechListener
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

enum class DictStatus { Loading, Ready, Failed }

class TranslatorState(context: Context) : SpeechListener {

    private val appContext = context.applicationContext
    private var pipeline: TranslationPipeline? = null

    val engine: SpeechEngine = AndroidTtsEngine(appContext, this)

    var input by mutableStateOf("")
        private set

    var dictStatus by mutableStateOf(DictStatus.Loading)
        private set

    var result by mutableStateOf<TranslationResult?>(null)
        private set

    var ttsStatus by mutableStateOf("Голос не инициализирован")
        private set

    var ttsReady by mutableStateOf(false)
        private set

    var speaking by mutableStateOf(false)
        private set

    val canSpeak: Boolean get() = dictStatus == DictStatus.Ready && ttsReady

    fun onInputChange(value: String) {
        input = value
    }

    suspend fun loadDictionaries() {
        dictStatus = DictStatus.Loading
        runCatching {
            withContext(Dispatchers.IO) { DictionaryLoader.load { appContext.assets.open(it) } }
        }.onSuccess { dicts ->
            pipeline = TranslationPipeline(OfflineTranslator(dicts.words, dicts.phrases))
            dictStatus = DictStatus.Ready
        }.onFailure { error ->
            dictStatus = DictStatus.Failed
            ttsStatus = "Словари не загрузились: ${error.message}"
        }
    }

    fun translate(): TranslationResult? {
        val active = pipeline
        if (active == null) {
            ttsStatus = "Словари ещё загружаются"
            return null
        }
        val computed = active.process(input)
        result = computed
        if (computed.spoken.isEmpty()) {
            ttsStatus = "После очистки и перевода не осталось текста"
        }
        return computed
    }

    fun speak() {
        val computed = result ?: translate() ?: return
        if (computed.spoken.isBlank()) {
            ttsStatus = "Нечего озвучивать"
            return
        }
        speaking = true
        engine.speak(computed.spoken, computed.language)
    }

    fun stop() {
        engine.stop()
        speaking = false
    }

    override fun onReady(available: Boolean) {
        ttsReady = available
        ttsStatus = if (available) "Синтезатор готов" else "На устройстве нет синтезатора речи"
    }

    override fun onStarted() {
        speaking = true
    }

    override fun onFinished() {
        speaking = false
    }

    override fun onFailed(message: String) {
        speaking = false
        ttsStatus = message
    }
}
