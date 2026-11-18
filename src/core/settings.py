"""電源を切っても残る設定(/settings.json)。
スマホ(BLE)から変える項目の定義と検証、保存、config への反映。

設計は docs/ble-control.md §4。優先順位は config.py < config_local.py < /settings.json。
ファイルには既定値から変えた項目だけでなく、set() で受けた項目を書く(既定値と同じでも残す)。
ファイルの読み込みで、未知のキーや不正な値は無視して既定値のまま使い、warnings に入れる。

値の表し方(JSON 側 = 外向き / config 側 = 内向き):
    symbol   "/["           <-> ("/", "[")
    path     ["WIDE1-1"]    <-> ("WIDE1-1",)
    comment  "Pico2 APRS"   <-> b"Pico2 APRS"
その他は同じ型(int / float / bool / str / None)。

ファイル操作は opener / remove / rename で差し替えられる(テストでは偽物を渡す)。
保存は path.tmp に書いてから置き換える。置き換えの途中で電源が落ちたときのために、
読み込みは本体が無ければ tmp を読む(core.stationfile と同じ)。
"""

import json
import os

from aprs.position import valid_table
from core.txgate import valid_callsign
from tnc.ax25 import Address

PATH = "/settings.json"
VERSION = 1
MAX_COMMENT = 40
MAX_PATH = 2
MAX_MESSAGE = 6  # aprs.mice.MESSAGES の番号。7(Emergency)はスマホからは選ばせない
BEACON_MODES = ("auto", "manual")


class SettingsError(ValueError):
    def __init__(self, key, msg):
        super().__init__("%s: %s" % (key, msg))
        self.key = key
        self.msg = msg


# (キー, config の属性, 型, 引数)
# 型: "call" "int" "float" "bool" "symbol" "path" "comment" "choice"(引数は選べる文字列の tuple)
ITEMS: tuple = (
    ("callsign", "CALLSIGN", "call", None),
    ("ssid", "SSID", "int", (0, 15)),
    ("symbol", "SYMBOL", "symbol", None),
    ("path", "TX_PATH", "path", MAX_PATH),
    ("comment", "BEACON_COMMENT", "comment", MAX_COMMENT),
    ("mice_message", "BEACON_MESSAGE", "int", (0, MAX_MESSAGE)),
    ("beacon_moving_s", "BEACON_MOVING_S", "int", (30, 3600)),
    ("beacon_still_s", "BEACON_STILL_S", "int", (60, 7200)),
    ("beacon_move_m", "BEACON_MOVE_M", "float", (1.0, 1000.0)),
    ("beacon_mode", "BEACON_MODE", "choice", BEACON_MODES),
    ("tx_enabled", "TX_ENABLED", "bool", None),
    ("fx25_tx", "FX25_TX", "bool", None),
)
_BY_KEY = {it[0]: it for it in ITEMS}

# 画面(Web アプリ)向けの説明。schema() で返す
_DESC = {
    "callsign": "Own callsign without SSID (null = not set, never transmit)",
    "ssid": "SSID",
    "symbol": "Symbol table and code (2 chars)",
    "path": "Digipeater path",
    "comment": "Beacon comment",
    "mice_message": "Mic-E message (0 Off Duty ... 6 Priority)",
    "beacon_moving_s": "Beacon interval while moving (s)",
    "beacon_still_s": "Beacon interval while not moving (s)",
    "beacon_move_m": "Distance counted as moving (m)",
    "beacon_mode": "auto = beacon on the timer rules, manual = only on beacon.send",
    "tx_enabled": "Transmit permission (the beacon switch must also be on)",
    "fx25_tx": "Send with FX.25",
}


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def check(key, v):
    """外向きの値を検証して内向き(config の値)にする。不正なら SettingsError"""
    it = _BY_KEY.get(key)
    if it is None:
        raise SettingsError(key, "unknown key")
    kind, arg = it[2], it[3]
    if kind == "call":
        if v is None:
            return None
        if not isinstance(v, str) or not valid_callsign(v):
            raise SettingsError(key, "3-6 chars A-Z 0-9 with a digit, or null")
        return v
    if kind == "int":
        if not _is_int(v) or not arg[0] <= v <= arg[1]:
            raise SettingsError(key, "integer %d-%d" % arg)
        return v
    if kind == "float":
        if not (_is_int(v) or isinstance(v, float)) or not arg[0] <= v <= arg[1]:
            raise SettingsError(key, "number %g-%g" % arg)
        return float(v)
    if kind == "bool":
        if not isinstance(v, bool):
            raise SettingsError(key, "true or false")
        return v
    if kind == "symbol":
        if (not isinstance(v, str) or len(v) != 2 or not valid_table(ord(v[0]))
                or not 0x21 <= ord(v[1]) <= 0x7E):
            raise SettingsError(key, "2 chars: table (/ \\ 0-9 A-Z) and code")
        return (v[0], v[1])
    if kind == "path":
        if not isinstance(v, list) or len(v) > arg:
            raise SettingsError(key, "list of up to %d hops" % arg)
        out = []
        for p in v:
            if not isinstance(p, str) or p.endswith("*"):
                raise SettingsError(key, "hop like WIDE1-1")
            try:
                a = Address.parse(p)
            except ValueError:
                raise SettingsError(key, "hop like WIDE1-1") from None
            out.append(str(a))
        return tuple(out)
    if kind == "comment":
        if not isinstance(v, str) or len(v) > arg:
            raise SettingsError(key, "up to %d printable ASCII chars" % arg)
        for c in v:
            if not 0x20 <= ord(c) <= 0x7E:
                raise SettingsError(key, "up to %d printable ASCII chars" % arg)
        return v.encode()
    if kind == "choice":
        if not isinstance(v, str) or v not in arg:
            raise SettingsError(key, "one of " + ", ".join(arg))
        return v
    raise SettingsError(key, "bad item kind")  # ITEMS の誤り


def outward(key, v):
    """config の値 -> 外向き(JSON)の値"""
    kind = _BY_KEY[key][2]
    if kind == "symbol":
        return v[0] + v[1]
    if kind == "path":
        return list(v)
    if kind == "comment":
        return bytes(v).decode()
    if kind == "float":
        return float(v)
    return v


def schema():
    """項目の一覧(Web アプリが画面を作るため)"""
    out = []
    for key, _attr, kind, arg in ITEMS:
        d: dict = {"key": key, "desc": _DESC[key]}
        if kind == "call":
            d["type"] = "str"
            d["nullable"] = True
            d["pattern"] = "^[A-Z0-9]{3,6}$"
        elif kind in ("int", "float"):
            d["type"] = kind
            d["min"] = arg[0]
            d["max"] = arg[1]
        elif kind == "bool":
            d["type"] = "bool"
        elif kind == "symbol":
            d["type"] = "str"
            d["len"] = 2
        elif kind == "path":
            d["type"] = "list"
            d["max_items"] = arg
        elif kind == "comment":
            d["type"] = "str"
            d["max_len"] = arg
        elif kind == "choice":
            d["type"] = "str"
            d["choices"] = list(arg)
        out.append(d)
    return out


class Settings:
    def __init__(self, cfg, path=PATH, opener=open, remove=os.remove, rename=os.rename, log=None):
        """cfg: config モジュール(または同じ属性を持つもの)。作った時点の値を既定値として覚える
        (config.py + config_local.py の値。reset() でここへ戻す)"""
        self.cfg = cfg
        self.path = path
        self.tmp = path + ".tmp"
        self.opener = opener
        self.remove = remove
        self.rename = rename
        self.log = log or (lambda *a: None)
        self.defaults = {key: getattr(cfg, attr) for key, attr, _k, _a in ITEMS}
        self.saved = {}  # ファイルにある(反映した)項目: キー -> 外向きの値
        self.warnings = []
        self.listeners = []  # 反映のあとに呼ぶ f(変わったキーの list)

    # ---- 読む
    def values(self):
        """今の全項目(外向き)"""
        return {key: outward(key, getattr(self.cfg, attr)) for key, attr, _k, _a in ITEMS}

    def _read(self, path):
        f = self.opener(path, "rb")
        try:
            return f.read()
        finally:
            f.close()

    def load(self):
        """ファイルを読んで config に反映する。ファイルが無ければ何もしない。
        壊れたファイルや不正な項目は無視して warnings に入れる。反映した項目のキーの list を返す"""
        self.warnings = []
        data = None
        for p in (self.path, self.tmp):
            try:
                data = self._read(p)
                break
            except OSError:
                pass
        if data is None:
            return []
        try:
            doc = json.loads(data)
            vals = doc["values"]
            if not isinstance(vals, dict):
                raise ValueError("values is not an object")
        except (ValueError, KeyError, TypeError) as e:
            self.warnings.append("%s: unreadable (%s)" % (self.path, e))
            self.log("[settings] %s" % self.warnings[-1])
            return []
        applied = {}
        for key, v in vals.items():
            try:
                applied[key] = check(key, v)
            except SettingsError as e:
                self.warnings.append(str(e))
                self.log("[settings] ignored %s" % e)
        for key, v in applied.items():
            setattr(self.cfg, _BY_KEY[key][1], v)
            self.saved[key] = outward(key, v)
        keys = list(applied)
        if keys:
            self.log("[settings] loaded %s" % ", ".join(sorted(keys)))
        return keys

    # ---- 書く
    def _write(self, saved):
        data = json.dumps({"version": VERSION, "values": saved})
        f = self.opener(self.tmp, "wb")
        try:
            f.write(data.encode())
        finally:
            f.close()
        try:
            self.remove(self.path)
        except OSError:
            pass
        self.rename(self.tmp, self.path)

    def set(self, values):
        """values: {キー: 外向きの値}。全部を検証し、1 つでも不正なら何も変えず SettingsError。
        ファイルに書けなければ OSError(このときも config は変えない)。
        変わったキーの list を返す(同じ値でもファイルには残す)"""
        if not isinstance(values, dict) or not values:
            raise SettingsError("values", "object with at least one item")
        conv = {}
        for key, v in values.items():
            conv[key] = check(key, v)
        saved = dict(self.saved)
        for key, v in conv.items():
            saved[key] = outward(key, v)
        self._write(saved)
        self.saved = saved
        changed = []
        for key, v in conv.items():
            attr = _BY_KEY[key][1]
            if getattr(self.cfg, attr) != v:
                changed.append(key)
            setattr(self.cfg, attr, v)
        self._notify(changed)
        return changed

    def reset(self):
        """ファイルを消して既定値(作った時点の値)に戻す。変わったキーの list を返す"""
        for p in (self.path, self.tmp):
            try:
                self.remove(p)
            except OSError:
                pass
        self.saved = {}
        changed = []
        for key, attr, _k, _a in ITEMS:
            v = self.defaults[key]
            if getattr(self.cfg, attr) != v:
                changed.append(key)
            setattr(self.cfg, attr, v)
        self._notify(changed)
        return changed

    def _notify(self, changed):
        if changed:
            for f in self.listeners:
                f(changed)
