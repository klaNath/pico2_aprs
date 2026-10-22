#!/usr/bin/env python3
"""開発環境のセットアップ(初回に一度だけ実行)

    python3 tools/bootstrap.py                 # Pico 2 用(既定)
    python3 tools/bootstrap.py --board rpi_pico2_w
    python3 tools/bootstrap.py --mp-version 1.28.0   # ファームウェアのバージョンに合わせる

やること:
  1. .venv を作成
  2. requirements-dev.txt(mpremote, ruff, pyright)をインストール
  3. MicroPython の型スタブを typings/ にインストール(VSCode の補完・型チェック用)
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / ".venv"
TYPINGS = ROOT / "typings"


def run(cmd: list[str]) -> None:
    print("$", " ".join(cmd), flush=True)
    subprocess.check_call(cmd, cwd=ROOT)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--board", default="rpi_pico2", help="rpi_pico2 / rpi_pico2_w など(既定 rpi_pico2)")
    ap.add_argument("--mp-version", default="", help="スタブのバージョン(例 1.28.0)。省略時は最新")
    a = ap.parse_args()

    if sys.version_info < (3, 9):
        print("Python 3.9 以上が必要です。")
        return 1

    if not VENV.exists():
        print("[1/3] .venv を作成")
        venv.EnvBuilder(with_pip=True).create(VENV)
    else:
        print("[1/3] .venv は既にあります")
    py = str(VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))

    print("[2/3] 開発ツールをインストール")
    run([py, "-m", "pip", "install", "-q", "--upgrade", "pip"])
    run([py, "-m", "pip", "install", "-q", "-r", "requirements-dev.txt"])

    print("[3/3] MicroPython スタブを typings/ にインストール")
    pkg = f"micropython-rp2-{a.board}-stubs"
    if a.mp_version:
        pkg += f"=={a.mp_version}.*"
    if TYPINGS.exists():
        shutil.rmtree(TYPINGS)
    run([py, "-m", "pip", "install", "-q", "--no-user", "--target", str(TYPINGS), pkg])

    print()
    print("完了。次のステップ:")
    print("  1. VSCode で「Python: Select Interpreter」→ .venv を選択")
    print("  2. Pico を USB 接続して:  python tools/dev.py info")
    print("  3. 動作確認:              python tools/dev.py test")
    return 0


if __name__ == "__main__":
    sys.exit(main())
