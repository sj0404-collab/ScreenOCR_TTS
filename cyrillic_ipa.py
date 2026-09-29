"""Simple Cyrillic to IPA mapping for Russian Piper TTS.

Directly maps Russian text to IPA phonemes without needing espeak-ng.
Based on standard Russian phonetic rules.
"""

# Basic Cyrillic to IPA mapping
_CYRILLIC_TO_IPA = {
    'а': 'a', 'б': 'b', 'в': 'v', 'г': 'ɡ', 'д': 'd',
    'е': 'je', 'ё': 'jo', 'ж': 'ʒ', 'з': 'z', 'и': 'i',
    'й': 'j', 'к': 'k', 'л': 'l', 'м': 'm', 'н': 'n',
    'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't',
    'у': 'u', 'ф': 'f', 'х': 'x', 'ц': 'ts', 'ч': 'tʃ',
    'ш': 'ʃ', 'щ': 'ʃtʃ', 'ъ': '', 'ы': 'ɨ', 'ь': '',
    'э': 'e', 'ю': 'ju', 'я': 'ja',
}

# Voiced/voiceless pairs for final devoicing
_VOICED = set('бвгджз')
Voiceless = set('пфктшсцчхщ')
_VOWELS = set('аеёиоуыэюя')

# After consonant: е→e, ё→o, ю→u, я→a (soft sign does softening)
_AFTER_CONSONANT = {
    'е': 'e', 'ё': 'o', 'ю': 'u', 'я': 'a',
}


def cyrillic_to_ipa(text: str) -> str:
    """Convert Russian text to IPA phonemes for Piper TTS."""
    result = []
    text = text.lower()
    for i, ch in enumerate(text):
        if ch in _CYRILLIC_TO_IPA:
            prev = text[i - 1] if i > 0 else ''
            if ch in _AFTER_CONSONANT and prev and prev in 'бвгджзклмнпрстфхцчшщ':
                result.append(_AFTER_CONSONANT[ch])
            else:
                result.append(_CYRILLIC_TO_IPA[ch])
        elif ch.isalpha():
            # Latin letters - pass through
            result.append(ch)
        elif ch in '.,!?;:-':
            result.append(ch)
        elif ch == ' ':
            result.append(' ')
    return ''.join(result)


if __name__ == '__main__':
    tests = [
        "привет как дела",
        "это работает",
        "хорошо",
        "сейчас я тебе покажу",
        "hello world",
    ]
    for t in tests:
        print(f"{t!r:30} -> {cyrillic_to_ipa(t)!r}")
