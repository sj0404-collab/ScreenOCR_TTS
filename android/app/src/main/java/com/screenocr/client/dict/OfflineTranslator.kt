package com.screenocr.client.dict

import java.util.Locale

/**
 * Офлайн-перевод EN→RU по словарю, порт `OfflineTranslator` из `offline_dict.py`.
 *
 * Отличия от Python, оба намеренные:
 *
 * - Фразовый проход сделан поиском самого длинного совпадения по хешу вместо
 *   1447 `re.search`/`re.sub` на вызов. Побочный эффект: при перекрывающихся
 *   фразах, когда более длинная начинается правее, выигрывает более ранняя
 *   (в Python — более длинная). На игровых текстах не проявляется.
 * - Переводы с пустым значением отбрасываются. В Python `"of": ""` даёт
 *   двойной пробел в результате.
 */
class OfflineTranslator(
    words: Map<String, String> = DictData.EXTRA_WORDS,
    phrases: Map<String, String> = DictData.PHRASES,
    private val contractions: List<Pair<String, String>> = DictData.CONTRACTIONS,
) {

    private val words: Map<String, String> = words
    private val phrases: Map<String, String> = phrases
    private val maxPhraseLen: Int = phrases.keys.maxOfOrNull { it.length } ?: 0

    fun translate(text: String): String {
        if (text.isEmpty()) return ""
        var t = text.lowercase(Locale.ROOT).trim()
        for ((from, to) in contractions) t = t.replace(from, to)
        phrases[t]?.let { return it }

        val result = mutableListOf<String>()
        var i = 0
        while (i < t.length) {
            val phrase = matchPhrase(t, i)
            if (phrase != null) {
                result.add(phrases.getValue(phrase))
                i += phrase.length
            } else {
                val end = wordEnd(t, i)
                addWord(result, t.substring(i, end))
                i = end
            }
            while (i < t.length && t[i].isWhitespace()) i++
        }
        return result.filter { it.isNotEmpty() }.joinToString(" ")
    }

    fun hasUntranslated(text: String): Boolean {
        val r = translate(text)
        if (r.isEmpty()) return true
        if (r.split(' ').any { it.length > 2 && it.all { c -> c.code < 128 } && it.all { c -> c.isLetter() } }) {
            return true
        }
        val sourceWords = splitWords(text.lowercase(Locale.ROOT)).toSet()
        val resultWords = splitWords(r.lowercase(Locale.ROOT)).toSet()
        if (sourceWords.intersect(resultWords).size > sourceWords.size * 0.5) return true
        return splitWords(r).size < splitWords(text).size * 0.4
    }

    private fun addWord(out: MutableList<String>, token: String) {
        val bare = NON_WORD_RE.replace(token, "")
        if (bare.isEmpty()) return
        if (bare in ARTICLES) return
        out.add(words[bare] ?: bare)
    }

    private fun matchPhrase(text: String, start: Int): String? {
        if (maxPhraseLen == 0) return null
        if (start > 0 && isWordChar(text[start - 1])) return null
        if (!isWordChar(text[start])) return null
        val limit = minOf(maxPhraseLen, text.length - start)
        for (len in limit downTo 1) {
            val end = start + len
            if (end < text.length && isWordChar(text[end])) continue
            val candidate = text.substring(start, end)
            if (candidate in phrases) return candidate
        }
        return null
    }

    private fun wordEnd(text: String, start: Int): Int {
        var end = start
        while (end < text.length && !text[end].isWhitespace()) end++
        return end
    }

    private fun splitWords(text: String): List<String> = text.split(WHITESPACE_RE).filter { it.isNotEmpty() }

    private fun isWordChar(c: Char): Boolean = c == '_' || c.isLetterOrDigit()

    companion object {
        private val ARTICLES = setOf("the", "a", "an")
        private val WHITESPACE_RE = Regex("\\s+")
        private val NON_WORD_RE = Regex("(?U)[^\\w]")
    }
}
