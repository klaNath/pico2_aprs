#!/usr/bin/env python3
"""Pico 2 / MicroPython 開発ヘルパー

Claude (Claude Code) と人間の両方が使う、非対話・タイムアウト付きの mpremote ラッパー。
どの python で起動しても、プロジェクトの .venv があればそちらで自動的に再実行される。

    python tools/dev.py ports                 接続中の Pico を列挙
    python tools/dev.py info                  ファームウェア/メモリ/ストレージ情報
    python tools/dev.py run [FILE] -t 30      src/ をマウントして FILE(既定 src/main.py)を実行
    python tools/dev.py exec "CODE" -t 10     src/ をマウントしてコード片を実行(--no-mount で書き込んだ本体を使う)
    python tools/dev.py test [PATTERN]        tests/ をデバイス上で実行(結果で終了コードが変わる)
    python tools/dev.py test --host [PATTERN] tests/ を PC(CPython)上で実行(test_hw_* はスキップ)
    python tools/dev.py lint                  ruff + pyright(ホスト側の静的チェック)
    python tools/dev.py check                 lint + test --host + test
    python tools/dev.py deploy [--reset]      src/ の中身をデバイスのフラッシュへ書き込み
    python tools/dev.py romfs-query           ROMFS 区画の有無・サイズを表示
    python tools/dev.py romfs-build           src/ から ROMFS イメージを作る(build/app.romfs、書き込みなし)
    python tools/dev.py romfs-deploy [--reset] src/ を .mpy 化して ROMFS へ書き込み、ランチャー main.py を配置
    python tools/dev.py build [--skip-check]  検査(check)のあと、本体だけ(デモを除く)の ROMFS イメージを作る
    python tools/dev.py release [--skip-check] build + ROMFS へ書き込み + 起動の確認(電源投入で動く状態にする)
    python tools/dev.py monitor -s 10 [--reset] シリアル出力を一定時間だけ読む(起動時の挙動確認)
    python tools/dev.py ls [PATH] / cat PATH  デバイス上のファイル操作
    python tools/dev.py rm-main               デバイス上の main.py を削除(自動起動を止める)
    python tools/dev.py mip PKG               micropython-lib のパッケージをインストール
    python tools/dev.py reset                 ハードリセット
    python tools/dev.py repl                  対話 REPL(人間用。Claude は使わない)

環境変数 PICO_PORT でポートを明示できる(例: COM5, /dev/ttyACM0)。
"""

from __future__ import annotations

import argparse
import os
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
TESTS = ROOT / "tests"
VENV = ROOT / ".venv"
RP_VID = 0x2E8A  # Raspberry Pi の USB Vendor ID

# ---------------------------------------------------------------- 文字コード(UTF-8 に統一)
# Windows では既定の出力エンコーディングが cp932 になり、日本語や
# デバイスからの UTF-8 出力が化ける。自分自身と子プロセス(mpremote)の
# 入出力をすべて UTF-8 に揃える。
os.environ["PYTHONUTF8"] = "1"
os.environ["PYTHONIOENCODING"] = "utf-8"
CHILD_ENV = os.environ.copy()

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

if os.name == "nt":
    # コンソールのコードページも UTF-8(65001)にする(ターミナルに直接表示する場合)
    try:
        import ctypes

        ctypes.windll.kernel32.SetConsoleOutputCP(65001)  # type: ignore[attr-defined]
        ctypes.windll.kernel32.SetConsoleCP(65001)  # type: ignore[attr-defined]
    except Exception:
        pass


# ---------------------------------------------------------------- venv 自動切り替え
def _venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _in_venv() -> bool:
    try:
        return Path(sys.prefix).resolve() == VENV.resolve()
    except OSError:
        return False


if VENV.exists() and not _in_venv() and _venv_python().exists():
    sys.exit(
        subprocess.call(
            [str(_venv_python()), str(Path(__file__).resolve()), *sys.argv[1:]], env=CHILD_ENV
        )
    )


# ---------------------------------------------------------------- 共通処理
def log(msg: str) -> None:
    print(f"[dev] {msg}", file=sys.stderr, flush=True)


def die(msg: str, code: int = 2) -> None:
    log(msg)
    sys.exit(code)


def find_ports() -> list:
    try:
        from serial.tools import list_ports
    except ImportError:
        die("pyserial がありません。先に `python tools/bootstrap.py` を実行してください。")
    return [p for p in list_ports.comports() if p.vid == RP_VID]


def find_port() -> str:
    env = os.environ.get("PICO_PORT")
    if env:
        return env
    ports = find_ports()
    if not ports:
        die(
            "Pico が見つかりません。USB ケーブル(データ通信対応か)、MicroPython が書き込まれているか、"
            "BOOTSEL モード(RP2350 ドライブとして見えている状態)になっていないかを確認してください。"
        )
    if len(ports) > 1:
        log(f"複数の Pico を検出: {[p.device for p in ports]} → {ports[0].device} を使用(PICO_PORT で指定可)")
    return ports[0].device


def stream(cmd: list[str], timeout: float | None) -> tuple[int, str]:
    """コマンドを実行し、出力をそのまま流しつつ収集する。timeout 秒で強制終了。"""
    proc = subprocess.Popen(
        cmd,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        env=CHILD_ENV,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    chunks: list[str] = []

    def reader() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            chunks.append(line)
            sys.stdout.write(line)
            sys.stdout.flush()

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    try:
        rc = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        t.join(2)
        log(
            f"タイムアウト({timeout:g} 秒)で打ち切りました。無限ループのプログラムなら想定どおりです。"
            "デバイス上のプログラムは次の mpremote 接続時に Ctrl-C で停止されます。"
        )
        return 124, "".join(chunks)
    t.join(2)
    return rc, "".join(chunks)


def mpremote(args: list[str], timeout: float | None = 60) -> tuple[int, str]:
    cmd = [sys.executable, "-m", "mpremote", "connect", find_port(), *args]
    log("mpremote " + " ".join(args))
    rc, out = stream(cmd, timeout)
    if "could not enter raw repl" in out or "failed to access" in out.lower():
        log(
            "シリアルポートに接続できません。MicroPico / Thonny / シリアルモニタなど、"
            "ポートを掴んでいる他のツールを閉じてください。"
        )
    return rc, out


def rel(p: Path) -> str:
    return p.resolve().relative_to(ROOT).as_posix()


# ---------------------------------------------------------------- サブコマンド
INFO_CODE = r"""
import sys, os, gc, machine
gc.collect()
print("implementation:", sys.implementation)
print("version       :", sys.version)
print("machine       :", os.uname().machine)
print("freq          :", machine.freq(), "Hz")
print("mem_free      :", gc.mem_free(), "bytes / alloc:", gc.mem_alloc())
s = os.statvfs("/")
print("flash fs      :", s[0] * s[3], "bytes free of", s[0] * s[2])
print("files at /    :", os.listdir("/"))
"""


def cmd_ports(a) -> int:
    ports = find_ports()
    if not ports:
        print("Pico は見つかりませんでした。")
        return 1
    for p in ports:
        print(f"{p.device}\t{p.description}\tserial={p.serial_number}")
    return 0


def cmd_info(a) -> int:
    return mpremote(["exec", INFO_CODE], a.timeout)[0]


def cmd_run(a) -> int:
    f = Path(a.file)
    if not f.exists():
        die(f"{f} がありません")
    return mpremote(["mount", rel(SRC), "run", rel(f)], a.timeout)[0]


def cmd_exec(a) -> int:
    if a.no_mount:  # 書き込んだ本体(フラッシュ / ROMFS)をそのまま使う
        return mpremote(["exec", a.code], a.timeout)[0]
    return mpremote(["mount", rel(SRC), "exec", a.code], a.timeout)[0]


def _test_result(rc: int, out: str) -> int:
    if "RESULT: OK" in out:
        return 0
    if "RESULT: FAIL" in out:
        return 1
    log("テスト結果行(RESULT:)が得られませんでした。上の出力を確認してください。")
    return rc or 1


def cmd_test(a) -> int:
    if a.host:
        log("PC(CPython)上で tests/ を実行します(test_hw_* はスキップ)")
        return _test_result(*stream([sys.executable, rel(ROOT / "tools" / "host_test.py"), a.pattern], a.timeout))
    code = (
        "import sys\n"
        "sys.path.insert(0, '/remote/tests')\n"
        "sys.path.insert(0, '/remote/src')\n"
        "import _runner\n"
        f"_runner.main({a.pattern!r})\n"
    )
    return _test_result(*mpremote(["mount", ".", "exec", code], a.timeout))


def cmd_lint(a) -> int:
    rc0 = stream([sys.executable, rel(ROOT / "tools" / "vendor.py"), "--check"], 60)[0]
    rc1 = stream([sys.executable, "-m", "ruff", "check", "src", "tests", "tools"], 120)[0]
    rc2 = stream([sys.executable, "-m", "pyright"], 300)[0]
    return rc0 or rc1 or rc2


def cmd_check(a) -> int:
    rc = cmd_lint(a)
    a.pattern = ""
    a.timeout = 180
    a.host = True
    rc = cmd_test(a) or rc
    a.host = False
    return cmd_test(a) or rc


def cmd_deploy(a) -> int:
    files = sorted(
        p for p in SRC.rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.suffix in {".py", ".mpy", ".json", ".txt", ".bin"}
    )
    if not files:
        die("src/ にファイルがありません")
    dirs = sorted({p.parent.relative_to(SRC).as_posix() for p in files} - {"."})
    mk = "import os\nfor d in %r:\n    try:\n        os.mkdir('/' + d)\n    except OSError:\n        pass\n" % dirs
    args = ["exec", mk]
    for p in files:
        args += ["+", "fs", "cp", rel(p), ":" + p.relative_to(SRC).as_posix()]
    if a.reset:
        args += ["+", "reset"]
    rc, _ = mpremote(args, a.timeout)
    if rc == 0:
        log(f"{len(files)} ファイルを書き込みました" + ("(リセット済み)" if a.reset else ""))
    return rc


# ---------------------------------------------------------------- ROMFS
# ROMFS はフラッシュ上の読み出し専用イメージで、/rom にマウントされ sys.path に入る。
# .py は mpy-cross で .mpy にコンパイルされ、RAM に載せずフラッシュから直接実行される
# (import が速く、メモリ消費が少ない)。
#
# .mpy 化は mpremote に任せず dev.py が行う。mpremote は -march を付けずに mpy-cross を
# 呼ぶため @micropython.viper / native を含むファイルが "invalid arch" で失敗する。
# -s で元のソースパス(src/...)を埋め込むので、トレースバックはそのままローカルを指す。
#
# 配置:
#   ROMFS (/rom)  : src/ の全モジュール(main.py は _romfs_main として格納)
#   フラッシュ (/) : main.py = /rom の _romfs_main を import するだけのランチャー
#                   boot.py(src にあれば。起動直後に必要なのでフラッシュに置く)
# フラッシュ側に同名モジュールが残っていると ROMFS より優先されてしまうため削除する。
ROMFS_STAGE = ROOT / "build" / "romfs"
ROMFS_IMAGE = ROOT / "build" / "app.romfs"
ROMFS_SIZE = 1024 * 1024  # このファームウェアの ROMFS 区画(romfs-query で確認できる)
# build / release で実機に載せないもの(src/ からの相対パス)。デモ・試験用スクリプトと、
# テンプレートの app.py、旧 GNSS(GT-505)用のドライバ。dev.py run では今までどおり使える
RELEASE_EXCLUDE = (
    "app.py", "gnss_demo.py", "gt505_demo.py", "lcd_test.py", "m10_check.py", "rx_loopback.py",
    "sdlog_demo.py", "tx_demo.py", "ui_demo.py", "fm_if_test.py", "fm_if_afsk_test.py",
    "fm_if_rt_test.py",
    "core/nmea.py", "hw/gnss.py",
)
# release で起動を確かめるとき、この文字列が出れば成功(station.Station.start の表示)
RELEASE_BOOT_OK = "[station] "
RELEASE_BOOT_OK_END = " started,"
ROMFS_ENTRY = "_romfs_main"
ROMFS_MARCH = "armv7emsp"  # RP2350 Arm (Cortex-M33 + 単精度 FPU)。RISC-V ビルドなら rv32imc
# sys.implementation._mpy >> 10 の値 → -march 名(py/persistentcode.h の MP_NATIVE_ARCH_*)
MPY_ARCH = {4: "armv6m", 5: "armv7m", 6: "armv7em", 7: "armv7emsp", 8: "armv7emdp", 11: "rv32imc"}
ROMFS_LAUNCHER = f'''\
# dev.py romfs-deploy が生成したランチャー。アプリ本体は ROMFS (/rom) にある。
# 通常の `dev.py deploy` を行うと src/main.py で上書きされ、フラッシュ版に戻る。
import sys

for _p in ("/rom", "/rom/lib"):
    if _p not in sys.path:
        sys.path.append(_p)

import {ROMFS_ENTRY}  # noqa: E402,F401
'''
RM_TREE_CODE = """\
import os
def _rm(p):
    try:
        st = os.stat(p)
    except OSError:
        return
    if st[0] & 0x4000:
        for n in os.listdir(p):
            _rm(p + '/' + n)
        os.rmdir(p)
    else:
        os.remove(p)
    print('flash: removed', p)
for _n in %r:
    _rm('/' + _n)
"""


def _romfs_stage(march: str | None, exclude: tuple[str, ...] = ()) -> list[str]:
    """src/ を build/romfs/ に複製し、main.py を ROMFS_ENTRY に改名する。
    exclude(src/ からの相対パス)は入れない。
    march が指定されていれば .py を .mpy にコンパイルして .py を消す(失敗したら中止)。
    フラッシュ側で削除すべき(ROMFS を隠してしまう)名前の一覧を返す。"""
    import shutil

    # Windows では READONLY 属性のディレクトリを rmdir できない。src/ やコピー・移動された
    # build/ にはこの属性が付いていることがあり(OneDrive やエクスプローラーでの移動など)、
    # copytree は属性も複製するので、消すときに属性を外して再試行する。
    def _force(func, path, _exc):
        os.chmod(path, os.stat(path).st_mode | stat.S_IWRITE)
        func(path)

    if ROMFS_STAGE.exists():
        shutil.rmtree(ROMFS_STAGE, onerror=_force)
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", ".*")
    shutil.copytree(SRC, ROMFS_STAGE, ignore=ignore)
    (ROMFS_STAGE / "boot.py").unlink(missing_ok=True)
    for name in exclude:
        p = ROMFS_STAGE / name
        if not p.exists():
            die(f"除外の指定 src/{name} がありません(dev.py の RELEASE_EXCLUDE を確認)")
        if p.is_dir():
            shutil.rmtree(p, onerror=_force)
        else:
            p.unlink()
    if exclude:
        log(f"{len(exclude)} 個を除外しました: {', '.join(exclude)}")
    main = ROMFS_STAGE / "main.py"
    if main.exists():
        main.rename(ROMFS_STAGE / f"{ROMFS_ENTRY}.py")
    shadow: list[str] = []
    for p in sorted(ROMFS_STAGE.iterdir()):
        if p.is_dir():
            shadow.append(p.name)
        elif p.suffix in {".py", ".mpy"}:
            shadow += [p.stem + ".py", p.stem + ".mpy"]
        else:
            shadow.append(p.name)
    if march:
        _romfs_compile(march)
    return shadow


def _romfs_compile(march: str) -> None:
    import mpy_cross

    n = 0
    for py in sorted(ROMFS_STAGE.rglob("*.py")):
        if py.with_suffix(".mpy").exists():  # src/ に同名の .mpy があればそちらを使う
            py.unlink()
            continue
        stage_rel = py.relative_to(ROMFS_STAGE).as_posix()
        src_rel = "main.py" if stage_rel == f"{ROMFS_ENTRY}.py" else stage_rel
        proc = mpy_cross.run(
            f"-march={march}", "-s", f"src/{src_rel}", "-o", str(py.with_suffix(".mpy")), str(py),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        out = proc.communicate()[0]
        if proc.returncode != 0:
            sys.stdout.write(out)
            die(f"mpy-cross が src/{src_rel} のコンパイルに失敗しました(-march={march})")
        py.unlink()
        n += 1
    log(f"{n} ファイルを .mpy にコンパイルしました(-march={march})")


def _romfs_march(a) -> str | None:
    """.mpy 化に使う -march。None なら .py のまま格納する。"""
    if a.no_mpy:
        return None
    try:
        import mpy_cross  # noqa: F401
    except ImportError:
        log(
            "mpy-cross が無いため .py のまま格納します。"
            "`python tools/bootstrap.py` で requirements-dev.txt の mpy-cross を入れてください"
        )
        return None
    return a.march


def cmd_romfs_query(a) -> int:
    return mpremote(["romfs", "query"], a.timeout)[0]


def cmd_romfs_build(a) -> int:
    _romfs_stage(_romfs_march(a), getattr(a, "exclude", ()))
    out = Path(a.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    # .mpy 化は済んでいるので mpremote には --no-mpy で詰めるだけをさせる
    cmd = [sys.executable, "-m", "mpremote", "romfs", "--no-mpy",
           "-o", str(out), "build", rel(ROMFS_STAGE)]
    rc = stream(cmd, 120)[0]
    if rc == 0 and out.exists():
        log(f"ROMFS イメージ: {rel(out)}({out.stat().st_size} bytes)")
    return rc


def cmd_romfs_deploy(a) -> int:
    # 1. デバイスに ROMFS 区画があるか確認
    # (「No ROMFS partitions available」にも一致しないよう、区画サイズの行で判定する)
    rc, out = mpremote(["romfs", "query"], 20)
    if rc != 0 or f"ROMFS{a.partition} partition has size" not in out:
        die(
            f"このファームウェアでは ROMFS 区画 {a.partition} が見つかりません。ROMFS は MICROPY_VFS_ROM が有効で、"
            "かつ ROMFS 区画を持つビルドでのみ使えます。`dev.py romfs-query` の出力を確認し、"
            "必要なら ROMFS 区画付きのファームウェアを書き込んでください。"
        )

    # 2. .mpy の -march がデバイスと一致するか確認(不一致だと import 時に ValueError になる)
    march = _romfs_march(a)
    if march:
        rc, out = mpremote(["exec", "import sys; print('MPYARCH', sys.implementation._mpy >> 10)"], 20)
        arch = next((int(w.split()[1]) for w in out.splitlines() if w.startswith("MPYARCH ")), -1)
        if MPY_ARCH.get(arch) != march:
            die(f"デバイスの .mpy アーキテクチャ({MPY_ARCH.get(arch, arch)})が -march={march} と一致しません")

    # 3. イメージを作ってデプロイ(.mpy 化は dev.py、容量チェックと書き込みは mpremote)
    shadow = _romfs_stage(march, getattr(a, "exclude", ()))
    log(f"ROMFS 区画 {a.partition} へ {rel(ROMFS_STAGE)} をデプロイします")
    rc, _ = mpremote(
        ["romfs", "-p", str(a.partition), "--no-mpy", "deploy", rel(ROMFS_STAGE)],
        a.timeout,
    )
    if rc != 0:
        log("ROMFS のデプロイに失敗しました(イメージが区画より大きい場合もここで失敗します)")
        return rc

    # 4. フラッシュ側: 同名モジュールを削除し、ランチャー main.py と boot.py を置く
    launcher = ROOT / "build" / "romfs_launcher_main.py"
    launcher.write_text(ROMFS_LAUNCHER, encoding="utf-8")
    args = ["exec", RM_TREE_CODE % shadow, "+", "fs", "cp", rel(launcher), ":main.py"]
    if (SRC / "boot.py").exists():
        args += ["+", "fs", "cp", rel(SRC / "boot.py"), ":boot.py"]

    # 5. リセットして ROMFS をマウント(ソフトリセットなら中身も確認する)
    if a.reset:
        args += ["+", "reset"]
    else:
        verify = "import os, sys\nprint('/rom   :', os.listdir('/rom'))\nprint('sys.path:', sys.path)\n"
        args += ["+", "soft-reset", "+", "exec", verify]
    rc, _ = mpremote(args, 60)
    if rc == 0:
        log("ROMFS デプロイ完了。起動時の動作は `dev.py monitor --reset -s 15` で確認できます")
    return rc


def _release_args(a) -> None:
    """build / release で共通の romfs-build / romfs-deploy の引数"""
    a.exclude = RELEASE_EXCLUDE
    a.no_mpy = False
    a.march = ROMFS_MARCH
    a.output = rel(ROMFS_IMAGE)


def cmd_build(a) -> int:
    if not a.skip_check:
        log("検査: lint + test --host + test(実機)。失敗したらイメージを作りません")
        if cmd_check(a) != 0:
            log("検査に失敗したので中止しました(--skip-check で省略できます)")
            return 1
    _release_args(a)
    rc = cmd_romfs_build(a)
    if rc != 0:
        return rc
    size = ROMFS_IMAGE.stat().st_size
    log(f"ROMFS 区画 {ROMFS_SIZE // 1024} KB の {size * 100 // ROMFS_SIZE}% を使います")
    if size > ROMFS_SIZE:
        log("イメージが ROMFS 区画より大きいので書き込めません")
        return 1
    return 0


def cmd_release(a) -> int:
    rc = cmd_build(a)
    if rc != 0:
        return rc
    log("ROMFS へ書き込みます(フラッシュの main.py は ROMFS を呼ぶランチャーになります)")
    a.partition = 0
    a.reset = False
    a.timeout = 180
    rc = cmd_romfs_deploy(a)
    if rc != 0:
        return rc
    log(f"ソフトリセットして、電源投入時と同じ経路(main.py)で起動を {a.boot_seconds:g} 秒確かめます")
    out = _boot_watch(a.boot_seconds)
    ok = any(RELEASE_BOOT_OK in line and RELEASE_BOOT_OK_END in line for line in out.splitlines())
    if not ok:
        log("起動の表示が見つかりませんでした。上の出力を確認してください")
        return 1
    log("書き込み完了。電源を入れるだけで動きます(プログラムは動かしたままにしています)")
    return 0


def _boot_watch(seconds: float) -> str:
    """Ctrl-C で止めてソフトリセットし、main.py の出力を seconds 秒読む(読んだ文字列を返す)"""
    import serial

    port = find_port()
    try:
        ser = serial.Serial(port, 115200, timeout=0.1)
    except serial.SerialException as e:
        die(f"ポートを開けません: {e}(他のツールがポートを使用中かもしれません)")
    got = []
    with ser:
        ser.write(b"\r\x03\x03")
        time.sleep(0.2)
        ser.reset_input_buffer()
        ser.write(b"\x04")
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            data = ser.read(512)
            if data:
                s = data.decode("utf-8", "replace")
                got.append(s)
                sys.stdout.write(s)
                sys.stdout.flush()
    print()
    return "".join(got)


def cmd_monitor(a) -> int:
    import serial  # pyserial(mpremote の依存として入っている)

    port = find_port()
    log(f"{port} を {a.seconds:g} 秒間モニタします" + ("(ソフトリセットして main.py を起動)" if a.reset else ""))
    try:
        ser = serial.Serial(port, 115200, timeout=0.1)
    except serial.SerialException as e:
        die(f"ポートを開けません: {e}(他のツールがポートを使用中かもしれません)")
    with ser:
        if a.reset:
            ser.write(b"\r\x03\x03")  # 実行中のプログラムを停止
            time.sleep(0.2)
            ser.reset_input_buffer()
            ser.write(b"\x04")  # ソフトリセット → boot.py / main.py が走る
        end = time.monotonic() + a.seconds
        while time.monotonic() < end:
            data = ser.read(512)
            if data:
                sys.stdout.write(data.decode("utf-8", "replace"))
                sys.stdout.flush()
        if a.stop:
            ser.write(b"\r\x03\x03")
    print()
    log("モニタ終了")
    return 0


def cmd_ls(a) -> int:
    return mpremote(["fs", "ls", ":" + a.path.lstrip(":")], a.timeout)[0]


def cmd_cat(a) -> int:
    return mpremote(["fs", "cat", ":" + a.path.lstrip(":")], a.timeout)[0]


def cmd_rm_main(a) -> int:
    return mpremote(["fs", "rm", ":main.py"], a.timeout)[0]


def cmd_mip(a) -> int:
    return mpremote(["mip", "install", *a.packages], a.timeout)[0]


def cmd_reset(a) -> int:
    return mpremote(["reset"], a.timeout)[0]


def cmd_repl(a) -> int:
    log("対話 REPL を開きます(Ctrl-] または Ctrl-x で終了)")
    return subprocess.call([sys.executable, "-m", "mpremote", "connect", find_port(), "repl"], cwd=ROOT, env=CHILD_ENV)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, fn, timeout=None, **kw):
        p = sub.add_parser(name, **kw)
        p.set_defaults(fn=fn)
        if timeout is not None:
            p.add_argument("-t", "--timeout", type=float, default=timeout, help=f"秒(既定 {timeout})")
        return p

    add("ports", cmd_ports)
    add("info", cmd_info, 20)
    add("run", cmd_run, 30).add_argument("file", nargs="?", default="src/main.py")
    p = add("exec", cmd_exec, 15)
    p.add_argument("code")
    p.add_argument("--no-mount", action="store_true",
                   help="src/ をマウントしない(書き込んだ本体で動かす)")
    p = add("test", cmd_test, 180)
    p.add_argument("pattern", nargs="?", default="")
    p.add_argument("--host", action="store_true", help="PC(CPython)上で実行(デバイス不要)")
    add("lint", cmd_lint)
    add("check", cmd_check)
    p = add("deploy", cmd_deploy, 120)
    p.add_argument("--reset", action="store_true", help="書き込み後にハードリセット")
    add("romfs-query", cmd_romfs_query, 20)
    p = add("romfs-build", cmd_romfs_build)
    p.add_argument("-o", "--output", default="build/app.romfs")
    p.add_argument("--no-mpy", action="store_true", help=".mpy にコンパイルせず .py のまま格納")
    p.add_argument("--march", default=ROMFS_MARCH, help=f"mpy-cross の -march(既定 {ROMFS_MARCH})")
    p = add("romfs-deploy", cmd_romfs_deploy, 180)
    p.add_argument("-p", "--partition", type=int, default=0, help="ROMFS 区画番号(既定 0)")
    p.add_argument("--no-mpy", action="store_true", help=".mpy にコンパイルせず .py のまま格納")
    p.add_argument("--march", default=ROMFS_MARCH, help=f"mpy-cross の -march(既定 {ROMFS_MARCH})")
    p.add_argument("--reset", action="store_true", help="ソフトリセットの代わりにハードリセット")
    p = add("build", cmd_build)
    p.add_argument("--skip-check", action="store_true", help="lint とテストを省く")
    p = add("release", cmd_release)
    p.add_argument("--skip-check", action="store_true", help="lint とテストを省く")
    p.add_argument("--boot-seconds", type=float, default=25, help="起動を確かめる秒(既定 25)")
    p = add("monitor", cmd_monitor)
    p.add_argument("-s", "--seconds", type=float, default=10)
    p.add_argument("--reset", action="store_true", help="ソフトリセットして main.py から観察")
    p.add_argument("--stop", action="store_true", help="終了時に Ctrl-C でプログラムを止める")
    add("ls", cmd_ls, 20).add_argument("path", nargs="?", default="/")
    add("cat", cmd_cat, 20).add_argument("path")
    add("rm-main", cmd_rm_main, 20)
    add("mip", cmd_mip, 120).add_argument("packages", nargs="+")
    add("reset", cmd_reset, 20)
    add("repl", cmd_repl)

    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
