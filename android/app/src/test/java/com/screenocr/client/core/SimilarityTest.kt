package com.screenocr.client.core

import org.junit.Assert.assertEquals
import org.junit.Test

class SimilarityTest {

    @Test
    fun `identical strings are fully similar`() {
        assertEquals(1.0, Similarity.ratio("Привет", "Привет"), 1e-9)
    }

    @Test
    fun `empty input is not similar`() {
        assertEquals(0.0, Similarity.ratio("", "Привет"), 1e-9)
    }

    @Test
    fun `length mismatch beyond 30 percent is rejected outright`() {
        assertEquals(0.0, Similarity.ratio("abc", "abcdefghijkl"), 1e-9)
    }

    @Test
    fun `trailing punctuation barely changes score`() {
        assertEquals(0.909091, Similarity.ratio("Привет мир", "Привет мир!"), 1e-6)
    }

    @Test
    fun `short common prefix loses to the length guard`() {
        assertEquals(0.0, Similarity.ratio("Привет мир", "Пока"), 1e-9)
    }

    @Test
    fun `no common characters means no similarity`() {
        assertEquals(0.0, Similarity.ratio("abcd", "xyzw"), 1e-9)
    }

    @Test
    fun `is symmetric`() {
        assertEquals(
            Similarity.ratio("Привет мир", "Привет мир!"),
            Similarity.ratio("Привет мир!", "Привет мир"),
            1e-9,
        )
    }
}
