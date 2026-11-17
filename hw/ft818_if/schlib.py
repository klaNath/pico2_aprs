"""KiCad 9 の回路図(.kicad_sch)を書き出す小さな道具。gen_sch.py から使う。"""

import math
import os
import uuid

from sexpr import dump, find, load_symbol, pins

LIBDIR = os.environ.get("KICAD_SYMBOL_DIR", r"C:/Program Files/KiCad/9.0/share/kicad/symbols")
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "ft818_if.kicad_sch")
PROJECT = "ft818_if"
U = 2.54
OX = 9 # 全体を右へずらす(左端にコネクタの説明を書く余白)

_n = [0]


def uid():
    _n[0] += 1
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "pico2_aprs/ft818_if/%d" % _n[0]))


ROOT = uid()


def mm(v):
    s = ("%.4f" % (v * U)).rstrip("0").rstrip(".")
    return s or "0"


def xy(x, y):
    return "%s %s" % (mm(x + OX), mm(y))


FONT = "(effects (font (size 1.27 1.27))%s)"


class Sch:
    def __init__(self):
        self.libs = {}
        self.items = []
        self.segs = []
        self.pin_pts = []
        self.pwr = 0
        self.flg = 0

    # ---- 部品 ----
    def place(self, lib_id, ref, value, x, y, rot=0, mirror=None, unit=1,
              ref_at=None, val_at=None, hide_value=False):
        if lib_id not in self.libs:
            lib, name = lib_id.split(":")
            self.libs[lib_id] = load_symbol(LIBDIR, lib, name)
        sd = self.libs[lib_id]
        fang = 90 if rot in (90, 270) else 0

        def prop(name, val, at, hide=False):
            if at is None:
                at = (x, y, None)
            px, py, just = at
            eff = ""
            if just:
                eff += " (justify %s)" % just
            if hide:
                eff += " (hide yes)"
            return '\t\t(property "%s" "%s" (at %s %d) %s)\n' % (
                name, val, xy(px, py), fang, FONT % eff)

        out = {}
        s = '\t(symbol (lib_id "%s") (at %s %d)' % (lib_id, xy(x, y), rot)
        if mirror:
            s += " (mirror %s)" % mirror
        s += " (unit %d)\n\t\t(exclude_from_sim no) (in_bom yes) (on_board yes) (dnp no)\n" % unit
        s += '\t\t(uuid "%s")\n' % uid()
        hide_ref = ref.startswith("#")
        s += prop("Reference", ref, ref_at, hide_ref)
        s += prop("Value", value, val_at, hide_value)
        fp = [p[2] for p in find(sd, "property") if p[1] == "Footprint"]
        s += prop("Footprint", fp[0] if fp else "", None, True)
        s += prop("Datasheet", "~", None, True)
        a = math.radians(rot)
        for (pu, num), (px, py, _ang, _name) in sorted(pins(sd).items()):
            if pu not in (0, unit):
                continue
            if mirror == "y":
                px = -px
            elif mirror == "x":
                py = -py
            rx = px * math.cos(a) - py * math.sin(a)
            ry = px * math.sin(a) + py * math.cos(a)
            pt = (round(x + rx / U, 3), round(y - ry / U, 3))
            out[num] = pt
            self.pin_pts.append(pt)
            s += '\t\t(pin "%s" (uuid "%s"))\n' % (num, uid())
        s += '\t\t(instances (project "%s" (path "/%s" (reference "%s") (unit %d))))\n\t)\n' % (
            PROJECT, ROOT, ref, unit)
        self.items.append(s)
        return out

    def H(self, lib_id, ref, value, x, y):
        """横向きの 2 端子部品。戻り値 (左端, 右端)"""
        d = 1.6 if lib_id == "Device:C" else 1.1  # コンデンサは極板が縦に長い
        p = self.place(lib_id, ref, value, x, y, rot=90,
                       ref_at=(x, y - d, None), val_at=(x, y + d, None))
        return p["1"], p["2"]

    def V(self, lib_id, ref, value, x, y):
        """縦向きの 2 端子部品。戻り値 (上端, 下端)"""
        p = self.place(lib_id, ref, value, x, y,
                       ref_at=(x + 0.9, y - 0.5, "left"), val_at=(x + 0.9, y + 0.5, "left"))
        return p["1"], p["2"]

    def power(self, name, x, y, rot=0):
        self.pwr += 1
        d = {0: (0, 1.5, None), 90: (2.8, 0, None), 270: (-1.6, 0, "right"), 180: (0, -1.5, None)}
        if name in ("+3V3",):
            d = {0: (0, -1.5, None), 270: (1.6, 0, "left")}
        dx, dy, just = d[rot]
        # 回転した電源シンボルの値は水平に見せたいので、角度の補正を place に任せず 0 で書く
        self.place("power:" + name, "#PWR%02d" % self.pwr, name, x, y, rot=rot,
                   val_at=(x + dx, y + dy, just))

    def flag(self, x, y):
        self.flg += 1
        self.place("power:PWR_FLAG", "#FLG%02d" % self.flg, "PWR_FLAG", x, y,
                   val_at=(x, y - 1.6, None))

    # ---- 配線 ----
    def wire(self, *pts):
        for a, b in zip(pts, pts[1:]):
            assert a[0] == b[0] or a[1] == b[1], (a, b)
            self.segs.append((tuple(a), tuple(b)))

    def emit_wires(self):
        """T 字の接続点で配線を分割してから書き出す(KiCad は配線の途中の接続点では結線しない)"""
        pts = set(self.pin_pts)
        for a, b in self.segs:
            pts.add(a)
            pts.add(b)
        segs = []
        for a, b in self.segs:
            cut = sorted([p for p in pts if self._interior(p, (a, b))] + [a, b])
            if a > b:
                cut.reverse()
            segs += list(zip(cut, cut[1:]))
        ends = {}
        for a, b in segs:
            self.items.append('\t(wire (pts (xy %s) (xy %s)) (stroke (width 0) (type default)) (uuid "%s"))\n'
                              % (xy(*a), xy(*b), uid()))
            for p in (a, b):
                ends[p] = ends.get(p, 0) + 1
        for p in self.pin_pts:
            if p in ends:
                ends[p] += 1
        for p in sorted(p for p, n in ends.items() if n >= 3):
            self.items.append('\t(junction (at %s) (diameter 0) (color 0 0 0 0) (uuid "%s"))\n' % (xy(*p), uid()))

    @staticmethod
    def _interior(p, seg):
        (x1, y1), (x2, y2) = seg
        if p in seg:
            return False
        if x1 == x2 == p[0]:
            return min(y1, y2) < p[1] < max(y1, y2)
        if y1 == y2 == p[1]:
            return min(x1, x2) < p[0] < max(x1, x2)
        return False

    def nc(self, pt):
        self.items.append('\t(no_connect (at %s) (uuid "%s"))\n' % (xy(*pt), uid()))

    def label(self, name, x, y, ang=0):
        self.items.append('\t(label "%s" (at %s %d) %s (uuid "%s"))\n' % (
            name, xy(x, y), ang, FONT % " (justify left bottom)", uid()))

    def glabel(self, name, x, y, ang=0, shape="input"):
        just = "left" if ang in (0, 90) else "right"
        self.items.append(
            '\t(global_label "%s" (shape %s) (at %s %d) %s (uuid "%s")\n'
            '\t\t(property "Intersheetrefs" "${INTERSHEET_REFS}" (at 0 0 0) %s)\n\t)\n' % (
                name, shape, xy(x, y), ang, FONT % (" (justify %s)" % just), uid(),
                FONT % " (hide yes)"))

    def text(self, s, x, y, size=1.27, just="left bottom", bold=False):
        s = s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        self.items.append(
            '\t(text "%s" (exclude_from_sim no) (at %s 0) (effects (font (size %g %g)%s) (justify %s)) (uuid "%s"))\n'
            % (s, xy(x, y), size, size, " (bold yes)" if bold else "", just, uid()))

    def box(self, x1, y1, x2, y2):
        self.items.append(
            '\t(rectangle (start %s) (end %s) (stroke (width 0) (type dash)) (fill (type none)) (uuid "%s"))\n'
            % (xy(x1, y1), xy(x2, y2), uid()))

    def write(self, path):
        self.emit_wires()
        head = (
            '(kicad_sch (version 20250114) (generator "eeschema") (generator_version "9.0")\n'
            '\t(uuid "%s")\n\t(paper "A3")\n'
            '\t(title_block (title "無線機インターフェース FT-818ND / FT3D・FT5D")\n'
            '\t\t(date "2026-11-13") (rev "0.3")\n'
            '\t\t(comment 1 "1200bps AFSK 専用。定数の根拠: docs/spice/*.cir")\n'
            '\t\t(comment 2 "生成元: hw/ft818_if/gen_sch.py")\n\t)\n' % ROOT)
        libs = "\t(lib_symbols\n"
        for k in sorted(self.libs):
            libs += "\t\t" + dump(self.libs[k], 2) + "\n"
        libs += "\t)\n"
        tail = '\t(sheet_instances (path "/" (page "1")))\n\t(embedded_fonts no)\n)\n'
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(head + libs + "".join(self.items) + tail)
