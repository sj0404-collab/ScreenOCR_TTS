# -*- coding: utf-8 -*-
"""
Voice Translator — STT -> Translate -> TTS pipeline.
Captures game audio via WASAPI loopback, translates English to Russian voice.
"""
import asyncio
import concurrent.futures
import json
import logging
import queue
import re
import threading
import time
from collections import deque

import numpy as np

from speech_decider import SpeechDecider

logger = logging.getLogger(__name__)

# Singleton offline translator (loaded once)
_offline_singleton = None


def _get_offline():
    global _offline_singleton
    if _offline_singleton is None:
        from offline_dict import OfflineTranslator
        _offline_singleton = OfflineTranslator()
    return _offline_singleton


def _get_net():
    from net_monitor import get_monitor
    return get_monitor()


class VoiceTranslator:
    """Captures game audio via WASAPI loopback, translates via Whisper + Translator + TTS."""

    def __init__(self, tts_engine=None, translator=None, decider=None):
        self.tts = tts_engine
        self.translator = translator
        # Решатель «что озвучивать»: скоринг по признакам вместо жёстких
        # правил. Можно подставить свой с другими весами под конкретную игру.
        self._decider = decider or SpeechDecider()
        self._whisper_model = None
        self._running = False
        self._src_lang = "auto"
        self._dst_lang = "ru"
        self.on_text = None
        self.on_heard = None
        self.on_voice_detected = None  # callback(is_voice: bool) — VAD state
        self._target_pid = None
        self._model_name = "tiny"          # fast, low-latency default
        self._chunk_duration = 3.0         # seconds of audio per transcription (3s for full sentences)
        # Очередь аудио БЕЗ жёсткого maxsize: раньше при нехватке
        # производительности мы ВЫБРАСЫВАЛИ старые куски, и реплики терялись.
        # Теперь копим (Whisper догонит), а счётчик _audio_dropped означает
        # реальную потерю и растёт только при аварийном переполнении.
        self._audio_queue = queue.Queue()
        self._audio_dropped = 0                      # реально потерянные куски
        self._audio_max_backlog_sec = 30.0          # дальше всё-таки отстаём
        self._audio_backlog_sec = 0.0
        self._tts_queue = queue.Queue()
        self._tts_worker_alive = False
        # Сколько раз поднимать упавший воркер: иначе одна ошибка
        # навсегда выключала бы озвучку/перевод.
        self._restart_max = 3
        # Параллельный перевод: пока Whisper разбирает следующий кусок,
        # предыдущий уже переводится по сети — задержка перестаёт складываться.
        self._tr_queue = queue.Queue()
        self._tr_worker_alive = False
        # Потоковый режим: отдаём реплику по частям, не дожидаясь конца фразы.
        self._stream_enabled = True
        self._stream_pause_ms = 260      # пауза внутри реплики = граница куска
        self._stream_min_chunk_sec = 1.1  # короче — смысла резать нет
        self._stream_min_gap = 0.7       # не чаще, чем раз в 0.7 с
        self._stream_force_cut_sec = 8.0  # нет паузы — режем по времени
        self._last_emit_at = 0.0
        # Жадный декодер:beam_size=1 вместо 5. В потоке задержка важнее
        # точности, куски и так режутся по паузам.
        self._stt_beam_size = 1
        # Субтитры с экрана: фолбэк, когда голоса нет, и уточнение текста,
        # когда голос и субтитры есть одновременно.
        # ВАЖНО: _sub_enabled и компания ОБЯЗАТЕЛЬНО инициализируются здесь.
        # Раньше эти строки потерялись при правке конструктора, и start()
        # падал с AttributeError, если субтитры не включались (то есть
        # при обычном запуске без --subtitles).
        self._sub_enabled = False
        self._sub_settings = None
        self._sub_region = None
        self._sub_require_fg = True
        self._sub_fast_ocr = True
        self._sub_reader = None
        self._sub_thread = None
        # потоки воркеров: без них нельзя отличить «жив» от «флаг стоит»
        self._tts_thread = None
        self._tr_thread = None
        self._audio_cb = None
        self._flush_timer = None
        self._warned_no_translate = False
        self._pending_fut = None
        self._pending_text = ""
        self._pending_lang = ""
        self._pending_speaker = ""
        # Решатель «что озвучивать»: скоринг по признакам вместо жёстких
        # правил — иначе одни игры озвучиваются, другие нет.
        self._sub_poll_sec = 0.6
        self._sub_fallback_after = 2.5   # столько секунд тишины без речи -> OCR
        self._sub_last_text = ""
        self._sub_last_seen = 0.0
        self._sub_pending = ""           # свежий текст субтитров
        self._sub_pending_at = 0.0
        # Сколько «свежим» считается текст на экране. Окно нужно и для
        # сверки, и для фолбэка: старый текст из предыдущей реплики
        # сверять с новой голосовой репликой нельзя.
        # 8 c, а не 3: локальный OCR на CPU идёт 5 c на кадр, при более
        # узком окне свежие субтитры успевали бы протухнуть.
        self._sub_max_age = 8.0
        self._last_voice_at = 0.0
        self._sub_thread = None
        self._game_audio = None
        self._last_spoken_en = ""
        self._last_spoken_ru = ""
        self._tts_lock = threading.Lock()
        self._echo_until = 0.0
        self._tts_playing = False        # True while our own voice is audible
        # Запас ПОСЛЕ озвучки: loopback отдаёт чанк с задержкой
        # (размер буфера pyaudio 1 c + задержка устройства ~0.5 c).
        self._echo_grace_sec = 2.0
        self._duck_volume = 0.3          # game volume while TTS speaks
        self._recent_ru = deque(maxlen=16)  # our own speech -> anti-echo filter
        # Не переводить речь на языке нашей же озвучки: это наш собственный
        # голос, попавший в loopback (гарантированный разрыв петли).
        self._skip_own_lang = True
        self._tts_start = 0.0
        self._stt_tts_rate = 0
        # Voice settings for STT dual mode
        self._en_voice = "en-US-BrianMultilingualNeural"   # Multilingual male
        self._ru_voice = "en-US-BrianMultilingualNeural"  # Multilingual male (same voice)
        # Dual mode: EN first, then RU translation in parallel
        self._dual_mode = False   # True = сначала EN, потом параллельно RU
        # VAD settings
        self._vad_enabled = True
        self._vad_threshold = 0.01  # RMS threshold for voice activity
        self._vad_min_speech_ms = 200  # min speech duration to trigger
        self._vad_silence_ms = 600  # silence duration to end speech segment
        # Streaming subtitle support: on_flush fires when a line should be
        # finalized (after silence or sentence-ending punctuation) so the GUI
        # can start a new paragraph below.
        self.on_flush = None
        self._flush_timer = None
        self._flush_delay = 1.5  # seconds of silence before flushing
        self._online_voice = None  # Edge voice to restore when back online

    _LANG_NAMES = {
        "en": "English", "ru": "Russian", "ja": "Japanese",
        "zh": "Chinese", "ko": "Korean", "de": "German",
        "fr": "French", "es": "Spanish", "it": "Italian",
        "pt": "Portuguese", "ar": "Arabic", "hi": "Hindi",
        "tr": "Turkish", "pl": "Polish", "nl": "Dutch",
        "sv": "Swedish", "fi": "Finnish", "uk": "Ukrainian",
        "cs": "Czech", "el": "Greek", "he": "Hebrew",
        "th": "Thai", "vi": "Vietnamese", "id": "Indonesian",
        "ms": "Malay",
    }

    def set_languages(self, src: str, dst: str):
        self._src_lang = src
        self._dst_lang = dst

    def set_target_pid(self, pid):
        """Restrict audio capture to a single process (per-process loopback)."""
        self._target_pid = int(pid) if pid else None

    def set_model(self, name):
        """Set the Whisper model name (e.g. 'tiny', 'base', 'small')."""
        if name and name != self._model_name:
            self._model_name = name
            # force reload on next use
            self._whisper_model = None

    def set_chunk_duration(self, sec):
        try:
            self._chunk_duration = max(0.5, float(sec))
        except Exception:
            pass

    def set_stt_tts_rate(self, rate):
        try:
            self._stt_tts_rate = int(rate)
        except Exception:
            pass

    def set_dual_mode(self, enabled: bool):
        """Включить dual mode: сначала EN повтор, потом параллельно RU перевод."""
        self._dual_mode = enabled
        logger.info(f"[VoiceTranslator] Dual mode: {'ON' if enabled else 'OFF'}")

    def set_stt_voices(self, en_voice: str = None, ru_voice: str = None):
        """Настроить голоса для STT: EN повтор и RU перевод."""
        if en_voice:
            self._en_voice = en_voice
        if ru_voice:
            self._ru_voice = ru_voice
        logger.info(f"[VoiceTranslator] Voices: EN={self._en_voice}, RU={self._ru_voice}")

    def set_vad(self, enabled: bool, threshold: float = 0.01):
        """Включить/выключить VAD и установить порог RMS."""
        self._vad_enabled = enabled
        self._vad_threshold = threshold
        logger.info(f"[VoiceTranslator] VAD: {'ON' if enabled else 'OFF'} (threshold={threshold})")

    def _detect_voice(self, audio: np.ndarray) -> bool:
        """Voice Activity Detection по RMS энергии сигнала (дешёвый pre-filter)."""
        if not self._vad_enabled:
            return True  # VAD выключен — всегда есть "голос"
        if len(audio) == 0:
            return False
        rms = float(np.sqrt(np.mean(audio ** 2)))
        return rms >= self._vad_threshold

    def _silero_speech_ranges(self, audio: np.ndarray, sr: int,
                              min_silence_ms: int = 450):
        """Найти участки РЕЧИ через Silero VAD (в отличие от RMS отличает
        речь от музыки/шума). Возвращает список (start_sec, end_sec) или None,
        если VAD недоступен.

        min_silence_ms — какой паузы считать границей. Для финализации
        реплики берём 450 мс, а для потоковой отдачи по чанкам — меньшую
        (~260 мс), чтобы найти ЕСТЕСТВЕННУЮ точку внутри длинной реплики
        и начать озвучку раньше, не дожидаясь конца фразы.
        """
        try:
            from faster_whisper.vad import get_speech_timestamps, VadOptions
        except Exception as e:
            logger.warning(f"[VoiceTranslator] Silero VAD unavailable: {e}")
            return None
        try:
            opts = VadOptions(
                threshold=0.5,             # уверенность речи
                min_speech_duration_ms=250,
                max_speech_duration_s=20.0,
                min_silence_duration_ms=min_silence_ms,
                speech_pad_ms=200,
            )
            chunks = get_speech_timestamps(
                audio, vad_options=opts, sampling_rate=sr)
            return [(c["start"] / sr, c["end"] / sr) for c in chunks]
        except Exception as e:
            logger.warning(f"[VoiceTranslator] Silero VAD failed: {e}")
            return None

    def _find_stream_cut(self, buffer: np.ndarray, sr: int):
        """Ищем ЕСТЕСТВЕННУЮ границу внутри буфера, чтобы озвучить реплику
        по частям, не дожидаясь конца.

        Возвращает (start_i, end_i) или None. Граница берётся по паузе
        ВНУТРИ речи: если в буфере уже два участка речи, значит между ними
        пауза — это и есть безопасное место для разреза.
        """
        if not self._stream_enabled:
            return None
        now = time.time()
        if now - self._last_emit_at < self._stream_min_gap:
            return None

        # Нет паузы и буфер уже большой — режем по времени, иначе реплика
        # может расти до аварийного предела и задержка снова уедет.
        force_by_len = len(buffer) >= int(self._stream_force_cut_sec * sr)

        ranges = self._silero_speech_ranges(buffer, sr, self._stream_pause_ms)
        if not ranges:
            return None

        if len(ranges) >= 2:
            start, end = ranges[0]
            if (end - start) >= self._stream_min_chunk_sec:
                self._last_emit_at = now
                logger.debug(f"[VoiceTranslator] поток: режу по паузе на "
                             f"{end:.2f} c ({end - start:.2f} c речи)")
                return int(start * sr), int(end * sr)

        if force_by_len:
            start, end = ranges[-1]
            if (end - start) < self._stream_min_chunk_sec and len(ranges) >= 2:
                start, end = ranges[-2]
            if (end - start) >= 0.4:
                self._last_emit_at = now
                logger.debug(f"[VoiceTranslator] поток: режу по времени на "
                             f"{end:.2f} c")
                return int(start * sr), int(end * sr)
        return None

    @staticmethod
    def _normalize_text(text: str) -> str:
        """Нормализация текста для сравнения (регистр/пунктуация)."""
        text = (text or "").lower()
        text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
        return re.sub(r"\s+", " ", text).strip()

    def _looks_like_own_voice(self, text: str) -> bool:
        """True, если текст похож на то, что мы уже произнесли сами.

        Нужен для системного loopback: наш TTS тоже попадает в захват.
        """
        norm = self._normalize_text(text)
        if not norm:
            return True
        words = set(norm.split())
        if not words:
            return True
        for prev in self._recent_ru:
            p = self._normalize_text(prev)
            if not p:
                continue
            pw = set(p.split())
            if norm == p:
                return True
            if words and pw and len(words & pw) / max(len(words), len(pw)) > 0.6:
                return True
        return False

    def _speak_en_then_ru(self, en_text: str, ru_text: str, tts_voice: str = None):
        """Dual mode: сначала озвучить EN, потом RU перевод.
        
        Используем один мультилингвальный голос для обоих языков,
        чтобы сохранить единый тембр. tts_voice переключает голос
        на время озвучки.
        """
        if not self.tts:
            return

        # Сохраняем текущий голос
        old_voice = self.tts.voice
        old_type = self.tts.voice_type
        old_rate = self.tts.rate

        voice = tts_voice or self._en_voice

        try:
            # 1) Озвучить на английском
            self.tts.set_voice(voice)
            self.tts.rate = 0

            loop = asyncio.new_event_loop()
            loop.run_until_complete(self.tts.speak(en_text))
            loop.close()
        except Exception as e:
            logger.warning(f"[VoiceTranslator] EN speak error: {e}")

        # 2) Озвучить на русском (тот же мультилингвальный голос)
        if ru_text and ru_text != en_text:
            try:
                self.tts.set_voice(voice)
                self.tts.rate = -20  # медленнее для лучшего восприятия

                loop = asyncio.new_event_loop()
                loop.run_until_complete(self.tts.speak(ru_text))
                loop.close()
            except Exception as e:
                logger.warning(f"[VoiceTranslator] RU speak error: {e}")

        # Восстанавливаем
        self.tts.voice = old_voice
        self.tts.voice_type = old_type
        self.tts.rate = old_rate

    def _schedule_flush(self):
        """Reset the silence timer — flush fires after _flush_delay seconds."""
        if self._flush_timer:
            self._flush_timer.cancel()
        self._flush_timer = threading.Timer(self._flush_delay, self._flush_pending)
        self._flush_timer.daemon = True
        self._flush_timer.start()

    def _flush_pending(self):
        """Called when a line should be finalized (silence or sentence end)."""
        if self.on_flush:
            try:
                self.on_flush()
            except Exception:
                pass

    def _ensure_whisper(self):
        if self._whisper_model is not None:
            return True
        try:
            import torch
            from faster_whisper import WhisperModel
            device = "cuda" if torch.cuda.is_available() else "cpu"
            compute_type = "float16" if device == "cuda" else "int8"
            logger.info(f"[VoiceTranslator] Loading faster-whisper {self._model_name} on {device} ({compute_type})...")
            self._whisper_model = WhisperModel(
                self._model_name, device=device, compute_type=compute_type)
            logger.info(f"[VoiceTranslator] faster-whisper {self._model_name} ready ({device})")
            return True
        except Exception as e:
            logger.error(f"[VoiceTranslator] faster-whisper load failed: {e}")
            return False

    def start(self):
        if self._running:
            return

        self._last_spoken_en = ""
        self._last_spoken_ru = ""

        # Preload offline dictionary once
        _get_offline()

        from game_audio_capture import game_audio
        self._game_audio = game_audio

        game = game_audio.find_game_process()
        if game:
            logger.info(f"[VoiceTranslator] Game found: {game['name']} (PID={game['pid']})")
        else:
            logger.warning("[VoiceTranslator] No game found, capturing all system audio")

        def on_audio(audio, sr):
            # Раньше при заполнении очереди мы ВЫБРАСЫВАЛИ старое аудио, и
            # реплики терялись на ровном месте (пользователь: «давай полное
            # чтение»). Теперь очередь не ограничена: если Whisper отстаёт,
            # копится бэклог, и он догоняется. Реально рвём только когда
            # отставание превысило _audio_max_backlog_sec — иначе задержка
            # уйдёт в бесконечность.
            try:
                dur = len(audio) / float(sr or 16000)
            except Exception:
                dur = 0.0
            self._audio_backlog_sec += max(0.0, dur)
            if self._audio_backlog_sec > self._audio_max_backlog_sec:
                try:
                    old, osr = self._audio_queue.get_nowait()
                    try:
                        self._audio_backlog_sec -= len(old) / float(osr or 16000)
                    except Exception:
                        pass
                    self._audio_dropped += 1
                except queue.Empty:
                    self._audio_backlog_sec = 0.0
            self._audio_queue.put((audio, sr))

        game_audio.add_audio_callback(on_audio)
        self._audio_cb = on_audio
        # Apply the current target PID. Only RESTART capture when an explicit
        # exe is selected and differs from the current one — restarting the
        # loopback stream while its read-thread is active causes a native
        # crash. For the default ("focused window", pid=None) we keep the
        # already-running capture.
        target = getattr(self, "_target_pid", None)
        if target is not None and game_audio._running and game_audio._game_pid != target:
            game_audio.stop()
        game_audio.start(game_pid=target)

        self._running = True
        # Отсчёт «когда последний раз слышали речь»: с нуля фолбэк по
        # субтитрам сработал бы мгновенно после запуска.
        self._last_voice_at = time.time()
        if self._sub_enabled and self._sub_thread is None:
            self._start_subtitle_thread()
        # Load Whisper in a BACKGROUND thread so the GUI stays responsive.
        # Loading the model synchronously on the UI thread froze the app
        # (torch spike) — now the UI never blocks on model load.
        self._spawn('whisper-load', self._load_whisper_bg)
        logger.info("[VoiceTranslator] Started (Whisper loading in background)")

    def _load_whisper_bg(self):
        if self._ensure_whisper():
            self._spawn('stt-loop', self._process_loop, restart=True)
            logger.info("[VoiceTranslator] Whisper ready, STT processing started")
        else:
            logger.error("[VoiceTranslator] Whisper failed to load; STT inactive")

    def stop(self):
        """Остановить конвейер. Не должна падать ни при каких условиях:
        вызывается и из GUI при закрытии, и из finally в run_voice."""
        try:
            self._running = False
            if self._flush_timer:
                try:
                    self._flush_timer.cancel()
                except Exception:
                    pass
                self._flush_timer = None
            if self._game_audio:
                if getattr(self, "_audio_cb", None):
                    try:
                        self._game_audio.remove_audio_callback(self._audio_cb)
                    except Exception:
                        pass
                    self._audio_cb = None
                # приватный список: getattr, чтобы не упасть, если
                # реализация capturer изменится
                if not getattr(self._game_audio, "_audio_callbacks", None):
                    try:
                        self._game_audio.stop()
                    except Exception as e:
                        logger.debug(f"[VoiceTranslator] capturer.stop: {e}")
            # Освобождаем reader субтитров: mss держит GDI-дескрипторы,
            # при частых стартах/остановках они накапливались
            if self._sub_reader is not None:
                try:
                    self._sub_reader.close()
                except Exception as e:
                    logger.debug(f"[VoiceTranslator] sub_reader.close: {e}")
                self._sub_reader = None
            logger.info("[VoiceTranslator] Stopped")
        except Exception:
            logger.error("[VoiceTranslator] сбой при остановке",
                         exc_info=True)

    def _process_loop(self):
        """Накопление аудио и разбиение на РЕПЛИКИ по паузам (Silero VAD).

        Логика:
        - буфер растёт входящими чанками;
        - Silero VAD находит участки речи (музыка/шум отбрасываются);
        - реплика отдаётся в обработку целиком, когда за ней идёт пауза;
        - если речь ещё продолжается — ждём следующий чанк.
        """
        SR = 16000
        MIN_WINDOW = int(0.5 * SR)     # меньше — рано смотреть
        TAIL_KEEP = int(0.35 * SR)    # хвост, который может продолжиться речью
        PAD_KEEP = int(0.20 * SR)     # немного контекста перед следующей репликой
        MAX_BUF = int(15.0 * SR)      # аварийный предел буфера
        MIN_SEG = int(0.30 * SR)      # короче — мусор

        buffer = np.array([], dtype=np.float32)
        last_audio_at = 0.0
        dropped_mark = 0

        while self._running:
            # Отстаём от игры? Сообщаем один раз, чтобы было видно в логе.
            if self._audio_dropped > dropped_mark:
                dropped_mark = self._audio_dropped
                logger.warning(f"[VoiceTranslator] отставание > "
                               f"{self._audio_max_backlog_sec:.0f} c, потеряно "
                               f"кусков={dropped_mark} (Whisper медленнее "
                               f"реального времени — берите модель меньше)")

            try:
                audio, new_sr = self._audio_queue.get(timeout=0.4)
                try:
                    self._audio_backlog_sec -= len(audio) / float(new_sr or 16000)
                except Exception:
                    pass
            except queue.Empty:
                # ══════ ТИШИНА В КАНАЛЕ ══════
                # Слой захвата отбрасывает тихие чанки, поэтому «хвоста паузы»
                # в буфере может не быть. Если нового звука нет дольше
                # 0.7 c — считаем реплику законченной и обрабатываем её.
                if buffer.size and (time.time() - last_audio_at) >= 0.7:
                    buffer = self._drain_buffer(buffer, SR, force=True,
                                               min_window=MIN_WINDOW,
                                               min_seg=MIN_SEG,
                                               tail_keep=TAIL_KEEP)
                continue
            except Exception as e:
                logger.error(f"[VoiceTranslator] Loop error: {e}")
                continue

            # ══════ ЖЁСТКАЯ ЗАЩИТА ОТ САМОВОСПРИЯТИЯ ══════
            # Пока говорит наш TTS — входящее аудио выбрасываем целиком.
            if self._tts_playing or time.time() < self._echo_until:
                buffer = np.array([], dtype=np.float32)
                last_audio_at = time.time()
                continue

            try:
                if new_sr and new_sr != SR:
                    import scipy.signal
                    audio = scipy.signal.resample(
                        audio, int(len(audio) * SR / new_sr))
                audio = np.asarray(audio, dtype=np.float32)
            except Exception as e:
                logger.warning(f"[VoiceTranslator] resample failed: {e}")
                continue

            last_audio_at = time.time()
            if len(audio) == 0:
                continue

            buffer = np.concatenate([buffer, audio])
            if len(buffer) > MAX_BUF:
                buffer = buffer[-MAX_BUF:]

            if len(buffer) < MIN_WINDOW:
                continue

            buffer = self._drain_buffer(buffer, SR, force=False,
                                        min_window=MIN_WINDOW,
                                        min_seg=MIN_SEG,
                                        tail_keep=TAIL_KEEP)

    def _drain_buffer(self, buffer: np.ndarray, sr: int, force: bool,
                      min_window: int, min_seg: int, tail_keep: int):
        """Извлечь из буфера завершённые реплики и обработать их.

        force=True — звук закончился (тишина в канале), обрабатываем всё, что
        накоплено, даже если пауза внутри не выглядит завершённой.
        """
        MAX_BUF = int(15.0 * sr)
        PAD_KEEP = int(0.20 * sr)

        if len(buffer) < min_window:
            return buffer

        # Дешёвый RMS-гейт: тишина точно не речь
        if not self._detect_voice(buffer):
            return buffer[-tail_keep:]

        # ══════ ПОТОКОВЫЙ РЕЖИМ ══════
        # Пока реплика ещё не закончилась, но внутри уже есть естественная
        # пауза — отдаём стабильную часть сейчас. Так озвучка начинается
        # на пару секунд раньше, чем при ожидании полной тишины.
        if not force:
            cut = self._find_stream_cut(buffer, sr)
            if cut is not None:
                s_i, e_i = cut
                if e_i - s_i >= min_seg:
                    seg = buffer[s_i:e_i].copy()
                    try:
                        self._process_chunk(seg, sr)
                    except Exception as e:
                        logger.error(f"[VoiceTranslator] stream chunk error: {e}")
                    return buffer[max(0, e_i - PAD_KEEP):]

        ranges = self._silero_speech_ranges(buffer, sr)
        if ranges is None:
            # VAD недоступен — режем по окну
            if force or len(buffer) >= int(6.0 * sr):
                try:
                    self._process_chunk(buffer, sr)
                except Exception as e:
                    logger.error(f"[VoiceTranslator] chunk error: {e}")
                return np.array([], dtype=np.float32)
            return buffer

        if not ranges:
            # Речи нет (музыка/шум) — не даём Whisper галлюцинировать
            return buffer[-tail_keep:]

        start, end = ranges[0]
        s_i = int(start * sr)
        e_i = int(end * sr)

        if e_i - s_i < min_seg:
            # слишком короткий кусок — ждём продолжения
            if force or len(buffer) >= int(12.0 * sr):
                return buffer[-tail_keep:]
            return buffer

        # Реплика ещё может продолжаться — ждём следующего чанка
        if not force and e_i >= len(buffer) - tail_keep \
                and len(buffer) < int(12.0 * sr):
            return buffer

        seg = buffer[s_i:e_i].copy()
        try:
            self._process_chunk(seg, sr)
        except Exception as e:
            logger.error(f"[VoiceTranslator] chunk error: {e}")

        buffer = buffer[max(0, e_i - PAD_KEEP):]
        if len(buffer) > MAX_BUF:
            buffer = buffer[-MAX_BUF:]
        return buffer


    def _is_duplicate(self, en_text: str) -> bool:
        """Fuzzy deduplication — skip if >70% words overlap with last spoken."""
        if not self._last_spoken_en:
            return False
        a = set(self._last_spoken_en.lower().split())
        b = set(en_text.lower().split())
        if not a or not b:
            return False
        overlap = len(a & b) / max(len(a), len(b))
        return overlap > 0.7

    def _is_duplicate_ru(self, ru_text: str) -> bool:
        """Skip if the Russian translation overlaps our last spoken translation
        (catches our own TTS leaking back through the loopback)."""
        if not self._last_spoken_ru:
            return False
        a = set(self._last_spoken_ru.lower().split())
        b = set(ru_text.lower().split())
        if not a or not b:
            return False
        overlap = len(a & b) / max(len(a), len(b))
        return overlap > 0.7

    def _translate_async(self, text: str, src: str, dst: str):
        """Отдать текст на перевод в фоне и вернуть Future.

        Раньше было строго последовательно: Whisper -> ПЕРЕВОД (2 c) ->
        ОЗВУЧКА, и Whisper следующего куска ждал. Теперь перевод идёт
        параллельно с распознаванием следующего куска, поэтому задержка
        не суммируется.
        """
        fut = concurrent.futures.Future()
        if not self.translator:
            fut.set_result("")
            return fut
        self._tr_queue.put((text, src, dst, fut))
        self._ensure_tr_worker()
        return fut

    def _spawn(self, name, target, restart: bool = False):
        """Запустить фоновой поток так, чтобы он НЕ умирал молча.

        Раньше потоки создавались напрямую: любое исключение внутри
        (None вместо TTS, сбой OCR, и т.п.) проглатывалось интерпретатором,
        поток тихо умирал — и конвейер замолкал без единой записи в лог.
        Ровно это выглядело как «озвучка работает, потом тишина».

        restart=True — для потоков с очередью: после падения поднимаем их
        снова (не больше _restart_max раз), иначе одна ошибка навсегда
        выключала бы озвучку.
        """
        def _run():
            tries = 0
            while self._running:
                try:
                    target()
                    return
                except Exception:
                    tries += 1
                    logger.error(f"[{name}] поток упал (попытка {tries})",
                                 exc_info=True)
                    if not restart or tries >= self._restart_max:
                        logger.error(f"[{name}] поток остановлен")
                        return
                    time.sleep(1.0)
                    logger.info(f"[{name}] перезапуск потока")

        t = threading.Thread(target=_run, name=name, daemon=True)
        t.start()
        return t

    def _tts_worker_running(self) -> bool:
        """Жив ли поток озвучки.

        Раньше проверялся флаг _tts_worker_alive, но он сбрасывался при
        падении потока, и в секундный промежуток рестарта мог стартовать
        ВТОРОЙ воркер на той же очереди. Теперь смотрим на живость.
        """
        t = getattr(self, "_tts_thread", None)
        return bool(t is not None and t.is_alive())

    def _ensure_tts_worker(self):
        """Поднять поток озвучки, если он не работает."""
        if self._tts_worker_running():
            return
        self._tts_worker_alive = True
        self._tts_thread = self._spawn('tts', self._tts_worker,
                                       restart=True)

    def _ensure_tr_worker(self):
        """Поднять поток перевода, если он не работает."""
        t = getattr(self, "_tr_thread", None)
        if t is not None and t.is_alive():
            return
        self._tr_worker_alive = True
        self._tr_thread = self._spawn('translate', self._translate_worker,
                                      restart=True)

    def _translate_worker(self):
        while self._running:
            try:
                text, src, dst, fut = self._tr_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            except Exception:
                break
            try:
                # Озвучка: пословный офлайн-словарь недопустим
                # («Are you bringing the map?» -> «являются Ю ПРИВЕДЕНИЕ карта»)
                if self.translator is None:
                    fut.set_result("")
                    continue
                if hasattr(self.translator, "allow_offline_dict"):
                    self.translator.allow_offline_dict = False
                r = self.translator.translate(text, src=src, dst=dst)
                fut.set_result(r or "")
            except Exception as e:
                # warning, а не debug: раньше сбой перевода был виден
                # только при отладке, и реплики просто исчезали
                logger.warning(f"[VoiceTranslator] перевод не удался: {e}")
                if not fut.done():
                    fut.set_result("")

    def _sub_window_open(self) -> bool:
        """Есть ли смысл сверяться с субтитрами прямо сейчас.

        Сверка нужна только когда окно игры активно: иначе на экране
        чужое окно и «субтитры» — это его текст.
        """
        if not self._sub_enabled:
            return False
        r = self._sub_reader
        if r is None:
            return False
        try:
            return bool(r.is_active())
        except Exception:
            return False

    def _await_subtitles(self, timeout: float = 0.45) -> str:
        """Дождаться свежих субтитров, чтобы «сверить» речь с экраном.

        Голос и текст приходят разными путями: STT работает сразу, а OCR
        читает кадр раз в _sub_poll_sec. Без этого ожидания озвучка
        иногда уходила раньше, чем прочитался текст на экране, и сверка
        не срабатывала.

        Ждём недолго (по умолчанию 0.45 c): лучше озвучить по голосу, чем
        добавить задержку. Если окно игры не в фокусе — не жмём вовсе.
        """
        if not self._sub_enabled or not self._sub_window_open():
            return getattr(self, "_sub_pending", "") or ""
        deadline = time.time() + max(0.0, timeout)
        while True:
            sub = getattr(self, "_sub_pending", "") or ""
            if sub and (time.time() - getattr(self, "_sub_pending_at", 0.0)) \
                    <= self._sub_max_age:
                return sub
            if time.time() >= deadline:
                return sub
            if not self._sub_window_open():
                return sub
            time.sleep(0.04)

    def _resolve_text(self, whisper_text: str):
        """Слить распознанную речь с субтитрами на экране.

        Если субтитры свежие и пересекаются с речью — берём их: в них точная
        разметка и кавычки, а Whisper часто ломает имена. Если речь есть,
        а субтитры чужие (другой персонаж / следующая реплика) — оставляем
        распознавание. Так ничего не теряется и не подменяется.
        """
        sub = getattr(self, "_sub_pending", "") or ""
        if not sub:
            return whisper_text, False
        if (time.time() - getattr(self, "_sub_pending_at", 0.0)) \
                > self._sub_max_age:
            return whisper_text, False
        a = set(self._normalize_text(whisper_text).split())
        b = set(self._normalize_text(sub).split())
        if not a or not b:
            return whisper_text, False
        overlap = len(a & b) / max(len(a), len(b))
        if overlap < 0.34:
            return whisper_text, False
        return sub, True

    def set_subtitles(self, enabled: bool, settings=None,
                      region_ratio=None, require_foreground: bool = True,
                      fast_ocr: bool = True):
        """Включить работу с субтитрами на экране.

        Даёт два эффекта: фолбэк, когда голоса нет, но субтитры есть, и
        уточнение текста, когда голос и субтитры идут одновременно.

        region_ratio=None -> берётся область по умолчанию из SubtitleReader
        (она подобрана так, чтобы не задевать панель задач Windows).
        require_foreground -> читать субтитры только когда окно игры в фокусе,
        иначе в кадр попадёт любое другое окно поверх игры.
        """
        self._sub_enabled = bool(enabled)
        self._sub_settings = settings
        self._sub_region = region_ratio
        self._sub_require_fg = bool(require_foreground)
        self._sub_fast_ocr = bool(fast_ocr)
        if self._sub_enabled and self._running and self._sub_thread is None:
            self._start_subtitle_thread()

    def _start_subtitle_thread(self):
        if self._sub_thread is not None and self._sub_thread.is_alive():
            return
        # _spawn сам создаёт и запускает поток. Раньше здесь стоял лишний
        # .start() — при повторном старте он бросал
        # «RuntimeError: threads can only be started once» и перевод
        # субтитров не включался вообще.
        self._sub_thread = self._spawn('subtitles', self._subtitle_loop,
                                       restart=True)
        logger.info("[VoiceTranslator] чтение субтитров включено")

    @staticmethod
    def _load_overlay_regions(path: str = "regions.json"):
        """Зелёные рамки из GUI: список {x, y, width, height}.

        Пользователь рисует рамку, а мы сканируем субтитры внутри неё:
        так из кадра уходят HUD, панель задач и края экрана. Файл
        перечитывается, поэтому перерисовка рамки подхватывается на лету.
        """
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return []
        if isinstance(data, dict):
            data = data.get("regions", [])
        out = []
        for r in (data or []):
            if not isinstance(r, dict):
                continue
            if r.get("locked"):
                continue
            try:
                w, h = int(r.get("width", 0)), int(r.get("height", 0))
                if w > 30 and h > 12:
                    out.append({"x": int(r.get("x", 0)),
                                "y": int(r.get("y", 0)),
                                "width": w, "height": h})
            except Exception:
                continue
        return out

    def _subtitle_loop(self):
        """Фоновая OCR-разведка субтитров.

        Держим один свежий текст: им уточняются распознанные реплики. Если
        голоса нет дольше _sub_fallback_after секунд, а субтитры есть —
        озвучиваем их: в сценах, где реплику не слышно, пользователь всё
        равно получает перевод.
        """
        try:
            from subtitle_reader import SubtitleReader
        except Exception as e:
            logger.warning(f"[VoiceTranslator] subtitle_reader unavailable: {e}")
            return
        try:
            self._sub_reader = SubtitleReader(
                getattr(self, "_sub_settings", None),
                getattr(self, "_sub_region", None),
                target_pid=getattr(self, "_target_pid", None),
                require_foreground=getattr(self, "_sub_require_fg", True),
                fast_ocr=getattr(self, "_sub_fast_ocr", True))
        except Exception as e:
            logger.warning(f"[SubtitleReader] init failed: {e}")
            return

        spoken = ""
        cand = ""
        hits = 0
        # Рамки подхватываем и обновляем: пользователь может перерисовать
        # зелёную рамку прямо во время работы.
        regions_checked_at = 0.0
        while self._running and self._sub_enabled:
            time.sleep(self._sub_poll_sec)
            if time.time() - regions_checked_at > 2.0:
                regions_checked_at = time.time()
                try:
                    self._sub_reader.set_regions(
                        self._load_overlay_regions())
                except Exception as e:
                    logger.debug(f"[VoiceTranslator] рамки не прочитаны: {e}")
            try:
                # standalone=True: озвучиваем сами, голоса нет — нужно
                # минимум 2 слова, чтобы не прочитать пункт меню.
                text = self._sub_reader.read(standalone=True)
            except Exception as e:
                logger.debug(f"[SubtitleReader] read error: {e}")
                continue
            if not text:
                # Субтитров на экране нет. Кандидата держим: если текст
                # просто не изменился, read() вернёт "" — это НЕ значит,
                # что субтитры исчезли (см. current()).
                if cand:
                    cur = self._sub_reader.current(max_age=self._sub_max_age)
                    if cur and self._normalize_text(cur) == \
                            self._normalize_text(cand):
                        hits += 1          # тот же текст держится — стабилен
                    else:
                        cand, hits = "", 0
            else:
                # Свежие субтитры — кандидат на уточнение для следующего куска.
                self._sub_pending = text
                self._sub_pending_at = time.time()
                # Текст должен ПРОДЕРЖАТЬСЯ: разовый кадр — это чаще всего
                # элемент интерфейса, а не реплика. Говорим только после
                # двух одинаковых чтений подряд.
                if self._normalize_text(text) == self._normalize_text(cand):
                    hits += 1
                else:
                    cand, hits = text, 1

            if hits < 1:
                continue

            # ── Решает решатель, а не жёсткие правила ──
            # Он смотрит на всё сразу: есть ли голос, сколько слов,
            # есть ли знаки конца фразы, где строка на экране, держится ли
            # текст, похож ли он на «живую» речь. Так работает в любых
            # играх: субтитры могут быть где угодно и состоять из одного
            # слова, а интерфейс — наоборот из длинных фраз.
            try:
                age = time.time() - self._sub_reader.current_age()
            except Exception:
                age = 0.0
            action, _score, why = self._decider.decide(
                cand,
                has_voice=time.time() - self._last_voice_at < 1.2,
                stability=hits,
                screen_pos=1.0,
                age=age,
            )
            if action != "speak":
                self._decider.log(action, _score, why, cand)
                continue

            # Фолбэк: голоса нет, а текст на экране есть.
            quiet = time.time() - self._last_voice_at
            if quiet < self._sub_fallback_after:
                continue
            if spoken and self._looks_like_own_voice(cand):
                continue
            if not self.tts or not self.translator:
                continue
            spoken = cand
            if self.on_heard:
                try:
                    self.on_heard(cand, "OCR", "")
                except Exception:
                    pass
            fut = self._translate_async(cand, "auto", self._dst_lang)
            self._tts_queue.put((cand, fut, None, "OCR", ""))
            self._ensure_tts_worker()
            # произнесли — ждём, пока текст сменится
            cand, hits = "", 0

    def _process_chunk(self, audio: np.ndarray, sr: int):
        """Одна целая реплика: STT -> анализ голоса -> перевод -> TTS.

        Реплика уже вырезана по паузам (Silero VAD), поэтому Whisper получает
        законченную фразу, а не огрызок. Текст сначала уходит в on_heard/
        on_text (текст в интерфейс), и только затем в очередь озвучки.
        """
        SR = 16000
        try:
            if sr != SR:
                import scipy.signal
                audio = scipy.signal.resample(audio, int(len(audio) * SR / sr))
                sr = SR

            audio = np.asarray(audio, dtype=np.float32)
            if len(audio) < int(0.25 * SR):
                return

            peak = float(np.abs(audio).max()) if len(audio) else 0.0
            if peak <= 0.0001:
                return
            if peak > 0:
                audio = audio / peak * 0.95

            # Язык: 'auto' -> определяем сами (иначе Whisper переведёт нашу
            # собственную русскую речь на en и анти-эхо по тексту не сработает)
            forced_lang = self._src_lang if self._src_lang in ("en", "ja") else None
            if self._src_lang in ("auto", "", None) or forced_lang is None:
                forced_lang = None
            else:
                forced_lang = self._src_lang

            offline = _get_offline()
            net = _get_net()

            # ══════ WHISPER ══════
            segments, _info = self._whisper_model.transcribe(
                audio,
                language=forced_lang,
                # beam_size=1 (жадный) вместо 5: в потоковом режиме куски
                # режутся по естественным паузам, точность луча уже не нужна,
                # а выигрыш по скорости — в разы. Это и есть основная причина
                # «долгой задержки»: раньше луч из 5 просчитывался на всю
                # реплику ДО начала озвучки.
                beam_size=self._stt_beam_size,
                word_timestamps=False,
                vad_filter=False,             # реплика уже вырезана по паузам
                condition_on_previous_text=False,   # без "залипания" на прошлом тексте
                no_speech_threshold=0.6,
                log_prob_threshold=-1.0,
                compression_ratio_threshold=2.4,
                temperature=[0.0, 0.2, 0.4],
            )

            detected = getattr(_info, "language", None) or forced_lang or self._src_lang
            if self._skip_own_lang and detected and detected == self._dst_lang:
                logger.debug("[VoiceTranslator] skip: язык речи == язык озвучки "
                             f"({detected}) — это наш собственный голос")
                return

            lang_name = self._LANG_NAMES.get(detected, detected)
            src_lang = detected
            is_voice = True
            if self.on_voice_detected:
                try:
                    self.on_voice_detected(is_voice)
                except Exception:
                    pass

            for seg in segments:
                text = (seg.text or "").strip()
                if not text or len(text) < 2:
                    continue

                # ── Фильтры доверия: музыка/шум часто дают "фразы" ──
                nsp = getattr(seg, "no_speech_prob", 0.0) or 0.0
                alp = getattr(seg, "avg_logprob", 0.0) or 0.0
                cr = getattr(seg, "compression_ratio", 0.0) or 0.0
                if nsp > 0.6:
                    logger.debug(f"[VoiceTranslator] skip (no_speech={nsp:.2f}): {text[:40]}")
                    continue
                if alp < -1.0:
                    logger.debug(f"[VoiceTranslator] skip (logprob={alp:.2f}): {text[:40]}")
                    continue
                if cr > 2.4:
                    logger.debug(f"[VoiceTranslator] skip (compression={cr:.2f}): {text[:40]}")
                    continue

                # ── Эхо: это похоже на нашу собственную речь? ──
                if self._looks_like_own_voice(text):
                    logger.debug(f"[VoiceTranslator] skip (own echo): {text[:40]}")
                    continue
                if self._is_duplicate(text):
                    continue

                # Слияние с субтитрами на экране (если включено).
                # Перед сверкой ДОЖИДАЕМСЯ свежих субтитров: иначе озвучка
                # успевала уйти раньше, чем OCR прочитал экран, и сверка
                # не срабатывала. Ожидание короткое и только при активном
                # окне игры — на задержку оно почти не влияет.
                if self._sub_enabled:
                    self._await_subtitles()
                text, fused = self._resolve_text(text)

                # ── Анализ голоса именно этого сегмента ──
                from voice_analyzer import analyze_voice, get_voice_label
                seg_audio = audio
                s_t = getattr(seg, "start", None)
                e_t = getattr(seg, "end", None)
                if s_t is not None and e_t is not None and e_t > s_t:
                    s_i = max(0, int(s_t * SR))
                    e_i = min(len(audio), int(e_t * SR))
                    if e_i - s_i >= int(0.3 * SR):
                        seg_audio = audio[s_i:e_i]
                try:
                    voice_info = analyze_voice(seg_audio, SR)
                    speaker_label = get_voice_label(voice_info)
                except Exception:
                    voice_info = None
                    speaker_label = ""

                self._last_voice_at = time.time()

                # ── Текст сразу в интерфейс: оригинал не ждёт перевода ──
                if self.on_heard:
                    try:
                        self.on_heard(text, lang_name, speaker_label)
                    except Exception:
                        pass
                if self.on_text:
                    try:
                        self.on_text(text, "", lang_name, speaker_label)
                    except Exception:
                        pass

                # ── Перевод уходит в фон, STT не блокируется ──
                fut = self._translate_async(text, src_lang, self._dst_lang)
                tts_voice = self._pick_tts_voice(voice_info)

                if self.tts:
                    # (en, future, voice, lang, speaker) — язык и голос
                    # едём вместе с репликой, иначе при нескольких кусках
                    # в очереди подпись относилась бы к чужой реплике.
                    self._tts_queue.put((text, fut, tts_voice, src_lang,
                                         speaker_label))
                    self._ensure_tts_worker()

                self._last_spoken_en = text
                self._pending_fut = fut
                self._pending_text = text
                self._pending_lang = lang_name
                self._pending_speaker = speaker_label
                if fused:
                    logger.debug("[VoiceTranslator] текст уточнён субтитрами")

            # Реплика обработана — интерфейс может перенести её в отдельную строку
            self._flush_pending()

        except Exception as e:
            logger.error(f"[VoiceTranslator] Process error: {e}")

    def _pick_tts_voice(self, voice_info) -> str:
        """Выбрать мультилингвальный голос TTS по полу/возраста говорящего.
        
        Используем мультилингвальные модели (Brian/Ava/Andrew/Emma/Ana),
        которые говорят И на английском И на русском одним голосом.
        Это обеспечивает единый тембр для обоих языков.
        """
        from voice_analyzer import VoiceFeatures
        if not isinstance(voice_info, VoiceFeatures):
            return self._multilingual_voice("male")

        gender = voice_info.gender

        if gender == "male":
            return self._multilingual_voice("male")
        elif gender == "female":
            return self._multilingual_voice("female")
        elif gender == "child":
            return self._multilingual_voice("child")
        else:
            return self._multilingual_voice("male")

    def _multilingual_voice(self, gender: str) -> str:
        """Мультилингвальный голос по полу. Один голос для EN+RU."""
        VOICES = {
            "male":   "en-US-BrianMultilingualNeural",   # Мужской, EN+RU+DE+FR+ES
            "female": "en-US-AvaMultilingualNeural",     # Женский, EN+RU+DE+FR+ES
            "child":  "en-US-EmmaMultilingualNeural",     # Ребёнок/молодой, EN+RU+DE+FR+ES
        }
        return VOICES.get(gender, VOICES["male"])

    def _apply_net_tts_mode(self):
        """Switch TTS engine by network state: Edge when fast, RHVoice otherwise."""
        if not self.tts:
            return
        try:
            fast = _get_net().is_fast()
            # Respect user's explicit voice choice (piper/rhvoice/silero/persona)
            # Only auto-switch if user is on edge or sapi
            if self.tts.voice_type in ("edge", "sapi"):
                if fast:
                    if self._online_voice is None and self.tts.voice_type == "edge":
                        self._online_voice = self.tts.voice
                    if self.tts.voice_type != "edge":
                        self.tts.set_voice(self._online_voice or "ru-RU-DmitryNeural")
                else:
                    if self.tts.voice_type != "rhvoice":
                        self.tts.set_voice("rhvoice:Victoria")
        except Exception:
            pass

    @staticmethod
    def _split_sentences(text: str):
        """Split translated text into sentences for incremental TTS."""
        parts = re.split(r'([.!?…]+\s*)', text)
        out = []
        buf = ""
        for p in parts:
            buf += p
            if re.search(r'[.!?…]+\s*$', p) or p == parts[-1]:
                if buf.strip():
                    out.append(buf.strip())
                buf = ""
        if buf.strip():
            out.append(buf.strip())
        return out or [text]

    def _speak_safe(self, text: str, tts_voice: str = None):
        """Поставить реплику в очередь озвучки (не блокируя STT-поток).

        text может быть строкой (перевод уже готов) или кортежем
        (en_text, future, voice) из потокового конвейера.
        """
        if not self.tts:
            return
        if isinstance(text, str):
            if not text or not text.strip():
                return
            item = (text, None, tts_voice)
        else:
            item = tuple(text)

        # Очередь озвучки НЕ обрезается: пользователь просил, чтобы всё
        # произносилось полностью. Раньше при qsize() > 2 старые реплики
        # молча выбрасывались — половина диалогов просто не озвучивалась.
        # Если очередь растёт неестественно долго, сообщаем об этом, но
        # ничего не теряем.
        if self._tts_queue.qsize() > 8:
            logger.warning(f"[VoiceTranslator] очередь озвучки переполнена "
                           f"({self._tts_queue.qsize()}) — реплики будут "
                           f"произнесены с задержкой")

        self._tts_queue.put(item)

        self._ensure_tts_worker()

    # Маркеры недоступности провайдера: «[Zen failed: ...]»,
    # «[orcarouter removed: endpoint unavailable]», «[Google failed]».
    # Раньше проверка была startswith("[") — но это отбрасывало и НОРМАЛЬНЫЙ
    # перевод, который просто начинается со скобки («[Шёпот] Привет»).
    _FAIL_MARK_RE = re.compile(
        r"^\s*\[[^\]]{0,160}\b(?:failed|error|unavailable|removed|disabled|"
        r"not\s+available)\b[^\]]{0,160}\]\s*$",
        re.IGNORECASE)

    @classmethod
    def _looks_like_error(cls, text: str) -> bool:
        """Похоже ли это на служебный маркер вместо перевода?"""
        if not text or not text.strip():
            return True
        return bool(cls._FAIL_MARK_RE.match(text.strip()))

    def _tts_worker(self):
        """Фоновая озвучка: реплики по очереди, каждая — одним высказыванием.

        Элемент очереди: (en_text, future|None, voice, lang, speaker).
        Перевод приходит Future'ом, поэтому к моменту озвучки он обычно
        уже готов — а если нет, ждём здесь, а не в потоке распознавания.

        Пока идёт озвучка, выставляется _tts_playing: STT в это время
        полностью игнорирует входящий звук, поэтому система не слышит саму
        себя и не зацикливается.
        """
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            while self._running:
                try:
                    item = self._tts_queue.get(timeout=0.5)
                except queue.Empty:
                    continue
                except Exception:
                    break

                # Инициализируем ВСЕ поля: раньше ветка 2-элементного кортежа
                # не задавала ru_text, и озвучка падала с UnboundLocalError —
                # реплики переводились, но НИКОГДА не произносились.
                text = tts_voice = en_text = ru_text = fut = None
                lang = speaker = ""
                if isinstance(item, tuple):
                    if len(item) == 5:
                        en_text, fut, tts_voice, lang, speaker = item
                    elif len(item) == 3:
                        en_text, fut, tts_voice = item
                    elif len(item) == 2:
                        a, b = item
                        if isinstance(a, concurrent.futures.Future):
                            en_text, fut, tts_voice = None, a, b
                        else:
                            text, tts_voice = a, b
                    else:
                        text = item[0]
                        tts_voice = item[1] if len(item) > 1 else None
                else:
                    text = item

                # ── Ждём перевод (уже мог быть готов) ──
                if fut is not None:
                    try:
                        ru_text = fut.result(timeout=25)
                    except Exception as e:
                        logger.debug(f"[VoiceTranslator] fut error: {e}")
                        ru_text = ""
                    if self._looks_like_error(ru_text):
                        ru_text = ""
                        if not getattr(self, "_warned_no_translate", False):
                            self._warned_no_translate = True
                            logger.warning(
                                "[VoiceTranslator] Сетевой перевод недоступен — "
                                "озвучка пропущена (показан оригинал). "
                                "Проверьте: python test_translation_providers.py")
                elif text:
                    ru_text = text
                    text = None

                if ru_text:
                    self._recent_ru.append(ru_text)
                    self._last_spoken_ru = ru_text
                    if en_text and self.on_text:
                        try:
                            # язык/подпись берём из СВОЕГО элемента очереди
                            self.on_text(en_text, ru_text,
                                         lang or getattr(self,
                                                         "_pending_lang", ""),
                                         speaker or getattr(
                                             self, "_pending_speaker", ""))
                        except Exception:
                            pass

                if not ru_text:
                    continue

                # TTS мог быть выключен (--no-tts) или снят уже после
                # постановки в очередь. Без этой проверки воркер падал бы
                # на self.tts.voice и озвучка замолкала навсегда.
                if self.tts is None:
                    logger.debug("[tts] озвучка выключена — пропускаю реплику")
                    continue

                game_vol = None
                old_voice = old_type = None
                # Захват только процесса игры = наш голос в запись не попадёт.
                # Проверяем, что изоляция РЕАЛЬНО работает (идут пакеты), а не
                # просто что объект захвата был создан: иначе анти-петля
                # отключилась бы при неработающей изоляции.
                isolated = bool(self._game_audio
                                and self._game_audio.is_isolated())
                try:
                    if self._game_audio and getattr(self._game_audio, "_game_pid", None):
                        try:
                            game_vol = self._game_audio.get_game_volume()
                            self._game_audio.set_game_volume(self._duck_volume)
                        except Exception:
                            game_vol = None

                    # Флаг на всё время реальной озвучки
                    self._tts_start = time.time()
                    self._tts_playing = True
                    if isolated:
                        self._echo_until = 0.0

                    if tts_voice:
                        old_voice = self.tts.voice
                        old_type = self.tts.voice_type
                        self.tts.set_voice(tts_voice)

                    rate_before = getattr(self.tts, "rate", 0)
                    if self._stt_tts_rate:
                        try:
                            self.tts.rate = self._stt_tts_rate
                        except Exception:
                            pass

                    # ── Озвучка с попаданием в паузы/акценты ──
                    # Длинный перевод режем на предложения и произносим
                    # отдельными высказываниями: паузы получаются там же,
                    # где они в оригинале, и озвучка не читает «одним
                    # потоком» без естественных остановок.
                    parts = [ru_text]
                    if len(ru_text) > 70:
                        parts = self._split_sentences(ru_text)

                    for part in parts:
                        part = (part or "").strip()
                        if not part:
                            continue
                        if self._dual_mode and en_text and en_text != part:
                            loop.run_until_complete(asyncio.wait_for(
                                self.tts.speak(en_text), timeout=20))
                        loop.run_until_complete(asyncio.wait_for(
                            self.tts.speak(part), timeout=20))
                        if len(parts) > 1:
                            # короткая пауза между предложениями
                            loop.run_until_complete(asyncio.sleep(0.12))

                    if self._stt_tts_rate:
                        try:
                            self.tts.rate = rate_before
                        except Exception:
                            pass

                except Exception as e:
                    logger.error(f"[VoiceTranslator] TTS error: {e}")
                finally:
                    # Если изоляция по процессу не сработала, глушим STT до конца
                    # нашего звука + запас на задержку loopback/рендера.
                    if not isolated:
                        play_start = getattr(self.tts, "playback_started_at", 0.0) or 0.0
                        play_end = getattr(self.tts, "playback_ended_at", 0.0) or 0.0
                        if play_end <= 0 or play_end < play_start:
                            play_end = time.time()
                        self._tts_playing = True
                        self._echo_until = play_end + max(2.5, self._echo_grace_sec)
                        logger.debug(f"[VoiceTranslator] echo block until "
                                     f"{self._echo_until:.2f}s")
                    else:
                        self._tts_playing = False
                        self._echo_until = 0.0
                    if tts_voice and old_voice:
                        try:
                            self.tts.voice = old_voice
                            self.tts.voice_type = old_type
                        except Exception:
                            pass
                    if game_vol is not None and self._game_audio:
                        try:
                            self._game_audio.set_game_volume(game_vol)
                        except Exception:
                            pass
        finally:
            try:
                loop.close()
            except Exception:
                pass
            self._tts_playing = False
            self._tts_worker_alive = False


# Global instance
voice_translator = VoiceTranslator()
