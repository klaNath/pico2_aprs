"""core.settings(/settings.json の設定)のテスト。PC と実機の両方で動く。
ファイルは偽物(フラッシュに書かない)。コールサインはすべて架空の例。"""

import json

from core import settings as S
from core.settings import Settings, SettingsError


class Cfg:
    """config モジュールの代わり(config.py + config_local.py の値に相当)"""

    def __init__(self):
        self.CALLSIGN = None
        self.SSID: int = 7
        self.SYMBOL = ("/", "[")
        self.TX_PATH = ("WIDE1-1", "WIDE2-1")
        self.BEACON_COMMENT = b"Pico2 APRS"
        self.BEACON_MESSAGE = 1
        self.BEACON_MOVING_S = 90
        self.BEACON_STILL_S = 600
        self.BEACON_MOVE_M = 10.0
        self.BEACON_MODE = "auto"
        self.TX_ENABLED = False
        self.FX25_TX = False


class FakeFile:
    def __init__(self, fs, path, data=b""):
        self.fs = fs
        self.path = path
        self.data = data
        self.buf = []

    def read(self):
        return self.data

    def write(self, b):
        if self.fs.fail:
            raise OSError(28)  # ENOSPC
        self.buf.append(bytes(b))

    def close(self):
        if self.buf:
            self.fs.files[self.path] = b"".join(self.buf)


class FakeFs:
    def __init__(self):
        self.files = {}
        self.fail = False
        self.writes = 0

    def open(self, path, mode):
        if mode == "rb":
            if path not in self.files:
                raise OSError(2)
            return FakeFile(self, path, self.files[path])
        assert mode == "wb"
        self.writes += 1
        self.files[path] = b""
        return FakeFile(self, path)

    def remove(self, path):
        if path not in self.files:
            raise OSError(2)
        del self.files[path]

    def rename(self, a, b):
        self.files[b] = self.files.pop(a)


def make(fs=None, cfg=None):
    fs = fs if fs is not None else FakeFs()
    cfg = cfg if cfg is not None else Cfg()
    st = Settings(cfg, "/settings.json", opener=fs.open, remove=fs.remove, rename=fs.rename)
    return st, fs, cfg


def put(fs, values, path="/settings.json"):
    fs.files[path] = json.dumps({"version": 1, "values": values}).encode()


def bad(key, v):
    try:
        S.check(key, v)
    except SettingsError as e:
        assert e.key == key
        return True
    return False


def test_check_converts_to_config_values():
    assert S.check("callsign", "Q1TEST") == "Q1TEST"
    assert S.check("callsign", None) is None
    assert S.check("symbol", "/>") == ("/", ">")
    assert S.check("symbol", "R&") == ("R", "&")  # オーバーレイ
    assert S.check("path", ["WIDE1-1"]) == ("WIDE1-1",)
    assert S.check("path", ["wide2-2", "Q1TEST-1"]) == ("WIDE2-2", "Q1TEST-1")
    assert S.check("path", []) == ()
    assert S.check("comment", "hello 73") == b"hello 73"
    assert S.check("beacon_move_m", 25) == 25.0
    assert S.check("tx_enabled", True) is True
    assert S.check("beacon_mode", "auto") == "auto" and S.check("beacon_mode", "manual") == "manual"
    for key, v in (("ssid", 0), ("ssid", 15), ("beacon_moving_s", 30), ("beacon_still_s", 7200)):
        assert S.check(key, v) == v


def test_check_rejects_bad_values():
    assert bad("callsign", "Q1")  # 短い
    assert bad("callsign", "Q1TEST7")  # 長い
    assert bad("callsign", "q1test")  # 小文字(大文字に変えない。docs/ble-protocol.md §9.1)
    assert bad("callsign", " Q1TEST") and bad("callsign", "Q1TEST ")  # 空白(trim しない)
    assert bad("callsign", "Q1TEST-7")  # SSID は ssid に分ける
    assert bad("callsign", "QATEST")  # 数字が無い
    assert bad("callsign", 5)
    assert bad("ssid", 16) and bad("ssid", -1) and bad("ssid", 1.0) and bad("ssid", True)
    assert bad("symbol", "/") and bad("symbol", "a[") and bad("symbol", "/ ")
    assert bad("path", "WIDE1-1")  # list でない
    assert bad("path", ["WIDE1-1", "WIDE2-1", "WIDE3-1"])  # 3 個
    assert bad("path", ["WIDE1-16"]) and bad("path", ["WIDE1-1*"]) and bad("path", [""])
    assert bad("comment", "x" * 41) and bad("comment", "tab\there") and bad("comment", "é")
    assert bad("mice_message", 7)  # Emergency はスマホから選ばせない
    assert bad("beacon_moving_s", 29) and bad("beacon_still_s", 7201)
    assert bad("beacon_move_m", 0.5) and bad("beacon_move_m", "10")
    assert bad("tx_enabled", 1) and bad("fx25_tx", None)
    assert bad("beacon_mode", "Manual") and bad("beacon_mode", "") and bad("beacon_mode", None)
    assert bad("beacon_mode", 1) and bad("beacon_mode", ["manual"])
    assert bad("no_such_key", 1)


def test_moving_and_still_intervals_are_separate_items():
    keys = [d["key"] for d in S.schema()]
    assert "beacon_moving_s" in keys and "beacon_still_s" in keys
    st, _fs, cfg = make()
    st.set({"beacon_moving_s": 120, "beacon_still_s": 1800})
    assert cfg.BEACON_MOVING_S == 120 and cfg.BEACON_STILL_S == 1800


def test_schema_covers_every_item():
    sch = S.schema()
    assert [d["key"] for d in sch] == [it[0] for it in S.ITEMS]
    by = {d["key"]: d for d in sch}
    assert by["ssid"]["type"] == "int" and by["ssid"]["min"] == 0 and by["ssid"]["max"] == 15
    assert by["callsign"]["nullable"] is True
    assert by["comment"]["max_len"] == S.MAX_COMMENT
    assert by["beacon_mode"]["type"] == "str" and by["beacon_mode"]["choices"] == ["auto", "manual"]
    json.dumps(sch)  # そのまま JSON にできる


def test_values_are_outward():
    st, _fs, _cfg = make()
    v = st.values()
    assert v["symbol"] == "/[" and v["path"] == ["WIDE1-1", "WIDE2-1"]
    assert v["comment"] == "Pico2 APRS" and v["callsign"] is None and v["beacon_move_m"] == 10.0
    json.dumps(v)


def test_load_without_file_changes_nothing():
    st, _fs, cfg = make()
    assert st.load() == []
    assert cfg.SSID == 7 and st.warnings == []


def test_set_writes_file_and_applies():
    st, fs, cfg = make()
    got = []
    st.listeners.append(got.append)
    changed = st.set({"callsign": "Q1TEST", "ssid": 9, "comment": "new", "tx_enabled": True})
    assert sorted(changed) == ["callsign", "comment", "ssid", "tx_enabled"]
    assert got == [changed]
    assert cfg.CALLSIGN == "Q1TEST" and cfg.SSID == 9 and cfg.BEACON_COMMENT == b"new"
    assert cfg.TX_ENABLED is True
    assert "/settings.json.tmp" not in fs.files and fs.writes == 1  # 1 回の set で 1 回だけ書く
    doc = json.loads(fs.files["/settings.json"])
    assert doc["version"] == 1 and doc["values"]["ssid"] == 9 and doc["values"]["comment"] == "new"
    # 2 回目の set は前の項目を残して足す
    st.set({"path": ["WIDE1-1"]})
    doc = json.loads(fs.files["/settings.json"])
    assert doc["values"]["ssid"] == 9 and doc["values"]["path"] == ["WIDE1-1"]


def test_set_same_value_is_saved_but_not_changed():
    st, fs, _cfg = make()
    got = []
    st.listeners.append(got.append)
    assert st.set({"ssid": 7}) == []
    assert got == []  # 変わっていなければ知らせない
    assert json.loads(fs.files["/settings.json"])["values"] == {"ssid": 7}


def test_set_is_all_or_nothing():
    st, fs, cfg = make()
    try:
        st.set({"ssid": 3, "beacon_still_s": 10})
    except SettingsError as e:
        assert e.key == "beacon_still_s"
    else:
        raise AssertionError("no error")
    assert cfg.SSID == 7 and fs.files == {} and fs.writes == 0
    for v in ({}, None, [1]):
        try:
            st.set(v)
        except SettingsError:
            pass
        else:
            raise AssertionError("no error for %r" % (v,))


def test_write_error_keeps_config_and_old_file():
    st, fs, cfg = make()
    st.set({"ssid": 3})
    old = fs.files["/settings.json"]
    fs.fail = True
    try:
        st.set({"ssid": 4})
    except OSError:
        pass
    else:
        raise AssertionError("no error")
    assert cfg.SSID == 3 and fs.files["/settings.json"] == old
    assert st.saved == {"ssid": 3}


def test_load_applies_file_over_defaults():
    fs = FakeFs()
    cfg = Cfg()
    cfg.SSID = 5  # config_local.py で変えた値に相当
    put(fs, {"callsign": "Q1TEST", "symbol": "/>", "path": ["WIDE2-1"], "beacon_move_m": 20})
    st, _fs, _cfg = make(fs, cfg)
    assert sorted(st.load()) == ["beacon_move_m", "callsign", "path", "symbol"]
    assert cfg.CALLSIGN == "Q1TEST" and cfg.SYMBOL == ("/", ">") and cfg.TX_PATH == ("WIDE2-1",)
    assert cfg.BEACON_MOVE_M == 20.0 and cfg.SSID == 5  # ファイルに無い項目はそのまま
    assert st.defaults["ssid"] == 5 and st.defaults["callsign"] is None
    assert st.warnings == []


def test_load_ignores_unknown_and_bad_items():
    fs = FakeFs()
    put(fs, {"ssid": 99, "mode": "si5351", "comment": "ok"})
    st, _fs, cfg = make(fs)
    assert st.load() == ["comment"]
    assert cfg.SSID == 7 and cfg.BEACON_COMMENT == b"ok"
    assert len(st.warnings) == 2
    assert not hasattr(cfg, "MODE")
    assert st.saved == {"comment": "ok"}  # 不正な項目は次の保存で消える


def test_load_broken_file_uses_defaults():
    for data in (b"{not json", b"[]", b'{"values": 3}', b'{"version": 1}'):
        fs = FakeFs()
        fs.files["/settings.json"] = data
        st, _fs, cfg = make(fs)
        assert st.load() == []
        assert cfg.SSID == 7 and len(st.warnings) == 1


def test_load_reads_tmp_when_main_file_missing():
    fs = FakeFs()
    put(fs, {"ssid": 2}, "/settings.json.tmp")  # 置き換えの途中で電源が落ちた
    st, _fs, cfg = make(fs)
    assert st.load() == ["ssid"] and cfg.SSID == 2


def test_reset_restores_defaults_and_removes_file():
    fs = FakeFs()
    put(fs, {"ssid": 2, "tx_enabled": True})
    st, _fs, cfg = make(fs)
    st.load()
    got = []
    st.listeners.append(got.append)
    assert sorted(st.reset()) == ["ssid", "tx_enabled"]
    assert cfg.SSID == 7 and cfg.TX_ENABLED is False
    assert fs.files == {} and st.saved == {}
    assert len(got) == 1 and sorted(got[0]) == ["ssid", "tx_enabled"]
    assert st.reset() == []  # もう一度: 変化なし、ファイルが無くても失敗しない


def test_roundtrip_through_file():
    st, fs, _cfg = make()
    st.set({"callsign": "Q1TEST", "symbol": "\\>", "comment": "a b", "beacon_move_m": 12.5,
            "fx25_tx": True, "beacon_mode": "manual"})
    st2, _fs, cfg2 = make(fs)
    st2.load()
    assert st2.values() == st.values()
    assert cfg2.SYMBOL == ("\\", ">") and cfg2.FX25_TX is True and cfg2.BEACON_MODE == "manual"
