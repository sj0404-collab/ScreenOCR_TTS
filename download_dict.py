# -*- coding: utf-8 -*-
import sys, os, urllib.request, ssl

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

base_url = 'https://raw.githubusercontent.com/sj0404-collab/ocr-rus-cyrillic/main'
files = {
    'models/cyrillic_ocr/cyrillic_dict_v3.txt': 'models/dicts/cyrillic_dict.txt',
    'models/cyrillic_ocr/cyrillic_dict_v5.txt': 'models/dicts/ppocrv5_cyrillic_dict.txt',
}

for local_path, remote_path in files.items():
    url = f'{base_url}/{remote_path}'
    try:
        data = urllib.request.urlopen(url, context=ctx, timeout=10).read()
        # Strip BOM if present
        if data[:3] == b'\xef\xbb\xbf':
            data = data[3:]
        with open(local_path, 'wb') as f:
            f.write(data)
        text = data.decode('utf-8')
        chars = [l for l in text.splitlines() if l.strip()]
        print(f'{local_path}: {len(data)} bytes, {len(chars)} chars')
        for i, c in enumerate(chars[:5]):
            print(f'  [{i}] U+{ord(c):04X} = {repr(c)}')
        print('  ...')
        for i in range(max(5, len(chars)-3), len(chars)):
            c = chars[i]
            print(f'  [{i}] U+{ord(c):04X} = {repr(c)}')
    except Exception as e:
        print(f'{local_path}: Error: {e}')