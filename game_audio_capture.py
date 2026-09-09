# -*- coding: utf-8 -*-
"""
Game Audio Capture — WASAPI loopback + per-process filtering.
Captures audio from the game window specifically, without headphones/mic noise.
"""
import ctypes
import logging
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
        self._chunk_sec = 1.0  # faster audio delivery -> lower STT latency
        self._audio_callbacks = []  # list of callables(audio, sample_rate)
        self._proc_capture = None
        self._capture_thread = None
        self.per_process_enabled = False  # opt-in: per-process COM code is fragile
        self._silence_threshold = 0.005
        self._voice_threshold = 0.01  # min peak to count as "voice/audio activity"
        self._saved_default_output = None  # saved before VB-Cable switch
        self._vbcable_enabled = True  # auto-switch to VB-Cable if available

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
            p_dev = c_void_p()
            enumerator.GetDevice(device_id, c_void_p.__compat_type__())
            # Get the device
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

    def _switch_to_vbcable(self):
        """Switch default output to CABLE Input, save original for restore."""
        try:
            from pycaw.pycaw import AudioUtilities
            speakers = AudioUtilities.GetSpeakers()
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
        """Restore original default output device."""
        if self._saved_default_output is None:
            return
        try:
            if PYCAW_AVAILABLE:
                devices = AudioUtilities.GetAllDevices()
                for d in devices:
                    name = d.FriendlyName or ""
                    if "AMD" in name and "Loopback" not in name and d.state == 1:
                        ok = self._set_default_render(d.id)
                        if ok:
                            logger.info(f"[GameAudio] Default output restored: {name}")
                        break
            self._saved_default_output = None
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
        """Find the target process.

        Priority: the currently FOCUSED window's PID (the app the user is
        actually looking at), then fall back to a name-pattern search.
        We no longer grab unrelated processes (e.g. a game running on
        another virtual desktop) just because their name matches.
        """
        if not PYCAW_AVAILABLE:
            return None

        sessions = AudioUtilities.GetAllSessions()

        # 1) Focused window first — this is the "current PID" the user wants.
        fg_pid = self._get_foreground_pid()
        if fg_pid:
            for s in sessions:
                if s.Process and s.Process.pid == fg_pid:
                    name = s.Process.name()
                    vol = s.SimpleAudioVolume.GetMasterVolume()
                    self._game_pid = fg_pid
                    self._game_name = name
                    logger.info(f"[GameAudio] Focused window: {name} (PID={fg_pid}, vol={vol:.2f})")
                    return {"pid": fg_pid, "name": name, "volume": vol}

        # 2) Fallback: keyword match (game launchers / engines)
        game_keywords = [
            'fnaf', 'nte', 'game', 'unreal', 'unity', 'godot',
            'epic', 'origin', 'battle', 'blizzard', 'segaa', 'kono',
            'shipping', 'win64',
        ]
        launcher_keywords = ['steam', 'epicgames', 'origin', 'battle.net']

        best = None
        for s in sessions:
            if s.Process:
                name = s.Process.name().lower()
                pid = s.Process.pid
                vol = s.SimpleAudioVolume.GetMasterVolume()

                is_launcher = any(kw in name for kw in launcher_keywords)
                is_game = any(kw in name for kw in game_keywords) and not is_launcher

                if is_game and (best is None or not best.get("_is_game")):
                    best = {"pid": pid, "name": s.Process.name(), "volume": vol, "_is_game": True}
                elif not is_launcher and best is None:
                    best = {"pid": pid, "name": s.Process.name(), "volume": vol, "_is_game": False}

        if best:
            best.pop("_is_game", None)
            self._game_pid = best["pid"]
            self._game_name = best["name"]
            logger.info(f"[GameAudio] Found (keyword): {self._game_name} (PID={self._game_pid}, vol={best['volume']:.2f})")

        return best

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
        """Start capturing audio.

        Auto-switches default output to VB-Cable (CABLE Input) so game audio
        is routed through the virtual cable. Restores on stop().
        """
        if self._running:
            return

        if game_pid is not None:
            self._game_pid = game_pid
        elif not self._game_pid:
            game = self.find_game_process()
            if game:
                self._game_pid = game["pid"]
                self._game_name = game["name"]
            else:
                logger.warning("[GameAudio] No target process found, capturing all system audio")

        # Auto-switch default output to VB-Cable
        if self._vbcable_enabled:
            self._switch_to_vbcable()

        # Per-process capture only for an EXPLICITLY selected exe AND when
        # enabled (opt-in — the COM code is fragile and can hard-crash).
        explicit = game_pid is not None
        if (explicit and self.per_process_enabled and not force_loopback
                and PROC_CAP_AVAILABLE and proc_cap_mod.is_supported()):
            try:
                self._proc_capture = proc_cap_mod.ProcessLoopbackCapture(
                    self._game_pid, self._on_proc_audio,
                    silence_threshold=self._silence_threshold,
                    on_error=self._on_proc_error)
                self._running = True
                self._proc_capture.start()
                logger.info(f"[GameAudio] Per-process capture started "
                            f"(PID={self._game_pid}, app={self._game_name})")
                return
            except Exception as e:
                logger.warning(f"[GameAudio] Per-process init failed, "
                               f"falling back to system loopback: {e}")

        # Fallback: system-wide WASAPI loopback — capture from ALL loopback devices
        if not PYAUDIO_AVAILABLE:
            logger.error("[GameAudio] pyaudiowpatch not available")
            return

        loopbacks = self.get_loopback_devices()
        if not loopbacks:
            logger.error("[GameAudio] No WASAPI loopback device found")
            return

        self._running = True
        self._capture_threads = []
        for lb in loopbacks:
            sr = int(lb['defaultSampleRate'])
            ch = lb['maxInputChannels']
            t = threading.Thread(
                target=self._capture_loop, args=(lb,), daemon=True)
            t.start()
            self._capture_threads.append(t)
            logger.info(f"[GameAudio] Started capture: {lb['name']} (sr={sr})")

    def _on_proc_audio(self, audio, sr):
        """Callback from per-process capture — feeds registered consumers."""
        for cb in list(self._audio_callbacks):
            try:
                cb(audio, sr)
            except Exception as e:
                logger.warning(f"[GameAudio] Callback error: {e}")

    def _on_proc_error(self, err):
        """Per-process capture failed at runtime — fall back to system loopback."""
        logger.warning(f"[GameAudio] Per-process capture failed ({err}); "
                       f"falling back to system-wide loopback")
        self._running = False
        self._proc_capture = None
        try:
            self.start(force_loopback=True)
        except Exception as e:
            logger.error(f"[GameAudio] Fallback start failed: {e}")

    def stop(self):
        self._running = False
        if getattr(self, "_proc_capture", None):
            try:
                self._proc_capture.stop()
            except Exception:
                pass
            self._proc_capture = None
        # Wait for all capture threads to exit
        for t in getattr(self, "_capture_threads", []):
            if t.is_alive():
                t.join(timeout=3.0)
        self._capture_threads = []
        t = getattr(self, "_capture_thread", None)
        if t is not None and t.is_alive():
            t.join(timeout=3.0)
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
        # Restore default output device
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

            while self._running:
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
                        logger.warning(f"[GameAudio] Read error ({loopback_info['name']}): {e}")
                        time.sleep(0.1)

        except Exception as e:
            logger.error(f"[GameAudio] Capture error ({loopback_info['name']}): {e}")
        finally:
            if stream:
                try:
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
