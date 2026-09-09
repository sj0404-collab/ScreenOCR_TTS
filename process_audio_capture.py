# -*- coding: utf-8 -*-
"""
Per-process (application) WASAPI loopback capture.

Captures audio from ONE specific process only (by PID), ignoring all other
system audio. Uses the Windows 10 2004+ AUDIOCLIENT_PROCESS_LOOPBACK_PARAMS
API (IAudioClient2::SetClientProperties with bIsProcessLoopback = TRUE).

Falls back gracefully: is_supported() returns False on older Windows, and the
caller (game_audio_capture) keeps using the system-wide loopback in that case.
"""
import logging
import sys
import threading
import ctypes
import numpy as np

logger = logging.getLogger(__name__)

try:
    import comtypes
    from comtypes import GUID, IUnknown, COMMETHOD, POINTER, c_void_p
    from ctypes import (
        HRESULT, c_int, c_int64, c_uint, c_uint32, c_uint64, c_ulong, c_ushort,
    )
    COMTYPES_AVAILABLE = True
except Exception as e:  # pragma: no cover
    COMTYPES_AVAILABLE = False
    logger.warning(f"[ProcCapture] comtypes unavailable: {e}")


# ── GUIDs ───────────────────────────────────────────────────
IID_IAudioClient = GUID('{1CB9AD4C-DBFA-4C32-B178-C2F568A703B2}')
IID_IAudioClient2 = GUID('{72658AB2-BCD9-44C2-9023-369E0C8F9C8D}')
IID_IAudioCaptureClient = GUID('{C8ADBD64-E71E-48A0-A4DE-185C395CD317}')
IID_IMMDeviceEnumerator = GUID('{A95664D2-9614-4F35-A746-DE8DB63617E6}')
CLSID_MMDeviceEnumerator = GUID('{BCDE0395-E721-11CE-BE1D-00AA0057B223}')


# ── Constants ───────────────────────────────────────────────
AUDCLNT_SHAREMODE_SHARED = 1
AUDCLNT_STREAMFLAGS_LOOPBACK = 0x00020000
E_RENDER = 0
E_CONSOLE = 0
CLSCTX_ALL = 0x17
COINIT_MULTITHREADED = 0x0

WAVE_FORMAT_PCM = 1
WAVE_FORMAT_IEEE_FLOAT = 3
WAVE_FORMAT_EXTENSIBLE = 0xFFFE


# ── Structures ──────────────────────────────────────────────
class WAVEFORMATEX(ctypes.Structure):
    _fields_ = [
        ('wFormatTag', c_ushort),
        ('nChannels', c_ushort),
        ('nSamplesPerSec', c_uint),
        ('nAvgBytesPerSec', c_uint),
        ('nBlockAlign', c_ushort),
        ('wBitsPerSample', c_ushort),
        ('cbSize', c_ushort),
    ]


class AUDIOCLIENT_PROCESS_LOOPBACK_PARAMS(ctypes.Structure):
    _fields_ = [
        ('ProcessId', c_ulong),
        ('IncludeProcessTree', c_int),
    ]


class AudioClientProperties(ctypes.Structure):
    _fields_ = [
        ('cbSize', c_uint),
        ('bIsProcessLoopback', c_int),
        ('ProcessLoopbackParams', AUDIOCLIENT_PROCESS_LOOPBACK_PARAMS),
    ]


# ── COM interfaces (minimal vtable) ─────────────────────────
if COMTYPES_AVAILABLE:
    class IMMDeviceEnumerator(comtypes.IUnknown):
        _iid_ = IID_IMMDeviceEnumerator
        _methods_ = [
            COMMETHOD([], HRESULT, 'EnumAudioEndpoints',
                      (['in'], c_int, 'dataFlow'),
                      (['in'], c_uint, 'dwStateMask'),
                      (['out'], POINTER(c_void_p), 'ppDevices')),
            COMMETHOD([], HRESULT, 'GetDefaultAudioEndpoint',
                      (['in'], c_int, 'dataFlow'),
                      (['in'], c_int, 'role'),
                      (['out'], POINTER(c_void_p), 'ppEndpoint')),
        ]

    class IMMDevice(comtypes.IUnknown):
        _iid_ = GUID('{D666063F-1587-4E43-81F1-B948E807363F}')
        _methods_ = [
            COMMETHOD([], HRESULT, 'Activate',
                      (['in'], POINTER(GUID), 'iid'),
                      (['in'], c_uint, 'dwClsCtx'),
                      (['in'], c_void_p, 'pActivationParams'),
                      (['out'], POINTER(c_void_p), 'ppInterface')),
        ]

    class IAudioClient(comtypes.IUnknown):
        _iid_ = IID_IAudioClient
        _methods_ = [
            COMMETHOD([], HRESULT, 'Initialize',
                      (['in'], c_int, 'ShareMode'),
                      (['in'], c_uint32, 'StreamFlags'),
                      (['in'], c_int64, 'hnsBufferDuration'),
                      (['in'], c_int64, 'hnsPeriodicity'),
                      (['in'], POINTER(WAVEFORMATEX), 'pFormat'),
                      (['in'], POINTER(GUID), 'AudioSessionGuid')),
            COMMETHOD([], HRESULT, 'GetBufferSize',
                      (['out'], POINTER(c_uint32), 'pNumBufferFrames')),
            COMMETHOD([], HRESULT, 'GetStreamLatency',
                      (['out'], POINTER(c_int64), 'phnsLatency')),
            COMMETHOD([], HRESULT, 'GetCurrentPadding',
                      (['out'], POINTER(c_uint32), 'pNumPaddingFrames')),
            COMMETHOD([], HRESULT, 'IsFormatSupported',
                      (['in'], c_int, 'ShareMode'),
                      (['in'], POINTER(WAVEFORMATEX), 'pFormat'),
                      (['out'], POINTER(c_void_p), 'ppClosestMatch')),
            COMMETHOD([], HRESULT, 'GetMixFormat',
                      (['out'], POINTER(POINTER(WAVEFORMATEX)), 'ppDeviceFormat')),
            COMMETHOD([], HRESULT, 'GetDevicePeriod',
                      (['out'], POINTER(c_int64), 'phnsDefaultDevicePeriod'),
                      (['out'], POINTER(c_int64), 'phnsMinimumDevicePeriod')),
            COMMETHOD([], HRESULT, 'Start'),
            COMMETHOD([], HRESULT, 'Stop'),
            COMMETHOD([], HRESULT, 'Reset'),
            COMMETHOD([], HRESULT, 'SetEventHandle',
                      (['in'], c_void_p, 'eventHandle')),
            COMMETHOD([], HRESULT, 'GetService',
                      (['in'], POINTER(GUID), 'riid'),
                      (['out'], POINTER(c_void_p), 'ppv')),
        ]

    class IAudioClient2(IAudioClient):
        _iid_ = IID_IAudioClient2
        _methods_ = [
            COMMETHOD([], HRESULT, 'IsOffloadCapable',
                      (['in'], c_int, 'Category'),
                      (['out'], POINTER(c_int), 'pbOffloadCapable')),
            COMMETHOD([], HRESULT, 'SetClientProperties',
                      (['in'], POINTER(AudioClientProperties), 'pProperties')),
            COMMETHOD([], HRESULT, 'GetBufferSizeLimits',
                      (['in'], POINTER(WAVEFORMATEX), 'pFormat'),
                      (['in'], c_int, 'bEventDriven'),
                      (['out'], POINTER(c_int64), 'phnsMinBufferDuration'),
                      (['out'], POINTER(c_int64), 'phnsMaxBufferDuration')),
        ]

    class IAudioCaptureClient(comtypes.IUnknown):
        _iid_ = IID_IAudioCaptureClient
        _methods_ = [
            COMMETHOD([], HRESULT, 'GetBuffer',
                      (['out'], POINTER(c_void_p), 'ppData'),
                      (['out'], POINTER(c_uint32), 'pNumFramesToRead'),
                      (['out'], POINTER(c_uint32), 'pdwFlags'),
                      (['out'], POINTER(c_uint64), 'pu64DevicePosition'),
                      (['out'], POINTER(c_uint64), 'pu64QPCPosition')),
            COMMETHOD([], HRESULT, 'ReleaseBuffer',
                      (['in'], c_uint32, 'NumFramesRead')),
            COMMETHOD([], HRESULT, 'GetNextPacketSize',
                      (['out'], POINTER(c_uint32), 'pNumFramesInNextPacket')),
            COMMETHOD([], HRESULT, 'GetDevicePosition',
                      (['out'], POINTER(c_uint64), 'pu64DevicePosition'),
                      (['out'], POINTER(c_uint64), 'pu64QPCPosition')),
            COMMETHOD([], HRESULT, 'GetAdapterDevicePosition',
                      (['out'], POINTER(c_uint64), 'pu64DevicePosition'),
                      (['out'], POINTER(c_uint64), 'pu64QPCPosition')),
        ]


def is_supported():
    """True if the OS supports per-process loopback (Win10 2004+)."""
    if not COMTYPES_AVAILABLE:
        return False
    try:
        v = sys.getwindowsversion()
        return (v.major, v.build) >= (10, 19041)
    except Exception:
        return False


def enumerate_audio_processes():
    """Return list of (pid, name) for processes currently producing audio."""
    procs = []
    try:
        from pycaw.pycaw import AudioUtilities
        for s in AudioUtilities.GetAllSessions():
            if s.Process:
                try:
                    procs.append((s.Process.pid, s.Process.name()))
                except Exception:
                    pass
    except Exception as e:
        logger.warning(f"[ProcCapture] enumerate failed: {e}")
    seen = set()
    out = []
    for pid, name in procs:
        if pid not in seen:
            seen.add(pid)
            out.append((pid, name))
    return out


class ProcessLoopbackCapture:
    """Captures audio from a single process (PID) only."""

    def __init__(self, pid, callback, silence_threshold=0.005, on_error=None):
        self.pid = int(pid)
        self.callback = callback
        self.silence_threshold = silence_threshold
        self.on_error = on_error
        self._running = False
        self._started = False
        self._thread = None
        self.sample_rate = 48000
        self.channels = 2
        self._client = None
        self._capture = None

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def _to_float_mono(self, data_ptr, nframes, wave_fmt):
        n = nframes * self.channels
        if wave_fmt == WAVE_FORMAT_IEEE_FLOAT:
            raw = (ctypes.c_float * n).from_address(data_ptr)
            audio = np.frombuffer(raw, dtype=np.float32).copy()
        elif wave_fmt == WAVE_FORMAT_PCM:
            raw = (ctypes.c_int16 * n).from_address(data_ptr)
            audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        else:
            return None
        audio = audio.reshape(nframes, self.channels)
        if self.channels > 1:
            audio = audio.mean(axis=1)
        return audio.astype(np.float32)

    def _run(self):
        try:
            comtypes.CoInitializeEx(None, COINIT_MULTITHREADED)
        except Exception:
            try:
                comtypes.CoInitialize()
            except Exception:
                pass
        try:
            enum = comtypes.CoCreateInstance(
                CLSID_MMDeviceEnumerator, IMMDeviceEnumerator,
                comtypes.CLSCTX_ALL)
            endpoint = enum.GetDefaultAudioEndpoint(E_RENDER, E_CONSOLE)
            pp = c_void_p()
            endpoint.Activate(IID_IAudioClient2, CLSCTX_ALL, None, pp)
            client = comtypes.cast(pp, POINTER(IAudioClient2)).contents
            self._client = client

            props = AudioClientProperties()
            props.cbSize = ctypes.sizeof(AudioClientProperties)
            props.bIsProcessLoopback = 1
            props.ProcessLoopbackParams.ProcessId = c_ulong(self.pid)
            props.ProcessLoopbackParams.IncludeProcessTree = 1
            client.SetClientProperties(props)

            mixpp = POINTER(WAVEFORMATEX)()
            client.GetMixFormat(mixpp)
            fmt = mixpp.contents
            self.sample_rate = fmt.nSamplesPerSec
            self.channels = fmt.nChannels
            wave_fmt = fmt.wFormatTag
            if wave_fmt == WAVE_FORMAT_EXTENSIBLE:
                wave_fmt = WAVE_FORMAT_IEEE_FLOAT

            client.Initialize(
                AUDCLNT_SHAREMODE_SHARED, AUDCLNT_STREAMFLAGS_LOOPBACK,
                0, 0, fmt, None)

            srv = c_void_p()
            client.GetService(IID_IAudioCaptureClient, srv)
            cap = comtypes.cast(srv, POINTER(IAudioCaptureClient)).contents
            self._capture = cap

            client.Start()
            self._started = True
            logger.info(f"[ProcCapture] Capturing PID={self.pid} "
                        f"(sr={self.sample_rate}, ch={self.channels})")

            while self._running:
                nxt = c_uint32(0)
                cap.GetNextPacketSize(nxt)
                if nxt.value == 0:
                    continue
                ppdata = c_void_p()
                nframes = c_uint32(0)
                flags = c_uint32(0)
                cap.GetBuffer(ppdata, nframes, flags, None, None)
                try:
                    if nframes.value == 0:
                        continue
                    audio = self._to_float_mono(
                        ppdata, nframes.value, wave_fmt)
                    if audio is None:
                        continue
                    peak = float(np.abs(audio).max())
                    if peak < self.silence_threshold:
                        continue
                    try:
                        self.callback(audio, self.sample_rate)
                    except Exception as e:
                        logger.warning(f"[ProcCapture] callback error: {e}")
                finally:
                    cap.ReleaseBuffer(nframes.value)
        except Exception as e:
            logger.error(f"[ProcCapture] Capture error (PID={self.pid}): {e}")
            # Notify owner so it can fall back to system loopback
            try:
                if self.on_error:
                    self.on_error(e)
            except Exception:
                pass
        finally:
            try:
                if self._client:
                    self._client.Stop()
            except Exception:
                pass
            self._running = False
            logger.info(f"[ProcCapture] Stopped (PID={self.pid})")
