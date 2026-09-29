package com.screenocr.client.dict

import java.io.File
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

class FlatJsonTest {

    @Test
    fun `flat object parses`() {
        val parsed = FlatJson.parseObject("""{"a": "one", "b": "two"}""")
        assertEquals(2, parsed.size)
        assertEquals("one", parsed["a"])
        assertEquals("two", parsed["b"])
    }

    @Test
    fun `empty object parses`() {
        assertTrue(FlatJson.parseObject("{}").isEmpty())
        assertTrue(FlatJson.parseObject("  {  }  ").isEmpty())
    }

    @Test
    fun `escapes are decoded`() {
        val parsed = FlatJson.parseObject("""{"k": "строка \"кавычка\" \\ слэш \n таб\t жирный A Ж"}""")
        assertEquals("строка \"кавычка\" \\ слэш \n таб\t жирный A Ж", parsed["k"])
    }

    @Test
    fun `scalar values are accepted as text`() {
        val parsed = FlatJson.parseObject("""{"n": 12, "f": 1.50, "t": true, "z": null}""")
        assertEquals("12", parsed["n"])
        assertEquals("1.50", parsed["f"])
        assertEquals("true", parsed["t"])
        assertEquals("", parsed["z"])
    }

    @Test
    fun `empty value survives as empty string`() {
        val parsed = FlatJson.parseObject("""{"of": ""}""")
        assertEquals("", parsed["of"])
        assertEquals(1, parsed.size)
    }

    @Test
    fun `nested structures are rejected`() {
        for (bad in listOf("""{"a": {"b": 1}}""", """{"a": [1, 2]}""")) {
            try {
                FlatJson.parseObject(bad)
                fail("ожидалось JsonFormatException для $bad")
            } catch (expected: JsonFormatException) {
                assertTrue(expected.message!!.isNotEmpty())
            }
        }
    }

    @Test
    fun `malformed json is rejected`() {
        val bad = listOf(
            """{"a" "b"}""",
            """{"a": }""",
            """{"a": 1,}""",
            """{"a": "незакрытая}""",
            """{"a": 1} мусор""",
            """{"a": 1""",
            "не json",
        )
        for (text in bad) {
            try {
                FlatJson.parseObject(text)
                fail("ожидалось JsonFormatException для $text")
            } catch (expected: JsonFormatException) {
                assertTrue(expected.message!!.isNotEmpty())
            }
        }
    }

    @Test
    fun `real dictionary file parses completely`() {
        val file = TestDictionaries.dictFile("en_rus_full.json")
        val parsed = FlatJson.parseObject(file.readText())
        assertEquals(68155, parsed.size)
        assertEquals("ПРИВЕТ", parsed["hello"])
    }
}
