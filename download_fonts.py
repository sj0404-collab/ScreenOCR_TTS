"""
Download game-style English fonts from Google Fonts for OCR preprocessing and overlay.
Run once: python download_fonts.py
"""
import os
import urllib.request
import zipfile
import io
import json

FONTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
os.makedirs(FONTS_DIR, exist_ok=True)

# Google Fonts that are popular in games
GAME_FONTS = {
    # === PIXEL / RETRO ===
    "Press Start 2P": "PressStart2P",
    "VT323": "VT323",
    "Silkscreen": "Silkscreen",
    "Pixelify Sans": "PixelifySans",
    "DotGothic16": "DotGothic16",
    "Micro 5": "Micro5",
    "Sedgwick Ave Display": "SedgwickAveDisplay",

    # === SCI-FI / TECH ===
    "Orbitron": "Orbitron",
    "Rajdhani": "Rajdhani",
    "Audiowide": "Audiowide",
    "Exo 2": "Exo2",
    "Chakra Petch": "ChakraPetch",
    "Share Tech Mono": "ShareTechMono",
    "Electrolize": "Electrolize",
    "Aldrich": "Aldrich",
    "Bungee Hairline": "BungeeHairline",
    "Major Mono Display": "MajorMonoDisplay",

    # === FANTASY / MEDIEVAL ===
    "MedievalSharp": "MedievalSharp",
    "Cinzel": "Cinzel",
    "Cinzel Decorative": "CinzelDecorative",
    "Uncial Antiqua": "UncialAntiqua",
    "Calistoga": "Calistoga",
    "Metamorphous": "Metamorphous",
    "Almendra Display": "AlmendraDisplay",

    # === HORROR ===
    "Creepster": "Creepster",
    "Butcherman": "Butcherman",
    "Eater": "Eater",
    "Nosifer": "Nosifer",
    "Rubik Glitch": "RubikGlitch",

    # === MODERN / CLEAN ===
    "Montserrat": "Montserrat",
    "Poppins": "Poppins",
    "Inter": "Inter",
    "Raleway": "Raleway",
    "Oswald": "Oswald",
    "Bebas Neue": "BebasNeue",
    "Barlow Condensed": "BarlowCondensed",
    "Teko": "Teko",
    "Russo One": "RussoOne",
    "Black Ops One": "BlackOpsOne",

    # === CYBERPUNK / NEON ===
    "Bungee Shade": "BungeeShade",
    "Rubik Mono One": "RubikMonoOne",
    "Monoton": "Monoton",
    "Fascinate": "Fascinate",
    "Faster One": "FasterOne",
    "Syncopate": "Syncopate",
    "Audiowide": "Audiowide",

    # === RACING / SPORTS ===
    "Bungee Inline": "BungeeInline",
    "Bungee Outline": "BungeeOutline",
    "Wallpoet": "Wallpoet",
    "Saira Stencil One": "SairaStencilOne",
    "Staatliches": "Staatliches",

    # === RPG / ADVENTURE ===
    "Philosopher": "Philosopher",
    "Spectral SC": "SpectralSC",
    "Cardo": "Cardo",
    "Yeseva One": "YesevaOne",
    "Playfair Display SC": "PlayfairDisplaySC",

    # === MILITARY / TACTICAL ===
    "Black Han Sans": "BlackHanSans",
    "Junge": "Junge",
    "Bungee Shade": "BungeeShade",

    # === STEAMPUNK ===
    "Special Elite": "SpecialElite",
    "Permanent Marker": "PermanentMarker",
    "Homemade Apple": "HomemadeApple",
    "Rock Salt": "RockSalt",

    # === ANIME / MANGA ===
    "Zen Dots": "ZenDots",
    "Noto Sans JP": "NotoSansJP",
    "Dela Gothic One": "DelaGothicOne",
    "Reggae One": "ReggaeOne",
    "Hachi Maru Pop": "HachiMaruPop",
}


def download_font(name: str, family: str):
    """Download a font from Google Fonts API."""
    safe_name = family.replace(" ", "+")
    url = f"https://fonts.google.com/download?family={safe_name}"
    font_path = os.path.join(FONTS_DIR, f"{family}.ttf")

    if os.path.exists(font_path):
        return True

    try:
        print(f"  Downloading {name}...")
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
        })
        resp = urllib.request.urlopen(req, timeout=15)
        data = resp.read()

        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for fname in zf.namelist():
                if fname.endswith('.ttf') or fname.endswith('.otf'):
                    with zf.open(fname) as src, open(font_path, 'wb') as dst:
                        dst.write(src.read())
                    print(f"  OK: {font_path}")
                    return True
    except Exception as e:
        print(f"  FAIL: {name}: {e}")

    # Fallback: try direct CSS URL
    try:
        css_url = f"https://fonts.googleapis.com/css2?family={safe_name}&display=swap"
        req = urllib.request.Request(css_url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
        })
        resp = urllib.request.urlopen(req, timeout=10)
        css = resp.read().decode()
        import re
        match = re.search(r'url\((https://[^)]+\.ttf)\)', css)
        if match:
            font_url = match.group(1)
            urllib.request.urlretrieve(font_url, font_path)
            print(f"  OK (fallback): {font_path}")
            return True
    except Exception as e:
        print(f"  FAIL (fallback): {name}: {e}")

    return False


def main():
    print(f"=== Downloading {len(GAME_FONTS)} game fonts ===")
    print(f"Target: {FONTS_DIR}\n")

    ok = 0
    fail = 0
    for name, family in GAME_FONTS.items():
        if download_font(name, family):
            ok += 1
        else:
            fail += 1

    print(f"\n=== Done: {ok} downloaded, {fail} failed ===")

    # Save font list
    font_list = []
    for f in os.listdir(FONTS_DIR):
        if f.endswith(('.ttf', '.otf')):
            font_list.append(f)
    with open(os.path.join(FONTS_DIR, "font_list.json"), "w") as fp:
        json.dump(sorted(font_list), fp, indent=2)
    print(f"Font list saved: {len(font_list)} fonts")


if __name__ == "__main__":
    main()
