# SPDX-License-Identifier: Apache-2.0
# Copyright © 2023 Arm Technology (China) Co. Ltd.

from AIPUBuilder.Optimizer.utils import *
from AIPUBuilder.Optimizer.framework import *

import torch

# y = arcsin x， x∈[–1，1]， y∈[–π/2，π/2]


@quant_register(OpType.Asin)
def asin_quantize(self, *args):
    q_mode_activation = self.attrs["q_mode_activation"]
    if QuantMode.is_per_channel(q_mode_activation) == True:
        OPT_FATAL("Currently not support per-channel quantization")
    q_bits_activation = self.attrs["q_bits_activation"]

    inp = self.inputs[0]
    out = self.outputs[0]
    if inp.extrema_min < -1 or inp.extrema_max > 1:
        OPT_WARN("input of Asin(layer_id=%s) must be range[-1,1], otherwise the output is nan, please check!"
                 % (self.attrs['layer_id']))
    out.qbits = q_bits_activation
    out_sign = True
    dev = inp.betensor.device
    out.scale, out.zerop, out.qmin, out.qmax, out.dtype = get_linear_quant_params_from_tensor(
        out, q_mode_activation, out.qbits, out_sign)
    lsteps = 2 ** min(inp.qbits, int(self.get_attrs('lut_items_in_bits')))
    lut = linear_dequantize(torch.linspace(inp.qmin, inp.qmax, steps=lsteps, device=dev), inp.scale, inp.zerop)
    lut = torch.asin(lut)
    lut = linear_quantize_clip(lut, out.scale, out.zerop, out.qmin, out.qmax)
    self.constants["lut"] = PyTensor(self.name+"/asin_lut", lut.cpu().numpy().astype(dtype2nptype(out.dtype)))
    out.qinvariant = False


@op_register(OpType.Asin)
def asin(self, *args):
    inp = self.inputs[0]
    out = self.outputs[0]
    if self.quantized:
        x = inp.betensor
        x = x - inp.qmin
        lut = self.constants["lut"].betensor
        x = torch.reshape(x, (-1,))
        y = lookup_lut_powerof2(x, lut, inp.qbits, False, dtype2bits(
            self.constants["lut"].dtype), is_signed(self.constants["lut"].dtype))
        out.betensor = torch.reshape(y, inp.betensor.shape)
    else:
        out.betensor = torch.asin(inp.betensor)
        if torch.any(torch.isnan(out.betensor)):
            out.betensor = torch.where(torch.isnan(out.betensor), torch.zeros_like(inp.betensor), out.betensor)
            OPT_WARN('layer_id=%s, type=%s, the output has nan, please confirm whether input is range[-1,1], now set nan to zero'
                     % (self.attrs['layer_id'], str(self.type)))

    return out.betensor
