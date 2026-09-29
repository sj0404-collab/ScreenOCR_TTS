package com.screenocr.client.core

object TextCleaner {

    private val LAT_TO_CYR = mapOf(
        'a' to 'а', 'c' to 'с', 'e' to 'е', 'o' to 'о', 'p' to 'р', 'x' to 'х', 'y' to 'у',
        'A' to 'А', 'B' to 'В', 'C' to 'С', 'E' to 'Е', 'H' to 'Н', 'K' to 'К', 'M' to 'М',
        'O' to 'О', 'P' to 'Р', 'T' to 'Т', 'X' to 'Х', 'Y' to 'У', 'U' to 'У', 'u' to 'у',
        'i' to 'і', 'I' to 'І',
    )

    private val CYR_TO_LAT = mapOf(
        'а' to 'a', 'с' to 'c', 'е' to 'e', 'о' to 'o', 'р' to 'p', 'х' to 'x', 'у' to 'y',
        'А' to 'A', 'В' to 'B', 'С' to 'C', 'Е' to 'E', 'Н' to 'H', 'К' to 'K', 'М' to 'M',
        'О' to 'O', 'Р' to 'P', 'Т' to 'T', 'Х' to 'X', 'У' to 'Y',
        'і' to 'i', 'І' to 'I', 'ї' to 'i', 'Ї' to 'I', 'є' to 'e', 'Є' to 'E',
        'ґ' to 'g', 'Ґ' to 'G', 'ё' to 'e', 'Ё' to 'E',
    )

    private val UKR_TO_RUS = mapOf(
        'І' to 'И', 'і' to 'и', 'Ї' to 'Ы', 'ї' to 'ы', 'Є' to 'Е', 'є' to 'е',
    )

    private const val CYR_UNIQUE = "абвгдежзийклмнопстуфчшщъыьэюяёЁ"
    private const val LAT_UNIQUE = "BbDdFfGgJjLlNnQqRrSsWwZz"
    private const val PUNCT = ".,!?:;»”’'\"…—–-"
    private const val GARBAGE_WHITELIST = ".,!?;:-'\"()—–…/ \n"

    private val KNOWN_CORRECTIONS = mapOf(
        "curl" to "Ctrl", "ctri" to "Ctrl", "cirl" to "Ctrl",
        "alf" to "Alt", "shifl" to "Shift", "shlft" to "Shift",
        "enler" to "Enter", "esc" to "Esc", "dei" to "Del",
    )

    val KNOWN_WORDS: Set<String> = setOf(
        "и", "в", "на", "не", "что", "это", "как", "он", "она", "оно", "они",
        "поиск", "клонирование",
        "мы", "вы", "я", "ты", "его", "её", "их", "нас", "вас", "мне", "тебе",
        "ему", "ей", "нам", "вам", "им", "меня", "тебя", "него", "неё", "них",
        "был", "была", "было", "были", "будет", "будут", "быть", "бы",
        "мог", "могла", "могло", "могли", "можно", "нужно", "надо",
        "есть", "нет", "да", "или", "а", "но", "если", "то", "тоже", "так",
        "уже", "ещё", "еще", "вот", "тут", "там", "где", "когда", "пока",
        "всё", "все", "кто", "чем", "чём", "всего", "всем", "этот", "эта",
        "эти", "тот", "та", "те", "такой", "такая", "такие", "какой", "какая",
        "один", "два", "три", "четыре", "пять", "шесть", "семь", "восемь",
        "девять", "десять", "после", "перед", "между", "через", "без", "для",
        "из", "от", "до", "под", "над", "при", "по", "за", "про", "ante",
        "большой", "маленький", "новый", "старый", "хороший", "плохой",
        "говорит", "сказал", "сказала", "думает", "знает", "видит", "идёт",
        "идет", "стоит", "лежит", "сидит", "летит", "бежит", "работает",
        "время", "день", "ночь", "утро", "вечер", "год", "месяц", "неделя",
        "рука", "нога", "глаз", "голова", "лицо", "тело", "сердце",
        "дом", "квартира", "комната", "окно", "дверь", "стена", "пол",
        "вода", "огонь", "земля", "небо", "ветер", "солнце", "луна",
        "жизнь", "смерть", "любовь", "ненависть", "радость", "горе",
        "сила", "слабость", "ум", "душа", "разум", "волшебство", "магия",
        "战争", "мир", "победа", "поражение", "битва", "бой", "сражение",
        "the", "and", "for", "are", "but", "not", "you", "all", "can", "had",
        "her", "was", "one", "our", "out", "day", "get", "has", "him", "his",
        "how", "its", "may", "new", "now", "old", "see", "way", "who", "did",
        "got", "let", "say", "too", "use", "that", "with", "have", "this",
        "will", "your", "from", "they", "been", "said", "each", "make",
        "like", "long", "look", "many", "most", "over", "such", "take",
        "than", "them", "then", "what", "when", "more", "some", "time",
        "very", "just", "know", "also", "back", "only", "come", "good",
        "give", "first", "life", "world", "still", "find", "here", "thing",
        "many", "well", "tell", "than", "that", "this", "with", "have",
        "about", "could", "would", "should", "there", "their", "which",
        "people", "think", "really", "something", "nothing", "everything",
    )

    private val RUN_RE = Regex("[A-Za-z]+|[\\u0400-\\u052F\\uA640-\\uA69F]+|[^A-Za-z\\u0400-\\u052F\\uA640-\\uA69F]+")
    private val WORD_RE = Regex("[A-Za-z\\u0400-\\u052F\\uA640-\\uA69F]{2,}")
    private val HYPHEN_RE = Regex("(?U)(\\w)-\\s*\n\\s*(\\w)")
    private val WHITESPACE_RE = Regex("\\s+")
    private val CYR_RE = Regex("[а-яА-ЯёЁ]")
    private val LAT_RE = Regex("[a-zA-Z]")
    private val LAT_WORD_RE = Regex("[A-Za-z]+")
    private val CYR_ONLY_RE = Regex("^[а-яА-ЯёЁ]+$")
    private val LAT_ONLY_RE = Regex("^[a-zA-Z]+$")
    private val RAMP_SPLIT_RE = Regex("[\\s,.\\-!?;:\"'()\\[\\]{}<>+/=…—–%№]+")
    private val NUMBER_RE = Regex("\\b\\d+[.,]\\d*0+\\b")

    private fun isCyr(c: Char): Boolean = c.code in 0x0400..0x052F || c.code in 0xA640..0xA69F
    private fun isLat(c: Char): Boolean = c in 'a'..'z' || c in 'A'..'Z'
    private fun isLatRun(s: String): Boolean = s.isNotEmpty() && s.all { isLat(it) }
    private fun isCyrRun(s: String): Boolean = s.isNotEmpty() && s.all { isCyr(it) }

    private fun String.isUpperCaseAll(): Boolean {
        val cased = filter { it.isLetter() }
        return cased.isNotEmpty() && cased.all { it.isUpperCase() }
    }

    private fun splitWords(s: String): List<String> = s.split(WHITESPACE_RE).filter { it.isNotEmpty() }

    fun fixLookalikesPerWord(text: String): String {
        if (text.isEmpty()) return text
        return WORD_RE.replace(text) { fixWord(it.value) }
    }

    private fun fixWord(word: String): String {
        val runs = RUN_RE.findAll(word).map { it.value }.toList()
        val letterRuns = runs.filter { isLatRun(it) || isCyrRun(it) }
        val mixedBoundary = letterRuns.size >= 2 && letterRuns.zipWithNext().any { (a, b) ->
            a.length >= 2 && b.length >= 2 &&
                ((isCyrRun(a) && isLatRun(b)) || (isLatRun(a) && isCyrRun(b)))
        }
        if (mixedBoundary) {
            val mapped = runs.map { r ->
                if (isCyrRun(r) && r.length >= 2) r.map { UKR_TO_RUS[it] ?: it }.joinToString("") else r
            }
            val res = mutableListOf<String>()
            var prevLetters = ""
            for (chunk in mapped) {
                val needsSpace = res.isNotEmpty() && chunk.length >= 2 &&
                    ((isLatRun(chunk) && isCyrRun(prevLetters) && prevLetters.length >= 2) ||
                        (isCyrRun(chunk) && isLatRun(prevLetters) && prevLetters.length >= 2))
                if (needsSpace) res.add(" ")
                res.add(chunk)
                if (isLatRun(chunk) || isCyrRun(chunk)) prevLetters = chunk
                else if (chunk.isBlank()) prevLetters = ""
            }
            return res.joinToString("")
        }

        val cyrUnique = word.count { it in CYR_UNIQUE }
        val latUnique = word.count { it in LAT_UNIQUE }

        if (cyrUnique == 0 && latUnique == 0) {
            val cyrAll = word.count { isCyr(it) }
            val latAll = word.count { isLat(it) }
            return when {
                cyrAll > latAll -> {
                    val lat2cyr = word.map { LAT_TO_CYR[it] ?: it }.joinToString("")
                    lat2cyr.map { UKR_TO_RUS[it] ?: it }.joinToString("")
                }
                latAll > cyrAll -> word.map { CYR_TO_LAT[it] ?: it }.joinToString("")
                else -> word
            }
        }

        return when {
            cyrUnique > latUnique -> {
                val lat2cyr = word.map { LAT_TO_CYR[it] ?: it }.joinToString("")
                lat2cyr.map { UKR_TO_RUS[it] ?: it }.joinToString("")
            }
            latUnique > cyrUnique -> word.map { CYR_TO_LAT[it] ?: it }.joinToString("")
            else -> word
        }
    }

    fun joinLineHyphens(text: String): String = HYPHEN_RE.replace(text, "$1$2")

    fun filterGarbageTokens(text: String): String {
        if (text.isEmpty()) return text
        val result = mutableListOf<String>()
        for (line in text.split("\n")) {
            val cleaned = splitWords(line).filter { w ->
                CYR_RE.containsMatchIn(w) || LAT_RE.containsMatchIn(w) ||
                    w.all { it in GARBAGE_WHITELIST || it.isDigit() }
            }
            if (cleaned.isNotEmpty()) result.add(cleaned.joinToString(" "))
        }
        return result.joinToString("\n")
    }

    fun looksLikeDictionaryRamp(text: String): Boolean {
        if (text.length < 10) return false
        for (word in RAMP_SPLIT_RE.split(text)) {
            if (word.length < 8) continue
            var ascending = 1
            for (i in 1 until word.length) {
                if (word[i] > word[i - 1]) {
                    ascending++
                    if (ascending >= 10) return true
                } else {
                    ascending = 1
                }
            }
        }
        return false
    }

    fun normalizeWhitespace(text: String): String {
        if (text.isEmpty()) return text
        var t = Regex("[ \\t]+").replace(text, " ")
        t = Regex(" *\n *").replace(t, "\n")
        t = Regex("\\.{4,}").replace(t, "…")
        t = Regex("\\.\\.\\.").replace(t, "…")
        t = Regex("\\s+([,.!?;:])").replace(t, "$1")
        t = Regex("([(\\[«])\\s+").replace(t, "$1")
        return t.split("\n").joinToString("\n") { it.trim() }
    }

    fun normalizeNumbers(text: String): String = NUMBER_RE.replace(text) { m ->
        val s = m.value
        val sep = if (',' in s) ',' else '.'
        val idx = s.indexOf(sep)
        val integer = s.substring(0, idx)
        val frac = s.substring(idx + 1).trimEnd('0')
        if (frac.isEmpty()) integer else integer + sep + frac
    }

    fun cyrillicFitness(text: String): Double {
        if (text.isEmpty()) return 0.0
        val alpha = text.filter { it.isLetter() }
        if (alpha.isEmpty()) return 0.0
        val cyr = alpha.count { it.lowercaseChar() in 'а'..'я' || it.lowercaseChar() == 'ё' }
        return cyr.toDouble() / alpha.length
    }

    fun latinFitness(text: String): Double {
        if (text.isEmpty()) return 0.0
        val alpha = text.filter { it.isLetter() }
        if (alpha.isEmpty()) return 0.0
        return alpha.count { isLat(it) }.toDouble() / alpha.length
    }

    fun reattachLeadingPunct(text: String): String {
        val out = mutableListOf<String>()
        for (tok in text.split(" ")) {
            var i = 0
            while (i < tok.length && tok[i] in PUNCT) i++
            val last = out.lastOrNull()
            if (i > 0 && i < tok.length && !last.isNullOrEmpty() &&
                last.last() !in PUNCT && last.last().isLetterOrDigit()
            ) {
                val rest = tok.substring(i)
                out[out.lastIndex] = last + tok.substring(0, i)
                if (rest.isNotEmpty()) out.add(rest)
            } else {
                out.add(tok)
            }
        }
        return out.filter { it.isNotEmpty() }.joinToString(" ")
    }

    fun isAcceptableCyrillicText(text: String): Boolean {
        if (text.isEmpty()) return false
        for (w in splitWords(text)) {
            val clean = w.trim { it in ".,!?;:—–-" }
            if (clean.isEmpty()) continue
            if (CYR_ONLY_RE.matches(clean) || LAT_ONLY_RE.matches(clean)) continue
            if (CYR_RE.containsMatchIn(clean) && LAT_RE.containsMatchIn(clean)) continue
            return false
        }
        return true
    }

    fun applyKnownCorrections(text: String): String = LAT_WORD_RE.replace(text) { m ->
        KNOWN_CORRECTIONS[m.value.lowercase()] ?: m.value
    }

    fun restoreKnownWords(text: String): String {
        if (text.isEmpty()) return text
        val result = mutableListOf<String>()
        for (word in splitWords(text)) {
            if (word.length < 6 || !CYR_ONLY_RE.matches(word)) {
                result.add(word)
                continue
            }
            val low = word.lowercase()
            if (low in KNOWN_WORDS) {
                result.add(word)
                continue
            }
            val split = dpSplitOnKnown(low)
            if (split != null && split.size > 1 && split.all { it in KNOWN_WORDS }) {
                val restored = mutableListOf<String>()
                var idx = 0
                for (part in split) {
                    val slice = word.substring(idx, idx + part.length)
                    restored.add(
                        when {
                            slice.isUpperCaseAll() -> part.uppercase()
                            slice.first().isUpperCase() -> part.replaceFirstChar { it.uppercaseChar() }
                            else -> part
                        }
                    )
                    idx += part.length
                }
                result.add(restored.joinToString(" "))
            } else {
                result.add(word)
            }
        }
        return result.joinToString(" ")
    }

    fun dpSplitOnKnown(word: String, maxWordLen: Int = 20): List<String>? {
        val n = word.length
        if (n == 0) return emptyList()
        if (n > 200) return null
        val dp = arrayOfNulls<List<String>>(n + 1)
        dp[0] = emptyList()
        for (i in 1..n) {
            for (length in 1..minOf(maxWordLen, i)) {
                val prev = dp[i - length] ?: continue
                val candidate = word.substring(i - length, i)
                if (candidate in KNOWN_WORDS) {
                    val newList = prev + candidate
                    val cur = dp[i]
                    if (cur == null || newList.size < cur.size) dp[i] = newList
                }
            }
        }
        return dp[n]
    }

    fun normalizeLocalCyrillicCaption(
        text: String,
        fixVisualConfusions: ((String) -> String)? = null,
    ): String {
        if (text.isEmpty()) return text
        var t = joinLineHyphens(text)
        t = fixLookalikesPerWord(t)
        t = restoreKnownWords(t)
        if (fixVisualConfusions != null) t = fixVisualConfusions(t)
        return reattachLeadingPunct(t)
    }

    fun fullCleanPipeline(
        text: String,
        engineType: String = "tflite_cyrillic",
        onDictionaryRamp: (String) -> Unit = {},
    ): String {
        if (text.isEmpty()) return text
        var t = normalizeWhitespace(text)
        t = fixLookalikesPerWord(t)
        t = joinLineHyphens(t)
        if (engineType in CYRILLIC_ENGINES) t = restoreKnownWords(t)
        t = filterGarbageTokens(t)
        t = applyKnownCorrections(t)
        t = normalizeNumbers(t)
        t = normalizeWhitespace(t)
        if (looksLikeDictionaryRamp(t)) onDictionaryRamp(t)
        return t
    }

    private val CYRILLIC_ENGINES = setOf("tflite_cyrillic", "cyrillic_onnx", "easyocr")
}
