"""Демо всех доступных голосов: Edge, RHVoice, Silero, Piper, SAPI."""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))

from tts_engine import TTSEngine
from settings import Settings

settings = Settings()
TEXT = "Привет! Меня зовут Тимофей. Сегодня хорошая погода."

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "voice_samples")
os.makedirs(OUTPUT_DIR, exist_ok=True)


async def demo_all():
    engine = TTSEngine(settings)
    
    print("=" * 60)
    print("ДЕМО ВСЕХ ДОСТУПНЫХ ГОЛОСОВ")
    print(f"Текст: {TEXT}")
    print(f"Папка: {OUTPUT_DIR}")
    print("=" * 60)
    
    results = []
    
    # --- Edge-TTS ---
    edge_samples = [
        ("ru-RU-DmitryNeural", "Edge: Dmitry (M, ru)"),
        ("ru-RU-SvetlanaNeural", "Edge: Svetlana (F, ru)"),
        ("ru-RU-DarinaNeural", "Edge: Darina (F, ru)"),
        ("en-US-GuyNeural", "Edge: Guy (M, en)"),
        ("en-US-JennyNeural", "Edge: Jenny (F, en)"),
        ("ja-JP-KeitaNeural", "Edge: Keita (M, ja)"),
    ]
    
    for voice_code, label in edge_samples:
        engine.voice_type = "edge"
        engine.set_voice(voice_code)
        fname = f"edge_{voice_code.replace('-', '_')}.mp3"
        path = os.path.join(OUTPUT_DIR, fname)
        try:
            t0 = time.perf_counter()
            await engine.generate_audio_file(TEXT, output_path=path)
            ms = (time.perf_counter() - t0) * 1000
            sz = os.path.getsize(path) if os.path.exists(path) else 0
            results.append((label, f"{ms:.0f}ms", f"{sz} bytes", path))
        except Exception as e:
            results.append((label, "ERROR", str(e)[:60], None))
    
    # --- RHVoice ---
    rhvoice_samples = [
        ("Aleksandr", "RHVoice: Aleksandr (M, ru)"),
        ("Anna", "RHVoice: Anna (F, ru)"),
        ("Arina", "RHVoice: Arina (F, ru)"),
        ("Mikhail", "RHVoice: Mikhail (M, ru)"),
        ("Tatiana", "RHVoice: Tatiana (F, ru)"),
        ("Elena", "RHVoice: Elena (F, ru)"),
    ]
    
    for voice_name, label in rhvoice_samples:
        engine.voice_type = "rhvoice"
        engine.set_voice(f"rhvoice:{voice_name}")
        fname = f"rhvoice_{voice_name}.wav"
        path = os.path.join(OUTPUT_DIR, fname)
        try:
            t0 = time.perf_counter()
            await engine.generate_audio_file(TEXT, output_path=path)
            ms = (time.perf_counter() - t0) * 1000
            sz = os.path.getsize(path) if os.path.exists(path) else 0
            results.append((label, f"{ms:.0f}ms", f"{sz} bytes", path))
        except Exception as e:
            results.append((label, "ERROR", str(e)[:60], None))
    
    # --- Silero ---
    if engine._silero_model:
        silero_samples = [
            ("silero:aidar", "Silero: aidar (M, ru)"),
            ("silero:baya", "Silero: baya (F, ru)"),
            ("silero:kseniya", "Silero: kseniya (F, ru)"),
            ("silero:eugene", "Silero: eugene (M, ru)"),
        ]
        for voice_code, label in silero_samples:
            engine.voice_type = "silero"
            engine.set_voice(voice_code)
            fname = f"silero_{voice_code.split(':')[1]}.wav"
            path = os.path.join(OUTPUT_DIR, fname)
            try:
                t0 = time.perf_counter()
                await engine.generate_audio_file(TEXT, output_path=path)
                ms = (time.perf_counter() - t0) * 1000
                sz = os.path.getsize(path) if os.path.exists(path) else 0
                results.append((label, f"{ms:.0f}ms", f"{sz} bytes", path))
            except Exception as e:
                results.append((label, "ERROR", str(e)[:60], None))
    else:
        results.append(("Silero", "N/A", "модель не загружена", None))
    
    # --- Piper ---
    if engine._piper_voices:
        for v in engine._piper_voices[:4]:
            voice_code = v.get("code", "")
            label = f"Piper: {v.get('name', voice_code)} ({v.get('lang', '?')})"
            engine.voice_type = "piper"
            engine.set_voice(f"piper:{voice_code}")
            fname = f"piper_{voice_code}.wav"
            path = os.path.join(OUTPUT_DIR, fname)
            try:
                t0 = time.perf_counter()
                await engine.generate_audio_file(TEXT, output_path=path)
                ms = (time.perf_counter() - t0) * 1000
                sz = os.path.getsize(path) if os.path.exists(path) else 0
                results.append((label, f"{ms:.0f}ms", f"{sz} bytes", path))
            except Exception as e:
                results.append((label, "ERROR", str(e)[:60], None))
    else:
        results.append(("Piper", "N/A", "голоса не найдены", None))
    
    # --- SAPI ---
    engine.voice_type = "sapi"
    engine._add_pauses_ssml = lambda t: t
    if engine.sapi_available and engine.sapi_voice:
        sapi_name = engine.sapi_voice
        path = os.path.join(OUTPUT_DIR, "sapi_default.wav")
        try:
            t0 = time.perf_counter()
            await engine.generate_audio_file(TEXT, output_path=path)
            ms = (time.perf_counter() - t0) * 1000
            sz = os.path.getsize(path) if os.path.exists(path) else 0
            results.append((f"SAPI: {sapi_name}", f"{ms:.0f}ms", f"{sz} bytes", path))
        except Exception as e:
            results.append(("SAPI", "ERROR", str(e)[:60], None))
    else:
        results.append(("SAPI", "N/A", "голос не найден", None))
    
    # --- Вывод результатов ---
    print("\n" + "=" * 60)
    print("РЕЗУЛЬТАТЫ")
    print("=" * 60)
    print(f"{'Голос':<40} {'Время':>8} {'Размер':>12}")
    print("-" * 60)
    for label, speed, size, path in results:
        print(f"{label:<40} {speed:>8} {size:>12}")
    
    print(f"\nВсего файлов: {len([r for r in results if r[3]])} в {OUTPUT_DIR}")
    print("Откройте папку и прослушайте файлы!")


if __name__ == "__main__":
    asyncio.run(demo_all())
