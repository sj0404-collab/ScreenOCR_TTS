"""Тестирование задержки TTS: время до начала чтения."""
import asyncio
import time
import os
import sys
import wave

sys.path.insert(0, os.path.dirname(__file__))

from tts_engine import TTSEngine
from settings import Settings

settings = Settings()

TEST_TEXTS = {
    "ru_short": "Привет, мир!",
    "ru_medium": "Привет. Как дела? Это тест TTS движка с паузами.",
    "ru_long": "Привет! Сегодня хорошая погода. Давай прогуляемся в парке. Там красиво, деревья зелёные.",
    "en_short": "Hello, world!",
    "en_medium": "Hello. How are you? This is a TTS engine test with pauses.",
}

def measure_time_to_first_audio(audio_bytes: bytes) -> float:
    """Измеряет реальную длительность аудио (для оценки)."""
    if not audio_bytes or len(audio_bytes) < 100:
        return 0.0
    try:
        import io
        with wave.open(io.BytesIO(audio_bytes), 'rb') as wf:
            frames = wf.getnframes()
            rate = wf.getframerate()
            return frames / rate * 1000  # ms
    except Exception:
        return 0.0


async def test_edge_tts_with_ssml():
    """Тест Edge-TTS с SSML-паузами: время генерации."""
    engine = TTSEngine(settings)
    engine.set_voice("ru-RU-DmitryNeural")
    engine.voice_type = "edge"
    
    print("\n" + "=" * 60)
    print("EDGE-TTS С SSML-ПАУЗАМИ")
    print("=" * 60)
    
    results = []
    for name, text in TEST_TEXTS.items():
        ssml_text = engine._add_pauses_ssml(text)
        
        start = time.perf_counter()
        try:
            audio = await engine.generate_audio(text)
            elapsed = (time.perf_counter() - start) * 1000
            audio_duration = measure_time_to_first_audio(audio)
            results.append((name, elapsed, audio_duration, len(audio)))
            print(f"  {name:20s} | генерация: {elapsed:7.0f}ms | аудио: {audio_duration:7.0f}ms | размер: {len(audio):>6d} байт")
        except Exception as e:
            elapsed = (time.perf_counter() - start) * 1000
            results.append((name, elapsed, 0, 0))
            print(f"  {name:20s} | ОШИБКА: {e} ({elapsed:.0f}ms)")
    
    return results


async def test_edge_tts_without_ssml():
    """Тест Edge-TTS БЕЗ SSML-пауз (baseline)."""
    engine = TTSEngine(settings)
    engine.set_voice("ru-RU-DmitryNeural")
    engine.voice_type = "edge"
    
    print("\n" + "=" * 60)
    print("EDGE-TTS БЕЗ SSML (BASELINE)")
    print("=" * 60)
    
    # Временно отключаем SSML
    original = engine._add_pauses_ssml
    engine._add_pauses_ssml = lambda t: t
    
    results = []
    for name, text in TEST_TEXTS.items():
        start = time.perf_counter()
        try:
            audio = await engine.generate_audio(text)
            elapsed = (time.perf_counter() - start) * 1000
            audio_duration = measure_time_to_first_audio(audio)
            results.append((name, elapsed, audio_duration, len(audio)))
            print(f"  {name:20s} | генерация: {elapsed:7.0f}ms | аудио: {audio_duration:7.0f}ms | размер: {len(audio):>6d} байт")
        except Exception as e:
            elapsed = (time.perf_counter() - start) * 1000
            results.append((name, elapsed, 0, 0))
            print(f"  {name:20s} | ОШИБКА: {e} ({elapsed:.0f}ms)")
    
    engine._add_pauses_ssml = original
    return results


async def test_sapi():
    """Тест SAPI (локальный, должен быть быстрым)."""
    engine = TTSEngine(settings)
    
    print("\n" + "=" * 60)
    print("SAPI (ЛОКАЛЬНЫЙ)")
    print("=" * 60)
    
    if not engine.sapi_available:
        print("  SAPI недоступен")
        return []
    
    results = []
    for name, text in list(TEST_TEXTS.items())[:3]:
        start = time.perf_counter()
        try:
            await engine._speak_sapi(text)
            elapsed = (time.perf_counter() - start) * 1000
            results.append((name, elapsed))
            print(f"  {name:20s} | воспроизведение: {elapsed:7.0f}ms")
        except Exception as e:
            elapsed = (time.perf_counter() - start) * 1000
            results.append((name, elapsed))
            print(f"  {name:20s} | ОШИБКА: {e} ({elapsed:.0f}ms)")
    
    return results


async def main():
    print("Тестирование TTS: время до начала чтения")
    print(f"Дата: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    
    edge_with_ssml = await test_edge_tts_with_ssml()
    edge_without_ssml = await test_edge_tts_without_ssml()
    
    # Сравнение
    print("\n" + "=" * 60)
    print("СРАВНЕНИЕ: С SSML vs БЕЗ SSML")
    print("=" * 60)
    print(f"  {'Режим':20s} | {'С SSML':>10s} | {'Без SSML':>10s} | {'Разница':>10s}")
    print("-" * 60)
    
    for (name_s, gen_s, aud_s, _), (name_b, gen_b, aud_b, _) in zip(edge_with_ssml, edge_without_ssml):
        diff = gen_s - gen_b
        print(f"  {name_s:20s} | {gen_s:8.0f}ms | {gen_b:8.0f}ms | {diff:+8.0f}ms")


if __name__ == "__main__":
    asyncio.run(main())
