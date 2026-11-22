#!/usr/bin/env python3
"""Run a YoWASP tool with a larger wasm stack (toolchain-friction workaround).

yosys-slang recurses deeply on this RTL and exhausts wasmtime's default 2 MiB
wasm stack ("wasm trap: call stack exhausted"). This wrapper raises
Config.max_wasm_stack and runs the tool on a big-stack thread. It changes no
installed package; it only monkeypatches the in-process wasmtime Config.

Usage: yowasp_bigstack.py {yosys|nextpnr-ecp5|ecppack} ARGS...
"""
import sys
import threading

STACK = 256 * 1024 * 1024


def main() -> int:
    tool, argv = sys.argv[1], sys.argv[2:]
    import wasmtime
    orig = wasmtime.Config.__init__

    def patched(self, *a, **k):
        orig(self, *a, **k)
        # async_stack_size must be >= max_wasm_stack; the python binding does
        # not expose it, but the C symbol is exported by the bundled library.
        import ctypes
        from wasmtime import _ffi
        _ffi.dll.wasmtime_config_async_stack_size_set.argtypes = [
            ctypes.c_void_p, ctypes.c_size_t]
        _ffi.dll.wasmtime_config_async_stack_size_set(self.ptr(), STACK)
        self.max_wasm_stack = STACK
    wasmtime.Config.__init__ = patched
    if tool == "yosys":
        import yowasp_yosys as m
        fn = m.run_yosys
    elif tool == "nextpnr-ecp5":
        import yowasp_nextpnr_ecp5 as m
        fn = m.run_nextpnr_ecp5
    elif tool == "ecppack":
        import yowasp_nextpnr_ecp5 as m
        fn = m.run_ecppack
    else:
        raise SystemExit("unknown tool " + tool)
    result = []
    threading.stack_size(256 * 1024 * 1024)

    def run():
        try:
            result.append(fn(argv))
        except SystemExit as e:
            result.append(e.code or 0)
    t = threading.Thread(target=run)
    t.start()
    t.join()
    return int(result[0] or 0) if result else 1


if __name__ == "__main__":
    sys.exit(main())
