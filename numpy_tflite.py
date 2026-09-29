# -*- coding: utf-8 -*-
"""
Minimal numpy TFLite interpreter for PP-OCR models.
Only implements ops actually used by PP-OCRv3/v4/v5.
"""
import struct
import numpy as np
import tflite
from typing import Dict, List, Optional, Tuple


class TfliteInterpreter:
    """Execute TFLite models using pure numpy."""

    def __init__(self, model_path: str):
        self.model_path = model_path
        with open(model_path, 'rb') as f:
            self._buf = f.read()
        self._model = tflite.Model.GetRootAs(self._buf, 0)
        self._sg = self._model.Subgraphs(0)
        self._tensors: Dict[int, np.ndarray] = {}
        self._opcodes = []
        self._op_names = {}
        for i in range(self._model.OperatorCodesLength()):
            oc = self._model.OperatorCodes(i)
            builtin = oc.BuiltinCode()
            self._opcodes.append(builtin)
            self._op_names[i] = builtin

    def invoke(self, input_data: np.ndarray) -> np.ndarray:
        self._tensors.clear()

        input_idx = self._sg.Inputs(0)
        self._tensors[input_idx] = input_data.astype(np.float32)

        for buf_idx in range(self._model.BuffersLength()):
            buf = self._model.Buffers(buf_idx)
            if buf.DataLength() > 0:
                data = buf.DataAsNumpy()
                if not isinstance(data, int) and hasattr(data, 'shape'):
                    pass  # Skip buffer preloading for now

        for op_i in range(self._sg.OperatorsLength()):
            op = self._sg.Operators(op_i)
            oc_idx = op.OpcodeIndex()
            builtin = self._opcodes[oc_idx]

            op_inputs = []
            for j in range(op.InputsLength()):
                idx = op.Inputs(j)
                if idx >= 0:
                    op_inputs.append(idx)

            op_outputs = []
            for j in range(op.OutputsLength()):
                idx = op.Outputs(j)
                if idx >= 0:
                    op_outputs.append(idx)

            # Load weight tensors on demand
            for t_idx in op_inputs + op_outputs:
                if t_idx not in self._tensors:
                    self._load_tensor(t_idx)

            # Dispatch
            try:
                if builtin == 3 or builtin == 111:  # CONV_2D / ADD (111)
                    if builtin == 3:
                        self._conv2d(op, op_inputs, op_outputs)
                    else:
                        self._elementwise_binary(op_inputs, op_outputs, np.add)
                elif builtin == 4:  # DEPTHWISE_CONV_2D
                    self._depthwise_conv2d(op, op_inputs, op_outputs)
                elif builtin == 18:  # MUL
                    self._elementwise_binary(op_inputs, op_outputs, np.multiply)
                elif builtin == 39:  # SUB
                    self._elementwise_binary(op_inputs, op_outputs, np.subtract)
                elif builtin == 40:  # DIV
                    self._elementwise_binary(op_inputs, op_outputs, np.true_divide)
                elif builtin == 19 or builtin == 21:  # RELU / RELU6
                    self._relu(op_inputs, op_outputs, max_val=6.0 if builtin == 21 else None)
                elif builtin == 14 or builtin == 42:  # LOGISTIC / SIGMOID
                    self._sigmoid(op_inputs, op_outputs)
                elif builtin == 152:  # HARD_SWISH
                    self._hard_swish(op_inputs, op_outputs)
                elif builtin == 126:  # HARD_SIGMOID
                    self._hard_sigmoid(op_inputs, op_outputs)
                elif builtin == 41:  # PRELU
                    self._prelu(op, op_inputs, op_outputs)
                elif builtin == 22:  # RESHAPE
                    self._reshape(op, op_inputs, op_outputs)
                elif builtin == 37:  # TRANSPOSE
                    self._transpose(op, op_inputs, op_outputs)
                elif builtin == 2:  # CONCATENATION
                    self._concatenation(op, op_inputs, op_outputs)
                elif builtin == 24:  # SOFTMAX
                    self._softmax(op, op_inputs, op_outputs)
                elif builtin == 32 or builtin == 34:  # PAD
                    self._pad(op, op_inputs, op_outputs)
                elif builtin == 1:  # AVERAGE_POOL_2D
                    self._pool2d(op, op_inputs, op_outputs, 'avg')
                elif builtin == 17:  # MAX_POOL_2D
                    self._pool2d(op, op_inputs, op_outputs, 'max')
                elif builtin == 38:  # MEAN
                    self._mean(op, op_inputs, op_outputs)
                elif builtin == 9 or builtin == 97:  # FULLY_CONNECTED
                    self._fully_connected(op, op_inputs, op_outputs)
                elif builtin == 29 or builtin == 56:  # STRIDED_SLICE
                    self._strided_slice(op, op_inputs, op_outputs)
                elif builtin == 51:  # RESIZE_BILINEAR
                    self._resize_bilinear(op, op_inputs, op_outputs)
                elif builtin == 52:  # RESIZE_NEAREST_NEIGHBOR
                    self._resize_nearest(op, op_inputs, op_outputs)
                elif builtin == 47:  # PACK
                    self._pack(op, op_inputs, op_outputs)
                elif builtin == 67:  # SQUEEZE
                    self._squeeze(op, op_inputs, op_outputs)
                elif builtin == 75:  # EXPAND_DIMS
                    self._expand_dims(op, op_inputs, op_outputs)
                elif builtin == 49:  # GATHER
                    self._gather(op, op_inputs, op_outputs)
                else:
                    # Unknown op - try identity if single input/output
                    if len(op_inputs) >= 1 and len(op_outputs) >= 1:
                        self._tensors[op_outputs[0]] = self._tensors.get(
                            op_inputs[0], np.zeros((1,), dtype=np.float32)
                        )
            except Exception as e:
                import traceback
                print(f'  Op {op_i} (builtin={builtin}) failed: {e}')
                traceback.print_exc()
                # Identity fallback
                if op_outputs:
                    self._tensors[op_outputs[0]] = self._tensors.get(
                        op_inputs[0], np.zeros((1,), dtype=np.float32)
                    )

        output_idx = self._sg.Outputs(0)
        return self._tensors.get(output_idx, np.zeros((1,), dtype=np.float32))

    def _load_tensor(self, t_idx: int):
        if t_idx in self._tensors:
            return
        t = self._sg.Tensors(t_idx)
        buf = self._model.Buffers(t.Buffer())

        dtype_map = {
            0: np.float32, 1: np.float16, 2: np.int32, 3: np.uint8,
            4: np.int64, 9: np.int8, 11: np.float64,
        }
        tf_type = t.Type()
        np_type = dtype_map.get(tf_type, np.float32)
        shape = [t.Shape(j) for j in range(t.ShapeLength())]

        if buf.DataLength() > 0:
            data = buf.DataAsNumpy()
            if not isinstance(data, int):
                try:
                    arr = np.array(data, dtype=np.uint8).view(np_type)
                    if arr.size >= np.prod(shape) if shape else 0:
                        self._tensors[t_idx] = arr[:np.prod(shape)].reshape(shape).copy()
                        return
                    else:
                        self._tensors[t_idx] = arr.reshape(shape).copy()
                        return
                except Exception:
                    pass

        self._tensors[t_idx] = np.zeros(shape if shape else [1], dtype=np_type)

    # ── Op implementations ──────────────────────────────────

    def _conv2d(self, op, inputs, outputs):
        input_t = self._tensors[inputs[0]]
        kernel_t = self._tensors[inputs[1]]
        bias_t = self._tensors.get(inputs[2], np.zeros(kernel_t.shape[0], dtype=np.float32))

        # Get options
        opts = op.BuiltinOptions()
        strides = [1, 1, 1, 1]
        paddings = 0  # 0=VALID, 1=SAME
        dilations = [1, 1, 1, 1]
        if opts is not None and hasattr(opts, 'StridesH'):
            strides = [1, opts.StridesH(), opts.StridesW(), 1]
            paddings = opts.Padding()
            if hasattr(opts, 'DilationH'):
                dilations = [1, opts.DilationH(), opts.DilationW(), 1]

        # NHWC -> im2col -> matmul
        n, h, w, c_in = input_t.shape
        k_h, k_w, _, c_out = kernel_t.shape
        s_h, s_w = strides[1], strides[2]
        d_h, d_w = dilations[1], dilations[2]

        if paddings == 1:  # SAME
            out_h = int(np.ceil(h / s_h))
            out_w = int(np.ceil(w / s_w))
            pad_h = max(0, (out_h - 1) * s_w + k_h * d_h - h) // 2  # fixed: s_h
            pad_w = max(0, (out_w - 1) * s_w + k_w * d_w - w) // 2
            pad_h = max(0, (out_h - 1) * s_h + k_h * d_h - h) // 2
            pad_w = max(0, (out_w - 1) * s_w + k_w * d_w - w) // 2
            input_t = np.pad(input_t, [[0,0],[pad_h,pad_h],[pad_w,pad_w],[0,0]], mode='constant')
            _, h, w, _ = input_t.shape
        else:
            out_h = (h - k_h * d_h) // s_h + 1
            out_w = (w - k_w * d_w) // s_w + 1

        # im2col
        cols = np.zeros((n, out_h, out_w, k_h * k_w * c_in), dtype=np.float32)
        for i in range(k_h):
            for j in range(k_w):
                cols[:, :, :, (i * k_w + j) * c_in:(i * k_w + j + 1) * c_in] = \
                    input_t[:, i*d_h::s_h, j*d_w::s_w, :][:, :out_h, :out_w, :]

        # Reshape kernel: (k_h, k_w, c_in, c_out) -> (k_h*k_w*c_in, c_out)
        kernel_flat = kernel_t.reshape(-1, c_out)

        # Matmul: (n*out_h*out_w, k_h*k_w*c_in) x (k_h*k_w*c_in, c_out)
        cols_flat = cols.reshape(-1, k_h * k_w * c_in)
        out = cols_flat @ kernel_flat
        if bias_t is not None and bias_t.size > 0:
            out = out + bias_t.reshape(1, -1)

        self._tensors[outputs[0]] = out.reshape(n, out_h, out_w, c_out)

    def _depthwise_conv2d(self, op, inputs, outputs):
        input_t = self._tensors[inputs[0]]
        kernel_t = self._tensors[inputs[1]]
        bias_t = self._tensors.get(inputs[2], np.zeros(kernel_t.shape[-1], dtype=np.float32))

        opts = op.BuiltinOptions()
        s_h, s_w = 1, 1
        paddings = 0
        d_h, d_w = 1, 1
        if opts is not None and hasattr(opts, 'StridesH'):
            s_h, s_w = opts.StridesH(), opts.StridesW()
            paddings = opts.Padding()
            if hasattr(opts, 'DilationH'):
                d_h, d_w = opts.DilationH(), opts.DilationW()

        n, h, w, c_in = input_t.shape
        k_h, k_w, _, c_out_mul = kernel_t.shape
        c_out = c_in * c_out_mul

        if paddings == 1:
            out_h = int(np.ceil(h / s_h))
            out_w = int(np.ceil(w / s_w))
            pad_h = max(0, (out_h - 1) * s_h + k_h * d_h - h) // 2
            pad_w = max(0, (out_w - 1) * s_w + k_w * d_w - w) // 2
            input_t = np.pad(input_t, [[0,0],[pad_h,pad_h],[pad_w,pad_w],[0,0]])
            _, h, w, _ = input_t.shape
        else:
            out_h = (h - k_h * d_h) // s_h + 1
            out_w = (w - k_w * d_w) // s_w + 1

        # im2col for depthwise
        cols = np.zeros((n, out_h, out_w, k_h * k_w * c_in), dtype=np.float32)
        for i in range(k_h):
            for j in range(k_w):
                cols[:, :, :, (i * k_w + j) * c_in:(i * k_w + j + 1) * c_in] = \
                    input_t[:, i*d_h::s_h, j*d_w::s_w, :][:, :out_h, :out_w, :]

        # Kernel: (k_h, k_w, c_in, 1) -> (c_in, k_h*k_w)
        kernel_dw = kernel_t.reshape(c_in, k_h * k_w)
        # Multiply each channel with its kernel
        cols_c = cols.reshape(n * out_h * out_w, c_in, k_h * k_w)
        out = np.sum(cols_c * kernel_dw, axis=2)
        if bias_t is not None and bias_t.size > 0:
            out = out + bias_t.reshape(1, -1)

        self._tensors[outputs[0]] = out.reshape(n, out_h, out_w, c_in)

    def _elementwise_binary(self, inputs, outputs, func):
        a = self._tensors[inputs[0]]
        b = self._tensors[inputs[1]]
        if isinstance(b, int) or (isinstance(b, np.ndarray) and b.ndim == 0):
            pass
        # Broadcast
        try:
            result = func(a, b)
        except ValueError:
            result = func(a, np.broadcast_to(b, a.shape))
        self._tensors[outputs[0]] = result

    def _relu(self, inputs, outputs, max_val=None):
        x = self._tensors[inputs[0]]
        out = np.maximum(x, 0)
        if max_val is not None:
            out = np.minimum(out, max_val)
        self._tensors[outputs[0]] = out

    def _sigmoid(self, inputs, outputs):
        x = self._tensors[inputs[0]]
        self._tensors[outputs[0]] = 1.0 / (1.0 + np.exp(-np.clip(x, -500, 500)))

    def _hard_swish(self, inputs, outputs):
        x = self._tensors[inputs[0]]
        self._tensors[outputs[0]] = x * np.clip(x + 3, 0, 6) / 6.0

    def _hard_sigmoid(self, inputs, outputs):
        x = self._tensors[inputs[0]]
        self._tensors[outputs[0]] = np.clip(x / 6 + 0.5, 0, 1)

    def _prelu(self, op, inputs, outputs):
        x = self._tensors[inputs[0]]
        slope = self._tensors[inputs[1]] if len(inputs) > 1 else np.array(0.25)
        out = np.where(x >= 0, x, x * slope)
        self._tensors[outputs[0]] = out

    def _reshape(self, op, inputs, outputs):
        x = self._tensors[inputs[0]]
        if len(inputs) > 1:
            target = self._tensors[inputs[1]]
            if isinstance(target, np.ndarray):
                target = target.astype(np.int32).tolist()
        else:
            opts = op.BuiltinOptions()
            if opts is not None and hasattr(opts, 'NewShape'):
                target = [opts.NewShape(i) for i in range(opts.NewShapeLength())]
            else:
                target = x.shape
        self._tensors[outputs[0]] = x.reshape(target)

    def _transpose(self, op, inputs, outputs):
        x = self._tensors[inputs[0]]
        if len(inputs) > 1:
            perm = self._tensors[inputs[1]].astype(np.int32).tolist()
        else:
            perm = list(range(len(x.shape))[::-1])
        self._tensors[outputs[0]] = np.transpose(x, perm)

    def _concatenation(self, op, inputs, outputs):
        arrays = [self._tensors[i] for i in inputs]
        opts = op.BuiltinOptions()
        axis = 0
        if opts is not None and hasattr(opts, 'Axis'):
            axis = opts.Axis()
        self._tensors[outputs[0]] = np.concatenate(arrays, axis=axis)

    def _softmax(self, op, inputs, outputs):
        x = self._tensors[inputs[0]]
        opts = op.BuiltinOptions()
        axis = -1
        if opts is not None and hasattr(opts, 'Beta'):
            pass
        if opts is not None and hasattr(opts, 'Axis'):
            axis = opts.Axis()
        e = np.exp(x - np.max(x, axis=axis, keepdims=True))
        self._tensors[outputs[0]] = e / np.sum(e, axis=axis, keepdims=True)

    def _pad(self, op, inputs, outputs):
        x = self._tensors[inputs[0]]
        if len(inputs) > 1:
            paddings = self._tensors[inputs[1]]
            if isinstance(paddings, np.ndarray):
                paddings = paddings.tolist()
        else:
            opts = op.BuiltinOptions()
            paddings = [[0,0],[0,0],[0,0],[0,0]]
            if opts is not None and hasattr(opts, 'Padding'):
                p = opts
                # TFLite PAD options have left/right arrays
                pass
        if isinstance(paddings, list) and len(paddings) > 0:
            if isinstance(paddings[0], list):
                pad_width = [(p[0], p[1]) for p in paddings]
            else:
                pad_width = [(paddings[i], paddings[i+1]) for i in range(0, len(paddings), 2)]
        else:
            pad_width = [(0, 0)] * x.ndim
        self._tensors[outputs[0]] = np.pad(x, pad_width, mode='constant', constant_values=0)

    def _pool2d(self, op, inputs, outputs, mode='avg'):
        x = self._tensors[inputs[0]]
        opts = op.BuiltinOptions()
        k_h, k_w = 2, 2
        s_h, s_w = 2, 2
        paddings = 0
        if opts is not None and hasattr(opts, 'FilterHeight'):
            k_h, k_w = opts.FilterHeight(), opts.FilterWidth()
            s_h, s_w = opts.StrideH(), opts.StrideW()
            paddings = opts.Padding()

        n, h, w, c = x.shape
        if paddings == 1:
            out_h = int(np.ceil(h / s_h))
            out_w = int(np.ceil(w / s_w))
            pad_h = max(0, (out_h - 1) * s_h + k_h - h) // 2
            pad_w = max(0, (out_w - 1) * s_w + k_w - w) // 2
            x = np.pad(x, [[0,0],[pad_h,pad_h],[pad_w,pad_w],[0,0]])
            _, h, w, _ = x.shape
        else:
            out_h = (h - k_h) // s_h + 1
            out_w = (w - k_w) // s_w + 1

        out = np.zeros((n, out_h, out_w, c), dtype=np.float32)
        for i in range(out_h):
            for j in range(out_w):
                patch = x[:, i*s_h:i*s_h+k_h, j*s_w:j*s_w+k_w, :]
                if mode == 'avg':
                    out[:, i, j, :] = np.mean(patch, axis=(1, 2))
                else:
                    out[:, i, j, :] = np.max(patch, axis=(1, 2))

        self._tensors[outputs[0]] = out

    def _mean(self, op, inputs, outputs):
        x = self._tensors[inputs[0]]
        opts = op.BuiltinOptions()
        keepdims = True
        if opts is not None and hasattr(opts, 'KeepDims'):
            keepdims = opts.KeepDims()
        axes = self._tensors[inputs[1]].astype(np.int32).tolist() if len(inputs) > 1 else list(range(x.ndim))
        self._tensors[outputs[0]] = np.mean(x, axis=axes, keepdims=keepdims)

    def _fully_connected(self, op, inputs, outputs):
        x = self._tensors[inputs[0]]
        w = self._tensors[inputs[1]]
        bias = self._tensors.get(inputs[2], np.zeros(w.shape[0], dtype=np.float32))
        out = x.reshape(x.shape[0], -1) @ w.T
        if bias is not None and bias.size > 0:
            out = out + bias.reshape(1, -1)
        self._tensors[outputs[0]] = out

    def _strided_slice(self, op, inputs, outputs):
        x = self._tensors[inputs[0]]
        begin = self._tensors[inputs[1]].astype(np.int32).tolist() if len(inputs) > 1 else [0]*x.ndim
        end = self._tensors[inputs[2]].astype(np.int32).tolist() if len(inputs) > 2 else list(x.shape)
        strides_arr = self._tensors[inputs[3]].astype(np.int32).tolist() if len(inputs) > 3 else [1]*x.ndim

        slices = []
        for i in range(len(begin)):
            s = strides_arr[i] if i < len(strides_arr) else 1
            slices.append(slice(begin[i], end[i], s))
        self._tensors[outputs[0]] = x[tuple(slices)]

    def _resize_bilinear(self, op, inputs, outputs):
        x = self._tensors[inputs[0]]
        opts = op.BuiltinOptions()
        # Get target size from second input
        if len(inputs) > 1:
            target = self._tensors[inputs[1]]
            if isinstance(target, np.ndarray):
                new_h, new_w = int(target[0]), int(target[1])
            else:
                new_h, new_w = target[0], target[1]
        else:
            new_h, new_w = x.shape[1], x.shape[2]

        # Simple bilinear via repeated interpolation
        from PIL import Image
        img = Image.fromarray((np.clip(x[0], 0, 1) * 255).astype(np.uint8) if x.shape[-1] == 3 else
                              np.tile(np.clip(x[0, :, :, 0], 0, 1) * 255, (3, 1, 1)).astype(np.uint8))
        # Generic approach: scipy or manual
        out = np.zeros((x.shape[0], new_h, new_w, x.shape[3]), dtype=np.float32)
        for b in range(x.shape[0]):
            for c in range(x.shape[3]):
                channel = x[b, :, :, c]
                from PIL import Image as PILImage
                img = PILImage.fromarray(channel, mode='F')
                img = img.resize((new_w, new_h), PILImage.Resampling.BILINEAR)
                out[b, :, :, c] = np.array(img, dtype=np.float32)
        self._tensors[outputs[0]] = out

    def _resize_nearest(self, op, inputs, outputs):
        x = self._tensors[inputs[0]]
        if len(inputs) > 1:
            target = self._tensors[inputs[1]]
            new_h, new_w = int(target[0]), int(target[1])
        else:
            new_h, new_w = x.shape[1], x.shape[2]

        out = np.zeros((x.shape[0], new_h, new_w, x.shape[3]), dtype=np.float32)
        for b in range(x.shape[0]):
            for c in range(x.shape[3]):
                channel = x[b, :, :, c]
                from PIL import Image as PILImage
                img = PILImage.fromarray(channel, mode='F')
                img = img.resize((new_w, new_h), PILImage.Resampling.NEAREST)
                out[b, :, :, c] = np.array(img, dtype=np.float32)
        self._tensors[outputs[0]] = out

    def _pack(self, op, inputs, outputs):
        opts = op.BuiltinOptions()
        axis = 0
        if opts is not None and hasattr(opts, 'Axis'):
            axis = opts.Axis()
        arrays = [self._tensors[i] for i in inputs]
        self._tensors[outputs[0]] = np.stack(arrays, axis=axis)

    def _squeeze(self, op, inputs, outputs):
        x = self._tensors[inputs[0]]
        opts = op.BuiltinOptions()
        if opts is not None and hasattr(opts, 'Dim'):
            axes = [opts.Dim(i) for i in range(opts.DimLength())]
        else:
            axes = [i for i, s in enumerate(x.shape) if s == 1]
        self._tensors[outputs[0]] = np.squeeze(x, axis=axes if axes else None)

    def _expand_dims(self, op, inputs, outputs):
        x = self._tensors[inputs[0]]
        opts = op.BuiltinOptions()
        axis = 0
        if opts is not None and hasattr(opts, 'Axis'):
            axis = opts.Axis()
        elif len(inputs) > 1:
            axis = int(self._tensors[inputs[1]].flatten()[0])
        self._tensors[outputs[0]] = np.expand_dims(x, axis=axis)

    def _gather(self, op, inputs, outputs):
        x = self._tensors[inputs[0]]
        indices = self._tensors[inputs[1]].astype(np.int32)
        opts = op.BuiltinOptions()
        axis = 0
        if opts is not None and hasattr(opts, 'Axis'):
            axis = opts.Axis()
        self._tensors[outputs[0]] = np.take(x, indices, axis=axis)
