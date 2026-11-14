"""回路図の検査: 配線の交差(接続なし)の一覧と、書き出したネットリストの全ネットを表示する。

    python hw/ft818_if/check.py   (先に gen_sch.py と kicad-cli sch export netlist を実行しておく)
"""

import os
import re

import gen_sch

HERE = os.path.dirname(os.path.abspath(__file__))


def crossings(segs):
    out = []
    for i, (a, b) in enumerate(segs):
        for c, d in segs[i + 1:]:
            if a[1] == b[1] and c[0] == d[0]:
                h, v = (a, b), (c, d)
            elif a[0] == b[0] and c[1] == d[1]:
                h, v = (c, d), (a, b)
            else:
                continue
            x, y = v[0][0], h[0][1]
            if (min(h[0][0], h[1][0]) < x < max(h[0][0], h[1][0])
                    and min(v[0][1], v[1][1]) < y < max(v[0][1], v[1][1])):
                out.append((x, y))
    return out


def nets(path):
    t = open(path, encoding="utf-8").read()
    t = t[t.index("(nets"):]
    for blk in t.split("(net (code")[1:]:
        name = re.search(r'\(name "([^"]*)"', blk).group(1)
        nodes = re.findall(r'\(node \(ref "([^"]+)"\) \(pin "([^"]+)"\)', blk)
        yield name, ["%s.%s" % n for n in nodes]


if __name__ == "__main__":
    print("crossings:", crossings(gen_sch.build().segs))
    for name, nodes in nets(os.path.join(HERE, "ft818_if.net")):
        print(name, ":", " ".join(nodes))
