"""
Live OCR сканер для реального времени — TTS стриминг поверх ScreenScanner.
Наследует ScreenScanner (захват/OCR/детекция), добавляет TTS pipeline.
"""
import asyncio
import collections
import logging
import os
import re
import time
from typing import Optional, Callable

from scanner import ScreenScanner

logger = logging.getLogger(__name__)


class LiveScanner(ScreenScanner):
    """Сканер реального времени с TTS стримингом и chunk+clear режимом."""

    def __init__(self, ocr_engine, settings, tts_engine=None):
        super().__init__(ocr_engine, settings)
        self.tts_engine = tts_engine

        # Режим голоса: False = один голос, True = мультиголос
        self.multi_voice = settings.get("tts.multi_voice", False)

        # TTS стриминг
        self._tts_streaming = False
        self._tts_queue = asyncio.Queue()
        self._tts_task = None
        self._last_tts_text = ""
        self._tts_paused = False

        # Sync mode: сканирование ждёт пока TTS договорит
        self._sync_mode = False

        # Sentence streaming: читает по предложениям, стирает прочитанное
        self._sentence_mode = False
        self._pending_sentences = []
        self._spoken_offset = 0
        self.on_text_spoken: Optional[Callable[[str], None]] = None
        self._audio_to_text = {}

        # Callback'и (переопределяем для TTS)
        self.on_text_detected: Optional[Callable[[str], None]] = None
        self.on_scan_started: Optional[Callable[[], None]] = None
        self.on_scan_stopped: Optional[Callable[[], None]] = None

        # Построчный режим
        self._line_by_line_mode = False
        self._spoken_lines = set()
        self._last_ocr_text = ""

        # Chunk+Clear режим: OCR → буфер → TTS читает по порядку → очистка
        self._chunk_clear_mode = False
        self._chunk_buffer = collections.deque()
        self._chunk_ready = asyncio.Event()
        self._spoken_chunks = set()

        # Punctuation stream: накапливает до запятой/точки → перевод → TTS → очистка
        self._punct_mode = False
        self._punct_buffer = ""
        self._punct_last_update = 0.0
        self._punct_idle_threshold = 5.0  # сек без изменений → пауза
        self._punct_idle = False
        self.on_translate_chunk: Optional[Callable[[str], str]] = None  # callback: text -> translation

        # Fast frame diff for change detection
        self._prev_frame = None
        self._last_spoken_normalized = ""

        # Continuous mode: True = непрерывный, False = ручной
        self.continuous_mode = False
        self._last_text_hash = None

        # OCR логирование ( live mode )
        self._ocr_log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ocr_logs")
        os.makedirs(self._ocr_log_dir, exist_ok=True)
        self._ocr_log_counter = 0

        logger.info("LiveScanner инициализирован с TTS стримингом")

    # ==================== TEXT VALIDATION ====================

    @staticmethod
    def _is_valid_text(text: str) -> bool:
        """Проверка текста от мусора OCR: кракозябры, таймстемпы, шум, кнопки GUI."""
        if not text or not text.strip():
            return False
        t = text.strip()
        if len(t) < 3:
            return False
        # Заменяющие символы (U+FFFD) — признак сломанной кодировки
        if '\ufffd' in t:
            return False
        letters = sum(1 for c in t if c.isalpha())
        if letters == 0:
            return False
        # Слишком много цифр — таймстемпы, номера, шум
        digits = sum(1 for c in t if c.isdigit())
        if digits > letters:
            return False
        # Таймстемпы: 2026-06-22, 18:11:16 и т.д.
        if re.search(r'\d{4}[-/.]\d{2}[-/.]\d{2}', t):
            return False
        if re.search(r'\d{1,2}:\d{2}(:\d{2})?', t):
            return False
        # Спецсимволов > 40% от букв — шум OCR
        special = sum(1 for c in t if not c.isalnum() and not c.isspace() and c not in '.,!?-:;\'\"()')
        if special > letters * 0.4:
            return False
        # Слишком короткие слова подряд — мусор ("шу^ неу шв шее")
        words = t.split()
        if words and all(len(w) <= 2 for w in words):
            return False
        return True

    # ==================== HOOK: NEW TEXT → TTS ====================

    def _on_new_text(self, text: str):
        """Хук — один скрин → одна озвучка всего текста."""
        if not text or not self.tts_engine:
            return
        if not self._is_valid_text(text):
            return

        # Speak весь текст целиком (async)
        try:
            if self.tts_engine.is_playing:
                self.tts_engine.force_stop()
            asyncio.create_task(self.tts_engine.speak(text))
            logger.info(f"[TTS] Озвучка: {text[:60]}...")
        except Exception as e:
            logger.error(f"[TTS] Ошибка озвучки: {e}")

        # Веб-поток субтитров
        try:
            from web_tts_server import push_subtitle
            push_subtitle(text)
        except Exception:
            pass

    # ==================== SCAN LOOP OVERRIDE ====================

    async def _scan_loop(self):
        """Строгий цикл: скрин → OCR → озвучка (ждать конца) → задержка → следующий скрин."""
        logger.info("Запуск цикла сканирования (LiveScanner)")

        while self.running:
            if self.paused:
                await asyncio.sleep(0.1)
                continue

            try:
                # Захват экрана
                img = self.ocr_engine.capture_region()
                if img is None:
                    await asyncio.sleep(0.1)
                    continue

                # В ручном режиме: пропускаем если скрин не изменился
                if not self.continuous_mode:
                    if not self._screenshot_changed(img):
                        await asyncio.sleep(0.1)
                        continue

                # OCR
                t_scan_start = time.time()
                result = await self.ocr_engine.recognize_async(img)
                text = result[0] if isinstance(result, tuple) else result
                confidence = result[1] if isinstance(result, tuple) and len(result) > 1 else 0.0
                scan_seconds = time.time() - t_scan_start

                if text and text.strip():
                    normalized = ' '.join(text.strip().lower().split())

                    # Punctuation stream mode: accumulate → translate → speak → clear
                    if self._punct_mode:
                        if self.on_text_detected:
                            self.on_text_detected(text)
                        self._process_punct_stream(text)
                        self._check_punct_idle()
                        continue

                    # В непрерывном режиме: всегда показываем текст (для игр с постоянно меняющимся текстом)
                    if self.continuous_mode:
                        # Просто показываем и озвучиваем каждый раз
                        if self.on_text_detected:
                            self.on_text_detected(text)
                        try:
                            if self.tts_engine.is_playing:
                                self.tts_engine.force_stop()
                            asyncio.create_task(self.tts_engine.speak(text))
                        except Exception as e:
                            logger.error(f"[TTS] Ошибка: {e}")
                    else:
                        # Дедуп: пропускаем если >80% похоже на прошлое
                        if self._last_spoken_normalized:
                            sim = self._similarity(normalized, self._last_spoken_normalized)
                            if sim > 0.8:
                                logger.debug(f"[SCAN] Похоже на прошлое (sim={sim:.0%}), пропускаем")
                                continue

                        self._last_spoken_normalized = normalized

                        # Сохраняем в единый лог
                        self._append_to_log(text, confidence, scan_seconds)

                        # GUI callback
                        if self.on_text_detected:
                            self.on_text_detected(text)

                        # Озвучка — не ждём конца, следующий скрин параллельно
                        try:
                            if self.tts_engine.is_playing:
                                self.tts_engine.force_stop()
                            asyncio.create_task(self.tts_engine.speak(text))
                        except Exception as e:
                            logger.error(f"[TTS] Ошибка: {e}")

                # Индикатор времени сканирования
                if self.on_scan_timing:
                    self.on_scan_timing(scan_seconds, text or "", confidence)

            except Exception as e:
                logger.error(f"Ошибка в цикле сканирования: {e}")
                await asyncio.sleep(1)

        logger.info("Цикл сканирования остановлен")

    # ==================== OCR LOGGING ====================

    def _save_ocr_result(self, raw_text: str, confidence: float, scan_time: float):
        """Сохраняет чистый текст в один txt файл (перезаписывается)."""
        pass

    def _append_to_log(self, text: str, confidence: float, scan_time: float):
        """Добавляет результат в единый лог-файл."""
        try:
            filepath = os.path.join(self._ocr_log_dir, "all_results.txt")
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            with open(filepath, "a", encoding="utf-8") as f:
                f.write(f"[{timestamp}] ({confidence:.0f}%, {scan_time:.1f}s)\n")
                f.write(f"{text.strip()}\n")
                f.write(f"{'─' * 40}\n")
            logger.debug(f"[OCR-LOG] Appended to log")
        except Exception as e:
            logger.warning(f"[OCR-LOG] Failed to append: {e}")

    # ==================== CHUNK+CLEAR MODE ====================

    def set_chunk_clear_mode(self, enabled: bool):
        """Включение/выключение chunk+clear режима.
        Взаимоисключается с line-by-line и sentence режимами."""
        self._chunk_clear_mode = enabled
        if enabled:
            self._line_by_line_mode = False
            self._sentence_mode = False
            self._pending_sentences.clear()
            self._spoken_lines.clear()
        logger.info(f"[CHUNK-CLEAR] Режим: {'вкл' if enabled else 'выкл'}")

    def set_punct_mode(self, enabled: bool):
        """Punctuation stream: накапливает до запятой/точки → перевод → TTS → очистка.
        Idle detection: если текст не меняется > punct_idle_threshold сек → пауза."""
        self._punct_mode = enabled
        if enabled:
            self._chunk_clear_mode = False
            self._line_by_line_mode = False
            self._sentence_mode = False
            self._punct_buffer = ""
            self._punct_idle = False
            self._punct_last_update = time.time()
        logger.info(f"[PUNCT] Режим: {'вкл' if enabled else 'выкл'}")

    def _process_punct_stream(self, text: str):
        """Обработка punctuation stream: накапливает до запятой/точки."""
        if not self._punct_mode or not text or not text.strip():
            return

        normalized = ' '.join(text.strip().lower().split())
        if len(normalized) <= 2:
            return

        now = time.time()
        self._punct_last_update = now
        self._punct_idle = False

        # Если новый текст совпадает с предыдущим — пропускаем
        if normalized == ' '.join(self._punct_buffer.lower().split()):
            return

        # Добавляем в буфер
        if self._punct_buffer:
            self._punct_buffer += " " + text.strip()
        else:
            self._punct_buffer = text.strip()

        logger.info(f"[PUNCT] Буфер ({len(self._punct_buffer)}): {self._punct_buffer[:60]}...")

        # Проверяем символы-триггеры: запятая или точка в конце
        trigger = None
        if self._punct_buffer.rstrip().endswith(',') or self._punct_buffer.rstrip().endswith('.'):
            trigger = self._punct_buffer.rstrip()[:-1].strip()  # без запятой/точки
            # Если осталось только 1-2 слова — не отправляем, ждём ещё
            if len(trigger.split()) < 3:
                trigger = None

        # Или если буфер стал длинным (>15 слов) — отправляем без триггера
        if not trigger and len(self._punct_buffer.split()) > 15:
            trigger = self._punct_buffer.strip()

        if trigger and trigger.strip():
            chunk = trigger.strip()
            self._punct_buffer = ""
            logger.info(f"[PUNCT] Отправка на перевод: '{chunk[:60]}...'")

            # Вызываем callback перевода
            if self.on_translate_chunk:
                try:
                    translated = self.on_translate_chunk(chunk)
                    if translated:
                        logger.info(f"[PUNCT] Перевод: '{translated[:60]}...'")
                        # Озвучиваем перевод
                        if self.tts_engine:
                            threading.Thread(
                                target=self._speak_chunk,
                                args=(translated,),
                                daemon=True
                            ).start()
                except Exception as e:
                    logger.error(f"[PUNCT] Ошибка перевода: {e}")

    def _check_punct_idle(self):
        """Проверка idle для punctuation stream."""
        if not self._punct_mode:
            return
        now = time.time()
        if not self._punct_idle and (now - self._punct_last_update) > self._punct_idle_threshold:
            self._punct_idle = True
            # Отправляем остаток буфера если есть
            if self._punct_buffer.strip() and len(self._punct_buffer.split()) >= 2:
                chunk = self._punct_buffer.strip()
                self._punct_buffer = ""
                if self.on_translate_chunk:
                    try:
                        translated = self.on_translate_chunk(chunk)
                        if translated and self.tts_engine:
                            threading.Thread(
                                target=self._speak_chunk,
                                args=(translated,),
                                daemon=True
                            ).start()
                    except Exception as e:
                        logger.error(f"[PUNCT] Idle translate error: {e}")
            logger.info(f"[PUNCT] Idle: текст не менялся >{self._punct_idle_threshold}сек")

    def _speak_chunk(self, text: str):
        """Озвучка чанка в отдельном потоке с новым event loop."""
        try:
            loop = asyncio.new_event_loop()
            loop.run_until_complete(self.tts_engine.speak(text))
            loop.close()
        except Exception as e:
            logger.error(f"[PUNCT] Speak error: {e}")

    def _queue_chunk(self, text: str):
        """Добавить текстовый чанк в буфер для последовательного чтения."""
        if not text or not text.strip():
            return

        normalized = ' '.join(text.strip().lower().split())
        if len(normalized) <= 3:
            return

        # Дедупликация: не добавлять если уже прочитано или в буфере
        chunk_hash = hash(normalized)
        if chunk_hash in self._spoken_chunks:
            return
        for buffered in self._chunk_buffer:
            if hash(' '.join(buffered.strip().lower().split())) == chunk_hash:
                return

        # Не отправлять в TTS если на паузе
        if self._tts_paused:
            return

        self._chunk_buffer.append(text.strip())
        self._spoken_chunks.add(chunk_hash)

        # Запускаем TTS стриминг если ещё не запущен
        if self.tts_engine and (self._tts_task is None or self._tts_task.done()):
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    self._tts_task = loop.create_task(self.start_tts_streaming())
                else:
                    self._tts_task = asyncio.ensure_future(self.start_tts_streaming())
            except RuntimeError:
                pass

        # Сигнализируем producer'у что есть данные
        self._chunk_ready.set()

        # Ограничение буфера
        if len(self._chunk_buffer) > 20:
            self._chunk_buffer.popleft()

        logger.info(f"[CHUNK-CLEAR] Чанк в буфер ({len(self._chunk_buffer)}): {text[:40]}...")

    async def _tts_producer_chunk_clear(self):
        """Producer для chunk+clear режима: потребляет чанки по порядку, генерирует аудио."""
        while self.running:
            try:
                # Если буфер пуст — ждём сигнала или таймаута
                if not self._chunk_buffer:
                    self._chunk_ready.clear()
                    try:
                        await asyncio.wait_for(self._chunk_ready.wait(), timeout=0.5)
                    except asyncio.TimeoutError:
                        continue

                # Обрабатываем всё что есть в буфере
                while self._chunk_buffer and self.running:
                    text = self._chunk_buffer.popleft()
                    if not text or not text.strip():
                        continue
                    if not self.tts_engine:
                        continue
                    try:
                        self._sync_voice_type()
                        path = await self.tts_engine.generate_audio_file(text)
                        if path:
                            await self._audio_queue.put(path)
                            self._audio_to_text[path] = text
                            logger.info(f"[CHUNK-CLEAR] Сгенерирован чанк: {text[:30]}...")
                    except Exception as e:
                        logger.error(f"[CHUNK-CLEAR] Ошибка генерации: {e}")

                # Буфер опустел — сбрасываем флаг
                self._chunk_ready.clear()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[CHUNK-CLEAR] Producer ошибка: {e}")
                await asyncio.sleep(0.5)

    # ==================== TTS STREAMING ====================

    def enable_tts_streaming(self, tts_engine):
        """Включение TTS стриминга для нового текста."""
        self.tts_engine = tts_engine
        logger.info("TTS стриминг включен")

    def disable_tts_streaming(self):
        """Выключение TTS стриминга."""
        self.tts_engine = None
        self._tts_streaming = False
        if self._tts_task:
            self._tts_task.cancel()
            self._tts_task = None
        logger.info("TTS стриминг выключен")

    async def _tts_stream_loop(self):
        """Конвейерный цикл потокового воспроизведения TTS."""
        mode_label = "CHUNK-CLEAR" if self._chunk_clear_mode else (
            "MULTI-VOICE" if self.multi_voice else "SINGLE-VOICE")
        logger.info(f"[TTS-STREAM] Запуск конвейера стриминга ({mode_label})")

        self._audio_queue = asyncio.Queue()
        self._stream_text_queue: asyncio.Queue = asyncio.Queue()

        # Persistent VLC (опционально)
        self._use_persistent_vlc = False
        try:
            if (self.settings.get("tts.persistent_vlc", False)
                    and self.tts_engine
                    and hasattr(self.tts_engine, "start_persistent_vlc")):
                self._use_persistent_vlc = self.tts_engine.start_persistent_vlc()
        except Exception as e:
            logger.error(f"[TTS-STREAM] persistent VLC недоступен: {e}")
            self._use_persistent_vlc = False

        # Выбираем producer в зависимости от режима
        if self._chunk_clear_mode:
            producer = asyncio.create_task(self._tts_producer_chunk_clear())
        elif self.multi_voice:
            producer = asyncio.create_task(self._tts_producer_multi_voice())
        else:
            producer = asyncio.create_task(self._tts_producer())
        consumer = asyncio.create_task(self._tts_consumer())
        stream_consumer = asyncio.create_task(self._tts_streaming_consumer())
        try:
            await asyncio.gather(producer, consumer, stream_consumer)
        except asyncio.CancelledError:
            producer.cancel()
            consumer.cancel()
        logger.info("[TTS-STREAM] Конвейер стриминга остановлен")

    async def _tts_producer(self):
        """Генерирует аудиофайлы из текстовой очереди (single voice)."""
        while self.running:
            try:
                try:
                    text = await asyncio.wait_for(self._tts_queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                if not text or not text.strip():
                    continue
                if not self.tts_engine:
                    continue
                try:
                    self._sync_voice_type()
                    path = await self.tts_engine.generate_audio_file(text)
                    if path:
                        await self._audio_queue.put(path)
                        self._audio_to_text[path] = text
                        logger.info(f"[TTS-STREAM] Сгенерирован чанк: {text[:30]}...")
                except Exception as e:
                    logger.error(f"[TTS-STREAM] Ошибка генерации: {e}")
                finally:
                    self._tts_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[TTS-STREAM] Producer ошибка: {e}")
                await asyncio.sleep(0.5)

    async def _tts_producer_multi_voice(self):
        """Генерирует аудиофайлы, переключая голоса по полу слов."""
        from voice_profiles import VoiceProfile
        from tts_engine import TTSEngine

        current_gender = None
        current_voice = self.tts_engine.voice if self.tts_engine else "ru-RU-DmitryNeural"

        while self.running:
            try:
                try:
                    text = await asyncio.wait_for(self._tts_queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                if not text or not text.strip():
                    continue
                if not self.tts_engine:
                    continue
                try:
                    has_roles = any(
                        re.match(r'^[A-Za-zА-Яа-яёЁ]+:', line)
                        for line in text.split("\n")[:5]
                    )

                    if has_roles:
                        parsed = self.tts_engine.multi_voice_parser.get_voice_for_text(text)
                        for profile, content in parsed:
                            if not content.strip():
                                continue
                            old_voice = self.tts_engine.voice
                            old_type = self.tts_engine.voice_type
                            self.tts_engine.set_voice(profile.voice_code)
                            path = await self.tts_engine.generate_audio_file(content)
                            self.tts_engine.voice = old_voice
                            self.tts_engine.voice_type = old_type
                            if path:
                                await self._audio_queue.put(path)
                                logger.info(f"[TTS-STREAM-MULTI] [{profile.language}] {profile.voice_code}: {content[:30]}...")
                    else:
                        lang = TTSEngine._detect_text_lang(text)

                        # If user chose an offline voice (piper/rhvoice/silero), keep it
                        if self.tts_engine.voice_type not in ("edge", "sapi"):
                            current_voice = self.tts_engine.voice
                        else:
                            if self.tts_engine and hasattr(self.tts_engine, 'voice_router'):
                                current_voice = self.tts_engine.voice_router.get_voice_for_text(text)
                            else:
                                current_voice = self.tts_engine.voice

                        old_voice = self.tts_engine.voice
                        old_type = self.tts_engine.voice_type
                        self.tts_engine.set_voice(current_voice)
                        path = await self.tts_engine.generate_audio_file(text)
                        self.tts_engine.voice = old_voice
                        self.tts_engine.voice_type = old_type
                        if path:
                            await self._audio_queue.put(path)
                            logger.info(f"[TTS-STREAM-MULTI] [{lang}] {current_voice}: {text[:30]}...")
                except Exception as e:
                    logger.error(f"[TTS-STREAM-MULTI] Ошибка генерации: {e}")
                finally:
                    self._tts_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[TTS-STREAM-MULTI] Producer ошибка: {e}")
                await asyncio.sleep(0.5)

    # ==================== SCREENSHOT COMPARISON ====================

    def _screenshot_changed(self, img) -> bool:
        """Проверка: изменился ли скриншот (игнорируя курсор мыши)."""
        import numpy as np
        try:
            # Уменьшаем для быстрого сравнения
            small = img.resize((64, 64)).convert("L")
            arr = np.array(small, dtype=np.float32)

            if self._prev_frame is None:
                self._prev_frame = arr
                return True

            # Сравниваем без центральной области (там обычно курсор)
            h, w = arr.shape
            # Убираем центральные 10% (курсор) и края 5% (артефакты)
            margin_x = max(1, int(w * 0.05))
            margin_y = max(1, int(h * 0.05))
            center_x = w // 2
            center_y = h // 2
            exclude_r = max(1, int(min(w, h) * 0.05))

            # Создаём маску: всё кроме центральной области курсора
            mask = np.ones_like(arr, dtype=bool)
            y_start = max(0, center_y - exclude_r)
            y_end = min(h, center_y + exclude_r)
            x_start = max(0, center_x - exclude_r)
            x_end = min(w, center_x + exclude_r)
            mask[y_start:y_end, x_start:x_end] = False

            # Сравниваем только по маске
            if mask.any():
                diff = float(np.mean(np.abs(arr[mask] - self._prev_frame[mask])))
            else:
                diff = float(np.mean(np.abs(arr - self._prev_frame)))

            self._prev_frame = arr

            # Если разница > 5.0 — скриншот реально изменился (а не просто курсор)
            if diff > 5.0:
                logger.debug(f"[SCREENSHOT] Изменение: diff={diff:.2f}")
                return True
            return False

        except Exception:
            return True

    def _detect_text_quick(self, img):
        """Быстрая проверка: изменилось ли что-то на экране (пиксельный дифф, ~1-5мс).
        Если изменений нет — OCR запускать не нужно."""
        import numpy as np
        try:
            small = img.resize((32, 32)).convert("L")
            arr = np.array(small, dtype=np.float32)
            if self._prev_frame is None:
                self._prev_frame = arr
                return True
            diff = float(np.mean(np.abs(arr - self._prev_frame)))
            self._prev_frame = arr
            return diff > 2.0
        except Exception:
            return True

    def _detect_text_language(self, text: str) -> str:
        """Определяет язык текста: 'ru' или 'en'."""
        if not text:
            return "en"
        cyrillic_count = sum(1 for c in text if '\u0400' <= c <= '\u04FF')
        total_letters = sum(1 for c in text if c.isalpha())
        if total_letters == 0:
            return "en"
        if cyrillic_count / total_letters > 0.3:
            return "ru"
        return "en"

    def _get_voice_for_language(self, lang: str) -> str:
        """Возвращает код голоса для языка из словаря профилей."""
        if not self.tts_engine:
            return "ru-RU-DmitryNeural" if lang == "ru" else "en-US-GuyNeural"
        for key, profile in self.tts_engine.voice_dict.profiles.items():
            if profile.language == lang:
                return profile.voice_code
        return "ru-RU-DmitryNeural" if lang == "ru" else "en-US-GuyNeural"

    def _sync_voice_type(self):
        """Sync voice_type с текущим голосом для правильного движка."""
        if not self.tts_engine:
            return
        voice = self.tts_engine.voice
        if voice.startswith(("ru-RU-", "en-", "de-", "fr-", "es-", "it-", "ja-", "zh-", "ko-", "uk-", "pl-", "pt-", "tr-", "ar-", "hi-", "th-")):
            self.tts_engine.voice_type = "edge"
        elif voice.startswith("rhvoice:"):
            self.tts_engine.voice_type = "rhvoice"
        elif voice.startswith("silero:"):
            self.tts_engine.voice_type = "silero"
        elif voice.startswith("persona:"):
            self.tts_engine.voice_type = "persona"

    async def _tts_consumer(self):
        """Проигрывает готовые аудиофайлы подряд без пауз."""
        while self.running:
            try:
                try:
                    path = await asyncio.wait_for(self._audio_queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                if not self.tts_engine:
                    continue
                self._tts_streaming = True
                try:
                    if self._sync_mode:
                        await self.tts_engine._play_audio(path)
                    elif self._use_persistent_vlc and hasattr(
                        self.tts_engine, "enqueue_persistent_vlc"
                    ):
                        self.tts_engine.enqueue_persistent_vlc(path)
                    else:
                        await self.tts_engine._play_audio(path)
                except Exception as e:
                    logger.error(f"[TTS-STREAM] Ошибка воспроизведения: {e}")
                finally:
                    self._tts_streaming = False
                    source_text = self._audio_to_text.pop(path, None)
                    if self._sentence_mode and source_text:
                        self._on_sentence_spoken(source_text)
                    self._audio_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[TTS-STREAM] Consumer ошибка: {e}")
                await asyncio.sleep(0.5)

    async def _tts_streaming_consumer(self):
        """Streaming consumer — uses speak() with Edge-TTS → VLC stdin for lowest latency."""
        while self.running:
            try:
                try:
                    sentence_text = await asyncio.wait_for(self._stream_text_queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                if not self.tts_engine:
                    self._stream_text_queue.task_done()
                    continue
                self._tts_streaming = True
                try:
                    await self.tts_engine.speak(sentence_text)
                except Exception as e:
                    logger.error(f"[TTS-STREAM-CONSUMER] Ошибка: {e}")
                finally:
                    self._tts_streaming = False
                    if self._sentence_mode:
                        self._on_sentence_spoken(sentence_text)
                    self._stream_text_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[TTS-STREAM-CONSUMER] Ошибка: {e}")
                await asyncio.sleep(0.5)

    async def start_tts_streaming(self):
        """Запуск цикла TTS стриминга."""
        if self._tts_task is None:
            self._tts_task = asyncio.create_task(self._tts_stream_loop())
            logger.info("TTS стриминг запущен")

    async def stop_tts_streaming(self):
        """Остановка цикла TTS стриминга."""
        if self._tts_task:
            self._tts_task.cancel()
            try:
                await self._tts_task
            except asyncio.CancelledError:
                pass
            self._tts_task = None

        if self.tts_engine:
            self.tts_engine.stop_stream()
            if hasattr(self.tts_engine, "stop_persistent_vlc"):
                self.tts_engine.stop_persistent_vlc()

        # Очистка TTS очереди
        while not self._tts_queue.empty():
            try:
                self._tts_queue.get_nowait()
                self._tts_queue.task_done()
            except Exception:
                break

        # Очистка стриминговой очереди
        while not self._stream_text_queue.empty():
            try:
                self._stream_text_queue.get_nowait()
                self._stream_text_queue.task_done()
            except Exception:
                break

        # Очистка буфера чанков
        self._chunk_buffer.clear()
        self._chunk_ready.clear()

        logger.info("TTS стриминг остановлен")

    def queue_text_for_tts(self, text: str):
        """Добавить дельту текста в очередь TTS."""
        if not self.tts_engine:
            return
        if not text or not text.strip() or len(text.strip()) < 3:
            return

        delta = self._get_text_delta(text)
        self._last_tts_text = text.strip()

        if not delta or len(delta.strip()) < 2:
            return

        try:
            if self._tts_streaming:
                return

            if self._tts_paused:
                return

            if self._tts_task is None or self._tts_task.done():
                self._tts_task = asyncio.create_task(self.start_tts_streaming())

            # Route directly to streaming consumer (speak() → Edge-TTS → VLC stdin)
            self._stream_text_queue.put_nowait(delta)
            logger.info(f"[TTS-STREAM] Дельта: {delta[:40]}...")

        except Exception as e:
            logger.error(f"[TTS-QUEUE] Ошибка: {e}")

    # ==================== SENTENCE-BY-SENTENCE MODE ====================

    def _queue_sentences(self, text: str):
        """Пофразовый режим: разбивает текст на предложения."""
        if not text or not self.tts_engine:
            return

        sentences = self._split_sentences(text)
        if not sentences:
            return

        new_sentences = []
        for s in sentences:
            s_stripped = s.strip()
            if not s_stripped or len(s_stripped) < 3:
                continue
            already_queued = False
            for pending in self._pending_sentences:
                if self._similarity(s_stripped.lower(), pending.lower()) > 0.85:
                    already_queued = True
                    break
            if not already_queued:
                new_sentences.append(s_stripped)

        if not new_sentences:
            return

        if self._tts_paused:
            return

        for s in new_sentences:
            self._pending_sentences.append(s)
            try:
                if self._stream_text_queue.qsize() > 5:
                    logger.debug(f"[SENTENCE] Очередь стриминга полная ({self._stream_text_queue.qsize()}), пропускаем")
                    return

                if self._tts_task is None or self._tts_task.done():
                    self._tts_task = asyncio.create_task(self.start_tts_streaming())

                self._stream_text_queue.put_nowait(s)
                logger.info(f"[SENTENCE] Предложение в стриминг: {s[:50]}...")
            except Exception as e:
                logger.error(f"[SENTENCE] Ошибка: {e}")

    def _on_sentence_spoken(self, spoken_text: str):
        """Вызывается когда предложение озвучено — стирает из pending."""
        if not self._pending_sentences:
            return
        for i, s in enumerate(self._pending_sentences):
            if self._similarity(spoken_text.lower(), s.lower()) > 0.8:
                self._pending_sentences.pop(i)
                break
        if self.on_text_spoken:
            remaining = "\n".join(self._pending_sentences)
            self.on_text_spoken(remaining)

    # ==================== LINE-BY-LINE MODE ====================

    def set_line_by_line_mode(self, enabled: bool):
        """Включение/выключение построчного режима. Взаимоисключается с chunk+clear."""
        self._line_by_line_mode = enabled
        if enabled:
            self._spoken_lines.clear()
            self._chunk_clear_mode = False
        logger.info(f"[LINE-BY-LINE] Режим: {'вкл' if enabled else 'выкл'}")

    def set_sync_mode(self, enabled: bool):
        """Включение/выключение sync-режима."""
        self._sync_mode = enabled
        logger.info(f"[SYNC] Режим синхронизации: {'вкл' if enabled else 'выкл'}")

    def set_sentence_mode(self, enabled: bool):
        """Включение/выключение пофразового режима. Взаимоисключается с chunk+clear."""
        self._sentence_mode = enabled
        if not enabled:
            self._pending_sentences.clear()
            self._spoken_offset = 0
        if enabled:
            self._chunk_clear_mode = False
        logger.info(f"[SENTENCE] Пофразовый режим: {'вкл' if enabled else 'выкл'}")

    @staticmethod
    def _split_sentences(text: str) -> list:
        """Разбивает текст на предложения."""
        if not text or not text.strip():
            return []
        parts = re.split(r'(?<=[.!?])\s+|\n{2,}', text.strip())
        result = []
        for p in parts:
            p = p.strip()
            if not p:
                continue
            if len(p) > 80:
                sub = re.split(r'(?<=,)\s+', p)
                result.extend(s.strip() for s in sub if s.strip())
            else:
                result.append(p)
        return result

    def clear_spoken_lines(self):
        """Очистка кэша озвученных строк."""
        self._spoken_lines.clear()
        logger.info("[LINE-BY-LINE] Кэш озвученных строк очищен")

    def _normalize_line(self, line: str) -> str:
        """Нормализация строки для сравнения."""
        normalized = ' '.join(line.strip().lower().split())
        normalized = normalized.rstrip('.,!?:;-')
        return normalized

    def _queue_new_lines(self, text: str):
        """Построчная обработка: каждая НОВАЯ строка отправляется в TTS."""
        if not text or not self.tts_engine:
            return

        lines = [l.strip() for l in text.split("\n") if l.strip()]
        if not lines:
            return

        new_lines = []
        for line in lines:
            normalized = self._normalize_line(line)
            if not normalized or len(normalized) < 3:
                continue
            already_spoken = False
            for spoken in self._spoken_lines:
                ratio = self._similarity(normalized, spoken)
                if ratio > 0.85:
                    already_spoken = True
                    break
            if not already_spoken:
                self._spoken_lines.add(normalized)
                new_lines.append(line)

        if not new_lines:
            return

        if self._tts_paused:
            return

        for line in new_lines:
            try:
                if self._stream_text_queue.qsize() > 8:
                    logger.debug(f"[LINE-BY-LINE] Очередь стриминга полная ({self._stream_text_queue.qsize()}), пропускаем")
                    return

                if self._tts_task is None or self._tts_task.done():
                    self._tts_task = asyncio.create_task(self.start_tts_streaming())

                self._stream_text_queue.put_nowait(line)
                logger.info(f"[LINE-BY-LINE] Строка в поток: {line[:50]}...")
            except Exception as e:
                logger.error(f"[LINE-BY-LINE] Ошибка: {e}")

    # ==================== CACHE OVERRIDE ====================

    def clear_cache(self):
        """Очистка кэша текста + TTS кэшей."""
        super().clear_cache()
        self._last_tts_text = ""
        self._spoken_chunks.clear()
        self._chunk_buffer.clear()
        self._chunk_ready.clear()
        self._spoken_lines.clear()
        self._pending_sentences.clear()
        logger.info("LiveScanner кэш полностью очищен")

    # ==================== STATUS ====================

    def get_status(self) -> dict:
        """Получение статуса сканера с TTS информацией."""
        status = super().get_status()
        status.update({
            "tts_streaming": self._tts_streaming,
            "tts_queue_size": self._tts_queue.qsize(),
            "line_by_line": self._line_by_line_mode,
            "spoken_lines_count": len(self._spoken_lines),
            "sync_mode": self._sync_mode,
            "chunk_clear_mode": self._chunk_clear_mode,
            "chunk_buffer_size": len(self._chunk_buffer),
        })
        return status
