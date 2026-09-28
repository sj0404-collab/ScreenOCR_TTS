# -*- coding: utf-8 -*-
"""Offline EN->RU dictionary — comprehensive with online fallback."""
import re
import json
import os
import logging
import requests

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LEARNED_PATH = os.path.join(BASE_DIR, "learned_dict.json")

from contractions import CONTRACTIONS
from phrases_dict import PHRASES
from words_extra import EXTRA_WORDS


# Articles to drop in EN→RU (Russian has no articles)
_ARTICLES = {"the", "a", "an"}


class OfflineTranslator:
    def __init__(self):
        self._words = {}
        self._phrases = dict(PHRASES)
        self._contractions = dict(CONTRACTIONS)
        self._words.update(EXTRA_WORDS)
        self._load_json("en_rus_full.json", self._words)
        self._load_json("cities_dict.json", self._phrases)
        self._load_json("game_names.json", self._phrases)
        self._learned = self._load_json_file(LEARNED_PATH)
        self._dirty = False

    def _load_json(self, name, target):
        path = os.path.join(BASE_DIR, name)
        if not os.path.exists(path): return
        try:
            with open(path, "r", encoding="utf-8") as f:
                d = json.load(f)
            for k, v in d.items():
                if k not in target and isinstance(v, str):
                    target[k] = v
        except Exception:
            pass

    def _load_json_file(self, path):
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def translate(self, text):
        if not text: return ""
        t = text.lower().strip()
        for s, f in self._contractions.items():
            t = t.replace(s, f)
        if t in self._phrases: return self._phrases[t]
        if t in self._learned: return self._learned[t]
        replaced = t
        pm = {}
        for p in sorted(self._phrases, key=len, reverse=True):
            pattern = r'\b' + re.escape(p) + r'\b'
            if re.search(pattern, replaced):
                k = f"__PH{len(pm)}__"
                pm[k] = self._phrases[p]
                replaced = re.sub(pattern, k, replaced)
        result = []
        for w in replaced.split():
            pure = re.sub(r'[^\w]', '', w)
            if pure in pm:
                result.append(pm[pure])
            elif w in pm:
                result.append(pm[w])
            else:
                c = re.sub(r'[^\w]', '', w)
                if c in _ARTICLES:
                    continue  # drop articles (Russian has none)
                if c in self._words: result.append(self._words[c])
                elif c: result.append(c)
        return " ".join(result)

    def has_untranslated(self, text):
        """Check if offline translation has obvious quality issues."""
        r = self.translate(text)
        if not r:
            return True
        # Check for leftover ASCII words (not articles, not short)
        if any(len(c) > 2 and c.isascii() and c.isalpha() for c in r.split()):
            return True
        # Check if the translation is mostly same as input (no real translation happened)
        t_words = set(text.lower().split())
        r_words = set(r.lower().split())
        if len(t_words & r_words) > len(t_words) * 0.5:
            return True
        # Check if translation has very few words compared to input (bad word-by-word)
        if len(r.split()) < len(text.split()) * 0.4:
            return True
        return False

    def translate_with_fallback(self, text, src="en", dst="ru",
                                zen_key=None):
        """Перевод: сначала словарь, при неполном покрытии — сеть.

        Параметр openrouter_key/openrouter_model удалён 26.09.2026 вместе с
        провайдером (ключ отклонялся с 403).
        """
        offline = self.translate(text)
        if not self.has_untranslated(text):
            return offline
        online = self._try_online(text, src, dst, zen_key)
        if online:
            key = text.lower().strip()
            self._learned[key] = online
            self._save_learned()
            return online
        return offline

    def _try_online(self, text, src, dst, zen_key):
        if zen_key:
            r = self._zen(text, dst, zen_key)
            if r: return r
        r = self._google(text, src, dst)
        if r: return r
        return ""

    def _zen(self, text, dst, api_key):
        try:
            resp = requests.post(
                "https://opencode.ai/zen/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}",
                         "Content-Type": "application/json"},
                json={"model": "space-bunny-free",
                      "messages": [{"role": "user", "content":
                        f"Translate to Russian. ONLY the translation.\n\n{text}"}],
                      "temperature": 0.1, "max_tokens": 1024},
                timeout=30)
            if resp.status_code == 200:
                c = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
                if c and c.strip(): return c.strip()
        except Exception as e:
            logger.debug(f"Zen: {e}")
        return ""

    def _google(self, text, src, dst):
        try:
            resp = requests.get(
                "https://translate.googleapis.com/translate_a/single",
                params={"client": "gtx", "sl": src, "tl": dst, "dt": "t", "q": text},
                headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                if data and data[0]:
                    return "".join(p[0] for p in data[0] if p[0]).strip()
        except Exception as e:
            logger.debug(f"Google: {e}")
        return ""

    def _save_learned(self):
        try:
            with open(LEARNED_PATH, "w", encoding="utf-8") as f:
                json.dump(self._learned, f, ensure_ascii=False)
        except Exception:
            pass

    def translate_ja(self, text):
        """Offline Japanese -> Russian via the bundled ja_ru_dict.json."""
        return _get_ja_ru().translate(text)


# ── Offline Japanese -> Russian dictionary (JMdict common + EN->RU app dict) ──
_JA_RU_SINGLETON = None

# Particles / auxiliaries that carry no meaning on their own — dropped in output.
_JA_PARTICLES = set(
    "をにてはがのとでものへやかねよさぞわくしけなただですますたいてるってうちようではもしもの"
    "われこれそれあれどれせいようえみえみえーぁぃぅぇぉっゃゅょゎ"
    "がでにをへはもやかよわばぱだざば"
)


class JaRuTranslator:
    """Greedy longest-match Japanese->Russian dictionary translator.

    No network, no morphological analyzer — segments the input by matching the
    longest known surface form, translates each to Russian, and drops particles.
    """

    def __init__(self, path=None):
        if path is None:
            path = os.path.join(BASE_DIR, "ja_ru_dict.json")
        self.dict = {}
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    self.dict = json.load(f)
            except Exception:
                pass
        self.keyset = set(self.dict.keys())
        self.maxlen = max((len(k) for k in self.keyset), default=1)

    def translate(self, text):
        if not text or not self.dict:
            return ""
        out = []
        i, n = 0, len(text)
        while i < n:
            matched = None
            for L in range(min(self.maxlen, n - i), 0, -1):
                if text[i:i + L] in self.keyset:
                    matched = text[i:i + L]
                    break
            if matched:
                if matched in _JA_PARTICLES:
                    i += len(matched)
                    continue
                out.append(self.dict[matched])
                i += len(matched)
            else:
                c = text[i]
                if c in _JA_PARTICLES:
                    i += 1
                    continue
                out.append(c)
                i += 1
        return " ".join(out).strip()


def _get_ja_ru():
    global _JA_RU_SINGLETON
    if _JA_RU_SINGLETON is None:
        _JA_RU_SINGLETON = JaRuTranslator()
    return _JA_RU_SINGLETON
