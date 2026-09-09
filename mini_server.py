"""
Автономный сервер для Android-приложения.
Работает без GUI, без live_scanner.
Поддерживает: /health, /events (SSE), /speak, /voices, /live
"""
import sys
import os
import json
import time
import queue
import threading
import subprocess
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

PORT = 8765
TIMEOUT_AFTER_CONNECT = 120

logger = logging.getLogger("mini_server")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

CACHE_DIR = Path(__file__).parent / "tts_cache"
CACHE_DIR.mkdir(exist_ok=True)

_subtitle_clients = []
_subtitle_lock = threading.Lock()
phone_connected = threading.Event()
shutdown_event = threading.Event()
server_instance = None


def push_subtitle(text):
    if not text or not text.strip():
        return
    with _subtitle_lock:
        for q in list(_subtitle_clients):
            try:
                q.put_nowait(text)
            except Exception:
                pass


def get_all_voices():
    result = {"sapi": [], "edge": []}
    try:
        ps = (
            'Add-Type -AssemblyName System.Speech; '
            '$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; '
            '$s.GetInstalledVoices() | ForEach-Object { '
            '  $_.VoiceInfo.Name + "|" + $_.VoiceInfo.Culture.Name + "|" + $_.VoiceInfo.Gender '
            '} | Out-String'
        )
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-Command", ps],
            capture_output=True, text=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        for line in proc.stdout.strip().split("\n"):
            line = line.strip()
            if "|" in line:
                parts = line.split("|")
                result["sapi"].append(parts[0].strip())
    except Exception as e:
        logger.error(f"SAPI error: {e}")
    return result


def synthesize_sapi(text, voice=""):
    try:
        safe_text = text.replace("'", "''")
        voice_param = f"-Voice '{voice}'" if voice else ""
        ps = (
            f'Add-Type -AssemblyName System.Speech; '
            f'$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; '
            f'{voice_param}; '
            f'$out = "{CACHE_DIR}\\out.wav"; '
            f'$s.SetOutputToWaveFile($out); '
            f'$s.Speak("{safe_text}"); '
            f'$s.SetOutputToNull(); '
            f'Write-Output $out'
        )
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-Command", ps],
            capture_output=True, text=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        wav_path = proc.stdout.strip()
        if wav_path and Path(wav_path).exists():
            return Path(wav_path).name
    except Exception as e:
        logger.error(f"SAPI synthesize error: {e}")
    return None


class MiniHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        logger.info(f"{self.client_address[0]} {fmt % args}")

    def _json(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def _text(self, text, code=200, content_type="text/plain"):
        data = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def _html(self, html):
        self._text(html, content_type="text/html")

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?")[0]

        if path == "/health":
            self._json({"ok": True, "engine": "SAPI", "mode": "mini_server"})

        elif path == "/voices":
            self._json(get_all_voices())

        elif path == "/events":
            global phone_connected
            if not phone_connected.is_set():
                phone_connected.set()
                logger.info(">>> ТЕЛЕФОН ПОДКЛЮЧЁН ЧЕРЕЗ SSE!")
                logger.info(f">>> Таймер {TIMEOUT_AFTER_CONNECT}с запущен")
                threading.Timer(TIMEOUT_AFTER_CONNECT, on_timeout).start()

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
                while True:
                    try:
                        text = q.get(timeout=15)
                        event_data = json.dumps({"text": text}, ensure_ascii=False)
                        self.wfile.write(f"event: subtitle\ndata: {event_data}\n\n".encode("utf-8"))
                        self.wfile.flush()
                    except queue.Empty:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
            except Exception:
                pass
            finally:
                with _subtitle_lock:
                    try:
                        _subtitle_clients.remove(q)
                    except ValueError:
                        pass

        elif path == "/live":
            live_html = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Live TTS</title>
<style>
body{background:#1a1a2e;color:#fff;font-family:monospace;text-align:center;padding:40px;margin:0}
#sub{font-size:28px;margin:20px 0;min-height:60px;color:#f5c2e7}
#st{color:#a6e3a1;font-size:16px}
</style></head><body>
<h1>Screen Overlay TTS - Live</h1>
<div id="st">Подключение...</div>
<div id="sub"></div>
<script>
const es=new EventSource('/events');
es.onopen=()=>{document.getElementById('st').textContent='Подключено!'};
es.addEventListener('subtitle',e=>{
  const d=JSON.parse(e.data);
  document.getElementById('sub').textContent=d.text;
  if('speechSynthesis' in window){
    const u=new SpeechSynthesisUtterance(d.text);
    u.lang='ru-RU';u.rate=1;
    speechSynthesis.speak(u);
  }
});
es.onerror=()=>{document.getElementById('st').textContent='Переподключение...'};
</script></body></html>"""
            self._html(live_html)

        elif path.startswith("/audio/"):
            fname = path.split("/audio/", 1)[1]
            fpath = CACHE_DIR / fname
            if fpath.exists():
                data = fpath.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "audio/wav")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            else:
                self._json({"error": "not found"}, 404)
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        if self.path == "/speak":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else {}
            text = body.get("text", "")
            voice = body.get("voice", "")

            if not text:
                self._json({"error": "no text"}, 400)
                return

            fname = synthesize_sapi(text, voice)
            if fname:
                self._json({"ok": True, "file": fname})
            else:
                self._json({"ok": False, "error": "synthesis failed"}, 500)
        else:
            self._json({"error": "not found"}, 404)


def on_timeout():
    logger.info(">>> 2 минуты прошли. Остановка сервера...")
    shutdown_event.set()
    if server_instance:
        server_instance.shutdown()


def get_local_ip():
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


def main():
    global server_instance

    ip = get_local_ip()
    logger.info("╔═══════════════════════════════════════════════╗")
    logger.info("║  Mini Server для Screen Overlay TTS           ║")
    logger.info(f"║  IP:   {ip:<37}║")
    logger.info(f"║  Port: {PORT:<37}║")
    logger.info(f"║  Таймер: {TIMEOUT_AFTER_CONNECT}с после подключения   ║")
    logger.info("╚═══════════════════════════════════════════════╝")
    logger.info(f"Введи в приложении: {ip}:{PORT}")
    logger.info("Ожидание подключения телефона... (Ctrl+C для остановки)")

    server_instance = ThreadingHTTPServer(("0.0.0.0", PORT), MiniHandler)
    server_instance.daemon_threads = True

    def serve():
        server_instance.serve_forever()

    t = threading.Thread(target=serve, daemon=True)
    t.start()

    try:
        while not shutdown_event.is_set():
            shutdown_event.wait(timeout=1)
    except KeyboardInterrupt:
        logger.info("Остановка...")
    finally:
        try:
            server_instance.shutdown()
        except Exception:
            pass
        logger.info("Сервер остановлен.")


if __name__ == "__main__":
    main()
