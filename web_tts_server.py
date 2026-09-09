# -*- coding: utf-8 -*-
"""
Веб-сервер TTS — запускается вместе с основным приложением.

Два режима:
1) /        — ручная озвучка через системный SAPI (синтез на сервере в wav).
2) /live    — LIVE-субтитры: текст из рамки транслируется на страницу через
              SSE и озвучивается ВСТРОЕННЫМ голосом браузера (Web Speech API,
              в Яндекс.Браузере — голос Алисы) в реальном времени.

Доступен по http://127.0.0.1:8765

Эндпоинты:
  GET  /         — страница ручной озвучки (SAPI)
  GET  /live     — страница live-субтитров (голос браузера)
  GET  /events   — SSE-поток текста для live-страницы
  POST /speak    — {"text","voice"} → синтез SAPI в wav
  GET  /voices   — список SAPI-голосов
  GET  /audio/<f>— отдать сгенерированный wav
  GET  /health   — статус
"""
import json
import logging
import os
import queue
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger("web_tts")

CACHE_DIR = Path(__file__).parent / "tts_cache"
CACHE_DIR.mkdir(exist_ok=True)

# ==================== LIVE-субтитры: очереди подписчиков ====================
_subtitle_clients = []          # list[queue.Queue]
_subtitle_lock = threading.Lock()
_ocr_result_clients = []        # list[queue.Queue] — OCR scan results
_ocr_result_lock = threading.Lock()

# GUI log callback list
_log_callbacks = []  # list[Callable[[str], None]]
_log_lock = threading.Lock()

# ==================== Callback registry для связи с GUI ====================
_gui_callbacks = {}  # type: dict[str, Callable]
_gui_callbacks_lock = threading.Lock()


def register_gui_callbacks(callbacks: dict):
    """Регистрация callback-ов из gui.py для связи web API с MainWindow."""
    with _gui_callbacks_lock:
        _gui_callbacks.update(callbacks)


def _get_cb(name: str):
    with _gui_callbacks_lock:
        return _gui_callbacks.get(name)


def push_subtitle(text: str):
    """Вызывается из live_scanner — рассылает новый текст всем SSE-клиентам."""
    if not text or not text.strip():
        return
    with _subtitle_lock:
        for q in list(_subtitle_clients):
            try:
                q.put_nowait(text)
            except Exception:
                pass


def push_ocr_result(text: str, region_name: str = ""):
    """Вызывается из gui.py при завершении OCR — рассылает результат всем SSE-клиентам."""
    if not text:
        return
    payload = json.dumps({"text": text, "region": region_name}, ensure_ascii=False)
    with _ocr_result_lock:
        for q in list(_ocr_result_clients):
            try:
                q.put_nowait(payload)
            except Exception:
                pass


# ==================== SAPI ====================
def get_all_voices() -> dict:
    """Список голосов всех доступных движков: SAPI + Edge-TTS."""
    result = {"sapi": [], "edge": []}

    # SAPI (локальные)
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
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
        for line in proc.stdout.strip().split("\n"):
            line = line.strip()
            if "|" in line:
                parts = line.split("|")
                result["sapi"].append({
                    "name": parts[0].strip(),
                    "culture": parts[1].strip() if len(parts) > 1 else "",
                    "gender": parts[2].strip() if len(parts) > 2 else "",
                })
    except Exception as e:
        logger.error(f"SAPI voices error: {e}")

    # Edge-TTS (облачные, включая Алису)
    try:
        import asyncio as _aio
        import edge_tts as _et
        _loop = _aio.new_event_loop()
        voices = _loop.run_until_complete(_et.list_voices())
        _loop.close()
        for v in voices:
            name = v.get("ShortName", "")
            # Фильтруем русские и английские
            locale = v.get("Locale", "")
            if locale.startswith("ru") or locale.startswith("en"):
                result["edge"].append({
                    "name": name,
                    "locale": locale,
                    "gender": v.get("Gender", ""),
                    "friendly_name": v.get("FriendlyName", name),
                })
    except Exception as e:
        logger.error(f"Edge-TTS voices error: {e}")

    return result


def speak_engine(text: str, engine: str = "auto", voice_name: str = None) -> str:
    """Синтез с автоматическим fallback: edge → sapi.
    Возвращает путь к аудиофайлу."""
    safe = "".join(c if c.isalnum() else "_" for c in text[:30])
    suffix = f"_{int(time.time()*1000)}"

    if engine == "edge":
        result = _speak_edge(text, voice_name, safe, suffix)
        if result:
            return result
        logger.warning("[SPEAK] Edge failed, fallback to SAPI")
        return _sapi_speak(text, voice_name, safe, suffix)

    if engine == "sapi":
        result = _sapi_speak(text, voice_name, safe, suffix)
        if result:
            return result
        logger.warning("[SPEAK] SAPI failed, fallback to Edge")
        return _speak_edge(text, voice_name, safe, suffix)

    # auto: try edge first (better quality), fallback to sapi
    result = _speak_edge(text, voice_name, safe, suffix)
    if result:
        return result
    logger.warning("[SPEAK] Edge failed, fallback to SAPI")
    return _sapi_speak(text, voice_name, safe, suffix)


def _sapi_speak(text: str, voice_name: str, safe: str, suffix: str) -> str:
    out_path = CACHE_DIR / f"{safe}{suffix}.wav"
    parts = [
        'Add-Type -AssemblyName System.Speech;',
        '$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;',
    ]
    if voice_name:
        parts.append(f'$s.SelectVoice("{voice_name}");')
    parts.append(f'$s.SetOutputToWaveFile("{out_path}");')
    parts.append('$s.Speak("' + text.replace('"', '\\"') + '");')
    parts.append('$s.SetOutputToDefaultAudioDevice();')
    ps = " ".join(parts)
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-Command", ps],
            capture_output=True, text=True, timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
        if out_path.exists() and out_path.stat().st_size > 100:
            return str(out_path)
        return ""
    except Exception as e:
        logger.error(f"SAPI ошибка: {e}")
        return ""


def _add_pauses_ssml(text: str) -> str:
    """Добавляет SSML-паузы по знакам препинания для Edge-TTS."""
    import re
    if not text:
        return text
    result = text
    result = re.sub(r'\.\.\.', '... <break time="500ms"/>', result)
    result = re.sub(r'([.!?])(\s+|$)', r'\1 <break time="600ms"/> ', result)
    result = re.sub(r'([,;:])(\s+|$)', r'\1 <break time="300ms"/> ', result)
    result = re.sub(r'([—–])(\s+|$)', r'\1 <break time="200ms"/> ', result)
    result = re.sub(r'\n+', ' <break time="500ms"/> ', result)
    return result


def _speak_edge(text: str, voice_name: str, safe: str, suffix: str) -> str:
    """Синтез через Edge-TTS (включая Алису)."""
    out_path = CACHE_DIR / f"{safe}{suffix}.mp3"
    if not voice_name:
        voice_name = "ru-RU-SvetlanaNeural"  # русский по умолчанию
    try:
        import asyncio as _aio
        import edge_tts as _et

        async def _gen():
            ssml_text = _add_pauses_ssml(text)
            comm = _et.Communicate(text=ssml_text, voice=voice_name)
            await comm.save(str(out_path))

        loop = _aio.new_event_loop()
        loop.run_until_complete(_gen())
        loop.close()

        if out_path.exists() and out_path.stat().st_size > 100:
            return str(out_path)
        return ""
    except Exception as e:
        logger.error(f"Edge-TTS ошибка: {e}")
        return ""


# ==================== HTML страницы ====================
MANUAL_PAGE = """<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8">
<title>Web TTS</title><style>
*{box-sizing:border-box;margin:0;padding:0}body{font-family:'Segoe UI',Arial,sans-serif;
background:#1e1e2e;color:#cdd6f4;display:flex;justify-content:center;padding:40px 20px}
.card{background:#2b2b3b;border-radius:12px;padding:32px;width:100%;max-width:600px}
h1{font-size:20px;color:#89b4fa;margin-bottom:8px}p{font-size:13px;color:#a6adc8;margin-bottom:20px}
a{color:#89b4fa}textarea{width:100%;height:120px;background:#181825;color:#cdd6f4;
border:1px solid #45475a;border-radius:8px;padding:12px;font-size:14px}
.row{display:flex;gap:10px;margin-top:12px}select{flex:1;background:#181825;color:#cdd6f4;
border:1px solid #45475a;border-radius:6px;padding:8px}button{background:#89b4fa;color:#1e1e2e;
border:none;border-radius:8px;padding:10px 24px;font-weight:bold;cursor:pointer}
#status{margin-top:12px;font-size:12px;color:#a6adc8}audio{margin-top:12px;width:100%}
</style></head><body><div class="card"><h1>🎙 Web TTS (SAPI)</h1>
<p>Синтез на сервере · <a href="/live">→ Live-субтитры (голос браузера)</a></p>
<textarea id="txt" placeholder="Введите текст..."></textarea>
<div class="row"><select id="voice"><option value="">— Голос по умолчанию —</option></select>
<button onclick="speak()">▶ Озвучить</button></div><div id="status"></div>
<audio id="player" controls style="display:none"></audio></div><script>
async function lv(){try{const r=await fetch('/voices');const d=await r.json();
const s=document.getElementById('voice');const all=[...(d.sapi||[]),...(d.edge||[])];
for(const v of all){const o=document.createElement('option');
o.value=v.name;o.textContent=v.name+' ('+(v.culture||v.locale||'')+')';s.appendChild(o);}}catch(e){}}lv();
async function speak(){const t=document.getElementById('txt').value.trim();if(!t)return;
const st=document.getElementById('status');st.textContent='Синтез...';
try{const r=await fetch('/speak',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({text:t,voice:document.getElementById('voice').value})});const d=await r.json();
if(d.ok){st.textContent='✅ Готово';const p=document.getElementById('player');p.src='/audio/'+d.file;
p.style.display='block';p.play();}else{st.textContent='❌ '+(d.error||'Ошибка');}}catch(e){st.textContent='❌ '+e.message;}}
</script></body></html>"""

LIVE_PAGE = """<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8">
<title>Live субтитры</title><style>
*{box-sizing:border-box;margin:0;padding:0}body{font-family:'Segoe UI',Arial,sans-serif;
background:#0d1117;color:#c9d1d9;display:flex;flex-direction:column;align-items:center;padding:30px 20px}
h1{font-size:22px;color:#58a6ff;margin-bottom:6px}p{font-size:13px;color:#8b949e;margin-bottom:20px}
a{color:#58a6ff}#box{background:#161b22;border:1px solid #30363d;border-radius:12px;padding:24px;
width:100%;max-width:700px;min-height:200px}#text{font-size:19px;line-height:1.6;white-space:pre-wrap}
#status{margin-top:12px;font-size:12px}.ok{color:#3fb950}.err{color:#f85149}
.row{display:flex;gap:10px;margin-top:16px;width:100%;max-width:700px}select{flex:1;background:#21262d;
color:#c9d1d9;border:1px solid #30363d;border-radius:6px;padding:8px}button{background:#238636;color:#fff;
border:none;border-radius:8px;padding:10px 20px;font-weight:bold;cursor:pointer}button:hover{background:#2ea043}
</style></head><body><h1>🎙 Live субтитры</h1>
<p>Озвучка голосом браузера (Алиса в Яндекс.Браузере) · <a href="/">→ Ручная (SAPI)</a></p>
<div id="box"><div id="text">Ожидание текста...</div></div>
<div id="status">● Не подключено</div>
<div class="row"><select id="voice"></select>
<button id="btn" onclick="toggle()">▶ Слушать</button></div><script>
let on=false,es=null,last='';
function loadVoices(){const sel=document.getElementById('voice');function fill(){
const vs=speechSynthesis.getVoices();sel.innerHTML='';for(const v of vs){const o=document.createElement('option');
o.value=v.name;o.textContent=v.name+' ('+v.lang+')';if(v.lang.startsWith('ru')&&!sel.dataset.set){o.selected=true;sel.dataset.set='1';}
sel.appendChild(o);}}fill();speechSynthesis.onvoiceschanged=fill;}loadVoices();
function say(t){if(!t||!t.trim())return;const u=new SpeechSynthesisUtterance(t);const sel=document.getElementById('voice');
const v=speechSynthesis.getVoices().find(x=>x.name===sel.value);if(v)u.voice=v;u.rate=1.1;speechSynthesis.speak(u);}
function toggle(){on=!on;const b=document.getElementById('btn');if(on){b.textContent='⏸ Стоп';start();}
else{b.textContent='▶ Слушать';if(es){es.close();es=null;}speechSynthesis.cancel();
document.getElementById('status').textContent='● Остановлено';document.getElementById('status').className='';}}
function start(){if(es)es.close();es=new EventSource('/events');
es.addEventListener('connected',()=>{document.getElementById('status').textContent='● Подключено';document.getElementById('status').className='ok';});
es.addEventListener('subtitle',e=>{const d=JSON.parse(e.data);document.getElementById('text').textContent=d.text;
if(on&&d.text!==last){last=d.text;say(d.text);}});
es.onerror=()=>{document.getElementById('status').textContent='● Ошибка соединения';document.getElementById('status').className='err';};}
</script></body></html>"""


_ocr_instance = None

def _get_ocr():
    global _ocr_instance
    if _ocr_instance is None:
        from ocr_wrapper import OCRWrapper
        from settings import Settings
        _ocr_instance = OCRWrapper(Settings())
    return _ocr_instance


class TTSHandler(BaseHTTPRequestHandler):
    """Единый обработчик всех маршрутов."""

    def log_message(self, fmt, *args):
        msg = f"[HTTP] {fmt % args}"
        logger.debug(msg)
        with _log_lock:
            for cb in list(_log_callbacks):
                try:
                    cb(msg)
                except Exception:
                    pass

    def log_error(self, fmt, *args):
        msg = fmt % args
        # Suppress SSE disconnect noise
        if "10053" in msg or "ConnectionAborted" in msg or "BrokenPipe" in msg:
            return
        logger.warning(f"[HTTP-ERR] {msg}")

    # ==================== API: Status ====================
    def _safe_cb(self, name, default=None):
        """Call a GUI callback safely, catching all exceptions."""
        cb = _get_cb(name)
        if not cb:
            return default
        try:
            return cb()
        except Exception as e:
            logger.warning(f"Callback '{name}' failed: {e}")
            return default

    def _api_status(self):
        """Текущее состояние приложения."""
        live_running = self._safe_cb("is_live_running", False)
        ocr_ready = self._safe_cb("is_ocr_ready", False)

        status = {
            "ok": True,
            "live_running": live_running,
            "ocr_ready": ocr_ready,
            "tts_ready": True,
            "target_window": None,
            "region_count": 0,
        }
        # Проверяем regions.json
        regions_file = Path(__file__).parent / "regions.json"
        if regions_file.exists():
            try:
                regions = json.loads(regions_file.read_text(encoding="utf-8"))
                status["region_count"] = len(regions)
            except Exception:
                pass
        self._json(status)

    # ==================== API: Settings ====================
    def _api_get_settings(self):
        """Вернуть все настройки."""
        from settings import Settings
        s = Settings()
        settings_data = {}
        for key in [
            "ocr.language", "ocr.engine", "ocr.use_gpu",
            "ocr.confidence_threshold", "ocr.contrast", "ocr.brightness",
            "ocr.invert", "ocr.detect_changes", "ocr.auto_detect_region",
            "ocr.scan_interval_ms",
            "tts.voice", "tts.rate", "tts.pitch", "tts.engine", "tts.volume",
            "tts.multi_voice", "tts.cache_audio",
            "tts.male_voice", "tts.female_voice", "tts.narrator_voice",
            "tts.male_ru_voice", "tts.female_ru_voice", "tts.narrator_ru_voice",
            "tts.male_en_voice", "tts.female_en_voice", "tts.narrator_en_voice",
            "tts.male_speed", "tts.female_speed", "tts.male_pitch", "tts.female_pitch",
            "tts.narrator_pitch", "tts.male_rate", "tts.female_rate", "tts.narrator_rate",
            "tts.male_ru_voice_type", "tts.female_ru_voice_type",
            "scan.mode", "scan.interval", "scan.detect_changes",
            "scan.separate_regions", "scan.change_threshold",
            "live.line_by_line", "live.sync", "live.sentence", "live.chunk_clear",
            "overlay.enabled", "overlay.show_text", "overlay.opacity",
            "overlay.panel_opacity", "overlay.font_size",
            "overlay.font_color", "overlay.background_color", "overlay.position",
            "general.log_enabled", "general.log_level", "general.start_minimized",
            "ui.language", "ui.scale",
            "translation.enabled", "translation.dst_lang",
            "web_tts.enabled", "web_tts.port",
        ]:
            settings_data[key] = s.get(key)
        # Also return full sub-objects for convenience
        settings_data["overlay"] = {
            "enabled": s.get("overlay.enabled"),
            "show_text": s.get("overlay.show_text"),
            "opacity": s.get("overlay.opacity"),
            "panel_opacity": s.get("overlay.panel_opacity"),
            "font_size": s.get("overlay.font_size"),
            "font_color": s.get("overlay.font_color"),
            "background_color": s.get("overlay.background_color"),
            "position": s.get("overlay.position"),
        }
        settings_data["tts_roles"] = {
            "male_ru": s.get("tts.male_ru_voice"),
            "male_en": s.get("tts.male_en_voice"),
            "female_ru": s.get("tts.female_ru_voice"),
            "female_en": s.get("tts.female_en_voice"),
            "narrator_ru": s.get("tts.narrator_ru_voice"),
            "narrator_en": s.get("tts.narrator_en_voice"),
            "male_speed": s.get("tts.male_speed"),
            "female_speed": s.get("tts.female_speed"),
            "male_pitch": s.get("tts.male_pitch"),
            "female_pitch": s.get("tts.female_pitch"),
            "narrator_pitch": s.get("tts.narrator_pitch"),
            "male_rate": s.get("tts.male_rate"),
            "female_rate": s.get("tts.female_rate"),
            "narrator_rate": s.get("tts.narrator_rate"),
            "male_ru_voice_type": s.get("tts.male_ru_voice_type"),
            "female_ru_voice_type": s.get("tts.female_ru_voice_type"),
        }
        self._json({"ok": True, "settings": settings_data})

    def _api_set_settings(self):
        """Обновить настройки."""
        from settings import Settings
        s = Settings()
        length = int(self.headers.get("Content-Length", 0))
        try:
            data = json.loads(self.rfile.read(length))
        except Exception:
            self._json({"ok": False, "error": "Invalid JSON"}, 400)
            return
        for key, value in data.items():
            s.set(key, value)
        self._json({"ok": True})

    # ==================== API: Windows ====================
    def _api_windows(self):
        """Список видимых окон."""
        try:
            from ocr_wrapper import OCRWrapper
            windows = OCRWrapper.list_windows()
            result = [{"hwnd": h, "title": t} for h, t in windows]
            self._json({"ok": True, "windows": result})
        except Exception as e:
            self._json({"ok": False, "error": str(e)}, 500)

    def _api_select_window(self):
        """Выбрать окно по HWND."""
        length = int(self.headers.get("Content-Length", 0))
        try:
            data = json.loads(self.rfile.read(length))
            hwnd = data.get("hwnd")
        except Exception:
            self._json({"ok": False, "error": "Invalid JSON"}, 400)
            return
        # Сохраняем в config
        from settings import Settings
        s = Settings()
        s.set("ocr.target_hwnd", hwnd)
        # Уведомляем GUI
        cb = _get_cb("select_window")
        if cb:
            try:
                cb(hwnd)
            except Exception:
                pass
        self._json({"ok": True, "hwnd": hwnd})

    def _api_clear_window(self):
        """Сбросить выбор окна."""
        from settings import Settings
        s = Settings()
        s.set("ocr.target_hwnd", None)
        cb = _get_cb("clear_window")
        if cb:
            try:
                cb()
            except Exception:
                pass
        self._json({"ok": True})

    # ==================== API: Live Mode ====================
    def _api_live_start(self):
        """Запуск Live Mode."""
        cb = _get_cb("start_live")
        if cb:
            try:
                cb()
                self._json({"ok": True, "message": "Live mode started"})
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, 500)
        else:
            self._json({"ok": False, "error": "GUI not connected"}, 503)

    def _api_live_stop(self):
        """Остановка Live Mode + принудительная остановка всего аудио."""
        cb = _get_cb("stop_live")
        if cb:
            try:
                cb()
                self._json({"ok": True, "message": "Live mode stopped"})
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, 500)
        else:
            self._json({"ok": False, "error": "GUI not connected"}, 503)

    def _api_tts_stop(self):
        """Принудительная остановка всего TTS аудио."""
        cb = _get_cb("force_stop_tts")
        if cb:
            try:
                cb()
                self._json({"ok": True, "message": "TTS stopped"})
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, 500)
        else:
            self._json({"ok": False, "error": "GUI not connected"}, 503)

    def _api_live_status(self):
        """Статус Live Mode."""
        running = self._safe_cb("is_live_running", False)
        self._json({"ok": True, "running": running})

    # ==================== API: OCR ====================
    def _api_ocr_scan(self):
        """Ручное сканирование."""
        length = int(self.headers.get("Content-Length", 0))
        try:
            data = json.loads(self.rfile.read(length)) if length > 0 else {}
        except Exception:
            data = {}
        region_index = data.get("region_index", 0)
        self._scan_region(region_index)

    # ==================== API: Regions ====================
    def _api_regions_list(self):
        """Список регионов."""
        self._regions()

    def _api_save_region(self):
        """Добавить/обновить регион."""
        length = int(self.headers.get("Content-Length", 0))
        try:
            data = json.loads(self.rfile.read(length))
        except Exception:
            self._json({"ok": False, "error": "Invalid JSON"}, 400)
            return
        regions_file = Path(__file__).parent / "regions.json"
        regions = []
        if regions_file.exists():
            try:
                regions = json.loads(regions_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        idx = data.get("index")
        if idx is not None and 0 <= idx < len(regions):
            regions[idx].update(data)
        else:
            data.setdefault("name", f"R{len(regions)+1}")
            data.setdefault("color", "#A8C7FA")
            regions.append(data)
        regions_file.write_text(json.dumps(regions, ensure_ascii=False, indent=2), encoding="utf-8")
        self._json({"ok": True, "count": len(regions)})

    def _api_delete_region(self):
        """Удалить регион."""
        length = int(self.headers.get("Content-Length", 0))
        try:
            data = json.loads(self.rfile.read(length))
            idx = data.get("index", -1)
        except Exception:
            self._json({"ok": False, "error": "Invalid JSON"}, 400)
            return
        regions_file = Path(__file__).parent / "regions.json"
        if not regions_file.exists():
            self._json({"ok": False, "error": "No regions"}, 404)
            return
        try:
            regions = json.loads(regions_file.read_text(encoding="utf-8"))
            if 0 <= idx < len(regions):
                regions.pop(idx)
                regions_file.write_text(json.dumps(regions, ensure_ascii=False, indent=2), encoding="utf-8")
                self._json({"ok": True, "count": len(regions)})
            else:
                self._json({"ok": False, "error": "Index out of range"}, 400)
        except Exception as e:
            self._json({"ok": False, "error": str(e)}, 500)

    def _scan_region(self, index: int):
        """Запускает OCR на области и возвращает текст."""
        regions_file = Path(__file__).parent / "regions.json"
        if not regions_file.exists():
            self._json({"ok": False, "error": "No regions"}, 404)
            return
        try:
            regions = json.loads(regions_file.read_text(encoding="utf-8"))
            if index < 0 or index >= len(regions):
                self._json({"ok": False, "error": "Index out of range"}, 400)
                return
            r = regions[index]
            try:
                import mss
                from PIL import Image
                with mss.mss() as sct:
                    monitor = {"left": r["x"], "top": r["y"],
                               "width": r["width"], "height": r["height"]}
                    shot = sct.grab(monitor)
                    img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
                    # Use GUI's OCR if available, otherwise create standalone
                    ocr = None
                    gui_ocr_cb = _get_cb("get_ocr")
                    if gui_ocr_cb:
                        try:
                            ocr = gui_ocr_cb()
                        except Exception:
                            pass
                    if ocr is None:
                        ocr = _get_ocr()
                    result = ocr.recognize(img)
                    text = result[0] if isinstance(result, tuple) else result
                    text = text.strip() if isinstance(text, str) else str(text).strip()
                    # Push result to SSE for web UI
                    if text:
                        push_ocr_result(text, r.get("name", ""))
                    self._json({"ok": True, "text": text, "region": r["name"]})
            except ImportError as e:
                self._json({"ok": False, "error": f"Missing dependency: {e}"}, 500)
            except Exception as e:
                logger.error(f"Scan region error: {e}", exc_info=True)
                self._json({"ok": False, "error": str(e)}, 500)
        except Exception as e:
            self._json({"ok": False, "error": str(e)}, 500)

    def _ocr_image(self):
        """Принимает PNG-изображение от телефона и возвращает текст OCR."""
        try:
            content_type = self.headers.get("Content-Type", "")
            length = int(self.headers.get("Content-Length", 0))
            if length == 0:
                self._json({"ok": False, "error": "Empty body"}, 400)
                return

            raw = self.rfile.read(length)

            if "multipart/form-data" in content_type:
                boundary = content_type.split("boundary=")[-1].strip().encode()
                parts = raw.split(b"--" + boundary)
                img_bytes = None
                for part in parts:
                    if b"Content-Disposition" in part and b'name="image"' in part:
                        header_end = part.find(b"\r\n\r\n")
                        if header_end != -1:
                            img_bytes = part[header_end + 4:]
                            if img_bytes.endswith(b"\r\n"):
                                img_bytes = img_bytes[:-2]
                            break
                if img_bytes is None:
                    self._json({"ok": False, "error": "No image field"}, 400)
                    return
            else:
                img_bytes = raw

            from PIL import Image
            import io as _io
            img = Image.open(_io.BytesIO(img_bytes))
            if img.mode != "RGB":
                img = img.convert("RGB")

            ocr = _get_ocr()
            text = ocr.recognize(img)
            self._json({"ok": True, "text": text.strip()})
        except Exception as e:
            logger.error(f"OCR image error: {e}")
            self._json({"ok": False, "error": str(e)}, 500)

    def do_GET(self):
        try:
            self._do_GET_inner()
        except Exception as e:
            logger.error(f"GET error: {e}", exc_info=True)
            try:
                self._json({"ok": False, "error": str(e)}, 500)
            except Exception:
                pass

    def _do_GET_inner(self):
        from urllib.parse import unquote
        path = unquote(urlparse(self.path).path)
        if path == "/":
            self._html(MANUAL_PAGE)
        elif path == "/live":
            self._html(LIVE_PAGE)
        elif path == "/control":
            self._serve_control_panel()
        elif path == "/manifest.json":
            self._serve_static("manifest.json", "application/json")
        elif path == "/sw.js":
            self._serve_static("sw.js", "application/javascript")
        elif path.startswith("/icon-") and path.endswith(".png"):
            self._serve_static(path[1:], "image/png")
        elif path == "/qr":
            self._qr_page()
        elif path == "/events":
            self._sse()
        elif path == "/voices":
            self._json(get_all_voices())
        elif path == "/health":
            self._json({"ok": True, "engine": "SAPI", "ocr_image": True})
        elif path == "/regions":
            self._regions()
        elif path.startswith("/region_screenshot/"):
            self._region_screenshot(path[19:])
        elif path.startswith("/audio/"):
            self._audio(os.path.basename(path[7:]))
        elif path == "/api/status":
            self._api_status()
        elif path == "/api/settings":
            self._api_get_settings()
        elif path == "/api/windows":
            self._api_windows()
        elif path == "/api/live/status":
            self._api_live_status()
        elif path == "/api/regions":
            self._api_regions_list()
        else:
            self._json({"error": "Not found"}, 404)

    def do_POST(self):
        try:
            self._do_POST_inner()
        except Exception as e:
            logger.error(f"POST error: {e}", exc_info=True)
            try:
                self._json({"ok": False, "error": str(e)}, 500)
            except Exception:
                pass

    def _do_POST_inner(self):
        path = urlparse(self.path).path
        if path == "/speak":
            length = int(self.headers.get("Content-Length", 0))
            try:
                data = json.loads(self.rfile.read(length))
                text = data.get("text", "").strip()
                voice = data.get("voice", "") or None
                engine = data.get("engine", "auto") or "auto"
            except Exception:
                self._json({"ok": False, "error": "Invalid JSON"}, 400)
                return
            if not text:
                self._json({"ok": False, "error": "Empty text"}, 400)
                return
            out = speak_engine(text, engine, voice)
            if out:
                self._json({"ok": True, "file": os.path.basename(out)})
            else:
                self._json({"ok": False, "error": "All TTS engines failed"}, 500)
        elif path == "/scan":
            length = int(self.headers.get("Content-Length", 0))
            try:
                data = json.loads(self.rfile.read(length))
                region_index = data.get("region_index", 0)
            except Exception:
                self._json({"ok": False, "error": "Invalid JSON"}, 400)
                return
            self._scan_region(region_index)
        elif path == "/ocr_image":
            self._ocr_image()
        elif path == "/api/settings":
            self._api_set_settings()
        elif path == "/api/window/select":
            self._api_select_window()
        elif path == "/api/window/clear":
            self._api_clear_window()
        elif path == "/api/live/start":
            self._api_live_start()
        elif path == "/api/live/stop":
            self._api_live_stop()
        elif path == "/api/tts/stop":
            self._api_tts_stop()
        elif path == "/api/ocr/scan":
            self._api_ocr_scan()
        elif path == "/api/region/save":
            self._api_save_region()
        elif path == "/api/region/delete":
            self._api_delete_region()
        else:
            self._json({"error": "Not found"}, 404)

    def _serve_control_panel(self):
        """Отдать index.html из папки web/."""
        index_path = Path(__file__).parent / "web" / "index.html"
        if index_path.exists():
            html = index_path.read_text(encoding="utf-8")
            self._html(html)
        else:
            self._html("<h1>web/index.html not found</h1>")

    def _serve_static(self, filename: str, content_type: str):
        """Отдать статический файл из папки web/."""
        fpath = Path(__file__).parent / "web" / filename
        if fpath.exists():
            data = fpath.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        else:
            self._json({"error": "Not found"}, 404)

    def _sse(self):
        """Поток субтитров + OCR результатов (Server-Sent Events)."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        q = queue.Queue()
        ocr_q = queue.Queue()
        with _subtitle_lock:
            _subtitle_clients.append(q)
        with _ocr_result_lock:
            _ocr_result_clients.append(ocr_q)
        try:
            self.wfile.write(b"event: connected\ndata: ok\n\n")
            self.wfile.flush()
            while True:
                try:
                    text = q.get(timeout=0.1)
                    payload = json.dumps({"text": text}, ensure_ascii=False)
                    self.wfile.write(f"event: subtitle\ndata: {payload}\n\n".encode("utf-8"))
                    self.wfile.flush()
                except queue.Empty:
                    pass
                try:
                    ocr_payload = ocr_q.get(timeout=0.1)
                    self.wfile.write(f"event: ocr_result\ndata: {ocr_payload}\n\n".encode("utf-8"))
                    self.wfile.flush()
                except queue.Empty:
                    pass
                try:
                    q.get(timeout=18)
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass
        except Exception:
            pass
        finally:
            with _subtitle_lock:
                try:
                    _subtitle_clients.remove(q)
                except ValueError:
                    pass
            with _ocr_result_lock:
                try:
                    _ocr_result_clients.remove(ocr_q)
                except ValueError:
                    pass

    def _audio(self, fname):
        fpath = CACHE_DIR / fname
        if fpath.exists():
            ext = fpath.suffix.lower()
            ct = {"wav": "audio/wav", "mp3": "audio/mpeg", "ogg": "audio/ogg"}.get(ext, "audio/wav")
            self.send_response(200)
            self.send_header("Content-Type", ct)
            self.send_header("Content-Length", str(fpath.stat().st_size))
            self.end_headers()
            self.wfile.write(fpath.read_bytes())
        else:
            self._json({"error": "File not found"}, 404)

    def _html(self, html: str):
        data = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj: dict, code: int = 200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _regions(self):
        """Возвращает список областей сканирования с информацией о экране."""
        regions_file = Path(__file__).parent / "regions.json"
        regions = []
        if regions_file.exists():
            try:
                regions = json.loads(regions_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        # Добавляем индексы
        for i, r in enumerate(regions):
            r["index"] = i
        # Информация о экране ПК
        try:
            import ctypes
            user32 = ctypes.windll.user32
            screen_w = user32.GetSystemMetrics(0)
            screen_h = user32.GetSystemMetrics(1)
        except Exception:
            screen_w, screen_h = 1920, 1080
        self._json({
            "screen_width": screen_w,
            "screen_height": screen_h,
            "regions": regions
        })

    def _region_screenshot(self, idx_str: str):
        """Скриншот области сканирования (PNG)."""
        try:
            idx = int(idx_str)
        except ValueError:
            self._json({"error": "Invalid index"}, 400)
            return
        regions_file = Path(__file__).parent / "regions.json"
        if not regions_file.exists():
            self._json({"error": "No regions"}, 404)
            return
        try:
            regions = json.loads(regions_file.read_text(encoding="utf-8"))
            if idx < 0 or idx >= len(regions):
                self._json({"error": "Index out of range"}, 404)
                return
            r = regions[idx]
            # Capture screen region using mss
            try:
                import mss
                with mss.mss() as sct:
                    monitor = {"left": r["x"], "top": r["y"],
                               "width": r["width"], "height": r["height"]}
                    img = sct.grab(monitor)
                    png_bytes = mss.tools.to_png(img.rgb, img.size)
                    self.send_response(200)
                    self.send_header("Content-Type", "image/png")
                    self.send_header("Content-Length", str(len(png_bytes)))
                    self.end_headers()
                    self.wfile.write(png_bytes)
            except ImportError:
                self._json({"error": "mss not installed"}, 500)
            except Exception as e:
                self._json({"error": str(e)}, 500)
        except Exception as e:
            self._json({"error": str(e)}, 500)

    def _qr_page(self):
        """Страница с QR-кодом для подключения телефона."""
        try:
            import socket
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
        except Exception:
            local_ip = "127.0.0.1"
        url = f"http://{local_ip}:{self.server.server_port}"
        html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>QR - Screen TTS</title>
<style>
body{{background:#0D1117;color:#C9D1D9;font-family:system-ui;display:flex;flex-direction:column;align-items:center;
justify-content:center;min-height:100vh;margin:0;padding:20px;box-sizing:border-box}}
h1{{color:#7B68EE;margin-bottom:8px;font-size:20px}}
.url{{color:#89B4FA;font-size:16px;margin:12px 0;padding:8px 16px;background:#161B22;border-radius:8px;
word-break:break-all;font-family:monospace}}
#qr{{background:white;padding:16px;border-radius:12px;margin:16px 0}}
.hint{{color:#8B949E;font-size:13px;margin-top:12px;text-align:center;max-width:400px}}
</style></head><body>
<h1>📱 Подключение телефона</h1>
<div class="url">{url}</div>
<div id="qr"></div>
<p class="hint">Отсканируйте QR-код камерой телефона<br>или введите адрес вручную в приложении</p>
<script src="https://cdn.jsdelivr.net/npm/qrcodejs@1.0.0/qrcode.min.js"></script>
<script>
new QRCode(document.getElementById("qr"), {{
    text: "{url}",
    width: 256,
    height: 256,
    colorDark: "#000000",
    colorLight: "#ffffff"
}});
</script></body></html>"""
        self._html(html)


class WebTTSServer:
    """Веб-сервер TTS в отдельном потоке (ThreadingHTTPServer для SSE)."""

    def __init__(self, port: int = 8080):
        self._port = port
        self._server = None
        self._thread = None

    @property
    def port(self) -> int:
        return self._port

    @port.setter
    def port(self, value: int):
        self._port = value

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        import socket as _sock
        try:
            s = _sock.socket()
            s.bind(("0.0.0.0", self.port))
            s.close()
        except OSError:
            for alt in range(self.port + 1, self.port + 20):
                try:
                    s = _sock.socket()
                    s.bind(("0.0.0.0", alt))
                    s.close()
                    self._port = alt
                    logger.info(f"[WEB-TTS] Порт {self.port} занят, используем {alt}")
                    break
                except OSError:
                    continue
            else:
                raise RuntimeError(f"Порт {self.port} занят, свободных портов в диапазоне +20 не найдено")
        self._server = ThreadingHTTPServer(("0.0.0.0", self.port), TTSHandler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        logger.info(f"[WEB-TTS] Сервер запущен: http://0.0.0.0:{self.port} (/live для субтитров)")
        logger.info(f"[WEB-TTS] Доступен по всем интерфейсам. Для подключения с телефона используйте IP вашего ПК.")

    def stop(self):
        if self._server:
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception:
                pass
            self._server = None
        self._thread = None
        logger.info("[WEB-TTS] Сервер остановлен")

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()


# Глобальный экземпляр для импорта из main.py / live_scanner.py
web_tts = WebTTSServer()
