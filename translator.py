"""
Модуль переводчика.

Поддерживаемые сервисы:
  1. OrcaRouter (LLM) — требует API-ключ, бесплатные модели
  2. OpenRouter (LLM) — требует API-ключ
  3. Zen (opencode.ai) — бесплатный, без ключа
  4. Google Translate (unofficial) — бесплатный fallback
  5. DeepL Free (unofficial) — бесплатный fallback

Приоритет: OrcaRouter -> OpenRouter -> Zen -> Google Translate -> DeepL -> Offline
"""
import logging
import json
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

# Названия языков для промпта LLM
_LANG_NAMES = {
    "auto": "the appropriate language",
    "en": "English", "ru": "Russian", "ja": "Japanese",
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


class Translator:
    """Переводчик с приоритетом OrcaRouter -> OpenRouter -> Zen -> Google Translate."""

    def __init__(self, source="auto", target="en", **kwargs):
        self._api_key: str = ""
        self._model: str = ""
        self._source = source
        self._target = target
        self._orcarouter_key: str = ""
        self._orcarouter_model: str = ""
        self._openrouter_key: str = ""
        self._openrouter_model: str = ""

    def configure(self, api_key: str = "", model: str = "",
                  orcarouter_key: str = "", orcarouter_model: str = "",
                  openrouter_key: str = "", openrouter_model: str = ""):
        """Устанавливает ключи и модели для всех сервисов."""
        self._api_key = (api_key or "").strip()
        self._model = (model or "").strip()
        self._orcarouter_key = (orcarouter_key or "").strip()
        self._orcarouter_model = (orcarouter_model or "").strip()
        self._openrouter_key = (openrouter_key or "").strip()
        self._openrouter_model = (openrouter_model or "").strip()

    def translate(self, text: str, src: str = None, dst: str = None,
                  api_key: str = "", model: str = "",
                  engine: str = "auto") -> str:
        """
        Переводит текст. Пробует сервисы по порядку.
        api_key/model можно передать напрямую для одноразового вызова.
        engine: "auto", "orcarouter", "openrouter", "zen", "google", "deepl"
        """
        if not text or not text.strip():
            return ""

        src = src or self._source or "auto"
        dst = dst or self._target or "en"

        key = api_key or self._api_key
        mdl = model or self._model

        # Если указан конкретный движок
        if engine == "orcarouter":
            try:
                result = self._translate_orcarouter(text, src, dst,
                    self._orcarouter_key or key, self._orcarouter_model or mdl)
                if result:
                    return result
            except Exception as e:
                logger.debug(f"OrcaRouter failed: {e}")
            return "[OrcaRouter failed]"

        if engine == "openrouter":
            try:
                result = self._translate_openrouter(text, src, dst,
                    self._openrouter_key or key, self._openrouter_model or mdl)
                if result:
                    return result
            except Exception as e:
                logger.debug(f"OpenRouter failed: {e}")
            return "[OpenRouter failed]"

        if engine == "zen":
            try:
                result = self._translate_zen(text, src, dst, mdl)
                if result:
                    return result
            except Exception as e:
                logger.debug(f"Zen failed: {e}")
            return "[Zen failed]"

        # Auto mode — пробуем все по порядку
        # 1. OrcaRouter
        if self._orcarouter_key:
            try:
                result = self._translate_orcarouter(text, src, dst,
                    self._orcarouter_key, self._orcarouter_model)
                if result:
                    return result
            except Exception as e:
                logger.debug(f"OrcaRouter failed: {e}")

        # 2. OpenRouter
        if self._openrouter_key:
            try:
                result = self._translate_openrouter(text, src, dst,
                    self._openrouter_key, self._openrouter_model)
                if result:
                    return result
            except Exception as e:
                logger.debug(f"OpenRouter failed: {e}")

        # 3. Legacy OpenRouter key
        if key and key != self._orcarouter_key:
            try:
                result = self._translate_openrouter(text, src, dst, key, mdl)
                if result:
                    return result
            except Exception as e:
                logger.debug(f"OpenRouter (legacy) failed: {e}")

        # 4. Zen (бесплатный, без ключа)
        try:
            result = self._translate_zen(text, src, dst, mdl)
            if result:
                return result
        except Exception as e:
            logger.debug(f"Zen failed: {e}")

        # 5. Google Translate (fallback)
        try:
            result = self._translate_google(text, src, dst)
            if result:
                return result
        except Exception as e:
            logger.debug(f"Google Translate failed: {e}")

        # 6. DeepL free API (no key needed for small texts)
        try:
            result = self._translate_deepl_free(text, src, dst)
            if result:
                return result
        except Exception as e:
            logger.debug(f"DeepL free failed: {e}")

        # 7. Offline dictionary fallback (EN→RU only)
        if dst == "ru" and (src in ("en", "auto")):
            try:
                from en_ru_dict import translate_text
                result = translate_text(text)
                if result:
                    logger.info("[Translator] Used offline dictionary fallback")
                    return result
            except Exception as e:
                logger.debug(f"Offline dict failed: {e}")

        return "[Translation failed: all services unavailable]"

    # ── OpenRouter (LLM) ───────────────────────────────────────────────

    def _translate_openrouter(self, text: str, src: str, dst: str,
                              api_key: str, model: str) -> str:
        """Перевод через OpenRouter API (OpenAI-совместимый)."""
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
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model or "google/gemini-2.0-flash-001",
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

    # ── OrcaRouter (LLM) ──────────────────────────────────────────────

    def _translate_orcarouter(self, text: str, src: str, dst: str,
                              api_key: str, model: str) -> str:
        """Перевод через OrcaRouter API (OpenAI-совместимый, бесплатные модели)."""
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
            "https://api.orcarouter.ai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model or "qwen/qwen3.8-27b-free",
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
        resp = requests.get(
            "https://translate.googleapis.com/translate_a/single",
            params={
                "client": "gtx",
                "sl": src,
                "tl": dst,
                "dt": "t",
                "q": text,
            },
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()

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

    def test_connection(self, api_key: str, model: str, engine: str = "openrouter") -> dict:
        """
        Тестирует подключение к указанному сервису.
        engine: "openrouter", "orcarouter", "zen"
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

        if engine == "orcarouter":
            key = api_key or self._orcarouter_key
            mdl = model or self._orcarouter_model or "qwen/qwen3.8-27b-free"
            endpoint = "https://api.orcarouter.ai/v1/chat/completions"
        elif engine == "zen":
            key = ""
            mdl = model or "mimo-v2.5-free"
            endpoint = "https://opencode.ai/zen/v1/chat/completions"
        else:  # openrouter
            key = api_key or self._api_key
            mdl = model or self._model or "google/gemini-2.0-flash-001"
            endpoint = "https://openrouter.ai/api/v1/chat/completions"

        if engine != "zen" and (not key or not key.strip()):
            result["message"] = "API key is empty"
            return result

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
