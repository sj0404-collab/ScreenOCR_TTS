package com.screenocr.client.core

import kotlin.math.abs
import kotlin.math.max

object Similarity {

    fun ratio(a: String, b: String): Double {
        if (a.isEmpty() || b.isEmpty()) return 0.0
        if (a == b) return 1.0
        val maxLen = max(a.length, b.length)
        if (abs(a.length - b.length) > maxLen * 0.3) return 0.0
        val long = if (a.length >= b.length) a else b
        val short = if (a.length >= b.length) b else a
        var prev = IntArray(short.length + 1)
        var maxFound = 0
        for (i in 1..long.length) {
            val cur = IntArray(short.length + 1)
            for (j in 1..short.length) {
                if (long[i - 1] == short[j - 1]) {
                    cur[j] = prev[j - 1] + 1
                    if (cur[j] > maxFound) maxFound = cur[j]
                }
            }
            prev = cur
        }
        return if (maxLen == 0) 0.0 else maxFound.toDouble() / maxLen
    }
}
