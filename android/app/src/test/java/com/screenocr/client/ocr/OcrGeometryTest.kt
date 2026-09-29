package com.screenocr.client.ocr

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.math.abs

class OcrGeometryTest {

    private fun solid(width: Int, height: Int, pixel: Pixel): OcrImage =
        OcrImage.of(width, height) { _, _ -> pixel }

    @Test
    fun `gray uses the same luma weights as the splitter`() {
        assertEquals(255, Pixel(255, 255, 255).gray())
        assertEquals(0, Pixel(0, 0, 0).gray())
        val red = Pixel(255, 0, 0).gray()
        assertTrue("ожидалось ~77, получено $red", abs(red - 77) <= 1)
    }

    @Test
    fun `high contrast boosts and offsets luminance`() {
        val white = Pixel(255, 255, 255).highContrast()
        assertEquals(255, white.r)
        val black = Pixel(0, 0, 0).highContrast()
        assertEquals(0, black.r)
        val mid = Pixel(100, 100, 100).highContrast().r
        assertEquals(105, mid)
    }

    @Test
    fun `crop clamps to the image and keeps pixel order`() {
        val image = OcrImage.of(4, 4) { x, y -> Pixel(x * 10, y * 10, 0) }
        val crop = image.crop(2, 1, 10, 3)
        assertEquals(2, crop.width)
        assertEquals(2, crop.height)
        assertEquals(20, crop.at(0, 0).r)
        assertEquals(21, crop.at(0, 1).r)
    }

    @Test
    fun `otsu separates light background from dark ink`() {
        val gray = IntArray(200) { if (it % 4 == 0) 30 else 220 }
        val threshold = Otsu.threshold(gray)
        assertTrue("порог должен лежать между модами, получено $threshold", threshold in 30..219)
    }

    @Test
    fun `ink projection counts dark pixels per column`() {
        val image = OcrImage.of(5, 2) { x, _ -> if (x == 2) Pixel(0, 0, 0) else Pixel(255, 255, 255) }
        val ink = image.inkProjection(otsu = 128)
        assertEquals(listOf(0, 0, 2, 0, 0), ink.toList())
    }

    @Test
    fun `connected components keep a real blob and drop noise`() {
        val size = 32
        val probability = FloatArray(size * size)
        for (y in 10 until 16) {
            for (x in 8 until 14) probability[y * size + x] = 0.9f
        }
        probability[30 * size + 2] = 0.9f
        val boxes = ConnectedComponents.find(probability, size)
        assertEquals(1, boxes.size)
        assertEquals(listOf(8, 10, 13, 15), boxes[0].toList())
    }

    @Test
    fun `components thinner than three pixels are ignored`() {
        val size = 24
        val probability = FloatArray(size * size)
        for (x in 0 until 20) probability[5 * size + x] = 0.9f
        assertTrue("площадь 20 >= 16, отбрасывает только высота", ConnectedComponents.find(probability, size).isEmpty())
    }

    @Test
    fun `merge joins overlapping boxes and keeps distant ones apart`() {
        val a = TextBox(0, 0, 20, 12)
        val b = TextBox(15, 2, 40, 12)
        val far = TextBox(0, 200, 20, 212)
        val merged = TextBoxes.merge(listOf(a, b, far))
        assertEquals(2, merged.size)
        assertEquals(TextBox(0, 0, 40, 12), merged[0])
        assertEquals(far, merged[1])
    }

    @Test
    fun `box cap keeps the first 128 detections`() {
        val boxes = (0 until 200).map { TextBox(it, 0, it + 1, 1) }
        assertEquals(128, TextBoxes.cap(boxes).size)
    }

    @Test
    fun `padding adds four percent horizontally and twelve vertically`() {
        val padded = TextBoxes.pad(TextBox(100, 100, 200, 120), 800, 600)
        assertEquals(96, padded.left)
        assertEquals(98, padded.top)
        assertEquals(204, padded.right)
        assertEquals(122, padded.bottom)
    }

    @Test
    fun `padding never leaves the image`() {
        val padded = TextBoxes.pad(TextBox(0, 0, 4, 4), 100, 100)
        assertEquals(0, padded.left)
        assertEquals(0, padded.top)
    }

    @Test
    fun `detector box is expanded and mapped back to source pixels`() {
        val box = TextBoxes.fromDetector(
            minX = 100, minY = 100, maxX = 140, maxY = 120,
            scale = 0.5, offsetX = 0, offsetY = 0,
            offsetInImageX = 0, offsetInImageY = 0,
            sourceWidth = 1472, sourceHeight = 1472,
        )
        assertTrue("лево должно быть меньше 200, получено ${box.left}", box.left < 200)
        assertTrue("ширина должна быть около 104, получено ${box.width}", box.width in 100..108)
    }

    @Test
    fun `detector box too small is discarded`() {
        val box = TextBoxes.fromDetector(
            minX = 10, minY = 10, maxX = 11, maxY = 11,
            scale = 1.0, offsetX = 0, offsetY = 0,
            offsetInImageX = 0, offsetInImageY = 0,
            sourceWidth = 100, sourceHeight = 100,
        )
        assertEquals(TextBox(0, 0, 0, 0), box)
    }

    @Test
    fun `reading order groups by row and sorts by x`() {
        val items = listOf(
            TextBox(200, 10, 260, 26) to "второе",
            TextBox(10, 12, 90, 26) to "первое",
            TextBox(10, 60, 90, 74) to "нижнее",
        )
        val text = TextBoxes.assembleReadingOrder(items)
        assertEquals("первое второе\nнижнее", text)
    }

    @Test
    fun `reading order of nothing is empty`() {
        assertEquals("", TextBoxes.assembleReadingOrder(emptyList()))
    }

    @Test
    fun `high contrast image is uniform per pixel`() {
        val image = solid(2, 2, Pixel(200, 200, 200))
        val contrasted = image.highContrast()
        assertEquals(2, contrasted.width)
        assertEquals(255, contrasted.at(0, 0).r)
    }
}
