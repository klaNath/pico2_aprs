"""デバイス上で動く最小のテストランナー(`python tools/dev.py test` から呼ばれる)。

tests/test_*.py の中の test_* 関数を実行し、最後に
    RESULT: OK (n passed, 0 failed)  /  RESULT: FAIL (...)
を出力する。dev.py はこの行で成否を判定する。
PC 上(tools/host_test.py、host=True)では test_hw_* を実機専用としてスキップする。
モジュールに SKIP = "理由" があれば、そのモジュールのテストは PC でも実機でも全部スキップする
(いまは使わない機能のテストを止めておく。復活させるときは SKIP を消す)。
excluded: release で ROMFS に入れないモジュールの名前(dev.py test --romfs が渡す)。それを
import できずに失敗したテストのモジュールはスキップする(ほかの ImportError は失敗のまま)。
"""

import gc
import os
import sys
import time

TEST_DIR = "/remote/tests"


def _excluded(e, excluded):
    msg = str(e)
    for name in excluded:
        if name in msg:
            return name
    return None


def main(pattern="", host=False, excluded=()):
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
        except ImportError as e:
            name = _excluded(e, excluded)
            if name is None:
                failed += 1
                print("ERROR import", modname)
                sys.print_exception(e)  # type: ignore[attr-defined]
            else:
                skipped += 1
                print("SKIP", modname + ":", name, "is not in the release")
            continue
        except Exception as e:  # noqa: BLE001
            failed += 1
            print("ERROR import", modname)
            sys.print_exception(e)  # type: ignore[attr-defined]
            continue
        reason = getattr(mod, "SKIP", None)
        if reason:
            print("SKIP", modname + ":", reason)
        for name in sorted(dir(mod)):
            if not name.startswith("test_"):
                continue
            fn = getattr(mod, name)
            if not callable(fn):
                continue
            if reason or (host and name.startswith("test_hw_")):
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
        # 済んだテストのモジュール(と、それが import した別のテストのモジュール)を手放す。
        # 残すと実機ではヒープが細切れになり、後のテストの大きな確保が MemoryError になる。
        # src のモジュールは残す(import し直すと、マウント越しのコンパイルで遅くなる)
        mod = fn = None
        for m in [m for m in sys.modules if m.startswith("test_")]:
            del sys.modules[m]
    gc.collect()
    if not host:
        print("mem_free:", gc.mem_free())
    extra = ", %d skipped" % skipped if skipped else ""
    print(
        "RESULT: %s (%d passed, %d failed%s)"
        % ("OK" if failed == 0 else "FAIL", passed, failed, extra)
    )
