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
        self.irq_fn = None
        self.loaded = None
        self.disconnected = []
        self.buf_size = None

    def active(self, v=None):
        if v is None:
            return self.active_
        if v and not self.active_:
            # パッチ済みの BTstack は起動中に ER/IR とボンドを読む(irq が先に要る)
            self.loaded = None if self.irq_fn is None else self.irq_fn(29, (0x10, 0, b"SMER"))
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


def make(fs=None, shown=None, adv_closed=False):
    fs = fs if fs is not None else FakeFs()
    cfg = Cfg()
    st = Settings(cfg, "/settings.json", opener=fs.open, remove=fs.remove, rename=fs.rename)
    src = Src()
    r = Remote(st, None, src.status_fn, src.stations_fn)
    fb = FakeBle()
    shown = shown if shown is not None else []
    b = B.Ble(r, on_passkey=shown.append, bonds_path="/ble_bonds.json", ble=fb, opener=fs.open,
              remove=fs.remove, rename=fs.rename, adv_closed=adv_closed)
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
    fb.notified = []
    fb.ev(B.IRQ_MTU_EXCHANGED, 1, 517)  # MTU が大きくても NOTIFY_MAX で区切る
    fb.write(1, b'{"op":"settings.schema","id":3}\n')
    b.poll(3)
    chunks = [c for _c, c in fb.notified]
    assert max(len(c) for c in chunks) == B.NOTIFY_MAX == 244
    assert json.loads(b"".join(chunks))["id"] == 3


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
    fb.ev(B.IRQ_PASSKEY_ACTION, 1, B.PASSKEY_ACTION_DISPLAY, 123456)  # BTstack が作った値
    assert shown == [123456] and b.passkey == 123456 and fb.passkeys == []
    fb.ev(B.IRQ_ENCRYPTION_UPDATE, 1, True, True, True, 16)
    assert shown == [123456, None] and b.passkey is None  # 済んだら消す


def test_passkey_is_cleared_when_pairing_fails():
    b, fb, _r, _cfg, _fs, shown = make()
    connect(b, fb, auth=False)
    fb.ev(B.IRQ_PASSKEY_ACTION, 1, B.PASSKEY_ACTION_DISPLAY, 123456)
    fb.ev(B.IRQ_CENTRAL_DISCONNECT, 1, 0, b"\x00" * 6)
    assert shown == [123456, None] and fb.adv is not None  # 切れたら広告を再開


def test_passkey_is_cleared_when_pairing_is_cancelled():
    b, fb, _r, _cfg, _fs, shown = make()
    connect(b, fb, auth=False)
    fb.ev(B.IRQ_PASSKEY_ACTION, 1, B.PASSKEY_ACTION_DISPLAY, 42)
    fb.ev(B.IRQ_PASSKEY_ACTION, 1, B.PASSKEY_ACTION_NONE, 0)
    assert shown == [42, None] and b.passkey is None


def test_passkey_for_another_connection_is_ignored():
    b, fb, _r, _cfg, _fs, shown = make()
    connect(b, fb, auth=False)
    fb.ev(B.IRQ_PASSKEY_ACTION, 2, B.PASSKEY_ACTION_DISPLAY, 42)
    assert shown == [] and b.passkey is None


def test_saved_secrets_are_readable_while_bluetooth_starts():
    fs = FakeFs()
    b, fb, *_ = make(fs)
    fb.ev(B.IRQ_SET_SECRET, 0x10, b"SMER", b"k" * 16)
    b._save_secrets(force=True)
    b2, fb2, *_ = make(fs)
    assert fb2.loaded == b"k" * 16  # irq() を active(True) より前に結んでいる


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


def test_bond_save_failure_keeps_ble_and_retries():
    # フラッシュが満杯で鍵を書けなくても BLE を止めない。SAVE_RETRY_MS あとに書き直す
    b, fb, _r, _cfg, fs, _shown = make()
    fb.ev(B.IRQ_SET_SECRET, 2, b"key1", b"val1")
    t0 = b._secrets_dirty_ms
    assert t0 is not None
    fs.fail = True
    t1 = time.ticks_add(t0, B.SAVE_DELAY_MS + 1)
    b.poll(t1)  # 例外を出さない
    assert "/ble_bonds.json" not in fs.files and b.running
    fs.fail = False
    b.poll(time.ticks_add(t1, B.SAVE_RETRY_MS - 1))
    assert "/ble_bonds.json" not in fs.files
    b.poll(time.ticks_add(t1, B.SAVE_RETRY_MS))
    assert "/ble_bonds.json" in fs.files and b._secrets_dirty_ms is None


def test_forget_all_reports_a_save_failure():
    b, _fb, _r, _cfg, fs, _shown = make()
    b.stop()
    fs.fail = True
    try:
        b.forget_all()
        raise AssertionError("OSError expected")
    except OSError:
        pass


def test_short_encryption_key_is_not_authenticated():
    b, fb, r, _cfg, _fs, _shown = make()
    connect(b, fb, auth=False)
    fb.ev(B.IRQ_ENCRYPTION_UPDATE, 1, True, True, True, 7)  # 7 バイトの鍵(仕様の最小)
    assert not b.authenticated and not r.attached
    fb.ev(B.IRQ_ENCRYPTION_UPDATE, 1, True, True, True, 16)
    assert b.authenticated and b.key_size == 16


def test_secrets_are_capped_and_forgotten():
    b, fb, _r, _cfg, fs, _shown = make()
    for i in range(B.MAX_SECRETS + 5):
        fb.ev(B.IRQ_SET_SECRET, 2, b"k%d" % i, b"v")
    assert len(b.secrets) == B.MAX_SECRETS
    assert fb.ev(B.IRQ_GET_SECRET, 2, 0, b"k0") is None  # 古いものから消える
    assert fb.ev(B.IRQ_GET_SECRET, 2, 0, b"k%d" % (B.MAX_SECRETS + 4)) == b"v"
    fb.ev(B.IRQ_SET_SECRET, B.SECRET_TYPE_TLV, b"SMER", b"er")  # 本機の鍵(ボンドではない)
    b._save_secrets(force=True)
    assert "/ble_bonds.json" in fs.files
    assert b.forget_all() is False  # BLE が動いている間は消せない(BTstack の表とずれる)
    assert len(b.secrets) == B.MAX_SECRETS
    b.stop()
    assert b.forget_all() is True
    assert b.secrets == [(B.SECRET_TYPE_TLV, b"SMER", b"er")]  # ER/IR は残す
    doc = json.loads(fs.files["/ble_bonds.json"])
    assert len(doc["secrets"]) == 1 and "/ble_bonds.json.tmp" not in fs.files


def test_eviction_keeps_er_ir_and_drops_cccd_first():
    b, fb, _r, _cfg, _fs, _shown = make()
    T = B.SECRET_TYPE_TLV
    fb.ev(B.IRQ_SET_SECRET, T, b"SMER", b"er")
    fb.ev(B.IRQ_SET_SECRET, T, b"SMIR", b"ir")
    fb.ev(B.IRQ_SET_SECRET, T, b"BTD\x00", b"bond")
    fb.ev(B.IRQ_SET_SECRET, T, b"BTC\x01", b"ccc1")
    for i in range(B.MAX_SECRETS - 4):
        fb.ev(B.IRQ_SET_SECRET, 2, b"k%d" % i, b"v")
    assert len(b.secrets) == B.MAX_SECRETS
    assert fb.ev(B.IRQ_SET_SECRET, 2, b"new", b"v") is True
    keys = [k for _t, k, _v in b.secrets]
    assert b"BTC\x01" not in keys  # 通知の設定から消える
    assert b"SMER" in keys and b"SMIR" in keys and b"BTD\x00" in keys
    assert fb.ev(B.IRQ_SET_SECRET, 2, b"new2", b"v") is True
    keys = [k for _t, k, _v in b.secrets]
    assert b"SMER" in keys and b"SMIR" in keys and b"BTD\x00" not in keys  # 次は古いもの
    # ER/IR しか無ければ保存しない
    b.secrets = [(T, b"SMER", b"er"), (T, b"SMIR", b"ir")]
    assert b._evict_index() is None


def test_secret_changed_while_saving_is_saved_again():
    b, fb, _r, _cfg, fs, _shown = make()
    fb.ev(B.IRQ_SET_SECRET, 2, b"k1", b"v1")
    real_open = fs.open
    changed = []

    def open_and_change(path, mode):  # 書いている途中で IRQ が鍵を変える
        if "w" in mode and not changed:
            changed.append(path)
            fb.ev(B.IRQ_SET_SECRET, 2, b"k2", b"v2")
        return real_open(path, mode)

    b._open = open_and_change
    b._save_secrets(force=True)
    assert changed and b._secrets_dirty_ms is not None  # まだ保存が要る
    b._save_secrets(force=True)
    assert b._secrets_dirty_ms is None
    assert len(json.loads(fs.files["/ble_bonds.json"])["secrets"]) == 2


def test_failed_pairing_clears_passkey():
    b, fb, _r, _cfg, _fs, shown = make()
    connect(b, fb, auth=False)
    fb.ev(B.IRQ_PASSKEY_ACTION, 1, B.PASSKEY_ACTION_DISPLAY, 123456)
    assert shown == [123456]
    fb.ev(B.IRQ_ENCRYPTION_UPDATE, 1, False, False, False, 0)  # パスキーの入力違い
    assert shown == [123456, None] and b.passkey is None


def test_exception_in_on_passkey_does_not_escape_irq():
    def broken(_passkey):
        raise RuntimeError("lcd")

    b, fb, _r, _cfg, _fs, _shown = make()
    b.on_passkey = broken
    connect(b, fb, auth=False)
    assert fb.ev(B.IRQ_PASSKEY_ACTION, 1, B.PASSKEY_ACTION_DISPLAY, 123456) is None
    assert b.passkey == 123456 and b.irq_errors == 1
    fb.ev(B.IRQ_ENCRYPTION_UPDATE, 1, True, True, True, 16)  # 状態の更新は続く
    assert b.authenticated and b.passkey is None


def test_disconnect_during_stop_does_not_advertise():
    b, fb, _r, _cfg, _fs, _shown = make()
    connect(b, fb)

    def disconnect_now(conn):  # 切断のイベントが gap_disconnect の中で届く
        fb.disconnected.append(conn)
        fb.adv = None
        fb.ev(B.IRQ_CENTRAL_DISCONNECT, conn, 0, b"\x00" * 6)

    fb.gap_disconnect = disconnect_now
    b.stop()
    assert fb.adv is None and not fb.active_


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


def test_unauthenticated_link_is_dropped_after_timeout():
    b, fb, _r, _cfg, _fs, shown = make()
    connect(b, fb, auth=False)
    fb.ev(B.IRQ_PASSKEY_ACTION, 1, B.PASSKEY_ACTION_DISPLAY, 123456)
    t0 = b.connected_ms
    b.poll(time.ticks_add(t0, B.AUTH_TIMEOUT_MS - 1))  # パスキーを打っている間は切らない
    assert fb.disconnected == []
    b.poll(time.ticks_add(t0, B.AUTH_TIMEOUT_MS))
    assert fb.disconnected == [1]
    b.poll(time.ticks_add(t0, B.AUTH_TIMEOUT_MS + 1000))  # 切断のイベントを待つ間は 1 回だけ
    assert fb.disconnected == [1]
    fb.adv = None
    fb.ev(B.IRQ_CENTRAL_DISCONNECT, 1, 0, b"\x00" * 6)
    assert b.conn is None and fb.adv is not None and shown == [123456, None]


def test_authenticated_link_is_kept():
    b, fb, _r, _cfg, _fs, _shown = make()
    connect(b, fb)
    b.poll(time.ticks_add(b.connected_ms, 10 * B.AUTH_TIMEOUT_MS))
    assert fb.disconnected == [] and b.authenticated


def test_reconnect_starts_with_an_empty_line_buffer():
    """書きかけの行(書き込みの失敗や切断)は、つなぎ直すと消える(仕様書 §5.3)"""
    b, fb, _r, _cfg, _fs, _shown = make()
    link = Link(fb)
    connect(b, fb)
    b.poll(0)
    link.lines()
    fb.write(1, b'{"op":"hel')
    fb.ev(B.IRQ_CENTRAL_DISCONNECT, 1, 0, b"\x00" * 6)
    connect(b, fb, conn=2)
    fb.write(2, b'{"op":"hello","id":2}\n')
    b.poll(1)
    assert [(m["t"], m.get("id")) for m in link.lines()] == [("hello", 2), ("status", None)]


def test_bare_newline_resynchronizes():
    """書き込みに失敗したら \n を送る: 断片は id のない bad_json、空行は何も返さない(§5.3)"""
    b, fb, _r, _cfg, _fs, _shown = make()
    link = Link(fb)
    connect(b, fb)
    b.poll(0)
    link.lines()
    fb.write(1, b'{"op":"settings.re')  # 途中で失敗した書き込み
    fb.write(1, b"\n")
    fb.write(1, b"\n")  # 断片が無ければ無視される
    fb.write(1, b'{"op":"hello","id":3}\n')
    b.poll(1)
    ms = link.lines()
    assert [(m["t"], m.get("code"), m.get("id")) for m in ms] == [
        ("err", "bad_json", None), ("hello", None, 3)]


def test_busy_line_is_never_executed():
    """busy で捨てた行は実行せず、その id の応答も来ない。busy はそれまでの応答のあと(§8.6)"""
    b, fb, _r, cfg, _fs, _shown = make()
    link = Link(fb)
    connect(b, fb)
    b.poll(0)
    link.lines()
    for i in range(B.LINES_MAX):
        fb.write(1, b'{"op":"hello","id":%d}\n' % i)
    fb.write(1, b'{"op":"settings.set","id":99,"values":{"ssid":9}}\n')  # 5 行目: 捨てる
    fb.write(1, b'{"op":"settings.set","id":98,"values":{"ssid":8}}\n')  # 6 行目: 捨てる
    b.poll(1)
    ms = [m for m in link.lines() if m["t"] != "status"]
    assert [m.get("id") for m in ms] == list(range(B.LINES_MAX)) + [None]
    assert ms[-1]["code"] == "busy" and cfg.SSID != 9 and cfg.SSID != 8
    b.poll(2)
    assert [m for m in link.lines() if m["t"] != "status"] == []  # 遅れて来る応答もない


def test_disconnect_while_handling_a_line_drops_the_rest():
    # beacon.send(送信で最長約 2 秒)などの処理中に切断の IRQ が来ると、行のリストが替わる。
    # IndexError で BLE ごと止めず、前の接続の残りの要求も実行しない
    b, fb, r, cfg, _fs, _shown = make()
    connect(b, fb)
    b.poll(0)
    done = []
    orig = r.handle

    def handle(line):
        done.append(line)
        if len(done) == 1:
            fb.ev(B.IRQ_CENTRAL_DISCONNECT, 1, 0, b"\x00" * 6)
        orig(line)

    r.handle = handle
    fb.write(1, b'{"op":"status","id":1}\n{"op":"settings.set","id":2,"values":{"ssid":9}}\n')
    b.poll(1)
    assert len(done) == 1 and cfg.SSID != 9
    assert b.conn is None and fb.adv is not None
    connect(b, fb, conn=2)  # 次の接続はふつうに使える
    fb.write(2, b'{"op":"settings.set","id":3,"values":{"ssid":9}}\n')
    b.poll(2)
    assert cfg.SSID == 9



def _rows(n):  # 架空の局
    return [{"src": "Q1AA%d" % i, "last_s": i, "count": 1, "lat": 35.0, "lon": 139.0,
             "lvl_db": None} for i in range(n)]


def test_requests_wait_until_a_list_is_sent():
    # 一覧を返している間に来た要求は、一覧の終わりの後に応答する(docs/ble-protocol.md §6.1)。
    # 前は 2 つ目の stations.list が 1 つ目の残りを上書きして、1 つ目が途中で消えた
    b, fb, r, _cfg, _fs, _shown = make()
    link = Link(fb)
    r.stations_fn = lambda: _rows(4)
    connect(b, fb)
    b.poll(0)
    link.lines()
    fb.notify_fail = 3  # 通知が詰まって、一覧が何回かの poll にまたがる
    fb.write(1, b'{"op":"stations.list","id":1}\n{"op":"stations.list","id":2}\n'
                b'{"op":"status","id":3}\n')
    for i in range(20):
        b.poll(1 + i)
    ms = [m for m in link.lines() if "id" in m]
    assert [(m["t"], m["id"]) for m in ms] == (
        [("station", 1)] * 4 + [("stations.end", 1)]
        + [("station", 2)] * 4 + [("stations.end", 2)] + [("status", 3)])
    assert b._todo == []


def test_disconnect_drops_requests_waiting_for_a_list():
    b, fb, r, cfg, _fs, _shown = make()
    r.stations_fn = lambda: _rows(3)
    connect(b, fb)
    b.poll(0)
    fb.notify_fail = 100  # 一覧が送りきれない
    fb.write(1, b'{"op":"stations.list","id":1}\n'
                b'{"op":"settings.set","id":2,"values":{"ssid":9}}\n')
    b.poll(1)
    assert len(b._todo) == 1 and not r.idle
    fb.ev(B.IRQ_CENTRAL_DISCONNECT, 1, 0, b"\x00" * 6)
    assert b._todo == [] and r.idle
    fb.notify_fail = 0
    connect(b, fb, conn=2)
    b.poll(2)
    assert cfg.SSID != 9  # 前の接続の要求は実行しない


def test_disconnect_while_notifying_does_not_raise():
    b, fb, _r, _cfg, _fs, _shown = make()
    connect(b, fb)
    b.poll(0)
    sent = []

    def notify(conn, h, data):
        sent.append(data)
        if len(sent) == 1:
            fb.ev(B.IRQ_CENTRAL_DISCONNECT, 1, 0, b"\x00" * 6)

    fb.gatts_notify = notify
    assert b.write(b"z" * 100 + b"\n") is True  # MTU 23 なので何個かに分かれる
    assert len(sent) == 1 and b._out == []


def _fail_pairing(b, fb, conn, now):
    """conn でつなぎ、パスキーを出し、入力違いで失敗させ、poll で切断させる"""
    connect(b, fb, conn=conn, auth=False)
    fb.ev(B.IRQ_PASSKEY_ACTION, conn, B.PASSKEY_ACTION_DISPLAY, 123456)
    fb.ev(B.IRQ_ENCRYPTION_UPDATE, conn, False, False, False, 0)
    fb.ev(B.IRQ_PASSKEY_ACTION, conn, B.PASSKEY_ACTION_NONE, 0)  # 同じ失敗で両方来る(1 回と数える)
    b.poll(now)
    b.poll(time.ticks_add(now, 10))  # 切断のイベントを待つ間は、もう一度は要求しない
    assert fb.disconnected[-1] == conn and fb.disconnected.count(conn) == 1
    adv = fb.adv  # 切断の前の広告の設定(BTstack は切断のあと、この設定のまま広告を再開する)
    fb.ev(B.IRQ_CENTRAL_DISCONNECT, conn, 0, b"\x00" * 6)
    return adv


def test_failed_pairing_disconnects():
    # 1 つの接続の中でペアリングをやり直させない(docs/ble-protocol.md §4)
    b, fb, _r, _cfg, _fs, _shown = make()
    _fail_pairing(b, fb, 1, 0)
    assert b.pair_fails == 1 and fb.adv is not None  # 1 回目: すぐ広告に戻る
    connect(b, fb, conn=2)  # 認証できたら数え直す
    assert b.pair_fails == 0


def test_three_failures_stop_advertising_for_a_while():
    b, fb, _r, _cfg, _fs, _shown = make()
    _fail_pairing(b, fb, 1, 0)
    _fail_pairing(b, fb, 2, 1000)
    assert fb.adv is not None
    t3: "Any" = 2000  # ticks_ms の値として使う(スタブでは専用の型)
    assert _fail_pairing(b, fb, 3, t3) is None  # 3 回続いた: 切断の前に広告を止める
    assert fb.adv is None
    assert b.advertising_held  # LCD のアイコンが変わる(station._ble_state が "hold")
    b.poll(time.ticks_add(t3, B.PAIR_HOLD_MS - 1))
    assert fb.adv is None
    b.poll(time.ticks_add(t3, B.PAIR_HOLD_MS))
    assert fb.adv is not None and b.pair_fails == 0
    assert not b.advertising_held


def test_advertising_window_closes_for_good():
    # 起動から BLE_ADV_WINDOW_S を過ぎたら(局が close_advertising を呼ぶ)、どこからも
    # 広告を再開しない
    b, fb, _r, _cfg, _fs, _shown = make()
    assert fb.adv is not None and not b.advertising_closed
    connect(b, fb)  # 接続中に期限が来ても、接続はそのまま
    b.close_advertising()
    assert fb.adv is None and b.authenticated and not b.advertising_closed
    fb.ev(B.IRQ_CENTRAL_DISCONNECT, 1, 0, b"\x00" * 6)
    assert fb.adv is None and b.advertising_closed  # 切れても広告しない(LCD は灰色)
    b.poll(0)
    assert fb.adv is None


def test_advertising_window_also_stops_the_pairing_hold_restart():
    b, fb, _r, _cfg, _fs, _shown = make()
    for i in range(3):
        _fail_pairing(b, fb, i + 1, i * 1000)
    assert b.advertising_held and fb.adv is None
    b.close_advertising()
    t: "Any" = 2000  # ticks_ms の値として使う(スタブでは専用の型)
    b.poll(time.ticks_add(t, B.PAIR_HOLD_MS))  # 休止が明けても広告しない
    assert fb.adv is None and b.advertising_closed


def test_ble_made_again_after_the_window_does_not_advertise():
    # エラーのあと作り直すとき(adv_closed=True)は、起動しても広告しない
    b, fb, _r, _cfg, _fs, _shown = make(adv_closed=True)
    assert fb.adv is None and b.advertising_closed and b.running


def test_authenticated_link_is_not_dropped_by_pairing_check():
    b, fb, _r, _cfg, _fs, _shown = make()
    connect(b, fb)
    fb.ev(B.IRQ_PASSKEY_ACTION, 1, B.PASSKEY_ACTION_NONE, 0)  # 認証の後の取り消しの通知
    b.poll(0)
    assert fb.disconnected == [] and b.pair_fails == 0
