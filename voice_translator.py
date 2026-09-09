# -*- coding: utf-8 -*-
"""
Voice Translator — STT -> Translate -> TTS pipeline.
Captures game audio via WASAPI loopback, translates English to Russian voice.
"""
import asyncio
import logging
import queue
import re
import threading
import time

import numpy as np

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

    def __init__(self, tts_engine=None, translator=None):
        self.tts = tts_engine
        self.translator = translator
        self._whisper_model = None
        self._running = False
        self._src_lang = "auto"
        self._dst_lang = "ru"
        self.on_text = None
        self.on_heard = None
        self._target_pid = None
        self._model_name = "tiny"          # fast, low-latency default
        self._chunk_duration = 1.0         # seconds of audio per transcription
        self._audio_queue = queue.Queue()
        self._tts_queue = queue.Queue()
        self._tts_worker_alive = False
        self._game_audio = None
        self._last_spoken_en = ""
        self._last_spoken_ru = ""
        self._tts_lock = threading.Lock()
        self._echo_until = 0.0
        self._tts_start = 0.0
        self._stt_tts_rate = 0
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
        # Load Whisper in a BACKGROUND thread so the GUI stays responsive.
        # Loading the model synchronously on the UI thread froze the app
        # (torch spike) — now the UI never blocks on model load.
        threading.Thread(target=self._load_whisper_bg, daemon=True).start()
        logger.info("[VoiceTranslator] Started (Whisper loading in background)")

    def _load_whisper_bg(self):
        if self._ensure_whisper():
            threading.Thread(target=self._process_loop, daemon=True).start()
            logger.info("[VoiceTranslator] Whisper ready, STT processing started")
        else:
            logger.error("[VoiceTranslator] Whisper failed to load; STT inactive")

    def stop(self):
        self._running = False
        if self._flush_timer:
            self._flush_timer.cancel()
            self._flush_timer = None
        if self._game_audio:
            if getattr(self, '_audio_cb', None):
                self._game_audio.remove_audio_callback(self._audio_cb)
                self._audio_cb = None
            if not self._game_audio._audio_callbacks:
                self._game_audio.stop()
        logger.info("[VoiceTranslator] Stopped")

    def _process_loop(self):
        """Process audio chunks from queue — short chunks for low latency."""
        chunk_duration = self._chunk_duration
        buffer = np.array([], dtype=np.float32)
        sr = 16000

        while self._running:
            try:
                audio, new_sr = self._audio_queue.get(timeout=1.0)
                sr = new_sr
                # Skip audio captured while our own TTS is playing into the
                # loopback device (avoids an echo/feedback loop). Time-based
                # so STT resumes as soon as our voice finishes.
                if time.time() < self._echo_until:
                    buffer = np.array([], dtype=np.float32)
                    continue
                buffer = np.concatenate([buffer, audio])
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"[VoiceTranslator] Loop error: {e}")
                continue

            min_samples = int(sr * chunk_duration)
            if len(buffer) >= min_samples:
                chunk = buffer[:min_samples]
                buffer = buffer[min_samples:]
                self._process_chunk(chunk, sr)

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

    def _process_chunk(self, audio: np.ndarray, sr: int):
        """STT -> Translate -> TTS — true word-by-word streaming."""
        try:
            if sr != 16000:
                import scipy.signal
                audio = scipy.signal.resample(audio, int(len(audio) * 16000 / sr))

            if len(audio) < 800:
                return

            peak = np.abs(audio).max()
            if peak > 0:
                audio = audio / peak * 0.95

            # Whisper always gets the exact source language (en or ja)
            whisper_lang = self._src_lang if self._src_lang in ("en", "ja") else "en"
            segments, info = self._whisper_model.transcribe(
                audio,
                language=whisper_lang,
                beam_size=5,
                word_timestamps=True,
                vad_filter=False,
            )

            detected_lang = whisper_lang
            lang_name = self._LANG_NAMES.get(detected_lang, detected_lang)

            self._apply_net_tts_mode()

            src_lang = detected_lang or self._src_lang
            offline = _get_offline()
            net = _get_net()
            last_seg_text = ""

            for seg in segments:
                last_seg_text = seg.text.strip()
                words = getattr(seg, 'words', None) or []
                if words:
                    for w in words:
                        word = w.word.strip()
                        if not word or len(word) < 1:
                            continue

                        if self._is_duplicate(word):
                            continue

                        if self.on_heard:
                            try:
                                self.on_heard(word, lang_name)
                            except Exception as e:
                                logger.warning(f"[VoiceTranslator] on_heard callback error: {e}")

                        ru = ""
                        if net.is_fast() and self.translator:
                            try:
                                ru = self.translator.translate(word, src=src_lang, dst=self._dst_lang)
                            except Exception as e:
                                logger.debug(f"[VoiceTranslator] Online translate failed: {e}")
                                ru = ""

                        if not ru or not ru.strip() or ru.startswith("[Translation"):
                            if src_lang == "ja":
                                ru = offline.translate_ja(word)
                            else:
                                ru = offline.translate(word)

                        if not ru or not ru.strip():
                            ru = word

                        if self.on_text:
                            self.on_text(word, ru, lang_name)

                        if self.tts and ru:
                            self._speak_safe(ru)

                        self._last_spoken_en = word
                        self._last_spoken_ru = ru
                else:
                    en_text = last_seg_text
                    if not en_text or len(en_text) < 2:
                        continue
                    if self._is_duplicate(en_text):
                        continue

                    if self.on_heard:
                        try:
                            self.on_heard(en_text, lang_name)
                        except Exception as e:
                            logger.warning(f"[VoiceTranslator] on_heard callback error: {e}")

                    ru = ""
                    if net.is_fast() and self.translator:
                        try:
                            ru = self.translator.translate(en_text, src=src_lang, dst=self._dst_lang)
                        except Exception as e:
                            logger.debug(f"[VoiceTranslator] Online translate failed: {e}")
                            ru = ""

                    if not ru or not ru.strip() or ru.startswith("[Translation"):
                        if src_lang == "ja":
                            ru = offline.translate_ja(en_text)
                        else:
                            ru = offline.translate(en_text)

                    if not ru or not ru.strip():
                        ru = en_text

                    if self.on_text:
                        self.on_text(en_text, ru, lang_name)

                    if self.tts and ru:
                        self._speak_safe(ru)

                    self._last_spoken_en = en_text
                    self._last_spoken_ru = ru

            if last_seg_text and re.search(r'[.!?…。！？]\s*$', last_seg_text):
                self._flush_pending()
            elif last_seg_text:
                self._schedule_flush()

        except Exception as e:
            logger.error(f"[VoiceTranslator] Process error: {e}")

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

    def _speak_safe(self, text: str):
        """Queue text for TTS playback (non-blocking, VTuber-style).

        Text is added to a queue and spoken sequentially. No translations
        are dropped — they wait in the queue and play as soon as the
        previous one finishes.
        """
        if not self.tts:
            return

        self._tts_queue.put(text)

        if not getattr(self, "_tts_worker_alive", False):
            self._tts_worker_alive = True
            threading.Thread(target=self._tts_worker, daemon=True).start()

    def _tts_worker(self):
        """Background worker: pulls text from queue and speaks it sequentially."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        while True:
            try:
                text = self._tts_queue.get(timeout=0.5)
            except Exception:
                break

            game_vol = 1.0
            try:
                if self._game_audio and self._game_audio._game_pid:
                    try:
                        game_vol = self._game_audio.get_game_volume()
                        self._game_audio.set_game_volume(0.15)
                    except Exception:
                        pass

                est = min(30.0, max(2.0, len(text) * 0.08 + 2.0))
                self._echo_until = time.time() + est

                async def _speak():
                    await asyncio.wait_for(self.tts.speak(text), timeout=10)

                loop.run_until_complete(_speak())
            except Exception as e:
                logger.error(f"[VoiceTranslator] TTS error: {e}")
            finally:
                self._echo_until = 0.0
                if self._game_audio and self._game_audio._game_pid:
                    try:
                        self._game_audio.set_game_volume(game_vol)
                    except Exception:
                        pass

        loop.close()
        self._tts_worker_alive = False


# Global instance
voice_translator = VoiceTranslator()
