# -*- coding: utf-8 -*-
"""
server_pro — автономный «проф»-сервер ScreenOCR_TTS.
Работает без GUI (headless): OCR с телефона, TTS (Edge), перевод, cleaner, SSE.

Запуск:  python server_pro.py [--port 8074] [--public]
  --public  — без него слушает только 127.0.0.1, с ним 0.0.0.0
"""
import os
import io
import sys
import json
import time
import queue
import logging
import argparse
import threading
import socket
import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from settings import Settings

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("server_pro")

CACHE_DIR = ROOT / "tts_cache"
CACHE_DIR.mkdir(exist_ok=True)

_ocr_lock = threading.Lock()
_translate_lock = threading.Lock()
_ocr_engine = None
_ocr_ready = False
_voice_cache = None
_voice_cache_at = 0.0
_subtitle_clients = []
_subtitle_lock = threading.Lock()


def get_ocr():
    global _ocr_engine, _ocr_ready
    with _ocr_lock:
        if _ocr_engine is None:
            from ocr_wrapper import OCRWrapper
            logger.info("[server_pro] Loading OCR engine...")
            _ocr_engine = OCRWrapper(Settings())
            _ocr_ready = True
            logger.info("[server_pro] OCR ready")
        return _ocr_engine


def ocr_image_bytes(data: bytes) -> dict:
    from PIL import Image
    from ocr_text_cleaner import full_clean_pipeline
    get_ocr()
    img = Image.open(io.BytesIO(data))
    if img.mode != "RGB":
        img = img.convert("RGB")
    with _ocr_lock:
        result = _ocr_engine.recognize(img)
        raw = result[0] if isinstance(result, tuple) else result
    if not raw:
        return {"ok": True, "text": "", "cleaned": ""}
    cleaned = full_clean_pipeline(str(raw).strip())
    return {"ok": True, "text": str(raw).strip(), "cleaned": cleaned}


def translate_text(text: str, dst: str = "ru") -> str:
    with _translate_lock:
        from translator import Translator
        tr = Translator(source="auto", target=dst or "ru")
        try:
            out = tr.translate(text, dst=dst or "ru")
            return out or ""
        except Exception as e:
            logger.error(f"[TRANS] {e}")
            return ""


async def edge_speak(text: str, voice: str = "ru-RU-SvetlanaNeural",
                     rate: str = "+0%", pitch: str = "+0Hz") -> str:
    import edge_tts as et
    safe = "".join(c if c.isalnum() else "_" for c in text[:40]) or "tts"
    out_path = CACHE_DIR / f"{safe}_{int(time.time()*1000)}.mp3"
    comm = et.Communicate(text=text, voice=voice, rate=rate, pitch=pitch)
    await comm.save(str(out_path))
    return out_path.name if out_path.exists() else ""


def do_speak(text: str, voice: str = "", rate: str = "+0%", pitch: str = "+0Hz") -> str:
    if not voice:
        voice = "ru-RU-SvetlanaNeural"
    try:
        return asyncio.run(edge_speak(text, voice, rate, pitch))
    except Exception as e:
        logger.error(f"[TTS] {e}")
        return ""


def get_voices():
    global _voice_cache, _voice_cache_at
    now = time.time()
    if _voice_cache and now - _voice_cache_at < 600:
        return _voice_cache
    out = {"edge": [], "fallback": []}
    try:
        import edge_tts as et
        voices = asyncio.run(et.list_voices())
    except Exception as e:
        logger.error(f"[VOICES] {e}")
        return out
    for v in voices or []:
        loc = v.get("Locale", "")
        if loc.lower().startswith(("ru", "en")):
            out["edge"].append({
                "name": v.get("ShortName", ""),
                "locale": loc,
                "gender": v.get("Gender", ""),
                "friendly_name": v.get("FriendlyName", ""),
            })
    _voice_cache, _voice_cache_at = out, now
    return out


def push_subtitle(text: str):
    if not text or not text.strip():
        return
    with _subtitle_lock:
        for q in list(_subtitle_clients):
            try:
                q.put_nowait(text)
            except Exception:
                pass


def local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


class ProHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        logger.info(f"{self.client_address[0]} {fmt % args}")

    def _json(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)

    def _html(self, html):
        data = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)

    def _file(self, path: Path, ctype: str):
        if path.exists():
            data = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(data)
        else:
            self._json({"error": "not found"}, 404)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/" or path == "/index.html":
            self._file(ROOT / "web" / "index.html", "text/html; charset=utf-8")
            return
        if path.startswith("/web/"):
            rel = path[len("/web/"):]
            f = ROOT / "web" / rel
            ctype = "text/plain"
            if rel.endswith(".html"):
                ctype = "text/html; charset=utf-8"
            elif rel.endswith(".js"):
                ctype = "application/javascript; charset=utf-8"
            elif rel.endswith(".css"):
                ctype = "text/css; charset=utf-8"
            elif rel.endswith(".png"):
                ctype = "image/png"
            elif rel.endswith(".json"):
                ctype = "application/json; charset=utf-8"
            elif rel.endswith(".svg"):
                ctype = "image/svg+xml"
            self._file(f, ctype)
            return
        if path == "/manifest.json":
            self._file(ROOT / "web" / "manifest.json", "application/manifest+json; charset=utf-8")
            return
        if path == "/sw.js":
            self._file(ROOT / "web" / "sw.js", "application/javascript; charset=utf-8")
            return
        if path == "/health":
            global _ocr_ready
            self._json({
                "ok": True,
                "app": "ScreenOCR_TTS pro-server",
                "ocr_ready": _ocr_ready,
                "engine": "pro",
                "ip": local_ip(),
                "time": time.strftime("%H:%M:%S"),
            })
            return
        if path == "/voices":
            self._json(get_voices())
            return
        if path == "/events":
            self._sse()
            return
        if path.startswith("/audio/"):
            fname = Path(self.path[len("/audio/"):]).name
            self._file(CACHE_DIR / fname, self._audio_ctype(fname))
            return
        self._json({"error": f"Not found: {path}"}, 404)

    def _audio_ctype(self, fname: str) -> str:
        ext = Path(fname).suffix.lower()
        return {"mp3": "audio/mpeg", "wav": "audio/wav", "ogg": "audio/ogg"}.get(ext, "audio/mpeg")

    def _sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        q = queue.Queue()
        with _subtitle_lock:
            _subtitle_clients.append(q)
        try:
            self.wfile.write(b"event: connected\ndata: ok\n\n")
            self.wfile.flush()
            while True:
                try:
                    text = q.get(timeout=0.2)
                    payload = json.dumps({"text": text}, ensure_ascii=False)
                    self.wfile.write(f"event: subtitle\ndata: {payload}\n\n".encode("utf-8"))
                    self.wfile.flush()
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                except (BrokenPipeError, OSError):
                    break
        except Exception:
            pass
        finally:
            with _subtitle_lock:
                try:
                    _subtitle_clients.remove(q)
                except ValueError:
                    pass

    def do_POST(self):
        path = self.path.split("?")[0]
        if path == "/ocr_image":
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length)
            self._json(ocr_image_bytes(raw))
            return
        if path == "/translate":
            length = int(self.headers.get("Content-Length", 0))
            try:
                data = json.loads(self.rfile.read(length))
            except Exception:
                self._json({"error": "Invalid JSON"}, 400)
                return
            text = data.get("text", "")
            dst = data.get("dst", "ru")
            if not text:
                self._json({"ok": False, "error": "Empty text"}, 400)
                return
            self._json({"ok": True, "dst": dst, "text": translate_text(text, dst)})
            return
        if path == "/speak":
            length = int(self.headers.get("Content-Length", 0))
            try:
                data = json.loads(self.rfile.read(length))
            except Exception:
                self._json({"error": "Invalid JSON"}, 400)
                return
            text = data.get("text", "")
            if not text:
                self._json({"ok": False, "error": "Empty text"}, 400)
                return
            fname = do_speak(text, data.get("voice", ""), data.get("rate", "+0%"), data.get("pitch", "+0Hz"))
            if fname:
                self._json({"ok": True, "file": fname})
            else:
                self._json({"ok": False, "error": "TTS failed"}, 500)
            return
        self._json({"error": f"Not found: {path}"}, 404)


def main():
    ap = argparse.ArgumentParser(description="ScreenOCR_TTS pro-server")
    ap.add_argument("--port", type=int, default=8074)
    ap.add_argument("--public", action="store_true", help="listen on 0.0.0.0")
    args = ap.parse_args()

    host = "0.0.0.0" if args.public else "127.0.0.1"
    server = ThreadingHTTPServer((host, args.port), ProHandler)
    server.daemon_threads = True

    logger.info("╔═══════════════════════════════════════════════════════╗")
    logger.info("║  ScreenOCR_TTS PRO SERVER                             ║")
    logger.info(f"║  URL: http://{('0.0.0.0' if host=='0.0.0.0' else '127.0.0.1')}:{args.port}  ║")
    logger.info(f"║  LAN: http://{local_ip()}:{args.port}")
    logger.info("║  Проф-логика: OCR, TTS (Edge), перевод, cleaner, SSE  ║")
    logger.info("╚═══════════════════════════════════════════════════════╝")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Остановка...")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()