package com.screenocr.client.tts

interface SpeechListener {
    fun onReady(available: Boolean)
    fun onStarted()
    fun onFinished()
    fun onFailed(message: String)
}

/**
 * Озвучка. Реализация скрыта за интерфейсом, чтобы позже подставить Piper или
 * onnxruntime, не трогая UI и пайплайн.
 */
interface SpeechEngine {
    val isReady: Boolean
    fun speak(text: String, language: String)
    fun stop()
    fun shutdown()
}
