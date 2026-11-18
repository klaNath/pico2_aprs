"""BLE の状態アイコン(ble_icons.h、make_icons.py が作る C の配列)-> src/ui/ble_icons.py

.h の値は普通の RGB565(上位バイトが先)。framebuf の RGB565 にそのまま blit できるよう、
1 画素を上位・下位の順の 2 バイトに並べる(hw.rgb565.rgb と同じ並び)。
使うものだけ出す: 24px(320x240 用)と 20px(240x135 用)の adv_*・connected。

    python tools/icons/h2py.py
"""

import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / "ble_icons.h"
OUT = HERE.parent.parent / "src" / "ui" / "ble_icons.py"
SIZES = (24, 20)
LINE_BYTES = 24


def parse(text):
    """{名前: (一辺, [値, ...])}"""
    out = {}
    for m in re.finditer(r"uint16_t (\w+)\[(\d+)\*(\d+)\] = \{(.*?)\};", text, re.S):
        n = int(m.group(2))
        vals = [int(v, 16) for v in re.findall(r"0x[0-9A-Fa-f]{4}", m.group(4))]
        if len(vals) != n * int(m.group(3)):
            raise SystemExit("%s: 画素数が合わない" % m.group(1))
        out[m.group(1)] = (n, vals)
    return out


def to_bytes(vals):
    b = bytearray()
    for v in vals:
        b += bytes((v >> 8, v & 0xFF))
    return bytes(b)


def literal(data, indent="    "):
    lines = []
    for k in range(0, len(data), LINE_BYTES):
        lines.append(indent + 'b"' + "".join("\\x%02x" % c for c in data[k:k + LINE_BYTES]) + '"')
    return "(\n" + "\n".join(lines) + "\n" + indent[:-4] + ")"


def main():
    icons = parse(SRC.read_text(encoding="utf-8"))
    out = [
        '"""BLE の状態アイコン(RGB565、framebuf の並び。背景は黒)。',
        "",
        "生成物。手で編集しない。tools/icons/ble_icons.h(make_icons.py が作る)から",
        "python tools/icons/h2py.py で作る。",
        "ADV_n: 広告中のアニメーションのコマ(弧が増えていく)、CONN_n: 認証済みで接続中。n は一辺の画素数",
        '"""',
        "",
    ]
    for n in SIZES:
        advs = sorted(k for k in icons if re.fullmatch(r"ble_adv_\d+_%d" % n, k))
        frames = []
        for k in advs:
            size, vals = icons[k]
            assert size == n
            frames.append(literal(to_bytes(vals), " " * 8))
        out.append("ADV_%d = (" % n)
        for f in frames:
            out.append("    " + f + ",")
        out.append(")")
        size, vals = icons["ble_connected_%d" % n]
        out.append("CONN_%d = %s" % (n, literal(to_bytes(vals))))
        out.append("")
    OUT.write_text("\n".join(out), encoding="utf-8", newline="\n")
    print("wrote", OUT.relative_to(HERE.parent.parent))


if __name__ == "__main__":
    main()
