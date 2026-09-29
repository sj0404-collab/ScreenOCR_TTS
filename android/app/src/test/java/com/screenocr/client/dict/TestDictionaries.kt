package com.screenocr.client.dict

import java.io.File

/**
 * Доступ к словарям репозитория из JVM-тестов.
 *
 * Путь задаёт `android/app/build.gradle.kts` через system property
 * `screenocr.repoRoot`. Тесты вне Gradle (из IDE) свойство не увидят — об этом
 * сообщаем явно, а не пропускаем проверку молча.
 */
object TestDictionaries {

    private const val PROPERTY = "screenocr.repoRoot"

    val repoRoot: File by lazy {
        val value = System.getProperty(PROPERTY)
            ?: error("Не задан system property $PROPERTY. Запускай тесты через ./gradlew testDebugUnitTest.")
        File(value)
    }

    val dicts: DictSet by lazy { DictionaryLoader.loadFromDir(repoRoot) }

    val fullTranslator: OfflineTranslator by lazy { OfflineTranslator(dicts.words, dicts.phrases) }

    fun dictFile(name: String): File = File(repoRoot, name)
}
