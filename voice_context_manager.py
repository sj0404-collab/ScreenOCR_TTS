"""Voice Context Manager - auto-learns which voice works best for each situation.
Saves learning data to voice_context_learning.json
"""
import re
import os
import json
from typing import Dict, List, Optional, Tuple

LEARNING_FILE = os.path.join(os.path.dirname(__file__), "voice_context_learning.json")

# Situation types
SITUATION_TYPES = {
    "narrator": "Повествование / описания",
    "dialogue_male": "Мужская прямая речь",
    "dialogue_female": "Женская прямая речь",
    "emotion": "Эмоциональные описания",
    "action": "Действия / глаголы",
    "description": "Описания / детали",
    "thought": "Внутренний монолог",
    "question": "Вопросы",
    "exclamation": "Восклицания",
}

# Patterns to detect situations
SITUATION_PATTERNS = {
    "dialogue_male": [
        r'(?:сказал|спросил|ответил|воскликнул|прошептал|мужчина|парень|он\b)',
        r'^[^"]*"[^"]*"[^.]*(?:он|мужчина|парень)',
    ],
    "dialogue_female": [
        r'(?:сказала|спросила|ответила|воскликнула|прошептала|женщина|девушка|она\b)',
        r'^[^"]*"[^"]*"[^.]*(?:она|женщина|девушка)',
    ],
    "emotion": [
        r'(?:радостно|грустно|счастливо|злостно|испуганно|удивленно|спокойно)',
        r'(?:улыбнулся|заплакал|рассмеялся|вздохнул|вскрикнул)',
    ],
    "action": [
        r'(?:побежал|остановился|сел|встал|пошёл|открыл|закрыл|взял|положил)',
    ],
    "thought": [
        r'(?:подумал|подумала|решил|решила|понял|поняла|осознал|осознала)',
        r'(?:казалось|казалось|казалось)',
    ],
    "question": [
        r'\?$',
        r'(?:почему|зачем|когда|где|как|что|кто|чего|почему)',
    ],
    "exclamation": [
        r'!$',
        r'(?:восклицание|вскрик)',
    ],
}


class VoiceContextManager:
    """Auto-learns which voice works best for each context type."""

    def __init__(self):
        self._learning_data = self._load_learning()
        self._correction_count = 0

    def _load_learning(self) -> dict:
        if os.path.isfile(LEARNING_FILE):
            with open(LEARNING_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {
            "voice_scores": {},  # {voice_name: {situation: score}}
            "corrections": [],   # List of user corrections
            "total_synths": 0,
        }

    def _save_learning(self):
        with open(LEARNING_FILE, 'w', encoding='utf-8') as f:
            json.dump(self._learning_data, f, ensure_ascii=False, indent=2)

    def detect_situation(self, text: str, prev_text: str = None) -> List[str]:
        """Detect situation types in text. Returns list of detected situations."""
        situations = []
        text_lower = text.lower().strip()

        for situation, patterns in SITUATION_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, text_lower):
                    situations.append(situation)
                    break

        # Default to narrator if nothing detected
        if not situations:
            situations.append("narrator")

        return situations

    def get_best_voice(self, situations: List[str], available_voices: List[str],
                       male_voices: List[str] = None, female_voices: List[str] = None,
                       narrator_voice: str = None) -> str:
        """Get best voice for given situations based on learning.
        Enforces gender balance: male dialogue -> male voice, female -> female.
        """
        if not available_voices:
            return None

        male_voices = male_voices or []
        female_voices = female_voices or []
        narrator_voice = narrator_voice or available_voices[0]

        # Determine required gender
        require_male = "dialogue_male" in situations
        require_female = "dialogue_female" in situations

        # Filter available voices by gender if needed
        if require_male:
            candidates = [v for v in available_voices if v in male_voices]
        elif require_female:
            candidates = [v for v in available_voices if v in female_voices]
        else:
            candidates = available_voices[:]

        if not candidates:
            candidates = available_voices[:]

        # Score each voice
        voice_scores = {}
        for voice in candidates:
            score = 0
            for situation in situations:
                situation_scores = self._learning_data["voice_scores"].get(voice, {})
                score += situation_scores.get(situation, 0)

            # Base preferences
            if "dialogue_male" in situations and voice in male_voices:
                score += 10
            if "dialogue_female" in situations and voice in female_voices:
                score += 10
            if "narrator" in situations and voice == narrator_voice:
                score += 10

            # Gender balance: slightly prefer underrepresented gender
            if require_male or require_female:
                # Add small random factor to avoid always picking same voice
                import random
                score += random.randint(0, 2)

            voice_scores[voice] = score

        if voice_scores:
            return max(voice_scores, key=voice_scores.get)
        return candidates[0] if candidates else available_voices[0]

    def record_correction(self, text: str, old_voice: str, new_voice: str,
                          situation: str):
        """Record user correction for learning."""
        self._learning_data["corrections"].append({
            "text": text[:100],
            "old_voice": old_voice,
            "new_voice": new_voice,
            "situation": situation,
        })

        # Update voice scores
        if new_voice not in self._learning_data["voice_scores"]:
            self._learning_data["voice_scores"][new_voice] = {}
        if situation not in self._learning_data["voice_scores"][new_voice]:
            self._learning_data["voice_scores"][new_voice][situation] = 0
        self._learning_data["voice_scores"][new_voice][situation] += 1

        # Decrease old voice score
        if old_voice in self._learning_data["voice_scores"]:
            if situation in self._learning_data["voice_scores"][old_voice]:
                self._learning_data["voice_scores"][old_voice][situation] -= 1

        self._correction_count += 1
        if self._correction_count % 5 == 0:  # Save every 5 corrections
            self._save_learning()

    def increment_synth_count(self):
        """Increment total synthesis count."""
        self._learning_data["total_synths"] += 1
        if self._learning_data["total_synths"] % 50 == 0:
            self._save_learning()

    def get_stats(self) -> dict:
        """Get learning statistics."""
        return {
            "total_synths": self._learning_data["total_synths"],
            "total_corrections": len(self._learning_data["corrections"]),
            "voices_learned": len(self._learning_data["voice_scores"]),
        }

    def get_voice_recommendations(self) -> Dict[str, str]:
        """Get recommended voice for each situation type."""
        recommendations = {}
        for situation in SITUATION_TYPES:
            best_voice = None
            best_score = -1
            for voice, scores in self._learning_data["voice_scores"].items():
                score = scores.get(situation, 0)
                if score > best_score:
                    best_score = score
                    best_voice = voice
            if best_voice:
                recommendations[situation] = best_voice
        return recommendations
