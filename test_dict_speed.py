# -*- coding: utf-8 -*-
"""Quick test of offline word-by-word translation."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from offline_dict import OfflineTranslator

d = OfflineTranslator()
print(f"Dictionary loaded: {len(d._words)} words, {len(d._phrases)} phrases")

tests = [
    "Hello how are you",
    "I need to find the key",
    "The door is locked",
    "Where is the exit",
    "Help me please",
    "Attack the enemy",
    "Open the chest",
    "Follow me",
    "Watch out",
    "Good morning",
    "I am looking for something",
    "Can you help me find it",
    "The treasure is hidden behind the door",
    "Be careful there are enemies nearby",
    "Let us go to the castle",
]

import time
for t in tests:
    t0 = time.time()
    words = t.split()
    translated = []
    for w in words:
        clean = w.strip(".,!?").lower()
        if clean in d._words:
            translated.append(d._words[clean])
        elif clean in d._phrases:
            translated.append(d._phrases[clean])
        else:
            expanded = clean
            for s, f in d._contractions.items():
                if clean == s.strip("'"):
                    expanded = f
                    break
            if expanded != clean and expanded in d._words:
                translated.append(d._words[expanded])
            else:
                translated.append(clean)
    ru = " ".join(translated)
    dt = (time.time() - t0) * 1000
    print(f"EN: {t}")
    print(f"RU: {ru}")
    print(f"    ({dt:.1f}ms)")
    print()
