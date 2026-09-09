"""
history.py — Сохранение и загрузка истории распознавания/перевода.

Форматы: JSON, CSV
"""
import json
import csv
import os
import time
from pathlib import Path
from typing import Optional


class HistoryEntry:
    """Одна запись истории."""
    def __init__(self, timestamp: float = None, source_text: str = "",
                 translated_text: str = "", source_lang: str = "",
                 target_lang: str = "", ocr_text: str = "",
                 voice_preset: str = "", image_path: str = ""):
        self.timestamp = timestamp or time.time()
        self.source_text = source_text
        self.translated_text = translated_text
        self.source_lang = source_lang
        self.target_lang = target_lang
        self.ocr_text = ocr_text
        self.voice_preset = voice_preset
        self.image_path = image_path

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "time_str": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.timestamp)),
            "source_text": self.source_text,
            "translated_text": self.translated_text,
            "source_lang": self.source_lang,
            "target_lang": self.target_lang,
            "ocr_text": self.ocr_text,
            "voice_preset": self.voice_preset,
            "image_path": self.image_path,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "HistoryEntry":
        return cls(
            timestamp=d.get("timestamp", 0),
            source_text=d.get("source_text", ""),
            translated_text=d.get("translated_text", ""),
            source_lang=d.get("source_lang", ""),
            target_lang=d.get("target_lang", ""),
            ocr_text=d.get("ocr_text", ""),
            voice_preset=d.get("voice_preset", ""),
            image_path=d.get("image_path", ""),
        )


class History:
    """Менеджер истории."""
    def __init__(self, filepath: str = None, max_entries: int = 500):
        if filepath is None:
            filepath = os.path.join(os.path.dirname(__file__), "history.json")
        self.filepath = filepath
        self.max_entries = max_entries
        self.entries: list[HistoryEntry] = []
        self.load()

    def load(self):
        if not os.path.exists(self.filepath):
            self.entries = []
            return
        try:
            with open(self.filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.entries = [HistoryEntry.from_dict(e) for e in data]
        except Exception:
            self.entries = []

    def save(self):
        data = [e.to_dict() for e in self.entries[-self.max_entries:]]
        with open(self.filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def add(self, entry: HistoryEntry):
        self.entries.append(entry)
        if len(self.entries) > self.max_entries:
            self.entries = self.entries[-self.max_entries:]
        self.save()

    def add_simple(self, source_text: str, translated_text: str = "",
                   source_lang: str = "", target_lang: str = "",
                   ocr_text: str = "", voice_preset: str = ""):
        entry = HistoryEntry(
            source_text=source_text,
            translated_text=translated_text,
            source_lang=source_lang,
            target_lang=target_lang,
            ocr_text=ocr_text,
            voice_preset=voice_preset,
        )
        self.add(entry)

    def search(self, query: str) -> list[HistoryEntry]:
        q = query.lower()
        return [e for e in self.entries
                if q in e.source_text.lower()
                or q in e.translated_text.lower()
                or q in e.ocr_text.lower()]

    def get_recent(self, n: int = 10) -> list[HistoryEntry]:
        return self.entries[-n:]

    def get_by_lang(self, source_lang: str = None, target_lang: str = None) -> list[HistoryEntry]:
        result = self.entries
        if source_lang:
            result = [e for e in result if e.source_lang == source_lang]
        if target_lang:
            result = [e for e in result if e.target_lang == target_lang]
        return result

    def clear(self):
        self.entries = []
        self.save()

    def export_csv(self, csv_path: str):
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=[
                "time_str", "source_lang", "target_lang",
                "source_text", "translated_text", "ocr_text", "voice_preset"
            ])
            writer.writeheader()
            for e in self.entries:
                d = e.to_dict()
                del d["timestamp"]
                del d["image_path"]
                writer.writerow(d)

    def import_csv(self, csv_path: str):
        with open(csv_path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                entry = HistoryEntry(
                    source_text=row.get("source_text", ""),
                    translated_text=row.get("translated_text", ""),
                    source_lang=row.get("source_lang", ""),
                    target_lang=row.get("target_lang", ""),
                    ocr_text=row.get("ocr_text", ""),
                    voice_preset=row.get("voice_preset", ""),
                )
                self.entries.append(entry)
        self.save()

    def __len__(self):
        return len(self.entries)

    def __getitem__(self, idx):
        return self.entries[idx]


if __name__ == "__main__":
    h = History()
    print(f"History: {len(h)} entries")
    if h.entries:
        for e in h.get_recent(3):
            print(f"  [{e.to_dict().get('time_str', '?')}] {e.source_text[:50]}")
