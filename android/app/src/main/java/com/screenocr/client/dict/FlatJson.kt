package com.screenocr.client.dict

class JsonFormatException(message: String) : RuntimeException(message)

/**
 * Разбор плоского JSON-объекта вида {"ключ": "значение"}.
 *
 * Словари — это 68 155 пар строк; тянуть ради них JSON-библиотеку в APK
 * незачем, а `org.json` на старте Android парсит такой файл заметно медленнее
 * и недоступен в JVM-тестах (в android.jar он заглушен). Вложенные объекты и
 * массивы не поддерживаются: в словарях их нет, а поддержка была бы кодом без
 * применения.
 */
object FlatJson {

    fun parseObject(text: String): Map<String, String> {
        val reader = Reader(text)
        reader.skipWhitespace()
        reader.expect('{')
        val out = LinkedHashMap<String, String>()
        reader.skipWhitespace()
        if (reader.peek() == '}') {
            reader.read()
            reader.skipWhitespace()
            reader.requireEnd()
            return out
        }
        while (true) {
            reader.skipWhitespace()
            val key = reader.readString()
            reader.skipWhitespace()
            reader.expect(':')
            reader.skipWhitespace()
            out[key] = reader.readScalar()
            reader.skipWhitespace()
            when (reader.read()) {
                ',' -> Unit
                '}' -> break
                else -> throw JsonFormatException("ожидался ',' или '}' на позиции ${reader.pos - 1}")
            }
        }
        reader.skipWhitespace()
        reader.requireEnd()
        return out
    }

    private class Reader(private val text: String) {
        var pos = 0

        fun peek(): Char? = if (pos < text.length) text[pos] else null

        fun read(): Char {
            if (pos >= text.length) throw JsonFormatException("неожиданный конец файла")
            return text[pos++]
        }

        fun skipWhitespace() {
            while (pos < text.length && text[pos].isWhitespace()) pos++
        }

        fun expect(c: Char) {
            val actual = read()
            if (actual != c) throw JsonFormatException("ожидался '$c', получен '$actual' на позиции ${pos - 1}")
        }

        fun requireEnd() {
            if (pos != text.length) throw JsonFormatException("лишние данные после объекта на позиции $pos")
        }

        fun readString(): String {
            expect('"')
            val sb = StringBuilder()
            while (true) {
                when (val c = read()) {
                    '"' -> return sb.toString()
                    '\\' -> sb.append(readEscape())
                    else -> sb.append(c)
                }
            }
        }

        private fun readEscape(): Char = when (val c = read()) {
            '"', '\\', '/' -> c
            'b' -> '\b'
            'f' -> ''
            'n' -> '\n'
            'r' -> '\r'
            't' -> '\t'
            'u' -> {
                val hex = text.substring(pos, pos + 4)
                if (hex.length != 4 || hex.any { it.digitToIntOrNull(16) == null }) {
                    throw JsonFormatException("битый \\u-escape на позиции $pos")
                }
                pos += 4
                hex.toInt(16).toChar()
            }
            else -> throw JsonFormatException("неизвестный escape '\\$c' на позиции ${pos - 1}")
        }

        fun readScalar(): String {
            when (peek()) {
                '"' -> return readString()
                '{', '[' -> throw JsonFormatException("вложенные структуры не поддерживаются на позиции $pos")
                't' -> return literal("true", "true")
                'f' -> return literal("false", "false")
                'n' -> return literal("null", "")
                else -> {
                    val start = pos
                    while (pos < text.length && !text[pos].let { it == ',' || it == '}' || it.isWhitespace() }) pos++
                    val raw = text.substring(start, pos)
                    if (raw.isEmpty()) throw JsonFormatException("пустое значение на позиции $start")
                    return raw
                }
            }
        }

        private fun literal(word: String, value: String): String {
            if (!text.startsWith(word, pos)) throw JsonFormatException("ожидалось '$word' на позиции $pos")
            pos += word.length
            return value
        }
    }
}
