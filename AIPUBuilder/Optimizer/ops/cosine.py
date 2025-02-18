# SPDX-License-Identifier: Apache-2.0
# Copyright © 2023 Arm Technology (China) Co. Ltd.

from AIPUBuilder.Optimizer.utils import *
from AIPUBuilder.Optimizer.framework import *

import torch

'''
New 16-bit trigonometric quantization method:
1. Create a sine/cosine value lut to the range of [0,2pi].
    lut size can be adjusted. 10 or more bits are recommended.
2. Expand output range to extrema value of activation bits.
3. Calculate index of lut according to input.
    a. origin index: idx = (inp_f % 2pi) / 2pi * lut_size
    b. a = b % n, then ka = kb % kn for integer k
        let m = lut_size / 2pi, then we have
        idx = (m*inp_f) % (m*2pi) / (m*2pi) * lut_size
        so idx = (m*inp_f) % lut_size
        Considering quantization scale
        idx = (inp_q * lut_size/(2pi*scale)) % lut_size
    c. let lut_size / (2pi * scale) be the do_scale and so_shift
        Since lut_size is always a power of 2, we can use bitwise_and
        to calculate modularity. For example, n % 1024 = n & 0b11_1111_1111
    d. pruning the idx values to [0,2pi]
4. Read value from lut. Negative index in range [-2pi, 0] is allowed.
'''


def trigonometric_quantize(node, func):
    q_mode_activation = node.attrs["q_mode_activation"]
    if QuantMode.is_per_channel(q_mode_activation) == True:
        OPT_FATAL("Currently not support per-channel quantization")
    q_bits_activation = int(node.attrs["q_bits_activation"])

    inp = node.inputs[0]
    out = node.outputs[0]
    lsteps = 2 ** min(int(node.get_attrs('lut_items_in_bits')), q_bits_activation)

    if q_bits_activation <= 8:
        out.qbits = q_bits_activation
        out.scale, out.zerop, out.qmin, out.qmax, out.dtype = get_linear_quant_params_from_tensor(
            out, q_mode_activation, out.qbits, True)
        lut = linear_dequantize(torch.linspace(inp.qmin, inp.qmax, steps=lsteps, device=inp.betensor.device),
                                inp.scale, inp.zerop)
        lut = func(lut)
        lut = linear_quantize_clip(lut, out.scale, out.zerop, out.qmin, out.qmax)
        node.constants["lut"] = PyTensor(node.name+"/cos_lut", lut.cpu().numpy().astype(dtype2nptype(out.dtype)))
        out.qinvariant = False
    else:
        _, qmax = bits2range(q_bits_activation, True)
        out.scale = qmax
        out.zerop = 0
        out.qmin = -qmax
        out.qmax = qmax
        out.qbits = q_bits_activation
        out.dtype = bits2dtype(q_bits_activation, True)
        out.qinvariant = False

        lut = torch.linspace(0, 2*torch.pi, steps=lsteps)
        lut = func(lut)
        lut = linear_quantize_clip(lut, out.scale, out.zerop, out.qmin, out.qmax).long()
        node.constants["lut"] = PyTensor(node.name+"/lut", lut.cpu().numpy().astype(dtype2nptype(out.dtype)))

        local_scale = (lsteps / 2 / torch.pi) / inp.scale
        do_scale, do_scale_type, do_shift, do_shift_type = get_scale_approximation_params(
            local_scale, mult_bits=q_bits_activation)
        node.params["shift_value"] = int(do_shift)
        node.params["shift_type"] = do_shift_type
        node.params["scale_value"] = int(do_scale)
        node.params["scale_type"] = do_scale_type


@quant_register(OpType.Cosine)
def cosine_quantize(self, *args):
    trigonometric_quantize(self, torch.cos)


def trigonometric_forward(node, inp, lut):
    scale0 = node.params["scale_value"]
    shift0 = node.params["shift_value"]
    lut_size = lut.numel()

    mask = torch.tensor(lut_size - 1, dtype=torch.int32, device=inp.device)
    index = linear_requantize(inp, scale0, shift0, 0, -2147483648, 2147483647).to(torch.int32)
    index = index & mask

    index_large_mask = index >= lut_size
    index_less_mask = index < -lut_size
    index[index_large_mask] = index[index_large_mask] - lut_size
    index[index_less_mask] = index[index_less_mask] + lut_size

    out = lut[index.long()]
    return out


@op_register(OpType.Cosine)
def cosine(self, *args):
    inp = self.inputs[0]
    out = self.outputs[0]
    if self.quantized:
        q_bits_activation = inp.qbits
        if q_bits_activation > 8:
            x = inp.betensor.long() + inp.zerop
            lut = self.constants["lut"].betensor
            out.betensor = trigonometric_forward(self, x, lut)
        else:
            x = inp.betensor
            x = x - inp.qmin
            lut = self.constants["lut"].betensor
            x = torch.reshape(x, (-1,))
            y = lookup_lut_powerof2(x, lut, inp.qbits, False, dtype2bits(
                self.constants["lut"].dtype), is_signed(self.constants["lut"].dtype))
            out.betensor = torch.reshape(y, inp.betensor.shape)
    else:
        out.betensor = torch.cos(inp.betensor)
    return out.betensor
