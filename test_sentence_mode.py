"""Тест пофразового режима: задержка для коротких предложений."""
import asyncio
import time
import os
import sys
import wave
import io

sys.path.insert(0, os.path.dirname(__file__))

from tts_engine import TTSEngine
from live_scanner import LiveScanner
from settings import Settings

settings = Settings()

LONG_TEXT = "Привет! Как дела? Это тест пофразового TTS. Каждое предложение читается отдельно. Текст стирается по мере чтения. Голос и субтитры синхронны."

def measure_duration(audio_bytes: bytes) -> float:
    if not audio_bytes or len(audio_bytes) < 100:
        return 0.0
    try:
        with wave.open(io.BytesIO(audio_bytes), 'rb') as wf:
            return wf.getnframes() / wf.getframerate() * 1000
    except Exception:
        return 0.0


async def test_sentence_split():
    print("=" * 60)
    print("1. ТЕСТ РАЗБИЕНИЯ НА ПРЕДЛОЖЕНИЯ")
    print("=" * 60)
    sentences = LiveScanner._split_sentences(LONG_TEXT)
    for i, s in enumerate(sentences):
        print(f"  [{i+1}] {s}")
    print(f"\n  Всего предложений: {len(sentences)}")
    return sentences


async def test_per_sentence_generation():
    print("\n" + "=" * 60)
    print("2. ВРЕМЯ ГЕНЕРАЦИИ КАЖДОГО ПРЕДЛОЖЕНИЯ (Edge-TTS, без SSML)")
    print("=" * 60)
    
    engine = TTSEngine(settings)
    engine.set_voice("ru-RU-DmitryNeural")
    engine.voice_type = "edge"
    # Отключаем SSML для быстрой генерации
    engine._add_pauses_ssml = lambda t: t
    
    sentences = LiveScanner._split_sentences(LONG_TEXT)
    
    total_gen = 0
    total_audio = 0
    for i, s in enumerate(sentences):
        start = time.perf_counter()
        try:
            audio = await engine.generate_audio(s)
            gen_ms = (time.perf_counter() - start) * 1000
            dur_ms = measure_duration(audio)
            total_gen += gen_ms
            total_audio += dur_ms
            print(f"  [{i+1}] генерация: {gen_ms:6.0f}ms | аудио: {dur_ms:6.0f}ms | {s[:40]}")
        except Exception as e:
            gen_ms = (time.perf_counter() - start) * 1000
            print(f"  [{i+1}] ОШИБКА: {e} ({gen_ms:.0f}ms)")
    
    print(f"\n  ИТОГО генерация: {total_gen:.0f}ms")
    print(f"  ИТОГО аудио:     {total_audio:.0f}ms")
    print(f"  Средняя задержка до чтения: {total_gen/len(sentences):.0f}ms на предложение")


async def test_vs_whole_text():
    print("\n" + "=" * 60)
    print("3. СРАВНЕНИЕ: пофразово vs целиком")
    print("=" * 60)
    
    engine = TTSEngine(settings)
    engine.set_voice("ru-RU-DmitryNeural")
    engine.voice_type = "edge"
    engine._add_pauses_ssml = lambda t: t
    
    # Целиком
    start = time.perf_counter()
    audio_whole = await engine.generate_audio(LONG_TEXT)
    whole_gen = (time.perf_counter() - start) * 1000
    whole_dur = measure_duration(audio_whole)
    
    # Пофразово (сумма)
    sentences = LiveScanner._split_sentences(LONG_TEXT)
    total_gen = 0
    total_dur = 0
    for s in sentences:
        start = time.perf_counter()
        audio = await engine.generate_audio(s)
        total_gen += (time.perf_counter() - start) * 1000
        total_dur += measure_duration(audio)
    
    print(f"  Целиком:    генерация {whole_gen:.0f}ms | аудио {whole_dur:.0f}ms")
    print(f"  Пофразово:  генерация {total_gen:.0f}ms | аудио {total_dur:.0f}ms")
    print(f"  Первое предложение (пофразово): {total_gen/len(sentences):.0f}ms → голос НАЧИНАЕТ через ~{total_gen/len(sentences):.0f}ms")
    print(f"  Весь текст (целиком): голос НАЧИНАЕТ через ~{whole_gen:.0f}ms")


async def main():
    print("Тест пофразового TTS-режима")
    print(f"Дата: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Текст: {LONG_TEXT[:60]}...")
    
    await test_sentence_split()
    await test_per_sentence_generation()
    await test_vs_whole_text()


if __name__ == "__main__":
    asyncio.run(main())
