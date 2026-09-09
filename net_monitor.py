# -*- coding: utf-8 -*-
"""Speed-aware internet monitor for hybrid online/offline STT translation.

States:
  "fast"    — reachable and responsive  -> use cloud (Google + Edge)
  "slow"    — reachable but sluggish     -> use offline (RHVoice + local dict)
  "offline" — unreachable                -> use offline (RHVoice + local dict)
"""
import logging
import threading
import time
import urllib.request

logger = logging.getLogger(__name__)

_FAST_MAX_LATENCY = 1.5   # seconds for a small request to count as "fast"
_SLOW_MAX_LATENCY = 4.0   # above this (but reachable) counts as "slow"
_CHECK_INTERVAL = 6.0     # seconds between background re-checks
_PROBE_URL = "https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl=ru&dt=t&q=net"


class NetMonitor:
    def __init__(self):
        self._state = "fast"
        self._lock = threading.Lock()
        self._last_check = 0.0
        self._stop = False
        try:
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()
        except Exception:
            pass

    def _measure(self):
        t0 = time.time()
        try:
            req = urllib.request.Request(_PROBE_URL, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=_SLOW_MAX_LATENCY) as r:
                r.read(200)
            dt = time.time() - t0
            if dt <= _FAST_MAX_LATENCY:
                return "fast"
            return "slow"
        except Exception:
            return "offline"

    def _loop(self):
        while not self._stop:
            try:
                st = self._measure()
                with self._lock:
                    if st != self._state:
                        logger.info(f"[NetMonitor] state -> {st}")
                    self._state = st
            except Exception:
                pass
            time.sleep(_CHECK_INTERVAL)

    @property
    def state(self):
        with self._lock:
            return self._state

    def is_fast(self):
        return self.state == "fast"

    def is_offline(self):
        return self.state in ("slow", "offline")

    def stop(self):
        self._stop = True


_monitor = None


def get_monitor():
    global _monitor
    if _monitor is None:
        _monitor = NetMonitor()
    return _monitor
