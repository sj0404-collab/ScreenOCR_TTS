"""
Persistent state for overlay boxes (region rectangles).
"""
import json
import os
from dataclasses import dataclass, asdict
from typing import List
from pathlib import Path

_STATE_FILE = str(Path(__file__).parent / "overlay_boxes.json")


@dataclass
class OverlayBox:
    x: int = 0
    y: int = 0
    w: int = 0
    h: int = 0


class OverlayState:
    def __init__(self):
        self.boxes: List[OverlayBox] = []
        self._load()

    def _load(self):
        if os.path.exists(_STATE_FILE):
            try:
                with open(_STATE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.boxes = [OverlayBox(**b) for b in data.get("boxes", [])]
            except Exception:
                self.boxes = []

    def _save(self):
        with open(_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"boxes": [asdict(b) for b in self.boxes]}, f, ensure_ascii=False, indent=2)

    @classmethod
    def add_box(cls, box: OverlayBox):
        state = cls()
        state.boxes.append(box)
        state._save()
        return state
