package com.screenocr.client.dict

import java.io.File
import java.io.InputStream

class DictSet(val words: Map<String, String>, val phrases: Map<String, String>)

/**
 * Сборка словарей. Порядок загрузки повторяет `OfflineTranslator.__init__`:
 * литералы первыми, JSON дописывается поверх, первое значение выигрывает.
 *
 * `opener` абстрагирует источник: `context.assets.open` на устройстве,
 * `File(...).inputStream()` в тестах.
 */
object DictionaryLoader {

    private const val WORDS_FILE = "en_rus_full.json"
    private const val CITIES_FILE = "cities_dict.json"
    private const val GAMES_FILE = "game_names.json"

    fun load(opener: (String) -> InputStream): DictSet {
        val words = LinkedHashMap(DictData.EXTRA_WORDS)
        putAllMissing(words, WORDS_FILE, opener)
        val phrases = LinkedHashMap(DictData.PHRASES)
        putAllMissing(phrases, CITIES_FILE, opener)
        putAllMissing(phrases, GAMES_FILE, opener)
        return DictSet(words, phrases)
    }

    fun loadFromDir(dir: File): DictSet = load { File(dir, it).inputStream() }

    private fun putAllMissing(target: MutableMap<String, String>, name: String, opener: (String) -> InputStream) {
        val text = opener(name).use { it.readBytes().toString(Charsets.UTF_8) }
        for ((key, value) in FlatJson.parseObject(text)) {
            if (key !in target) target[key] = value
        }
    }
}
