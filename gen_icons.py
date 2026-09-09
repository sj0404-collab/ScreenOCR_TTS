from PIL import Image, ImageDraw, ImageFont
import os

sizes = {
    'mdpi': 48,
    'hdpi': 72,
    'xhdpi': 96,
    'xxhdpi': 144,
    'xxxhdpi': 192
}

base = r'D:\Manga\ScreenOCR_TTS\ScreenOverlayAPK\app\src\main\res'

for density, size in sizes.items():
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    margin = 4
    r = size // 6
    draw.rounded_rectangle([margin, margin, size - margin, size - margin], radius=r, fill=(13, 17, 23, 255))

    bar_h = max(3, size // 12)
    draw.rounded_rectangle([margin, size - margin - bar_h, size - margin, size - margin], radius=r, fill=(123, 104, 238, 255))

    font_size = int(size * 0.55)
    try:
        font = ImageFont.truetype("arialbd.ttf", font_size)
    except Exception:
        font = ImageFont.load_default()

    text = "T"
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    x = (size - tw) // 2 - bbox[0]
    y = (size - th) // 2 - bbox[1] - bar_h // 2
    draw.text((x, y), text, fill=(255, 255, 255, 255), font=font)

    folder = os.path.join(base, f'mipmap-{density}')
    os.makedirs(folder, exist_ok=True)
    img.save(os.path.join(folder, 'ic_launcher.png'))
    img.save(os.path.join(folder, 'ic_launcher_round.png'))
    print(f'Created {density}: {size}x{size}')

print('Done!')
