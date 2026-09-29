# -*- coding: utf-8 -*-
"""
Game Audio Capture — WASAPI loopback + per-process filtering.
Captures audio from the game window specifically, without headphones/mic noise.
"""
import ctypes
import logging
import re
import struct
import threading
import time

import numpy as np

logger = logging.getLogger(__name__)

try:
    import pyaudiowpatch as pyaudio
    PYAUDIO_AVAILABLE = True
except ImportError:
    PYAUDIO_AVAILABLE = False

try:
    from pycaw.pycaw import AudioUtilities
    PYCAW_AVAILABLE = True
except ImportError:
    PYCAW_AVAILABLE = False

try:
    import process_audio_capture as proc_cap_mod
    PROC_CAP_AVAILABLE = True
except Exception:
    PROC_CAP_AVAILABLE = False


class GameAudioCapture:
    """
    Captures game audio via WASAPI loopback.
    Uses pycaw to identify game process audio sessions.
    Filters out silence and non-game audio.
    """

    def __init__(self):
        self._pa = None
        self._stream = None
        self._running = False
        self._game_pid = None
        self._game_name = None
        self._sample_rate = 48000
        self._channels = 2
        # Блок чтения 0.25 c, а не 1 c: поток захвата замечает _running=False
        # и завершается за четверть секунды (иначе он переживал stop() и
        # копился при каждом старте), плюс аудио попадает в STT быстрее.
        self._chunk_sec = 0.25
        self._audio_callbacks = []  # list of callables(audio, sample_rate)
        self._proc_capture = None
        self._capture_thread = None
        self._capture_threads = []
        # «Поколение» захвата. Потоки петли застревают внутри
        # stream.read() (C-уровень, извне их не прервать) и переживают
        # stop(). Если они продолжают звать колбэки, то после N
        # перезапусков одна реплика попадает в STT N раз. Поэтому поток
        # знает своё поколение и молчит, если поколение устарело.
        self._gen = 0
        # Per-process capture: захват ТОЛЬКО игры (наш TTS сразу исключён).
        # Включаем автоматически, но с проверкой «пошёл ли звук» и откатом.
        self.per_process_enabled = True
        self._proc_audio_seen = False
        self._proc_check_sec = 5.0   # пауза перед health-check
        self._proc_retry_sec = 300.0  # не повторять process-capture чаще
        self._proc_failed = {}       # pid -> (время, ошибка) — чтобы не долбить
        self._target_logged_pid = None  # для лога «цель изменилась»
        self._active_streams = 0     # реально открытых loopback-потоков
        self._auto_target = True     # цель определяется автоматически
        self._auto_monitor = None
        self._silence_threshold = 0.005
        self._voice_threshold = 0.01  # min peak to count as "voice/audio activity"
        self._saved_default_output = None  # saved before VB-Cable switch
        # VB-Cable переключает системный вывод устройства — опасно.
        # Включается только явно (set_vbcable(True)).
        self._vbcable_enabled = False

    # Процессы, которые почти наверняка НЕ являются источником игровых реплик
    _AUDIO_DENYLIST = (
        "python", "pythonw", "explorer", "system", "audiodg", "svchost",
        "chrome", "msedge", "firefox", "opera", "brave", "vivaldi",
        "vlc", "mpv", "potplay", "aimp", "foobar", "spotify", "itunes",
        "discord", "telegram", "skype", "steam", "epicgameslauncher",
        "battle.net", "bnet", "origin", "launcher", "crashreporter",
        "obs64", "obs32", "audacity", "ardour", "dxtory", "rtap",
    )

    # Ключевые слова, типичные для игровых процессов
    _GAME_HINTS = (
        "game", "genshin", "honkai", "wuthering", "eldenring", "cyberpunk",
        "unity", "unreal", "godot", "shipping", "win64", "client", "launcher_",
        "starrail", "zzz", "pgr", "arknights", "cod", "warframe", "destiny",
        "overwatch", "apex", "pubg", "tarkov", "stalker", "cyber", "fnaf",
    )

    def _find_vbcable_output_device(self):
        """Find CABLE Input device ID (output device where game audio goes)."""
        if not PYCAW_AVAILABLE:
            return None
        try:
            devices = AudioUtilities.GetAllDevices()
            for d in devices:
                name = d.FriendlyName or ""
                if "CABLE Input" in name and "Loopback" not in name:
                    if d.state == 1:  # Active
                        return d.id
        except Exception as e:
            logger.warning(f"[GameAudio] VB-Cable search failed: {e}")
        return None

    def _find_vbcable_render_id(self):
        """Find CABLE Input render device ID via pycaw."""
        if not PYCAW_AVAILABLE:
            return None
        try:
            devices = AudioUtilities.GetAllDevices()
            for d in devices:
                name = d.FriendlyName or ""
                if "CABLE Input" in name and "Loopback" not in name and d.state == 1:
                    return d.id
        except Exception:
            pass
        return None

    def _set_default_render(self, device_id):
        """Set default render device via IPolicyConfig COM (Win10 1903+)."""
        try:
            import comtypes
            from comtypes import GUID, HRESULT, POINTER, c_void_p
            from ctypes import c_wchar_p, c_int

            IID_IPolicyConfig = GUID('{CA286FC3-91FD-42C3-8E9B-CAAFA66242E3}')
            CLSID_MMDeviceEnumerator = GUID('{BCDE0395-E721-11CE-BE1D-00AA0057B223}')
            IID_IMMDeviceEnumerator = GUID('{A95664D2-9614-4F35-A746-DE8DB63617E6}')
            CLSCTX_ALL = 0x17

            class IMMDeviceEnumerator(comtypes.IUnknown):
                _iid_ = IID_IMMDeviceEnumerator
                _methods_ = [
                    HRESULT('EnumAudioEndpoints', c_void_p, c_int, c_uint, POINTER(c_void_p)),
                    HRESULT('GetDefaultAudioEndpoint', c_void_p, c_int, c_int, POINTER(c_void_p)),
                ]

            class IMMDevice(comtypes.IUnknown):
                _iid_ = GUID('{D666063F-1587-4E43-81F1-B948E807363F}')
                _methods_ = [
                    HRESULT('Activate', POINTER(GUID), c_uint, c_void_p, POINTER(c_void_p)),
                ]

            class IPolicyConfig(comtypes.IUnknown):
                _iid_ = IID_IPolicyConfig
                _methods_ = [
                    HRESULT('GetMixFormat', c_void_p, POINTER(c_void_p)),
                    HRESULT('IsSupported', c_void_p, c_void_p),
                    HRESULT('GetDeviceFormat', c_void_p, c_int, c_void_p),
                    HRESULT('ResetDeviceFormat', c_void_p),
                    HRESULT('SetDeviceFormat', c_void_p, c_void_p, c_void_p),
                    HRESULT('GetProcessingPeriod', c_void_p, c_int, c_void_p, c_void_p),
                    HRESULT('SetProcessingPeriod', c_void_p, c_void_p),
                    HRESULT('GetShareMode', c_void_p, c_void_p),
                    HRESULT('SetShareMode', c_void_p, c_void_p),
                    HRESULT('SelectConfiguration', c_void_p, c_void_p),
                    HRESULT('SetDefaultEndpoint', c_wchar_p, c_int),
                ]

            enumerator = comtypes.CoCreateInstance(
                CLSID_MMDeviceEnumerator, IMMDeviceEnumerator, CLSCTX_ALL)
            dev_pp = POINTER(IMMDevice)()
            enumerator.GetDevice(device_id, dev_pp)
            dev = dev_pp.contents

            pp = c_void_p()
            dev.Activate(IID_IPolicyConfig.__wrapped__, CLSCTX_ALL, None, pp)
            policy = comtypes.cast(pp, POINTER(IPolicyConfig)).contents
            policy.SetDefaultEndpoint(device_id, 0)
            return True
        except Exception as e:
            logger.debug(f"[GameAudio] IPolicyConfig failed: {e}")
            return False

    def set_vbcable(self, enabled: bool):
        """Ручное включение переключения вывода на VB-Cable (по умолчанию выкл.)."""
        self._vbcable_enabled = bool(enabled)

    def _switch_to_vbcable(self):
        """Switch default output to CABLE Input, save original for restore."""
        try:
            from pycaw.pycaw import AudioUtilities
            speakers = AudioUtilities.GetSpeakers()
            if speakers is None:
                logger.info("[GameAudio] No default output device")
                return False
            try:
                self._saved_default_output = speakers._dev  # restore by id later
            except Exception:
                self._saved_default_output = speakers

            cable_id = self._find_vbcable_render_id()
            if not cable_id:
                logger.info("[GameAudio] VB-Cable not found, skipping switch")
                return False

            ok = self._set_default_render(cable_id)
            if ok:
                logger.info("[GameAudio] Default output switched to VB-Cable")
            else:
                logger.warning("[GameAudio] VB-Cable switch failed (IPolicyConfig)")
            return ok
        except Exception as e:
            logger.warning(f"[GameAudio] VB-Cable switch error: {e}")
            return False

    def _restore_default_output(self):
        """Restore the ORIGINAL default output device (by saved id)."""
        saved = self._saved_default_output
        self._saved_default_output = None
        if saved is None:
            return
        device_id = getattr(saved, "id", saved)
        try:
            if self._set_default_render(device_id):
                logger.info("[GameAudio] Default output restored")
            else:
                logger.warning("[GameAudio] Failed to restore default output")
        except Exception as e:
            logger.warning(f"[GameAudio] Restore failed: {e}")

    def _get_foreground_pid(self):
        """Return the PID of the currently focused (foreground) window."""
        try:
            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            if not hwnd:
                return None
            pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(int(hwnd), ctypes.byref(pid))
            return pid.value
        except Exception as e:
            logger.warning(f"[GameAudio] GetForegroundWindow failed: {e}")
            return None

    def find_game_process(self, hwnd=None) -> dict:
        """Авто-определение источника речи по АКТИВНЫМ аудиосессиям Windows.

        Порядок:
        1) активная сессия того окна, на котором сейчас фокус (это и есть игра);
        2) активная сессия с «игровыми» признаками в имени;
        3) любая активная сессия, кроме себя/браузеров/плееров/лаунчеров.

        Требование «State == Active» отсекает молчащие приложения, у которых
        просто есть звуковая сессия. Возвращает None, если активных сессий нет.
        """
        if not PYCAW_AVAILABLE:
            return None

        import os
        my_pid = os.getpid()

        try:
            sessions = AudioUtilities.GetAllSessions()
        except Exception as e:
            logger.warning(f"[GameAudio] session enumeration failed: {e}")
            return None

        active = []
        for s in sessions:
            try:
                if int(s.State) != 1:      # 1 = AudioSessionStateActive
                    continue
                pid = int(s.ProcessId)
                if pid == 0 or pid == my_pid:
                    continue
                proc = s.Process
                if proc is None:
                    continue
                name = proc.name()
                low = name.lower()
                if any(d in low for d in self._AUDIO_DENYLIST):
                    continue
                try:
                    vol = float(s.SimpleAudioVolume.GetMasterVolume())
                    if vol <= 0.001:
                        continue
                except Exception:
                    vol = 1.0
                active.append({"pid": pid, "name": name, "volume": vol,
                               "_low": low})
            except Exception:
                continue

        if not active:
            return None

        # Метка выбора: info только когда цель СМЕНИЛАСЬ, иначе авто-монитор
        # заливал лог одинаковыми строками каждые 3 секунды.
        def _mark(item, why):
            changed = item["pid"] != getattr(self, "_target_logged_pid", None)
            self._game_pid = item["pid"]
            self._game_name = item["name"]
            self._target_logged_pid = item["pid"]
            log = logger.info if changed else logger.debug
            log(f"[GameAudio] Target ({why}): {item['name']} "
                f"(PID={item['pid']}, vol={item['volume']:.2f})")
            return item

        # 1) Окно в фокусе
        fg_pid = self._get_foreground_pid()
        for item in active:
            if fg_pid and item["pid"] == fg_pid:
                return _mark(item, "focused")

        # 2) Похоже на игру
        for item in active:
            if any(h in item["_low"] for h in self._GAME_HINTS):
                return _mark(item, "game-like")

        # 3) Любая оставшаяся активная сессия
        return _mark(active[0], "active audio")

    def refresh_target(self) -> dict:
        """Переопределить цель (используется авто-монитором)."""
        return self.find_game_process()

    def _start_auto_monitor(self, interval=3.0):
        """Следить за активной аудиосессией и подхватывать смену процесса."""
        if self._auto_monitor and self._auto_monitor.is_alive():
            return

        def _loop():
            while self._running and self._auto_target:
                time.sleep(interval)
                try:
                    found = self.refresh_target()
                except Exception as e:
                    logger.debug(f"[GameAudio] auto monitor error: {e}")
                    continue
                if not found:
                    continue
                # Сравниваем с ЦЕЛЬЮ, а не с тем, открыт ли process-capture:
                # при работе через loopback _proc_capture_pid() == None, и
                # старое сравнение пересоздавало захват каждые 3 секунды.
                if found["pid"] != self._game_pid:
                    logger.info(f"[GameAudio] Active source changed -> "
                                f"{found['name']} (PID={found['pid']})")
                    self._switch_capture_to(found["pid"])

            logger.info("[GameAudio] Auto monitor stopped")

        self._auto_monitor = threading.Thread(target=_loop, daemon=True)
        self._auto_monitor.start()

    def is_isolated(self) -> bool:
        """True только если звук реально приходит ИЗ ОДНОГО ПРОЦЕССА.

        Проверяем не «объект создан», а «пакеты идут»: иначе анти-петля в
        STT отключилась бы, а мы бы писали в текст всё подряд.
        """
        cap = getattr(self, "_proc_capture", None)
        if not cap:
            return False
        try:
            return bool(cap.is_active() and cap.packets_received > 0)
        except Exception:
            return False

    def _proc_capture_pid(self):
        if getattr(self, "_proc_capture", None):
            return getattr(self._proc_capture, "pid", None)
        return None

    @staticmethod
    def _name_of_pid(pid):
        try:
            import psutil
            return psutil.Process(int(pid)).name()
        except Exception:
            return None

    def get_loopback_devices(self):
        """Get ALL WASAPI loopback devices (capture from all outputs)."""
        if not PYAUDIO_AVAILABLE:
            return []

        self._pa = pyaudio.PyAudio()
        loopbacks = []
        for i in range(self._pa.get_device_count()):
            info = self._pa.get_device_info_by_index(i)
            if info.get('isLoopbackDevice', False):
                loopbacks.append(info)

        # Приоритет: устройство, соответствующее текущему выводу по умолчанию.
        # Мёртвые CABLE-устройства (отключённый VB-Cable) иначе открываются
        # первыми и тратят время на бесполезные попытки.
        #
        # ВАЖНО: имя loopback-устройства у pyaudiowpatch отличается от имени
        # обычного вывода суффиксом " [Loopback]", поэтому сравнение строк
        # без нормализации НИКОГДА не срабатывало: приоритет не применялся,
        # и первым открывался мёртвый CABLE (RMS ~0.00001), где тишина.
        # Реальный звук игры был на AMD (RMS ~0.003).
        try:
            default_out = self._pa.get_default_output_device_info().get('name')
        except Exception:
            default_out = None

        def _norm(nm):
            return (nm or "").replace("[Loopback]", "").replace("Loopback", "").strip().lower()

        def _rank(d):
            name = d['name']
            if default_out and _norm(name) == _norm(default_out):
                return 0
            # Виртуальные CABLE без совпадения с выводом по умолчанию —
            # почти всегда остатки отключённого VB-Cable.
            if re.search(r'cable|vb-?audio', name, re.I):
                return 2
            return 1

        loopbacks.sort(key=_rank)

        for lb in loopbacks:
            logger.info(f"[GameAudio] Loopback device found: {lb['name']}")
        return loopbacks

    def get_loopback_device(self):
        """Get WASAPI loopback device (kept for compatibility)."""
        devices = self.get_loopback_devices()
        return devices[0] if devices else None

    def add_audio_callback(self, cb):
        """Register an audio callback. Starts capture if needed (ref-counted)."""
        if cb not in self._audio_callbacks:
            self._audio_callbacks.append(cb)
        if not self._running:
            self.start()

    def remove_audio_callback(self, cb):
        """Unregister an audio callback. Stops capture when no consumers remain."""
        if cb in self._audio_callbacks:
            self._audio_callbacks.remove(cb)
        if not self._audio_callbacks and self._running:
            self.stop()

    def start(self, game_pid=None, force_loopback=False):
        """Начать захват аудио.

        1) Пытаемся захватить ТОЛЬКО выбранный/авто-определённый процесс
           (наш собственный TTS тогда физически не попадает в запись).
        2) Если не вышло — системный WASAPI loopback.
        """
        if self._running:
            # Явный выбор exe важнее текущего авто-захвата: переключаемся
            if game_pid is not None and self._auto_target \
                    and self._proc_capture_pid() != game_pid:
                self._game_name = self._name_of_pid(game_pid) or self._game_name
                self._switch_capture_to(game_pid)
            return

        self._auto_target = game_pid is None
        if game_pid is not None:
            self._game_pid = game_pid
        else:
            game = self.find_game_process()
            if game:
                self._game_pid = game["pid"]
                self._game_name = game["name"]
            else:
                logger.warning("[GameAudio] No active audio source found, "
                               "capturing all system audio")

        if self._vbcable_enabled:
            self._switch_to_vbcable()

        if (self.per_process_enabled and not force_loopback and self._game_pid
                and PROC_CAP_AVAILABLE and proc_cap_mod.is_supported()):
            if self._start_process_capture(self._game_pid):
                self._running = True
                self._start_auto_monitor()
                return

        if not self._start_loopback_capture():
            self._stop_streams()
            if self._vbcable_enabled:
                self._restore_default_output()
            return

        self._running = True
        self._start_auto_monitor()

    def _start_process_capture(self, pid) -> bool:
        """Захват одного процесса + проверка, что звук реально пошёл."""
        pid = int(pid)
        # Этот API (AUDIOCLIENT_PROCESS_LOOPBACK) есть не на всех системах:
        # при отказе не пытаемся снова 5 минут, сразу работаем через loopback.
        bad = self._proc_failed.get(pid)
        if bad and (time.time() - bad[0]) < self._proc_retry_sec:
            return False

        self._proc_audio_seen = False
        try:
            capture = proc_cap_mod.ProcessLoopbackCapture(
                pid, self._on_proc_audio,
                silence_threshold=self._silence_threshold,
                on_error=self._on_proc_error)
            capture.start()
        except Exception as e:
            self._proc_failed[pid] = (time.time(), e)
            logger.warning(f"[GameAudio] Per-process init failed "
                           f"(fallback to loopback): {e}")
            return False

        self._proc_capture = capture
        # Health-check: падение/смерть потока ловим и откатываемся на loopback
        checker = threading.Thread(
            target=self._check_process_capture, args=(capture,), daemon=True)
        checker.start()
        logger.info(f"[GameAudio] Per-process capture started "
                    f"(PID={pid}, app={self._game_name})")
        return True

    def _check_process_capture(self, capture):
        """Health-check: откатываемся на loopback только при РЕАЛЬНОЙ поломке.

        Раньше проверка срабатывала, если за 5 c не пришёл не-тихий пакет, —
        но тишина в игре (загрузка, пауза диалога) не означает отказ захвата.
        Теперь ориентируемся на состояние API и ошибки, а не на громкость.
        """
        time.sleep(self._proc_check_sec)
        if not self._running or capture is not self._proc_capture:
            return

        failed = capture.last_error is not None or not capture.is_active()
        if failed:
            reason = capture.last_error or "capture thread is not active"
            logger.warning(f"[GameAudio] Per-process capture failed "
                           f"({reason}), falling back to system loopback")
            try:
                capture.stop()
            except Exception:
                pass
            if capture is self._proc_capture:
                self._proc_capture = None
            self._running = False
            self._start_loopback_capture()
            return

        if self._proc_audio_seen:
            logger.info("[GameAudio] Per-process capture is delivering audio")
        elif capture.packets_received:
            logger.info("[GameAudio] Per-process capture is alive "
                        f"({capture.packets_received} packets), source just quiet")
        else:
            logger.info("[GameAudio] Per-process capture is alive, "
                        "no packets yet (game idle) — keeping it")

    def _switch_capture_to(self, pid):
        """Переключить захват на другой процесс (смена активного источника)."""
        if not pid or pid == self._game_pid:
            return
        was_proc = self._proc_capture is not None
        self._stop_streams()
        self._running = False
        self._game_pid = pid
        if was_proc and self.per_process_enabled and PROC_CAP_AVAILABLE:
            if self._start_process_capture(pid):
                self._running = True
                return
        if self._start_loopback_capture():
            self._running = True

    def _start_loopback_capture(self) -> bool:
        """Системный WASAPI loopback (видим всё, включая наш TTS)."""
        if not PYAUDIO_AVAILABLE:
            logger.error("[GameAudio] pyaudiowpatch not available")
            return False

        loopbacks = self.get_loopback_devices()
        if not loopbacks:
            logger.error("[GameAudio] No WASAPI loopback device found")
            return False

        # ВАЖНО: _running поднимаем ДО старта потоков, иначе поток успевает
        # увидеть _running == False и сразу закрыть только что открытый поток.
        self._running = True
        self._active_streams = 0
        self._capture_threads = []

        # Открываем устройства по приоритету и ОСТАНАВЛИВАЕМСЯ на первом
        # успешном. Раньше открывались все сразу: кроме лишних ошибок это
        # давало дублирование звука, а мёртвые CABLE-устройства открываются
        # «успешно» (RMS ~0.00001) и молча тащат тишину в конвейер.
        for lb in loopbacks:
            t = threading.Thread(
                target=self._capture_loop, args=(lb,), daemon=True)
            t.start()
            self._capture_threads.append(t)
            logger.info(f"[GameAudio] Trying capture: {lb['name']}")

            deadline = time.time() + 2.0
            while time.time() < deadline and self._active_streams == 0 \
                    and self._running:
                time.sleep(0.05)

            if self._active_streams > 0:
                logger.info(f"[GameAudio] Capture device chosen: {lb['name']}")
                break
            logger.warning(f"[GameAudio] {lb['name']}: not opened, trying next")

        if self._active_streams == 0:
            logger.error("[GameAudio] No loopback device could be opened")
            self._running = False
            return False
        return True

    def _on_proc_audio(self, audio, sr):
        """Callback from per-process capture — feeds registered consumers."""
        self._proc_audio_seen = True
        for cb in list(self._audio_callbacks):
            try:
                cb(audio, sr)
            except Exception as e:
                logger.warning(f"[GameAudio] Callback error: {e}")

    def _on_proc_error(self, err):
        """Per-process capture failed at runtime — fall back to system loopback."""
        logger.warning(f"[GameAudio] Per-process capture failed ({err}); "
                       f"falling back to system-wide loopback")
        if self._game_pid:
            self._proc_failed[int(self._game_pid)] = (time.time(), err)
        self._running = False
        self._proc_capture = None
        try:
            self.start(force_loopback=True)
        except Exception as e:
            logger.error(f"[GameAudio] Fallback start failed: {e}")

    def _stop_streams(self):
        """Остановить все потоки/потоки захвата без сброса состояния."""
        self._running = False
        self._active_streams = 0
        # Старые потоки захвата застрянут в read(); помечаем их устаревшими
        self._gen += 1
        if getattr(self, "_proc_capture", None):
            try:
                self._proc_capture.stop()
            except Exception:
                pass
            self._proc_capture = None
        for t in list(getattr(self, "_capture_threads", [])):
            if t.is_alive():
                t.join(timeout=2.0)
        self._capture_threads = []
        t = getattr(self, "_capture_thread", None)
        if t is not None and t.is_alive():
            t.join(timeout=2.0)
        self._capture_thread = None
        # НЕ закрываем потоки отсюда: stream закрывает сам поток захвата
        # в finally. Закрытие из чужого потока во время read() роняло
        # процесс с 0xC0000374 (повреждение кучи в PortAudio).
        if self._stream:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        if self._pa:
            try:
                self._pa.terminate()
            except Exception:
                pass
            self._pa = None

    def stop(self):
        self._auto_target = False
        monitor = getattr(self, "_auto_monitor", None)
        if monitor and monitor.is_alive():
            monitor.join(timeout=3.5)
        self._auto_monitor = None
        self._stop_streams()
        if self._vbcable_enabled:
            self._restore_default_output()
        logger.info("[GameAudio] Stopped")

    def _capture_loop(self, loopback_info):
        """Capture audio from one WASAPI loopback device."""
        device_sr = int(loopback_info['defaultSampleRate'])
        device_ch = loopback_info['maxInputChannels']
        pa = None
        stream = None
        try:
            pa = pyaudio.PyAudio()
            stream = pa.open(
                format=pyaudio.paInt16,
                channels=device_ch,
                rate=device_sr,
                input=True,
                input_device_index=loopback_info['index'],
                frames_per_buffer=int(device_sr * self._chunk_sec),
            )

            chunk_samples = int(device_sr * self._chunk_sec)
            self._active_streams += 1
            gen = self._gen          # «поколение» этого захвата
            logger.info(f"[GameAudio] Listening: {loopback_info['name']} "
                        f"(sr={device_sr}, ch={device_ch})")

            while self._running and gen == self._gen:
                try:
                    data = stream.read(chunk_samples, exception_on_overflow=False)
                    audio = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0

                    if device_ch == 2:
                        audio = (audio[0::2] + audio[1::2]) / 2.0

                    peak = float(np.abs(audio).max())
                    if peak < self._silence_threshold:
                        continue

                    for cb in list(self._audio_callbacks):
                        try:
                            cb(audio, device_sr)
                        except Exception as e:
                            logger.warning(f"[GameAudio] Callback error: {e}")

                except Exception as e:
                    if self._running:
                        # Устаревшее/занятое устройство (например, отключённый
                        # VB-Cable) — молча прекращаем этот поток, остальные
                        # loopback-устройства продолжают работать.
                        logger.info(f"[GameAudio] Loopback '{loopback_info['name']}' "
                                    f"unavailable: {e}")
                        break

        except Exception as e:
            logger.warning(f"[GameAudio] Cannot open loopback "
                           f"'{loopback_info.get('name', '?')}': {e}")
        finally:
            if stream:
                try:
                    self._active_streams = max(0, self._active_streams - 1)
                    stream.stop_stream()
                    stream.close()
                except Exception:
                    pass
            if pa:
                try:
                    pa.terminate()
                except Exception:
                    pass

    @staticmethod
    def set_default_output_volume(volume: float):
        """Set master volume of the default playback device (0.0 - 1.0)."""
        if not PYCAW_AVAILABLE:
            return
        try:
            from pycaw.pycaw import AudioUtilities
            device = AudioUtilities.GetSpeakers()
            vol = max(0.0, min(1.0, float(volume)))
            device.EndpointVolume.SetMasterVolumeLevelScalar(vol, None)
            logger.info(f"[GameAudio] Set output volume to {vol:.0%}")
        except Exception as e:
            logger.warning(f"[GameAudio] Failed to set output volume: {e}")

    def get_game_volume(self) -> float:
        """Get current game audio volume."""
        if not PYCAW_AVAILABLE or not self._game_pid:
            return 1.0
        try:
            sessions = AudioUtilities.GetAllSessions()
            for s in sessions:
                if s.Process and s.Process.pid == self._game_pid:
                    return s.SimpleAudioVolume.GetMasterVolume()
        except Exception:
            pass
        return 1.0

    def set_game_volume(self, volume: float):
        """Set game audio volume (0.0 - 1.0)."""
        if not PYCAW_AVAILABLE or not self._game_pid:
            return
        try:
            sessions = AudioUtilities.GetAllSessions()
            for s in sessions:
                if s.Process and s.Process.pid == self._game_pid:
                    s.SimpleAudioVolume.SetMasterVolume(max(0.0, min(1.0, volume)), None)
        except Exception:
            pass

    def is_game_playing_audio(self) -> bool:
        """Check if game process has active audio."""
        if not PYCAW_AVAILABLE or not self._game_pid:
            return True
        try:
            sessions = AudioUtilities.GetAllSessions()
            for s in sessions:
                if s.Process and s.Process.pid == self._game_pid:
                    vol = s.SimpleAudioVolume.GetMasterVolume()
                    return vol > 0
        except Exception:
            pass
        return False


# Global instance
game_audio = GameAudioCapture()
