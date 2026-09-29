package com.screenocr.client.tts

/**
 * Подготовка текста к озвучке: склейка пробелов и нарезка на куски.
 *
 * Android-движок TTS режет длинные фразы сам и на границах слов, из-за чего
 * озвучка длинного текста звучит с запинками. Поэтому режем заранее — по
 * границам предложений, а если предложение длиннее лимита, то по пробелам.
 */
object SpeechText {

    const val MAX_CHUNK = 300

    private val WHITESPACE_RE = Regex("\\s+")
    private val SENTENCE_END_RE = Regex("(?<=[.!?…])\\s+")

    fun chunks(text: String): List<String> {
        val flat = text.replace(WHITESPACE_RE, " ").trim()
        if (flat.isEmpty()) return emptyList()
        if (flat.length <= MAX_CHUNK) return listOf(flat)

        val out = mutableListOf<String>()
        val current = StringBuilder()
        for (sentence in SENTENCE_END_RE.split(flat)) {
            if (sentence.isEmpty()) continue
            if (current.isNotEmpty() && current.length + 1 + sentence.length > MAX_CHUNK) {
                out.add(current.toString())
                current.setLength(0)
            }
            if (sentence.length > MAX_CHUNK) {
                if (current.isNotEmpty()) {
                    out.add(current.toString())
                    current.setLength(0)
                }
                out.addAll(splitLong(sentence))
            } else {
                if (current.isNotEmpty()) current.append(' ')
                current.append(sentence)
            }
        }
        if (current.isNotEmpty()) out.add(current.toString())
        return out
    }

    private fun splitLong(sentence: String): List<String> {
        val parts = mutableListOf<String>()
        val current = StringBuilder()
        for (word in sentence.split(' ')) {
            if (current.isNotEmpty() && current.length + 1 + word.length > MAX_CHUNK) {
                parts.add(current.toString())
                current.setLength(0)
            }
            if (current.isNotEmpty()) current.append(' ')
            current.append(word)
        }
        if (current.isNotEmpty()) parts.add(current.toString())
        return parts
    }
}
