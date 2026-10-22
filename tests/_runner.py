"""デバイス上で動く最小のテストランナー(`python tools/dev.py test` から呼ばれる)。

tests/test_*.py の中の test_* 関数を実行し、最後に
    RESULT: OK (n passed, 0 failed)  /  RESULT: FAIL (...)
を出力する。dev.py はこの行で成否を判定する。
PC 上(tools/host_test.py、host=True)では test_hw_* を実機専用としてスキップする。
"""

import gc
import os
import sys
import time

TEST_DIR = "/remote/tests"


def main(pattern="", host=False):
    files = sorted(
        f
        for f in os.listdir(TEST_DIR)
        if f.startswith("test_") and f.endswith(".py") and pattern in f
    )
    passed = 0
    failed = 0
    skipped = 0
    for f in files:
        modname = f[:-3]
        try:
            mod = __import__(modname)
        except Exception as e:  # noqa: BLE001
            failed += 1
            print("ERROR import", modname)
            sys.print_exception(e)  # type: ignore[attr-defined]
            continue
        for name in sorted(dir(mod)):
            if not name.startswith("test_"):
                continue
            fn = getattr(mod, name)
            if not callable(fn):
                continue
            if host and name.startswith("test_hw_"):
                skipped += 1
                continue
            gc.collect()
            t0 = time.ticks_ms()
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                failed += 1
                print("FAIL", modname + "." + name)
                sys.print_exception(e)  # type: ignore[attr-defined]
            else:
                passed += 1
                dt = time.ticks_diff(time.ticks_ms(), t0)
                print("PASS", modname + "." + name, "(%d ms)" % dt)
    gc.collect()
    if not host:
        print("mem_free:", gc.mem_free())
    extra = ", %d skipped (hw)" % skipped if skipped else ""
    print(
        "RESULT: %s (%d passed, %d failed%s)"
        % ("OK" if failed == 0 else "FAIL", passed, failed, extra)
    )
