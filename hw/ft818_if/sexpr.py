"""KiCad の S 式の最小パーサ/ライタと、シンボルライブラリからの取り出し(extends を展開)。"""

import copy
import re


class Sym(str):
    """引用符なしのトークン"""


BS = chr(92)
_TOK = re.compile(r'\s*(\(|\)|"(?:' + BS + BS + r'.|[^"' + BS + BS + r'])*"|[^\s()"]+)')


def _unescape(s):
    out = []
    i = 0
    while i < len(s):
        c = s[i]
        if c == BS and i + 1 < len(s):
            n = s[i + 1]
            out.append("\n" if n == "n" else n)
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _escape(s):
    return s.replace(BS, BS + BS).replace('"', BS + '"').replace("\n", BS + "n")


def parse(text):
    stack = [[]]
    for m in _TOK.finditer(text):
        t = m.group(1)
        if t == "(":
            stack.append([])
        elif t == ")":
            x = stack.pop()
            stack[-1].append(x)
        elif t[0] == '"':
            stack[-1].append(_unescape(t[1:-1]))
        else:
            stack[-1].append(Sym(t))
    return stack[0][0]


def dump(x, ind=0):
    if isinstance(x, list):
        if all(not isinstance(i, list) for i in x):
            return "(" + " ".join(dump(i) for i in x) + ")"
        pad = "\t" * (ind + 1)
        out = "(" + dump(x[0])
        for i in x[1:]:
            if isinstance(i, list):
                out += "\n" + pad + dump(i, ind + 1)
            else:
                out += " " + dump(i)
        return out + "\n" + "\t" * ind + ")"
    if isinstance(x, Sym):
        return str(x)
    if isinstance(x, str):
        return '"' + _escape(x) + '"'
    if isinstance(x, float):
        s = ("%.4f" % x).rstrip("0").rstrip(".")
        return "0" if s in ("-0", "") else s
    return str(x)


def find(node, key):
    return [c for c in node[1:] if isinstance(c, list) and c and c[0] == key]


_libs = {}


def load_symbol(libdir, lib, name):
    """lib:name のシンボル定義(extends 展開済み、名前は 'lib:name')を返す"""
    if lib not in _libs:
        with open("%s/%s.kicad_sym" % (libdir, lib), encoding="utf-8") as f:
            _libs[lib] = {s[1]: s for s in find(parse(f.read()), "symbol")}
    s = _libs[lib][name]
    ext = find(s, "extends")
    if ext:
        base = _libs[lib][ext[0][1]]
        new = copy.deepcopy(base)
        new[1] = name
        props = {p[1]: p for p in find(s, "property")}
        for i, c in enumerate(new):
            if isinstance(c, list) and c[0] == "property" and c[1] in props:
                new[i] = copy.deepcopy(props.pop(c[1]))
            elif isinstance(c, list) and c[0] == "symbol":
                c[1] = name + c[1][len(base[1]):]
        idx = max(i for i, c in enumerate(new) if isinstance(c, list) and c[0] == "property") + 1
        for p in props.values():
            new.insert(idx, copy.deepcopy(p))
            idx += 1
        s = new
    else:
        s = copy.deepcopy(s)
    s[1] = "%s:%s" % (lib, name)
    return s


def pins(symdef):
    """{(unit, number): (x, y, angle, name)}(シンボル座標、Y は上向き)"""
    out = {}
    for sub in find(symdef, "symbol"):
        unit = int(sub[1].rsplit("_", 2)[-2])
        for p in find(sub, "pin"):
            at = find(p, "at")[0]
            out[(unit, find(p, "number")[0][1])] = (
                float(at[1]), float(at[2]), float(at[3]) if len(at) > 3 else 0.0,
                find(p, "name")[0][1])
    return out
