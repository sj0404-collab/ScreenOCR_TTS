# -*- coding: utf-8 -*-
"""Analyze TFLite models using the tflite Python package."""
import tflite
import numpy as np

def analyze_model(path):
    with open(path, 'rb') as f:
        buf = f.read()
    model = tflite.Model.GetRootAs(buf, 0)
    
    BUILTIN_OP_NAMES = {
        0: 'ADD', 1: 'AVERAGE_POOL_2D', 2: 'CONCATENATION', 3: 'CONV_2D',
        4: 'DEPTHWISE_CONV_2D', 6: 'DEQUANTIZE', 9: 'FULLY_CONNECTED',
        14: 'LOGISTIC', 17: 'MAX_POOL_2D', 18: 'MUL', 19: 'RELU',
        21: 'RELU6', 22: 'RESHAPE', 24: 'SOFTMAX', 29: 'STRIDED_SLICE',
        32: 'PAD', 33: 'UNIDIRECTIONAL_SEQUENCE_LSTM', 37: 'TRANSPOSE',
        38: 'MEAN', 39: 'SUB', 40: 'DIV', 47: 'PACK', 51: 'RESIZE_BILINEAR',
        56: 'STRIDED_SLICE', 72: 'BATCH_MATMUL', 111: 'ADD',
    }
    
    print(f'Model: {path}')
    print(f'Subgraphs: {model.SubgraphsLength()}')
    print(f'OperatorCodes: {model.OperatorCodesLength()}')
    
    # Resolve operator codes
    opcodes = []
    for i in range(model.OperatorCodesLength()):
        oc = model.OperatorCodes(i)
        builtin = oc.BuiltinCode()
        if builtin == 32767:  # extended
            builtin = oc.DeprecatedBuiltinCode()
        opcodes.append(builtin)
    
    for sg_idx in range(model.SubgraphsLength()):
        sg = model.Subgraphs(sg_idx)
        name = sg.Name().decode() if sg.Name() else str(sg_idx)
        print(f'\n  Subgraph "{name}": {sg.OperatorsLength()} ops, {sg.TensorsLength()} tensors')
        
        # Input/output tensors
        inputs = [sg.Inputs(j) for j in range(sg.InputsLength())]
        outputs = [sg.Outputs(j) for j in range(sg.OutputsLength())]
        print(f'    Inputs:  {inputs}')
        print(f'    Outputs: {outputs}')
        
        # Tensor shapes
        for t_idx in range(sg.TensorsLength()):
            t = sg.Tensors(t_idx)
            tname = t.Name().decode() if t.Name() else str(t_idx)
            shape = [t.Shape(j) for j in range(t.ShapeLength())]
            dtype = t.Type()
            buf_idx = t.Buffer()
            buf = model.Buffers(buf_idx)
            buf_size = buf.DataLength() if buf.DataLength() > 0 else 0
            if t_idx in inputs or t_idx in outputs:
                print(f'    Tensor {t_idx}: {tname} shape={shape} dtype={dtype} buf={buf_idx} ({buf_size} bytes) [IN/OUT]')
        
        # Op counts
        op_used = {}
        for op_i in range(sg.OperatorsLength()):
            op = sg.Operators(op_i)
            oc_idx = op.OpcodeIndex()
            if oc_idx < len(opcodes):
                builtin = opcodes[oc_idx]
                name = BUILTIN_OP_NAMES.get(builtin, f'UNKNOWN_{builtin}')
                op_used[name] = op_used.get(name, 0) + 1
        
        print(f'    Op counts:')
        for name, count in sorted(op_used.items(), key=lambda x: -x[1]):
            print(f'      {name}: {count}')
    
    import os
    print(f'File size: {os.path.getsize(path) / 1024 / 1024:.2f} MB')

print('='*60)
print('DETECTOR')
print('='*60)
analyze_model('models/cyrillic_ocr/cyrillic_detector.tflite')

print('\n' + '='*60)
print('RECOGNIZER V3')
print('='*60)
analyze_model('models/cyrillic_ocr/cyrillic_recognizer_v3.tflite')

print('\n' + '='*60)
print('RECOGNIZER V5')
print('='*60)
analyze_model('models/cyrillic_ocr/cyrillic_recognizer_v5.tflite')
