"""Бенчмарк: стриминг vs файловая генерация."""
import asyncio
import time
import os
import sys
import wave
import io

sys.path.insert(0, os.path.dirname(__file__))

from tts_engine import TTSEngine
from settings import Settings

settings = Settings()

SENTENCES = [
    "Привет!",
    "Как дела?",
    "Это тест пофразового TTS.",
    "Каждое предложение читается отдельно.",
    "Текст стирается по мере чтения.",
    "Голос и субтитры синхронны.",
]


async def bench_file_generation():
    """Старый способ: полная генерация файла → воспроизведение."""
    engine = TTSEngine(settings)
    engine.set_voice("ru-RU-DmitryNeural")
    engine.voice_type = "edge"
    engine._add_pauses_ssml = lambda t: t  # без SSML

    total = 0
    for s in SENTENCES:
        start = time.perf_counter()
        path = await engine.generate_audio_file(s)
        ms = (time.perf_counter() - start) * 1000
        total += ms
        print(f"  [FILE] {ms:6.0f}ms | {s[:40]}")
    print(f"  ИТОГО файл: {total:.0f}ms (среднее {total/len(SENTENCES):.0f}ms)")
    return total


async def bench_streaming():
    """Новый способ: стриминг чанков → VLC stdin."""
    engine = TTSEngine(settings)
    engine.set_voice("ru-RU-DmitryNeural")
    engine.voice_type = "edge"

    total = 0
    for s in SENTENCES:
        start = time.perf_counter()
        await engine.play_streaming(s)
        ms = (time.perf_counter() - start) * 1000
        total += ms
        print(f"  [STREAM] {ms:6.0f}ms | {s[:40]}")
    print(f"  ИТОГО стриминг: {total:.0f}ms (среднее {total/len(SENTENCES):.0f}ms)")
    return total


async def bench_streaming_timing():
    """Замер: сколько времени до ПЕРВОГО звука."""
    engine = TTSEngine(settings)
    engine.set_voice("ru-RU-DmitryNeural")
    engine.voice_type = "edge"

    import subprocess
    vlc_paths = [
        r"E:\Program Files\VideoLAN\VLC\vlc.exe",
        r"C:\Program Files\VideoLAN\VLC\vlc.exe",
    ]
    vlc_exe = None
    for p in vlc_paths:
        if os.path.isfile(p):
            vlc_exe = p
            break
    if not vlc_exe:
        print("  VLC не найден!")
        return

    import edge_tts
    text = "Привет! Как дела?"
    rate_str = "+0%"
    volume_str = "+0%"
    pitch_str = "+0Hz"

    creation_flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0

    print(f"\n  Текст: '{text}'")
    print(f"  Замер: время до первого чанка и полного воспроизведения")

    # Способ 1: save() — полная генерация
    t0 = time.perf_counter()
    comm = edge_tts.Communicate(text=text, voice="ru-RU-DmitryNeural", rate=rate_str, volume=volume_str, pitch=pitch_str)
    chunks = []
    async for chunk in comm.stream():
        if chunk["type"] == "audio" and chunk["data"]:
            chunks.append(chunk["data"])
    t_stream_done = (time.perf_counter() - t0) * 1000

    # Записываем в файл и играем
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        for c in chunks:
            f.write(c)
        temp_path = f.name

    t1 = time.perf_counter()
    proc = subprocess.Popen(
        [vlc_exe, '--play-and-exit', '--no-video', '--quiet', temp_path],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=creation_flags,
    )
    proc.wait()
    t_play_done = (time.perf_counter() - t1) * 1000
    t_total_file = (time.perf_counter() - t0) * 1000
    os.unlink(temp_path)

    print(f"\n  === Способ 1: save() -> файл -> VLC ===")
    print(f"  Генерация chunks:     {t_stream_done:.0f}ms")
    print(f"  Запись + VLC play:    {t_play_done:.0f}ms")
    print(f"  ИТОГО:                {t_total_file:.0f}ms")

    # Способ 2: stream → pipe в VLC stdin
    t0 = time.perf_counter()
    first_chunk_time = None
    comm = edge_tts.Communicate(text=text, voice="ru-RU-DmitryNeural", rate=rate_str, volume=volume_str, pitch=pitch_str)

    proc = subprocess.Popen(
        [vlc_exe, '--play-and-exit', '--no-video', '--quiet', '--demux=mp3', '-'],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=creation_flags,
    )

    chunk_count = 0
    async for chunk in comm.stream():
        if chunk["type"] == "audio" and chunk["data"]:
            if chunk_count == 0:
                first_chunk_time = (time.perf_counter() - t0) * 1000
            chunk_count += 1
            proc.stdin.write(chunk["data"])
            proc.stdin.flush()

    proc.stdin.close()
    proc.wait()
    t_total_stream = (time.perf_counter() - t0) * 1000

    print(f"\n  === Способ 2: stream -> VLC stdin pipe ===")
    print(f"  Первый чанк:          {first_chunk_time:.0f}ms  <-- ГОЛОС НАЧИНАЕТ ТУТ")
    print(f"  Полное воспроизведение: {t_total_stream:.0f}ms")
    print(f"  Чанков: {chunk_count}")

    print(f"\n  === ЭКОНОМИЯ ===")
    print(f"  Старый (файл->VLC):  {t_total_file:.0f}ms")
    print(f"  Новый (pipe->VLC):   {t_total_stream:.0f}ms")
    print(f"  Голос начинает через: {first_chunk_time:.0f}ms вместо {t_total_file:.0f}ms")
    if first_chunk_time and t_total_file > 0:
        print(f"  Ускорение начала голоса: {t_total_file / first_chunk_time:.1f}x")


async def main():
    print("=" * 60)
    print("БЕНЧМАРК: стриминг vs файловая генерация")
    print("=" * 60)

    await bench_streaming_timing()


if __name__ == "__main__":
    asyncio.run(main())
