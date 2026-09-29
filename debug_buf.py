# -*- coding: utf-8 -*-
import tflite
with open('models/cyrillic_ocr/cyrillic_detector.tflite', 'rb') as f:
    buf = f.read()
model = tflite.Model.GetRootAs(buf, 0)
sg = model.Subgraphs(0)

for t_idx in [0, 1, 2, 10, 100]:
    t = sg.Tensors(t_idx)
    b = model.Buffers(t.Buffer())
    print(f'Tensor {t_idx}: Buffer idx={t.Buffer()}, DataLength={b.DataLength()}')
    try:
        d = b.DataAsNumpy()
        print(f'  DataAsNumpy type={type(d)}')
        if hasattr(d, 'shape'):
            print(f'  shape={d.shape} dtype={d.dtype}')
            print(f'  first 10: {d[:10]}')
        else:
            print(f'  value={d}')
    except Exception as e:
        print(f'  error: {e}')
    print()
