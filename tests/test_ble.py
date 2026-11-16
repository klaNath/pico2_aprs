"""hw.ble(BLE の層)のテスト。偽の bluetooth.BLE を使うので PC でも実機でも動く。
(実機の BLE を使う試験は test_hw_ble.py)。コールサインと座標はすべて架空の例。"""

import json
import sys
import time

try:
    from typing import Any  # noqa: F401  (型注釈用。MicroPython には無い)
except ImportError:
    pass

from test_remote import Src
from test_settings import Cfg, FakeFs

from core.remote import Remote
from core.settings import Settings


def _install_bluetooth():
    """PC には bluetooth モジュールが無いので、UUID だけの代用品を入れる(実機では本物を使う)"""
    try:
        import bluetooth  # noqa: F401  # pyright: ignore[reportMissingImports]

        return
    except ImportError:
        pass
    import types  # PC(CPython)だけ

    m = types.ModuleType("bluetooth")

    class UUID:
        def __init__(self, s):
            self.s = s

    m.UUID = UUID  # type: ignore[attr-defined]
    m.BLE = object  # type: ignore[attr-defined]
    sys.modules["bluetooth"] = m


_install_bluetooth()

from hw import ble as B  # noqa: E402


class FakeBle:
    def __init__(self):
        self.irq_fn: "Any" = None
        self.cfg = {}
        self.active_ = False
        self.adv: "Any" = None
        self.notified = []
        self.notify_fail = 0  # この回数だけ gatts_notify が OSError
        self.rx = b""
        self.passkeys = []
        self.disconnected = []
        self.buf_size = None

    def active(self, v=None):
        if v is None:
            return self.active_
        self.active_ = v

    def irq(self, fn):
        self.irq_fn = fn

    def config(self, *a, **kw):
        if a:
            if a[0] == "mac":
                return (0, bytes([1, 2, 3, 4, 0xAB, 0xCD]))
            raise ValueError("unknown config param")
        self.cfg.update(kw)

    def gatts_register_services(self, services):
        ((_uuid, chars),) = services
        assert len(chars) == 2
        self.chars: "Any" = chars
        return ((10, 12),)

    def gatts_set_buffer(self, h, n, append=False):
        self.buf_size = (h, n)

    def gatts_read(self, h):
        assert h == 12
        return self.rx

    def gatts_notify(self, conn, h, data):
        assert h == 10
        if self.notify_fail:
            self.notify_fail -= 1
            raise OSError(12)
        self.notified.append((conn, bytes(data)))

    def gap_advertise(self, interval, adv_data=None, resp_data=None, **kw):
        self.adv = None if interval is None else (interval, adv_data, resp_data)

    def gap_passkey(self, conn, action, key):
        self.passkeys.append((conn, action, key))

    def gap_disconnect(self, conn):
        self.disconnected.append(conn)

    # テストから使う(中央装置の動き)
    def ev(self, event, *data):
        return self.irq_fn(event, data)

    def write(self, conn, data):
        self.rx = data
        self.ev(B.IRQ_GATTS_WRITE, conn, 12)


class Link:
    """remote の送信先(Ble.write)から出た行を JSON にして集める。BLE の通知を分けて組み立てる"""

    def __init__(self, ble):
        self.ble = ble
        self.buf = b""

    def lines(self):
        for _conn, chunk in self.ble.notified:
            self.buf += chunk
        self.ble.notified = []
        out = []
        while b"\n" in self.buf:
            line, self.buf = self.buf.split(b"\n", 1)
            out.append(json.loads(line))
        return out


def make(fs=None, shown=None):
    fs = fs if fs is not None else FakeFs()
    cfg = Cfg()
    st = Settings(cfg, "/settings.json", opener=fs.open, remove=fs.remove, rename=fs.rename)
    src = Src()
    r = Remote(st, None, src.status_fn, src.stations_fn)
    fb = FakeBle()
    shown = shown if shown is not None else []
    b = B.Ble(r, on_passkey=shown.append, bonds_path="/ble_bonds.json", ble=fb, opener=fs.open,
              remove=fs.remove, rename=fs.rename, urandom=lambda n: b"\x40\xe2\x01\x00")
    b.start()
    return b, fb, r, cfg, fs, shown


def connect(b, fb, conn=1, auth=True):
    fb.ev(B.IRQ_CENTRAL_CONNECT, conn, 0, b"\x00" * 6)
    if auth:
        fb.ev(B.IRQ_ENCRYPTION_UPDATE, conn, True, True, True, 16)


def test_start_configures_pairing_and_advertises():
    b, fb, _r, _cfg, _fs, _shown = make()
    assert fb.active_ and fb.cfg["bond"] and fb.cfg["mitm"] and fb.cfg["le_secure"]
    assert fb.cfg["io"] == 0 and fb.cfg["mtu"] == 517  # DisplayOnly
    assert fb.buf_size == (12, B.RX_BUF)
    flags = [f for _u, f in fb.chars]
    assert flags[1] & B.FLAG_WRITE_AUTHENTICATED  # RX は認証された接続だけ
    assert flags[0] == B.FLAG_NOTIFY
    assert b.name == "PicoAPRS-ABCD"
    interval, adv, resp = fb.adv
    assert interval == B.ADV_INTERVAL_US
    assert adv.startswith(b"\x02\x01\x06") and B.uuid_le(B.NUS) in adv
    assert resp == b"\x0e\x09PicoAPRS-ABCD"


def test_uuid_is_little_endian():
    u = B.uuid_le("6E400001-B5A3-F393-E0A9-E50E24DCCA9E")
    assert u[:2] == b"\x9e\xca" and u[-2:] == b"\x40\x6e"
    assert len(u) == 16


def test_nothing_is_processed_before_authentication():
    b, fb, r, _cfg, _fs, _shown = make()
    link = Link(fb)
    connect(b, fb, auth=False)
    assert not b.authenticated and not r.attached
    fb.write(1, b'{"op":"hello","id":1}\n')
    b.poll(0)
    assert link.lines() == [] and b.dropped_writes == 22
    # 認証されたら処理する(認証前に書いた分は捨てられたまま)
    fb.ev(B.IRQ_ENCRYPTION_UPDATE, 1, True, False, False, 16)  # 暗号化だけで MITM なし: まだ
    assert not b.authenticated
    fb.ev(B.IRQ_ENCRYPTION_UPDATE, 1, True, True, True, 16)
    assert b.authenticated and r.attached
    b.poll(1)
    assert [m["t"] for m in link.lines()] == ["status"]  # 接続直後の status


def test_commands_roundtrip_with_split_writes():
    b, fb, _r, cfg, _fs, _shown = make()
    link = Link(fb)
    connect(b, fb)
    b.poll(0)
    link.lines()
    req = b'{"op":"settings.set","id":7,"values":{"ssid":9}}\n'
    for i in range(0, len(req), 20):  # 20 バイトずつ(仕様書 §5.2)
        fb.write(1, req[i:i + 20])
    b.poll(1)
    (m,) = link.lines()
    assert m["t"] == "settings" and m["id"] == 7 and m["values"]["ssid"] == 9 and cfg.SSID == 9


def test_two_lines_in_one_write_and_crlf():
    b, fb, _r, _cfg, _fs, _shown = make()
    link = Link(fb)
    connect(b, fb)
    b.poll(0)
    link.lines()
    fb.write(1, b'{"op":"hello","id":1}\r\n{"op":"status","id":2}\n')
    b.poll(1)
    ms = link.lines()
    assert [(m["t"], m["id"]) for m in ms] == [("hello", 1), ("status", 2)]


def test_notifications_are_split_by_mtu():
    b, fb, _r, _cfg, _fs, _shown = make()
    connect(b, fb)
    b.poll(0)
    fb.notified = []
    assert b.mtu == 23
    fb.write(1, b'{"op":"settings.schema","id":1}\n')
    b.poll(1)
    chunks = [c for _c, c in fb.notified]
    assert len(chunks) > 3 and all(len(c) <= 20 for c in chunks)
    fb.notified = []
    fb.ev(B.IRQ_MTU_EXCHANGED, 1, 247)
    assert b.mtu == 247
    fb.write(1, b'{"op":"settings.schema","id":2}\n')
    b.poll(2)
    chunks = [c for _c, c in fb.notified]
    assert max(len(c) for c in chunks) == 244 and all(len(c) <= 244 for c in chunks)
    msg = json.loads(b"".join(chunks))
    assert msg["t"] == "schema" and msg["id"] == 2


def test_full_notify_queue_is_retried_without_loss():
    b, fb, _r, _cfg, _fs, _shown = make()
    link = Link(fb)
    connect(b, fb)
    fb.notify_fail = 3
    b.poll(0)  # 接続直後の status。3 回失敗しても続きを後で送る
    assert link.lines() == []
    for t in range(1, 10):
        b.poll(t)
    (m,) = link.lines()
    assert m["t"] == "status"
    # 送り残しがあるうちは、次の write は受け付けない(False)
    fb.notify_fail = 99
    assert b.write(b"x" * 100 + b"\n") is True
    assert b.write(b"y\n") is False


def test_too_long_and_busy_are_reported():
    b, fb, _r, _cfg, _fs, _shown = make()
    link = Link(fb)
    connect(b, fb)
    b.poll(0)
    link.lines()
    fb.ev(B.IRQ_MTU_EXCHANGED, 1, 517)
    for _ in range(3):  # 514 バイトずつ書いて 1 行にする(LINE_MAX 512 を超える)
        fb.write(1, b"x" * 200)
    fb.write(1, b"\n")
    b.poll(1)
    (m,) = link.lines()
    assert m["code"] == "too_long" and "id" not in m
    # 処理待ちが満杯のとき(poll の前に、続けて LINES_MAX を超えて書く)
    for i in range(B.LINES_MAX + 2):
        fb.write(1, b'{"op":"hello","id":%d}\n' % i)
    b.poll(2)
    ms = link.lines()
    assert [m["id"] for m in ms if m["t"] == "hello"] == list(range(B.LINES_MAX))
    assert any(m.get("code") == "busy" for m in ms)


def test_line_buffer_edges():
    lb = B.LineBuffer(line_max=5, lines_max=2)
    lb.feed(b"abc\n\n")  # 空行は無視
    assert lb.lines == [b"abc"]
    lb.feed(b"12345\n")  # ちょうど上限
    assert lb.lines == [b"abc", b"12345"] and lb.too_long == 0
    lb.feed(b"123456789\nok\n")  # 超えた行は捨て、次の行は読む
    assert lb.too_long == 1 and lb.lines == [b"abc", b"12345"] and lb.busy == 1


def test_passkey_is_shown_while_pairing():
    b, fb, _r, _cfg, _fs, shown = make()
    connect(b, fb, auth=False)
    fb.ev(B.IRQ_PASSKEY_ACTION, 1, B.PASSKEY_ACTION_DISPLAY, 0)
    assert fb.passkeys == [(1, 3, 123456)] and shown == [123456] and b.passkey == 123456
    fb.ev(B.IRQ_ENCRYPTION_UPDATE, 1, True, True, True, 16)
    assert shown == [123456, None] and b.passkey is None  # 済んだら消す


def test_passkey_is_cleared_when_pairing_fails():
    b, fb, _r, _cfg, _fs, shown = make()
    connect(b, fb, auth=False)
    fb.ev(B.IRQ_PASSKEY_ACTION, 1, B.PASSKEY_ACTION_DISPLAY, 0)
    fb.ev(B.IRQ_CENTRAL_DISCONNECT, 1, 0, b"\x00" * 6)
    assert shown == [123456, None] and fb.adv is not None  # 切れたら広告を再開


def test_disconnect_detaches_and_restarts_advertising():
    b, fb, r, _cfg, _fs, _shown = make()
    connect(b, fb)
    assert r.attached
    fb.adv = None
    fb.ev(B.IRQ_CENTRAL_DISCONNECT, 1, 0, b"\x00" * 6)
    assert not r.attached and not b.authenticated and b.conn is None
    assert fb.adv is not None
    assert b.write(b"x\n") is True  # 接続がなければ捨てる


def test_secrets_roundtrip_and_save_delay():
    b, fb, _r, _cfg, fs, _shown = make()
    assert fb.ev(B.IRQ_SET_SECRET, 2, b"key1", b"val1") is True
    assert fb.ev(B.IRQ_SET_SECRET, 2, b"key2", b"val2") is True
    assert fb.ev(B.IRQ_SET_SECRET, 1, b"key1", b"other") is True  # 種類が違えば別の鍵
    assert fb.ev(B.IRQ_GET_SECRET, 2, 0, b"key1") == b"val1"
    assert fb.ev(B.IRQ_GET_SECRET, 2, 0, None) == b"val1"  # index で探す
    assert fb.ev(B.IRQ_GET_SECRET, 2, 1, None) == b"val2"
    assert fb.ev(B.IRQ_GET_SECRET, 2, 2, None) is None
    assert fb.ev(B.IRQ_GET_SECRET, 2, 0, b"nope") is None
    assert fs.files == {}  # すぐには書かない
    t0 = b._secrets_dirty_ms
    assert t0 is not None
    b.poll(time.ticks_add(t0, 100))
    assert fs.files == {}
    b.poll(time.ticks_add(t0, B.SAVE_DELAY_MS + 1))
    doc = json.loads(fs.files["/ble_bonds.json"])
    assert len(doc["secrets"]) == 3 and "/ble_bonds.json.tmp" not in fs.files
    # 電源を入れ直しても残る
    b2, fb2, _r2, _c, _f, _s = make(fs)
    assert fb2.ev(B.IRQ_GET_SECRET, 2, 0, b"key2") == b"val2"
    # 削除
    assert fb2.ev(B.IRQ_SET_SECRET, 2, b"key2", None) is True
    assert fb2.ev(B.IRQ_SET_SECRET, 2, b"none", None) is False
    assert fb2.ev(B.IRQ_GET_SECRET, 2, 0, b"key2") is None


def test_secrets_are_capped_and_forgotten():
    b, fb, _r, _cfg, fs, _shown = make()
    for i in range(B.MAX_SECRETS + 5):
        fb.ev(B.IRQ_SET_SECRET, 2, b"k%d" % i, b"v")
    assert len(b.secrets) == B.MAX_SECRETS
    assert fb.ev(B.IRQ_GET_SECRET, 2, 0, b"k0") is None  # 古いものから消える
    assert fb.ev(B.IRQ_GET_SECRET, 2, 0, b"k%d" % (B.MAX_SECRETS + 4)) == b"v"
    b._save_secrets(force=True)
    assert "/ble_bonds.json" in fs.files
    b.forget_all()
    assert fs.files == {} and b.secrets == []


def test_broken_bonds_file_is_ignored():
    fs = FakeFs()
    fs.files["/ble_bonds.json"] = b"{not json"
    b, fb, _r, _cfg, _fs, _shown = make(fs)
    assert b.secrets == [] and fb.ev(B.IRQ_GET_SECRET, 2, 0, None) is None


def test_stop_disconnects_and_flushes_bonds():
    b, fb, r, _cfg, fs, _shown = make()
    connect(b, fb)
    fb.ev(B.IRQ_SET_SECRET, 2, b"k", b"v")
    b.stop()
    assert fb.disconnected == [1] and not fb.active_ and not r.attached
    assert "/ble_bonds.json" in fs.files
    b.poll(0)  # 止めたあとは何もしない
