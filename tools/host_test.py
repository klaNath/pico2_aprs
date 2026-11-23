#!/usr/bin/env python3
"""tests/ を PC(CPython)上で実行する(`python tools/dev.py test --host` から呼ばれる)。

MicroPython 固有の API(micropython.const, time.ticks_*, sys.print_exception)を
最小限の代用品で補い、デバイスと同じ tests/_runner.py を使う。
名前が test_hw_ で始まるテストは実機専用としてスキップされる。
"""

from __future__ import annotations

import os
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

    # Windows の monotonic は分解能が 15.6 ms(GetTickCount64)で、sleep_ms(10) が 0 ms に見えることがある
    t0 = time.perf_counter_ns()
    period = 1 << 30  # MicroPython rp2 の ticks の周期
    # 起点を周期の終わりの少し前に置き、テストの途中で折り返させる(引き算や大小比較の誤りを PC で
    # 見つける)。HOST_TICKS_START=<ミリ秒>で変えられる(0 で実機の起動直後と同じ)
    env = os.environ.get("HOST_TICKS_START")
    if env:
        start_ms = int(env) & (period - 1)
        start_us = (start_ms * 1000) & (period - 1)
    else:
        start_ms, start_us = period - 5_000, period - 5_000_000

    def ticks_ms() -> int:
        return ((time.perf_counter_ns() - t0) // 1_000_000 + start_ms) & (period - 1)

    def ticks_us() -> int:
        return ((time.perf_counter_ns() - t0) // 1_000 + start_us) & (period - 1)

    def ticks_add(t: int, d: int) -> int:
        # 実機(MicroPython)と同じく、半周を超える差は受けない(長い時間を一度に足す誤りを PC でも見つける)
        if not -period // 2 <= d < period // 2:
            raise OverflowError("ticks interval overflow")
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
    import gc

    if not hasattr(gc, "mem_free"):  # 局の状態の表示(Station._report)が使う
        gc.mem_free = lambda: 0  # type: ignore[attr-defined]
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
