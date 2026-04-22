# Copyright (c) DP Technology.
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

from functools import lru_cache
from unicore.data import BaseWrapperDataset


class KeyDataset(BaseWrapperDataset):
    def __init__(self, dataset, key, default=None):
        self.dataset = dataset
        self.key = key
        self.default = default

    def __len__(self):
        return len(self.dataset)

    @lru_cache(maxsize=16)
    def __getitem__(self, idx):
        item = self.dataset[idx]
        if self.default is not None and self.key not in item:
            return self.default
        return item[self.key]

class LengthDataset(BaseWrapperDataset):

    def __init__(self, dataset):
        super().__init__(dataset)

    @lru_cache(maxsize=16)
    def __getitem__(self, idx):
        item = self.dataset[idx]
        return len(item)