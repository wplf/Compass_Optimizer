# SPDX-License-Identifier: Apache-2.0
# Copyright © 2023 Arm Technology (China) Co. Ltd.

import numpy as np
import torch

from AIPUBuilder.Optimizer.framework import *

from torch.utils.data import Dataset
from collections import defaultdict


@register_plugin(PluginType.Dataset, '1.0')
class FasterrcnnVocDataset(Dataset):
    """
    This FasterrcnnVocDataset plugin is used for fasterrcnn_caffe model in Optimizer.
    """

    def __init__(self, data_file=None, label_file=None):
        """
        :param data_file: a .npy file
        :param label_file: a dict format in .npy file and format is {image_name_idx: [label_index, ymin, xmin, ymax, xmax]}
                        image_name_idx: int,
                        label_index: list,
                        ymin: list, the len(ymin) == len(label_index)
                        xmin: list, the len(xmin) == len(label_index)
                        ymax: list, the len(ymax) == len(label_index)
                        xmax: list, the len(xmax) == len(label_index)
        """
        self.data = np.load(data_file, mmap_mode='c').astype(np.float32)
        self.label = None
        if label_file is not None:
            self.label = np.load(label_file, allow_pickle=True).item()

    def __len__(self):
        return len(self.data)

    def __getitem__(self, idx):
        image_data_t = self.data[idx]
        image_data = np.zeros_like(image_data_t)
        image_data[0] = image_data_t[2]
        image_data[1] = image_data_t[1]
        image_data[2] = image_data_t[0]
        image_data = image_data.copy()
        sample = [[image_data, np.array([[600, 600, 1]])], {}]
        if self.label is not None:
            raw_label = self.label[idx]
            image_name = idx
            label_idx = raw_label[0]
            ymin = raw_label[1]
            xmin = raw_label[2]
            ymax = raw_label[3]
            xmax = raw_label[4]
            bbox = []
            for index in range(len(raw_label[1])):
                ele = [raw_label[1][index], raw_label[2][index],
                       raw_label[3][index], raw_label[4][index]]
                bbox.append(ele)

            sample[1].update({
                'image_name': np.array(image_name),
                'label_index': np.array(label_idx),
                'bbox': np.array(bbox),
            })
        return sample

    @staticmethod
    def collate_fn(batch):
        batch_label = {}
        batch_data0 = None
        batch_data1 = None
        batch_data_all = []
        for batch_idx in range(len(batch)):
            els_sample = batch[batch_idx][0][0]
            single_data = torch.unsqueeze(torch.tensor(els_sample), 0)
            batch_data0 = single_data if batch_idx == 0 else torch.cat(
                (batch_data0, single_data), 0)
            batch_data1 = torch.tensor(batch[batch_idx][0][1]) if batch_idx == 0 else torch.cat(
                (batch_data1, torch.tensor(batch[batch_idx][0][1])), 0)
        batch_data_all = [batch_data0, batch_data1]

        image_list = []
        label_list = []
        bbox_list = []
        ori_img_shape_list = []
        for idx, sample in enumerate(batch):
            if not sample[1]:
                continue
            image_name = sample[1]['image_name']
            label_index = sample[1]['label_index']
            bbox = sample[1]['bbox']
            image_list.append(torch.tensor(image_name))
            label_list.append(torch.tensor(label_index))
            bbox_list.append(torch.tensor(bbox))
        batch_label.update({'image_name': image_list})
        batch_label.update({'label_index': label_list})
        batch_label.update({'bbox': bbox_list})
        return batch_data_all, batch_label
