"""
Font manager for game fonts.
Loads available fonts from fonts/ directory, provides font selection for OCR and overlay.
"""
import os
import json
import logging

logger = logging.getLogger(__name__)

FONTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")

# Font categories for UI grouping
FONT_CATEGORIES = {
    "Pixel / Retro": [
        "PressStart2P", "VT323", "Silkscreen", "PixelifySans",
        "DotGothic16", "Micro5", "SedgwickAveDisplay",
    ],
    "Sci-Fi / Tech": [
        "Orbitron", "Rajdhani", "Audiowide", "Exo2", "ChakraPetch",
        "ShareTechMono", "Electrolize", "Aldrich", "MajorMonoDisplay",
    ],
    "Fantasy / Medieval": [
        "MedievalSharp", "Cinzel", "CinzelDecorative", "UncialAntiqua",
        "Calistoga", "Metamorphous", "AlmendraDisplay",
    ],
    "Horror": [
        "Creepster", "Butcherman", "Eater", "Nosifer", "RubikGlitch",
    ],
    "Modern / Clean": [
        "Montserrat", "Poppins", "Inter", "Raleway", "Oswald",
        "BebasNeue", "BarlowCondensed", "Teko", "RussoOne", "BlackOpsOne",
    ],
    "Cyberpunk / Neon": [
        "BungeeShade", "RubikMonoOne", "Monoton", "Fascinate",
        "FasterOne", "Syncopate",
    ],
    "Racing / Sports": [
        "BungeeInline", "BungeeOutline", "Wallpoet",
        "SairaStencilOne", "Staatliches",
    ],
    "RPG / Adventure": [
        "Philosopher", "SpectralSC", "Cardo", "YesevaOne",
        "PlayfairDisplaySC",
    ],
    "Military / Tactical": [
        "BlackHanSans", "Junge", "BlackOpsOne", "RussoOne", "Teko",
    ],
    "Steampunk": [
        "SpecialElite", "PermanentMarker", "HomemadeApple", "RockSalt",
    ],
    "Anime / Manga": [
        "ZenDots", "DelaGothicOne", "ReggaeOne", "HachiMaruPop",
    ],
}


class FontManager:
    """Manages game fonts for OCR preprocessing and overlay display."""

    def __init__(self):
        self._fonts = {}  # name -> path
        self._load_fonts()

    def _load_fonts(self):
        """Scan fonts/ directory and load available fonts."""
        if not os.path.isdir(FONTS_DIR):
            logger.warning(f"[FontManager] Fonts directory not found: {FONTS_DIR}")
            return

        for fname in os.listdir(FONTS_DIR):
            if fname.lower().endswith(('.ttf', '.otf')):
                name = os.path.splitext(fname)[0]
                path = os.path.join(FONTS_DIR, fname)
                self._fonts[name] = path

        logger.info(f"[FontManager] Loaded {len(self._fonts)} fonts")

    def get_font_path(self, name: str) -> str:
        """Get full path to a font by name."""
        return self._fonts.get(name, "")

    def get_all_fonts(self) -> dict:
        """Get all available fonts: {name: path}."""
        return dict(self._fonts)

    def get_font_names(self) -> list:
        """Get sorted list of available font names."""
        return sorted(self._fonts.keys())

    def get_fonts_by_category(self) -> dict:
        """Get fonts grouped by category (only available fonts included)."""
        result = {}
        for category, names in FONT_CATEGORIES.items():
            available = [n for n in names if n in self._fonts]
            if available:
                result[category] = available
        return result

    def get_fallback_font(self) -> str:
        """Get a good fallback font for game subtitles."""
        preferred = [
            "Rajdhani", "Exo2", "ChakraPetch", "Orbitron",
            "Montserrat", "Poppins", "Inter", "Raleway",
        ]
        for name in preferred:
            if name in self._fonts:
                return self._fonts[name]
        # Last resort: system font
        return "Segoe UI"

    def get_qfont(self, name: str, size: int = 14) -> "QFont":
        """Create a QFont from a game font name."""
        from PyQt6.QtGui import QFont
        path = self._fonts.get(name, "")
        if path and os.path.exists(path):
            font = QFont()
            font.setFamilies([name])
            font.setPointSize(size)
            # Load from file for custom fonts
            from PyQt6.QtGui import QFontDatabase
            font_id = QFontDatabase.addApplicationFont(path)
            if font_id >= 0:
                families = QFontDatabase.applicationFontFamilies(font_id)
                if families:
                    font.setFamilies([families[0]])
            return font
        return QFont("Segoe UI", size)


# Singleton
_manager = None


def get_font_manager() -> FontManager:
    global _manager
    if _manager is None:
        _manager = FontManager()
    return _manager
