import avatar_animation as aa
import os, math, json
from PIL import Image

aa.ensure_dirs()
d = aa.get_anim_dir('polygon_breath')

import avatar_assets
path = avatar_assets.get_path('neutral', 'default')
img = Image.open(path).convert('RGBA')

W, H = 128, 128

for i in range(32):
    t = i / 32.0
    
    # Polygon-трансформы: дыхание + легкое вращение + смещение
    breathe = 1.0 + 0.03 * math.sin(t * 4 * math.pi)
    tilt = math.sin(t * 2 * math.pi) * 3.0
    shift_x = math.cos(t * 2 * math.pi) * 1.0
    shift_y = math.sin(t * math.pi) * 1.2
    
    sw = int(W * breathe)
    sh = int(H * breathe)
    angle_rad = math.radians(tilt)
    cos_a = math.cos(angle_rad)
    sin_a = math.sin(angle_rad)
    
    # 4 угла квадрата для polygon-tween
    corners = [(-sw/2, -sh/2), (sw/2, -sh/2), (sw/2, sh/2), (-sw/2, sh/2)]
    quad = []
    for cx, cy in corners:
        rx = cx * cos_a - cy * sin_a
        ry = cx * sin_a + cy * cos_a
        tx = W/2 + rx + shift_x
        ty = H/2 + ry + shift_y
        quad.extend([tx, ty])
    
    large = img.resize((256, 256), Image.LANCZOS)
    frame = large.transform((W, H), Image.Transform.QUAD, tuple(quad), resample=Image.BICUBIC)
    
    fname = f'frame_{i:04d}.png'
    fpath = os.path.join(d, fname)
    frame.save(fpath, 'PNG')
    print(f'Created {fpath}')

meta = {'name': 'polygon_breath', 'fps': 16, 'loop': True, 'frame_order': []}
json.dump(meta, open(os.path.join(d, '_meta.json'), 'w'), indent=2)
gif = aa.export_gif('polygon_breath', fps=16, size=(128, 128))
print(f'GIF: {gif}')
print(f'Frames: 32, Size: {W}x{H}')
