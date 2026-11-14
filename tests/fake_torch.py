"""Fake tensor backend shared by the mutation runtime adapter tests.

Just enough of the Torch surface the mutation sessions use (slot read/write,
clone, hooks) to exercise their mechanics without Torch. Stdlib-only.
"""

import array
import math


class _Device:
    type = "cpu"


class _Truth:
    def __init__(self, value):
        self.value = value

    def all(self):
        return self.value


class FakeTensor:
    dtype = "float32"
    device = _Device()

    def __init__(self, values, offset=0, length=None, width=None):
        self.values = values
        self.offset = offset
        self.length = len(values) if length is None else length
        self.width = width

    def __getitem__(self, index):
        if self.width is None:
            return FakeTensor(self.values, self.offset + index, 1)
        return FakeTensor(self.values, self.offset + index * self.width, self.width)

    def __setitem__(self, index, value):
        self.values[self.offset + index] = value

    def detach(self):
        return self

    def contiguous(self):
        return self

    def numpy(self):
        return self

    def tobytes(self):
        return self.values[self.offset:self.offset + self.length].tobytes()

    def copy_(self, other):
        self.values[self.offset:self.offset + self.length] = other.values[other.offset:other.offset + other.length]
        return self

    def clone(self):
        return FakeTensor(array.array("f", self.values[self.offset:self.offset + self.length]),
                          0, self.length, self.width)


class _NoGrad:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeTorch:
    float32 = "float32"

    @staticmethod
    def isfinite(tensor):
        return _Truth(all(math.isfinite(x) for x in tensor.values[tensor.offset:tensor.offset + tensor.length]))

    @staticmethod
    def tensor(values, dtype=None):
        return FakeTensor(array.array("f", values))

    @staticmethod
    def no_grad():
        return _NoGrad()


class _Handle:
    def __init__(self, hooks, hook):
        self.hooks, self.hook = hooks, hook

    def remove(self):
        if self.hook in self.hooks:
            self.hooks.remove(self.hook)


class FakeModule:
    def __init__(self, fail_hook=False):
        self.hooks = []
        self.fail_hook = fail_hook
        self.registrations = 0

    def register_forward_hook(self, hook):
        self.registrations += 1
        if self.fail_hook and self.registrations > 1:
            # The passive capture registers first; the session's hook fails.
            raise RuntimeError("partial setup failure")
        self.hooks.append(hook)
        return _Handle(self.hooks, hook)

    def __call__(self, inputs, result):
        for hook in list(self.hooks):
            hook(self, inputs, result)
        return result
