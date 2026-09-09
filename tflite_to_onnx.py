# -*- coding: utf-8 -*-
"""
TFLite to ONNX converter for PP-OCR models.
Extracts weights and structure from TFLite flatbuffer, builds ONNX graph.
"""
import os
import struct
import numpy as np
import tflite
from typing import Dict, List, Tuple, Optional

try:
    import onnx
    from onnx import helper, TensorProto, numpy_helper
    HAS_ONNX = True
except ImportError:
    HAS_ONNX = False

# TFLite builtin op codes -> names
BUILTIN_CODES = {
    0: 'ADD', 1: 'AVERAGE_POOL_2D', 2: 'CONCATENATION', 3: 'CONV_2D',
    4: 'DEPTHWISE_CONV_2D', 9: 'FULLY_CONNECTED', 14: 'LOGISTIC',
    17: 'MAX_POOL_2D', 18: 'MUL', 19: 'RELU', 21: 'RELU6', 22: 'RESHAPE',
    24: 'SOFTMAX', 29: 'STRIDED_SLICE', 32: 'PAD', 37: 'TRANSPOSE',
    38: 'MEAN', 39: 'SUB', 40: 'DIV', 44: 'FLOOR_DIV',
    47: 'PACK', 51: 'RESIZE_BILINEAR', 52: 'RESIZE_NEAREST_NEIGHBOR',
    53: 'CALL', 56: 'STRIDED_SLICE', 72: 'BATCH_MATMUL',
    111: 'ADD', 112: 'SQUARE_DIFF',
}

# Extended codes (PaddleOCR custom)
EXTENDED_CODES = {
    34: 'PAD', 41: 'PRELU', 42: 'SIGMOID', 49: 'GATHER',
    67: 'SQUEEZE', 75: 'EXPAND_DIMS', 97: 'FULLY_CONNECTED',
    126: 'HARD_SIGMOID', 152: 'HARD_SWISH',
}


def get_op_name(opcode_index: int, model: tflite.Model) -> str:
    oc = model.OperatorCodes(opcode_index)
    builtin = oc.BuiltinCode()
    if builtin in BUILTIN_CODES:
        return BUILTIN_CODES[builtin]
    if builtin in EXTENDED_CODES:
        return EXTENDED_CODES[builtin]
    return f'UNKNOWN_{builtin}'


def extract_weights(model: tflite.Model, buf_idx: int) -> Optional[np.ndarray]:
    buf = model.Buffers(buf_idx)
    if buf.DataLength() == 0:
        return None
    data = buf.DataAsNumpy()
    if isinstance(data, int):
        return None
    return np.array(data, dtype=np.uint8)


def extract_tensor(model: tflite.Model, sg: tflite.SubGraph, t_idx: int) -> Optional[np.ndarray]:
    t = sg.Tensors(t_idx)
    buf_data = extract_weights(model, t.Buffer())
    if buf_data is None:
        return None
    
    dtype_map = {
        0: np.float32,  # FLOAT32
        1: np.float16,  # FLOAT16
        2: np.int32,    # INT32
        3: np.uint8,    # UINT8
        4: np.int64,    # INT64
        9: np.int8,     # INT8
        11: np.float64, # FLOAT64
    }
    tf_type = t.Type()
    np_type = dtype_map.get(tf_type, np.float32)
    
    shape = [t.Shape(j) for j in range(t.ShapeLength())]
    # Handle scalar
    if len(shape) == 0:
        shape = [1]
    
    try:
        arr = buf_data.view(np_type)
        expected = 1
        for s in shape:
            if s > 0:
                expected *= s
        if arr.size >= expected:
            return arr[:expected].reshape(shape).copy()
        else:
            return arr.reshape(shape).copy()
    except Exception:
        return None


def tflite_to_onnx(tflite_path: str, onnx_path: str) -> bool:
    """Convert a TFLite model to ONNX format."""
    if not HAS_ONNX:
        print("onnx package not installed")
        return False
    
    with open(tflite_path, 'rb') as f:
        buf = f.read()
    
    model = tflite.Model.GetRootAs(buf, 0)
    sg = model.Subgraphs(0)
    
    name = sg.Name().decode() if sg.Name() else 'model'
    print(f'Converting: {name}')
    
    # Extract input/output shapes
    input_idx = sg.Inputs(0)
    output_idx = sg.Outputs(0)
    input_t = sg.Tensors(input_idx)
    output_t = sg.Tensors(output_idx)
    
    input_shape = [input_t.Shape(j) for j in range(input_t.ShapeLength())]
    output_shape = [output_t.Shape(j) for j in range(output_t.ShapeLength())]
    
    print(f'  Input:  tensor {input_idx} shape={input_shape}')
    print(f'  Output: tensor {output_idx} shape={output_shape}')
    
    # Extract all weights
    weights = {}
    for t_idx in range(sg.TensorsLength()):
        t = sg.Tensors(t_idx)
        tname = t.Name().decode() if t.Name() else f'tensor_{t_idx}'
        arr = extract_tensor(model, sg, t_idx)
        if arr is not None:
            weights[t_idx] = (tname, arr)
    
    # Map op codes
    opcodes = []
    for i in range(model.OperatorCodesLength()):
        oc = model.OperatorCodes(i)
        builtin = oc.BuiltinCode()
        opcodes.append(builtin)
    
    # Build ONNX initializers and nodes
    initializers = []
    inputs = []
    outputs_list = []
    nodes = []
    value_info = []
    
    # Input tensor
    input_name = f'input_{input_idx}'
    inputs.append(helper.make_tensor_value_info(input_name, TensorProto.FLOAT, input_shape))
    
    # Create initializers for all weight tensors
    tensor_names = {}
    for t_idx, (tname, arr) in weights.items():
        safe_name = tname.replace('/', '_').replace('.', '_').replace('-', '_')
        if t_idx == input_idx:
            tensor_names[t_idx] = input_name
            continue
        if arr.dtype in (np.float32, np.float16, np.float64):
            init = numpy_helper.from_array(arr, name=safe_name)
            initializers.append(init)
            tensor_names[t_idx] = safe_name
        elif arr.dtype in (np.int32, np.int64):
            init = numpy_helper.from_array(arr.astype(np.int64), name=safe_name)
            initializers.append(init)
            tensor_names[t_idx] = safe_name
        else:
            tensor_names[t_idx] = safe_name
    
    # Build nodes from ops
    for op_i in range(sg.OperatorsLength()):
        op = sg.Operators(op_i)
        oc_idx = op.OpcodeIndex()
        op_name = get_op_name(oc_idx, model)
        
        inputs_list = [op.Inputs(j) for j in range(op.InputsLength()) if op.Inputs(j) >= 0]
        outputs_list_op = [op.Outputs(j) for j in range(op.OutputsLength()) if op.Outputs(j) >= 0]
        
        input_names = [tensor_names.get(i, f't{i}') for i in inputs_list]
        output_names = []
        for o in outputs_list_op:
            t = sg.Tensors(o)
            oname = t.Name().decode() if t.Name() else f't{o}'
            safe_oname = oname.replace('/', '_').replace('.', '_').replace('-', '_')
            tensor_names[o] = safe_oname
            output_names.append(safe_oname)
        
        # Build node
        attrs = {}
        if hasattr(op, 'CustomOptions') and op.CustomOptionsLength() > 0:
            attrs['custom'] = op.CustomOptionsAsNumpy().tobytes()
        
        # Get builtin options
        builtin_options = None
        if op.BuiltinOptions() is not None:
            bo = op.BuiltinOptions()
            builtin_options = bo
        
        # Map to ONNX op
        onnx_op = None
        if op_name == 'CONV_2D':
            onnx_op = 'Conv'
        elif op_name == 'DEPTHWISE_CONV_2D':
            onnx_op = 'Conv'
            attrs['group'] = -1  # Will fix per-op
        elif op_name in ('ADD',):
            onnx_op = 'Add'
        elif op_name in ('MUL',):
            onnx_op = 'Mul'
        elif op_name in ('SUB',):
            onnx_op = 'Sub'
        elif op_name in ('DIV',):
            onnx_op = 'Div'
        elif op_name in ('RELU',):
            onnx_op = 'Relu'
        elif op_name in ('RELU6',):
            onnx_op = 'Relu'
        elif op_name in ('LOGISTIC', 'SIGMOID', 'HARD_SIGMOID'):
            onnx_op = 'Sigmoid'
        elif op_name in ('HARD_SWISH',):
            onnx_op = 'HardSwish'
        elif op_name == 'SOFTMAX':
            onnx_op = 'Softmax'
        elif op_name == 'RESHAPE':
            onnx_op = 'Reshape'
        elif op_name == 'TRANSPOSE':
            onnx_op = 'Transpose'
        elif op_name == 'CONCATENATION':
            onnx_op = 'Concat'
        elif op_name == 'AVERAGE_POOL_2D':
            onnx_op = 'AveragePool'
        elif op_name == 'MAX_POOL_2D':
            onnx_op = 'MaxPool'
        elif op_name == 'PRELU':
            onnx_op = 'PRelu'
        elif op_name == 'PAD':
            onnx_op = 'Pad'
        elif op_name == 'MEAN':
            onnx_op = 'ReduceMean'
        elif op_name == 'STRIDED_SLICE':
            onnx_op = 'Slice'
        elif op_name == 'RESIZE_BILINEAR':
            onnx_op = 'Resize'
        elif op_name == 'RESIZE_NEAREST_NEIGHBOR':
            onnx_op = 'Resize'
        elif op_name == 'FULLY_CONNECTED':
            onnx_op = 'MatMul'
        elif op_name == 'PACK':
            onnx_op = 'Concat'
        elif op_name == 'GATHER':
            onnx_op = 'Gather'
        elif op_name == 'SQUEEZE':
            onnx_op = 'Squeeze'
        elif op_name == 'EXPAND_DIMS':
            onnx_op = 'Unsqueeze'
        elif op_name == 'FLOOR_DIV':
            onnx_op = 'Div'
        else:
            onnx_op = 'Identity'
        
        if onnx_op is None:
            continue
        
        # Skip identity nodes for simplicity
        if onnx_op == 'Identity' and len(input_names) > 0 and len(output_names) > 0:
            tensor_names[outputs_list_op[0]] = input_names[0]
            continue
        
        node = helper.make_node(onnx_op, input_names, output_names, name=f'op_{op_i}_{op_name}', **attrs)
        nodes.append(node)
    
    # Output
    output_oname = tensor_names.get(output_idx, f'output_{output_idx}')
    outputs_list.append(helper.make_tensor_value_info(output_oname, TensorProto.FLOAT, output_shape))
    
    graph = helper.make_graph(nodes, name, inputs, outputs_list, initializer=initializers)
    onnx_model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
    onnx_model.ir_version = 7
    
    onnx.checker.check_model(onnx_model)
    onnx.save(onnx_model, onnx_path)
    print(f'  Saved: {onnx_path} ({os.path.getsize(onnx_path) / 1024 / 1024:.2f} MB)')
    return True


# Convert all models
os.makedirs('models/cyrillic_ocr_onnx', exist_ok=True)

models = [
    ('models/cyrillic_ocr/cyrillic_detector.tflite', 'models/cyrillic_ocr_onnx/detector.onnx'),
    ('models/cyrillic_ocr/cyrillic_recognizer_v3.tflite', 'models/cyrillic_ocr_onnx/recognizer_v3.onnx'),
    ('models/cyrillic_ocr/cyrillic_recognizer_v5.tflite', 'models/cyrillic_ocr_onnx/recognizer_v5.onnx'),
]

for tflite_path, onnx_path in models:
    try:
        ok = tflite_to_onnx(tflite_path, onnx_path)
        if not ok:
            print(f'  FAILED: {tflite_path}')
    except Exception as e:
        print(f'  ERROR: {tflite_path}: {e}')
        import traceback
        traceback.print_exc()
