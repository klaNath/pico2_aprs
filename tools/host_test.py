#!/usr/bin/env python3
"""tests/ を PC(CPython)上で実行する(`python tools/dev.py test --host` から呼ばれる)。

MicroPython 固有の API(micropython.const, time.ticks_*, sys.print_exception)を
最小限の代用品で補い、デバイスと同じ tests/_runner.py を使う。
名前が test_hw_ で始まるテストは実機専用としてスキップされる。
"""

from __future__ import annotations

import sys
import time
import traceback
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def install_shims() -> None:
    mp = types.ModuleType("micropython")
    mp.const = lambda x: x  # type: ignore[attr-defined]
    mp.native = lambda f: f  # type: ignore[attr-defined]
    mp.viper = lambda f: f  # type: ignore[attr-defined]
    mp.schedule = lambda f, a: f(a)  # type: ignore[attr-defined]
    sys.modules["micropython"] = mp
    # viper のポインタ型: 配列をそのまま添字アクセスする(注釈の評価にも使われる)
    import builtins

    for name in ("ptr", "ptr8", "ptr16", "ptr32", "uint"):
        setattr(builtins, name, lambda x: x)

    t0 = time.monotonic_ns()
    period = 1 << 30  # MicroPython rp2 の ticks の周期

    def ticks_ms() -> int:
        return ((time.monotonic_ns() - t0) // 1_000_000) & (period - 1)

    def ticks_us() -> int:
        return ((time.monotonic_ns() - t0) // 1_000) & (period - 1)

    def ticks_add(t: int, d: int) -> int:
        return (t + d) & (period - 1)

    def ticks_diff(a: int, b: int) -> int:
        return ((a - b + period // 2) & (period - 1)) - period // 2

    for name, fn in [
        ("ticks_ms", ticks_ms),
        ("ticks_us", ticks_us),
        ("ticks_add", ticks_add),
        ("ticks_diff", ticks_diff),
        ("sleep_ms", lambda ms: time.sleep(ms / 1000)),
    ]:
        setattr(time, name, fn)
    sys.print_exception = lambda e, f=None: traceback.print_exception(e, file=f or sys.stdout)  # type: ignore[attr-defined]


def main() -> int:
    install_shims()
    sys.path[:0] = [str(ROOT / "tests"), str(ROOT / "src")]
    import _runner  # type: ignore[import-not-found]

    _runner.TEST_DIR = str(ROOT / "tests")
    _runner.main(sys.argv[1] if len(sys.argv) > 1 else "", host=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
