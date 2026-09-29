# -*- coding: utf-8 -*-
"""Test offline_dict.translate() with phrase matching."""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from offline_dict import OfflineTranslator
d = OfflineTranslator()

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
    "Let's go to the castle",
    "I don't understand",
    "Where are you",
    "What happened",
    "Give me the key",
    "I need help",
]

for t in tests:
    t0 = time.time()
    ru = d.translate(t)
    dt = (time.time() - t0) * 1000
    print(f"EN: {t}")
    print(f"RU: {ru}")
    print(f"    ({dt:.1f}ms)")
    print()
