"""
Модуль переводчика.

Поддерживаемые сервисы:
  1. Zen (opencode.ai) — бесплатные модели, БЕЗ ключа
  2. Google Translate (unofficial) — бесплатный fallback
  3. DeepL Free (unofficial) — бесплатный fallback

Приоритет: Zen -> Google Translate -> DeepL -> Offline

УДАЛЁННЫЕ провайдеры (проверено 26.09.2026, не работали):
  * OrcaRouter — api.orcarouter.ai/v1/chat/completions отдаёт 404,
    эндпоинт больше не существует;
  * OpenRouter — ключ из настроек отклоняется с 403 Forbidden.

По Zen важно: почти все её free-модели отдают
  403 FreeTierError: "OpenCode's free tier can only be used from within
  OpenCode" — снаружи OpenCode они недоступны. Работает space-bunny-free,
  именно он стоит первым в ZEN_MODELS.
"""
import logging
import json
import re
import threading
import time
import requests

logger = logging.getLogger(__name__)

# ─── Языки ──────────────────────────────────────────────────────────────
LANGUAGES = {
    "auto": "Auto-detect",
    "en": "English",
    "ru": "Russian",
    "ja": "Japanese",
    "ko": "Korean",
    "zh": "Chinese",
    "de": "German",
    "fr": "French",
    "es": "Spanish",
    "it": "Italian",
    "pt": "Portuguese",
    "ar": "Arabic",
    "hi": "Hindi",
    "tr": "Turkish",
    "pl": "Polish",
    "nl": "Dutch",
    "sv": "Swedish",
    "fi": "Finnish",
    "uk": "Ukrainian",
    "cs": "Czech",
    "el": "Greek",
    "he": "Hebrew",
    "th": "Thai",
    "vi": "Vietnamese",
    "id": "Indonesian",
    "ms": "Malay",
}

# ── Устойчивость к троттлингу Google Translate ────────────────────────────
# Бесплатный endpoint translate_a/single отдаёт 429 «Sorry...» при частых
# запросах с одного IP. В игре реплики идут каждые несколько секунд, поэтому:
#   * одна Session с keep-alive (меньше рукопожатий);
#   * минимальный интервал между запросами;
#   * повтор с нарастающей паузой;
#   * кэш переводов (игровые реплики повторяются).
_GOOGLE_URL = "https://translate.googleapis.com/translate_a/single"
_GOOGLE_MIN_INTERVAL = 1.1     # секунд между запросами к Google
_GOOGLE_RETRY_DELAYS = (1.5, 4.0)
_google_last_call = 0.0
_google_lock = threading.Lock()
_session = None


def _get_session():
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/120.0 Safari/537.36"),
            "Accept": "*/*",
        })
    return _session


def _google_get(params, timeout):
    """GET к Google с троттлингом и повторами при 429. Возвращает Response
    или None, если все попытки исчерпаны."""
    global _google_last_call
    delay = 0
    for attempt in range(len(_GOOGLE_RETRY_DELAYS) + 1):
        if attempt:
            delay = _GOOGLE_RETRY_DELAYS[attempt - 1]
            time.sleep(delay)
        with _google_lock:
            wait = _GOOGLE_MIN_INTERVAL - (time.monotonic() - _google_last_call)
            if wait > 0:
                time.sleep(wait)
            _google_last_call = time.monotonic()
        try:
            resp = _get_session().get(_GOOGLE_URL, params=params, timeout=timeout)
        except Exception as e:
            logger.debug(f"Google request failed: {e}")
            continue
        if resp.status_code == 200:
            return resp
        if resp.status_code in (429, 503):
            logger.debug(f"Google throttled ({resp.status_code}), "
                         f"повтор через {delay or _GOOGLE_MIN_INTERVAL:.1f}c")
            continue
        logger.debug(f"Google HTTP {resp.status_code}")
        return None
    return None


# Названия языков для промпта LLM
_LANG_NAMES = {
    "auto": "the appropriate language",    "en": "English", "ru": "Russian", "ja": "Japanese",
    "ko": "Korean", "zh": "Chinese", "de": "German",
    "fr": "French", "es": "Spanish", "it": "Italian",
    "pt": "Portuguese", "ar": "Arabic", "hi": "Hindi",
    "tr": "Turkish", "pl": "Polish", "nl": "Dutch",
    "sv": "Swedish", "fi": "Finnish", "uk": "Ukrainian",
    "cs": "Czech", "el": "Greek", "he": "Hebrew",
    "th": "Thai", "vi": "Vietnamese", "id": "Indonesian",
    "ms": "Malay",
}

_TIMEOUT = 30

# Модели Zen (opencode.ai), доступные БЕЗ ключа из внешнего приложения.
# Проверено 26.09.2026 прямыми запросами к /zen/v1/chat/completions:
#   space-bunny-free                        -> HTTP 200, точный русский
#   mimo-v2.6-flash-free                    -> 403 FreeTierError
#   mimo-v2.5-free                          -> 403 FreeTierError
#   nemotron-3.5-lightning-free             -> 403 FreeTierError
#   deepseek-v4-flash-free                  -> 400 Model is unavailable
#   jev-1.13-free / muse-spark-*-free / ... -> 401/403/500
# 403 FreeTierError = «can only be used from within OpenCode», то есть
# из нашего скрипта эти модели недоступны.
ZEN_MODELS = ["space-bunny-free"]

# Перевод, который выглядит как НЕПЕРЕВЕДЁННЫЙ источник. Раньше такой мусор
# молча уходил в TTS: «Adventure time!» -> «Adventure время»,
# «Off we go!» -> «выкл we идти». Ловим по строчным латинским словам
# внутри русского текста (имена вроде Genshin/Paimon начинаются с заглавной
# и допускаются).
_LATIN_WORD = re.compile(r"(?<![A-Za-z])([a-z]{2,})(?![A-Za-z])")


def looks_untranslated(text: str, dst: str = "ru") -> bool:
    """True, если перевод suspicious: русский текст с нетронутыми словами."""
    if not text or dst not in ("ru", "uk", "be", "bg"):
        return False
    return bool(_LATIN_WORD.search(text))


class Translator:
    """Переводчик с приоритетом Zen -> Google Translate -> DeepL -> Offline.

    Провайдеры OrcaRouter и OpenRouter удалены 26.09.2026: их endpoints
    не отвечают (404 / 403). Параметры orcarouter_*/openrouter_* в configure()
    оставлены для совместимости старых вызовов и игнорируются.
    """

    def __init__(self, source="auto", target="en", **kwargs):
        self._api_key: str = ""
        self._model: str = ""
        self._source = source
        self._target = target
        self._zen_model: str = ""
        # Офлайн-словарь — только для OCR. Для озвучки его выключают:
        # пословный перевод звучит хуже, чем молчание.
        self.allow_offline_dict: bool = True
        # Кэш переводов: в игре реплики часто повторяются, а каждый запрос
        # к внешнему сервису — это риск попасть под троттлинг.
        self._cache: dict = {}
        self._cache_max = 500

    def configure(self, api_key: str = "", model: str = "",
                  orcarouter_key: str = "", orcarouter_model: str = "",
                  openrouter_key: str = "", openrouter_model: str = "",
                  zen_model: str = ""):
        """Устанавливает ключи и модели сервисов.

        orcarouter_* / openrouter_* оставлены как no-op: провайдеры удалены,
        но старые вызовы из GUI не должны падать с TypeError.
        """
        self._api_key = (api_key or "").strip()
        self._model = (model or "").strip()
        # У Zen своя модель: раньше сюда попадала общая translation.model
        # (например qwen/...), и Zen отдавал пословную кашу.
        self._zen_model = (zen_model or "").strip()

    def _zen_candidates(self, fallback: str = "") -> list:
        """Список моделей Zen для перебора: настроенная, затем рабочие.

        Модель из настроек идёт первой, но если она из тех, что отдают
        403 FreeTierError, перебор падает на проверенную space-bunny-free.
        Идентификаторы Zen никогда не содержат «/», поэтому модели в формате
        openrouter/orcarouter (например «qwen/qwen3.8-27b-free») отбрасываются
        без сетевого запроса — они всё равно вернули бы 401/404.
        """
        out = []
        for m in (self._zen_model, (fallback or "").strip(), *ZEN_MODELS):
            m = (m or "").strip()
            if m and "/" in m:
                continue
            if m and m not in out:
                out.append(m)
        return out or list(ZEN_MODELS)

    def translate(self, text: str, src: str = None, dst: str = None,
                  api_key: str = "", model: str = "",
                  engine: str = "auto") -> str:
        """
        Переводит текст. Пробует сервисы по порядку.
        engine: "auto", "zen", "google", "deepl"

        Провайдеры OrcaRouter и OpenRouter удалены: их endpoints не отвечают
        (404 / 403). Старые вызовы engine="orcarouter"/"openrouter" вернут
        понятное сообщение вместо обращения к мёртвым адресам.
        """
        if not text or not text.strip():
            return ""

        src = src or self._source or "auto"
        dst = dst or self._target or "en"

        key = api_key or self._api_key
        mdl = model or self._model

        # Кэш: точный повтор перевода отдаём сразу, без обращения к сети
        ckey = (src, dst, text.strip())
        if ckey in self._cache:
            return self._cache[ckey]

        if engine in ("orcarouter", "openrouter"):
            logger.warning(f"[Translator] Провайдер {engine} удалён "
                           f"(endpoint не отвечает). Используйте Zen.")
            return f"[{engine} removed: endpoint unavailable]"

        if engine == "zen":
            # Раньше здесь бралась одна mdl = model or self._model, а GUI
            # передаёт модель только в zen_model. Тогда mdl была пустой,
            # _translate_zen подставлял свой старый default
            # (mimo-v2.5-free) и получал 403 FreeTierError.
            # Теперь идём по кандидатам: явный выбор -> zen_model -> default.
            last = ""
            for zen_model in self._zen_candidates(mdl):
                try:
                    result = self._translate_zen(text, src, dst, zen_model)
                    got = self._accept(result, ckey, f"Zen/{zen_model}")
                    if got:
                        return got
                    last = result
                except Exception as e:
                    logger.debug(f"Zen/{zen_model} failed: {e}")
                    last = f"Zen/{zen_model}: {e}"
            return f"[Zen failed: {last}]"

        # Auto mode — пробуем по порядку.
        # OrcaRouter (404) и OpenRouter (403) удалены как мёртвые.
        #
        # 1. Zen (opencode.ai) — бесплатные модели БЕЗ ключа.
        #    Проверено 26.09.2026: space-bunny-free отдаёт точный русский
        #    за 1.6-2.2 c («Adventure time!» -> «Время приключений!»).
        #    Остальные free-модели Zen отдают 403 FreeTierError
        #    («can only be used from within OpenCode»), поэтому запасная
        #    модель в ZEN_MODELS.
        for zen_model in self._zen_candidates(mdl):
            try:
                result = self._translate_zen(text, src, dst, zen_model)
                got = self._accept(result, ckey, f"Zen/{zen_model}")
                if got:
                    return got
            except Exception as e:
                logger.debug(f"Zen/{zen_model} failed: {e}")

        # 2. Google Translate — специализированный MT, даёт нормальный русский
        #    без ключа, но часто отдаёт 429 (троттлинг IP), поэтому идёт вторым.
        try:
            result = self._translate_google(text, src, dst)
            got = self._accept(result, ckey, "Google")
            if got:
                return got
        except Exception as e:
            logger.debug(f"Google Translate failed: {e}")

        # 3. DeepL free API (no key needed for small texts)
        try:
            result = self._translate_deepl_free(text, src, dst)
            got = self._accept(result, ckey, "DeepL")
            if got:
                return got
        except Exception as e:
            logger.debug(f"DeepL free failed: {e}")

        # 4. Offline dictionary fallback (EN→RU only).
        #    ВНИМАНИЕ: словарь переводит ПОСЛОВНО и даёт мусор
        #    («Are you bringing the map?» -> «являются Ю ПРИВЕДЕНИЕ карта»).
        #    Для озвучки его использовать нельзя, поэтому голосовой путь
        #    выключает его флагом allow_offline_dict и получает честный отказ.
        if self.allow_offline_dict and dst == "ru" and (src in ("en", "auto")):
            try:
                from en_ru_dict import translate_text
                result = translate_text(text)
                if result:
                    logger.info("[Translator] Used offline dictionary fallback")
                    return self._cache_put(ckey, result)
            except Exception as e:
                logger.debug(f"Offline dict failed: {e}")

        return "[Translation failed: all services unavailable]"

    def _cache_put(self, key, value: str) -> str:
        """Положить перевод в кэш (с ограничением размера)."""
        if len(self._cache) >= self._cache_max:
            # выбросим половину (FIFO-подобное усечение)
            for k in list(self._cache)[:self._cache_max // 2]:
                self._cache.pop(k, None)
        self._cache[key] = value
        return value

    def _accept(self, result: str, ckey, engine_name: str) -> str:
        """Пропустить результат через проверку «похоже на перевод»."""
        if not result:
            return ""
        if result.startswith("[") and result.endswith("]"):
            return ""            # это маркер отказа, а не перевод
        if looks_untranslated(result, ckey[1]):
            logger.warning(f"[Translator] {engine_name} вернул непереведённый "
                           f"текст, отбрасываем: {result[:60]!r}")
            return ""
        return self._cache_put(ckey, result)

    # ── Zen (opencode.ai, free) ────────────────────────────────────────

    def _translate_zen(self, text: str, src: str, dst: str, model: str) -> str:
        """Перевод через Zen API (бесплатный, без ключа)."""
        dst_name = _LANG_NAMES.get(dst, dst)

        if src == "auto":
            prompt = (
                f"Translate the following text to {dst_name}. "
                f"Output ONLY the translated text, nothing else.\n\n{text}"
            )
        else:
            src_name = _LANG_NAMES.get(src, src)
            prompt = (
                f"Translate the following text from {src_name} to {dst_name}. "
                f"Output ONLY the translated text, nothing else.\n\n{text}"
            )

        resp = requests.post(
            "https://opencode.ai/zen/v1/chat/completions",
            headers={
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/sj0404-collab/overlay-translator",
                "X-Title": "ScreenOCR_TTS",
            },
            json={
                "model": model or "mimo-v2.5-free",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
                "max_tokens": 4096,
            },
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()

        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        if content and content.strip():
            return content.strip()
        return ""

    # ── Google Translate (fallback) ────────────────────────────────────

    def _translate_google(self, text: str, src: str, dst: str) -> str:
        """Google Translate — бесплатный unofficial API (через gtx-клиент)."""
        params = {
            "client": "gtx",
            "sl": src,
            "tl": dst,
            "dt": "t",
            "q": text,
        }
        resp = _google_get(params, _TIMEOUT)
        if resp is None:
            return ""
        try:
            data = resp.json()
        except Exception as e:
            logger.debug(f"Google bad payload: {e}")
            return ""

        if data and data[0]:
            result = "".join(part[0] for part in data[0] if part[0])
            detected = data[2] if len(data) > 2 else None

            if result and result.strip() != text.strip():
                return result.strip()

            if detected == dst:
                return text.strip()

        return ""

    # ── DeepL Free (unofficial) ──────────────────────────────────────────

    def _translate_deepl_free(self, text: str, src: str, dst: str) -> str:
        """DeepL free via unofficial endpoint (no API key needed)."""
        # DeepL language codes
        dl_map = {
            "en": "EN", "ru": "RU", "de": "DE", "fr": "FR", "es": "ES",
            "it": "IT", "pt": "PT", "ja": "JA", "zh": "ZH", "ko": "KO",
            "nl": "NL", "pl": "PL", "uk": "UK", "tr": "TR",
        }
        sl = dl_map.get(src, "EN") if src != "auto" else "EN"
        tl = dl_map.get(dst, "RU")

        resp = requests.post(
            "https://www2.deepl.com/jsonrpc",
            json={
                "jsonrpc": "2.0",
                "method": "LMT_handle_texts",
                "params": {
                    "texts": [{"text": text, "requestAlternatives": 0}],
                    "splitting": "newlines",
                    "lang": {"source_lang_user_selected": sl, "target_lang": tl},
                    "commonJobParams": {"wasSpoken": False, "transcribe_as": ""},
                },
            },
            headers={
                "User-Agent": "Mozilla/5.0",
                "Content-Type": "application/json",
            },
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()

        if "result" in data:
            texts = data["result"].get("texts", [])
            if texts:
                translated = texts[0].get("text", "")
                if translated and translated.strip() != text.strip():
                    return translated.strip()
        return ""

    # ── Проверка соединения ────────────────────────────────────────────

    def test_connection(self, api_key: str = "", model: str = "",
                        engine: str = "zen") -> dict:
        """
        Тестирует подключение к указанному сервису.
        engine: "zen" (по умолчанию), "google", "deepl"
        Возвращает dict с полями:
            ok: bool — успешно ли
            message: str — описание результата
            tokens_prompt: int — токены промпта
            tokens_completion: int — токены ответа
            tokens_total: int — всего токенов
            latency_ms: int — задержка в мс
        """
        import time
        result = {
            "ok": False, "message": "", "tokens_prompt": 0,
            "tokens_completion": 0, "tokens_total": 0, "latency_ms": 0,
        }

        if engine in ("orcarouter", "openrouter"):
            result["message"] = (
                f"Провайдер {engine} удалён: endpoint не отвечает "
                f"(404/403). Используйте Zen (бесплатно, без ключа)."
            )
            return result

        # Zen — единственный LLM-провайдер: free, без ключа.
        key = ""
        mdl = model or (self._zen_model or ZEN_MODELS[0])
        endpoint = "https://opencode.ai/zen/v1/chat/completions"

        t0 = time.monotonic()
        try:
            headers = {
                "Content-Type": "application/json",
            }
            if key:
                headers["Authorization"] = f"Bearer {key.strip()}"
            if engine == "zen":
                headers["HTTP-Referer"] = "https://github.com/sj0404-collab/overlay-translator"
                headers["X-Title"] = "ScreenOCR_TTS"

            resp = requests.post(
                endpoint,
                headers=headers,
                json={
                    "model": mdl,
                    "messages": [{"role": "user", "content": "Say OK"}],
                    "max_tokens": 10,
                },
                timeout=15,
            )
            latency = int((time.monotonic() - t0) * 1000)
            result["latency_ms"] = latency

            if resp.status_code == 200:
                data = resp.json()
                usage = data.get("usage", {})
                result["ok"] = True
                result["message"] = f"Connected OK ({mdl})"
                result["tokens_prompt"] = usage.get("prompt_tokens", 0)
                result["tokens_completion"] = usage.get("completion_tokens", 0)
                result["tokens_total"] = usage.get("total_tokens", 0)
            else:
                err = resp.json().get("error", {})
                msg = err.get("message", resp.text[:200])
                result["message"] = f"HTTP {resp.status_code}: {msg}"
        except requests.exceptions.Timeout:
            result["message"] = "Timeout (15s)"
        except requests.exceptions.ConnectionError as e:
            result["message"] = f"Connection error: {e}"
        except Exception as e:
            result["message"] = f"Error: {e}"

        return result


# Глобальный экземпляр
translator = Translator()
