# SPDX-License-Identifier: Apache-2.0
# Copyright © 2023 Arm Technology (China) Co. Ltd.

#!/usr/bin/python
# -*- coding: UTF-8 -*-
# cython: language_level=3


def cast_to_NodeParamValue(v):
    from AIPUBuilder.Optimizer.logger import OPT_WARN
    try:
        from AIPUBuilder._C._core import NodeParamValue
        from AIPUBuilder._C._core import Dtype as _cDtype
    except Exception as e:
        OPT_WARN(
            f"when calling cast_to_NodeParamValue(c data struct), please install AIPUBuilder package. now error message: {e}", log_once=True)
    from AIPUBuilder.Optimizer.framework.pycore.pytype import Dtype as _pyDtype
    import torch
    import numpy as np

    dt_dict = {str(t[0].name): t[0] for t in _cDtype.__entries.values()}

    if isinstance(v, _pyDtype):
        v = dt_dict[v.name]
    elif isinstance(v, bool):
        v = v
    elif isinstance(v, (int, float, str, list)):
        v = NodeParamValue(v)
    elif isinstance(v, torch.Tensor) and v.dim() < 1:
        v = NodeParamValue(v.item())
    elif isinstance(v, np.ndarray) and v.ndim < 1:
        v = NodeParamValue(v.item())
    else:
        try:
            v = NodeParamValue(v)
        except Exception as e:
            v = str(v)
    return v


def cast_from_NodeParamValue_string(v):
    from AIPUBuilder.Optimizer.utils.dtype_utils import str2dtype, dtype2str
    from AIPUBuilder.Optimizer.framework.pycore.pytype import Dtype
    import re

    def is_valid_list_string(sv):
        if (len(sv) > 1) and (sv[0] == '[') and (sv[-1] == ']'):
            stk = []
            for i in range(len(sv)):
                c = sv[i]
                if '[' == c:
                    stk.append(c)
                elif ']' == c:
                    if len(stk) < 1:
                        return False
                    else:
                        stk.pop()
            if len(stk) < 1:
                return True
            else:
                return False
        else:
            return False

    dt_s = ''
    for dt in Dtype:
        str_dt = dtype2str(dt)
        dt_s += '|'.join([str_dt, f"dtype.{str_dt}|"])
    sv = v.strip()
    if is_valid_list_string(sv):
        # list
        lt = []
        ts = sv[1:-1] + ','
        cntl = 0
        cntr = 0
        pos = 0
        for i in range(len(ts)):
            c = ts[i]
            if '[' == c:
                cntl += 1
            elif ']' == c:
                cntr += 1
            elif ',' == c:
                if cntl == cntr:
                    sub_str = ts[pos:i].strip(' \'')
                    if len(sub_str) > 0:
                        lt.append(cast_from_NodeParamValue_string(sub_str))
                    pos = i+1
        return lt
    elif re.match('^('+dt_s[:-1]+')$', sv.lower()):
        return str2dtype(sv)
    elif re.match(r'^(\-|\+)?\d+$', sv.lower()):
        return int(sv)
    elif re.match(r'^(((\-|\+)?\d+((\.\d+)|\.)?(e(\-|\+)?\d+)?)|((\-|\+)?inf))$', sv.lower()):
        return float(sv)
    elif re.match(r'^true$', sv.lower()):
        return True
    elif re.match(r'^false$', sv.lower()):
        return False
    else:
        # str
        return str(sv)


def cast_to_NodeParamValue_string(v):
    from AIPUBuilder.Optimizer.framework.pycore.pytype import Dtype
    from AIPUBuilder.Optimizer.utils.dtype_utils import dtype2str
    import torch
    import numpy as np
    if isinstance(v, Dtype):
        return dtype2str(v)
    elif isinstance(v, bool):
        return str(v).lower()
    elif isinstance(v, int):
        return str(v)
    elif isinstance(v, float):
        return str(v)
    elif isinstance(v, str):
        return v
    elif isinstance(v, torch.Tensor) and v.dim() < 1:
        return str(v.item())
    elif isinstance(v, np.ndarray) and v.ndim < 1:
        return str(v.item())
    else:
        # list
        sv = '['
        cnt = 0
        for e in v:
            sv += cast_to_NodeParamValue_string(e) + ','
            cnt += 1
        if cnt > 0:
            return sv[:-1] + ']'
        else:
            return sv + ']'


def convert_aipu_graph_to_opt_graph(cg):
    from AIPUBuilder.Optimizer.framework.qgraph import QuantizeGraph
    from AIPUBuilder.Optimizer.framework.pycore.pynode import PyNode
    from AIPUBuilder.Optimizer.framework.pycore.pytensor import PyTensor
    from AIPUBuilder.Optimizer.framework.pycore.pytype import Dtype, OpTypeValue
    import torch

    g = QuantizeGraph()
    g.name = cg.name
    dt_dict = {dt.name: dt for dt in Dtype}
    nmap = {}
    emap = {}
    for n in cg.nodes:
        pn = PyNode(n.name, OpTypeValue(str(n.type)))
        pn.attrs['layer_id'] = str(n.attrs["layer_id"])
        for k, v in n.params.items():
            pn.params[k] = cast_from_NodeParamValue_string(str(v))
        for k, v in n.constants.items():
            pv = PyTensor(v.name, v.data())
            pv.dtype = dt_dict[v._dtype().__to_str__().upper()]
            pv.ir_dtype = pv.dtype
            pv.ir_shape = tuple(v.shape)
            pv.scale = torch.tensor(v.quantization.scales, device=pv.betensor.device)
            pv.zerop = torch.tensor(v.quantization.offsets, device=pv.betensor.device)
            pn.constants[k] = pv
        g.nodes.append(pn)
        pn.graph = g
        nmap[pn.name] = pn
        # store edges
        layer_top_range = []
        for v in n.outputs:
            pv = PyTensor(v.name, v.data())
            pv.dtype = dt_dict[v._dtype().__to_str__().upper()]
            pv.ir_dtype = pv.dtype
            pv.ir_shape = tuple(v.shape)
            pv.scale = torch.tensor(v.quantization.scales, device=pv.betensor.device)
            pv.zerop = torch.tensor(v.quantization.offsets, device=pv.betensor.device)
            if 'range' in v._attrs:
                # layer_top_range
                layer_top_range.append(v._attrs['range'])
            emap[pv.name] = pv
        if len(layer_top_range) > 0:
            pn.params['layer_top_range'] = layer_top_range
    # connect edges
    for i, n in enumerate(cg.nodes):
        pn = g.nodes[i]
        tlist = []
        for v in n.inputs:
            tlist.append(emap[v.name])
        pn.inputs = tuple(tlist)
        nlist = []
        for x in n.parents:
            nlist.append(nmap[x.name])
        pn.parents = tuple(nlist)
        tlist = []
        for v in n.outputs:
            tlist.append(emap[v.name])
        pn.outputs = tuple(tlist)
        nlist = []
        for x in n.children:
            nlist.append(nmap[x.name])
        pn.children = tuple(nlist)

        for ak, av in n.attrs.items():
            pn.attrs[ak] = cast_from_NodeParamValue_string(str(av))
    ilist = []
    for t in cg.input_tensors:
        ilist.append(emap[t.name])
    g.input_tensors = tuple(ilist)
    olist = []
    for t in cg.output_tensors:
        olist.append(emap[t.name])
    g.output_tensors = tuple(olist)
    return g


def convert_opt_graph_to_aipu_graph(g):
    from AIPUBuilder._C._core import Graph as _Graph
    from AIPUBuilder._C._core import Tensor as _Tensor
    from AIPUBuilder._C._core import TensorList as _TensorList
    from AIPUBuilder._C._core import TensorShape as _TensorShape
    from AIPUBuilder._C._core import Node as _Node
    from AIPUBuilder._C._core import OpType as _OpType
    from AIPUBuilder._C._core import Dtype as _Dtype
    from AIPUBuilder._C._core import _py_register_optype
    from AIPUBuilder.Optimizer.utils.dtype_utils import dtype2nptype

    def _convert_scale_zp_to_list(data):
        import torch
        import numpy as np
        ret_data = None
        if isinstance(data, (float, int)):
            ret_data = [data]
        elif isinstance(data, np.ndarray):
            ret_data = [data.tolist()] if data.ndim == 0 else data.tolist()
        elif isinstance(data, torch.Tensor):
            data = data.flatten()
            ret_data = [data.tolist()] if data.dim() == 0 else data.tolist()
        else:
            ret_data = data
        return ret_data

    def _register_optype(t: str):
        if t not in _OpType.__entries:
            v = _OpType(_py_register_optype(t))
            setattr(_OpType, t, _OpType(v))
            _OpType.__entries[t] = (v, None)  # (value, doc string)
        else:
            return _OpType.__entries[t][0]
        return v
    ot_dict = {str(t[0]): t[0] for t in _OpType.__entries.values()}
    dt_dict = {t[0].__to_str__().upper(): t[0] for t in _Dtype.__entries.values()}
    cg = _Graph(g.name)
    # cg.name = g.name
    # store edges
    emap = {}
    for n in g.nodes:
        for v in n.outputs:
            ct = _Tensor(v.name, _TensorShape(list(v.ir_shape)), dt_dict[v.dtype.name])
            # ct._set_dtype(dt_dict[v.dtype.name])
            ct.quantization.scales = _convert_scale_zp_to_list(v.scale)
            ct.quantization.offsets = [int(zp) for zp in _convert_scale_zp_to_list(v.zerop)]
            ct.quantization.bits = v.qbits if v.qbits else n.attrs['q_bits_activation'] if 'q_bits_activation' in n.attrs else -1
            ct.quantization.qinvariant = v.qinvariant
            emap[ct.name] = ct
    for n in g.nodes:
        cn = _Node(n.name, ot_dict[str(n.type)] if str(n.type) in ot_dict.keys() else _register_optype(n.type.name))
        for k, v in n.params.items():
            try:
                cn.params[k] = cast_to_NodeParamValue(v)
            except Exception as e:
                cn.params[k] = cast_to_NodeParamValue_string(v)
        for k, v in n.constants.items():
            ct = _Tensor(v.name, v.betensor.cpu().contiguous().numpy().astype(dtype2nptype(v.dtype)))
            ct._set_dtype(dt_dict[v.dtype.name])
            ct.quantization.scales = _convert_scale_zp_to_list(v.scale)
            ct.quantization.offsets = [int(z) for z in _convert_scale_zp_to_list(v.zerop)]
            if v.qbits:
                ct.quantization.bits = v.qbits
            elif 'weights' == k:
                ct.quantization.bits = n.attrs['q_bits_weight'] if 'q_bits_weight' in n.attrs else -1
            elif 'biases' == k:
                ct.quantization.bits = n.attrs['q_bits_bias'] if 'q_bits_bias' in n.attrs else -1
            ct.quantization.qinvariant = v.qinvariant
            cn.constants[k] = ct
        for v in n.inputs:
            cn.add_input(emap[v.name])
        for v in n.outputs:
            cn.add_output(emap[v.name])
        for ak, av in n.attrs.items():
            try:
                cn.attrs[ak] = cast_to_NodeParamValue(av)
            except:
                cn.attrs[ak] = av
        cg.add_node(cn)
    cg.input_tensors = _TensorList([emap[v.name] for v in g.input_tensors])
    cg.output_tensors = _TensorList([emap[v.name] for v in g.output_tensors])
    return cg


def parse_graph_from_ir(ir_txt, ir_bin):
    from AIPUBuilder.Optimizer.framework.qgraph import QuantizeGraph
    from AIPUBuilder.Optimizer.framework.pycore.pynode import PyNode
    from AIPUBuilder.Optimizer.framework.pycore.pytensor import PyTensor, TensorShape
    from AIPUBuilder.Optimizer.framework.pycore.pytype import register_optype, OpType
    from AIPUBuilder.Optimizer.logger import OPT_INFO, OPT_WARN, OPT_DEBUG, tqdm
    from AIPUBuilder.Optimizer.utils.dtype_utils import str2dtype, dtype2nptype
    import mmap
    import os
    import sys
    import re
    import numpy as np
    import torch
    g = QuantizeGraph()
    OPT_INFO('Suggest using "aipuchecker" to validate the IR firstly if you are not sure about its validity.')
    gstr = ''
    with open(ir_txt, 'r') as ftxt:
        gstr += ftxt.read()
    # get sections of key value pairs
    msg = 'Invalid IR, please use "aipuchecker" to diagnose it for more specific information.'
    try:
        sections = []
        sdict = {}
        gstr += '\n\n'
        for line in gstr.splitlines():
            if len(line.strip()) > 0:
                k, v = line.strip().split('=')
                sdict[k.strip()] = v.strip()
            else:
                if len(sdict.keys()) > 0:
                    sections.append(sdict)
                sdict = {}
        if len(sections) > 1:
            abstract = sections[0]
            g.name = abstract['model_name']
            if 'compat_quantized_model' in abstract:
                g.compat_quantized_model = True if abstract['compat_quantized_model'].lower() == 'true' else False
            inp_tensor_names = cast_from_NodeParamValue_string(
                abstract['input_tensors']) if 'input_tensors' in abstract.keys() else []
            inp_tensor_names = [str(s) for s in inp_tensor_names]
            out_tensor_names = cast_from_NodeParamValue_string(
                abstract['output_tensors']) if 'output_tensors' in abstract.keys() else []
            out_tensor_names = [str(s) for s in out_tensor_names]
            emap = {}
            for i in range(1, len(sections)):
                sec = sections[i]
                top_names = cast_from_NodeParamValue_string(sec['layer_top'])
                top_names = [str(s) for s in top_names]
                top_shape = cast_from_NodeParamValue_string(sec['layer_top_shape'])
                top_dtype = cast_from_NodeParamValue_string(sec['layer_top_type'])
                top_scale = cast_from_NodeParamValue_string(
                    sec['layer_top_scale']) if 'layer_top_scale' in sec.keys() else []
                top_zerop = cast_from_NodeParamValue_string(
                    sec['layer_top_zp']) if 'layer_top_zp' in sec.keys() else []
                for j in range(len(top_names)):
                    t = PyTensor(top_names[j])
                    t.ir_shape = TensorShape(top_shape[j])
                    t.ir_dtype = top_dtype[j]
                    t.dtype = t.ir_dtype
                    if len(top_scale) > j:
                        t.scale = torch.tensor(top_scale[j], device=t.betensor.device) if isinstance(
                            top_scale[j], list) else top_scale[j]
                    if len(top_zerop) > j:
                        t.zerop = torch.tensor(top_zerop[j], device=t.betensor.device) if isinstance(
                            top_zerop[j], list) else top_zerop[j]
                    emap[t.name] = t
            OPT_INFO("IR loaded.")
            tensor_list = []
            need_reordering = False
            old_offset = -1
            pbar = tqdm(range(1, len(sections)), desc="Building graph", file=sys.stdout)
            for i in pbar:
                sec = sections[i]
                n = PyNode(sec['layer_name'], register_optype(sec['layer_type']))
                n.attrs['layer_id'] = sec['layer_id']
                bottom_names = cast_from_NodeParamValue_string(sec['layer_bottom'])
                bottom_names = [str(s) for s in bottom_names]
                for j in range(len(bottom_names)):
                    t = emap[bottom_names[j]]
                    n.add_input(t)
                top_names = cast_from_NodeParamValue_string(sec['layer_top'])
                top_names = [str(s) for s in top_names]
                for j in range(len(top_names)):
                    t = emap[top_names[j]]
                    n.add_output(t)
                non_param_keys = ['layer_id', 'layer_name', 'layer_type', 'layer_bottom', 'layer_bottom_shape',
                                  'layer_bottom_type', 'layer_top', 'layer_top_shape', 'layer_top_type', 'layer_top_scale', 'layer_top_zp']
                for key in sec.keys():
                    if re.match(r'.+_offset', key):
                        ckey = key[:-7]
                        ckey_offset = key
                        ckey_type = ckey + '_type'
                        ckey_size = ckey + '_size'
                        ckey_shape = ckey + '_shape'
                        ckey_scale = ckey + '_scale'
                        ckey_zerop = ckey + '_zp'
                        if ckey_type in sec.keys() and ckey_size in sec.keys() and ckey_shape in sec.keys():
                            bytes_offset = int(sec[ckey_offset])
                            if not need_reordering and bytes_offset < old_offset:
                                need_reordering = True
                            old_offset = bytes_offset
                            bytes_size = int(sec[ckey_size])
                            t = PyTensor(f'{n.name}{ckey}')
                            t.ir_shape = TensorShape(cast_from_NodeParamValue_string(sec[ckey_shape]))
                            t.ir_dtype = cast_from_NodeParamValue_string(sec[ckey_type])
                            t.dtype = t.ir_dtype
                            tensor_list.append([bytes_offset, bytes_size, t, dtype2nptype(str2dtype(sec[ckey_type]))])
                            if set((ckey_scale, ckey_zerop)).issubset(set(sec.keys())):
                                t.scale = cast_from_NodeParamValue_string(sec[ckey_scale])
                                t.zerop = cast_from_NodeParamValue_string(sec[ckey_zerop])
                                t.scale = torch.tensor(t.scale, device=t.betensor.device) if isinstance(
                                    t.scale, list) else t.scale
                                t.zerop = torch.tensor(t.zerop, device=t.betensor.device) if isinstance(
                                    t.zerop, list) else t.zerop
                            n.constants[ckey] = t
                            non_param_keys.extend([ckey_offset, ckey_type, ckey_size, ckey_shape])
                for key in sec.keys():
                    if key not in non_param_keys:
                        n.params[key] = cast_from_NodeParamValue_string(sec[key])
                g.nodes.append(n)
            pbar.refresh()
            OPT_INFO("Begin to load weights.")
            f = open(ir_bin, "rb")
            fsize = os.path.getsize(ir_bin)
            if fsize == 0 or len(tensor_list) == 0:
                bstr = b''
            else:
                bstr = mmap.mmap(f.fileno(), fsize, access=mmap.ACCESS_READ)
                page_size = mmap.PAGESIZE
                page_count = fsize // page_size
                cores = torch.multiprocessing.cpu_count()  # Use multicores to preload
                pages_per_core = page_count // cores
                jobs = [[core * pages_per_core, (core+1) * pages_per_core] for core in range(cores)]

                def load_page(ranges):
                    for i in range(ranges[0], ranges[1]):
                        _ = bstr[i * 4096]
                ps = []
                for core in range(cores):
                    p = torch.multiprocessing.Process(target=load_page, args=(jobs[core],))
                    p.start()
                    ps.append(p)
                for p in ps:
                    p.join()
            OPT_INFO("Weights loaded.")
            global_offset = 0
            forward_threshold = 2**29  # 512MB
            if need_reordering:
                tensor_list = sorted(tensor_list, key=lambda x: x[0])
            pbar = tqdm(tensor_list, desc="Deserializing bin", file=sys.stdout)
            for bytes_offset, bytes_size, t, dtype in pbar:
                bytes_offset -= global_offset
                arr = np.frombuffer(bstr[bytes_offset: bytes_offset + bytes_size], dtype=dtype)
                if bytes_offset + bytes_size > forward_threshold:
                    OPT_DEBUG(
                        "Due to reading large bin file, OPT will split the large file to accelerate loading process.", log_once=True)
                    global_offset += bytes_offset + bytes_size
                    global_offset -= global_offset % mmap.ALLOCATIONGRANULARITY
                    f.close()
                    f = open(ir_bin, "rb")
                    bstr = mmap.mmap(f.fileno(), fsize - global_offset,
                                     access=mmap.ACCESS_READ, offset=int(global_offset))
                tmp = PyTensor("bintmp", arr)
                t.betensor = tmp.betensor.reshape(t.ir_shape)
            pbar.refresh()
            f.close()

            inp_tensors = []
            for tname in inp_tensor_names:
                inp_tensors.append(emap[tname])
            g.input_tensors = tuple(inp_tensors)
            out_tensors = []
            for tname in out_tensor_names:
                out_tensors.append(emap[tname])
            g.output_tensors = tuple(out_tensors)
            g.init_networkx()
            if len(g.input_tensors) < 1:
                OPT_WARN(
                    "There is no 'input_tensors' field in IR, will guess them according to the graph structure, which may get unexpected input_tensors.")
                OPT_WARN("The guessed input_tensors:")
                inp_tensors = []
                for n in g.nodes:
                    if OpType.Input == n.type:
                        for t in n.outputs:
                            inp_tensors.append(t)
                            OPT_WARN(t.name)
                g.input_tensors = tuple(inp_tensors)
            if len(g.output_tensors) < 1:
                OPT_WARN(
                    "There is no 'output_tensors' field in IR, will guess them according to the graph structure, which may get unexpected output_tensors.")
                OPT_WARN("The guessed output_tensors:")
                out_tensors = []
                for n in g.nodes:
                    if len(n.children) < 1:
                        for t in n.outputs:
                            out_tensors.append(t)
                            OPT_WARN(t.name)
                g.output_tensors = tuple(out_tensors)

            # compatiable the constant scale zp in constant data
            opt_mini_keys = {'scale': 'scale', 'zp': 'zerop'}
            mini_keys = ['scale', 'zp']
            for n in g.nodes:
                need_pop_key = []
                for k, t in n.constants.items():
                    for mini_key in mini_keys:
                        if k.endswith(f"_{mini_key}"):
                            main_key = k[0:-len(f"_{mini_key}")]
                            if main_key in n.constants.keys():
                                n.constants[main_key].__setattr__(opt_mini_keys[mini_key], n.constants[k].betensor)
                            else:
                                n.params[k] = t.betensor.cpu().numpy().flatten().tolist()
                                OPT_DEBUG(f"{main_key} has scale/zp, but this node={n.type} has not the corresponding {main_key} data,"
                                          f" and will change the constant data to parameter data.")
                            need_pop_key.append(k)
                for pop_key in need_pop_key:
                    n.constants.pop(pop_key)
            OPT_INFO('Successfully parsed IR with python API.')
    except Exception as e:
        OPT_WARN(f'Failed to parse IR with the exception msg: {e}')
        OPT_WARN(msg)
        raise e

    return g


def serialize_graph_to_ir(g, ir_txt, ir_bin):
    from AIPUBuilder.Optimizer.utils import dtype2str, dtype2bytes, dtype2nptype, make_path
    from AIPUBuilder.Optimizer.logger import OPT_INFO, tqdm
    import mmap
    import threading
    import sys

    def _convert_scale_zp_to_list(data):
        import torch
        import numpy as np
        ret_data = None
        if isinstance(data, (float, int)):
            ret_data = [data]
        elif isinstance(data, (np.ndarray, torch.Tensor)):
            ret_data = data.tolist()
            ret_data = _convert_scale_zp_to_list(ret_data)
        else:
            ret_data = data
        return ret_data

    gstr = ''
    gstr += f'model_name={g.name}\nlayer_number={len(g.nodes)}\n'
    inp_tensor_names = []
    for t in g.input_tensors:
        inp_tensor_names.append(t.name)
    gstr += f'input_tensors={cast_to_NodeParamValue_string(inp_tensor_names)}\n'
    out_tensor_names = []
    for t in g.output_tensors:
        out_tensor_names.append(t.name)
    gstr += f'output_tensors={cast_to_NodeParamValue_string(out_tensor_names)}\n'
    gstr += '\n'
    offset = 0
    tensor_list = []
    pbar = tqdm(enumerate(g.nodes), desc="Writing IR", file=sys.stdout)
    for i, n in pbar:
        gstr += f'layer_id={i}\nlayer_name={n.name}\nlayer_type={n.type.name}\n'
        bottom_names = []
        bottom_shape = []
        bottom_dtype = []
        for t in n.inputs:
            bottom_names.append(t.name)
            bottom_shape.append(list(t.ir_shape))
            bottom_dtype.append(dtype2str(t.dtype))
        top_names = []
        top_shape = []
        top_dtype = []
        top_scale = []
        top_zerop = []
        for t in n.outputs:
            top_names.append(t.name)
            top_shape.append(list(t.ir_shape))
            top_dtype.append(dtype2str(t.dtype))
            top_scale.append(t.scale)
            top_zerop.extend([int(zp) for zp in _convert_scale_zp_to_list(t.zerop)])
        gstr += f'layer_bottom={cast_to_NodeParamValue_string(bottom_names)}\n'
        gstr += f'layer_bottom_shape={cast_to_NodeParamValue_string(bottom_shape)}\n'
        gstr += f'layer_bottom_type={cast_to_NodeParamValue_string(bottom_dtype)}\n'
        gstr += f'layer_top={cast_to_NodeParamValue_string(top_names)}\n'
        gstr += f'layer_top_shape={cast_to_NodeParamValue_string(top_shape)}\n'
        gstr += f'layer_top_type={cast_to_NodeParamValue_string(top_dtype)}\n'
        gstr += f'layer_top_scale={cast_to_NodeParamValue_string(top_scale)}\n'
        gstr += f'layer_top_zp={cast_to_NodeParamValue_string(top_zerop)}\n'
        for c in n.constants.keys():
            ct = n.constants[c]
            c_size = dtype2bytes(ct.dtype) * ct.betensor.numel()
            gstr += f'{c}_type={dtype2str(ct.dtype)}\n'
            gstr += f'{c}_offset={offset}\n'
            gstr += f'{c}_size={c_size}\n'
            gstr += f'{c}_shape={cast_to_NodeParamValue_string(list(ct.betensor.shape))}\n'
            # bstr += ct.betensor.cpu().contiguous().numpy().astype(dtype2nptype(ct.dtype)).tobytes()
            tensor_list.append([offset, c_size, ct])
            offset += c_size
        for k, v in n.params.items():
            gstr += f'{k}={cast_to_NodeParamValue_string(v)}\n'
        gstr += '\n'
    pbar.refresh()
    make_path(ir_txt)
    make_path(ir_bin)
    with open(ir_txt, 'w') as ftxt:
        ftxt.write(gstr)

    if len(tensor_list) == 0:
        with open(ir_bin, 'wb') as fbin:
            pass
        return

    total_size = tensor_list[-1][0] + tensor_list[-1][1]
    mem_seg = 2 ** 29 - (2**29 % mmap.ALLOCATIONGRANULARITY)  # 512MB

    job_list = []
    job = []
    workloads = 0
    for offset, size, ct in tensor_list:
        offset = offset % mem_seg
        job.append([offset, size, ct])
        if offset + size > mem_seg:
            remains = mem_seg - offset
            job_list.append(job)
            workloads += len(job)
            job = [[0, size - remains, ct]]
    if len(job) > 0:
        job_list.append(job)
        workloads += len(job)

    def write_to_kernel(origin_offset, job, fileno, pbar):
        length = mem_seg
        if origin_offset + mem_seg > total_size:
            length = total_size - origin_offset
        mem = mmap.mmap(fileno, length, flags=mmap.MAP_SHARED, access=mmap.ACCESS_WRITE, offset=origin_offset)
        for offset, size, ct in job:
            bytes = ct.betensor.cpu().contiguous().numpy().astype(dtype2nptype(ct.dtype)).tobytes()
            if size == len(bytes):
                if offset + size <= mem_seg:
                    mem[offset:offset+size] = bytes
                else:
                    mem[offset:] = bytes[:length-offset]
            else:
                mem[:size] = bytes[-size:]
            pbar.update(1)
        mem.close()

    cores = len(job_list)
    # alloc space on disk
    fbin = open(ir_bin, 'wb')
    fbin.seek(total_size-1)
    fbin.write(b'\0')
    fbin.close()
    fbin = open(ir_bin, 'r+b')

    tpbar = tqdm(total=workloads, desc="Serializing bin", file=sys.stdout)
    ps = []
    for i in range(cores):
        p = threading.Thread(target=write_to_kernel, args=(i*mem_seg, job_list[i], fbin.fileno(), tpbar))
        p.start()
        ps.append(p)

    for p in ps:
        p.join()
    tpbar.refresh()

    fbin.close()
